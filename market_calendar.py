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


def calendar_ready_for(day: dt.date) -> bool:
    """日历是否**覆盖**这一天。覆盖不全（或依赖不可用）返回 False。

    为什么必须能和「这一天不是交易日」区分开：
      `trading_days()` 对**任何**异常都返回 `[]`，而 `[]` 既可能是「区间内没有
      交易日」（正常），也可能是「这个年份根本没有数据」（故障）。两者混为一谈时，
      调用方只能猜。

    已确认的定时炸弹（2026-10-29 实测）：`chinese_calendar` 1.11.0（PyPI 最新）
    只覆盖 **2004–2026**，对 2027 抛 `NotImplementedError: no available data for
    year 2027`。所以从 **2027-01-01 起**：
      - `audit_coverage` 每晚 rc=2（无法判定）→ 修复/复检/导出/部署全部不跑
      - `deploy-site` 里内联的 `is_workday` 直接 traceback → 发布链整条停摆
    该包按年更新（国务院发布次年放假安排后才发版），所以在 2027 数据发布前，
    唯一正确做法是**让调用方优雅降级**，而不是等它上线。这个函数就是给那些
    调用方用的判据。
    """
    wd = _workday_fn()
    if wd is None:
        return False
    try:
        wd(day)
    except Exception:
        return False
    return True


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
    需要区分「区间内无交易日」与「日历不可用/没这个年份」时，先调
    `calendar_ready_for()` 判可用性，别靠 `trading_days() == []` 猜。
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
