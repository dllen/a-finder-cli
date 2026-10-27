#!/usr/bin/env python3
"""按体检结果补齐缺口：L1 行情（仅近 7 天）/ L2 选股 / L3 计划。

原则：
  - 只补「缺失的日期」，绝不重算已有日期（当天产物不动）
  - L1 超过 MAX_PRICE_DAYS 天只报警不自动修（复权口径可能已变，填历史会污染序列）；
    跳过的缺口打 ::warning:: 并写进 --out 的 skipped，绝不静默成功
  - 某项失败不影响其它项（继续修下一项）；执行器抛异常也只算该项失败
  - 7 天窗口的基准日取**体检报告的 today**，不是执行时刻：跨零点、手工重放、
    排障用旧报告时若按 wall clock 算，本应修的行-情缺口会被静默跳过

退出码：0 = 无需修复或全部成功；1 = 有修复动作失败；
        2 = 输入不可用（报告缺失/坏 JSON/缺字段、库不存在、日历不可用）。
与 audit_coverage 的 0/1/2 对齐，CI 才能区分「坏输入」与「修复失败」。

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
import shlex
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from audit_coverage import CalendarUnavailable, Report, audit  # noqa: E402

MAX_PRICE_DAYS = 7      # 行情缺口自动修复的时间窗（天）
TOP_PICKS = 20
COMMAND_TIMEOUT_S = 1800    # 单条 CLI 命令上限；卡死按失败处理，别吃满 CI 预算

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


def _horizon(today: dt.date, max_price_days: int) -> str:
    return (today - dt.timedelta(days=max_price_days)).isoformat()


def _base_date(report: Report, today: Optional[dt.date]) -> dt.date:
    """窗口基准日：显式 --today 优先，否则用报告的 today（体检时刻）。"""
    return today or dt.date.fromisoformat(report.today)


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
    # 它按 --since 重算**所有** >= since 的日期（不只缺失日）：数据层靠 upsert 幂等
    # （ON CONFLICT(plan_date, portfolio, code, action, params_hash)），重复写只是更新；
    # 之所以从 min(缺口日) 重放，是因为 paper-trade 的持仓/资金曲线需要从缺口日起
    # 按库内已有状态往后推进 —— 缺口日之前的 carryover 不重放。
    cmds.append(
        _py(str(ROOT / "stock_cli.py"), "plan", "build-all",
            "--backfill", "--since", min(dates), "--db", db_path)
    )
    return RepairAction(layer="L3", dates=tuple(dates), commands=tuple(cmds),
                        reason=f"补算 {len(dates)} 天的交易计划（含各策略轮次）")


def plan_repairs(
    report: Report,
    *,
    db_path: str,
    today: Optional[dt.date] = None,
    max_price_days: int = MAX_PRICE_DAYS,
) -> List[RepairAction]:
    """把体检报告翻译成修复动作（不执行）。

    `today` 缺省时用 `report.today`（体检日），**不是** `date.today()`。
    """
    today = _base_date(report, today)
    horizon = _horizon(today, max_price_days)

    by_layer = report.by_layer()

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


def skipped_repairs(
    report: Report,
    *,
    today: Optional[dt.date] = None,
    max_price_days: int = MAX_PRICE_DAYS,
) -> List[Dict[str, str]]:
    """plan_repairs 有意不修的缺口（目前只有超出时间窗的 L1：只报警）。

    单独暴露是为了「报告里有缺口却没动作」时仍能说明原因，不静默返回成功。
    """
    today = _base_date(report, today)
    horizon = _horizon(today, max_price_days)
    return [
        {"layer": g.layer, "date": g.date, "detail": g.detail,
         "reason": f"行情缺口超出 {max_price_days} 天自动修复窗（复权口径风险），只报警"}
        for g in report.gaps
        if g.layer == "L1" and g.date < horizon
    ]


def _default_runner(cmd: Sequence[str]) -> Tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=COMMAND_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return 124, f"命令超时（>{COMMAND_TIMEOUT_S}s）: {shlex.join(cmd)}"
    tail = (proc.stdout or "")[-1500:] + (proc.stderr or "")[-1500:]
    return proc.returncode, tail


def run_repairs(
    actions: Sequence[RepairAction],
    *,
    runner: Runner = _default_runner,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
) -> List[Outcome]:
    """依次执行修复动作；某条命令失败（含执行器抛异常）只中断该 action。

    后面的 action 照常执行：一个层坏了不该连累其它层。
    """
    outcomes: List[Outcome] = []
    for action in actions:
        if dry_run:
            outcomes.append(Outcome(action=action, ok=True, output="[dry-run] 未执行"))
            continue
        ok = True
        output = ""
        for cmd in action.commands:
            log(f"$ {shlex.join(cmd)}")
            try:
                rc, out = runner(cmd)
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:      # noqa: BLE001 —— 执行器异常 = 这条命令失败
                ok = False
                output += f"执行器异常: {exc!r}\n"
                log(f"  ✗ {exc!r}")
                break
            output += out
            if rc != 0:
                ok = False
                log(f"  ✗ 退出码 {rc}")
                break
            log("  ✓")
        outcomes.append(Outcome(action=action, ok=ok, output=output))
    return outcomes


def _load_report(path: str) -> Report:
    return Report.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def _write_json(path: str, payload: Dict) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv=None, *, runner: Runner = _default_runner) -> int:
    ap = argparse.ArgumentParser(description="按体检报告补齐数据缺口")
    ap.add_argument("--db", default="hs300.db")
    ap.add_argument("--report", default=None, help="audit_coverage.py --out 生成的 JSON")
    ap.add_argument("--today", default=None,
                    help="基准日 YYYY-MM-DD（默认取报告的 today，无报告时用今天）")
    ap.add_argument("--max-price-days", type=int, default=MAX_PRICE_DAYS)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default=None, help="把修复结果写入该 JSON 文件")
    args = ap.parse_args(argv)

    try:
        today_override = dt.date.fromisoformat(args.today) if args.today else None
    except ValueError:
        print(f"::error::--today 不是合法日期: {args.today}", file=sys.stderr)
        return 2

    if not Path(args.db).exists():
        print(f"::error::数据库不存在: {args.db}", file=sys.stderr)
        return 2

    # 没有报告文件时自己跑一次体检（便于手工使用）
    if args.report:
        try:
            report = _load_report(args.report)
        except KeyError as exc:
            print(f"::error::体检报告缺字段: {exc}", file=sys.stderr)
            return 2
        except (OSError, ValueError, TypeError) as exc:
            print(f"::error::读取体检报告失败: {exc}", file=sys.stderr)
            return 2
    else:
        try:
            report = audit(args.db, today=today_override)
        except CalendarUnavailable as exc:
            print(f"::error::{exc}", file=sys.stderr)
            return 2
        except (FileNotFoundError, sqlite3.Error) as exc:
            print(f"::error::读取数据库失败: {exc}", file=sys.stderr)
            return 2

    try:
        today = _base_date(report, today_override)
    except ValueError:
        print(f"::error::报告里的 today 不合法: {report.today!r}", file=sys.stderr)
        return 2

    actions = plan_repairs(
        report, db_path=args.db, today=today,
        max_price_days=args.max_price_days,
    )
    skipped = skipped_repairs(
        report, today=today, max_price_days=args.max_price_days,
    )
    for s in skipped:
        print(f"::warning::跳过 [{s['layer']}] {s['date']} — {s['reason']}"
              f"（{s['detail']}）", file=sys.stderr)

    if actions:
        print(f"计划修复 {len(actions)} 组：")
        for a in actions:
            print(f"  [{a.layer}] {a.reason}")
    else:
        print("无需修复")

    outcomes = run_repairs(actions, runner=runner, dry_run=args.dry_run)

    payload = {
        "today": today.isoformat(),
        "skipped": skipped,
        "actions": [
            {"layer": a.layer, "dates": list(a.dates), "reason": a.reason}
            for a in actions
        ],
        "outcomes": [
            {"layer": o.action.layer, "dates": list(o.action.dates),
             "ok": o.ok, "reason": o.action.reason,
             "output": o.output[-4000:]}
            for o in outcomes
        ],
    }
    if args.out:
        try:
            _write_json(args.out, payload)
        except OSError as exc:
            print(f"::error::写入 {args.out} 失败: {exc}", file=sys.stderr)
            return 2

    failed = [o for o in outcomes if not o.ok]
    if failed:
        for o in failed:
            print(f"::error::修复失败 [{o.action.layer}] {o.action.reason}",
                  file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
