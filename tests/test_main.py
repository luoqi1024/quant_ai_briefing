from src import calculator, db_manager, main
from src.config import Settings
from src.data_fetcher import MarketQuote


def test_main_dry_run_completes_without_sending(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "dry_run.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))

    def fail_send(*_args, **_kwargs):
        raise AssertionError("dry-run must not send WeCom messages")

    monkeypatch.setattr("src.notifier.WeComNotifier.send_markdown", fail_send)

    exit_code = main.main(["--date", "2026-05-07", "--dry-run"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "投资日报" in output
    assert "热门投资方式观察" in output
    assert db_path.exists()


def test_main_requires_ai_settings_outside_dry_run(tmp_path, monkeypatch):
    db_path = tmp_path / "live.db"
    monkeypatch.setattr(
        main,
        "load_settings",
        lambda: Settings(database_path=str(db_path)),
    )
    monkeypatch.setattr(main, "is_daily_push_day", lambda *_args: True)

    exit_code = main.main(["--date", "2026-05-07"])

    assert exit_code == 2


def test_cached_quote_provider_avoids_duplicate_run_requests(tmp_path):
    db_path = tmp_path / "cached_quotes.db"
    db_manager.init_db(db_path)
    db_manager.seed_default_rules(db_path)
    calls = []

    def provider(asset_code, market_type, run_date):
        calls.append((asset_code, market_type, run_date))
        return {
            "asset_code": asset_code,
            "market_type": market_type,
            "price": 420.0 if asset_code == "QQQ" else 2.2,
            "change_pct": 0.0,
            "quote_date": run_date,
            "source": "mock",
        }

    cached_provider = main._cached_quote_provider(provider)
    calculator.run_shadow_accounting(
        "2026-05-07", db_path=db_path, quote_provider=cached_provider
    )
    calculator.build_portfolio_snapshot(
        "2026-05-07", db_path=db_path, quote_provider=cached_provider
    )

    assert calls == [
        ("QQQ", "US", "2026-05-07"),
        ("159915", "CN", "2026-05-07"),
    ]


def test_market_context_provider_persists_live_quote(tmp_path):
    db_path = tmp_path / "market_context.db"
    db_manager.init_db(db_path)

    class Fetcher:
        def fetch_quote(self, asset_code, market_type):
            return MarketQuote(
                asset_code=asset_code,
                market_type=market_type,
                price=420.0,
                change_pct=0.8,
                quote_date="2026-05-07",
                source="tencent_quote",
            )

    provider = main._persistent_market_context_quote_provider(
        fetcher=Fetcher(),
        db_path=str(db_path),
    )

    quote = provider("QQQ", "US", "2026-05-07")
    saved = db_manager.get_market_snapshot(
        asset_code="QQQ",
        market_type="US",
        date="2026-05-07",
        db_path=db_path,
    )

    assert quote.source == "tencent_quote"
    assert saved is not None
    assert saved["source"] == "tencent_quote"


def test_market_context_provider_uses_recent_snapshot_after_live_failure(tmp_path):
    db_path = tmp_path / "market_context.db"
    db_manager.init_db(db_path)
    db_manager.upsert_market_snapshot(
        asset_code="QQQ",
        market_type="US",
        date="2026-05-06",
        price=420.0,
        change_pct=0.8,
        source="tencent_quote",
        db_path=db_path,
    )

    class Fetcher:
        def fetch_quote(self, *_args):
            return None

    provider = main._persistent_market_context_quote_provider(
        fetcher=Fetcher(),
        db_path=str(db_path),
    )

    quote = provider("QQQ", "US", "2026-05-07")

    assert quote is not None
    assert quote["is_historical"] is True
    assert quote["stale_days"] == 1
    assert quote["quote_date"] == "2026-05-06"
    assert quote["source"] == "historical_snapshot:tencent_quote"


def test_market_context_provider_rejects_expired_snapshot(tmp_path):
    db_path = tmp_path / "market_context.db"
    db_manager.init_db(db_path)
    db_manager.upsert_market_snapshot(
        asset_code="QQQ",
        market_type="US",
        date="2026-04-29",
        price=420.0,
        change_pct=0.8,
        source="tencent_quote",
        db_path=db_path,
    )

    class Fetcher:
        def fetch_quote(self, *_args):
            return None

    provider = main._persistent_market_context_quote_provider(
        fetcher=Fetcher(),
        db_path=str(db_path),
    )

    assert provider("QQQ", "US", "2026-05-07") is None


def test_market_health_check_skips_ai_and_notification(monkeypatch, capsys):
    monkeypatch.setattr(main, "load_settings", lambda: Settings())
    monkeypatch.setattr(
        main.MarketDataFetcher,
        "fetch_quote",
        lambda self, asset_code, market_type: MarketQuote(
            asset_code=asset_code,
            market_type=market_type,
            price=100.0,
            change_pct=0.0,
            quote_date="2026-05-07",
            source="mock",
        ),
    )
    monkeypatch.setattr(
        main.AIReporter,
        "generate_report",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("AI must not run")),
    )
    monkeypatch.setattr(
        main.WeComNotifier,
        "send_markdown",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("notification must not run")
        ),
    )

    exit_code = main.main(["--date", "2026-05-07", "--check-market-data"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "coverage=9/9" in output


def test_main_skips_non_workday_before_runtime_actions(monkeypatch):
    monkeypatch.setattr(main, "load_settings", lambda: Settings())
    monkeypatch.setattr(main, "is_daily_push_day", lambda *_args: False)
    monkeypatch.setattr(
        main,
        "validate_settings",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not validate")),
    )

    exit_code = main.main(["--date", "2026-05-10"])

    assert exit_code == 0


def test_main_weekly_mode_skips_shadow_accounting(tmp_path, monkeypatch, capsys):
    db_path = tmp_path / "weekly.db"
    db_manager.init_db(db_path)
    db_manager.upsert_portfolio_snapshot(
        run_date="2026-06-19",
        currency="CNY",
        cost=100.0,
        market_value=105.0,
        floating_pnl=5.0,
        db_path=db_path,
    )
    db_manager.upsert_position_snapshot(
        run_date="2026-06-19",
        asset_code="TEST",
        asset_name="测试资产",
        currency="CNY",
        shares=10.0,
        cost=100.0,
        price=10.5,
        market_value=105.0,
        floating_pnl=5.0,
        floating_pnl_pct=5.0,
        change_pct=1.0,
        daily_pnl=1.0,
        quote_source="mock",
        quote_date="2026-06-19",
        db_path=db_path,
    )
    db_manager.upsert_market_snapshot(
        asset_code="QQQ",
        market_type="US",
        date="2026-06-19",
        price=420.0,
        change_pct=0.8,
        source="mock",
        db_path=db_path,
    )

    monkeypatch.setattr(
        main,
        "load_settings",
        lambda: Settings(
            database_path=str(db_path),
            ai_api_key="key",
            ai_url="https://example.invalid/chat",
            ai_model="model",
        ),
    )
    monkeypatch.setattr(
        main.calculator,
        "run_shadow_accounting",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("should not run")),
    )
    monkeypatch.setattr(
        "src.ai_reporter.AIReporter.generate_report",
        lambda self, snapshot, report_kind="daily": f"{report_kind}:{snapshot['week_end']}",
    )

    exit_code = main.main(["--date", "2026-06-21", "--report-kind", "weekly"])
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "weekly:2026-06-19" in output
