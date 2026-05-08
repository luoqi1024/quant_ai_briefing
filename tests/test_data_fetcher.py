import time

import pandas as pd
import pytest

from src import data_fetcher
from src.data_fetcher import MarketDataFetcher, MarketQuote


def test_fetch_quote_returns_none_when_external_api_fails(monkeypatch):
    fetcher = MarketDataFetcher(timeout=0.1, retries=0)

    def fail(_asset_code):
        raise RuntimeError("boom")

    monkeypatch.setattr(fetcher, "_fetch_us_stooq_quote", fail)
    monkeypatch.setattr(fetcher, "_fetch_us_quote", fail)

    assert fetcher.fetch_quote("QQQ", "US") is None


def test_fetch_quote_uses_mockable_fetch_methods(monkeypatch):
    fetcher = MarketDataFetcher(timeout=0.1, retries=0)
    quote = MarketQuote(
        asset_code="QQQ",
        market_type="US",
        price=420.0,
        change_pct=0.8,
        quote_date="2026-05-07",
        source="mock",
    )
    monkeypatch.setattr(fetcher, "_fetch_us_stooq_quote", lambda _asset_code: quote)

    assert fetcher.fetch_quote("QQQ", "US") == quote


def test_cn_quote_parses_akshare_chinese_columns():
    frame = pd.DataFrame(
        [
            {"代码": "159915", "最新价": "2.20", "涨跌幅": "1.05"},
        ]
    )

    quote = data_fetcher._quote_from_spot_table(
        frame,
        asset_code="159915",
        market_type="CN",
        source="akshare:test",
    )

    assert quote is not None
    assert quote == MarketQuote(
        asset_code="159915",
        market_type="CN",
        price=2.2,
        change_pct=1.05,
        quote_date=quote.quote_date,
        source="akshare:test",
    )


def test_fetch_quote_timeout_returns_promptly(monkeypatch):
    fetcher = MarketDataFetcher(timeout=0.05, retries=0)

    def slow(_asset_code):
        time.sleep(2)
        return None

    monkeypatch.setattr(fetcher, "_fetch_us_stooq_quote", slow)
    monkeypatch.setattr(fetcher, "_fetch_us_quote", lambda _asset_code: None)
    start = time.monotonic()

    assert fetcher.fetch_quote("QQQ", "US") is None
    assert time.monotonic() - start < 0.5


def test_cn_provider_order_prioritizes_fund_sources_for_etf_codes():
    assert data_fetcher._cn_provider_order("159915") == (
        "fund_etf_spot_em",
        "fund_lof_spot_em",
        "stock_zh_a_spot_em",
    )
    assert data_fetcher._cn_provider_order("600519")[0] == "stock_zh_a_spot_em"


def test_cn_fetch_tries_next_provider_after_timeout(monkeypatch):
    fetcher = MarketDataFetcher(timeout=0.05, retries=0)
    calls = []

    def fake_provider(asset_code, provider_name):
        calls.append(provider_name)
        if provider_name == "fund_etf_spot_em":
            time.sleep(2)
            return None
        return MarketQuote(
            asset_code=asset_code,
            market_type="CN",
            price=2.2,
            change_pct=1.0,
            quote_date="2026-05-07",
            source=f"mock:{provider_name}",
        )

    monkeypatch.setattr(fetcher, "_fetch_cn_eastmoney_quote", lambda _asset_code: None)
    monkeypatch.setattr(fetcher, "_fetch_cn_quote_from_provider", fake_provider)
    start = time.monotonic()

    quote = fetcher.fetch_quote("159915", "CN")

    assert quote is not None
    assert quote.source == "mock:fund_lof_spot_em"
    assert calls[:2] == ["fund_etf_spot_em", "fund_lof_spot_em"]
    assert time.monotonic() - start < 0.5


class _Response:
    def __init__(self, text="", payload=None):
        self.text = text
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def test_us_stooq_backup_parses_single_quote(monkeypatch):
    fetcher = MarketDataFetcher(timeout=0.1, retries=0)

    def fake_get(url, **kwargs):
        assert url == "https://stooq.com/q/l/"
        assert kwargs["params"] == {"s": "qqq.us", "i": "d"}
        return _Response(
            "QQQ.US,20260507,182323,696.53,701.24,693.79,695.13,14128411,\r\n"
        )

    monkeypatch.setattr(data_fetcher.requests, "get", fake_get)

    quote = fetcher._fetch_us_stooq_quote("QQQ")

    assert quote == MarketQuote(
        asset_code="QQQ",
        market_type="US",
        price=695.13,
        change_pct=pytest.approx(-0.20099636770849458),
        quote_date="2026-05-07",
        source="stooq",
    )


def test_us_stooq_backup_skips_header(monkeypatch):
    fetcher = MarketDataFetcher(timeout=0.1, retries=0)

    def fake_get(url, **kwargs):
        return _Response(
            "Symbol,Date,Time,Open,High,Low,Close,Volume\r\n"
            "SPY.US,20260507,182323,520.00,522.00,519.00,521.50,12345,\r\n"
        )

    monkeypatch.setattr(data_fetcher.requests, "get", fake_get)

    quote = fetcher._fetch_us_stooq_quote("SPY")

    assert quote is not None
    assert quote.price == 521.50
    assert quote.quote_date == "2026-05-07"


