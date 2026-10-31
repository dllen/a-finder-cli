"""三层缺口体检单测。

核心回归：库里有 10-27 而缺 10-26 时，必须能检出 10-26
（2026-10-27 线上数据缺失就是因为基准取自库内数据，缺的那天不可见）。
"""

import datetime as dt
import importlib.util
from pathlib import Path

import pytest

from db_repository import open_db

_SPEC = importlib.util.spec_from_file_location(
    "audit_coverage",
    Path(__file__).resolve().parent.parent / "scripts" / "audit_coverage.py",
)
audit_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(audit_mod)

TODAY = dt.date(2026, 10, 27)
FULL_PORTFOLIOS = ["5W", "10W", "15W", "20W", "25W", "30W", "35W",
                   "40W", "45W", "50W", "default"]


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "audit.db")
    conn = open_db(path)
    yield path, conn
    conn.close()


def _seed_prices(conn, date, n=300):
    for i in range(n):
        conn.execute(
            "INSERT INTO daily_prices (code, trade_date, open, close, high, low,"
            " volume, amount) VALUES (?,?,1,1,1,1,1,1)",
            (f"6000{i:03d}", date),
        )
    conn.commit()


def _seed_picks(conn, date):
    conn.execute(
        "INSERT INTO daily_picks (date, rank, kind, code, name, strategy,"
        " buy, stop, target, score, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (date, 1, "均线", "600519", "贵州茅台", "均线突破", 1.0, 0.9, 1.2, 80.0, "2026-10-27T20:00:00"),
    )
    conn.commit()


def _seed_plan(conn, date, portfolios=None):
    for pf in (portfolios or FULL_PORTFOLIOS):
        conn.execute(
            "INSERT INTO trade_plan (plan_date, portfolio, code, action, plan_price,"
            " size_pct, stop_price, tp_price, rr_ratio, status, reason,"
            " rationale_json, params_hash, created_at, shares)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (date, pf, "600519", "buy", 100.0, 0.1, 90.0, 120.0, 2.0,
             "ok", "", "{}", "hash1", "2026-10-27T20:00:00", 100),
        )
    conn.commit()


def _seed_full_day(conn, date):
    _seed_prices(conn, date)
    _seed_picks(conn, date)
    _seed_plan(conn, date)


def test_all_layers_complete_no_gaps(db):
    path, conn = db
    for d in ("2026-10-23", "2026-10-26", "2026-10-27"):
        _seed_full_day(conn, d)
    # window_days=4 → 区间 10-23~10-27，日历正好给出这 3 个交易日（24/25 是周末）
    report = audit_mod.audit(path, today=TODAY, window_days=4)
    assert report.ok
    assert report.gaps == []


def test_detects_missing_day_even_when_db_has_later_date(db):
    """核心回归：库里有 10-27，10-26 整天缺失 → 必须检出（旧实现看不见）。"""
    path, conn = db
    _seed_full_day(conn, "2026-10-23")
    _seed_full_day(conn, "2026-10-27")
    report = audit_mod.audit(path, today=TODAY, window_days=4)
    assert not report.ok
    assert "2026-10-26" in [g.date for g in report.gaps]


def test_l1_gap_suppresses_l2_l3(db):
    """行情缺失时只报 L1，不报派生出来的 L2/L3（避免噪声）。"""
    path, conn = db
    _seed_full_day(conn, "2026-10-23")
    _seed_full_day(conn, "2026-10-27")
    _seed_prices(conn, "2026-10-26", n=100)          # 只有 100 只 → L1 缺
    report = audit_mod.audit(path, today=TODAY, window_days=4)
    gap_1026 = [g for g in report.gaps if g.date == "2026-10-26"]
    assert [g.layer for g in gap_1026] == ["L1"]
    assert any(g.date == "2026-10-26" and g.layer in ("L2", "L3")
               for g in report.suppressed)


def test_l2_gap_detected(db):
    path, conn = db
    _seed_prices(conn, "2026-10-26")
    _seed_plan(conn, "2026-10-26")
    report = audit_mod.audit(path, today=dt.date(2026, 10, 26), window_days=0)
    gap = [g for g in report.gaps if g.date == "2026-10-26"]
    assert [g.layer for g in gap] == ["L2"]


