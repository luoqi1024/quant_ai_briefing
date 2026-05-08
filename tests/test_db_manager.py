import sqlite3

from src import db_manager


def test_init_db_creates_required_tables(tmp_path):
    db_path = tmp_path / "quant_test.db"

    db_manager.init_db(db_path)

    with sqlite3.connect(db_path) as conn:
        table_names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert {"investment_rules", "trade_history", "market_snapshots"} <= table_names


def test_seed_default_rules_is_idempotent(tmp_path):
    db_path = tmp_path / "quant_test.db"
    db_manager.init_db(db_path)

    first_count = db_manager.seed_default_rules(db_path)
    second_count = db_manager.seed_default_rules(db_path)
    rules = db_manager.get_all_rules(db_path=db_path)

    assert first_count == len(db_manager.DEFAULT_RULES)
    assert second_count == 0
    assert len(rules) == len(db_manager.DEFAULT_RULES)


def test_insert_trade_prevents_duplicates(tmp_path):
    db_path = tmp_path / "quant_test.db"
    db_manager.init_db(db_path)
    db_manager.seed_default_rules(db_path)
    rule = db_manager.get_all_rules(db_path=db_path)[0]

    inserted = db_manager.insert_trade(
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
    duplicate = db_manager.insert_trade(
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

    assert inserted is True
    assert duplicate is False
    assert db_manager.trade_exists(
        date="2026-05-07",
        asset_code=rule["asset_code"],
        action="buy",
        rule_id=rule["id"],
        db_path=db_path,
    )


def test_get_total_positions_aggregates_trades(tmp_path):
    db_path = tmp_path / "quant_test.db"
    db_manager.init_db(db_path)
    db_manager.seed_default_rules(db_path)
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
    db_manager.insert_trade(
        rule_id=rule["id"],
        date="2026-05-08",
        asset_code=rule["asset_code"],
        action="buy",
        currency=rule["currency"],
        amount=300.0,
        price=150.0,
        shares=2.0,
        db_path=db_path,
    )

    positions = db_manager.get_total_positions(db_path)

    assert positions == [
        {
            "asset_code": rule["asset_code"],
            "asset_name": rule["asset_name"],
            "currency": rule["currency"],
            "cost": 800.0,
            "shares": 7.0,
        }
    ]


def test_market_snapshot_upsert_and_get(tmp_path):
    db_path = tmp_path / "quant_test.db"
    db_manager.init_db(db_path)

    db_manager.upsert_market_snapshot(
        asset_code="QQQ",
        market_type="US",
        date="2026-05-07",
        price=420.0,
        change_pct=0.8,
        source="mock",
        db_path=db_path,
    )
    db_manager.upsert_market_snapshot(
        asset_code="QQQ",
        market_type="US",
        date="2026-05-07",
        price=421.5,
        change_pct=1.1,
        source="mock",
        db_path=db_path,
    )

    snapshot = db_manager.get_market_snapshot(
        asset_code="QQQ",
        market_type="US",
        date="2026-05-07",
        db_path=db_path,
    )

    assert snapshot is not None
    assert snapshot["price"] == 421.5
    assert snapshot["change_pct"] == 1.1


def test_get_latest_market_snapshot_returns_most_recent_before_date(tmp_path):
    db_path = tmp_path / "quant_test.db"
    db_manager.init_db(db_path)
    db_manager.upsert_market_snapshot(
        asset_code="QQQ",
        market_type="US",
        date="2026-05-06",
        price=410.0,
        change_pct=0.1,
        source="mock",
        db_path=db_path,
    )
    db_manager.upsert_market_snapshot(
        asset_code="QQQ",
        market_type="US",
        date="2026-05-07",
        price=420.0,
        change_pct=0.2,
        source="mock",
        db_path=db_path,
    )
    db_manager.upsert_market_snapshot(
        asset_code="QQQ",
        market_type="US",
        date="2026-05-09",
        price=430.0,
        change_pct=0.3,
        source="mock",
        db_path=db_path,
    )

    snapshot = db_manager.get_latest_market_snapshot(
        asset_code="QQQ",
        market_type="US",
        max_date="2026-05-08",
        db_path=db_path,
    )

    assert snapshot is not None
    assert snapshot["date"] == "2026-05-07"
    assert snapshot["price"] == 420.0
