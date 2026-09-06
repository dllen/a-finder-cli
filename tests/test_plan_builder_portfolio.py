import os
import tempfile
import pytest
from db_repository import open_db


@pytest.fixture
def fresh_db(tmp_path):
    path = str(tmp_path / "m.db")
    conn = open_db(path)
    yield path, conn
    conn.close()


def _seed_pick(conn, *, date, code, score=2.0, buy=100.0, stop=80.0, target=140.0):
    conn.execute(
        """INSERT INTO daily_picks (date, rank, kind, code, name, strategy,
             buy, stop, target, score)
           VALUES (?, 1, 'test', ?, ?, 'test', ?, ?, ?, ?)""",
        (date, code, code, buy, stop, target, score),
    )
    conn.commit()


def _seed_price(conn, code, close, trade_date):
    conn.execute(
        """INSERT INTO daily_prices (code, trade_date, open, close, high, low,
                                    volume, amount) VALUES (?,?,?,?,?,?,0,0)""",
        (code, trade_date, close, close, close, close),
    )
    conn.commit()


def test_build_plan_writes_portfolio_label(fresh_db):
    path, conn = fresh_db
    _seed_pick(conn, date="2026-09-07", code="600519")
    _seed_price(conn, "600519", 100.0, "2026-09-07")
    conn.close()

    from plan_builder import build_plan
    result = build_plan(
        "2026-09-07", path,
        params={"regime": "BULL", "capital": 100000},
        portfolio="10W",
    )
    assert result.portfolio == "10W"
    conn = open_db(path)
    try:
        rows = conn.execute("SELECT portfolio FROM trade_plan").fetchall()
        opens = conn.execute("SELECT portfolio FROM open_positions").fetchall()
        events = conn.execute("SELECT portfolio FROM trade_events").fetchall()
    finally:
        conn.close()
    assert all(r[0] == "10W" for r in rows)
    assert all(r[0] == "10W" for r in opens)
    assert all(r[0] == "10W" for r in events)


def test_build_plan_default_portfolio(fresh_db):
    """不传 portfolio 时默认 'default'（向后兼容）。"""
    path, conn = fresh_db
    _seed_pick(conn, date="2026-09-07", code="600519")
    _seed_price(conn, "600519", 100.0, "2026-09-07")
    conn.close()

    from plan_builder import build_plan
    build_plan("2026-09-07", path, params={"regime": "BULL"})
    conn = open_db(path)
    try:
        rows = conn.execute("SELECT DISTINCT portfolio FROM trade_plan").fetchall()
    finally:
        conn.close()
    assert rows == [("default",)]


def test_build_plan_isolates_paper_fill_by_portfolio(fresh_db):
    """同 code 在两个 portfolio 都建仓 → open_positions 两条独立行。"""
    path, conn = fresh_db
    _seed_pick(conn, date="2026-09-07", code="600519")
    _seed_price(conn, "600519", 100.0, "2026-09-07")
    conn.close()

    from plan_builder import build_plan
    build_plan("2026-09-07", path, params={"regime": "BULL"}, portfolio="default")
    build_plan("2026-09-07", path, params={"regime": "BULL"}, portfolio="10W")
    conn = open_db(path)
    try:
        opens = conn.execute(
            "SELECT portfolio, code FROM open_positions ORDER BY portfolio"
        ).fetchall()
    finally:
        conn.close()
    assert opens == [("10W", "600519"), ("default", "600519")]


def test_build_plan_cache_key_includes_portfolio(fresh_db):
    """同 plan_date + params_hash 但不同 portfolio 应各自独立 cache。"""
    path, conn = fresh_db
    _seed_pick(conn, date="2026-09-07", code="600519")
    _seed_price(conn, "600519", 100.0, "2026-09-07")
    conn.close()

    from plan_builder import build_plan
    build_plan("2026-09-07", path, params={"regime": "BULL"}, portfolio="default")
    # 改变 portfolio 重新跑：应该重算（cache hit 只对相同 portfolio）
    res = build_plan("2026-09-07", path, params={"regime": "BULL"}, portfolio="10W")
    assert res.portfolio == "10W"
    conn = open_db(path)
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM trade_plan WHERE plan_date='2026-09-07'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert n == 2  # default + 10W 各一条 buy


def test_build_plan_picks_injected(fresh_db):
    """_picks kwarg 用于 build_all_portfolios：注入 picks 跳过 daily_picks 读取。"""
    path, conn = fresh_db
    _seed_price(conn, "600519", 100.0, "2026-09-07")
    conn.close()

    from plan_builder import build_plan
    picks = [{
        "code": "600519", "score": 2.0, "buy": 100.0,
        "stop": 80.0, "target": 140.0, "strategy": "test",
    }]
    res = build_plan(
        "2026-09-07", path,
        params={"regime": "BULL", "capital": 100000},
        portfolio="10W", _picks=picks,
    )
    assert res.num_picks == 1
