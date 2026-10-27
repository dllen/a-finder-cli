"""缺口修复单测：选择逻辑 + 命令构造 + 退出码 + 报告还原。

命令执行器是注入的，测试不真正跑 CLI。
"""

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

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


def _make_db(tmp_path, name="x.db"):
    from db_repository import open_db
    db = tmp_path / name
    conn = open_db(str(db))
    conn.close()
    return str(db)


def _write_report(tmp_path, today, gaps, *, name="audit.json"):
    path = tmp_path / name
    path.write_text(json.dumps({
        "today": today,
        "window_days": 30,
        "expected_dates": [g["date"] for g in gaps],
        "gaps": gaps,
        "suppressed": [],
    }, ensure_ascii=False), encoding="utf-8")
    return str(path)


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
    report = _report([("2026-10-10", "L1", "行情只有 0 只")])
    assert repair_mod.plan_repairs(report, db_path="hs300.db", today=TODAY) == []
    # 但绝不静默：这些缺口要能说清为什么没修
    skipped = repair_mod.skipped_repairs(report, today=TODAY)
    assert [(s["layer"], s["date"]) for s in skipped] == [("L1", "2026-10-10")]


def test_price_window_follows_report_today_not_wall_clock():
    """7 天窗口以报告的 today（体检日）为基准，不是执行时刻。"""
    report = _report([("2026-10-18", "L1", "行情只有 0 只")])
    # 报告 today = 2026-10-27 → 10-18 已超出窗口
    assert repair_mod.plan_repairs(report, db_path="hs300.db") == []
    # 同一份缺口，体检日是 10-20 → 相对只隔 2 天，应当修
    report.today = "2026-10-20"
    actions = repair_mod.plan_repairs(report, db_path="hs300.db")
    assert [a.layer for a in actions] == ["L1"]
    assert actions[0].dates == ("2026-10-18",)
    # 显式 --today 优先于报告
    assert repair_mod.plan_repairs(report, db_path="hs300.db", today=TODAY) == []


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


def test_runner_exception_marks_action_failed_and_keeps_going():
    """执行器抛异常只算当前 action 失败，不能穿出 run_repairs 连累后面的层。"""
    actions = repair_mod.plan_repairs(
        _report([("2026-10-26", "L2", "无选股"), ("2026-10-27", "L3", "缺档位")]),
        db_path="hs300.db", today=TODAY,
    )
    calls = []

    def boom(cmd):
        calls.append(cmd)
        if _has(cmd, "pick_history.py"):
            raise OSError("python 不见了")
        return (0, "ok")

    outcomes = repair_mod.run_repairs(actions, runner=boom)
    assert len(outcomes) == 2
    assert outcomes[0].ok is False
    assert "python 不见了" in outcomes[0].output
    assert outcomes[1].ok is True           # L3 照跑
    assert len(calls) == 3                  # 1 条 pick_history + 2 条 plan


def test_dry_run_does_not_execute():
    actions = repair_mod.plan_repairs(
        _report([("2026-10-26", "L2", "无选股")]),
        db_path="hs300.db", today=TODAY,
    )
    calls = []
    lines = []

    def fake_runner(cmd):
        calls.append(cmd)
        return (0, "ok")

    outcomes = repair_mod.run_repairs(actions, runner=fake_runner, dry_run=True,
                                      log=lines.append)
    assert calls == []
    assert lines == []                      # dry-run 也不该刷执行日志
    assert len(outcomes) == 1


def test_report_from_dict_roundtrip():
    report = _report([("2026-10-26", "L1", "行情只有 100 只")],
                     suppressed=[("2026-10-26", "L2", "派生")])
    payload = report.to_dict()
    payload["future_field"] = "other"       # 报告多出字段不该炸
    again = audit_mod.Report.from_dict(payload)
    assert again.today == report.today
    assert again.window_days == report.window_days
    assert again.expected_dates == report.expected_dates
    assert again.gaps == report.gaps
    assert again.suppressed == report.suppressed


