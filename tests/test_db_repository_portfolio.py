import os
import tempfile
import pytest
from db_repository import (
    open_db,
    insert_open_position,
    accumulate_open_position,
    insert_trade_event,
    close_open_position,
    get_open_positions_with_unrealized,
    compute_portfolio_pnl,
    insert_trade_plan,
)


@pytest.fixture
def fresh_db(tmp_path):
    path = str(tmp_path / "m.db")
    conn = open_db(path)
    yield path, conn
    conn.close()


def _seed_price(conn, code, close, trade_date):
    conn.execute(
        """INSERT INTO daily_prices (code, trade_date, open, close, high, low,
                                    volume, amount) VALUES (?,?,?,?,?,?,0,0)""",
        (code, trade_date, close, close, close, close),
    )
    conn.commit()


def test_insert_open_position_with_portfolio(fresh_db):
    _, conn = fresh_db
    pos_id = insert_open_position(
        conn, "600519", "2026-09-07", 100.0, 0.1, 90.0, 120.0,
        shares=200, portfolio="10W",
    )
    row = conn.execute(
        "SELECT portfolio FROM open_positions WHERE pos_id=?", (pos_id,)
    ).fetchone()
    assert row[0] == "10W"


def test_insert_open_position_default_portfolio(fresh_db):
    _, conn = fresh_db
    pos_id = insert_open_position(
        conn, "600519", "2026-09-07", 100.0, 0.1, 90.0, 120.0,
    )
    row = conn.execute(
        "SELECT portfolio FROM open_positions WHERE pos_id=?", (pos_id,)
    ).fetchone()
    assert row[0] == "default"


def test_accumulate_open_position_filters_by_portfolio(fresh_db):
    """同 code 在两个 portfolio 各有 open 仓位，accumulate 按 portfolio 隔离。"""
    _, conn = fresh_db
    insert_open_position(conn, "600519", "2026-09-01", 100.0, 0.1, 90.0, 120.0,
                        shares=200, portfolio="default")
    insert_open_position(conn, "600519", "2026-09-01", 100.0, 0.1, 90.0, 120.0,
                        shares=200, portfolio="10W")
    # accumulate 到 10W 不应改 default
    pos_id = accumulate_open_position(
        conn, "600519", 110.0, 0.1, 90.0, 120.0,
        shares_to_add=200, entry_date="2026-09-07", portfolio="10W",
    )
    rows = conn.execute(
        "SELECT portfolio, shares, entry_price FROM open_positions WHERE code='600519' ORDER BY portfolio"
    ).fetchall()
    # ORDER BY portfolio sorts alphabetically: '10W' < 'default'
    assert rows[0][0] == "10W"
    assert rows[0][1] == 400  # accumulated
    assert abs(rows[0][2] - 105.0) < 0.01  # (200*100 + 200*110) / 400
    assert rows[1][0] == "default"
    assert rows[1][1] == 200
    assert rows[1][2] == 100.0  # unchanged


def test_insert_trade_event_with_portfolio(fresh_db):
    _, conn = fresh_db
    eid = insert_trade_event(
        conn, "2026-09-07", "600519", "open", 100.5,
        shares=200, portfolio="10W", note="paper_fill",
    )
    row = conn.execute(
        "SELECT portfolio FROM trade_events WHERE event_id=?", (eid,)
    ).fetchone()
    assert row[0] == "10W"


def test_get_open_positions_with_unrealized_filtered(fresh_db):
    _, conn = fresh_db
    _seed_price(conn, "600519", 110.0, "2026-09-07")
    insert_open_position(conn, "600519", "2026-09-01", 100.0, 0.1, 90.0, 120.0,
                        shares=200, portfolio="default")
    insert_open_position(conn, "000001", "2026-09-01", 50.0, 0.1, 45.0, 60.0,
                        shares=200, portfolio="10W")
    out = get_open_positions_with_unrealized(conn, portfolio="10W")
    assert out["count"] == 1
    assert out["items"][0]["code"] == "000001"


def test_compute_portfolio_pnl_no_positions(fresh_db):
    _, conn = fresh_db
    out = compute_portfolio_pnl(conn, "10W", initial_capital=100000)
    assert out["portfolio"] == "10W"
    assert out["initial_capital"] == 100000
    assert out["cash_remaining"] == 100000
    assert out["position_value"] == 0.0
    assert out["total_value"] == 100000
    assert out["realized_pnl"] == 0.0
    assert out["unrealized_pnl"] == 0.0
    assert out["return_rate"] == 0.0


