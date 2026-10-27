#!/usr/bin/env python3
"""按体检结果补齐缺口：L1 行情（仅近 7 天）/ L2 选股 / L3 计划。

原则：
  - 只补「缺失的日期」，绝不重算已有日期（当天产物不动）
  - L1 超过 MAX_PRICE_DAYS 天只报警不自动修（复权口径可能已变，填历史会污染序列）
  - 某项失败不影响其它项（继续修下一项）

命令执行器通过参数注入，测试不真正跑 CLI。

用法：
    python3 scripts/repair_gaps.py --db hs300.db --report /tmp/audit.json --dry-run
    python3 scripts/repair_gaps.py --db hs300.db --report /tmp/audit.json
"""

# 注意：这里**不能**加 `from __future__ import annotations`。
# dataclasses 在字段注解是字符串时会去 sys.modules 里查本模块（校验 ClassVar/KW_ONLY），
# 而测试用 importlib exec_module 直接加载本文件、不注册 sys.modules，于是拿到 None 报
# AttributeError。本模块的注解在 3.11 下都能求值，无需该 future import。
import argparse
import datetime as dt
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

MAX_PRICE_DAYS = 7      # 行情缺口自动修复的时间窗（天）
TOP_PICKS = 20

Runner = Callable[[Sequence[str]], Tuple[int, str]]


@dataclass(frozen=True)
class RepairAction:
    layer: str
    dates: Tuple[str, ...]
    commands: Tuple[Tuple[str, ...], ...]
    reason: str


@dataclass
class Outcome:
    action: RepairAction
    ok: bool
    output: str = ""


def _py(*args: str) -> Tuple[str, ...]:
    return (sys.executable, *args)


def _plan_l1(dates: Sequence[str], db_path: str) -> Optional[RepairAction]:
    if not dates:
        return None
    lo, hi = min(dates), max(dates)
    return RepairAction(
        layer="L1",
        dates=tuple(dates),
        commands=(
            _py(str(ROOT / "stock_cli.py"), "sync-hs300-range",
                "--start", lo, "--end", hi, "--db", db_path,
                "--gap-fill", "--concurrency", "6", "--rate", "6", "--retries", "2"),
        ),
        reason=f"补抓 {lo}~{hi} 的行情缺口",
    )


def _plan_l2(dates: Sequence[str], db_path: str) -> Optional[RepairAction]:
    if not dates:
        return None
    cmds = tuple(
        _py(str(ROOT / "pick_history.py"), "--db", db_path,
            "--top", str(TOP_PICKS), "--date", d, "--no-sync")
        for d in dates
    )
    return RepairAction(layer="L2", dates=tuple(dates), commands=cmds,
                        reason=f"补算 {len(dates)} 天的选股")


def _plan_l3(dates: Sequence[str], db_path: str) -> Optional[RepairAction]:
    if not dates:
        return None
    cmds: List[Tuple[str, ...]] = [
        _py(str(ROOT / "stock_cli.py"), "plan", "build", "--db", db_path, "--date", d)
        for d in dates
    ]
    # build-all --backfill 会跑两轮（无策略 + 每个策略各一遍），与 a-finder.sh site
    # 的第 3/4 步同源，因此产出的 params_hash 形状与 CI 一致。
    cmds.append(
        _py(str(ROOT / "stock_cli.py"), "plan", "build-all",
            "--backfill", "--since", min(dates), "--db", db_path)
    )
    return RepairAction(layer="L3", dates=tuple(dates), commands=tuple(cmds),
                        reason=f"补算 {len(dates)} 天的交易计划（含各策略轮次）")


def plan_repairs(
    report,
    *,
    db_path: str,
    today: Optional[dt.date] = None,
    max_price_days: int = MAX_PRICE_DAYS,
) -> List[RepairAction]:
    """把体检报告翻译成修复动作（不执行）。"""
    today = today or dt.date.today()
    horizon = (today - dt.timedelta(days=max_price_days)).isoformat()

    by_layer: Dict[str, List[str]] = {}
    for g in report.gaps:
        by_layer.setdefault(g.layer, []).append(g.date)

    actions: List[RepairAction] = []

    l1 = sorted(d for d in by_layer.get("L1", []) if d >= horizon)
    act = _plan_l1(l1, db_path)
    if act:
        actions.append(act)

    l2 = sorted(by_layer.get("L2", []))
    act = _plan_l2(l2, db_path)
    if act:
        actions.append(act)

    l3 = sorted(by_layer.get("L3", []))
    act = _plan_l3(l3, db_path)
    if act:
        actions.append(act)

    return actions


def _default_runner(cmd: Sequence[str]) -> Tuple[int, str]:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    tail = (proc.stdout or "")[-1500:] + (proc.stderr or "")[-1500:]
    return proc.returncode, tail


def run_repairs(
    actions: Sequence[RepairAction],
    *,
    runner: Runner = _default_runner,
    dry_run: bool = False,
) -> List[Outcome]:
    """依次执行修复动作；单项失败不中断后续动作。"""
    outcomes: List[Outcome] = []
    for action in actions:
        if dry_run:
            outcomes.append(Outcome(action=action, ok=True, output="[dry-run] 未执行"))
            continue
        ok = True
        output = ""
        for cmd in action.commands:
            print(f"$ {' '.join(cmd)}")
            rc, out = runner(cmd)
            output += out
            if rc != 0:
                ok = False
                print(f"  ✗ 退出码 {rc}")
                break
            print("  ✓")
        outcomes.append(Outcome(action=action, ok=ok, output=output))
    return outcomes


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="按体检报告补齐数据缺口")
    ap.add_argument("--db", default="hs300.db")
    ap.add_argument("--report", default=None, help="audit_coverage.py --out 生成的 JSON")
    ap.add_argument("--today", default=None, help="基准日 YYYY-MM-DD（测试用）")
    ap.add_argument("--max-price-days", type=int, default=MAX_PRICE_DAYS)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default=None, help="把修复结果写入该 JSON 文件")
    args = ap.parse_args(argv)

    today = dt.date.fromisoformat(args.today) if args.today else None

    # 没有报告文件时自己跑一次体检（便于手工使用）
    if args.report:
        payload = json.loads(Path(args.report).read_text(encoding="utf-8"))
        from audit_coverage import Gap, Report

        report = Report(
            today=payload["today"],
            window_days=payload["window_days"],
            expected_dates=payload.get("expected_dates", []),
            gaps=[Gap(**g) for g in payload.get("gaps", [])],
            suppressed=[Gap(**g) for g in payload.get("suppressed", [])],
        )
    else:
        from audit_coverage import audit

        report = audit(args.db, today=today)

    actions = plan_repairs(
        report, db_path=args.db, today=today,
        max_price_days=args.max_price_days,
    )

    if not actions:
        print("无需修复")
        result = {"actions": [], "outcomes": []}
        if args.out:
            Path(args.out).write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        return 0

    print(f"计划修复 {len(actions)} 组：")
    for a in actions:
        print(f"  [{a.layer}] {a.reason}")

    outcomes = run_repairs(actions, dry_run=args.dry_run)

    payload = {
        "actions": [
            {"layer": a.layer, "dates": list(a.dates), "reason": a.reason}
            for a in actions
        ],
        "outcomes": [
            {"layer": o.action.layer, "dates": list(o.action.dates),
             "ok": o.ok, "reason": o.action.reason}
            for o in outcomes
        ],
    }
    if args.out:
        Path(args.out).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    failed = [o for o in outcomes if not o.ok]
    if failed:
        for o in failed:
            print(f"::error::修复失败 [{o.action.layer}] {o.action.reason}",
                  file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
