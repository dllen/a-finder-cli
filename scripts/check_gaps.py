#!/usr/bin/env python3
"""扫描最近 N 个交易日的行情覆盖度，找出「整天缺失」或「只抓到几十只」的日子。

用途：缺口自愈 workflow 先用它报告缺哪几天，补抓后再用它验证。

用法：
    python3 scripts/check_gaps.py --db hs300.db --days 30 --min-codes 280
    python3 scripts/check_gaps.py --db hs300.db --days 30 --json

退出码：0 = 无缺口；1 = 有缺口（并打印明细）；2 = 参数/库错误。

2026-11-05：谱系已从 actions/cache 换成 GitHub Release 资产（tag db-lineage），
冷启动窗口 450 天，成分股接口不可达时降级到仓库内置名单
（data/hs300_constituents.json）。本文件在 gap-heal 的 push 触发路径里，
改动它会触发一次 gap-heal run —— 用来验证新谱系链路是否真的跑通。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional

#: **当天行情什么时候算「已出」**（北京时间小时）。
#:
#: A 股 15:00 收盘，行情源通常 15:00~15:30 之间陆续落地，
#: 所以 15:30 之前不能把当天算进期望（否则必然是「当天 0 只」的假缺口）。
#:
#: ⚠️ 之前是「**整天**都排除当天」—— 那在**收盘后**跑就错了：
#: 实测 2026-11-05 20:34（北京）CI 的 gap-heal 跑完，谱系仍停在 2026-11-04，
#: 因为 11-05 被当成「还没出数的当天」给排除了 ⇒ 当天永远补不进来。
#: ⇒ 改成**按时间判断**：收盘后当天就算期望。
DATA_READY_HOUR = 15
DATA_READY_MINUTE = 30


def _beijing_now() -> dt.datetime:
    """当前**北京时间**。

    ⚠️ 必须显式换算：CI 的 `actions/setup-python` 跑在 **UTC** 上，
    `dt.datetime.now()` 取的是 UTC —— 16:00~24:00 UTC 已经是北京次日，
    「今天」会算错一天（gap-heal.yml 之前**没设 TZ**，deploy-site/daily-audit 设了）。
    """
    try:
        from zoneinfo import ZoneInfo
        return dt.datetime.now(ZoneInfo("Asia/Shanghai"))
    except Exception:
        # zoneinfo 不可用（没 tzdata）时退回固定 +8
        return dt.datetime.now(dt.timezone(dt.timedelta(hours=8)))


def _trading_days(start: dt.date, end: dt.date) -> List[str]:
    """优先用 chinese_calendar；不可用时退化为「工作日」。"""
    try:
        from chinese_calendar import is_workday
    except Exception:
        is_workday = None

    days: List[str] = []
    cur = start
    while cur <= end:
        if is_workday is not None:
            try:
                ok = is_workday(cur) and cur.weekday() < 5
            except Exception:
                ok = cur.weekday() < 5
        else:
            ok = cur.weekday() < 5
        if ok:
            days.append(cur.isoformat())
        cur += dt.timedelta(days=1)
    return days


def find_gaps(
    db_path: str, days: int, min_codes: int, today: Optional[dt.date] = None,
    include_today: bool = False,
) -> Dict:
    now_bj = _beijing_now()
    today = today or now_bj.date()
    start = today - dt.timedelta(days=int(days * 1.6) + 7)  # 日历日窗口，覆盖周末/假日
    expected = _trading_days(start, today)
    # ⚠️ **当天**要不要算期望，取决于**行情出了没有**（不是「是不是今天」）。
    #
    # 为什么不能「整天都排除当天」（2026-11-05 两次踩坑）：
    # - 收盘**前**跑：当天必然 0 只 ⇒ 假缺口 ⇒ gap-heal「补抓后仍有缺口则失败」
    #   永远红、clean 恒 false ⇒ 谱系永远存不进去；
    # - 收盘**后**跑（实测 20:34 北京）：当天数据**已经有了**，却仍被排除 ⇒
    #   **当天的数据永远补不进谱系**（谱系卡在 2026-11-04 就是这么来的）。
    # ⇒ 按**时间**判断：北京时间 15:30 之后，当天就算期望。
    #
    # 只排除「**真的就是今天**」的那一天；
    # `--today` 传的是**历史日期**时（复检/测试）照常算进去 ——
    # 那种情况下那一天的数据本来就该存在。
    #
    # 显式传 --include-today 可以强制把当天算进去（不看时间）。
    data_ready = (now_bj.hour, now_bj.minute) >= (DATA_READY_HOUR, DATA_READY_MINUTE)
    if (not include_today and not data_ready and expected
            and expected[-1] == now_bj.date().isoformat()
            and now_bj.date() == today):
        expected = expected[:-1]

    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT trade_date, COUNT(DISTINCT code) FROM daily_prices "
            "WHERE trade_date >= ? GROUP BY trade_date",
            (start.isoformat(),),
        ).fetchall()
    except sqlite3.Error as exc:
        raise RuntimeError(f"读取数据库失败: {exc}") from exc
    finally:
        conn.close()

    got = {d: n for d, n in rows}
    # 只关心最近 days 个交易日
    target = expected[-days:] if len(expected) > days else expected
    gaps = [{"date": d, "codes": got.get(d, 0)} for d in target if got.get(d, 0) < min_codes]
    return {
        "checked": len(target),
        "min_codes": min_codes,
        "gaps": gaps,
        "worst": min((g["codes"] for g in gaps), default=None),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="扫描行情覆盖缺口")
    ap.add_argument("--db", default="hs300.db")
    ap.add_argument("--days", type=int, default=30, help="回看多少个交易日")
    ap.add_argument("--min-codes", type=int, default=280, help="当日最少应有几只")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--today", default=None, help="基准日 YYYY-MM-DD（默认今天，测试用）")
    ap.add_argument("--include-today", action="store_true",
                    help="把当天也算进期望交易日（默认不算：当天行情收盘后才落地）")
    args = ap.parse_args(argv)

    if not Path(args.db).exists():
        print(f"::error::数据库不存在: {args.db}", file=sys.stderr)
        return 2
    today = dt.date.fromisoformat(args.today) if args.today else None
    try:
        report = find_gaps(args.db, args.days, args.min_codes, today=today,
                           include_today=args.include_today)
    except RuntimeError as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"检查最近 {report['checked']} 个交易日（阈值 {report['min_codes']} 只）")
        if not report["gaps"]:
            print("✅ 无缺口")
        else:
            print(f"❌ 发现 {len(report['gaps'])} 天不完整：")
            for g in report["gaps"]:
                print(f"   {g['date']}: {g['codes']} 只")

    return 0 if not report["gaps"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
