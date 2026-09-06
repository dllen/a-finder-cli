import os
import tempfile
import pytest
from db_repository import open_db, compute_portfolio_pnl


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


def test_build_all_portfolios_default_tiers(fresh_db):
    """不传 tiers 时跑全 10 档。"""
    path, conn = fresh_db
    _seed_pick(conn, date="2026-09-07", code="600519")
    _seed_price(conn, "600519", 100.0, "2026-09-07")
    conn.close()

    from plan_builder import build_all_portfolios
    results = build_all_portfolios(
        "2026-09-07", path, params={"regime": "BULL"},
    )
    # 5W/10W/.../50W = 10 档
    assert len(results) == 10
    portfolios = {r.portfolio for r in results}
    assert portfolios == {"5W", "10W", "15W", "20W", "25W",
                          "30W", "35W", "40W", "45W", "50W"}


def test_build_all_portfolios_custom_tiers(fresh_db):
    path, conn = fresh_db
    _seed_pick(conn, date="2026-09-07", code="600519")
    _seed_price(conn, "600519", 100.0, "2026-09-07")
    conn.close()

    from plan_builder import build_all_portfolios
    results = build_all_portfolios(
        "2026-09-07", path, params={"regime": "BULL"},
        tiers=[50000, 200000],
    )
    assert {r.portfolio for r in results} == {"5W", "20W"}


def test_build_all_portfolios_shares_scale_by_capital(fresh_db):
    path, conn = fresh_db
    # buy=10, stop=8, target=14 -> stop below entry so rows are valid
    _seed_pick(conn, date="2026-09-07", code="600519", buy=10.0, stop=8.0, target=14.0)
    _seed_price(conn, "600519", 10.0, "2026-09-07")
    conn.close()

    from plan_builder import build_all_portfolios
    results = build_all_portfolios(
        "2026-09-07", path, params={"regime": "BULL"},
        tiers=[50000, 100000, 500000],
    )
    by_label = {r.portfolio: r for r in results}
    # Debug: print all buy rows
    for label, result in by_label.items():
        buy_rows = [row for row in result.rows if row.action == "buy"]
        print(f"{label}: buy_rows={[(r.code, r.shares, r.status, r.reason) for r in buy_rows]}, num_picks={result.num_picks}")
    shares_5w = sum(r.shares for r in by_label["5W"].rows if r.action == "buy")
    shares_50w = sum(r.shares for r in by_label["50W"].rows if r.action == "buy")
    assert shares_50w > shares_5w


def test_build_all_portfolios_per_tier_isolation(fresh_db):
    """一档失败不影响其他档（per-tier 独立事务）。"""
    path, conn = fresh_db
    _seed_pick(conn, date="2026-09-07", code="600519")
    _seed_price(conn, "600519", 100.0, "2026-09-07")
    conn.close()

    from plan_builder import build_all_portfolios
    # 故意让 10W 失败：用一个异常 params（price=0 让所有 buy 行 stop_above_entry? 简化：让 plan 失败）
    # 这里采用构造一个 buy 行 stop above entry 的场景
    # 直接 patch build_plan 在 10W 抛异常
    import plan_builder as pb
    orig_build = pb.build_plan
    call_count = {"n": 0}

    def flaky(*args, **kwargs):
        call_count["n"] += 1
        portfolio = kwargs.get("portfolio", "default")
        if portfolio == "10W":
            raise RuntimeError("simulated 10W failure")
        return orig_build(*args, **kwargs)

    pb.build_plan = flaky
    try:
        results = build_all_portfolios(
            "2026-09-07", path, params={"regime": "BULL"},
            tiers=[50000, 100000, 200000],
        )
    finally:
        pb.build_plan = orig_build

    # 5W + 20W 成功，10W 在 results 里以失败占位或缺失
    successful = [r for r in results if r is not None]
    failed = [r for r in results if r is None]
    assert len(successful) == 2
    assert len(failed) == 1
    assert call_count["n"] == 3


def test_build_all_portfolios_progress_callback(fresh_db):
    path, conn = fresh_db
    _seed_pick(conn, date="2026-09-07", code="600519")
    _seed_price(conn, "600519", 100.0, "2026-09-07")
    conn.close()

    progress_calls = []

    def progress(pct, msg):
        progress_calls.append((pct, msg))

    from plan_builder import build_all_portfolios
    build_all_portfolios(
        "2026-09-07", path, params={"regime": "BULL"},
        tiers=[50000, 100000], progress=progress,
    )
    # emit sequence: start(0%) + one per tier (50%, 100% for 2 tiers) = 3 calls
    assert len(progress_calls) >= 3
    assert progress_calls[0][0] == 0  # start at 0%
    assert progress_calls[-1][0] == 100  # end at 100%
