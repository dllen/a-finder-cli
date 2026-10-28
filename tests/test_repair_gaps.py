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


def test_l1_gap_cascades_to_picks_and_plans():
    """L1 缺 ⇒ 该日选股/计划必然也缺（没行情算不出选股），体检因抑制不报它们。

    若只补行情，复检会立刻报 L2/L3 缺、单轮不收敛 —— 正是「整天缺失」的主场景
    （2026-10-26 那种）。所以 L1 动作必须连带补 L2/L3。
    """
    actions = repair_mod.plan_repairs(
        _report([("2026-10-26", "L1", "行情只有 0 只")]),
        db_path="hs300.db", today=TODAY,
    )
    assert len(actions) == 1
    a = actions[0]
    assert a.layer == "L1"
    cmds = a.commands
    # 1) 先补行情
    assert _has(cmds[0], "sync-hs300-range")
    assert "--gap-fill" in cmds[0]
    # 2) 再补选股
    assert any(_has(c, "pick_history.py") and "2026-10-26" in c for c in cmds)
    # 3) 再补计划（default 档 + 全档位重放）
    assert any("build" in c and "2026-10-26" in c for c in cmds)
    assert any("build-all" in c and "--backfill" in c for c in cmds)
    # 顺序：行情在最前（选股依赖行情）
    assert _has(cmds[0], "sync-hs300-range")


def test_l1_cascade_uses_same_dates_only():
    """连带补的日期只覆盖 L1 缺口日期，不要牵连其它日期。"""
    actions = repair_mod.plan_repairs(
        _report([("2026-10-26", "L1", "行情只有 0 只")]),
        db_path="hs300.db", today=TODAY,
    )
    cmds = actions[0].commands
    for c in cmds:
        if _has(c, "pick_history.py") or "build" in c:
            assert "2026-10-26" in c or "--since" in c


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
    cmds = actions[0].commands
    # 每个日期一条 pick_history + 一条 plan build（default 档），全档位重放只一条
    assert sum(1 for c in cmds if _has(c, "pick_history.py")) == 2
    assert sum(1 for c in cmds if "build" in c and "--date" in c) == 2
    assert sum(1 for c in cmds if _has(c, "build-all")) == 1
    last = cmds[-1]
    assert _has(last, "build-all") and "2026-10-26" in last


def test_l2_gap_also_plans_followup_l3_commands():
    """F1 回归：选股缺失必然意味着计划缺失，L2 动作必须自带 L3 命令。

    否则 audit → repair → 复检 会停在 L3 缺口上，要跑第二轮才收敛
    （Task 7 e2e：10-26 删 daily_picks + trade_plan，修复后复检报
    「缺档位 5W..default」）。
    """
    actions = repair_mod.plan_repairs(
        _report([("2026-10-26", "L2", "无选股数据")]),
        db_path="hs300.db", today=TODAY,
    )
    assert [a.layer for a in actions] == ["L2"]     # 不需要单独的 L3 动作
    cmds = actions[0].commands
    assert len(cmds) == 3
    assert _has(cmds[0], "pick_history.py")         # 1) 先补选股
    assert "build" in cmds[1] and "--date" in cmds[1]   # 2) 补 default 档
    assert _has(cmds[2], "build-all") and "--backfill" in cmds[2]


def test_l2_and_l3_gaps_share_one_plan_replay():
    """L2 与 L3 缺口同时存在：一个 action 内含三类命令，且命令不重复。"""
    actions = repair_mod.plan_repairs(
        _report([("2026-10-26", "L2", "无选股数据"),
                 ("2026-10-23", "L3", "缺档位 5W")]),
        db_path="hs300.db", today=TODAY,
    )
    assert [a.layer for a in actions] == ["L2", "L3"]
    l2, l3 = actions

    # 三类命令都在 L2 动作里：补选股 → 补 default 档 → 全档位重放
    assert _has(l2.commands[0], "pick_history.py")
    assert "build" in l2.commands[1] and "--date" in l2.commands[1]
    last = l2.commands[-1]
    assert _has(last, "build-all") and "--backfill" in last
    # since 取并集最早日：只按 L2 的 10-26 重放会漏掉更早的 L3 缺口
    assert "--since" in last and "2026-10-23" in last

    # 不重复生成：build-all 全局只一条，L3 动作只补 default 档
    assert sum(1 for a in actions for c in a.commands if _has(c, "build-all")) == 1
    assert l3.commands and all("build" in c and "--date" in c for c in l3.commands)


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
    assert outcomes[0].ok is False          # L2 失败（第一条 pick_history 就挂）
    assert outcomes[1].ok is True           # L3 仍执行（不中断整体）
    # L2 动作首条命令失败即中断（1 条），L3 动作只有 default 档（1 条）
    assert len(calls) == 2


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
    assert len(calls) == 2                  # L2 首条即抛；L3 只有 default 档


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


