"""缺口扫描脚本（scripts/check_gaps.py）的测试。"""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from db_repository import open_db

_SPEC = importlib.util.spec_from_file_location(
    "check_gaps",
    Path(__file__).resolve().parent.parent / "scripts" / "check_gaps.py",
)
gaps_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gaps_mod)


def _seed(conn, date, codes):
    for c in codes:
        conn.execute(
            "INSERT INTO daily_prices (code, trade_date, open, close, high, low,"
            " volume, amount) VALUES (?,?,1,1,1,1,1,1)",
            (c, date),
        )
    conn.commit()


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "m.db")
    conn = open_db(path)
    yield path, conn
    conn.close()


def test_no_gap_when_all_days_full(db):
    path, conn = db
    for d in ("2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23"):
        _seed(conn, d, [f"6000{i:02d}" for i in range(300)])
    report = gaps_mod.find_gaps(path, days=5, min_codes=280,
                                today=gaps_mod.dt.date(2026, 10, 23))
    assert report["gaps"] == []
    assert report["checked"] == 5


def test_detects_day_missing_entirely(db):
    path, conn = db
    for d in ("2026-10-19", "2026-10-21"):
        _seed(conn, d, [f"6000{i:02d}" for i in range(300)])
    report = gaps_mod.find_gaps(path, days=3, min_codes=280,
                                today=gaps_mod.dt.date(2026, 10, 21))
    dates = [g["date"] for g in report["gaps"]]
    assert "2026-10-20" in dates
    assert all(g["codes"] < 280 for g in report["gaps"])


def test_detects_partial_day(db):
    path, conn = db
    _seed(conn, "2026-10-21", [f"6000{i:02d}" for i in range(50)])   # 只有 50 只
    report = gaps_mod.find_gaps(path, days=1, min_codes=280,
                                today=gaps_mod.dt.date(2026, 10, 21))
    assert report["gaps"] == [{"date": "2026-10-21", "codes": 50}]
    assert report["worst"] == 50


def test_weekends_not_counted(db):
    path, conn = db
    # 2026-10-24/25 是周末，不应出现在 checked 里
    report = gaps_mod.find_gaps(path, days=10, min_codes=1,
                                today=gaps_mod.dt.date(2026, 10, 26))
    checked_dates = [g["date"] for g in report["gaps"]]
    assert "2026-10-24" not in checked_dates
    assert "2026-10-25" not in checked_dates


def test_cli_exit_codes(db):
    path, conn = db
    _seed(conn, "2026-10-21", [f"6000{i:02d}" for i in range(300)])
    base = ["--db", path, "--today", "2026-10-21", "--min-codes", "280"]
    assert gaps_mod.main(base + ["--days", "1"]) == 0
    assert gaps_mod.main(base + ["--days", "5"]) == 1
    assert gaps_mod.main(["--db", path + ".nope"]) == 2


class TestTodayExcludedByDefault:
    """**当前日历日**默认不算缺口（2026-11-05 修）。

    为什么：当天的行情要等收盘后才落地，而「缺口」的定义是
    「**本来应该有**数据却没有」。把当天算进期望 ⇒ 收盘前跑必报一个
    「当天 0 只」的**假缺口** ⇒ gap-heal 的「补抓后仍有缺口则失败」永远红，
    `clean` 恒为 false ⇒ **谱系永远存不进去**（实测 2026-11-05）。

    这是同一条链上的最后一断点：前三个（窗口不够 / 空库崩溃 /
    成分股接口抛异常）都修好后，谱系仍然存不进去 ——
    因为「体检」这一步自己报了个假缺口。
    """

    def _today_is_trading_day(self):
        """今天是不是 A 股交易日（周末/假日则本组测试无意义）。"""
        import datetime as dt
        return dt.date.today().weekday() < 5

    def test_today_not_expected_by_default(self, db):
        if not self._today_is_trading_day():
            pytest.skip("今天不是工作日，测不出「当天假缺口」")
        path, conn = db
        import datetime as dt
        today = dt.date.today()
        # 填满昨天（若昨天是交易日），今天留空
        yesterday = today - dt.timedelta(days=1)
        _seed(conn, yesterday.isoformat(), ["60%04d" % i for i in range(300)])
        report = gaps_mod.find_gaps(path, 1, 280, today=today)
        assert report["gaps"] == [], (
            "当天不该算缺口（当天行情收盘后才落地）"
        )

    def test_include_today_reports_the_gap(self, db):
        if not self._today_is_trading_day():
            pytest.skip("今天不是工作日，测不出「当天假缺口」")
        path, conn = db
        import datetime as dt
        today = dt.date.today()
        yesterday = today - dt.timedelta(days=1)
        _seed(conn, yesterday.isoformat(), ["60%04d" % i for i in range(300)])
        report = gaps_mod.find_gaps(path, 1, 280, today=today,
                                   include_today=True)
        assert [g["date"] for g in report["gaps"]] == [today.isoformat()], (
            "显式要求含当天时，应当报出当天的缺口"
        )

    def test_historical_baseline_day_still_checked(self, db):
        """--today 传**历史日期**时，那一天照常算进去（复检语义不变）。"""
        path, conn = db
        # 10-19 与 10-21 都有数据，10-20 没有
        _seed(conn, "2026-10-19", ["61%04d" % i for i in range(300)])
        _seed(conn, "2026-10-21", ["62%04d" % i for i in range(300)])
        report = gaps_mod.find_gaps(path, 3, 280,
                                   today=gaps_mod.dt.date(2026, 10, 21))
        assert [g["date"] for g in report["gaps"]] == ["2026-10-20"], (
            "历史里真的缺的那天仍然必须报出来"
        )