def test_compute_portfolio_pnl_with_open_position(fresh_db):
    _, conn = fresh_db
    _seed_price(conn, "600519", 110.0, "2026-09-07")
    insert_open_position(conn, "600519", "2026-09-01", 100.0, 0.1, 90.0, 120.0,
                        shares=200, portfolio="10W")
    insert_trade_event(conn, "2026-09-01", "600519", "open", 100.0,
                      shares=200, portfolio="10W")
    out = compute_portfolio_pnl(conn, "10W", initial_capital=100000)
    # cost_basis = 100 * 200 = 20000
    # position_value = 110 * 200 = 22000
    # cash_remaining = 100000 - 20000 = 80000
    # unrealized_pnl = 22000 - 20000 = 2000
    assert out["cash_remaining"] == 80000
    assert out["position_value"] == 22000
    assert out["total_value"] == 102000
    assert out["unrealized_pnl"] == 2000
    assert out["realized_pnl"] == 0.0
    assert abs(out["return_rate"] - 0.02) < 1e-9


def test_compute_portfolio_pnl_after_close(fresh_db):
    """部分 close：realized + cash 调整。"""
    _, conn = fresh_db
    _seed_price(conn, "600519", 110.0, "2026-09-08")
    pos_id = insert_open_position(
        conn, "600519", "2026-09-01", 100.0, 0.1, 90.0, 120.0,
        shares=200, portfolio="10W",
    )
    insert_trade_event(conn, "2026-09-01", "600519", "open", 100.0,
                      shares=200, portfolio="10W")
    insert_trade_event(conn, "2026-09-08", "600519", "close", 110.0,
                      shares=200, pnl_amt=2000.0, portfolio="10W")
    close_open_position(conn, pos_id, "2026-09-08", 110.0, "tp_hit")
    out = compute_portfolio_pnl(conn, "10W", initial_capital=100000)
    # buy 现金流出 20000; close 现金流入 22000（100*200 + pnl 2000）
    # cash_remaining = 100000 - 20000 + 22000 = 102000
    assert out["cash_remaining"] == 102000
    assert out["position_value"] == 0.0
    assert out["realized_pnl"] == 2000.0
    assert out["unrealized_pnl"] == 0.0
    assert abs(out["return_rate"] - 0.02) < 1e-9


def test_compute_portfolio_pnl_isolated_per_portfolio(fresh_db):
    _, conn = fresh_db
    _seed_price(conn, "600519", 110.0, "2026-09-07")
    insert_open_position(conn, "600519", "2026-09-01", 100.0, 0.1, 90.0, 120.0,
                        shares=200, portfolio="default")
    insert_open_position(conn, "600519", "2026-09-01", 100.0, 0.1, 90.0, 120.0,
                        shares=200, portfolio="10W")
    out_def = compute_portfolio_pnl(conn, "default", initial_capital=100000)
    out_10w = compute_portfolio_pnl(conn, "10W", initial_capital=100000)
    assert out_def["position_value"] == 22000
    assert out_10w["position_value"] == 22000
    # 互不影响


def test_get_open_positions_with_unrealized_default_includes_all(fresh_db):
    """不传 portfolio = 全部 portfolio 聚合（向后兼容）。"""
    _, conn = fresh_db
    _seed_price(conn, "600519", 110.0, "2026-09-07")
    insert_open_position(conn, "600519", "2026-09-01", 100.0, 0.1, 90.0, 120.0,
                        shares=200, portfolio="default")
    insert_open_position(conn, "000001", "2026-09-01", 50.0, 0.1, 45.0, 60.0,
                        shares=200, portfolio="10W")
    out = get_open_positions_with_unrealized(conn)
    assert out["count"] == 2  # 全部


# ---------------------------------------------------------------------------
# as_of（按日期回看组合状态）
# ---------------------------------------------------------------------------

def test_compute_portfolio_pnl_as_of_price_uses_that_day(fresh_db):
    """as_of 用当日收盘价，而不是最新收盘价。"""
    _, conn = fresh_db
    _seed_price(conn, "600519", 110.0, "2026-09-07")
    _seed_price(conn, "600519", 130.0, "2026-09-10")
    insert_trade_event(conn, "2026-09-01", "600519", "open", 100.0,
                      shares=200, portfolio="10W")

    at_07 = compute_portfolio_pnl(conn, "10W", initial_capital=100000,
                                  as_of="2026-09-07")
    assert at_07["position_value"] == 22000      # 110 × 200
    assert at_07["unrealized_pnl"] == 2000
    assert at_07["cash_remaining"] == 80000

    at_10 = compute_portfolio_pnl(conn, "10W", initial_capital=100000,
                                  as_of="2026-09-10")
    assert at_10["position_value"] == 26000      # 130 × 200
    assert at_10["unrealized_pnl"] == 6000


