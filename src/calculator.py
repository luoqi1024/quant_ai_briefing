"""Portfolio calculation and shadow-accounting logic."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from src import db_manager

QuoteProvider = Callable[[str, str, str], Any]


def _as_date(value: str | date) -> date:
    if isinstance(value, date):
        return value
    return datetime.strptime(value, "%Y-%m-%d").date()


def _as_date_string(value: str | date) -> str:
    return _as_date(value).isoformat()


def _quote_value(quote: Any, key: str, default: Any = None) -> Any:
    if quote is None:
        return default
    if isinstance(quote, dict):
        return quote.get(key, default)
    return getattr(quote, key, default)


def should_trigger_rule(rule: dict[str, Any], run_date: str | date) -> bool:
    """Return whether a weekly/monthly rule triggers on run_date."""

    target_date = _as_date(run_date)
    if not int(rule.get("enabled", 1)):
        return False

    freq_type = rule["freq_type"]
    freq_value = int(rule["freq_value"])

    if freq_type == "weekly":
        return target_date.isoweekday() == freq_value
    if freq_type == "monthly":
        return target_date.day == freq_value
    if freq_type == "daily":
        return target_date.isoweekday() <= 5
    return False


def _get_quote(
    *,
    asset_code: str,
    market_type: str,
    run_date: str,
    quote_provider: QuoteProvider | None,
    db_path: str | Path | None,
) -> Any:
    if quote_provider is not None:
        return quote_provider(asset_code, market_type, run_date)
    return db_manager.get_market_snapshot(
        asset_code=asset_code,
        market_type=market_type,
        date=run_date,
        db_path=db_path,
    )


def _get_quote_for_snapshot(
    *,
    asset_code: str,
    market_type: str,
    run_date: str,
    quote_provider: QuoteProvider | None,
    db_path: str | Path | None,
) -> Any:
    quote = _get_quote(
        asset_code=asset_code,
        market_type=market_type,
        run_date=run_date,
        quote_provider=quote_provider,
        db_path=db_path,
    )
    price = _quote_value(quote, "price")
    if price is not None and float(price) > 0:
        return quote
    return db_manager.get_latest_market_snapshot(
        asset_code=asset_code,
        market_type=market_type,
        max_date=run_date,
        db_path=db_path,
    )


def run_shadow_accounting(
    run_date: str | date,
    *,
    db_path: str | Path | None = None,
    quote_provider: QuoteProvider | None = None,
) -> dict[str, Any]:
    """Create shadow trades for triggered rules using supplied or cached quotes."""

    run_date_str = _as_date_string(run_date)
    results: list[dict[str, Any]] = []

    for rule in db_manager.get_all_rules(enabled_only=True, db_path=db_path):
        if not should_trigger_rule(rule, run_date_str):
            results.append(
                {
                    "rule_id": rule["id"],
                    "asset_code": rule["asset_code"],
                    "status": "not_triggered",
                }
            )
            continue

        quote = _get_quote(
            asset_code=rule["asset_code"],
            market_type=rule["market_type"],
            run_date=run_date_str,
            quote_provider=quote_provider,
            db_path=db_path,
        )
        price = _quote_value(quote, "price")
        if price is None or float(price) <= 0:
            results.append(
                {
                    "rule_id": rule["id"],
                    "asset_code": rule["asset_code"],
                    "status": "missing_quote",
                }
            )
            continue

        quote_date = _quote_value(quote, "quote_date", run_date_str)
        change_pct = _quote_value(quote, "change_pct")
        source = _quote_value(quote, "source", "mock")
        db_manager.upsert_market_snapshot(
            asset_code=rule["asset_code"],
            market_type=rule["market_type"],
            date=quote_date,
            price=float(price),
            change_pct=None if change_pct is None else float(change_pct),
            source=source,
            db_path=db_path,
        )

        shares = float(rule["amount"]) / float(price)
        inserted = db_manager.insert_trade(
            rule_id=rule["id"],
            date=run_date_str,
            asset_code=rule["asset_code"],
            action="buy",
            currency=rule["currency"],
            amount=float(rule["amount"]),
            price=float(price),
            shares=shares,
            db_path=db_path,
        )
        results.append(
            {
                "rule_id": rule["id"],
                "asset_code": rule["asset_code"],
                "status": "inserted" if inserted else "duplicate",
                "price": float(price),
                "shares": shares,
            }
        )

    return {"run_date": run_date_str, "results": results}


def build_portfolio_snapshot(
    run_date: str | date,
    *,
    db_path: str | Path | None = None,
    quote_provider: QuoteProvider | None = None,
) -> dict[str, Any]:
    """Build current positions, market values, and PnL by currency."""

    run_date_str = _as_date_string(run_date)
    rules_by_asset = {
        rule["asset_code"]: rule
        for rule in db_manager.get_all_rules(enabled_only=False, db_path=db_path)
    }
    positions = []
    totals_by_currency: dict[str, dict[str, float]] = {}

    for position in db_manager.get_total_positions(db_path=db_path):
        rule = rules_by_asset.get(position["asset_code"], {})
        market_type = rule.get("market_type", "")
        quote = _get_quote_for_snapshot(
            asset_code=position["asset_code"],
            market_type=market_type,
            run_date=run_date_str,
            quote_provider=quote_provider,
            db_path=db_path,
        )
        price = _quote_value(quote, "price")
        quote_source = _quote_value(quote, "source", "latest_snapshot")
        quote_date = _quote_value(quote, "date", _quote_value(quote, "quote_date"))
        shares = float(position["shares"])
        cost = float(position["cost"])
        if price is None or float(price) <= 0:
            price = (cost / shares) if shares else 0.0
            quote_source = "average_cost_fallback"
            quote_date = None

        price = float(price)
        market_value = shares * price
        floating_pnl = market_value - cost
        floating_pnl_pct = (floating_pnl / cost * 100) if cost else 0.0
        change_pct = _quote_value(quote, "change_pct")
        daily_pnl = (
            market_value * float(change_pct) / (100 + float(change_pct))
            if change_pct is not None and (100 + float(change_pct)) != 0
            else None
        )
        currency = position["currency"]

        positions.append(
            {
                "asset_code": position["asset_code"],
                "asset_name": position["asset_name"],
                "currency": currency,
                "shares": shares,
                "cost": cost,
                "price": price,
                "market_value": market_value,
                "floating_pnl": floating_pnl,
                "floating_pnl_pct": floating_pnl_pct,
                "change_pct": change_pct,
                "daily_pnl": daily_pnl,
                "quote_source": quote_source,
                "quote_date": quote_date,
            }
        )

        totals = totals_by_currency.setdefault(
            currency, {"cost": 0.0, "market_value": 0.0, "floating_pnl": 0.0}
        )
        totals["cost"] += cost
        totals["market_value"] += market_value
        totals["floating_pnl"] += floating_pnl

    return {
        "run_date": run_date_str,
        "positions": positions,
        "totals_by_currency": totals_by_currency,
    }
