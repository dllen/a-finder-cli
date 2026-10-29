"""同步侧「未结算当日日线」守卫的测试（sync_service._market_settled / _drop_unsettled_today）。

背景（2026-10-29 查出的真实数据损坏）：
  数据源在**盘中**也会返回「今天」的日线，但那是未收盘的半截 K 线。
  `logs/fetch_success.log` 记录 2026-10-27 12:15 与 12:19 两次 gap-fill（午休时段），
  各写入 rows=1/只 → 300 只全部存了半截日线：600519 存 close=1348.0 而真实 1355.90，
  成交量 16961 vs 真实 38669（44%）。实测 254/300 只受影响。

  更糟的是它**不会自愈**：gap-fill 只补「缺失」的日期（看有没有行），
  半截日线**有行**所以永不重抓；审计 L1 判据是「>=280 只」，
  300 只全都“有数据”也拦不住；它还会污染 MA/动量/回测窗口与当天计划。
"""

import datetime as dt

import pytest

from db_repository import PriceRow
from sync_service import _drop_unsettled_today, _market_settled


def _row(date: str) -> PriceRow:
    return PriceRow(
        code="600519", trade_date=date,
        open_price=1.0, close_price=1.0, high_price=1.0, low_price=1.0,
        volume=1.0, amount=0.0, amplitude=0.0, pct_change=0.0,
        change=0.0, turnover=0.0,
    )


# 2026-10-29 是周四（交易日）
TRADING_DAY = dt.date(2026, 10, 29)


def _at(h, m):
    return dt.datetime(TRADING_DAY.year, TRADING_DAY.month, TRADING_DAY.day, h, m)


# ---------------------------------------------------------------- _market_settled


@pytest.mark.parametrize("hour,minute,expected", [
    (9, 30, False),    # 早盘
    (11, 45, False),   # 午休 —— 事故就是这时候抓的
    (14, 59, False),   # 收盘前一分钟
    (15, 5, True),     # 边界：正好结算点
    (15, 30, True),    # CI 的班次
    (20, 0, True),
])
def test_market_settled_on_trading_day(hour, minute, expected):
    assert _market_settled(_at(hour, minute)) is expected


def test_market_settled_on_weekend_is_true():
    """周末不可能有「当天的新日线」，不必拦截。"""
    assert _market_settled(dt.datetime(2026, 10, 31, 10, 0)) is True   # 周六
    assert _market_settled(dt.datetime(2026, 11, 1, 10, 0)) is True    # 周日


# ---------------------------------------------------------------- _drop_unsettled_today


def test_drops_today_before_settle_keeps_history():
    """盘中抓取：丢掉「今天」，历史日期一律保留。"""
    rows = [_row("2026-10-26"), _row("2026-10-27"), _row("2026-10-29")]
    kept = _drop_unsettled_today(rows, TRADING_DAY, now=_at(11, 45))
    assert [r.trade_date for r in kept] == ["2026-10-26", "2026-10-27"]


def test_keeps_today_after_settle():
    rows = [_row("2026-10-26"), _row("2026-10-29")]
    kept = _drop_unsettled_today(rows, TRADING_DAY, now=_at(15, 30))
    assert len(kept) == 2


def test_drop_is_noop_when_no_today_rows():
    """只补历史日期时不受影响（正是 gap-fill 的常见场景）。"""
    rows = [_row("2026-10-22"), _row("2026-10-23")]
    kept = _drop_unsettled_today(rows, TRADING_DAY, now=_at(11, 0))
    assert len(kept) == 2


def test_does_not_drop_other_days_that_look_like_today():
    """只按**日期相等**判断，不按「最新一天」判断 —— 否则补昨天也会被误删。"""
    rows = [_row("2026-10-28")]
    kept = _drop_unsettled_today(rows, TRADING_DAY, now=_at(11, 0))
    assert len(kept) == 1
