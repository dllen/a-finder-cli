#!/usr/bin/env python3
"""把 hs300.db 瘦身成适合放进 actions/cache 的形态。

**为什么需要这个（2026-11-04 查明）**
------------------------------------
CI 的行情谱系靠 actions/cache 的 hs300.db 传递。缓存配额只有 10 GB，
而库里的 trade_plan 表**只增不减**：

    trade_plan（含索引）   469 MB  ← 占全库 94%
    daily_prices（含索引）  21 MB
    其余                    9 MB
    --------------------------
    合计                   499 MB

每班（一天 8 班）都存一份全量库 ⇒ 几天就把 10 GB 配额打爆 ⇒
actions/cache **存不下但不报错**（步骤报 success、post 步骤 skipped）⇒
谱系停在最后一次成功保存的那天。

**关键性质：trade_plan 是派生表**
--------------------------------
它完全可以由 daily_picks + daily_prices 重算出来 —— CI 里就是
"plan build-all --backfill --since <anchor>" 干这件事。所以缓存里
**没必要带完整历史**的 trade_plan。

而真正必须留在缓存里的是**行情**：pick_history 用
build_market_from_db(min_days=221) 按**每只票的 K 线根数**过滤
⇒ 少一天都不行（这是 CLAUDE.md 记的「三维度」判据里的「历史深度」）。

**做法**
--------
删掉 trade_plan 里 --keep-days 窗口之外的行，再 VACUUM 回收空间。
窗口必须 ≥ 审计窗口（audit_coverage.py 的 30 天），
否则 L3 会误报「计划缺口」。

用法：
    python3 scripts/slim_db_for_cache.py --db hs300.db --in-place
    python3 scripts/slim_db_for_cache.py --db hs300.db --out /tmp/slim.db

退出码：0 = 成功；1 = 失败。
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import sqlite3
import sys

# 与 clean_trade_plan_rounds 共用同一条 canonical 规则（唯一真相源）。
# 放在 shared_lib 而不是 scripts 里，是因为这条规则**同时**被「清理脚本」
# 与「瘦身脚本」用 —— 两份各写一遍必然漂移，而漂移的后果是
# 「删掉的不是读路径会选的那一轮 ⇒ 前端数据悄悄变了」。
from shared_lib.plan_rows import CANONICAL_ROUNDS_SQL

#: 保留多少个日历日的 trade_plan。
#:
#: 必须 >= audit_coverage.WINDOW_DAYS（30 天）——审计要读窗口内的
#: (plan_date, portfolio) 来判 L3（计划缺口）。窗口给小了 ⇒
#: 缓存里的库会被判成「计划缺口」⇒ 触发无谓的修复/报警。
DEFAULT_KEEP_DAYS = 60

#: 审计窗口（audit_coverage.WINDOW_DAYS）+ 余量。
MIN_KEEP_DAYS = 45


def slim(db_path: str, out_path: str, keep_days: int) -> dict:
    """删掉旧 trade_plan 行并 VACUUM，返回统计信息。"""
    if keep_days < MIN_KEEP_DAYS:
        raise ValueError(
            "keep_days 至少 %d（审计窗口 30 天 + 余量）" % MIN_KEEP_DAYS
        )

    src_size = os.path.getsize(db_path)

    if out_path == db_path:
        # 原地瘦身：先备份，出错能回滚
        backup = db_path + ".slim.bak"
        shutil.copy2(db_path, backup)
    else:
        backup = None
        if os.path.exists(out_path):
            os.remove(out_path)
        shutil.copy2(db_path, out_path)

    conn = sqlite3.connect(out_path)
    try:
        row = conn.execute("SELECT MAX(plan_date) FROM trade_plan").fetchone()
        latest = row[0] if row else None
        deleted = 0
        cutoff = None
        if latest:
            cutoff = (
                dt.date.fromisoformat(latest) - dt.timedelta(days=keep_days)
            ).isoformat()
            cur = conn.execute(
                "DELETE FROM trade_plan WHERE plan_date < ?", (cutoff,)
            )
            deleted = cur.rowcount
            # 只删「旧日期」还不够：窗口内每个 (日期,档位) 还有**多个轮次**
            # （回补逐轮追加，每轮一个 params_hash）⇒ 一个交易日能到 5000 行。
            # 再按 canonical 规则（行数最多的那一轮，并列取 MAX(plan_id)）
            # 收敛成一轮 —— 与 db_repository.canonical_plan_round 同规则。
            #
            # ⚠️ 老库/精简库可能没有 params_hash / plan_id 列（canonical 规则
            # 需要它们）⇒ 这时**跳过**这一步，而不是让瘦身整个崩掉：
            # 瘦身的本职是「省空间」，不是「改数据结构」。
            deleted_rounds = 0
            cols = {r[1] for r in conn.execute("PRAGMA table_info(trade_plan)")}
            if {"params_hash", "plan_id"} <= cols:
                cur = conn.execute(
                    "DELETE FROM trade_plan WHERE (plan_date, portfolio, params_hash) "
                    "NOT IN (" + CANONICAL_ROUNDS_SQL + ")"
                )
                deleted_rounds = cur.rowcount
        conn.commit()
        # VACUUM 回收空间（必须在事务提交后单独跑）
        conn.execute("VACUUM")
        stats = {
            "latest_plan_date": latest,
            "cutoff": cutoff,
            "deleted_trade_plan_rows": deleted,
            "deleted_non_canonical_rounds": deleted_rounds,
            "trade_plan_rows": conn.execute(
                "SELECT COUNT(*) FROM trade_plan"
            ).fetchone()[0],
            "daily_prices_rows": conn.execute(
                "SELECT COUNT(*) FROM daily_prices"
            ).fetchone()[0],
            "daily_picks_rows": conn.execute(
                "SELECT COUNT(*) FROM daily_picks"
            ).fetchone()[0],
            "min_bars_per_code": conn.execute(
                "SELECT MIN(n) FROM (SELECT COUNT(*) n FROM daily_prices GROUP BY code)"
            ).fetchone()[0],
            "max_trade_date": conn.execute(
                "SELECT MAX(trade_date) FROM daily_prices"
            ).fetchone()[0],
        }
    finally:
        conn.close()

    stats["src_size"] = src_size
    stats["dst_size"] = os.path.getsize(out_path)
    if backup:
        os.remove(backup)
    return stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="瘦身 hs300.db 以便放入 actions/cache")
    ap.add_argument("--db", default="hs300.db", help="源库路径")
    ap.add_argument(
        "--out",
        default=None,
        help="输出路径（默认与 --db 同路径，即原地瘦身）",
    )
    ap.add_argument(
        "--keep-days",
        type=int,
        default=DEFAULT_KEEP_DAYS,
        help="保留多少日历日的 trade_plan（默认 %d，最小 %d）"
        % (DEFAULT_KEEP_DAYS, MIN_KEEP_DAYS),
    )
    ap.add_argument("--dry-run", action="store_true", help="只报告，不修改")
    args = ap.parse_args(argv)

    if not os.path.exists(args.db):
        print("ERROR: 库不存在: %s" % args.db, file=sys.stderr)
        return 1

    if args.dry_run:
        conn = sqlite3.connect("file:%s?mode=ro" % args.db, uri=True)
        try:
            latest = conn.execute("SELECT MAX(plan_date) FROM trade_plan").fetchone()[0]
            total = conn.execute("SELECT COUNT(*) FROM trade_plan").fetchone()[0]
            prices = conn.execute("SELECT COUNT(*) FROM daily_prices").fetchone()[0]
            if latest:
                cutoff = (
                    dt.date.fromisoformat(latest) - dt.timedelta(days=args.keep_days)
                ).isoformat()
                rows = conn.execute(
                    "SELECT COUNT(*) FROM trade_plan WHERE plan_date < ?", (cutoff,)
                ).fetchone()[0]
            else:
                cutoff = None
                rows = 0
        finally:
            conn.close()
        print("[dry-run] 库大小 %.1f MB" % (os.path.getsize(args.db) / 1048576.0))
        print("[dry-run] trade_plan 共 %d 行，窗口外 %d 行会被删" % (total, rows))
        print("[dry-run] daily_prices %d 行" % prices)
        return 0

    out_path = args.out or args.db
    try:
        st = slim(args.db, out_path, args.keep_days)
    except Exception as exc:  # noqa: BLE001
        print("ERROR: 瘦身失败: %s" % exc, file=sys.stderr)
        return 1

    print("源库     : %.1f MB" % (st["src_size"] / 1048576.0))
    print(
        "瘦身库   : %.1f MB (省 %.1f MB)"
        % (
            st["dst_size"] / 1048576.0,
            (st["src_size"] - st["dst_size"]) / 1048576.0,
        )
    )
    print(
        "trade_plan: 删 %d 行，剩 %d 行（窗口从 %s 起）"
        % (st["deleted_trade_plan_rows"], st["trade_plan_rows"], st["cutoff"])
    )
    print(
        "daily_prices: %d 行，最新 %s"
        % (st["daily_prices_rows"], st["max_trade_date"])
    )
    print("daily_picks : %d 行" % st["daily_picks_rows"])
    print(
        "每只票最少 K 线根数: %s（CI 要求 >= 221）" % st["min_bars_per_code"]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
