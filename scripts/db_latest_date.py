#!/usr/bin/env python3
"""取库里的行情最新日（robust 版，给 CI 用）。

**为什么不能直接一行 sqlite 查询**（2026-11-05 实测事故）：
原来的写法是

    DATA_DATE=$(python3 -c "import sqlite3;print(sqlite3.connect('hs300.db')
      .execute('SELECT MAX(trade_date) FROM daily_prices').fetchone()[0])")

冷启动时库里**连表都没有**（同步 0 行 ⇒ 从没建过 daily_prices），
于是这一行抛 sqlite3.OperationalError 并**退出 1**。
它被赋值给 DATA_DATE，而 CI 的那一步是 `set -e` ⇒
**整步瞬间失败（实测 0.00 min）**，而真正的错误（同步没抓到数据）
早在三步之前的 sync 步骤里被 `| tail -40 || true` 吞掉了 ——
于是 run 变红，但没人知道红在哪。

本脚本把三件事分开做：

1. 库不存在 / 没有 daily_prices 表 / 表为空 ⇒ 打印空串并**退出 3**（明确区分）；
2. 正常 ⇒ 打印最新日并退出 0；
3. 任何 sqlite 异常 ⇒ 打印错误到 stderr、退出 1。

用法：

    DATA_DATE=$(python3 scripts/db_latest_date.py --db hs300.db) || rc=$?
    # rc=3 ⇒ 库里没有行情（冷启动没成功）；rc=1 ⇒ 真错误

退出码：0 有最新日；3 库里没有行情；1 出错。
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys


def latest_trade_date(db_path: str):
    """返回 (最新日, 状态码)。状态码：0 正常 / 3 没有行情 / 1 出错。"""
    if not os.path.exists(db_path):
        print("::warning::数据库不存在：%s" % db_path, file=sys.stderr)
        return "", 3
    try:
        conn = sqlite3.connect(db_path)
    except sqlite3.Error as exc:
        print("::error::打开数据库失败：%s" % exc, file=sys.stderr)
        return "", 1
    try:
        row = conn.execute("SELECT MAX(trade_date) FROM daily_prices").fetchone()
    except sqlite3.OperationalError as exc:
        # 没有 daily_prices 表 = 这条谱系从来没抓到过行情
        print(
            "::warning::库里没有 daily_prices 表（这条谱系从未抓到行情）：%s" % exc,
            file=sys.stderr,
        )
        return "", 3
    except sqlite3.Error as exc:
        print("::error::读取行情最新日失败：%s" % exc, file=sys.stderr)
        return "", 1
    finally:
        conn.close()
    if not row or not row[0]:
        print("::warning::daily_prices 是空的（同步没抓到数据）", file=sys.stderr)
        return "", 3
    return str(row[0]), 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="取库里行情的最新交易日")
    ap.add_argument("--db", default="hs300.db")
    ap.add_argument("--github-output", action="store_true",
                    help="把 data_date= 写入 $GITHUB_OUTPUT")
    args = ap.parse_args(argv)

    date, rc = latest_trade_date(args.db)
    if rc == 0:
        print(date)
    if args.github_output:
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a", encoding="utf-8") as fh:
                fh.write("data_date=%s\n" % date)
                fh.write("data_date_rc=%s\n" % rc)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
