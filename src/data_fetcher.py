"""Market data access with timeout, retry, and graceful fallback."""

from __future__ import annotations

import logging
import queue
import json
import re
import requests
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MarketQuote:
    asset_code: str
    market_type: str
    price: float
    change_pct: float | None
    quote_date: str
    source: str


class MarketDataFetcher:
    """Fetch quotes from external market-data libraries."""

    def __init__(self, timeout: float = 10.0, retries: int = 2) -> None:
        self.timeout = timeout
        self.retries = retries

    def fetch_quote(self, asset_code: str, market_type: str) -> MarketQuote | None:
        """Fetch a quote. Returns None on any external failure."""

        if market_type == "US":
            return self._fetch_first_available(
                [
                    lambda: self._fetch_us_stooq_quote(asset_code),
                    lambda: self._fetch_us_quote(asset_code),
                ]
            )
        if market_type == "CN":
            return self._fetch_first_available(
                [lambda: self._fetch_cn_eastmoney_quote(asset_code)]
                + [
                    lambda provider_name=provider_name: self._fetch_cn_quote_from_provider(
                        asset_code,
                        provider_name,
                    )
                    for provider_name in _cn_provider_order(asset_code)
                ]
            )
        if market_type == "FUND":
            return self._fetch_first_available(
                [
                    lambda: self._fetch_fund_estimate_quote(asset_code),
                    lambda: self._fetch_fund_nav_quote(asset_code),
                ]
            )
        if market_type == "MANUAL":
            return None
        if market_type == "GOLD":
            return self._fetch_first_available(
                [lambda: self._fetch_sge_gold_quote(asset_code)]
                + [
                    lambda provider_name=provider_name: self._fetch_gold_quote_from_provider(
                        asset_code,
                        provider_name,
                    )
                    for provider_name in ("spot_gold_spot", "futures_spot_price")
                ]
            )
        if market_type == "CRYPTO":
            return self._fetch_first_available(
                [
                    lambda: self._fetch_crypto_coingecko_quote(asset_code),
                    lambda: self._fetch_crypto_binance_quote(asset_code),
                ]
            )
        logger.warning("Unsupported market type: %s", market_type)
        return None

    def _fetch_first_available(
        self,
        fetchers: list[Callable[[], MarketQuote | None]],
    ) -> MarketQuote | None:
        for fetcher in fetchers:
            quote = self._with_retry(fetcher)
            if quote is not None:
                return quote
        return None

    def _with_retry(self, fn: Callable[[], MarketQuote | None]) -> MarketQuote | None:
        for attempt in range(1, self.retries + 2):
            result = _run_with_timeout(fn, self.timeout)
            if result.status == "success" and result.quote is not None:
                return result.quote
            if result.status == "timeout":
                logger.warning("Market data request timed out on attempt %s", attempt)
            elif result.status == "error":
                logger.warning(
                    "Market data request failed on attempt %s: %s",
                    attempt,
                    result.error,
                )
            else:
                logger.warning("Market data request returned no data on attempt %s", attempt)
        return None

    def _fetch_us_quote(self, asset_code: str) -> MarketQuote | None:
        import pandas as pd
        import yfinance as yf

        data = yf.download(
            asset_code,
            period="5d",
            interval="1d",
            progress=False,
            timeout=self.timeout,
            auto_adjust=False,
        )
        if data is None or data.empty:
            return None

        close_data = data["Close"]
        if isinstance(close_data, pd.DataFrame):
            close_series = close_data[asset_code] if asset_code in close_data else close_data.iloc[:, 0]
        else:
            close_series = close_data

        close_series = close_series.dropna()
        if close_series.empty:
            return None

        price = float(close_series.iloc[-1])
        prev = float(close_series.iloc[-2]) if len(close_series) > 1 else price
        change_pct = ((price - prev) / prev * 100) if prev else None
        quote_date = close_series.index[-1].date().isoformat()
        return MarketQuote(
            asset_code=asset_code,
            market_type="US",
            price=price,
            change_pct=change_pct,
            quote_date=quote_date,
            source="yfinance",
        )

    def _fetch_us_stooq_quote(self, asset_code: str) -> MarketQuote | None:
        symbol = f"{asset_code.lower()}.us"
        response = requests.get(
            "https://stooq.com/q/l/",
            params={"s": symbol, "i": "d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        lines = [line.strip() for line in response.text.splitlines() if line.strip()]
        data_lines = [line for line in lines if not line.lower().startswith("symbol,")]
        if not data_lines:
            return None
        parts = [part.strip() for part in data_lines[0].split(",")]
        if len(parts) < 7 or any(part in {"", "N/D"} for part in (parts[1], parts[3], parts[6])):
            return None

        quote_date = datetime.strptime(parts[1], "%Y%m%d").date().isoformat()
        open_price = float(parts[3])
        price = float(parts[6])
        change_pct = ((price - open_price) / open_price * 100) if open_price else None
        return MarketQuote(
            asset_code=asset_code,
            market_type="US",
            price=price,
            change_pct=change_pct,
            quote_date=quote_date,
            source="stooq",
        )

    def _fetch_crypto_coingecko_quote(self, asset_code: str) -> MarketQuote | None:
        coin_id = _coingecko_coin_id(asset_code)
        if coin_id is None:
            return None
        response = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": coin_id,
                "vs_currencies": "usd",
                "include_24hr_change": "true",
            },
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json().get(coin_id) or {}
        price = _safe_float(payload.get("usd"))
        if price is None or price <= 0:
            return None
        return MarketQuote(
            asset_code=asset_code,
            market_type="CRYPTO",
            price=price,
            change_pct=_safe_float(payload.get("usd_24h_change")),
            quote_date=date.today().isoformat(),
            source="coingecko_simple_price",
        )

    def _fetch_cn_eastmoney_quote(self, asset_code: str) -> MarketQuote | None:
        secid = _eastmoney_secid(asset_code)
        response = requests.get(
            "https://push2.eastmoney.com/api/qt/stock/get",
            params={
                "secid": secid,
                "fields": "f43,f57,f58,f59,f60,f169,f170",
            },
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://quote.eastmoney.com/",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("data")
        if not data:
            return None

        scale = int(data.get("f59") or 2)
        raw_price = _safe_float(data.get("f43"))
        if raw_price is None or raw_price <= 0:
            return None

        return MarketQuote(
            asset_code=asset_code,
            market_type="CN",
            price=raw_price / (10**scale),
            change_pct=_safe_float(data.get("f170")) / 100
            if _safe_float(data.get("f170")) is not None
            else None,
            quote_date=date.today().isoformat(),
            source="eastmoney",
        )

    def _fetch_fund_estimate_quote(self, asset_code: str) -> MarketQuote | None:
        response = requests.get(
            f"https://fundgz.1234567.com.cn/js/{asset_code}.js",
            params={"rt": int(datetime.now().timestamp() * 1000)},
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://fund.eastmoney.com/",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        text = response.text.strip()
        if not text.startswith("jsonpgz(") or not text.endswith(");"):
            return None
        payload = json.loads(text[len("jsonpgz(") : -2])
        price = _safe_float(payload.get("gsz")) or _safe_float(payload.get("dwjz"))
        if price is None or price <= 0:
            return None
        quote_date = str(payload.get("gztime") or payload.get("jzrq") or date.today().isoformat())
        quote_date = quote_date.split()[0]
        return MarketQuote(
            asset_code=asset_code,
            market_type="FUND",
            price=price,
            change_pct=_safe_float(payload.get("gszzl")),
            quote_date=quote_date,
            source="eastmoney_fund_estimate",
        )

    def _fetch_fund_nav_quote(self, asset_code: str) -> MarketQuote | None:
        response = requests.get(
            "https://api.fund.eastmoney.com/f10/lsjz",
            params={"fundCode": asset_code, "pageIndex": 1, "pageSize": 1},
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://fund.eastmoney.com/",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        rows = ((payload.get("Data") or {}).get("LSJZList") or [])
        if not rows:
            return None
        row = rows[0]
        price = _safe_float(row.get("DWJZ"))
        if price is None or price <= 0:
            return None
        return MarketQuote(
            asset_code=asset_code,
            market_type="FUND",
            price=price,
            change_pct=_safe_float(row.get("JZZZL")),
            quote_date=str(row.get("FSRQ") or date.today().isoformat()),
            source="eastmoney_fund_nav",
        )

    def _fetch_sge_gold_quote(self, asset_code: str) -> MarketQuote | None:
        response = requests.get(
            "https://hq.sinajs.cn/list=SGE_AU9999",
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://finance.sina.com.cn/",
            },
            timeout=self.timeout,
        )
        response.raise_for_status()
        match = re.search(r'"(.*)"', response.text)
        if match is None:
            return None
        parts = [part.strip() for part in match.group(1).split(",")]
        if len(parts) < 18:
            return None

        price = _safe_float(parts[6]) or _safe_float(parts[5])
        if price is None or price <= 0:
            return None

        quote_date = parts[16].split()[0] if parts[16] else date.today().isoformat()
        change_pct_text = parts[17].replace("%", "")
        return MarketQuote(
            asset_code=asset_code,
            market_type="GOLD",
            price=price,
            change_pct=_safe_float(change_pct_text),
            quote_date=quote_date,
            source="sina_sge_au9999",
        )

    def _fetch_cn_quote_from_provider(
        self,
        asset_code: str,
        provider_name: str,
    ) -> MarketQuote | None:
        import akshare as ak

        provider = getattr(ak, provider_name, None)
        if provider is None:
            return None
        return _quote_from_spot_table(
            provider(),
            asset_code=asset_code,
            market_type="CN",
            source=f"akshare:{provider_name}",
        )

    def _fetch_gold_quote_from_provider(
        self,
        asset_code: str,
        provider_name: str,
    ) -> MarketQuote | None:
        import akshare as ak

        provider = getattr(ak, provider_name, None)
        if provider is None:
            return None
        try:
            data = provider() if provider_name != "futures_spot_price" else provider("黄金")
        except TypeError:
            data = provider()
        return _quote_from_spot_table(
            data,
            asset_code=asset_code,
            market_type="GOLD",
            source=f"akshare:{provider_name}",
        )

    def _fetch_crypto_binance_quote(self, asset_code: str) -> MarketQuote | None:
        response = requests.get(
            "https://api.binance.com/api/v3/ticker/24hr",
            params={"symbol": asset_code.upper()},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=self.timeout,
        )
        response.raise_for_status()
        payload = response.json()
        price = _safe_float(payload.get("lastPrice"))
        if price is None or price <= 0:
            return None
        return MarketQuote(
            asset_code=asset_code,
            market_type="CRYPTO",
            price=price,
            change_pct=_safe_float(payload.get("priceChangePercent")),
            quote_date=date.today().isoformat(),
            source="binance_24hr",
        )


@dataclass(frozen=True)
class _TimeoutResult:
    status: str
    quote: MarketQuote | None = None
    error: BaseException | None = None


def _run_with_timeout(
    fn: Callable[[], MarketQuote | None],
    timeout: float,
) -> _TimeoutResult:
    result_queue: queue.Queue[_TimeoutResult] = queue.Queue(maxsize=1)

    def runner() -> None:
        try:
            result_queue.put_nowait(_TimeoutResult(status="success", quote=fn()))
        except Exception as exc:  # noqa: BLE001 - market libraries can fail broadly.
            try:
                result_queue.put_nowait(_TimeoutResult(status="error", error=exc))
            except queue.Full:
                pass

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        return _TimeoutResult(status="timeout")

    try:
        return result_queue.get_nowait()
    except queue.Empty:
        return _TimeoutResult(status="error", error=RuntimeError("empty worker result"))


def _quote_from_spot_table(
    data: Any,
    *,
    asset_code: str,
    market_type: str,
    source: str,
) -> MarketQuote | None:
    if data is None or getattr(data, "empty", True):
        return None

    code_column = _first_existing_column(data, ("代码", "品种", "symbol", "代码名称", "名称"))
    price_column = _first_existing_column(data, ("最新价", "现价", "价格", "收盘", "最新"))
    change_column = _first_existing_column(data, ("涨跌幅", "涨幅", "涨跌", "涨跌额"))
    if code_column is None or price_column is None:
        return None

    codes = data[code_column].astype(str)
    row = data[codes == str(asset_code)]
    if row.empty:
        row = data[codes.str.contains(str(asset_code), case=False, regex=False, na=False)]
    if row.empty:
        return None

    current = row.iloc[0]
    price = _safe_float(current.get(price_column))
    if price is None or price <= 0:
        return None

    return MarketQuote(
        asset_code=asset_code,
        market_type=market_type,
        price=price,
        change_pct=_safe_float(current.get(change_column)) if change_column else None,
        quote_date=date.today().isoformat(),
        source=source,
    )


def _cn_provider_order(asset_code: str) -> tuple[str, ...]:
    code = str(asset_code)
    fund_first_prefixes = (
        "15",
        "16",
        "50",
        "51",
        "52",
        "56",
        "58",
    )
    if code.startswith(fund_first_prefixes):
        return ("fund_etf_spot_em", "fund_lof_spot_em", "stock_zh_a_spot_em")
    return ("stock_zh_a_spot_em", "fund_etf_spot_em", "fund_lof_spot_em")


def _eastmoney_secid(asset_code: str) -> str:
    code = str(asset_code)
    market_id = "1" if code.startswith(("5", "6", "9")) else "0"
    return f"{market_id}.{code}"


def _coingecko_coin_id(asset_code: str) -> str | None:
    return {
        "BTCUSDT": "bitcoin",
        "BTC-USD": "bitcoin",
        "ETHUSDT": "ethereum",
        "ETH-USD": "ethereum",
    }.get(asset_code.upper())


def _first_existing_column(data: Any, candidates: tuple[str, ...]) -> str | None:
    columns = {str(column): column for column in getattr(data, "columns", [])}
    for candidate in candidates:
        if candidate in columns:
            return columns[candidate]
    return None


def _safe_float(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None
