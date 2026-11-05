#!/usr/bin/env python3
"""清理 trade_plan 里「非 canonical 轮次」的重复行。

**背景（2026-11-05 实测）**
------------------------
`trade_plan` 是**多档位 × 多轮次**的宽表：一个交易日会有 10 个资金档位
（5W…50W）+ `default`，每个档位下还有多个 `params_hash` 轮次
（`plan build-all` 会跑「无策略 + 每个策略各一遍」，回补时**逐轮追加**）。

实测本地库：

    总行数                                 1,276,354
    distinct (plan_date, portfolio, code)     117,037
    264 个交易日 × 11 档位

⇒ 一个交易日约 5000 行，而**用户要看的是一套计划**（`MAX_POSITIONS=8`）。

**为什么以前没人炸**
--------------------
因为读路径早就绕开了这个问题 —— `db_repository.get_trade_plan_by_date`
默认只取「一个规范档位 + 一个 canonical 轮次」
（见 `canonical_plan_portfolio` / `canonical_plan_round`）。
所以**前端看到的从来是对的**，膨胀只体现在库体积与查询开销上。
（CLAUDE.md 里「回补会用不同 params_hash 追加重复行」记的就是这个。）

**保留规则：直接复用代码库自己的 canonical 规则**
--------------------------------------------------
`canonical_plan_round` 的规则是：

    行数最多的那一轮；并列时取 MAX(plan_id) 最大的（最新写入的）

本脚本**不另立规则** —— 它按同一条规则算出每个
`(plan_date, portfolio)` 的 canonical `params_hash`，只保留那一轮的行。

为什么这样删是**安全**的：

1. canonical 轮次本来就是「行数最多」⇒ 删掉别的轮次**不改变**行数，
   也就**不改变** canonical 轮次是谁 ⇒ 读路径结果逐字节相同
   （已对全部 2904 个 (日期,档位) 组合校验：0 处不一致）；
2. 删掉的是**派生数据**：`trade_plan` 可由 `daily_picks` + `daily_prices`
   重算（`plan build-all --backfill`），丢了也能补回来；
3. `plan build` 的缓存判据是 `(plan_date, portfolio, params_hash)`，
   删掉非 canonical 轮次 ⇒ 那几轮下次会**重算**（结果相同，幂等）；
   canonical 轮次保留 ⇒ 常用路径不会重算。

**用法**
------
    python3 scripts/clean_trade_plan_rounds.py --db hs300.db            # 只报告
    python3 scripts/clean_trade_plan_rounds.py --db hs300.db --apply    # 真删

退出码：0 成功；1 出错。
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys


#: 与 db_repository.canonical_plan_round **同一条规则**：
#: 行数最多的那一轮；并列时取 MAX(plan_id) 最大的。
#: 用窗口函数一次算出全部分组，删除时直接复用同一个子查询。
#: 有测试交叉校验它与 db_repository.canonical_plan_round 选出的轮次一致。
CANONICAL_ROUNDS_SQL = """
    SELECT plan_date, portfolio, params_hash FROM (
        SELECT plan_date, portfolio, params_hash,
               ROW_NUMBER() OVER (
                   PARTITION BY plan_date, portfolio
                   ORDER BY COUNT(*) DESC, MAX(plan_id) DESC
               ) AS rn
        FROM trade_plan
        GROUP BY plan_date, portfolio, params_hash
    ) WHERE rn = 1
"""


def plan_cleanup(conn: sqlite3.Connection) -> dict:
    """算出「保留哪些、删除哪些」。返回统计字典（不修改数据）。"""
    total = conn.execute("SELECT COUNT(*) FROM trade_plan").fetchone()[0]
    keep = conn.execute(
        "SELECT COUNT(*) FROM trade_plan WHERE (plan_date, portfolio, params_hash) IN ("
        + CANONICAL_ROUNDS_SQL + ")").fetchone()[0]
    groups = conn.execute(
        "SELECT COUNT(*) FROM (" + CANONICAL_ROUNDS_SQL + ")").fetchone()[0]
    dates = conn.execute(
        "SELECT COUNT(DISTINCT plan_date) FROM trade_plan").fetchone()[0]
    portfolios = conn.execute(
        "SELECT COUNT(DISTINCT portfolio) FROM trade_plan").fetchone()[0]
    return {
        "total": total,
        "keep": keep,
        "delete": total - keep,
        "groups": groups,
        "dates": dates,
        "portfolios": portfolios,
    }


def apply_cleanup(conn: sqlite3.Connection) -> int:
    """删掉非 canonical 轮次的行，返回删除行数。"""
    before = conn.execute("SELECT COUNT(*) FROM trade_plan").fetchone()[0]
    conn.execute(
        "DELETE FROM trade_plan WHERE (plan_date, portfolio, params_hash) NOT IN ("
        + CANONICAL_ROUNDS_SQL + ")")
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM trade_plan").fetchone()[0]
    return before - after


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="清理 trade_plan 里非 canonical 轮次的重复行")
    ap.add_argument("--db", default="hs300.db")
    ap.add_argument("--apply", action="store_true",
                    help="真删（默认只报告，不改数据）")
    ap.add_argument("--backup", default=None,
                    help="删除前把整库复制到该路径（强烈建议）")
    args = ap.parse_args(argv)

    if not os.path.exists(args.db):
        print("::error::数据库不存在: %s" % args.db, file=sys.stderr)
        return 1

    if args.apply and args.backup:
        src = sqlite3.connect(args.db)
        dst = sqlite3.connect(args.backup)
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()
        print("已备份到 %s" % args.backup)

    conn = sqlite3.connect(args.db)
    try:
        stats = plan_cleanup(conn)
        print("=== trade_plan 轮次清理 ===")
        print("  交易日数      : %s" % stats["dates"])
        print("  档位数        : %s" % stats["portfolios"])
        print("  (日期,档位) 组: %s" % stats["groups"])
        print("  当前行数      : %s" % stats["total"])
        print("  保留行数      : %s" % stats["keep"])
        print("  将删除行数    : %s" % stats["delete"])
        if not args.apply:
            print("（dry-run：加 --apply 才真删）")
            return 0
        deleted = apply_cleanup(conn)
        left = conn.execute("SELECT COUNT(*) FROM trade_plan").fetchone()[0]
        print("已删除 %s 行，剩余 %s 行" % (deleted, left))
        print("::notice::trade_plan 是派生表，需要完整历史时用 "
              "`plan build-all --backfill --since <anchor>` 重算")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
