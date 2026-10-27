#!/usr/bin/env python3
"""判断本地是否需要兜底跑一次数据流水线。

逻辑：读线上 `data/dates.json` 的最新日期，与「期望交易日」比较
  - 线上已是最新 → 本地什么都不做（避免与 CI 双跑、双推）
  - 线上落后     → 本地跑 `a-finder.sh site --push` 兜底

用法：
    python3 scripts/needs_fallback.py                    # 默认查 gh-pages
    python3 scripts/needs_fallback.py --today 2026-10-27
    python3 scripts/needs_fallback.py --dates-json /tmp/dates.json   # 离线/测试用

退出码：0 = 无需兜底（线上已最新）；1 = 需要兜底；2 = 网络/参数错误（按需要兜底处理）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
import urllib.request
from pathlib import Path
from typing import Optional

# 站点实际对外域名是 ycu.space（Pages 配了自定义域名，
# dllen.github.io/a-finder-cli 已 404 —— 别再改回去）
DEFAULT_URL = "https://ycu.space/data/dates.json"
UA = "a-finder-fallback/1.0"


def expected_trade_date(today: dt.date) -> str:
    """今天若是交易日则今天，否则取最近一个交易日。

    chinese_calendar 不可用时退化为「工作日」。
    """
    try:
        from chinese_calendar import is_workday
    except Exception:
        is_workday = None

    d = today
    for _ in range(30):  # 最长回退 30 天，够跨过长假
        if is_workday is not None:
            try:
                ok = is_workday(d) and d.weekday() < 5
            except Exception:
                ok = d.weekday() < 5
        else:
            ok = d.weekday() < 5
        if ok:
            return d.isoformat()
        d -= dt.timedelta(days=1)
    return today.isoformat()


def _fetch_dates(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def latest_published(payload: dict) -> Optional[str]:
    dates = payload.get("dates") or []
    return dates[0] if dates else None


def decide(latest: Optional[str], expect: str) -> tuple[bool, str]:
    """返回 (是否需要兜底, 说明)。"""
    if not latest:
        return True, "线上 dates.json 为空"
    if latest >= expect:
        return False, f"线上已是最新（{latest} >= 期望 {expect}）"
    return True, f"线上落后（{latest} < 期望 {expect}）"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="判断本地是否需要兜底跑数据流水线")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--dates-json", default=None, help="本地 dates.json 文件（离线/测试用）")
    ap.add_argument("--today", default=None, help="基准日 YYYY-MM-DD（默认今天）")
    args = ap.parse_args(argv)

    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    expect = expected_trade_date(today)

    try:
        if args.dates_json:
            payload = json.loads(Path(args.dates_json).read_text(encoding="utf-8"))
        else:
            payload = _fetch_dates(args.url)
    except Exception as exc:  # noqa: BLE001
        print(f"无法读取线上日期（{exc}）→ 按需要兜底处理")
        return 2

    need, reason = decide(latest_published(payload), expect)
    print(reason)
    return 1 if need else 0


if __name__ == "__main__":
    raise SystemExit(main())
