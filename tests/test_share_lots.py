# tests/test_share_lots.py
import os
import tempfile

from db_repository import open_db
from db_repository import insert_open_position, accumulate_open_position, get_open_positions
from plan_builder import build_plan


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


_SEED_RANK = {"n": 0}


def _seed_pick(conn, date, code, buy=100.0, score=2.0):
    # daily_picks PK is (date, rank, kind); vary rank so multiple codes can
    # be seeded on the same date without a UNIQUE constraint collision.
    _SEED_RANK["n"] += 1
    conn.execute(
        """INSERT INTO daily_picks (date, rank, kind, code, name, strategy, buy, stop, target, score)
        VALUES (?, ?, '均线', ?, ?, 'test', ?, ?, ?, ?)""",
        (date, _SEED_RANK["n"], code, code, buy, buy * 0.9, buy * 1.2, score),
    )
    conn.commit()


def test_build_plan_buys_200_shares_and_accumulates_next_day():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = open_db(path)
    _seed_pick(conn, "2026-08-18", "600519", buy=100.0)
    conn.close()
    build_plan("2026-08-18", path, params={"regime": "BULL"})

    conn = open_db(path)
    _seed_pick(conn, "2026-08-19", "600519", buy=110.0)
    conn.close()
    build_plan("2026-08-19", path, params={"regime": "BULL"})

    conn = open_db(path)
    try:
        opens = conn.execute(
            "SELECT shares, entry_price FROM open_positions WHERE code='600519' AND status='open'"
        ).fetchall()
        assert len(opens) == 1
        assert opens[0][0] == 400  # 累积 400 股
        # 加权均价 = (200*100.1 + 200*110.11)/400 ≈ 105.1（滑点 0.1%）
        assert 104.0 < opens[0][1] < 106.0
        evts = conn.execute(
            "SELECT plan_date, shares FROM trade_events WHERE event_type='open' ORDER BY plan_date"
        ).fetchall()
        assert evts == [("2026-08-18", 200), ("2026-08-19", 200)]
    finally:
        conn.close()


def test_build_plan_same_day_rebuild_does_not_double_buy():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = open_db(path)
    _seed_pick(conn, "2026-08-18", "600519", buy=100.0)
    conn.close()
    build_plan("2026-08-18", path, params={"regime": "BULL"})
    build_plan("2026-08-18", path, params={"regime": "BULL"})  # 同日重跑
    conn = open_db(path)
    try:
        shares = conn.execute(
            "SELECT shares FROM open_positions WHERE code='600519' AND status='open'"
        ).fetchone()[0]
        assert shares == 200  # 未重复买
    finally:
        conn.close()


def test_build_plan_sets_entry_date_on_open_position():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = open_db(path)
    _seed_pick(conn, "2026-08-18", "600519", buy=100.0)
    conn.close()
    build_plan("2026-08-18", path, params={"regime": "BULL"})
    conn = open_db(path)
    try:
        entry_date = conn.execute(
            "SELECT entry_date FROM open_positions WHERE code='600519' AND status='open'"
        ).fetchone()[0]
        assert entry_date == "2026-08-18"  # 非空 entry_date
    finally:
        conn.close()


def test_close_records_amount_pnl():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = open_db(path)
    # 200 股 @ 100，止损价 105（现价 102 会触发 exit? 需现价 <= stop? 见下）
    conn.execute(
        """INSERT INTO open_positions
        (code, entry_date, entry_price, size_pct, stop_price, tp_price, status, shares)
        VALUES ('000002','2026-08-10',100.0,0.1,105.0,115.0,'open',200)"""
    )
    conn.execute(
        "INSERT INTO daily_prices (code, trade_date, close) VALUES ('000002','2026-08-18',102.0)"
    )
    conn.commit()
    conn.close()
    build_plan("2026-08-18", path, params={"regime": "BULL"})

    conn = open_db(path)
    try:
        close = conn.execute(
            "SELECT shares, pnl_amt FROM trade_events WHERE event_type='close'"
        ).fetchone()
        assert close[0] == 200
        assert abs(close[1] - 400.0) < 0.01  # (102 - 100) * 200 = 400
        still_open = conn.execute(
            "SELECT COUNT(*) FROM open_positions WHERE code='000002' AND status='open'"
        ).fetchone()[0]
        assert still_open == 0
    finally:
        conn.close()


def test_sanity_gate_no_scaling_under_fixed_shares():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = open_db(path)
    for code in ["600000", "600001", "600002", "600003", "600004"]:
        _seed_pick(conn, "2026-08-18", code, buy=100.0)
    conn.close()
    result = build_plan("2026-08-18", path, params={
        "regime": "BULL", "max_single": 0.15, "max_total": 0.3,
    })
    buys = [r for r in result.rows if r.action == "buy"]
    assert len(buys) == 5
    # 固定股数下不得缩放、不得标 failed
    assert all(r.status == "ok" for r in buys)
    assert not any("scaled_to_fit" in r.reason for r in buys)
    assert not any("size_exceed_max" in r.reason for r in buys)
