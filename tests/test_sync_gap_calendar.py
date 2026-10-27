"""增量同步的缺口判定（市场日历）测试。

旧实现按日历日比缺口：长假期间（如国庆 10-01~10-07 休市）每个交易日都会
对全部 300 只股票重新回补一遍空区间——请求白花、限流风险上升，还不产生数据。
新实现只对「全市场真有行情的日期」判缺口。
"""

import datetime as dt

import pytest

import market_calendar as mc
import sync_service
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
# sync_hs300_range(gap_fill=True)：缺口必须走共享日历，且用同步侧语义
# ---------------------------------------------------------------------------

# 库里已有的最后一天；尾部缺口应为 10-24~10-27（日历只认 10-26/27 两个交易日）
_SEED_DAY = "2026-10-23"


def _run_gap_fill(db_path, monkeypatch, calendar_days):
    """跑一次 gap_fill 同步，返回日历调用与 _build_missing_segments 入参记录。"""
    calendar_calls = []
    segment_calls = []

    def recording_trading_days(start, end, *, include_known_weekend=False, known=None):
        calendar_calls.append(
            {
                "start": start,
                "end": end,
                "include_known_weekend": include_known_weekend,
                "known": set(known or ()),
            }
        )
        return calendar_days(start, end)

    def record_segments(existing, expected):
        segment_calls.append((existing, expected))
        return []          # 无缺口 → 不进入网络抓取

    monkeypatch.setattr(mc, "trading_days", recording_trading_days)
    monkeypatch.setattr(sync_service, "_build_missing_segments", record_segments)
    monkeypatch.setattr(
        sync_service, "fetch_hs300_constituents", lambda: {"600519": "贵州茅台"}
    )
    sync_service.sync_hs300_range(
        db_path, "2026-10-19", "2026-10-27",
        None, 1, 1000.0, 0, 0.1, False, False, True,
    )
    return calendar_calls, segment_calls


def test_gap_fill_uses_calendar_with_sync_semantics(db, monkeypatch):
    """gap_fill 必须传 include_known_weekend=True，并拿库里已有日期作 known。

    同步侧「库里有就认」：把生产代码改成 False（或漏传 known）时这里会红。
    """
    path, conn = db
    _seed_price(conn, "600519", _SEED_DAY)

    calls, segments = _run_gap_fill(
        path, monkeypatch, lambda s, e: ["2026-10-26", "2026-10-27"]
    )

    assert len(calls) == 1
    assert calls[0]["include_known_weekend"] is True
    assert calls[0]["known"] == {_SEED_DAY}
    assert segments == [([_SEED_DAY], ["2026-10-26", "2026-10-27"])]


def test_gap_fill_falls_back_when_calendar_unavailable(db, monkeypatch):
    """日历不可用（返回 []）→ `or None` 后走回退分支：市场日历 + 尾部窗口。"""
    path, conn = db
    _seed_price(conn, "600519", _SEED_DAY)

    _, segments = _run_gap_fill(path, monkeypatch, lambda s, e: [])

    assert segments == [
        (
            [_SEED_DAY],
            ["2026-10-23", "2026-10-24", "2026-10-25", "2026-10-26", "2026-10-27"],
        )
    ]


def test_weekend_only_segment_is_dropped():
    """库到周五、今天周六 → 尾部只有周末，不应产生请求段。"""
    assert _build_missing_segments(["2026-10-23"], ["2026-10-23", "2026-10-24"]) == []


def test_mixed_trailing_segment_kept():
    """尾部含交易日则保留（10-26 是周一）。"""
    segs = _build_missing_segments(
        ["2026-10-23"], ["2026-10-23", "2026-10-24", "2026-10-25", "2026-10-26", "2026-10-27"])
    assert segs == [("20261024", "20261027")]
