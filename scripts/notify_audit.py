#!/usr/bin/env python3
"""巡检报告格式化 + 通知推送。

三种结局：✅ 通过（每日心跳）/ 🛠 已自动修复 / 🚨 需人工。
通知通道分级降级：ntfy（若配了 NTFY_TOPIC）→ GitHub Step Summary（总是写）。

为什么要有心跳：静默失败最难排查。2026-10-26 站点被清空、10-27 cron 未触发，
都是靠人发现的。有了心跳，「今天没收到消息」本身就是信号。

输入契约（与 audit_coverage / repair_gaps 的 --out 对齐）：
  --audit  : {"today": "YYYY-MM-DD", "window_days": 30,
              "gaps": [{"date","layer","detail"}], ...}
  --repair : {"skipped": [...], "outcomes": [{"layer","dates","ok","reason"}], ...}
`outcomes[].ok` 为真才算已修复；`skipped`（超窗未修）永远算未修。
「剩余缺口」只认体检报告的 gaps：修复结果只用来给已存在的缺口补原因，
不凭 outcome 里的 dates 造新条目（Task 6 传的是复检后的报告，见 _remaining_gaps）。

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
    `repaired` 不参与判定 —— 「有缺口但 remaining 为空」本身就蕴含全部修好；
    保留参数只是让调用方和测试的语义自明。
    """
    if no_db:
        return "manual"
    if remaining:
        return "manual"
    if not gaps:
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


def _reasons_text(items: Sequence[Dict], fallback: str) -> str:
    """把若干 reason 拼成一句；全为空时用 fallback，别渲染出「修复：；」。"""
    return "；".join(filter(None, (it.get("reason") for it in items))) or fallback


def format_message(
    *,
    status: str,
    today: str,
    gaps: Sequence[Dict],
    repaired: Sequence[Dict],
    remaining: Sequence[Dict],
    no_db: bool = False,
    window_days: int = 30,
) -> tuple[str, str]:
    """返回 (标题, 正文)。"""
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
        body = (f"🛠 {today} 巡检发现缺口并已修复\n"
                f"发现：{_summarize(gaps) or '—'}\n"
                f"修复：{_reasons_text(repaired, '已自动补齐')}\n"
                # outcomes 只能证明修复命令退出码为 0，不等于「复检通过」，
                # 别把命令成功写成数据已验证
                f"结果：修复动作全部成功")
        return title, body

    title = "🚨 数据缺口需人工处理"
    lines = [f"🚨 {today} 巡检发现无法自动修复的缺口",
             f"剩余：{_summarize(remaining) or '—'}"]
    if repaired:
        lines.append(f"已修复：{_reasons_text(repaired, '修复动作成功')}")
    # skipped 项自带明确原因（例如行情超窗不自动修），直接透出比泛泛而谈有用；
    # 没有修复信息时回退 gap 的 detail，别让正文只剩「L1 2026-10-10」这种内部代码。
    notes: List[str] = []
    for item in remaining:
        if item.get("reason"):
            text = item["reason"]
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


def _outcome_dates(outcome: Dict) -> List[str]:
    raw = outcome.get("dates")
    if not isinstance(raw, list):
        return []
    return [d for d in raw if isinstance(d, str)]


def _remaining_gaps(
    gaps: Sequence[Dict],
    outcomes: Sequence[Dict],
    skipped: Sequence[Dict],
) -> List[Dict]:
    """仍未修好的缺口。

    以体检报告的 gaps 为**唯一事实来源**：修复失败/跳过的项只用来给已存在的
    缺口补 reason，绝不凭 outcome 的 dates 造出新缺口。

    为什么要这样：Task 6 优先传复检后的报告，gaps 里只剩真正还缺的日期。
    修复动作是「一个 action 覆盖多天」，第一条命令失败就 break，于是 failed
    outcome 的 dates 里可能包含前半段**已经修好**的日期；按 dates 铺开就会
    把已修好的报成「剩余」，与复检结论直接矛盾（audit 干净 + failed outcome
    甚至会假报 🚨）。
    """
    skipped_keys = {(s.get("layer"), s.get("date")) for s in skipped}
    fixed = {
        (o.get("layer"), d)
        for o in outcomes if o.get("ok")
        for d in _outcome_dates(o)
    } - skipped_keys
    # skipped（超窗未修）永远算未修，哪怕同 key 混进了一个 ok outcome

    reasons: Dict[tuple, str] = {}
    for o in outcomes:
        if o.get("ok"):
            continue
        reason = o.get("reason") or "修复命令失败"
        for d in _outcome_dates(o):
            reasons[(o.get("layer"), d)] = reason
    # skipped 项自带明确原因（例如行情超窗不自动修），优先于泛泛的失败原因
    for s in skipped:
        if s.get("reason"):
            reasons[(s.get("layer"), s.get("date"))] = s["reason"]

    remaining: List[Dict] = []
    seen = set()
    for g in gaps:
        key = (g.get("layer"), g.get("date"))
        if key in fixed or key in seen:
            continue
        seen.add(key)
        item = dict(g)
        if key in reasons:
            item["reason"] = reasons[key]
        remaining.append(item)
    return remaining


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
    return dt.date.today().isoformat()


