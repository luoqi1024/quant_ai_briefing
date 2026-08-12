"""Broader market context for the briefing prompts."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


QuoteProvider = Callable[[str, str, str], Any]
MIN_SUFFICIENT_QUOTES = 3


@dataclass(frozen=True)
class WatchAsset:
    name: str
    asset_code: str
    market_type: str
    category: str


POPULAR_INVESTMENT_WATCHLIST: tuple[WatchAsset, ...] = (
    WatchAsset("纳指科技", "QQQ", "US", "美股科技"),
    WatchAsset("标普500", "SPY", "US", "美股宽基"),
    WatchAsset("沪深300", "510300", "CN", "A股宽基"),
    WatchAsset("创业板", "159915", "CN", "A股成长"),
    WatchAsset("港股科技", "513180", "CN", "港股科技"),
    WatchAsset("黄金", "GOLD_GRAM_CNY", "GOLD", "贵金属"),
    WatchAsset("中长期国债", "511010", "CN", "债券"),
    WatchAsset("原油", "USO", "US", "商品"),
    WatchAsset("比特币", "BTCUSDT", "CRYPTO", "数字资产"),
)


def build_market_context(
    run_date: str,
    *,
    quote_provider: QuoteProvider | None,
    watchlist: tuple[WatchAsset, ...] = POPULAR_INVESTMENT_WATCHLIST,
) -> dict[str, Any]:
    """Fetch a compact cross-asset watchlist for the report prompt."""

    if quote_provider is None:
        return {
            "run_date": run_date,
            "popular_investments": [],
            "available_count": 0,
            "missing_count": len(watchlist),
            "total_count": len(watchlist),
            "is_sufficient": False,
        }

    items: list[dict[str, Any]] = []
    for asset in watchlist:
        quote = quote_provider(asset.asset_code, asset.market_type, run_date)
        price = _quote_value(quote, "price")
        if price is None or float(price) <= 0:
            items.append(
                {
                    "name": asset.name,
                    "category": asset.category,
                    "asset_code": asset.asset_code,
                    "market_type": asset.market_type,
                    "status": "missing_quote",
                }
            )
            continue

        is_historical = bool(_quote_value(quote, "is_historical", False))
        item = {
            "name": asset.name,
            "category": asset.category,
            "asset_code": asset.asset_code,
            "market_type": asset.market_type,
            "price": float(price),
            "change_pct": _quote_value(quote, "change_pct"),
            "quote_date": _quote_value(quote, "quote_date", _quote_value(quote, "date")),
            "source": _quote_value(quote, "source"),
            "status": "historical_snapshot" if is_historical else "ok",
        }
        if is_historical:
            item["stale_days"] = _quote_value(quote, "stale_days")
        items.append(item)

    available_count = sum(
        item.get("status") in {"ok", "historical_snapshot"} for item in items
    )
    total_count = len(items)
    required_count = min(MIN_SUFFICIENT_QUOTES, total_count)
    return {
        "run_date": run_date,
        "popular_investments": items,
        "available_count": available_count,
        "missing_count": total_count - available_count,
        "total_count": total_count,
        "is_sufficient": total_count > 0 and available_count >= required_count,
    }


def _quote_value(quote: Any, key: str, default: Any = None) -> Any:
    if quote is None:
        return default
    if isinstance(quote, dict):
        return quote.get(key, default)
    return getattr(quote, key, default)

