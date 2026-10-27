#!/usr/bin/env python3
"""巡检报告格式化 + 通知推送。

三种结局：✅ 通过（每日心跳）/ 🛠 已自动修复 / 🚨 需人工。
通知通道分级降级：ntfy（若配了 NTFY_TOPIC）→ GitHub Step Summary（总是写）。

为什么要有心跳：静默失败最难排查。2026-10-26 站点被清空、10-27 cron 未触发，
都是靠人发现的。有了心跳，「今天没收到消息」本身就是信号。

输入契约（与 audit_coverage / repair_gaps 的 --out 对齐）：
  --audit      : {"today": "YYYY-MM-DD", "window_days": 30,
                  "gaps": [{"date","layer","detail"}], ...}
                 Task 6 有复检报告（audit2.json）时必须传复检报告：
                 gaps 为空 + 有成功 outcome 也能判 🛠，不必非得给 --audit-pre。
                 只传这一份而 gaps 仍非空时一律按「剩余」处理（保守，不凭退出码断言修好）。
  --audit-pre  : 可选，修复前的原报告（同为 gaps 形状）。两者都给时，
                 用「原 gaps - 复检 gaps」判「已修复」，剩余仍以 --audit 为准；
                 复检 expected_dates 没覆盖到的原缺口不会被断言已修复。
  --repair     : {"skipped": [...], "outcomes": [{"layer","dates","ok","reason"}], ...}
`outcomes[].ok is True` 才算修复成功（`"false"` 这类真值字符串不算）；
`reason` 非字符串先 str() 归一化 —— 坏类型只降级，不能炸掉整条通知。
`skipped`（超窗未修）与 failed outcome 只给缺口补原因。

「剩余缺口」只认报告的 gaps：**绝不用 ok outcome 的 dates 去减 gap**。
ok 只等于修复命令退出码为 0，不等于数据真的补齐（sync 写 0 行同样 rc=0）；
复检报告里的 gaps 按定义就是「现在仍缺」，任何减法都会造出「🛠 已修复」的假消息。

退出码：0 = 通知已产出（含「需人工」，那靠 🚨 措辞 + ::error:: 注释暴露，
        是否让 CI 变红由体检/修复步骤的退出码决定）；
        2 = 没有体检输入、体检报告不可用、--today 不合法。
        前两种「体检自己坏了」的场景仍然会写 Summary + 推一条 🚨，只是退出码非 0。
        `--repair` 缺失/损坏只降级为 ::warning:: —— 修复信息是补充，不能因此不发告警。

注意：本模块的推送**绝不能被单测真发出去**。测试要么不配 topic，要么
monkeypatch post_ntfy / urlopen。

用法：
    python3 scripts/notify_audit.py --audit /tmp/audit.json --repair /tmp/repair.json
    python3 scripts/notify_audit.py --no-db --today 2026-10-27
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional, Sequence

NTFY_BASE = "https://ntfy.sh"      # 测试里 monkeypatch 成回环地址
MAX_BODY = 300                      # 心跳消息的软上限（长了就没人看）


def decide_status(*, gaps, repaired, remaining, no_db: bool = False) -> str:
    """从「发现/已修/剩余」推出三种结局。

    no_db 优先：没有数据库 = 什么都没体检，绝不能报「通过」。
    remaining 次之：只要还有未修缺口就不能说通过或已修复。
    没有剩余时，只要「发现过缺口」（gaps 非空，有 --audit-pre 时来自原报告）
    或「修复动作确实成功过」（repaired 非空，即 ok is True 的 outcome），
    就是 repaired；两者都没有才是 pass。
    这样 Task 6 只传复检报告（gaps=[]）+ 修复成功时 🛠 仍可达，
    而 failed outcome 的「干净复检」不会被误判成 🛠。
    """
    if no_db:
        return "manual"
    if remaining:
        return "manual"
    if not gaps and not repaired:
        return "pass"
    return "repaired"


def _as_date(value: str) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _split_runs(dates: Sequence[str]) -> List[List[str]]:
    """把日期切成「真正连续」的段；不能解析的日期各自成段（不猜）。"""
    runs: List[List[str]] = []
    for d in sorted(set(dates)):
        if runs:
            prev = _as_date(runs[-1][-1])
            cur = _as_date(d)
            if prev is not None and cur is not None and cur - prev == dt.timedelta(days=1):
                runs[-1].append(d)
                continue
        runs.append([d])
    return runs


def _runs_text(dates: Sequence[str]) -> str:
    parts = []
    for run in _split_runs(dates):
        if len(run) == 1:
            parts.append(run[0])
        else:
            parts.append(f"{run[0]}~{run[-1]}（{len(run)} 天）")
    return "、".join(parts)


def _summarize(items: Sequence[Dict], key: str = "layer") -> str:
    """按层聚合日期，连续区间收敛成 a~b，避免逐日铺开。

    只并**真正连续**的日子：10-20、10-26 不能被写成 10-20~10-26（虚报跨度）。
    """
    by_layer: Dict[str, List[str]] = {}
    for it in items:
        by_layer.setdefault(it[key], []).append(it["date"])
    return "；".join(
        f"{layer} {_runs_text(by_layer[layer])}" for layer in sorted(by_layer)
    )


def _is_ok(item: Dict) -> bool:
    """只认真正的布尔 True：`ok: "false"` 这种真值字符串绝不能算修复成功。"""
    return item.get("ok") is True


def _reason(item: Dict) -> str:
    """reason 归一化成 str：`123` 之类会让 '；'.join 抛 TypeError，炸掉整条通知。"""
    raw = item.get("reason")
    if raw is None:
        return ""
    return raw if isinstance(raw, str) else str(raw)


def _reasons_text(items: Sequence[Dict], fallback: str) -> str:
    """把若干 reason 拼成一句；全为空时用 fallback，别渲染出「修复：；」。"""
    return "；".join(filter(None, (_reason(it) for it in items))) or fallback


def format_message(
    *,
    status: str,
    today: str,
    gaps: Sequence[Dict],
    repaired: Sequence[Dict],
    remaining: Sequence[Dict],
    no_db: bool = False,
    window_days: int = 30,
    fixed: Sequence[Dict] = (),
    verified: bool = False,
) -> tuple[str, str]:
    """返回 (标题, 正文)。

    gaps 是「发现」的缺口；有原/复检两份报告时，fixed 是两份 gaps 的差集，
    verified=True 表示「剩余」来自复检报告（而不是只凭修复命令退出码）。
    """
    if no_db:
        return (
            "🚨 需人工：巡检无法执行（没有数据库）",
            f"🚨 {today} 巡检无法执行：缓存里没有数据库（hs300.db）\n"
            f"常见原因：早班 deploy-site 从未成功跑过，或 Actions 缓存已过期\n"
            f"建议：手动跑 bash a-finder.sh fallback，或触发一次 deploy-site",
        )

    if status == "pass":
        title = "✅ 数据巡检通过"
        # 只宣称体检真正覆盖到的内容：pass 路径不做导出/部署，
        # “当天数据已发布”无法在这里核实。
        body = (f"✅ {today} 巡检通过｜近 {window_days} 天三层无缺口\n"
                f"（心跳：收到这条说明巡检链路活着）")
        return title, body

    if status == "repaired":
        title = "🛠 数据缺口已自动修复"
        # 有原报告时「发现」来自原报告；只传复检报告时退回复检里修好的日期，
        # 不能因为复检 gaps 为空就把「发现」渲染成「—」。
        found = _summarize(gaps) or _summarize(_outcome_gap_items(repaired)) or "—"
        result = ("复检确认这些缺口已消失" if verified
                  else "修复动作全部成功")
        body = (f"🛠 {today} 巡检发现缺口并已修复\n"
                f"发现：{found}\n"
                f"修复：{_reasons_text(repaired, '已自动补齐')}\n"
                f"结果：{result}")
        return title, body

    title = "🚨 数据缺口需人工处理"
    lines = [f"🚨 {today} 巡检发现无法自动修复的缺口",
             f"剩余：{_summarize(remaining) or '—'}"]
    if fixed:
        # 只有原/复检两份 gaps 的差集能证明「已修复」；ok outcome 只代表命令
        # 退出码为 0，在有剩余缺口时拿它写「已修复」会自相矛盾。
        lines.append(f"已修复：{_summarize(fixed)}")
    # skipped 项自带明确原因（例如行情超窗不自动修），直接透出比泛泛而谈有用；
    # 没有修复信息时回退 gap 的 detail，别让正文只剩「L1 2026-10-10」这种内部代码。
    notes: List[str] = []
    for item in remaining:
        reason = _reason(item)
        if reason:
            text = reason
        elif item.get("detail"):
            text = f"{item.get('layer')} {item.get('date')}：{item['detail']}"
        else:
            continue
        if text not in notes:
            notes.append(text)
    if notes:
        lines.append(f"原因：{'；'.join(notes)}")
    lines.append("建议：手动跑 bash a-finder.sh fallback 或检查数据源")
    return title, "\n".join(lines)


def write_step_summary(title: str, body: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"## {title}\n\n```\n{body}\n```\n\n")
    except OSError:
        pass


def post_ntfy(topic: str, title: str, body: str, *, priority: str = "default") -> bool:
    if not topic:
        return False
    req = urllib.request.Request(
        f"{NTFY_BASE}/{topic}",
        data=body.encode("utf-8"),
        headers={
            # 标题必须是 UTF-8 **字节**：urllib 对 str 头值走 latin-1 编码，
            # 含 emoji 的标题会抛 UnicodeEncodeError 而推送静默失败。
            "Title": title.encode("utf-8"),
            "Priority": priority,
            "Tags": "chart_with_upwards_trend",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20):
            return True
    except Exception as exc:  # noqa: BLE001 —— 推送失败只降级，不能拖垮巡检
        print(f"::warning::ntfy 推送失败: {exc}", file=sys.stderr)
        return False


# ------------------------------------------------------------------ CLI 输入


def _read_json(path: str, *, fatal: bool = True) -> Optional[Dict]:
    level = "error" if fatal else "warning"
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"::{level}::读取 {path} 失败: {exc}", file=sys.stderr)
        return None
    if not isinstance(payload, dict):
        print(f"::{level}::{path} 顶层不是 JSON 对象", file=sys.stderr)
        return None
    return payload


def _items(payload: Dict, key: str, path: str, *,
           fatal: bool = True) -> Optional[List[Dict]]:
    """取对象数组；形状不对返回 None（交给调用方决定报错还是降级）。

    注意不能用 `payload.get(key) or []`：`""` / `0` / `false` 会被悄悄塌成空数组，
    绕过形状校验 —— 那正是「假心跳」的方向。
    """
    level = "error" if fatal else "warning"
    raw = payload.get(key)
    items = [] if raw is None else raw
    if not isinstance(items, list) or not all(isinstance(i, dict) for i in items):
        print(f"::{level}::{path} 的 {key} 不是对象数组", file=sys.stderr)
        return None
    return items


def _has_date_layer(item: Dict) -> bool:
    return all(isinstance(item.get(k), str) and item[k] for k in ("date", "layer"))


def _load_repair(path: str) -> tuple[List[Dict], List[Dict]]:
    """读修复结果；读不到/形状不对就当「没有修复信息」（全部按未修报警）。"""
    payload = _read_json(path, fatal=False)
    if payload is None:
        return [], []
    outcomes = _items(payload, "outcomes", path, fatal=False)
    skipped = _items(payload, "skipped", path, fatal=False)
    if outcomes is None or skipped is None:
        return [], []
    if not all(_has_date_layer(s) for s in skipped):
        print(f"::warning::{path} 的 skipped 项缺少 date/layer，整份修复信息不采信",
              file=sys.stderr)
        return [], []
    return outcomes, skipped


def _load_pre_gaps(path: str) -> Optional[List[Dict]]:
    """读修复前的原报告，只用来算「发现 / 已修复」。

    读不到或形状不对就返回 None：剩余缺口仍由复检报告决定，
    原报告只是对照，丢了不该阻止告警，也不该编出「已修复」。
    """
    payload = _read_json(path, fatal=False)
    if payload is None:
        return None
    gaps = _items(payload, "gaps", path, fatal=False)
    if gaps is None:
        return None
    if not all(_has_date_layer(g) for g in gaps):
        print(f"::warning::{path} 的 gaps 项缺少 date/layer，忽略原报告对照",
              file=sys.stderr)
        return None
    return gaps


def _outcome_dates(outcome: Dict) -> List[str]:
    raw = outcome.get("dates")
    if not isinstance(raw, list):
        return []
    return [d for d in raw if isinstance(d, str)]


def _outcome_gap_items(outcomes: Sequence[Dict]) -> List[Dict]:
    """把 ok outcome 的 dates 摊成 gap 形状，仅用于「发现」文案的回退。"""
    items: List[Dict] = []
    for o in outcomes:
        layer = o.get("layer")
        if not isinstance(layer, str):
            continue
        for d in _outcome_dates(o):
            items.append({"date": d, "layer": layer})
    return items


def _remaining_gaps(
    gaps: Sequence[Dict],
    outcomes: Sequence[Dict],
    skipped: Sequence[Dict],
) -> List[Dict]:
    """仍未修好的缺口 —— 只认体检报告的 gaps，outcome/skipped 只补原因。

    绝不用 ok outcome 的 dates 去「减掉」gap：ok 只等于修复命令退出码为 0，
    不等于数据真的补齐（sync 写 0 行同样 rc=0）。复检报告里的 gaps 按定义
    就是「现在仍缺」，任何减法都会造出「🛠 已修复」的假消息。

    也绝不凭 outcome 的 dates 造新缺口：修复动作是「一个 action 覆盖多天」，
    第一条命令失败就 break，failed outcome 的 dates 里可能含前半段已修好的日期。
    """
    reasons: Dict[tuple, str] = {}
    for o in outcomes:
        if _is_ok(o):
            continue
        layer = o.get("layer")
        if not isinstance(layer, str):
            continue
        reason = _reason(o) or "修复命令失败"
        for d in _outcome_dates(o):
            reasons[(layer, d)] = reason
    # skipped 项自带明确原因（例如行情超窗不自动修），优先于泛泛的失败原因
    for s in skipped:
        reason = _reason(s)
        if reason:
            reasons[(s.get("layer"), s.get("date"))] = reason

    remaining: List[Dict] = []
    seen = set()
    for g in gaps:
        key = (g.get("layer"), g.get("date"))
        if key in seen:
            continue
        seen.add(key)
        item = dict(g)
        if key in reasons:
            item["reason"] = reasons[key]
        remaining.append(item)
    return remaining


def _covered_dates(payload: Dict) -> Optional[set]:
    """复检报告覆盖的日期（expected_dates）；没这个字段就不设限。"""
    raw = payload.get("expected_dates")
    if not isinstance(raw, list):
        return None
    dates = {d for d in raw if isinstance(d, str)}
    return dates or None


def _diff_gaps(discovered: Sequence[Dict], remaining: Sequence[Dict]) -> List[Dict]:
    """原报告有、复检报告没有的缺口 = 复检确认已经消失的缺口。"""
    remaining_keys = {(g.get("layer"), g.get("date")) for g in remaining}
    fixed: List[Dict] = []
    seen = set()
    for g in discovered:
        key = (g.get("layer"), g.get("date"))
        if key in remaining_keys or key in seen:
            continue
        seen.add(key)
        fixed.append(dict(g))
    return fixed


def _pick_today(args_today: Optional[str], payload: Optional[Dict] = None) -> Optional[str]:
    """规范化 --today；未给则取报告里的 today，再退回今天。

    `fromisoformat` 会接受 `20261027` / `2026-W43-2` 这类非规范写法，
    原样展示会和报告里的 YYYY-MM-DD 对不上，所以统一回写成 isoformat。
    """
    if args_today is not None:
        value = _as_date(args_today)
        if value is None:
            print(f"::error::--today 不是合法日期: {args_today}", file=sys.stderr)
            return None
        if payload:
            report_today = payload.get("today")
            if (isinstance(report_today, str) and _as_date(report_today)
                    and _as_date(report_today) != value):
                print(f"::warning::--today {value.isoformat()} 与体检报告的 today "
                      f"{report_today} 不一致", file=sys.stderr)
        return value.isoformat()
    value = (payload or {}).get("today")
    if isinstance(value, str) and _as_date(value):
        return _as_date(value).isoformat()
    fallback = dt.date.today().isoformat()
    if payload:
        # 报告可能是旧的，静默标成今天会让心跳骗人；至少留一条 warning。
        print(f"::warning::体检报告缺少合法 today（{value!r}），"
              f"回退系统日期 {fallback}", file=sys.stderr)
    return fallback


def _pick_window_days(args_window: Optional[int], payload: Dict) -> int:
    """心跳文案里的窗口天数：显式参数 > 报告自带 > 30。"""
    if args_window is not None and args_window > 0:
        return args_window
    if args_window is not None:
        # 0/负数静默落到报告值会让人以为参数生效了，明确说出来。
        print(f"::warning::--window-days 必须是正整数（收到 {args_window}），"
              f"改用报告值/30", file=sys.stderr)
    value = payload.get("window_days")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return 30


def _alarm_no_report(*, reason: str, today: str, topic: str,
                     annotate: bool = True) -> int:
    """体检自己坏了（没报告/报告读不了）：仍然要告警，不能只剩 Actions 变红。

    「没有体检输入」和「缓存里没有数据库」同待遇：都写 Summary + 推送，
    否则最需要告警的场景（体检 rc=2 没写出 --out）在通知层完全静默。
    annotate=False 用于调用方已经打过具体 `::error::` 的情况，避免一次失败
    在 GitHub 上出现两条红注释。
    退出码保持 2，让 CI 也能体现「无法判定」。
    """
    title = "🚨 巡检无法执行：体检报告不可用"
    body = (f"🚨 {today} 巡检无法执行：{reason}\n"
            f"没有体检结果就无法判断数据完整性，按「需人工」处理\n"
            f"建议：查看 daily-audit workflow 里体检步骤的日志与退出码，"
            f"必要时手动跑 bash a-finder.sh fallback")
    print(body)
    write_step_summary(title, body)
    # 原因已经在正文/日志里，这里只留一条红色注释，别把标题与原因重复堆在一起
    if annotate:
        print(f"::error::{title}", file=sys.stderr)
    if topic:
        post_ntfy(topic, title, body, priority="high")
    else:
        print("::notice::未配置 NTFY_TOPIC，仅写入 Step Summary")
    return 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="巡检报告通知")
    ap.add_argument("--audit", default=None,
                    help="体检 JSON（含 gaps）；有 --audit-pre 时这是复检报告")
    ap.add_argument("--audit-pre", default=None,
                    help="修复前的原体检 JSON（可选）；给出后用它算「发现/已修复」，"
                         "剩余仍以 --audit 为准")
    ap.add_argument("--repair", default=None, help="修复结果 JSON（含 outcomes）")
    ap.add_argument("--today", default=None)
    ap.add_argument("--topic", default=os.environ.get("NTFY_TOPIC", ""))
    ap.add_argument("--window-days", type=int, default=None,
                    help="体检窗口天数（仅用于心跳文案；默认取报告里的 window_days，"
                         "缺失用 30）")
    ap.add_argument("--no-db", action="store_true",
                    help="缓存里没有数据库（此时不能报通过）")
    args = ap.parse_args(argv)

    gaps: List[Dict] = []
    audit_payload: Dict = {}
    pre_gaps: Optional[List[Dict]] = None
    outcomes: List[Dict] = []
    skipped: List[Dict] = []

    # 只传了原报告时它同时也是「本次报告」：没有复检就按原报告判剩余（会更保守）
    audit_path = args.audit or args.audit_pre

    # no_db 时体检根本没跑：旧报告/修复结果都不可信，也不读
    # （缓存丢失场景下这些文件可能压根不存在，不能因此漏掉告警）
    if not args.no_db:
        # 非法 --today 连告警里的日期都不可信：不发任何消息，直接失败
        today_hint = _pick_today(args.today)
        if today_hint is None:
            return 2
        # 没有任何体检输入却报「通过」= 假心跳；但也不能静默退出：
        # Task 6 在 audit_rc=2（日历不可用/库读取失败）时正是这条路径。
        if not audit_path:
            return _alarm_no_report(
                reason="没有体检报告（未传 --audit，体检步骤大概率 rc≠0）",
                today=today_hint, topic=args.topic)
        audit_payload = _read_json(audit_path)
        if audit_payload is None:
            return _alarm_no_report(
                reason=f"体检报告不可读：{audit_path}（体检步骤可能 rc≠0，没写出报告）",
                today=today_hint, topic=args.topic, annotate=False)
        gaps = _items(audit_payload, "gaps", audit_path)
        if gaps is None:
            return _alarm_no_report(
                reason=f"体检报告的 gaps 不是对象数组：{audit_path}",
                today=today_hint, topic=args.topic, annotate=False)
        if not all(_has_date_layer(g) for g in gaps):
            print(f"::error::{audit_path} 的 gaps 项缺少 date/layer", file=sys.stderr)
            return _alarm_no_report(
                reason=f"体检报告的 gaps 项缺少 date/layer：{audit_path}",
                today=today_hint, topic=args.topic, annotate=False)
        if args.repair:
            outcomes, skipped = _load_repair(args.repair)
        if args.audit and args.audit_pre:
            pre_gaps = _load_pre_gaps(args.audit_pre)

    today = _pick_today(args.today, audit_payload)
    if today is None:
        return 2
    window_days = _pick_window_days(args.window_days, audit_payload)

    repaired = [o for o in outcomes if _is_ok(o)]
    remaining = _remaining_gaps(gaps, outcomes, skipped)
    discovered = pre_gaps if pre_gaps is not None else gaps
    if pre_gaps is not None:
        # 复检窗口可能和原报告不同：只有复检真正覆盖到的日期才能说「已修复」，
        # 否则「复检里没有」只是没检查，不是修好了。
        covered = _covered_dates(audit_payload)
        if covered is not None:
            outside = [g for g in discovered if g.get("date") not in covered]
            if outside:
                print(f"::warning::复检报告未覆盖 {len(outside)} 个原缺口"
                      f"（{_summarize(outside)}），不对其断言「已修复」",
                      file=sys.stderr)
                outside_keys = {(g.get("layer"), g.get("date")) for g in outside}
                discovered = [g for g in discovered
                              if (g.get("layer"), g.get("date")) not in outside_keys]
    fixed = _diff_gaps(discovered, remaining)
    verified = pre_gaps is not None

    status = decide_status(gaps=discovered, repaired=repaired,
                           remaining=remaining, no_db=args.no_db)
    title, body = format_message(
        status=status, today=today, gaps=discovered, repaired=repaired,
        remaining=remaining, no_db=args.no_db, window_days=window_days,
        fixed=fixed, verified=verified,
    )

    print(body)
    write_step_summary(title, body)

    if status == "manual":
        # 不改变退出码，但让 GitHub 页面上有一条红色注释，别被吞进日志
        print(f"::error::{title}", file=sys.stderr)
    if status == "pass" and len(body) > MAX_BODY:
        print(f"::warning::心跳文案 {len(body)} 字符，超过 {MAX_BODY}", file=sys.stderr)

    if args.topic:
        post_ntfy(args.topic, title, body,
                  priority="high" if status == "manual" else "default")
    else:
        print("::notice::未配置 NTFY_TOPIC，仅写入 Step Summary")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
