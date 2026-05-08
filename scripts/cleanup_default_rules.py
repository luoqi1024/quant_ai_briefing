"""Remove default demo rules after real portfolio initialization."""

from __future__ import annotations

import sqlite3


with sqlite3.connect("quant_data.db") as conn:
    conn.execute("DELETE FROM investment_rules WHERE asset_code IN ('QQQ', '159915')")
    conn.commit()

print("removed default test rules QQQ/159915 if present")