def test_compute_portfolio_pnl_as_of_ignores_later_events(fresh_db):
    """as_of 之前的建仓计入，之后的建仓/平仓不计入。"""
    _, conn = fresh_db
    _seed_price(conn, "600519", 110.0, "2026-09-07")
    insert_trade_event(conn, "2026-09-01", "600519", "open", 100.0,
                      shares=200, portfolio="10W")
    # 09-12 才建的第二只、09-15 才平的第一只——as_of=09-07 都不该出现
    insert_trade_event(conn, "2026-09-12", "000001", "open", 10.0,
                      shares=1000, portfolio="10W")
    insert_trade_event(conn, "2026-09-15", "600519", "close", 120.0,
                      shares=200, pnl_amt=4000.0, portfolio="10W")

    out = compute_portfolio_pnl(conn, "10W", initial_capital=100000,
                                as_of="2026-09-07")
    assert out["positions_count"] == 1
    assert out["shares_total"] == 200
    assert out["realized_pnl"] == 0.0
    assert out["cash_remaining"] == 80000

    later = compute_portfolio_pnl(conn, "10W", initial_capital=100000,
                                  as_of="2026-09-20")
    assert later["realized_pnl"] == 4000.0
    assert later["positions_count"] == 1          # 只剩 000001
    assert later["shares_total"] == 1000


def test_compute_portfolio_pnl_as_of_partial_close_uses_average_cost(fresh_db):
    """部分平仓按移动平均成本扣减，剩余仓位成本不串轮次。"""
    _, conn = fresh_db
    _seed_price(conn, "600519", 100.0, "2026-09-03")
    insert_trade_event(conn, "2026-09-01", "600519", "open", 100.0,
                      shares=100, portfolio="10W")
    insert_trade_event(conn, "2026-09-02", "600519", "open", 120.0,
                      shares=100, portfolio="10W")
    insert_trade_event(conn, "2026-09-03", "600519", "close", 130.0,
                      shares=100, pnl_amt=2000.0, portfolio="10W")

    out = compute_portfolio_pnl(conn, "10W", initial_capital=100000,
                                as_of="2026-09-03")
    # 剩 100 股，均价仍是 110（均价不因部分平仓变化）
    assert out["shares_total"] == 100
    assert out["position_value"] == 10000.0        # 100 × 100
    assert out["realized_pnl"] == 2000.0
    # 现金 = 100000 − (10000+12000) + 13000 = 91000
    assert out["cash_remaining"] == 91000.0


def test_compute_portfolio_pnl_as_of_without_as_of_keeps_current_behaviour(fresh_db):
    """不传 as_of 时仍是原来的当前状态计算。"""
    _, conn = fresh_db
    _seed_price(conn, "600519", 110.0, "2026-09-07")
    insert_open_position(conn, "600519", "2026-09-01", 100.0, 0.1, 90.0, 120.0,
                        shares=200, portfolio="10W")
    insert_trade_event(conn, "2026-09-01", "600519", "open", 100.0,
                      shares=200, portfolio="10W")
    out = compute_portfolio_pnl(conn, "10W", initial_capital=100000)
    assert "as_of" not in out
    assert out["total_value"] == 102000


def test_get_open_positions_with_unrealized_as_of(fresh_db):
    """as_of 的持仓明细：按当日价、只含当日已建的仓。"""
    _, conn = fresh_db
    _seed_price(conn, "600519", 110.0, "2026-09-07")
    _seed_price(conn, "000001", 12.0, "2026-09-07")
    insert_trade_event(conn, "2026-09-01", "600519", "open", 100.0,
                      shares=200, portfolio="10W")
    insert_trade_event(conn, "2026-09-05", "000001", "open", 10.0,
                      shares=500, portfolio="10W")

    h = get_open_positions_with_unrealized(conn, portfolio="10W", as_of="2026-09-07")
    assert h["as_of"] == "2026-09-07"
    assert h["count"] == 2
    assert h["shares_total"] == 700
    by_code = {it["code"]: it for it in h["items"]}
    assert by_code["600519"]["floating_pnl"] == 2000.0     # (110-100)×200
    assert by_code["000001"]["floating_pnl"] == 1000.0     # (12-10)×500
    assert h["floating_pnl"] == 3000.0

    # 只看 09-01..09-04：第二只还没建仓
    h2 = get_open_positions_with_unrealized(conn, portfolio="10W", as_of="2026-09-02")
    assert h2["count"] == 1
    assert h2["items"][0]["code"] == "600519"
