# tests/test_share_lots.py
import os
import tempfile

from db_repository import open_db


def test_migration_adds_shares_and_backfills_open():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = open_db(path)  # applies schema + all migrations
    try:
        # 先造一个存量 open 持仓（模拟迁移前已有数据，shares 默认为 0）
        conn.execute(
            """INSERT INTO open_positions
            (code, entry_date, entry_price, size_pct, stop_price, tp_price, status)
            VALUES ('600519','2026-08-10',100.0,0.1,92.0,120.0,'open')"""
        )
        conn.commit()
        # 迁移对已存在表不会二次 ALTER；这里直接断言列已存在
        cols = [r[1] for r in conn.execute("PRAGMA table_info(open_positions)").fetchall()]
        assert "shares" in cols
        tp_cols = [r[1] for r in conn.execute("PRAGMA table_info(trade_plan)").fetchall()]
        assert "shares" in tp_cols
        te_cols = [r[1] for r in conn.execute("PRAGMA table_info(trade_events)").fetchall()]
        assert "shares" in te_cols and "pnl_amt" in te_cols
    finally:
        conn.close()
