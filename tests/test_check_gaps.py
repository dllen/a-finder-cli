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


class TestTodayIsTimeAware:
    """**当天**要不要算期望，取决于「行情出了没有」（2026-11-05 修）。

    两次踩坑，方向相反 —— 所以规则必须按**时间**判断：

    - 收盘**前**跑：当天必然 0 只 ⇒ 假缺口 ⇒ 「补抓后仍有缺口则失败」永远红、
      `clean` 恒 false ⇒ 谱系永远存不进去；
    - 收盘**后**跑（实测 20:34 北京）：当天数据**已经有了**，却仍被「整天排除」
      ⇒ **当天的数据永远补不进谱系**（谱系卡在 2026-11-04 就是这么来的）。

    ⇒ 北京时间 **15:30** 之后，当天算期望。这里用 monkeypatch 控制 `_beijing_now`，
    不依赖跑测试的真实时间（否则白天/晚上跑结果不同，测试会飘）。
    """

    def _patch_now(self, monkeypatch, hour, minute, day=None):
        import datetime as dt
        day = day or dt.date.today()
        fixed = dt.datetime(day.year, day.month, day.day, hour, minute,
                            tzinfo=dt.timezone(dt.timedelta(hours=8)))
        monkeypatch.setattr(gaps_mod, "_beijing_now", lambda: fixed)
        return day

    def _weekday_day(self):
        """找一个工作日当「今天」（周末测不出交易日逻辑）。"""
        import datetime as dt
        d = dt.date.today()
        while d.weekday() >= 5:
            d -= dt.timedelta(days=1)
        return d

    def test_before_close_today_not_expected(self, db, monkeypatch):
        """收盘前（09:00）→ 当天不算期望 ⇒ 不报假缺口。"""
        path, conn = db
        day = self._patch_now(monkeypatch, 9, 0, self._weekday_day())
        import datetime as dt
        _seed(conn, (day - dt.timedelta(days=1)).isoformat(),
              ["60%04d" % i for i in range(300)])
        report = gaps_mod.find_gaps(path, 1, 280, today=day)
        assert report["gaps"] == [], "收盘前不该把当天算成缺口"

    def test_after_close_today_is_expected(self, db, monkeypatch):
        """收盘后（16:00）→ 当天**算**期望 ⇒ 缺了就要报（这是之前漏掉的那半天）。"""
        path, conn = db
        day = self._patch_now(monkeypatch, 16, 0, self._weekday_day())
        import datetime as dt
        _seed(conn, (day - dt.timedelta(days=1)).isoformat(),
              ["60%04d" % i for i in range(300)])
        report = gaps_mod.find_gaps(path, 1, 280, today=day)
        assert [g["date"] for g in report["gaps"]] == [day.isoformat()], (
            "收盘后当天数据已出，缺了必须报 —— 否则当天永远补不进谱系"
        )

    def test_after_close_today_present_is_clean(self, db, monkeypatch):
        """收盘后当天数据齐全 ⇒ 无缺口。"""
        path, conn = db
        day = self._patch_now(monkeypatch, 16, 0, self._weekday_day())
        import datetime as dt
        _seed(conn, day.isoformat(), ["60%04d" % i for i in range(300)])
        _seed(conn, (day - dt.timedelta(days=1)).isoformat(),
              ["60%04d" % i for i in range(300)])
        report = gaps_mod.find_gaps(path, 1, 280, today=day)
        assert report["gaps"] == []

    def test_boundary_1530(self, db, monkeypatch):
        """15:30 是分界线：15:29 不算、15:30 算。"""
        path, conn = db
        day = self._weekday_day()
        import datetime as dt
        _seed(conn, (day - dt.timedelta(days=1)).isoformat(),
              ["60%04d" % i for i in range(300)])

        self._patch_now(monkeypatch, 15, 29, day)
        assert gaps_mod.find_gaps(path, 1, 280, today=day)["gaps"] == []

        self._patch_now(monkeypatch, 15, 30, day)
        assert [g["date"] for g in
                gaps_mod.find_gaps(path, 1, 280, today=day)["gaps"]] == [day.isoformat()]

    def test_include_today_forces_expected(self, db, monkeypatch):
        """`--include-today` 无视时间，强制把当天算进去。"""
        path, conn = db
        day = self._patch_now(monkeypatch, 9, 0, self._weekday_day())
        import datetime as dt
        _seed(conn, (day - dt.timedelta(days=1)).isoformat(),
              ["60%04d" % i for i in range(300)])
        report = gaps_mod.find_gaps(path, 1, 280, today=day, include_today=True)
        assert [g["date"] for g in report["gaps"]] == [day.isoformat()]

    def test_historical_today_param_still_expected(self, db, monkeypatch):
        """`--today` 传历史日期时照常算进去（那种日期的数据本来就该在）。"""
        path, conn = db
        import datetime as dt
        self._patch_now(monkeypatch, 9, 0)
        past = dt.date.today() - dt.timedelta(days=14)
        while past.weekday() >= 5:
            past -= dt.timedelta(days=1)
        report = gaps_mod.find_gaps(path, 1, 280, today=past)
        assert [g["date"] for g in report["gaps"]] == [past.isoformat()]


