"""Portfolio calculation and shadow-accounting logic."""

from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from typing import Any

from src import db_manager
from src.business_calendar import as_date, is_daily_push_day, weekly_summary_window

QuoteProvider = Callable[[str, str, str], Any]


def _as_date(value: str | date) -> date:
    return as_date(value)


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
        return is_daily_push_day(target_date)
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
    cached_quote = db_manager.get_market_snapshot(
        asset_code=asset_code,
        market_type=market_type,
        date=run_date,
        db_path=db_path,
    )
    if _quote_value(cached_quote, "source") == "manual-reconcile-screenshot":
        return cached_quote

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


def save_portfolio_snapshot(
    snapshot: dict[str, Any],
    *,
    db_path: str | Path | None = None,
) -> None:
    """Persist one daily snapshot for later weekly summaries."""

    run_date = snapshot["run_date"]
    for currency, values in snapshot.get("totals_by_currency", {}).items():
        db_manager.upsert_portfolio_snapshot(
            run_date=run_date,
            currency=currency,
            cost=float(values["cost"]),
            market_value=float(values["market_value"]),
            floating_pnl=float(values["floating_pnl"]),
            db_path=db_path,
        )

    for position in snapshot.get("positions", []):
        db_manager.upsert_position_snapshot(
            run_date=run_date,
            asset_code=position["asset_code"],
            asset_name=position["asset_name"],
            currency=position["currency"],
            shares=float(position["shares"]),
            cost=float(position["cost"]),
            price=float(position["price"]),
            market_value=float(position["market_value"]),
            floating_pnl=float(position["floating_pnl"]),
            floating_pnl_pct=float(position["floating_pnl_pct"]),
            change_pct=_as_optional_float(position.get("change_pct")),
            daily_pnl=_as_optional_float(position.get("daily_pnl")),
            quote_source=str(position.get("quote_source") or ""),
            quote_date=position.get("quote_date"),
            db_path=db_path,
        )


def build_weekly_summary(
    run_date: str | date,
    *,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Build a weekly report payload from persisted daily snapshots."""

    window = weekly_summary_window(run_date)
    week_start = window.week_start.isoformat()
    week_end = window.week_end.isoformat()

    end_snapshot_date = db_manager.get_latest_saved_snapshot_date_in_range(
        start_date=week_start,
        end_date=week_end,
        db_path=db_path,
    )
    if end_snapshot_date is None:
        return {
            "report_kind": "weekly",
            "run_date": _as_date_string(run_date),
            "week_start": week_start,
            "week_end": week_end,
            "has_week_data": False,
            "positions": [],
            "totals_by_currency": {},
            "baseline_totals_by_currency": {},
            "weekly_changes_by_currency": {},
            "top_contributor": None,
            "top_detractor": None,
        }

    baseline_date = db_manager.get_latest_saved_snapshot_date_before(
        before_date=week_start,
        db_path=db_path,
    )
    end_positions = db_manager.get_position_snapshots(end_snapshot_date, db_path=db_path)
    end_totals_rows = db_manager.get_portfolio_snapshots(end_snapshot_date, db_path=db_path)
    baseline_totals_rows = (
        db_manager.get_portfolio_snapshots(baseline_date, db_path=db_path)
        if baseline_date
        else []
    )
    baseline_position_rows = (
        db_manager.get_position_snapshots(baseline_date, db_path=db_path)
        if baseline_date
        else []
    )

    totals_by_currency = _totals_by_currency_from_rows(end_totals_rows)
    baseline_totals_by_currency = _totals_by_currency_from_rows(baseline_totals_rows)
    baseline_positions_by_key = {
        (row["asset_code"], row["currency"]): row for row in baseline_position_rows
    }
    positions = []
    for row in end_positions:
        baseline = baseline_positions_by_key.get((row["asset_code"], row["currency"]))
        weekly_market_value_change = float(row["market_value"]) - float(
            baseline["market_value"]
        ) if baseline else None
        weekly_floating_pnl_change = float(row["floating_pnl"]) - float(
            baseline["floating_pnl"]
        ) if baseline else None
        position = dict(row)
        position["weekly_market_value_change"] = weekly_market_value_change
        position["weekly_floating_pnl_change"] = weekly_floating_pnl_change
        positions.append(position)

    contributor_positions = [
        item for item in positions if item.get("weekly_floating_pnl_change") is not None
    ]
    top_contributor = (
        max(contributor_positions, key=lambda item: item["weekly_floating_pnl_change"])
        if contributor_positions
        else None
    )
    top_detractor = (
        min(contributor_positions, key=lambda item: item["weekly_floating_pnl_change"])
        if contributor_positions
        else None
    )

    return {
        "report_kind": "weekly",
        "run_date": _as_date_string(run_date),
        "week_start": week_start,
        "week_end": week_end,
        "has_week_data": True,
        "snapshot_date": end_snapshot_date,
        "baseline_date": baseline_date,
        "positions": positions,
        "totals_by_currency": totals_by_currency,
        "baseline_totals_by_currency": baseline_totals_by_currency,
        "weekly_changes_by_currency": _weekly_changes_by_currency(
            totals_by_currency,
            baseline_totals_by_currency,
        ),
        "top_contributor": top_contributor,
        "top_detractor": top_detractor,
    }


def _totals_by_currency_from_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    return {
        row["currency"]: {
            "cost": float(row["cost"]),
            "market_value": float(row["market_value"]),
            "floating_pnl": float(row["floating_pnl"]),
        }
        for row in rows
    }


def _weekly_changes_by_currency(
    current: dict[str, dict[str, float]],
    baseline: dict[str, dict[str, float]],
) -> dict[str, dict[str, float | None]]:
    currencies = set(current) | set(baseline)
    changes: dict[str, dict[str, float | None]] = {}
    for currency in currencies:
        current_values = current.get(currency)
        baseline_values = baseline.get(currency)
        if current_values is None or baseline_values is None:
            changes[currency] = {
                "market_value_change": None,
                "floating_pnl_change": None,
            }
            continue
        changes[currency] = {
            "market_value_change": current_values["market_value"]
            - baseline_values["market_value"],
            "floating_pnl_change": current_values["floating_pnl"]
            - baseline_values["floating_pnl"],
        }
    return changes


def _as_optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)
