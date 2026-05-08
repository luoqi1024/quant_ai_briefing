"""Enable automatic SGE AU9999 pricing for the gold accumulation holding."""

from __future__ import annotations

import sqlite3


with sqlite3.connect("quant_data.db") as conn:
    conn.execute(
        """
        UPDATE investment_rules
        SET market_type = 'GOLD'
        WHERE asset_code = 'GOLD_GRAM_CNY'
        """
    )
    conn.commit()

print("enabled GOLD market pricing for GOLD_GRAM_CNY")
