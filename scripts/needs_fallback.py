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


def past_deadline(now: dt.datetime, deadline: str) -> bool:
    """现在是否已过「可以确认 CI 死了」的时刻（北京时间的 HH:MM）。

    为什么需要这道门（2026-10-29 两谱系事故的另一半）：
      CI 从收盘 15:30 到 17:00 北京之间有 8 个班次（07:30–09:00 UTC）。本地若在
      CI 还没跑完时就抢先发布，线上就有了当天日期 —— 而 CI 的判据（修复前）正是
      「线上有没有当天」，于是 CI 判 skip、连缓存都不保存，谱系继续冻结。
      也就是说：**本地抢跑本身就在制造分叉**。

      所以本地兜底的语义必须是「CI 已经放弃了，我来救」，而不是「谁先到谁发」。
      过了 deadline（默认 17:00）线上仍落后 → 那才是 CI 真的没成。
    """
    hh, mm = str(deadline).split(":")
    cutoff = now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
    return now >= cutoff


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="判断本地是否需要兜底跑数据流水线")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--dates-json", default=None, help="本地 dates.json 文件（离线/测试用）")
    ap.add_argument("--today", default=None, help="基准日 YYYY-MM-DD（默认今天）")
    ap.add_argument("--deadline", default="17:00",
                    help="北京时间 HH:MM；过了这个点线上仍落后才兜底（默认 17:00，"
                         "给 CI 的 8 个班次留出机会）")
    ap.add_argument("--force", action="store_true",
                    help="忽略 deadline 强制判定（排障/手动补跑用）")
    args = ap.parse_args(argv)

    today = dt.date.fromisoformat(args.today) if args.today else dt.date.today()
    expect = expected_trade_date(today)

    # deadline 门在**所有网络请求之前**判：还没到点就不该去抢跑，
    # 也不该浪费一次请求。非交易日（线上已是最新）同样会在这里自然通过。
    if not args.force and not past_deadline(dt.datetime.now(), args.deadline):
        print(f"未到 deadline {args.deadline}（现在 {dt.datetime.now():%H:%M}），"
              f"让 CI 先跑 —— 本地不抢跑（这是避免双写分叉的关键）")
        return 0

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
