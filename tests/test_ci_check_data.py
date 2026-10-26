"""CI 数据完整性校验（scripts/ci_check_data.py）的测试。

这个脚本是 deploy-site workflow 的门禁：数据不完整时必须返回非 0，
否则会把旧数据当新数据发布而不报警。
"""

import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pytest

from db_repository import open_db

_SPEC = importlib.util.spec_from_file_location(
    "ci_check_data",
    Path(__file__).resolve().parent.parent / "scripts" / "ci_check_data.py",
)
ci = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ci)


def _seed(conn, codes, trade_date, close=10.0):
    for code in codes:
        conn.execute(
            """INSERT INTO daily_prices (code, trade_date, open, close, high, low,
                                        volume, amount) VALUES (?,?,?,?,?,?,0,0)""",
            (code, trade_date, close, close, close, close),
        )
    conn.commit()


def _seed_picks(conn, date, n=3):
    for i in range(n):
        conn.execute(
            "INSERT INTO daily_picks (date, rank, kind, code, name, strategy, buy, stop, target, score) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (date, i + 1, "均线", f"60000{i}", "测试", "linyuan", 10.0, 9.0, 12.0, 80.0),
        )
    conn.commit()


def _seed_plan(conn, date):
    conn.execute(
        "INSERT INTO trade_plan (plan_date, portfolio, code, action, plan_price, size_pct, "
        "stop_price, tp_price, rr_ratio, status) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (date, "default", "600001", "buy", 10.0, 0.1, 9.0, 12.0, 2.0, "pending"),
    )
    conn.commit()


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "m.db")
    conn = open_db(path)
    yield path, conn
    conn.close()


def test_pass_when_everything_lands(db):
    path, conn = db
    _seed(conn, [f"60000{i}" for i in range(300)], "2026-10-26")
    _seed_picks(conn, "2026-10-26")
    _seed_plan(conn, "2026-10-26")

    out = ci.check(path, "2026-10-26", min_codes=250, prices_only=False)
    assert out["ok"] is True
    assert out["latest_price"] == "2026-10-26"
    assert out["codes_on_expect"] == 300
    assert out["pick_date"] == "2026-10-26"
    assert out["plan_date"] == "2026-10-26"
    assert out["reasons"] == []


def test_fail_when_prices_stale(db):
    path, conn = db
    _seed(conn, [f"60000{i}" for i in range(300)], "2026-10-23")
    _seed_picks(conn, "2026-10-23")
    _seed_plan(conn, "2026-10-23")

    out = ci.check(path, "2026-10-26", min_codes=250, prices_only=False)
    assert out["ok"] is False
    assert any("2026-10-23" in r and "2026-10-26" in r for r in out["reasons"])


def test_fail_when_coverage_too_low(db):
    path, conn = db
    _seed(conn, [f"60000{i}" for i in range(10)], "2026-10-26")
    _seed_picks(conn, "2026-10-26")
    _seed_plan(conn, "2026-10-26")

    out = ci.check(path, "2026-10-26", min_codes=250, prices_only=False)
    assert out["ok"] is False
    assert any("只股票有行情" in r for r in out["reasons"])


def test_fail_when_picks_or_plan_missing(db):
    path, conn = db
    _seed(conn, [f"60000{i}" for i in range(300)], "2026-10-26")

    out = ci.check(path, "2026-10-26", min_codes=250, prices_only=False)
    assert out["ok"] is False
    assert any("daily_picks 为空" in r for r in out["reasons"])
    assert any("trade_plan 为空" in r for r in out["reasons"])

    # 只校验行情时通过（同步重试阶段用这个模式）
    assert ci.check(path, "2026-10-26", min_codes=250, prices_only=True)["ok"] is True


def test_fail_when_picks_behind_prices(db):
    path, conn = db
    _seed(conn, [f"60000{i}" for i in range(300)], "2026-10-26")
    _seed_picks(conn, "2026-10-23")
    _seed_plan(conn, "2026-10-26")

    out = ci.check(path, "2026-10-26", min_codes=250, prices_only=False)
    assert out["ok"] is False
    assert any("选股日期" in r and "落后" in r for r in out["reasons"])


def test_empty_prices_table_fails(db):
    path, _ = db
    out = ci.check(path, "2026-10-26", min_codes=250, prices_only=False)
    assert out["ok"] is False
    assert any("daily_prices 表为空" in r for r in out["reasons"])


def test_cli_exit_codes(db, monkeypatch):
    path, conn = db
    _seed(conn, [f"60000{i}" for i in range(300)], "2026-10-26")
    _seed_picks(conn, "2026-10-26")
    _seed_plan(conn, "2026-10-26")

    assert ci.main(["--db", path, "--expect", "2026-10-26", "--min-codes", "250"]) == 0
    assert ci.main(["--db", path, "--expect", "2026-10-27", "--min-codes", "250"]) == 1

    out_file = path + ".out"
    monkeypatch.setenv("GITHUB_OUTPUT", out_file)
    ci.main(["--db", path, "--expect", "2026-10-27", "--min-codes", "250", "--github-output"])
    assert "data_ok=false" in Path(out_file).read_text()


def test_latest_weekday_skips_weekend():
    # 2026-10-25 是周日 → 最近工作日应为 10-23（周五）
    assert ci.latest_weekday(dt.date(2026, 10, 25)) == "2026-10-23"
    assert ci.latest_weekday(dt.date(2026, 10, 26)) == "2026-10-26"
