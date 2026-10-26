#!/usr/bin/env python3
"""从本地 hs300.db 生成 D1 可执行的 SQL（历史行情 + 成分股）。

用法：
    python3 scripts/seed_d1.py --db hs300.db --out /tmp/d1_seed.sql
    npx wrangler@latest d1 execute a-finder --file=/tmp/d1_seed.sql --remote

只生成 INSERT OR REPLACE，重复执行安全。
"""

from __future__ import annotations

import argparse
import sqlite3
from typing import List, Optional, Tuple


def _lit(value) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return repr(float(value)) if isinstance(value, float) else str(value)


def _rows(conn: sqlite3.Connection, sql: str, params: Tuple = ()) -> List[tuple]:
    return conn.execute(sql, params).fetchall()


def build_symbols_sql(db_path: str) -> str:
    """成分股：一条多值 INSERT。"""
    conn = sqlite3.connect(db_path)
    try:
        rows = _rows(conn, "SELECT code, name FROM hs300_metadata ORDER BY code")
    finally:
        conn.close()
    if not rows:
        return ""
    values = ", ".join(
        f"({_lit(c)}, {_lit(n)}, datetime('now'))" for c, n in rows
    )
    return ("INSERT OR REPLACE INTO market_symbols (code, name, updated_at) "
            f"VALUES {values};")


def build_prices_sql(
    db_path: str, *, batch: int = 200, since: Optional[str] = None
) -> Tuple[str, int]:
    """行情：按 batch 分组成多值 INSERT，返回 (sql, 行数)。"""
    conn = sqlite3.connect(db_path)
    try:
        if since:
            rows = _rows(
                conn,
                "SELECT code, trade_date, open, close, high, low, volume, amount,"
                " turnover, pct_change FROM daily_prices WHERE trade_date >= ?"
                " ORDER BY trade_date, code",
                (since,),
            )
        else:
            rows = _rows(
                conn,
                "SELECT code, trade_date, open, close, high, low, volume, amount,"
                " turnover, pct_change FROM daily_prices ORDER BY trade_date, code",
            )
    finally:
        conn.close()

    cols = ("code, trade_date, open, close, high, low, volume, amount,"
            " turnover, pct_change")
    stmts = []
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        values = ", ".join("(" + ", ".join(_lit(v) for v in r) + ")" for r in chunk)
        stmts.append(f"INSERT OR REPLACE INTO market_prices ({cols}) VALUES {values};")
    return "\n".join(stmts), len(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description="生成 D1 seed SQL")
    ap.add_argument("--db", default="hs300.db")
    ap.add_argument("--out", required=True)
    ap.add_argument("--since", default=None, help="只导出该日期之后的行情")
    ap.add_argument("--batch", type=int, default=200)
    args = ap.parse_args()

    symbols = build_symbols_sql(args.db)
    prices, n = build_prices_sql(args.db, batch=args.batch, since=args.since)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write("-- D1 seed: 历史行情 + 成分股\n")
        if symbols:
            f.write(symbols + "\n")
        f.write(prices + "\n")
    print(f"已写出 {args.out}：成分股 SQL {'有' if symbols else '无'}，行情 {n} 行")


if __name__ == "__main__":
    main()