# ---------------------------------------------------------------- codes 字段


def test_l1_gap_records_actual_code_count(db):
    """L1 缺口必须带上当天**实际只数**（Gap.codes）。

    修复脚本靠它区分两类缺口（2026-10-30 修）：
      - codes == 0 → 整天没抓过，没有序列可污染 → 可用宽窗自动补
      - codes 1..279 → 抓了但没抓全，补它会与既有序列**混复权口径** → 窄窗
    以前只能从 detail 字符串反解「行情只有 N 只」—— 那是拿文案当数据源。
    """
    path, conn = db
    _seed_full_day(conn, "2026-10-23")
    _seed_full_day(conn, "2026-10-27")
    _seed_prices(conn, "2026-10-26", n=100)          # 只有 100 只 → L1 缺
    report = audit_mod.audit(path, today=TODAY, window_days=4)
    gap = [g for g in report.gaps if g.date == "2026-10-26"][0]
    assert gap.layer == "L1"
    assert gap.codes == 100


def test_l1_fully_missing_day_records_zero_codes(db):
    """整天没有一行 → codes == 0（不是 None）。"""
    path, conn = db
    _seed_full_day(conn, "2026-10-23")
    _seed_full_day(conn, "2026-10-27")
    report = audit_mod.audit(path, today=TODAY, window_days=4)
    gap = [g for g in report.gaps if g.date == "2026-10-26"][0]
    assert gap.codes == 0


def test_non_l1_gaps_have_no_codes(db):
    """L2/L3 缺口不涉及只数 → codes 为 None。"""
    path, conn = db
    _seed_prices(conn, "2026-10-26")
    report = audit_mod.audit(path, today=dt.date(2026, 10, 26), window_days=0)
    gap = [g for g in report.gaps if g.date == "2026-10-26"][0]
    assert gap.layer == "L2"
    assert gap.codes is None


def test_gap_roundtrip_preserves_codes():
    """报告的 JSON 往返必须带上 codes（否则修复脚本拿不到分类依据）。"""
    gap = audit_mod.Gap("2026-10-26", "L1", "行情只有 100 只（阈值 280）", codes=100)
    again = audit_mod.Gap.from_dict(gap.to_dict())
    assert again == gap
    assert again.codes == 100


def test_gap_from_dict_tolerates_missing_codes():
    """旧报告没有 codes 字段 → None（不是 KeyError）。"""
    again = audit_mod.Gap.from_dict(
        {"date": "2026-10-26", "layer": "L1", "detail": "行情只有 0 只"})
    assert again.codes is None


def test_gap_from_dict_rejects_non_integer_codes():
    """坏 codes（如字符串）→ 归一成 None，不猜、不炸。"""
    again = audit_mod.Gap.from_dict(
        {"date": "2026-10-26", "layer": "L1", "detail": "x", "codes": "100"})
    assert again.codes is None
    again2 = audit_mod.Gap.from_dict(
        {"date": "2026-10-26", "layer": "L1", "detail": "x", "codes": True})
    assert again2.codes is None


def test_few_picks_rows_not_a_gap(db):
    """选股行数天然波动 → 只看有没有行，不设行数阈值（否则大量误报）。"""
    path, conn = db
    _seed_prices(conn, "2026-10-26")
    _seed_picks(conn, "2026-10-26")                   # 仅 1 行
    _seed_plan(conn, "2026-10-26")
    report = audit_mod.audit(path, today=dt.date(2026, 10, 26), window_days=0)
    assert report.ok


def test_l2_gap_suppresses_l3(db):
    """L2 缺时不再判 L3：没有选股无从生成计划（补 spec 未覆盖的一条设计约束）。"""
    path, conn = db
    _seed_prices(conn, "2026-10-26")                  # L1 齐全，但无选股、无计划
    report = audit_mod.audit(path, today=dt.date(2026, 10, 26), window_days=0)
    gap = [g for g in report.gaps if g.date == "2026-10-26"]
    assert [g.layer for g in gap] == ["L2"]


