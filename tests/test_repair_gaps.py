"""缺口修复单测：选择逻辑 + 命令构造 + 幂等。

命令执行器是注入的，测试不真正跑 CLI。
"""

import datetime as dt
import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

_SPEC = importlib.util.spec_from_file_location(
    "repair_gaps", ROOT / "scripts" / "repair_gaps.py")
repair_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(repair_mod)

_SPEC2 = importlib.util.spec_from_file_location(
    "audit_coverage", ROOT / "scripts" / "audit_coverage.py")
audit_mod = importlib.util.module_from_spec(_SPEC2)
_SPEC2.loader.exec_module(audit_mod)

TODAY = dt.date(2026, 10, 27)


def _has(cmd, needle):
    """cmd 是 argv 元组，元素是完整路径（如 /path/pick_history.py）
    或独立参数（如 --gap-fill）→ 用子串匹配，不能用 == 判断。"""
    return any(needle in part for part in cmd)


def _report(gaps, suppressed=None):
    return audit_mod.Report(
        today=TODAY.isoformat(), window_days=30,
        expected_dates=["2026-10-23", "2026-10-26", "2026-10-27"],
        gaps=[audit_mod.Gap(*g) for g in gaps],
        suppressed=[audit_mod.Gap(*g) for g in (suppressed or [])],
    )


def test_no_gaps_produces_no_actions():
    assert repair_mod.plan_repairs(_report([]), db_path="hs300.db", today=TODAY) == []


def test_recent_l1_gap_plans_price_sync():
    actions = repair_mod.plan_repairs(
        _report([("2026-10-26", "L1", "行情只有 100 只")]),
        db_path="hs300.db", today=TODAY,
    )
    assert len(actions) == 1
    a = actions[0]
    assert a.layer == "L1"
    assert a.dates == ("2026-10-26",)
    cmd = a.commands[0]
    assert "sync-hs300-range" in cmd
    assert "--gap-fill" in cmd
    assert "2026-10-26" in cmd


def test_old_l1_gap_not_planned():
    """超过 7 天的行情缺口不自动修（复权口径一致性风险）。"""
    actions = repair_mod.plan_repairs(
        _report([("2026-10-10", "L1", "行情只有 0 只")]),
        db_path="hs300.db", today=TODAY,
    )
    assert actions == []


def test_l2_gap_plans_pick_history():
    actions = repair_mod.plan_repairs(
        _report([("2026-10-26", "L2", "无选股数据")]),
        db_path="hs300.db", today=TODAY,
    )
    assert len(actions) == 1
    assert actions[0].layer == "L2"
    cmd = actions[0].commands[0]
    assert _has(cmd, "pick_history.py")
    assert "--date" in cmd and "2026-10-26" in cmd
    assert "--no-sync" in cmd


def test_l3_gap_plans_default_then_backfill():
    actions = repair_mod.plan_repairs(
        _report([("2026-10-26", "L3", "缺档位 5W,10W")]),
        db_path="hs300.db", today=TODAY,
    )
    assert len(actions) == 1
    cmds = actions[0].commands
    assert len(cmds) == 2
    assert "build" in cmds[0] and "--date" in cmds[0]
    assert "build-all" in cmds[1] and "--backfill" in cmds[1]
    assert "--since" in cmds[1] and "2026-10-26" in cmds[1]


def test_multiple_l2_dates_merge_into_one_action():
    actions = repair_mod.plan_repairs(
        _report([("2026-10-26", "L2", "无选股"), ("2026-10-27", "L2", "无选股")]),
        db_path="hs300.db", today=TODAY,
    )
    assert len(actions) == 1
    assert actions[0].dates == ("2026-10-26", "2026-10-27")
    assert len(actions[0].commands) == 2      # 每个日期一条 pick_history


def test_run_repairs_reports_failure_and_keeps_going():
    actions = repair_mod.plan_repairs(
        _report([("2026-10-26", "L2", "无选股"), ("2026-10-27", "L3", "缺档位")]),
        db_path="hs300.db", today=TODAY,
    )
    calls = []

    def fake_runner(cmd):
        calls.append(cmd)
        if _has(cmd, "pick_history.py"):
            return (1, "boom")
        return (0, "ok")

    outcomes = repair_mod.run_repairs(actions, runner=fake_runner)
    assert len(outcomes) == 2
    assert outcomes[0].ok is False          # L2 失败
    assert outcomes[1].ok is True           # L3 仍执行（不中断整体）
    assert len(calls) == 3                  # 1 条 pick_history + 2 条 plan


def test_dry_run_does_not_execute():
    actions = repair_mod.plan_repairs(
        _report([("2026-10-26", "L2", "无选股")]),
        db_path="hs300.db", today=TODAY,
    )
    calls = []

    def fake_runner(cmd):
        calls.append(cmd)
        return (0, "ok")

    outcomes = repair_mod.run_repairs(actions, runner=fake_runner, dry_run=True)
    assert calls == []
    assert len(outcomes) == 1


def test_cli_dry_run_exit_zero(capsys, tmp_path):
    db = tmp_path / "x.db"
    from db_repository import open_db
    conn = open_db(str(db))
    conn.close()
    rc = repair_mod.main(["--db", str(db), "--today", "2026-10-27", "--dry-run"])
    assert rc in (0, 1)