def _pick_window_days(args_window: Optional[int], payload: Dict) -> int:
    """心跳文案里的窗口天数：显式参数 > 报告自带 > 30。"""
    if args_window is not None and args_window > 0:
        return args_window
    value = payload.get("window_days")
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return value
    return 30


def _alarm_no_report(*, reason: str, today: str, topic: str) -> int:
    """体检自己坏了（没报告/报告读不了）：仍然要告警，不能只剩 Actions 变红。

    「没有体检输入」和「缓存里没有数据库」同待遇：都写 Summary + 推送，
    否则最需要告警的场景（体检 rc=2 没写出 --out）在通知层完全静默。
    退出码保持 2，让 CI 也能体现「无法判定」。
    """
    title = "🚨 巡检无法执行：没有体检报告"
    body = (f"🚨 {today} 巡检无法执行：{reason}\n"
            f"没有体检结果就无法判断数据完整性，按「需人工」处理\n"
            f"建议：查看 daily-audit workflow 里体检步骤的日志与退出码，"
            f"必要时手动跑 bash a-finder.sh fallback")
    print(body)
    write_step_summary(title, body)
    # 原因已经在正文/日志里，这里只留一条红色注释，别把标题与原因重复堆在一起
    print(f"::error::{title}", file=sys.stderr)
    if topic:
        post_ntfy(topic, title, body, priority="high")
    else:
        print("::notice::未配置 NTFY_TOPIC，仅写入 Step Summary")
    return 2


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="巡检报告通知")
    ap.add_argument("--audit", default=None, help="体检 JSON（含 gaps）")
    ap.add_argument("--repair", default=None, help="修复结果 JSON（含 outcomes）")
    ap.add_argument("--today", default=None)
    ap.add_argument("--topic", default=os.environ.get("NTFY_TOPIC", ""))
    ap.add_argument("--window-days", type=int, default=None,
                    help="体检窗口天数（仅用于心跳文案；默认取报告里的 window_days，"
                         "缺失用 30）")
    ap.add_argument("--no-db", action="store_true",
                    help="缓存里没有数据库（此时不能报通过）")
    args = ap.parse_args(argv)

    # --today 非法时不发任何消息（连告警里的日期都不可信），直接失败
    if args.today is not None and _as_date(args.today) is None:
        print(f"::error::--today 不是合法日期: {args.today}", file=sys.stderr)
        return 2

    gaps: List[Dict] = []
    audit_payload: Dict = {}
    outcomes: List[Dict] = []
    skipped: List[Dict] = []

    # no_db 时体检根本没跑：旧报告/修复结果都不可信，也不读
    # （缓存丢失场景下这些文件可能压根不存在，不能因此漏掉告警）
    if not args.no_db:
        today_hint = _pick_today(args.today) or dt.date.today().isoformat()
        # 没有任何体检输入却报「通过」= 假心跳；但也不能静默退出：
        # Task 6 在 audit_rc=2（日历不可用/库读取失败）时正是这条路径。
        if not args.audit:
            return _alarm_no_report(
                reason="没有体检报告（未传 --audit，体检步骤大概率 rc≠0）",
                today=today_hint, topic=args.topic)
        audit_payload = _read_json(args.audit)
        if audit_payload is None:
            return _alarm_no_report(
                reason=f"体检报告不可读：{args.audit}（体检步骤可能 rc≠0，没写出报告）",
                today=today_hint, topic=args.topic)
        gaps = _items(audit_payload, "gaps", args.audit)
        if gaps is None:
            return _alarm_no_report(
                reason=f"体检报告的 gaps 不是对象数组：{args.audit}",
                today=today_hint, topic=args.topic)
        if not all(_has_date_layer(g) for g in gaps):
            print(f"::error::{args.audit} 的 gaps 项缺少 date/layer", file=sys.stderr)
            return _alarm_no_report(
                reason=f"体检报告的 gaps 项缺少 date/layer：{args.audit}",
                today=today_hint, topic=args.topic)
        if args.repair:
            outcomes, skipped = _load_repair(args.repair)

    today = _pick_today(args.today, audit_payload)
    if today is None:
        return 2
    window_days = _pick_window_days(args.window_days, audit_payload)

    repaired = [o for o in outcomes if o.get("ok")]
    remaining = _remaining_gaps(gaps, outcomes, skipped)

    status = decide_status(gaps=gaps, repaired=repaired, remaining=remaining,
                           no_db=args.no_db)
    title, body = format_message(
        status=status, today=today, gaps=gaps, repaired=repaired,
        remaining=remaining, no_db=args.no_db, window_days=window_days,
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