# ---------------------------------------------------------------- I-2 副作用


def _open_position(db, code="600519", date="2026-10-26"):
    """模拟 `plan build`（paper_trade=True）写组合状态的两张表。"""
    import sqlite3
    conn = sqlite3.connect(db)
    conn.execute(
        "INSERT INTO open_positions (code, entry_date, entry_price, size_pct,"
        " stop_price, tp_price, status) VALUES (?,?,?,?,?,?,?)",
        (code, date, 10.0, 0.1, 9.0, 12.0, "open"))
    conn.execute(
        "INSERT INTO trade_events (plan_date, code, event_type, price,"
        " size_pct, pnl_pct, note, created_at) VALUES (?,?,?,?,?,?,?,?)",
        (date, code, "open", 10.0, 0.1, 0.0, "", "2026-10-26T15:00:00"))
    conn.commit()
    conn.close()


def test_cli_records_side_effects_of_plan_replay(capsys, tmp_path):
    """L3 重放会改写组合状态 → 必须把行数变化写进 --out 的 side_effects。

    实测（2026-10-28 评审）：补 10-26 后 open_positions 535→542、
    trade_events 1048→1055；export 后 10W 的 holdings.count 7→8、
    size_total 0.8313→0.95、cash_remaining −2285→−12315。
    这些变化会被巡检自己的 export+deploy 推到 gh-pages —— 用户在组合页看到的
    数字会在夜里自己变。不记录就等于没人知道。
    """
    db = _make_db(tmp_path)
    rep = _write_report(tmp_path, "2026-10-27", [
        {"date": "2026-10-26", "layer": "L3", "detail": "缺档位 5W"}])
    out = tmp_path / "repair.json"
    calls = {"n": 0}

    def fake_runner(cmd):
        calls["n"] += 1
        if calls["n"] == 1:              # 第一条 `plan build` 建了一仓
            _open_position(db)
        return (0, "ok")

    rc = repair_mod.main(["--db", db, "--report", rep, "--out", str(out)],
                         runner=fake_runner)
    assert rc == 0

    payload = json.loads(out.read_text(encoding="utf-8"))
    se = payload["side_effects"]
    assert se["open_positions"] == {"before": 0, "after": 1, "delta": 1}
    assert se["trade_events"] == {"before": 0, "after": 1, "delta": 1}
    # 副作用不能只躺在 JSON 里：日志也要露出来
    assert "open_positions" in capsys.readouterr().out


def test_cli_side_effects_are_zero_when_nothing_changed(capsys, tmp_path):
    """没改写组合状态时 delta=0（不是缺字段），通知才知道要不要提醒。"""
    db = _make_db(tmp_path)
    rep = _write_report(tmp_path, "2026-10-27", [
        {"date": "2026-10-26", "layer": "L3", "detail": "缺档位 5W"}])
    out = tmp_path / "repair.json"

    rc = repair_mod.main(["--db", db, "--report", rep, "--out", str(out),
                          "--dry-run"])
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["side_effects"]["open_positions"]["delta"] == 0
    assert payload["side_effects"]["trade_events"]["delta"] == 0


def test_side_effect_counts_do_not_create_missing_db(tmp_path):
    """库不存在时不能顺手建一个空库出来（只读打开）。"""
    missing = tmp_path / "nope.db"
    counts = repair_mod._side_effect_counts(str(missing))
    assert counts == {"open_positions": None, "trade_events": None}
    assert not missing.exists()


def test_side_effect_diff_skips_unreadable_tables():
    """读不到的行数不猜：直接不报这张表。"""
    diff = repair_mod._side_effect_diff(
        {"open_positions": 3, "trade_events": None},
        {"open_positions": 5, "trade_events": None},
    )
    assert diff == {"open_positions": {"before": 3, "after": 5, "delta": 2}}
