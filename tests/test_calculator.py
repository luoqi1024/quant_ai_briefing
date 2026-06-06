from src import calculator, db_manager


def _setup_db(tmp_path):
    db_path = tmp_path / "quant_test.db"
    db_manager.init_db(db_path)
    db_manager.seed_default_rules(db_path)
    return db_path


def _mock_quote(asset_code, market_type, run_date):
    prices = {
        "QQQ": {"price": 400.0, "change_pct": 0.5},
        "159915": {"price": 2.0, "change_pct": -0.2},
    }
    quote = prices[asset_code]
    return {
        "asset_code": asset_code,
        "market_type": market_type,
        "price": quote["price"],
        "change_pct": quote["change_pct"],
        "quote_date": run_date,
        "source": "mock",
    }


def test_should_trigger_rule_weekly_and_monthly():
    weekly_rule = {"enabled": 1, "freq_type": "weekly", "freq_value": 4}
    monthly_rule = {"enabled": 1, "freq_type": "monthly", "freq_value": 7}
    daily_rule = {"enabled": 1, "freq_type": "daily", "freq_value": 0}

    assert calculator.should_trigger_rule(weekly_rule, "2026-05-07") is True
    assert calculator.should_trigger_rule(weekly_rule, "2026-05-08") is False
    assert calculator.should_trigger_rule(monthly_rule, "2026-05-07") is True
    assert calculator.should_trigger_rule(monthly_rule, "2026-05-08") is False
    assert calculator.should_trigger_rule(daily_rule, "2026-05-07") is True
    assert calculator.should_trigger_rule(daily_rule, "2026-05-08") is True
    assert calculator.should_trigger_rule(daily_rule, "2026-05-09") is False
    assert calculator.should_trigger_rule(daily_rule, "2026-05-10") is False


def test_run_shadow_accounting_inserts_triggered_trades_once(tmp_path):
    db_path = _setup_db(tmp_path)

    first = calculator.run_shadow_accounting(
        "2026-05-07", db_path=db_path, quote_provider=_mock_quote
    )
    second = calculator.run_shadow_accounting(
        "2026-05-07", db_path=db_path, quote_provider=_mock_quote
    )

    assert [item["status"] for item in first["results"]] == ["inserted", "inserted"]
    assert [item["status"] for item in second["results"]] == [
        "duplicate",
        "duplicate",
    ]
    positions = db_manager.get_total_positions(db_path)
    assert len(positions) == 2


def test_run_shadow_accounting_skips_missing_quotes(tmp_path):
    db_path = _setup_db(tmp_path)

    result = calculator.run_shadow_accounting(
        "2026-05-07", db_path=db_path, quote_provider=lambda *_args: None
    )

    assert [item["status"] for item in result["results"]] == [
        "missing_quote",
        "missing_quote",
    ]
    assert db_manager.get_total_positions(db_path) == []


def test_build_portfolio_snapshot_calculates_positions_and_pnl(tmp_path):
    db_path = _setup_db(tmp_path)
    calculator.run_shadow_accounting(
        "2026-05-07", db_path=db_path, quote_provider=_mock_quote
    )

    snapshot = calculator.build_portfolio_snapshot(
        "2026-05-07",
        db_path=db_path,
        quote_provider=lambda asset_code, market_type, run_date: {
            "asset_code": asset_code,
            "market_type": market_type,
            "price": 420.0 if asset_code == "QQQ" else 2.2,
            "change_pct": 0.8 if asset_code == "QQQ" else 1.0,
            "quote_date": run_date,
            "source": "mock",
        },
    )

    qqq = next(item for item in snapshot["positions"] if item["asset_code"] == "QQQ")
    cn = next(item for item in snapshot["positions"] if item["asset_code"] == "159915")

    assert qqq["shares"] == 1.25
    assert qqq["cost"] == 500.0
    assert qqq["market_value"] == 525.0
    assert qqq["floating_pnl"] == 25.0
    assert qqq["floating_pnl_pct"] == 5.0
    assert qqq["daily_pnl"] == 525.0 * 0.8 / 100.8

    assert cn["shares"] == 500.0
    assert cn["cost"] == 1000.0
    assert cn["market_value"] == 1100.0
    assert cn["floating_pnl"] == 100.0
    assert cn["daily_pnl"] == 1100.0 * 1.0 / 101.0
    assert snapshot["totals_by_currency"]["USD"] == {
        "cost": 500.0,
        "market_value": 525.0,
        "floating_pnl": 25.0,
    }
    assert snapshot["totals_by_currency"]["CNY"] == {
        "cost": 1000.0,
        "market_value": 1100.0,
        "floating_pnl": 100.0,
    }


def test_build_portfolio_snapshot_falls_back_to_latest_cached_quote(tmp_path):
    db_path = _setup_db(tmp_path)
    calculator.run_shadow_accounting(
        "2026-05-07", db_path=db_path, quote_provider=_mock_quote
    )

    snapshot = calculator.build_portfolio_snapshot(
        "2026-05-08",
        db_path=db_path,
        quote_provider=lambda *_args: None,
    )

    qqq = next(item for item in snapshot["positions"] if item["asset_code"] == "QQQ")
    cn = next(item for item in snapshot["positions"] if item["asset_code"] == "159915")

    assert qqq["price"] == 400.0
    assert qqq["market_value"] == 500.0
    assert qqq["quote_source"] == "mock"
    assert qqq["quote_date"] == "2026-05-07"
    assert cn["price"] == 2.0
    assert cn["market_value"] == 1000.0


def test_build_portfolio_snapshot_falls_back_to_average_cost_without_quote(tmp_path):
    db_path = _setup_db(tmp_path)
    rule = db_manager.get_all_rules(db_path=db_path)[0]
    db_manager.insert_trade(
        rule_id=rule["id"],
        date="2026-05-07",
        asset_code=rule["asset_code"],
        action="buy",
        currency=rule["currency"],
        amount=500.0,
        price=100.0,
        shares=5.0,
        db_path=db_path,
    )

    snapshot = calculator.build_portfolio_snapshot(
        "2026-05-08",
        db_path=db_path,
        quote_provider=lambda *_args: None,
    )
    qqq = snapshot["positions"][0]

    assert qqq["price"] == 100.0
    assert qqq["market_value"] == 500.0
    assert qqq["floating_pnl"] == 0.0
    assert qqq["quote_source"] == "average_cost_fallback"