def test_cli_dry_run_exit_zero(capsys, tmp_path):
    """空库 → 1 组 L1 动作 → dry-run 一定 rc=0。"""
    db = _make_db(tmp_path)
    out = tmp_path / "deep" / "dir" / "repair.json"   # 父目录按需创建
    rc = repair_mod.main(["--db", db, "--today", "2026-10-27", "--dry-run",
                          "--out", str(out)])
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert [a["layer"] for a in payload["actions"]] == ["L1"]
    # 30 天窗口里更早的 L1 缺口只报警（不出现在 actions，但要出现在 skipped）
    assert payload["skipped"]
    assert all(s["layer"] == "L1" and s["date"] < "2026-10-20"
               for s in payload["skipped"])


def test_cli_report_path_uses_report_today(capsys, tmp_path):
    """--report 的 today 是窗口基准：旧报告也能修（不是按执行时刻算）。"""
    db = _make_db(tmp_path)
    rep = _write_report(tmp_path, "2026-10-20", [
        {"date": "2026-10-18", "layer": "L1", "detail": "行情只有 0 只"}])
    out = tmp_path / "repair.json"
    rc = repair_mod.main(["--db", db, "--report", rep, "--dry-run", "--out", str(out)])
    assert rc == 0
    payload = json.loads(Path(out).read_text(encoding="utf-8"))
    assert [a["layer"] for a in payload["actions"]] == ["L1"]
    assert payload["actions"][0]["dates"] == ["2026-10-18"]
    assert payload["today"] == "2026-10-20"


def test_cli_reports_skipped_old_l1(capsys, tmp_path):
    """超窗缺口不修，但必须留痕：::warning:: + --out.skipped，不能静默成功。"""
    db = _make_db(tmp_path)
    rep = _write_report(tmp_path, "2026-10-27", [
        {"date": "2026-10-10", "layer": "L1", "detail": "行情只有 0 只"}])
    out = tmp_path / "repair.json"
    rc = repair_mod.main(["--db", db, "--report", rep, "--dry-run", "--out", str(out)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "无需修复" in captured.out
    assert "::warning::" in captured.err and "2026-10-10" in captured.err
    payload = json.loads(Path(out).read_text(encoding="utf-8"))
    assert payload["actions"] == []
    assert [s["date"] for s in payload["skipped"]] == ["2026-10-10"]


def test_cli_repair_failure_exit_one(capsys, tmp_path):
    """rc=1 专指修复真的失败（runner 可注入才测得出来）。"""
    db = _make_db(tmp_path)
    rep = _write_report(tmp_path, "2026-10-27", [
        {"date": "2026-10-27", "layer": "L2", "detail": "无选股数据"}])
    out = tmp_path / "repair.json"
    calls = []

    def fake_runner(cmd):
        calls.append(cmd)
        return (1, "boom")

    rc = repair_mod.main(["--db", db, "--report", rep, "--out", str(out)],
                         runner=fake_runner)
    assert rc == 1
    assert calls and _has(calls[0], "pick_history.py")
    payload = json.loads(Path(out).read_text(encoding="utf-8"))
    assert payload["outcomes"][0]["ok"] is False
    assert "boom" in payload["outcomes"][0]["output"]     # 失败原因不丢
    assert "::error::" in capsys.readouterr().err


def test_cli_bad_inputs_exit_two(capsys, tmp_path):
    """坏输入 = rc 2（与「修复失败」的 1 区分），且不吐 traceback。"""
    db = _make_db(tmp_path)
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not json", encoding="utf-8")
    no_window = tmp_path / "no-window.json"
    no_window.write_text(json.dumps({"today": "2026-10-27", "gaps": []}),
                         encoding="utf-8")
    cases = [
        ["--db", db, "--report", str(tmp_path / "nope.json")],   # 报告不存在
        ["--db", db, "--report", str(bad_json)],                 # 报告不是 JSON
        ["--db", db, "--report", str(no_window)],                # 报告缺字段
        ["--db", str(tmp_path / "nope.db")],                     # 库不存在
        ["--db", db, "--today", "2026-13-01"],                   # 日期不合法
    ]
    for argv in cases:
        capsys.readouterr()
        assert repair_mod.main(argv) == 2, argv
        captured = capsys.readouterr()
        assert "::error::" in captured.err, argv
        assert "Traceback" not in captured.err + captured.out, argv
