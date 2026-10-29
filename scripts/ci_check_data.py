#!/usr/bin/env python3
"""CI 数据完整性校验：确认当天数据真的落地，而不是拿旧数据糊过去。

deploy-site 的同步步骤容忍失败（数据源经常限流），但如果什么都不校验，
workflow 会绿着把昨天的数据当今天发布。这个脚本把「数据是否到位」变成可判定的
退出码，让 workflow 在数据不完整时变红（同时仍允许部署已拿到的数据）。

检查项：
  1. 行情最新日期 >= 期望交易日（默认：今天若是工作日则今天，否则最近工作日）
  2. 期望交易日有行情的股票数 >= --min-codes（默认 250，沪深300 允许少量停牌）
  3. daily_picks 有期望交易日（或行情最新日）的榜单
  4. trade_plan 有同一日期的计划

用法：
  python3 scripts/ci_check_data.py --db hs300.db
  python3 scripts/ci_check_data.py --db hs300.db --expect 2026-10-26 --json
  python3 scripts/ci_check_data.py --db hs300.db --prices-only   # 只校验行情
  python3 scripts/ci_check_data.py --db hs300.db --source d1   # 记录数据来源（d1/direct）
  python3 scripts/ci_check_data.py --db hs300.db --github-output   # 写 data_ok=true/false

退出码：0 = 全部通过；1 = 有检查未通过；2 = 参数/库错误。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sqlite3
import sys
from typing import Dict, List, Optional, Tuple


def latest_weekday(today: dt.date) -> str:
    """最近的「工作日」（不含节假日判断；这里只用于默认期望值）。"""
    d = today
    while d.weekday() >= 5:  # 5=Sat, 6=Sun
        d -= dt.timedelta(days=1)
    return d.isoformat()


def _q(conn: sqlite3.Connection, sql: str, params: Tuple = ()) -> Optional[object]:
    try:
        row = conn.execute(sql, params).fetchone()
    except sqlite3.Error:
        return None
    return row[0] if row else None


def check(db_path: str, expect: str, min_codes: int, prices_only: bool,
          source: str = "unknown") -> Dict:
    conn = sqlite3.connect(db_path)
    try:
        latest_price = _q(conn, "SELECT MAX(trade_date) FROM daily_prices")
        if latest_price is None:
            return {
                "ok": False,
                "expect": expect,
                "latest_price": None,
                "source": source,
                "reasons": ["daily_prices 表为空（行情未同步）"],
            }

        codes_on_latest = _q(
            conn,
            "SELECT COUNT(DISTINCT code) FROM daily_prices WHERE trade_date=?",
            (latest_price,),
        ) or 0
        codes_on_expect = _q(
            conn,
            "SELECT COUNT(DISTINCT code) FROM daily_prices WHERE trade_date=?",
            (expect,),
        ) or 0

        reasons: List[str] = []
        if latest_price < expect:
            reasons.append(f"行情最新 {latest_price} < 期望交易日 {expect}")
        # 不能用 `if codes_on_expect and codes_on_expect < min_codes`：
        # **0 是 falsy**，于是「期望日一行行情都没有」这个最严重的情况反而**不报错**
        # （实测 `--expect 2026-10-26`（该日 0 行）→ rc=0，还打印「✅ 通过」）。
        # 只在 expect <= latest_price（说明这一天本该有数据了）时校验。
        if expect <= latest_price and codes_on_expect < min_codes:
            reasons.append(f"{expect} 只有 {codes_on_expect} 只股票有行情（< {min_codes}）")
        if latest_price == expect and codes_on_latest < min_codes:
            reasons.append(f"{latest_price} 只有 {codes_on_latest} 只股票有行情（< {min_codes}）")

        # 榜单/计划应落在「行情最新日」（同步失败时不会伪造今天的日期）
        pick_date = _q(conn, "SELECT MAX(date) FROM daily_picks") if not prices_only else None
        pick_count = 0
        if pick_date:
            pick_count = _q(
                conn, "SELECT COUNT(*) FROM daily_picks WHERE date=?", (pick_date,)
            ) or 0
        plan_date = _q(conn, "SELECT MAX(plan_date) FROM trade_plan") if not prices_only else None

        if not prices_only:
            if not pick_date:
                reasons.append("daily_picks 为空（选股未执行）")
            elif pick_date < latest_price:
                reasons.append(f"选股日期 {pick_date} 落后于行情 {latest_price}")
            if not plan_date:
                reasons.append("trade_plan 为空（计划未生成）")
            elif plan_date < latest_price:
                reasons.append(f"计划日期 {plan_date} 落后于行情 {latest_price}")

        return {
            "ok": not reasons,
            "expect": expect,
            "latest_price": latest_price,
            "codes_on_latest": codes_on_latest,
            "codes_on_expect": codes_on_expect,
            "min_codes": min_codes,
            "pick_date": pick_date,
            "pick_rows": pick_count,
            "plan_date": plan_date,
            "source": source,
            "reasons": reasons,
        }
    finally:
        conn.close()


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="校验当天行情/选股/计划是否真的到位")
    ap.add_argument("--db", default="hs300.db")
    ap.add_argument("--expect", default="", help="期望交易日 YYYY-MM-DD（默认最近工作日）")
    ap.add_argument("--min-codes", type=int, default=250, help="期望日应有行情的最少股票数")
    ap.add_argument("--prices-only", action="store_true", help="只校验行情（同步重试阶段用）")
    ap.add_argument("--source", default="unknown",
                    help="数据来源：d1 / direct / unknown")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--github-output", action="store_true", help="把 data_ok= 写入 $GITHUB_OUTPUT")
    args = ap.parse_args(argv)

    if not os.path.exists(args.db):
        print(f"::error::数据库不存在: {args.db}", file=sys.stderr)
        return 2

    expect = args.expect or latest_weekday(dt.date.today())
    try:
        result = check(args.db, expect, args.min_codes, args.prices_only,
                       source=args.source)
    except sqlite3.Error as exc:
        print(f"::error::读取数据库失败: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print("=== 数据校验 ===")
        print(f"  期望交易日     : {result['expect']}")
        print(f"  数据来源       : {result.get('source', 'unknown')}")
        print(f"  行情最新日期   : {result['latest_price']}")
        print(f"  该日股票数     : {result.get('codes_on_expect', 0)}"
              f"（最新日 {result.get('codes_on_latest', 0)}，阈值 {result['min_codes']}）")
        if not args.prices_only:
            print(f"  选股日期/条数  : {result.get('pick_date')} / {result.get('pick_rows')}")
            print(f"  计划日期       : {result.get('plan_date')}")
        if result["ok"]:
            print("  结论           : ✅ 通过")
        else:
            print("  结论           : ❌ 未通过")
            for r in result["reasons"]:
                print(f"    - {r}")

    if args.github_output:
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a", encoding="utf-8") as f:
                f.write(f"data_ok={'true' if result['ok'] else 'false'}\n")

    if not result["ok"]:
        # 在 Actions 摘要里也能一眼看到
        for r in result["reasons"]:
            print(f"::warning::数据校验未通过：{r}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
