"""增量同步的缺口判定（市场日历）测试。

旧实现按日历日比缺口：长假期间（如国庆 10-01~10-07 休市）每个交易日都会
对全部 300 只股票重新回补一遍空区间——请求白花、限流风险上升，还不产生数据。
新实现只对「全市场真有行情的日期」判缺口。
"""

import sqlite3

import pytest

import market_calendar as mc
from db_repository import get_market_trade_dates, open_db
from sync_service import _build_gap_segments, _build_missing_segments


@pytest.fixture(autouse=True)
def _reset_calendar_cache():
    mc._reset_workday_cache()
    yield
    mc._reset_workday_cache()


def _seed_price(conn, code, trade_date, close=10.0):
    conn.execute(
        """INSERT INTO daily_prices (code, trade_date, open, close, high, low,
                                    volume, amount) VALUES (?,?,?,?,?,?,0,0)""",
        (code, trade_date, close, close, close, close),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# _build_missing_segments
# ---------------------------------------------------------------------------

def test_no_holiday_refetch():
    """长假区间不算缺口——市场日历里没有 10-01~10-07 的日期。"""
    existing = ["2026-09-29", "2026-09-30", "2026-10-08"]
    market = ["2026-09-29", "2026-09-30", "2026-10-08", "2026-10-09"]
    assert _build_missing_segments(existing, market) == [("20261009", "20261009")]


def test_contiguous_missing_dates_merge_into_one_segment():
    """连续缺 3 个交易日 → 合成一个请求，而不是 3 个。"""
    existing = ["2026-10-08"]
    market = ["2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13"]
    assert _build_missing_segments(existing, market) == [("20261009", "20261013")]


def test_suspended_stock_missing_dates_split_by_adjacency():
    """中间只缺一天时单独成段（不把有数据的日期也重拉）。"""
    existing = ["2026-10-08", "2026-10-12"]
    market = ["2026-10-08", "2026-10-09", "2026-10-12", "2026-10-13"]
    assert _build_missing_segments(existing, market) == [
        ("20261009", "20261009"),
        ("20261013", "20261013"),
    ]


def test_nothing_missing_returns_empty():
    market = ["2026-10-08", "2026-10-09"]
    assert _build_missing_segments(market, market) == []


def test_cold_start_all_expected_missing():
    assert _build_missing_segments([], ["2026-10-08", "2026-10-09"]) == [
        ("20261008", "20261009")
    ]


def test_old_calendar_gap_logic_still_refetches_holidays():
    """对照：旧实现会把长假区间当缺口（这就是要替换掉的行为）。"""
    import datetime as dt

    existing = ["2026-09-30", "2026-10-08"]
    segs = _build_gap_segments(existing, dt.date(2026, 9, 30), dt.date(2026, 10, 8))
    assert segs and segs[0][0] == "20261001", segs


# ---------------------------------------------------------------------------
# get_market_trade_dates
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "m.db")
    conn = open_db(path)
    yield path, conn
    conn.close()


def test_market_calendar_is_union_of_all_codes(db):
    _, conn = db
    _seed_price(conn, "600519", "2026-10-08")
    _seed_price(conn, "600519", "2026-10-09")
    _seed_price(conn, "000001", "2026-10-09")      # 只有它有 10-09
    _seed_price(conn, "000001", "2026-10-12")

    dates = get_market_trade_dates(conn, "2026-10-08", "2026-10-12")
    assert dates == ["2026-10-08", "2026-10-09", "2026-10-12"]


def test_market_calendar_ignores_dates_outside_range(db):
    _, conn = db
    _seed_price(conn, "600519", "2026-09-30")
    _seed_price(conn, "600519", "2026-10-08")
    assert get_market_trade_dates(conn, "2026-10-01", "2026-10-31") == ["2026-10-08"]


def test_market_calendar_empty_on_cold_start(db):
    _, conn = db
    assert get_market_trade_dates(conn, "2026-10-01", "2026-10-31") == []


# ---------------------------------------------------------------------------
# 交易日历（chinese_calendar）：修复「库里没有的日子永远补不上」
# ---------------------------------------------------------------------------

import datetime as dt

from market_calendar import trading_days


def _trading_days_from_calendar(start, end, known):
    """兼容包装：同步侧语义（库里有调休日就认）。"""
    return trading_days(start, end, include_known_weekend=True, known=known) or None


def test_calendar_marks_missing_trading_day():
    """10-26（周一）在库里缺失，但真实交易日历必须包含它，否则永远补不上。"""
    days = _trading_days_from_calendar(dt.date(2026, 10, 19), dt.date(2026, 10, 27), set())
    if days is None:
        pytest.skip("未安装 chinese_calendar")
    assert "2026-10-26" in days
    assert "2026-10-24" not in days      # 周六
    assert "2026-10-25" not in days      # 周日


def test_calendar_excludes_makeup_weekend_unless_known():
    """调休补班的周末在 chinese_calendar 里是工作日，但股市不开市 ——
    除非库里已有该日数据，否则不应计入应有交易日。"""
    days = _trading_days_from_calendar(dt.date(2026, 10, 1), dt.date(2026, 10, 12), set())
    if days is None:
        pytest.skip("未安装 chinese_calendar")
    assert all(dt.date.fromisoformat(d).weekday() < 5 for d in days)


def test_weekend_only_segment_is_dropped():
    """库到周五、今天周六 → 尾部只有周末，不应产生请求段。"""
    assert _build_missing_segments(["2026-10-23"], ["2026-10-23", "2026-10-24"]) == []


def test_mixed_trailing_segment_kept():
    """尾部含交易日则保留（10-26 是周一）。"""
    segs = _build_missing_segments(
        ["2026-10-23"], ["2026-10-23", "2026-10-24", "2026-10-25", "2026-10-26", "2026-10-27"])
    assert segs == [("20261024", "20261027")]
