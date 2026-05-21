"""Command line entry point for the daily quant briefing."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from typing import Any

from src import calculator, db_manager
from src.ai_reporter import AIReportError, AIReporter
from src.config import ConfigError, Settings, load_settings, validate_settings
from src.data_fetcher import MarketDataFetcher
from src.market_context import build_market_context
from src.notifier import WeComNotifier


logger = logging.getLogger(__name__)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a quant AI briefing.")
    parser.add_argument(
        "--date",
        dest="run_date",
        type=_valid_date,
        default=date.today().isoformat(),
        help="Run date in YYYY-MM-DD format.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run end-to-end with mock quotes and no WeCom send.",
    )
    parser.add_argument(
        "--send",
        action="store_true",
        help="Send the report to WeCom. By default the report is only printed.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_settings()
    _setup_logging(settings.log_level)

    logger.info("Starting quant briefing for %s", args.run_date)
    logger.info("Dry-run mode: %s; send enabled: %s", args.dry_run, args.send)
    try:
        validate_settings(settings, dry_run=args.dry_run, send=args.send)
    except ConfigError as exc:
        logger.error("Configuration validation failed: %s", exc)
        return 2
    logger.info("Configuration validation passed")

    db_manager.init_db(settings.database_path)
    inserted_rules = db_manager.seed_default_rules(settings.database_path)
    logger.info("Database initialized; seeded %s default rules", inserted_rules)

    quote_provider = (
        _dry_run_quote_provider
        if args.dry_run
        else _live_quote_provider(MarketDataFetcher())
    )
    quote_provider = _cached_quote_provider(quote_provider)
    accounting_result = calculator.run_shadow_accounting(
        args.run_date,
        db_path=settings.database_path,
        quote_provider=quote_provider,
    )
    logger.info("Shadow-accounting result: %s", accounting_result)

    snapshot = calculator.build_portfolio_snapshot(
        args.run_date,
        db_path=settings.database_path,
        quote_provider=quote_provider,
    )
    snapshot["market_context"] = build_market_context(
        args.run_date,
        quote_provider=quote_provider
        if args.dry_run
        else _cached_quote_provider(
            _live_quote_provider(MarketDataFetcher(timeout=3.0, retries=0))
        ),
    )
    logger.info("Portfolio snapshot totals: %s", snapshot["totals_by_currency"])

    reporter_settings = Settings() if args.dry_run else settings
    try:
        report = AIReporter(
            settings=reporter_settings,
            fallback_on_failure=args.dry_run,
        ).generate_report(snapshot)
    except AIReportError as exc:
        logger.error("AI report generation failed: %s", exc)
        return 2

    print(report)

    if args.send and not args.dry_run:
        sent = WeComNotifier(settings=settings).send_markdown(report)
        logger.info("WeCom send result: %s", sent)
        return 0 if sent else 2

    logger.info("WeCom send skipped")
    return 0


def _valid_date(value: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as exc:
        raise argparse.ArgumentTypeError("date must use YYYY-MM-DD format") from exc
    return value


def _setup_logging(level_name: str) -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    file_handler = logging.FileHandler("quant_daily.log", encoding="utf-8")
    file_handler.setFormatter(formatter)

    root.addHandler(console)
    root.addHandler(file_handler)


def _dry_run_quote_provider(asset_code: str, market_type: str, run_date: str) -> dict[str, Any]:
    prices = {
        "QQQ": {"price": 420.0, "change_pct": 0.8},
        "159915": {"price": 2.2, "change_pct": 1.0},
        "000834": {"price": 6.2729, "change_pct": 0.56},
        "270042": {"price": 8.1059, "change_pct": 0.56},
        "007542": {"price": 1.1736, "change_pct": -0.01},
        "GOLD_GRAM_CNY": {"price": 1059.1635555555555, "change_pct": 0.0},
        "SPY": {"price": 520.0, "change_pct": 0.4},
        "510300": {"price": 3.8, "change_pct": -0.3},
        "513180": {"price": 0.62, "change_pct": 1.2},
        "511010": {"price": 110.0, "change_pct": 0.05},
        "USO": {"price": 72.0, "change_pct": -0.8},
        "BTCUSDT": {"price": 68000.0, "change_pct": 2.4},
    }
    quote = prices.get(asset_code, {"price": 100.0, "change_pct": 0.0})
    return {
        "asset_code": asset_code,
        "market_type": market_type,
        "price": quote["price"],
        "change_pct": quote["change_pct"],
        "quote_date": run_date,
        "source": "dry-run",
    }


def _live_quote_provider(fetcher: MarketDataFetcher):
    def provider(asset_code: str, market_type: str, _run_date: str):
        return fetcher.fetch_quote(asset_code, market_type)

    return provider


def _cached_quote_provider(provider):
    cache: dict[tuple[str, str, str], Any] = {}

    def cached(asset_code: str, market_type: str, run_date: str):
        key = (asset_code, market_type, run_date)
        if key not in cache:
            cache[key] = provider(asset_code, market_type, run_date)
        return cache[key]

    return cached


if __name__ == "__main__":
    sys.exit(main())
