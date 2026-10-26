#!/usr/bin/env python3
"""从 Cloudflare Worker 拉增量行情，写入本地 SQLite（GH Actions 用）。

契约：
  - 成功且有新数据 → 退出码 0，written>0
  - Worker 无新数据/不可用 → 退出码 3，并写 GITHUB_OUTPUT fell_back=true
  - 参数错误 → 退出码 2

独立性：本脚本只依赖标准库（不 import 仓库内模块），因为 GH workflow 直接
`python3 scripts/pull_from_d1.py` 运行时 sys.path[0] 是 scripts/ 而不是仓库根。
DB 由上游 workflow 恢复缓存/同步创建，脚本只写不建表；表不存在时按「Worker
路径不可用」回退（退出码 3），让调用方走原来的 HTTP 同步兜底。
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import urllib.error
import urllib.request
from typing import Callable, Dict, Optional


class WorkerError(RuntimeError):
    pass


def _http_json(url: str, token: str) -> Dict:
    req = urllib.request.Request(url, headers={"X-Sync-Token": token})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        raise WorkerError(f"{url}: {exc}") from exc


def _prev_close(conn: sqlite3.Connection, code: str, date: str) -> Optional[float]:
    row = conn.execute(
        "SELECT close FROM daily_prices WHERE code=? AND trade_date<? "
        "ORDER BY trade_date DESC LIMIT 1",
        (code, date),
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else None


def pull(
    db_path: str,
    worker: str,
    token: str,
    *,
    max_days: int = 60,
    fetch_json: Callable[[str, str], Dict] = _http_json,
) -> Dict:
    base = worker.rstrip("/")
    try:
        health = fetch_json(f"{base}/api/market/health", token)
        latest = health.get("latest_date")
        if not latest:
            return {"written": 0, "fell_back": True, "reason": "worker has no data"}

        conn = sqlite3.connect(db_path)
        try:
            local_max = conn.execute(
                "SELECT MAX(trade_date) FROM daily_prices"
            ).fetchone()[0]
            dates_res = fetch_json(
                f"{base}/api/market/dates?since={local_max or '1970-01-01'}", token
            )
            dates = [
                d
                for d in (dates_res.get("dates") or [])
                if not local_max or d > local_max
            ]
            dates = sorted(dates)[-max_days:]
            if not dates:
                return {
                    "written": 0,
                    "fell_back": False,
                    "latest": latest,
                    "reason": "up to date",
                }

            written = 0
            for date in dates:
                payload = fetch_json(f"{base}/api/market/day/{date}", token)
                rows = payload.get("rows") or []
                for r in rows:
                    pct = r.get("pct_change")
                    if not pct:
                        prev = _prev_close(conn, r["code"], date)
                        if prev:
                            pct = (float(r["close"]) - prev) / prev * 100
                    conn.execute(
                        "INSERT OR REPLACE INTO daily_prices "
                        "(code, trade_date, open, close, high, low, volume, amount,"
                        " pct_change, turnover) VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (
                            r["code"],
                            r["trade_date"],
                            r["open"],
                            r["close"],
                            r["high"],
                            r["low"],
                            r["volume"],
                            r["amount"],
                            pct if pct is not None else 0.0,
                            r.get("turnover") or 0.0,
                        ),
                    )
                    written += 1
                conn.commit()
            return {
                "written": written,
                "fell_back": False,
                "latest": latest,
                "days": dates,
            }
        finally:
            conn.close()
    except (WorkerError, sqlite3.Error) as exc:
        print(f"::warning::Worker 路径不可用：{exc}", file=sys.stderr)
        return {"written": 0, "fell_back": True, "reason": str(exc)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="从 Worker 拉增量行情写 SQLite")
    ap.add_argument("--db", default="hs300.db")
    ap.add_argument("--worker", default="https://ycu.space")
    ap.add_argument("--token", default=os.environ.get("D1_SYNC_TOKEN", ""))
    ap.add_argument("--max-days", type=int, default=60)
    ap.add_argument("--github-output", action="store_true")
    args = ap.parse_args(argv)

    if not args.token:
        print("::error::缺少 SYNC token（--token 或 D1_SYNC_TOKEN）", file=sys.stderr)
        return 2

    result = pull(args.db, args.worker, args.token, max_days=args.max_days)
    print(json.dumps(result, ensure_ascii=False))

    if args.github_output:
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a", encoding="utf-8") as f:
                f.write(f"fell_back={'true' if result['fell_back'] else 'false'}\n")
                f.write(f"written={result.get('written', 0)}\n")
    return 3 if result["fell_back"] else 0


if __name__ == "__main__":
    sys.exit(main())
