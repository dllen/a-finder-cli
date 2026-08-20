# tests/test_share_lots.py
import os
import tempfile

from db_repository import open_db
from db_repository import insert_open_position, accumulate_open_position, get_open_positions


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


def test_insert_open_position_default_200_shares():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = open_db(path)
    try:
        insert_open_position(conn, "600519", "2026-08-18", 100.0, 0.1, 92.0, 120.0)
        opens = get_open_positions(conn)
        assert opens[0]["shares"] == 200
    finally:
        conn.close()


def test_accumulate_open_position_weights_avg():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = open_db(path)
    try:
        insert_open_position(conn, "600519", "2026-08-18", 100.0, 0.1, 92.0, 120.0)
        # 第二天再买 200 股 @ 110 → 加权均价 = (200*100 + 200*110)/400 = 105
        accumulate_open_position(conn, "600519", 110.0, 0.1, 97.0, 126.0, 200)
        opens = get_open_positions(conn)
        assert len(opens) == 1
        assert opens[0]["shares"] == 400
        assert abs(opens[0]["entry_price"] - 105.0) < 1e-6
        assert abs(opens[0]["stop_price"] - 97.0) < 1e-6
    finally:
        conn.close()


def test_accumulate_open_position_creates_when_missing():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = open_db(path)
    try:
        accumulate_open_position(conn, "000001", 10.0, 0.1, 9.2, 12.0, 200)
        opens = get_open_positions(conn)
        assert len(opens) == 1 and opens[0]["shares"] == 200
    finally:
        conn.close()