def test_l3_gap_when_portfolio_missing(db):
    path, conn = db
    _seed_prices(conn, "2026-10-26")
    _seed_picks(conn, "2026-10-26")
    _seed_plan(conn, "2026-10-26", portfolios=["10W", "default"])
    report = audit_mod.audit(path, today=dt.date(2026, 10, 26), window_days=0)
    gap = [g for g in report.gaps if g.date == "2026-10-26"]
    assert [g.layer for g in gap] == ["L3"]
    assert "5W" in gap[0].detail


def test_calendar_unavailable_raises(db, monkeypatch):
    path, conn = db
    _seed_full_day(conn, "2026-10-26")
    import market_calendar as mc
    monkeypatch.setattr(mc, "_WORKDAY_FN", None)
    monkeypatch.setattr(mc, "_WORKDAY_LOADED", True)
    with pytest.raises(audit_mod.CalendarUnavailable):
        audit_mod.audit(path, today=TODAY, window_days=4)


def test_cli_exit_codes(db, capsys):
    path, conn = db
    for d in ("2026-10-23", "2026-10-26", "2026-10-27"):
        _seed_full_day(conn, d)
    base = ["--db", path, "--today", "2026-10-27", "--window-days", "4"]
    assert audit_mod.main(base) == 0
    conn.execute("DELETE FROM daily_picks WHERE date='2026-10-26'")
    conn.commit()
    assert audit_mod.main(base) == 1
    assert audit_mod.main(["--db", str(Path(path).parent / "nope.db")]) == 2


def test_cli_json_output(db, capsys):
    path, conn = db
    _seed_full_day(conn, "2026-10-26")
    audit_mod.main(["--db", path, "--today", "2026-10-26", "--window-days", "0", "--json"])
    import json
    payload = json.loads(capsys.readouterr().out)
    assert "gaps" in payload and "expected_dates" in payload


# ------------------------------------------------- I-1：报告没落盘 ≠ 无缺口


def test_cli_write_failure_returns_two_without_traceback(db, tmp_path, capsys):
    """报告写盘失败 → rc=2（无法判定），不吐 traceback，也绝不返回 0/1。

    2026-10-28 评审复现的失败形态：写盘抛异常 → traceback → rc=1，
    但报告文件并不存在。下游（修复步 / 通知步）会把「读不到」当成「没有缺口」，
    最后发出一条绿色心跳。这里把写盘失败提升为 rc=2，与「日历不可用/库读不了」同级。
    """
    path, conn = db
    for d in ("2026-10-23", "2026-10-26", "2026-10-27"):
        _seed_full_day(conn, d)
    out = tmp_path / "no-such-dir" / "audit.json"      # 父目录不存在 → 写盘失败

    rc = audit_mod.main(["--db", path, "--today", "2026-10-27",
                         "--window-days", "4", "--out", str(out)])

    assert rc == 2
    assert not out.exists()
    captured = capsys.readouterr()
    assert "::error::" in captured.err
    assert "Traceback" not in captured.err + captured.out
    # 报告没写出来就不能先打印「✅ 三层无缺口」——日志不能骗人
    assert "三层无缺口" not in captured.out


def test_cli_write_failure_does_not_print_clean_banner(db, tmp_path, capsys):
    """有缺口但写盘失败时，也不能先吐「❌ 发现 N 处缺口」让人以为体检完成了。"""
    path, conn = db
    for d in ("2026-10-23", "2026-10-26", "2026-10-27"):
        _seed_full_day(conn, d)
    conn.execute("DELETE FROM daily_picks WHERE date='2026-10-26'")
    conn.commit()
    out = tmp_path / "no-such-dir" / "audit.json"

    rc = audit_mod.main(["--db", path, "--today", "2026-10-27",
                         "--window-days", "4", "--out", str(out)])

    assert rc == 2
    captured = capsys.readouterr()
    assert "❌ 发现" not in captured.out
    assert "体检区间" not in captured.out
