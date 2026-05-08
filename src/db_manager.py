"""SQLite persistence layer for shadow-accounting data."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from src.config import load_settings


DEFAULT_RULES = [
    {
        "asset_name": "Nasdaq 100 ETF",
        "asset_code": "QQQ",
        "market_type": "US",
        "currency": "USD",
        "amount": 500.0,
        "freq_type": "weekly",
        "freq_value": 4,
    },
    {
        "asset_name": "ChiNext ETF",
        "asset_code": "159915",
        "market_type": "CN",
        "currency": "CNY",
        "amount": 1000.0,
        "freq_type": "monthly",
        "freq_value": 7,
    },
]


def _db_path(db_path: str | Path | None = None) -> str:
    return str(db_path or load_settings().database_path)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


@contextmanager
def get_connection(db_path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    """Open a SQLite connection with row dictionaries enabled."""

    conn = sqlite3.connect(_db_path(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: str | Path | None = None) -> None:
    """Initialize database tables and constraints."""

    with get_connection(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS investment_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_name TEXT NOT NULL,
                asset_code TEXT NOT NULL,
                market_type TEXT NOT NULL,
                currency TEXT NOT NULL,
                amount REAL NOT NULL,
                freq_type TEXT NOT NULL,
                freq_value INTEGER NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                UNIQUE(asset_code, market_type, freq_type, freq_value)
            );

            CREATE TABLE IF NOT EXISTS trade_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id INTEGER,
                date TEXT NOT NULL,
                asset_code TEXT NOT NULL,
                action TEXT NOT NULL,
                currency TEXT NOT NULL,
                amount REAL NOT NULL,
                price REAL NOT NULL,
                shares REAL NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(rule_id) REFERENCES investment_rules(id),
                UNIQUE(date, asset_code, action, rule_id)
            );

            CREATE TABLE IF NOT EXISTS market_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                asset_code TEXT NOT NULL,
                market_type TEXT NOT NULL,
                date TEXT NOT NULL,
                price REAL NOT NULL,
                change_pct REAL,
                source TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                UNIQUE(asset_code, market_type, date)
            );
            """
        )


def seed_default_rules(db_path: str | Path | None = None) -> int:
    """Insert default test rules once. Returns inserted row count."""

    inserted = 0
    with get_connection(db_path) as conn:
        existing_count = conn.execute("SELECT COUNT(*) FROM investment_rules").fetchone()[0]
        if existing_count:
            return 0
        for rule in DEFAULT_RULES:
            cursor = conn.execute(
                """
                INSERT OR IGNORE INTO investment_rules (
                    asset_name, asset_code, market_type, currency, amount,
                    freq_type, freq_value, enabled, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                """,
                (
                    rule["asset_name"],
                    rule["asset_code"],
                    rule["market_type"],
                    rule["currency"],
                    rule["amount"],
                    rule["freq_type"],
                    rule["freq_value"],
                    _now(),
                ),
            )
            inserted += cursor.rowcount
    return inserted


def get_all_rules(
    enabled_only: bool = True, db_path: str | Path | None = None
) -> list[dict[str, Any]]:
    """Return configured investment rules."""

    sql = "SELECT * FROM investment_rules"
    params: tuple[Any, ...] = ()
    if enabled_only:
        sql += " WHERE enabled = ?"
        params = (1,)
    sql += " ORDER BY id"
    with get_connection(db_path) as conn:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]


def trade_exists(
    *,
    date: str,
    asset_code: str,
    action: str,
    rule_id: int | None,
    db_path: str | Path | None = None,
) -> bool:
    """Check whether a trade already exists for the uniqueness key."""

    with get_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT 1
            FROM trade_history
            WHERE date = ? AND asset_code = ? AND action = ? AND rule_id IS ?
            LIMIT 1
            """,
            (date, asset_code, action, rule_id),
        ).fetchone()
    return row is not None


def insert_trade(
    *,
    rule_id: int | None,
    date: str,
    asset_code: str,
    action: str,
    currency: str,
    amount: float,
    price: float,
    shares: float,
    db_path: str | Path | None = None,
) -> bool:
    """Insert a trade. Returns False when the duplicate constraint is hit."""

    try:
        with get_connection(db_path) as conn:
            conn.execute(
                """
                INSERT INTO trade_history (
                    rule_id, date, asset_code, action, currency, amount,
                    price, shares, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    rule_id,
                    date,
                    asset_code,
                    action,
                    currency,
                    amount,
                    price,
                    shares,
                    _now(),
                ),
            )
    except sqlite3.IntegrityError:
        return False
    return True


def get_total_positions(db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """Aggregate holdings and cost by asset and currency."""

    with get_connection(db_path) as conn:
        rows = conn.execute(
            """
            SELECT
                t.asset_code,
                COALESCE(r.asset_name, t.asset_code) AS asset_name,
                t.currency,
                SUM(CASE WHEN t.action = 'buy' THEN t.amount ELSE -t.amount END) AS cost,
                SUM(CASE WHEN t.action = 'buy' THEN t.shares ELSE -t.shares END) AS shares
            FROM trade_history t
            LEFT JOIN investment_rules r ON r.id = t.rule_id
            GROUP BY t.asset_code, t.currency
            HAVING ABS(shares) > 0.00000001
            ORDER BY t.asset_code
            """
        ).fetchall()
    return [dict(row) for row in rows]


def upsert_market_snapshot(
    *,
    asset_code: str,
    market_type: str,
    date: str,
    price: float,
    change_pct: float | None,
    source: str,
    db_path: str | Path | None = None,
) -> None:
    """Insert or update a market snapshot for an asset/date."""

    with get_connection(db_path) as conn:
        conn.execute(
            """
            INSERT INTO market_snapshots (
                asset_code, market_type, date, price, change_pct, source, fetched_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(asset_code, market_type, date)
            DO UPDATE SET
                price = excluded.price,
                change_pct = excluded.change_pct,
                source = excluded.source,
                fetched_at = excluded.fetched_at
            """,
            (asset_code, market_type, date, price, change_pct, source, _now()),
        )


def get_market_snapshot(
    *,
    asset_code: str,
    market_type: str,
    date: str,
    db_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Return a cached market snapshot when available."""

    with get_connection(db_path) as conn:
        row = conn.execute(
            """
            SELECT *
            FROM market_snapshots
            WHERE asset_code = ? AND market_type = ? AND date = ?
            LIMIT 1
            """,
            (asset_code, market_type, date),
        ).fetchone()
    return dict(row) if row else None


def get_latest_market_snapshot(
    *,
    asset_code: str,
    market_type: str,
    max_date: str | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Return the latest cached market snapshot up to max_date when available."""

    sql = """
        SELECT *
        FROM market_snapshots
        WHERE asset_code = ? AND market_type = ?
    """
    params: list[Any] = [asset_code, market_type]
    if max_date is not None:
        sql += " AND date <= ?"
        params.append(max_date)
    sql += " ORDER BY date DESC, fetched_at DESC LIMIT 1"

    with get_connection(db_path) as conn:
        row = conn.execute(sql, params).fetchone()
    return dict(row) if row else None