def test_cn_eastmoney_backup_parses_scaled_quote(monkeypatch):
    fetcher = MarketDataFetcher(timeout=0.1, retries=0)

    def fake_get(url, **kwargs):
        assert url == "https://push2.eastmoney.com/api/qt/stock/get"
        assert kwargs["params"]["secid"] == "0.159915"
        return _Response(
            payload={
                "data": {
                    "f43": 3845,
                    "f57": "159915",
                    "f58": "创业板ETF易方达",
                    "f59": 3,
                    "f60": 3786,
                    "f169": 59,
                    "f170": 156,
                }
            }
        )

    monkeypatch.setattr(data_fetcher.requests, "get", fake_get)

    quote = fetcher._fetch_cn_eastmoney_quote("159915")

    assert quote is not None
    assert quote.asset_code == "159915"
    assert quote.market_type == "CN"
    assert quote.price == pytest.approx(3.845)
    assert quote.change_pct == pytest.approx(1.56)
    assert quote.source == "eastmoney"


def test_eastmoney_secid_selects_exchange_prefix():
    assert data_fetcher._eastmoney_secid("159915") == "0.159915"
    assert data_fetcher._eastmoney_secid("600519") == "1.600519"
    assert data_fetcher._eastmoney_secid("510300") == "1.510300"


def test_fund_estimate_quote_parses_jsonp(monkeypatch):
    fetcher = MarketDataFetcher(timeout=0.1, retries=0)

    def fake_get(url, **kwargs):
        assert url == "https://fundgz.1234567.com.cn/js/000834.js"
        return _Response(
            'jsonpgz({"fundcode":"000834","jzrq":"2026-05-06",'
            '"dwjz":"6.2381","gsz":"6.2729","gszzl":"0.56",'
            '"gztime":"2026-05-07 22:29"});'
        )

    monkeypatch.setattr(data_fetcher.requests, "get", fake_get)

    quote = fetcher._fetch_fund_estimate_quote("000834")

    assert quote == MarketQuote(
        asset_code="000834",
        market_type="FUND",
        price=6.2729,
        change_pct=0.56,
        quote_date="2026-05-07",
        source="eastmoney_fund_estimate",
    )


def test_fund_nav_quote_parses_latest_nav(monkeypatch):
    fetcher = MarketDataFetcher(timeout=0.1, retries=0)

    def fake_get(url, **kwargs):
        assert url == "https://api.fund.eastmoney.com/f10/lsjz"
        assert kwargs["params"]["fundCode"] == "007542"
        return _Response(
            payload={
                "Data": {
                    "LSJZList": [
                        {
                            "FSRQ": "2026-05-07",
                            "DWJZ": "1.1738",
                            "JZZZL": "0.01",
                        }
                    ]
                }
            }
        )

    monkeypatch.setattr(data_fetcher.requests, "get", fake_get)

    quote = fetcher._fetch_fund_nav_quote("007542")

    assert quote == MarketQuote(
        asset_code="007542",
        market_type="FUND",
        price=1.1738,
        change_pct=0.01,
        quote_date="2026-05-07",
        source="eastmoney_fund_nav",
    )


def test_sge_gold_quote_parses_sina_au9999(monkeypatch):
    fetcher = MarketDataFetcher(timeout=0.1, retries=0)

    def fake_get(url, **kwargs):
        assert url == "https://hq.sinajs.cn/list=SGE_AU9999"
        return _Response(
            'var hq_str_SGE_AU9999="AU9999,沪  金99,Au99.99,1035.00,'
            '1038.78,1038.94,1038.20,1045.00,1035.00,1038.60,'
            '1035.00,1044.00,11.00,195.00,1398.00,1452208125.00,'
            '2026-05-08 00:49:43,-0.35%";'
        )

    monkeypatch.setattr(data_fetcher.requests, "get", fake_get)

    quote = fetcher._fetch_sge_gold_quote("GOLD_GRAM_CNY")

    assert quote == MarketQuote(
        asset_code="GOLD_GRAM_CNY",
        market_type="GOLD",
        price=1038.20,
        change_pct=-0.35,
        quote_date="2026-05-08",
        source="sina_sge_au9999",
    )


def test_crypto_binance_quote_parses_24hr_ticker(monkeypatch):
    fetcher = MarketDataFetcher(timeout=0.1, retries=0)

    def fake_get(url, **kwargs):
        assert url == "https://api.binance.com/api/v3/ticker/24hr"
        assert kwargs["params"] == {"symbol": "BTCUSDT"}
        return _Response(
            payload={
                "lastPrice": "68000.50",
                "priceChangePercent": "2.34",
            }
        )

    monkeypatch.setattr(data_fetcher.requests, "get", fake_get)

    quote = fetcher._fetch_crypto_binance_quote("BTCUSDT")

    assert quote is not None
    assert quote.asset_code == "BTCUSDT"
    assert quote.market_type == "CRYPTO"
    assert quote.price == 68000.50
    assert quote.change_pct == 2.34
    assert quote.source == "binance_24hr"


def test_crypto_coingecko_quote_parses_simple_price(monkeypatch):
    fetcher = MarketDataFetcher(timeout=0.1, retries=0)

    def fake_get(url, **kwargs):
        assert url == "https://api.coingecko.com/api/v3/simple/price"
        assert kwargs["params"]["ids"] == "bitcoin"
        return _Response(
            payload={
                "bitcoin": {
                    "usd": 68123.45,
                    "usd_24h_change": -1.23,
                }
            }
        )

    monkeypatch.setattr(data_fetcher.requests, "get", fake_get)

    quote = fetcher._fetch_crypto_coingecko_quote("BTCUSDT")

    assert quote is not None
    assert quote.asset_code == "BTCUSDT"
    assert quote.market_type == "CRYPTO"
    assert quote.price == 68123.45
    assert quote.change_pct == -1.23
    assert quote.source == "coingecko_simple_price"
