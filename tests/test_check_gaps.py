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
