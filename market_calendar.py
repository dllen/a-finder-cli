"""A 股交易日历：同步与巡检共用的唯一口径。

为什么独立成模块：日历口径一旦散落多处就会漂移。2026-10-26 的漏检
（缺口以「库的最后一天」为基准，缺的那天在基准里不可见）就是口径散落的代价。

两种调用语义必须显式区分：
  - 同步（sync_service）：库里有调休日数据就认 → include_known_weekend=True
  - 巡检（scripts/audit_coverage）：要客观基准 → include_known_weekend=False
"""

from __future__ import annotations

import datetime as dt
from typing import Iterable, List, Optional

_WORKDAY_FN = None
_WORKDAY_LOADED = False


def _reset_workday_cache() -> None:
    """测试用：清空日历可用性缓存，让 monkeypatch 生效。"""
    global _WORKDAY_FN, _WORKDAY_LOADED
    _WORKDAY_FN = None
    _WORKDAY_LOADED = False


def _workday_fn():
    """懒加载 chinese_calendar.is_workday；不可用时返回 None。

    可用性判定是**进程级**的：首次导入失败后会缓存 None，本进程不再重试
    （旧实现每次调用都重试 import）。排障时别误以为它会自动恢复。
    """
    global _WORKDAY_FN, _WORKDAY_LOADED
    if not _WORKDAY_LOADED:
        _WORKDAY_LOADED = True
        try:
            from chinese_calendar import is_workday

            _WORKDAY_FN = is_workday
        except Exception:
            _WORKDAY_FN = None
    return _WORKDAY_FN


def trading_days(
    start: dt.date,
    end: dt.date,
    *,
    include_known_weekend: bool = False,
    known: Optional[Iterable[str]] = None,
) -> List[str]:
    """[start, end] 内真正的 A 股交易日（ISO 字符串，升序）。

    chinese_calendar 的 is_workday 为 True 但属于周末（调休补班）时，A 股不开市：
      - include_known_weekend=False → 一律排除（巡检用：客观基准）
      - include_known_weekend=True  → 该日在 known 里则保留（同步用：库里有就认）

    chinese_calendar 不可用时返回 []（调用方自行降级，绝不猜）。
    """
    wd = _workday_fn()
    if wd is None:
        return []

    known_set = set(known or ())
    days: List[str] = []
    cur = start
    while cur <= end:
        try:
            workday = bool(wd(cur))
        except Exception:
            return []
        if workday:
            iso = cur.isoformat()
            if cur.weekday() < 5:
                days.append(iso)
            elif include_known_weekend and iso in known_set:
                days.append(iso)
        cur += dt.timedelta(days=1)
    return days
