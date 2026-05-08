from src import calculator, db_manager, main
from src.config import Settings


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
