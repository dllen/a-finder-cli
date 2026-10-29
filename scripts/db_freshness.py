#!/usr/bin/env python3
"""判定本地 hs300.db 相对「期望交易日」是否新鲜 —— CI 同步前的判据。

为什么需要这个脚本（2026-10-29 查明的两谱系事故）：

  `deploy-site.yml` 的 freshness 步骤原来读**线上站点**的 `data/dates.json`，
  问的是「站点有没有当天的日期」。但站点可以被**另一个写者**发布 —— 本地
  `a-finder.sh site --push` 就是。于是：

    本地发布 2026-10-27 → 线上 dates.json 最新 = 10-27 → CI 的 freshness 判 skip
    → 跳过同步、也跳过缓存保存 → CI 的行情谱系永远不推进

  而 `export_json.py` 的 dates.json 来自 `daily_picks`/`trade_plan`，
  **不看 `daily_prices`** —— 所以「站点有这天」≠「CI 库有这天的行情」。

  正确的判据只有一个：**问 CI 自己的库**。这个脚本就是那个判据。

落后天数用**交易日**算，不用自然日：
  - 周一早上跑，库停在周五 → 自然日差 3 天，交易日只差 0 天（周五就是最新交易日）
  - 用自然日会把每个周一都误判成「落后 3 天」

用法：
    python3 scripts/db_freshness.py --db hs300.db --expect 2026-10-30
    python3 scripts/db_freshness.py --db hs300.db --expect 2026-10-30 --json
    python3 scripts/db_freshness.py --db hs300.db --expect 2026-10-30 --github-output

退出码：0 = 库已含期望交易日（不必同步）；1 = 落后但可增量补；2 = 无库/读不了；
        3 = 落后过多，判定为**陈旧谱系**，建议冷启动重建。
与其它脚本的 0/1/2 约定对齐，另加 3 表示「谱系级别的问题」，让 CI 能分流。
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from market_calendar import trading_days  # noqa: E402

# 落后多少个交易日就判定为「陈旧谱系」而不是「正常增量」。
# 3 天 ≈ 跨一个周末 + 1 天：正常的节假日/数据源延迟不会超过这个量级；
# 一旦超过，说明不是「今天没抓到」，而是**这条谱系很久没推进了**（缓存孤儿）。
DEFAULT_MAX_LAG = 3


def _latest_trade_date(db_path: str) -> Optional[str]:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute("SELECT MAX(trade_date) FROM daily_prices").fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def _count_codes(db_path: str, date: str) -> int:
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT COUNT(DISTINCT code) FROM daily_prices WHERE trade_date = ?",
            (date,),
        ).fetchone()
        return int(row[0]) if row else 0
    finally:
        conn.close()


def assess(
    db_path: str,
    expect: str,
    *,
    max_lag: int = DEFAULT_MAX_LAG,
) -> Dict[str, object]:
    """算出库相对 expect 的新鲜度。返回可直接序列化成 JSON 的字典。"""
    expect_date = dt.date.fromisoformat(expect)
    result: Dict[str, object] = {
        "db": db_path,
        "expect": expect,
        "latest": None,
        "lag_trading_days": None,
        "stale": True,
        "orphan_lineage": False,
        "reason": "",
        "codes_on_expect": 0,
    }

    if not Path(db_path).exists():
        result["reason"] = "数据库不存在"
        result["orphan_lineage"] = True
        return result

    try:
        latest = _latest_trade_date(db_path)
    except Exception as exc:  # noqa: BLE001
        result["reason"] = f"读 daily_prices 失败: {exc}"
        return result

    if not latest:
        result["reason"] = "daily_prices 为空（空库/坏库）"
        result["orphan_lineage"] = True
        return result

    result["latest"] = latest

    # 应该有的交易日：从库的最新日之后 到 expect（含）。
    # 用交易日历算，天然跨过周末与长假。
    if latest >= expect:
        result["lag_trading_days"] = 0
        result["stale"] = False
        result["codes_on_expect"] = _count_codes(db_path, expect)
        result["reason"] = "库已含期望交易日"
        return result

    # 从 latest 的次日开始找应该有的交易日
    window_start = dt.date.fromisoformat(latest) + dt.timedelta(days=1)
    missing: List[str] = trading_days(window_start, expect_date)
    # 日历不可用时 trading_days 返回 [] —— 那就退化为「按工作日数」，
    # 并在 reason 里说明，别让「算不出来」看起来像「不落后」。
    if not missing and window_start <= expect_date:
        cursor = window_start
        while cursor <= expect_date:
            if cursor.weekday() < 5:
                missing.append(cursor.isoformat())
            cursor += dt.timedelta(days=1)
        result["reason"] = "（交易日历不可用，按工作日估算）"

    lag = len(missing)
    result["lag_trading_days"] = lag
    result["stale"] = lag > 0
    result["orphan_lineage"] = lag > max_lag
    if not result["reason"]:
        result["reason"] = (
            f"落后 {lag} 个交易日（最新 {latest} < 期望 {expect}）"
            + (f"，超过阈值 {max_lag} → 判定为陈旧谱系" if lag > max_lag else "")
        )
    return result


def _code_for(result: Dict[str, object], max_lag: int) -> int:
    """退出码语义：0 新鲜 / 1 可增量补 / 2 无库读不了 / 3 陈旧谱系。

    注意顺序：lag == 0 必须先判成「新鲜」，不能被 `not lag` 的真值判断漏到下面
    （`not 0` 是 True —— 旧写法会把「已最新」误报成「需要增量同步」）。
    """
    if result["latest"] is None:
        # 没有库 / 空库 / 读不了 → CI 走冷启动
        return 2
    if not result["stale"]:
        return 0
    if result["orphan_lineage"]:
        return 3
    return 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="判定 hs300.db 是否新鲜")
    ap.add_argument("--db", default="hs300.db")
    ap.add_argument("--expect", required=True, help="期望交易日 YYYY-MM-DD")
    ap.add_argument("--max-lag", type=int, default=DEFAULT_MAX_LAG,
                    help=f"落后多少个交易日算陈旧谱系（默认 {DEFAULT_MAX_LAG}）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--github-output", action="store_true",
                    help="把 fresh/lag/orphan 写进 $GITHUB_OUTPUT")
    args = ap.parse_args(argv)

    try:
        dt.date.fromisoformat(args.expect)
    except ValueError:
        print(f"::error::--expect 不是合法日期: {args.expect}", file=sys.stderr)
        return 2

    result = assess(args.db, args.expect, max_lag=args.max_lag)
    code = _code_for(result, args.max_lag)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"库: {result['db']}")
        print(f"最新行情日: {result['latest'] or '（无）'}")
        print(f"期望交易日: {result['expect']}")
        print(f"落后交易日: {result['lag_trading_days'] if result['lag_trading_days'] is not None else '（未知）'}")
        print(f"结论: {result['reason']}")
        if result["orphan_lineage"]:
            print("⚠ 判定为陈旧谱系：缓存很可能是不再推进的孤儿，应当冷启动重建")

    if args.github_output:
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            fresh = "false" if (result["stale"] or code != 0) else "true"
            lag = result["lag_trading_days"]
            with open(out, "a", encoding="utf-8") as fh:
                fh.write(f"fresh={fresh}\n")
                fh.write(f"latest={result['latest'] or ''}\n")
                fh.write(f"lag={'' if lag is None else lag}\n")
                fh.write(f"orphan={'true' if result['orphan_lineage'] else 'false'}\n")
                fh.write(f"rc={code}\n")

    return code


if __name__ == "__main__":
    raise SystemExit(main())
