"""Import investment rules and initial trades from a CSV file.

Expected columns:
asset_name,asset_code,market_type,currency,rule_amount,freq_type,freq_value,
enabled,trade_date,amount,price,shares

Rows with the same asset/rule fields can be repeated for multiple trades.
This script is intentionally generic and does not contain personal portfolio
data. Back up your database before importing real data.
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
from datetime import datetime
from pathlib import Path

from src import db_manager


RULE_FIELDS = (
    "asset_name",
    "asset_code",
    "market_type",
    "currency",
    "rule_amount",
    "freq_type",
    "freq_value",
    "enabled",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import portfolio data from CSV.")
    parser.add_argument("csv_path", type=Path, help="CSV file to import.")
    parser.add_argument("--db", type=Path, default=Path("quant_data.db"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    db_manager.init_db(args.db)
    rows = _read_rows(args.csv_path)
    now = datetime.now().isoformat(timespec="seconds")

    with sqlite3.connect(args.db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        rule_ids: dict[tuple[str, ...], int] = {}
        for row in rows:
            key = tuple(row[field] for field in RULE_FIELDS)
            if key not in rule_ids:
                rule_ids[key] = _upsert_rule(conn, row, now)
            _insert_trade(conn, row, rule_ids[key], now)
        conn.commit()

    print(f"imported_rows={len(rows)}")
    print(f"database={args.db}")
    return 0


def _read_rows(csv_path: Path) -> list[dict[str, str]]:
    with csv_path.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    missing = set(_required_columns()) - set(rows[0].keys() if rows else [])
    if missing:
        raise ValueError(f"missing CSV columns: {', '.join(sorted(missing))}")
    return rows


def _required_columns() -> tuple[str, ...]:
    return RULE_FIELDS + ("trade_date", "amount", "price", "shares")


def _upsert_rule(conn: sqlite3.Connection, row: dict[str, str], now: str) -> int:
    conn.execute(
        """
        INSERT OR IGNORE INTO investment_rules (
            asset_name, asset_code, market_type, currency, amount,
            freq_type, freq_value, enabled, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            row["asset_name"],
            row["asset_code"],
            row["market_type"],
            row["currency"],
            float(row["rule_amount"]),
            row["freq_type"],
            int(row["freq_value"]),
            int(row["enabled"]),
            now,
        ),
    )
    existing = conn.execute(
        """
        SELECT id
        FROM investment_rules
        WHERE asset_code = ? AND market_type = ? AND freq_type = ? AND freq_value = ?
        """,
        (
            row["asset_code"],
            row["market_type"],
            row["freq_type"],
            int(row["freq_value"]),
        ),
    ).fetchone()
    if existing is None:
        raise RuntimeError(f"failed to create rule for {row['asset_code']}")
    return int(existing[0])


def _insert_trade(
    conn: sqlite3.Connection,
    row: dict[str, str],
    rule_id: int,
    now: str,
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO trade_history (
            rule_id, date, asset_code, action, currency, amount,
            price, shares, created_at
        )
        VALUES (?, ?, ?, 'buy', ?, ?, ?, ?, ?)
        """,
        (
            rule_id,
            row["trade_date"],
            row["asset_code"],
            row["currency"],
            float(row["amount"]),
            float(row["price"]),
            float(row["shares"]),
            now,
        ),
    )


if __name__ == "__main__":
    raise SystemExit(main())
