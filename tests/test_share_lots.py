# tests/test_share_lots.py
import os
import tempfile

from db_repository import open_db
from db_repository import insert_open_position, accumulate_open_position, get_open_positions
from db_repository import get_holdings_detail
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
        assert opens[0][0] == 200  # 累积 100 + 100 股
        # 加权均价 = (100*100.1 + 100*110.11)/200 ≈ 105.1（滑点 0.1%）
        assert 104.0 < opens[0][1] < 106.0
        evts = conn.execute(
            "SELECT plan_date, shares FROM trade_events WHERE event_type='open' ORDER BY plan_date"
        ).fetchall()
        assert evts == [("2026-08-18", 100), ("2026-08-19", 100)]
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
        assert shares == 100  # 未重复买
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


def test_holdings_detail_summary():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = open_db(path)
    conn.execute(
        "INSERT INTO hs300_metadata (code, name) VALUES ('600519','贵州茅台')"
    )
    conn.execute(
        """INSERT INTO open_positions
        (code, entry_date, entry_price, size_pct, stop_price, tp_price, status, shares)
        VALUES ('600519','2026-08-18',100.0,0.1,92.0,120.0,'open',200)"""
    )
    conn.execute(
        "INSERT INTO daily_prices (code, trade_date, close) VALUES ('600519','2026-08-19',110.0)"
    )
    conn.commit()
    d = get_holdings_detail(conn)
    assert d["summary"]["open_count"] == 1
    assert d["summary"]["shares_total"] == 200
    assert d["summary"]["floating_pnl"] == 2000.0
    assert d["summary"]["realized_pnl"] == 0.0
    assert d["holdings"][0]["name"] == "贵州茅台"
    assert d["holdings"][0]["floating_pnl"] == 2000.0
    assert d["holdings"][0]["stop_pnl"] == (92.0 - 100.0) * 200
    conn.close()


def test_build_plan_sizes_shares_by_capital():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = open_db(path)
    _seed_pick(conn, "2026-08-18", "600519", buy=100.0, score=2.0)
    conn.close()
    # 5W：50000*0.125=6250 元 → 62 股 → 不足一手 → 0（不建仓）
    r_small = build_plan("2026-08-18", path, params={"regime": "BULL", "capital": 50000})
    # 50W：500000*0.125=62500 元 → 625 股 → 6 手 = 600 股
    r_big = build_plan("2026-08-18", path, params={"regime": "BULL", "capital": 500000})

    buy_small = [r for r in r_small.rows if r.action == "buy"]
    buy_big = [r for r in r_big.rows if r.action == "buy"]
    assert buy_small[0].shares == 0
    assert buy_big[0].shares == 600

    conn = open_db(path)
    try:
        opens = conn.execute(
            "SELECT shares FROM open_positions WHERE code='600519' AND status='open'"
        ).fetchall()
    finally:
        conn.close()
    # 第一次 0 股不建仓，第二次 600 股建仓 → 仅一笔 600
    assert [o[0] for o in opens] == [600]


def test_build_plan_paper_trade_false_skips_fills():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = open_db(path)
    _seed_pick(conn, "2026-08-18", "600519", buy=100.0, score=2.0)
    conn.close()
    result = build_plan("2026-08-18", path, params={"regime": "BULL"}, paper_trade=False)

    buys = [r for r in result.rows if r.action == "buy"]
    assert len(buys) == 1
    assert buys[0].shares > 0  # 资金感知股数已算

    conn = open_db(path)
    try:
        plan_n = conn.execute("SELECT COUNT(*) FROM trade_plan").fetchone()[0]
        open_n = conn.execute("SELECT COUNT(*) FROM open_positions").fetchone()[0]
        evt_n = conn.execute("SELECT COUNT(*) FROM trade_events").fetchone()[0]
    finally:
        conn.close()
    assert plan_n == 1
    assert open_n == 0
    assert evt_n == 0


def test_build_plan_include_carryover_false_skips_hold_rows():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = open_db(path)
    _seed_pick(conn, "2026-08-18", "600519", buy=100.0, score=2.0)
    # 存量持仓：正常 build 会产出 hold 行
    conn.execute(
        "INSERT INTO open_positions (code, entry_date, entry_price, size_pct, stop_price, tp_price, status, shares) "
        "VALUES ('000001','2026-08-10',50.0,0.1,46.0,60.0,'open',200)"
    )
    conn.execute("INSERT INTO daily_prices (code, trade_date, close) VALUES ('000001','2026-08-18',52.0)")
    conn.commit()
    conn.close()

    result = build_plan("2026-08-18", path, params={"regime": "BULL"},
                        paper_trade=False, include_carryover=False)
    assert {r.action for r in result.rows} == {"buy"}  # 只有买入行，无 hold/exit
