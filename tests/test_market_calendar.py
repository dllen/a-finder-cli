"""交易日历单测：同步与巡检共用的唯一口径。"""

import datetime as dt

import pytest

import market_calendar as mc


@pytest.fixture(autouse=True)
def _reset():
    """每个测试前后重置日历可用性缓存，避免测试间串味。"""
    mc._reset_workday_cache()
    yield
    mc._reset_workday_cache()


def test_weekend_excluded():
    """2026-10-24/25 是周六周日 → 不在交易日里。"""
    days = mc.trading_days(dt.date(2026, 10, 23), dt.date(2026, 10, 27))
    assert days == ["2026-10-23", "2026-10-26", "2026-10-27"]


def test_national_day_holiday_excluded():
    """2026-10-01 起国庆长假 → 假期内无交易日。"""
    days = mc.trading_days(dt.date(2026, 9, 30), dt.date(2026, 10, 9))
    assert "2026-10-01" not in days
    assert "2026-10-02" not in days
    assert days, "长假两侧应仍有交易日"


def test_makeup_weekend_excluded_by_default():
    """调休补班的周末在 chinese_calendar 里是工作日，但 A 股不开市 → 默认排除。"""
    days = mc.trading_days(dt.date(2026, 9, 1), dt.date(2026, 9, 30))
    assert all(dt.date.fromisoformat(d).weekday() < 5 for d in days)
    # 2026-09-20 是调休补班的周日，日历记为工作日，但 A 股不开市
    assert "2026-09-20" not in days


def test_makeup_weekend_kept_when_known_and_enabled():
    """显式开启且该日在 known 里 → 保留（同步侧语义：库里有就认）。"""
    days = mc.trading_days(
        dt.date(2026, 9, 1), dt.date(2026, 9, 30),
        include_known_weekend=True, known={"2026-09-20"},
    )
    assert "2026-09-20" in days


def test_makeup_weekend_excluded_when_enabled_but_not_known():
    """开启 include_known_weekend 但调休周末不在 known 里 → 仍然排除。

    同步语义的负向分支：必须「库里真有这一天」才认，不能退化成
    「开关一开就收下所有调休日」。
    """
    days = mc.trading_days(
        dt.date(2026, 9, 1), dt.date(2026, 9, 30),
        include_known_weekend=True, known=set(),
    )
    assert "2026-09-20" not in days
    assert all(dt.date.fromisoformat(d).weekday() < 5 for d in days)


def test_calendar_unavailable_returns_empty(monkeypatch):
    """chinese_calendar 不可用 → 返回 []，绝不猜。"""
    monkeypatch.setattr(mc, "_WORKDAY_FN", None)
    monkeypatch.setattr(mc, "_WORKDAY_LOADED", True)
    assert mc.trading_days(dt.date(2026, 10, 1), dt.date(2026, 10, 31)) == []


def test_empty_range():
    assert mc.trading_days(dt.date(2026, 10, 27), dt.date(2026, 10, 26)) == []


def test_single_trading_day():
    assert mc.trading_days(dt.date(2026, 10, 26), dt.date(2026, 10, 26)) == ["2026-10-26"]
