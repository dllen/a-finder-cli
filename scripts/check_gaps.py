#!/usr/bin/env python3
"""扫描最近 N 个交易日的行情覆盖度，找出「整天缺失」或「只抓到几十只」的日子。

用途：缺口自愈 workflow 先用它报告缺哪几天，补抓后再用它验证。

用法：
    python3 scripts/check_gaps.py --db hs300.db --days 30 --min-codes 280
    python3 scripts/check_gaps.py --db hs300.db --days 30 --json

退出码：0 = 无缺口；1 = 有缺口（并打印明细）；2 = 参数/库错误。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
from pathlib import Path
from typing import Dict, List, Optional


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
    db_path: str, days: int, min_codes: int, today: Optional[dt.date] = None
) -> Dict:
    today = today or dt.date.today()
    start = today - dt.timedelta(days=int(days * 1.6) + 7)  # 日历日窗口，覆盖周末/假日
    expected = _trading_days(start, today)

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
    args = ap.parse_args(argv)

    if not Path(args.db).exists():
        print(f"::error::数据库不存在: {args.db}", file=sys.stderr)
        return 2
    today = dt.date.fromisoformat(args.today) if args.today else None
    try:
        report = find_gaps(args.db, args.days, args.min_codes, today=today)
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
