# 数据产出保障体系（每日巡检 + 自动补齐）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 增加一条独立于 GitHub schedule 的晚间巡检链路，每晚 21:00 体检三层数据（行情/选股/计划）、自动补齐近期缺口、并无论成败都发出可感知的报告。

**Architecture:** 抽出共享交易日历模块 `market_calendar.py`（同步与巡检共用，显式区分语义）；新增纯判定脚本 `scripts/audit_coverage.py`（无写副作用，可单测）与修复脚本 `scripts/repair_gaps.py`（消费体检结果，注入式命令执行器便于测试）；由新的 `daily-audit.yml` 串起「体检 → 修复 → 复检 → 发布 → 通知」，与既有发布链路 `deploy-site.yml` 完全解耦。

**Tech Stack:** Python 3.11、SQLite、`chinese_calendar`、GitHub Actions、ntfy.sh（可选）、pytest（Python 单测）、`node --test`（既有 JS 单测不涉及）。

**设计文档:** `docs/superpowers/specs/2026-10-27-daily-audit-design.md`

---

## 文件结构

| 文件 | 职责 | 状态 |
|---|---|---|
| `market_calendar.py` | A 股交易日历唯一口径（同步/巡检共用） | 新建 |
| `tests/test_market_calendar.py` | 日历单测（周末/节假日/调休/降级） | 新建 |
| `sync_service.py` | 改用 `market_calendar`，删除私有日历函数 | 修改 |
| `tests/test_sync_gap_calendar.py` | 改为从 `market_calendar` 导入 | 修改 |
| `scripts/audit_coverage.py` | 三层缺口判定（纯函数，无写副作用） | 新建 |
| `tests/test_audit_coverage.py` | 体检单测（含 10-26 漏检回归） | 新建 |
| `scripts/repair_gaps.py` | 按体检结果补齐（注入式 runner） | 新建 |
| `tests/test_repair_gaps.py` | 修复单测（选择逻辑 + 幂等） | 新建 |
| `scripts/notify_audit.py` | 报告格式化 + ntfy 推送 | 新建 |
| `tests/test_notify_audit.py` | 通知单测 | 新建 |
| `.github/workflows/daily-audit.yml` | 21:00 巡检链路 | 新建 |
| `CLAUDE.md` | 记录巡检链路与排查顺序 | 修改 |

**依赖方向（单向，无环）：**

```
market_calendar  ←  sync_service
                 ←  scripts/audit_coverage
                          ←  scripts/repair_gaps
                                 ←  scripts/notify_audit
                                        ←  .github/workflows/daily-audit.yml
```

---

## Task 1: 共享交易日历模块

**Files:**
- Create: `market_calendar.py`
- Test: `tests/test_market_calendar.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_market_calendar.py`：

```python
"""交易日历单测：同步与巡检共用的唯一口径。"""

import datetime as dt

import pytest

import market_calendar as mc


@pytest.fixture(autouse=True)
def _reset():
    """每个测试前后重置日历可用性缓存，避免测试间串味。"""
    mc._reset_workday_cache()
    yield
    mc._reset_workday_cache()


def test_weekend_excluded():
    """2026-10-24/25 是周六周日 → 不在交易日里。"""
    days = mc.trading_days(dt.date(2026, 10, 23), dt.date(2026, 10, 27))
    assert days == ["2026-10-23", "2026-10-26", "2026-10-27"]


def test_national_day_holiday_excluded():
    """2026-10-01 起国庆长假 → 假期内无交易日。"""
    days = mc.trading_days(dt.date(2026, 9, 30), dt.date(2026, 10, 9))
    assert "2026-10-01" not in days
    assert "2026-10-02" not in days
    assert days, "长假两侧应仍有交易日"


def test_makeup_weekend_excluded_by_default():
    """调休补班的周末在 chinese_calendar 里是工作日，但 A 股不开市 → 默认排除。"""
    days = mc.trading_days(dt.date(2026, 9, 1), dt.date(2026, 9, 30))
    assert all(dt.date.fromisoformat(d).weekday() < 5 for d in days)


def test_makeup_weekend_kept_when_known_and_enabled():
    """显式开启且该日在 known 里 → 保留（同步侧语义：库里有就认）。"""
    days = mc.trading_days(
        dt.date(2026, 9, 1), dt.date(2026, 9, 30),
        include_known_weekend=True, known={"2026-09-27"},
    )
    assert "2026-09-27" in days


def test_calendar_unavailable_returns_empty(monkeypatch):
    """chinese_calendar 不可用 → 返回 []，绝不猜。"""
    mc._WORKDAY_FN = None
    mc._WORKDAY_LOADED = True
    assert mc.trading_days(dt.date(2026, 10, 1), dt.date(2026, 10, 31)) == []


def test_empty_range():
    assert mc.trading_days(dt.date(2026, 10, 27), dt.date(2026, 10, 26)) == []


def test_single_trading_day():
    assert mc.trading_days(dt.date(2026, 10, 26), dt.date(2026, 10, 26)) == ["2026-10-26"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_market_calendar.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'market_calendar'`

- [ ] **Step 3: 最小实现**

创建 `market_calendar.py`：

```python
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
    """懒加载 chinese_calendar.is_workday；不可用时返回 None。"""
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
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_market_calendar.py -v`
Expected: 7 passed

- [ ] **Step 5: 提交**

```bash
git add market_calendar.py tests/test_market_calendar.py
git commit -m "feat(calendar): 抽出共享交易日历模块

同步与巡检共用同一口径，显式区分语义：
- 同步（include_known_weekend=True）：库里有调休日数据就认
- 巡检（默认 False）：客观基准，避免残留数据掩盖缺口

chinese_calendar 不可用时返回 []（不猜），调用方各自降级。"
```

---

## Task 2: 同步侧改用共享日历

**Files:**
- Modify: `sync_service.py`（删除 `_trading_days_from_calendar`，调用 `market_calendar`）
- Test: `tests/test_sync_gap_calendar.py`（改 import）

- [ ] **Step 1: 改测试的导入（先让它失败）**

编辑 `tests/test_sync_gap_calendar.py`，把这一行：

```python
from sync_service import _trading_days_from_calendar
```

改为：

```python
from market_calendar import trading_days


def _trading_days_from_calendar(start, end, known):
    """兼容包装：同步侧语义（库里有调休日就认）。"""
    return trading_days(start, end, include_known_weekend=True, known=known) or None
```

同时在文件顶部 `import pytest` 之后补 `import market_calendar as mc` 与 autouse fixture：

```python
@pytest.fixture(autouse=True)
def _reset_calendar_cache():
    mc._reset_workday_cache()
    yield
    mc._reset_workday_cache()
```

Run: `.venv/bin/python -m pytest tests/test_sync_gap_calendar.py -v`
Expected: PASS（包装函数已提供等价语义）

- [ ] **Step 2: 在 sync_service 中改用 market_calendar**

编辑 `sync_service.py`：删除整个 `_trading_days_from_calendar` 函数（从 `def _trading_days_from_calendar(` 到其 `return days or None` 行结束），并在调用处（约第 356 行）改为：

```python
            from market_calendar import trading_days

            expected_from_calendar = (
                trading_days(start, end, include_known_weekend=True, known=set(market_dates))
                or None
            )
```

保持后续 `if expected_from_calendar:` / `elif market_dates:` 分支不变 —— `None` 与原来的返回语义一致，行为等价。

- [ ] **Step 3: 跑全量日历与同步测试**

Run: `.venv/bin/python -m pytest tests/test_market_calendar.py tests/test_sync_gap_calendar.py tests/test_sync_service.py -v`
Expected: 全部 passed

- [ ] **Step 4: 跑全量测试确认无回归**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 全部 passed（数量应 ≥ 537）

- [ ] **Step 5: 提交**

```bash
git add sync_service.py tests/test_sync_gap_calendar.py
git commit -m "refactor(sync): 改用共享交易日历模块

删除 sync_service._trading_days_from_calendar（私有函数跨模块调用是坏味道），
改由 market_calendar.trading_days 提供同一口径，同步侧显式传
include_known_weekend=True 保持既有行为（库里有调休日数据就认）。"
```

---

## Task 3: 三层缺口体检脚本

**Files:**
- Create: `scripts/audit_coverage.py`
- Test: `tests/test_audit_coverage.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_audit_coverage.py`：

```python
"""三层缺口体检单测。

核心回归：库里有 10-27 而缺 10-26 时，必须能检出 10-26
（2026-10-27 线上数据缺失就是因为基准取自库内数据，缺的那天不可见）。
"""

import datetime as dt
import importlib.util
from pathlib import Path

import pytest

from db_repository import open_db

_SPEC = importlib.util.spec_from_file_location(
    "audit_coverage",
    Path(__file__).resolve().parent.parent / "scripts" / "audit_coverage.py",
)
audit_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(audit_mod)

TODAY = dt.date(2026, 10, 27)
FULL_PORTFOLIOS = ["5W", "10W", "15W", "20W", "25W", "30W", "35W",
                   "40W", "45W", "50W", "default"]


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "audit.db")
    conn = open_db(path)
    yield path, conn
    conn.close()


def _seed_prices(conn, date, n=300):
    for i in range(n):
        conn.execute(
            "INSERT INTO daily_prices (code, trade_date, open, close, high, low,"
            " volume, amount) VALUES (?,?,1,1,1,1,1,1)",
            (f"6000{i:03d}", date),
        )
    conn.commit()


def _seed_picks(conn, date):
    conn.execute(
        "INSERT INTO daily_picks (date, rank, kind, code, name, strategy,"
        " buy, stop, target, score, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (date, 1, "均线", "600519", "贵州茅台", "均线突破", 1.0, 0.9, 1.2, 80.0, "2026-10-27T20:00:00"),
    )
    conn.commit()


def _seed_plan(conn, date, portfolios=None):
    for pf in (portfolios or FULL_PORTFOLIOS):
        conn.execute(
            "INSERT INTO trade_plan (plan_date, portfolio, code, action, plan_price,"
            " size_pct, stop_price, tp_price, rr_ratio, status, reason,"
            " rationale_json, params_hash, created_at, shares)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (date, pf, "600519", "buy", 100.0, 0.1, 90.0, 120.0, 2.0,
             "ok", "", "{}", "hash1", "2026-10-27T20:00:00", 100),
        )
    conn.commit()


def _seed_full_day(conn, date):
    _seed_prices(conn, date)
    _seed_picks(conn, date)
    _seed_plan(conn, date)


def test_all_layers_complete_no_gaps(db):
    path, conn = db
    for d in ("2026-10-23", "2026-10-26", "2026-10-27"):
        _seed_full_day(conn, d)
    # window_days=4 → 区间 10-23~10-27，日历正好给出这 3 个交易日（24/25 是周末）
    report = audit_mod.audit(path, today=TODAY, window_days=4)
    assert report.ok
    assert report.gaps == []


def test_detects_missing_day_even_when_db_has_later_date(db):
    """核心回归：库里有 10-27，10-26 整天缺失 → 必须检出（旧实现看不见）。"""
    path, conn = db
    _seed_full_day(conn, "2026-10-23")
    _seed_full_day(conn, "2026-10-27")
    report = audit_mod.audit(path, today=TODAY, window_days=4)
    assert not report.ok
    assert "2026-10-26" in [g.date for g in report.gaps]


def test_l1_gap_suppresses_l2_l3(db):
    """行情缺失时只报 L1，不报派生出来的 L2/L3（避免噪声）。"""
    path, conn = db
    _seed_full_day(conn, "2026-10-23")
    _seed_full_day(conn, "2026-10-27")
    _seed_prices(conn, "2026-10-26", n=100)          # 只有 100 只 → L1 缺
    report = audit_mod.audit(path, today=TODAY, window_days=4)
    gap_1026 = [g for g in report.gaps if g.date == "2026-10-26"]
    assert [g.layer for g in gap_1026] == ["L1"]
    assert any(g.date == "2026-10-26" and g.layer in ("L2", "L3")
               for g in report.suppressed)


def test_l2_gap_detected(db):
    path, conn = db
    _seed_prices(conn, "2026-10-26")
    _seed_plan(conn, "2026-10-26")
    report = audit_mod.audit(path, today=dt.date(2026, 10, 26), window_days=0)
    gap = [g for g in report.gaps if g.date == "2026-10-26"]
    assert [g.layer for g in gap] == ["L2"]


def test_few_picks_rows_not_a_gap(db):
    """选股行数天然波动 → 只看有没有行，不设行数阈值（否则大量误报）。"""
    path, conn = db
    _seed_prices(conn, "2026-10-26")
    _seed_picks(conn, "2026-10-26")                   # 仅 1 行
    _seed_plan(conn, "2026-10-26")
    report = audit_mod.audit(path, today=dt.date(2026, 10, 26), window_days=0)
    assert report.ok


def test_l3_gap_when_portfolio_missing(db):
    path, conn = db
    _seed_prices(conn, "2026-10-26")
    _seed_picks(conn, "2026-10-26")
    _seed_plan(conn, "2026-10-26", portfolios=["10W", "default"])
    report = audit_mod.audit(path, today=dt.date(2026, 10, 26), window_days=0)
    gap = [g for g in report.gaps if g.date == "2026-10-26"]
    assert [g.layer for g in gap] == ["L3"]
    assert "5W" in gap[0].detail


def test_calendar_unavailable_raises(db, monkeypatch):
    path, conn = db
    _seed_full_day(conn, "2026-10-26")
    import market_calendar as mc
    monkeypatch.setattr(mc, "_WORKDAY_FN", None)
    monkeypatch.setattr(mc, "_WORKDAY_LOADED", True)
    with pytest.raises(audit_mod.CalendarUnavailable):
        audit_mod.audit(path, today=TODAY, window_days=4)


def test_cli_exit_codes(db, capsys):
    path, conn = db
    for d in ("2026-10-23", "2026-10-26", "2026-10-27"):
        _seed_full_day(conn, d)
    base = ["--db", path, "--today", "2026-10-27", "--window-days", "4"]
    assert audit_mod.main(base) == 0
    conn.execute("DELETE FROM daily_picks WHERE date='2026-10-26'")
    conn.commit()
    assert audit_mod.main(base) == 1
    assert audit_mod.main(["--db", str(Path(path).parent / "nope.db")]) == 2


def test_cli_json_output(db, capsys):
    path, conn = db
    _seed_full_day(conn, "2026-10-26")
    audit_mod.main(["--db", path, "--today", "2026-10-26", "--window-days", "0", "--json"])
    import json
    payload = json.loads(capsys.readouterr().out)
    assert "gaps" in payload and "expected_dates" in payload
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_audit_coverage.py -v`
Expected: FAIL — `FileNotFoundError` / `No module named 'audit_coverage'`

- [ ] **Step 3: 实现脚本**

创建 `scripts/audit_coverage.py`：

```python
#!/usr/bin/env python3
"""三层数据缺口体检：行情(L1) / 选股(L2) / 计划(L3)。

基准是 chinese_calendar 算出的真实交易日（默认近 30 天），**完全独立于库内数据**。
这是与 scripts/check_gaps.py 的根本差异：后者以库内已有日期为基准，因此
「库里有 10-27 而缺 10-26」时永远看不见 10-26（2026-10-27 实际踩过这个坑）。

层级关系：L1 缺时只报 L1，不报派生的 L2/L3（没有行情算不出选股）。

用法：
    python3 scripts/audit_coverage.py --db hs300.db
    python3 scripts/audit_coverage.py --db hs300.db --window-days 30 --json

退出码：0 = 无缺口；1 = 有缺口；2 = 无法判定（日历不可用/库读取失败）。
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from market_calendar import trading_days  # noqa: E402

WINDOW_DAYS = 30
MIN_CODES = 280
EXPECTED_PORTFOLIOS = ("5W", "10W", "15W", "20W", "25W", "30W",
                       "35W", "40W", "45W", "50W", "default")


class CalendarUnavailable(RuntimeError):
    """交易日历不可用，无法判定基准。"""


@dataclass(frozen=True)
class Gap:
    date: str
    layer: str          # "L1" | "L2" | "L3"
    detail: str

    def to_dict(self) -> Dict[str, str]:
        return {"date": self.date, "layer": self.layer, "detail": self.detail}


@dataclass
class Report:
    today: str
    window_days: int
    expected_dates: List[str] = field(default_factory=list)
    gaps: List[Gap] = field(default_factory=list)
    suppressed: List[Gap] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.gaps

    def by_layer(self) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = {}
        for g in self.gaps:
            out.setdefault(g.layer, []).append(g.date)
        return out

    def to_dict(self) -> Dict:
        return {
            "today": self.today,
            "ok": self.ok,
            "window_days": self.window_days,
            "expected_dates": self.expected_dates,
            "gaps": [g.to_dict() for g in self.gaps],
            "suppressed": [g.to_dict() for g in self.suppressed],
        }


def _read_layer_counts(db_path: str, since: str):
    """读三层的覆盖情况。返回 (行情每日只数, 选股有行的日期, 计划每日档位集合)。"""
    conn = sqlite3.connect(db_path)
    try:
        prices = {
            d: n for d, n in conn.execute(
                "SELECT trade_date, COUNT(DISTINCT code) FROM daily_prices"
                " WHERE trade_date >= ? GROUP BY trade_date", (since,))
        }
        picks = {
            r[0] for r in conn.execute(
                "SELECT DISTINCT date FROM daily_picks WHERE date >= ?", (since,))
        }
        plans: Dict[str, set] = {}
        for d, pf in conn.execute(
            "SELECT DISTINCT plan_date, portfolio FROM trade_plan WHERE plan_date >= ?",
            (since,),
        ):
            plans.setdefault(d, set()).add(pf)
    finally:
        conn.close()
    return prices, picks, plans


def audit(
    db_path: str,
    *,
    today: Optional[dt.date] = None,
    window_days: int = WINDOW_DAYS,
    min_codes: int = MIN_CODES,
) -> Report:
    """三层体检。基准来自交易日历，与库内数据无关。"""
    today = today or dt.date.today()
    start = today - dt.timedelta(days=window_days)
    expected = trading_days(start, today)
    if not expected:
        raise CalendarUnavailable(
            "交易日历不可用（chinese_calendar 未安装或该年份无数据），无法判定基准")

    if not Path(db_path).exists():
        raise FileNotFoundError(f"数据库不存在: {db_path}")

    prices, picks, plans = _read_layer_counts(db_path, start.isoformat())
    report = Report(today=today.isoformat(), window_days=window_days,
                    expected_dates=expected)

    for d in expected:
        # L1 行情
        codes = prices.get(d, 0)
        if codes < min_codes:
            report.gaps.append(Gap(d, "L1", f"行情只有 {codes} 只（阈值 {min_codes}）"))
            # L1 缺 → L2/L3 判定无意义，记 suppressed 备查，不报
            if d not in picks:
                report.suppressed.append(Gap(d, "L2", "无选股（派生自 L1 缺失）"))
            missing_pf = [p for p in EXPECTED_PORTFOLIOS if p not in plans.get(d, set())]
            if missing_pf:
                report.suppressed.append(
                    Gap(d, "L3", f"缺档位 {','.join(missing_pf)}（派生自 L1 缺失）"))
            continue

        # L2 选股：只看有没有行（行数天然波动，不设阈值）
        if d not in picks:
            report.gaps.append(Gap(d, "L2", "无选股数据"))
            continue   # 选股没有 → 计划同样无从生成

        # L3 计划：每个应有档位都要有行
        have = plans.get(d, set())
        missing_pf = [p for p in EXPECTED_PORTFOLIOS if p not in have]
        if missing_pf:
            report.gaps.append(Gap(d, "L3", f"缺档位 {','.join(missing_pf)}"))

    return report


def _print_human(report: Report) -> None:
    total = len(report.expected_dates)
    print(f"体检区间：最近 {report.window_days} 天，应有 {total} 个交易日")
    if report.ok:
        print(f"✅ 三层无缺口（{report.expected_dates[0]} ~ {report.expected_dates[-1]}）")
        return
    print(f"❌ 发现 {len(report.gaps)} 处缺口：")
    for g in report.gaps:
        print(f"   [{g.layer}] {g.date} — {g.detail}")
    if report.suppressed:
        print(f"   （另有 {len(report.suppressed)} 处派生缺口因 L1 缺失未单独统计）")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="三层数据缺口体检")
    ap.add_argument("--db", default="hs300.db")
    ap.add_argument("--today", default=None, help="基准日 YYYY-MM-DD（默认今天，测试用）")
    ap.add_argument("--window-days", type=int, default=WINDOW_DAYS)
    ap.add_argument("--min-codes", type=int, default=MIN_CODES)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out", default=None, help="把 JSON 报告写入该文件")
    args = ap.parse_args(argv)

    today = dt.date.fromisoformat(args.today) if args.today else None
    try:
        report = audit(args.db, today=today, window_days=args.window_days,
                       min_codes=args.min_codes)
    except CalendarUnavailable as exc:
        print(f"::error::{exc}", file=sys.stderr)
        return 2
    except (FileNotFoundError, sqlite3.Error) as exc:
        print(f"::error::读取数据库失败: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        _print_human(report)

    if args.out:
        Path(args.out).write_text(
            json.dumps(report.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_audit_coverage.py -v`
Expected: 全部 passed

- [ ] **Step 5: 在真实库上跑一次（观察输出是否合理）**

Run: `.venv/bin/python scripts/audit_coverage.py --db hs300.db`
Expected: 输出「体检区间：最近 30 天，应有 N 个交易日」；今天（10-27）刚补完数据，三层应基本齐全，如仍有缺口则记录具体日期供后续 Task 6 验证

- [ ] **Step 6: 提交**

```bash
git add scripts/audit_coverage.py tests/test_audit_coverage.py
git commit -m "feat(audit): 三层数据缺口体检脚本

基准用 chinese_calendar 的真实交易日，完全独立于库内数据 ——
修掉「库里有 10-27 而缺 10-26 时看不见 10-26」的漏检（10-27 实际踩过）。

- L1 行情：该日不足 280 只 → 缺
- L2 选股：该日无行 → 缺（不设行数阈值，行数天然波动）
- L3 计划：任一应有档位无行 → 缺
- L1 缺时只报 L1，L2/L3 记入 suppressed（避免噪声）

退出码：0 无缺口 / 1 有缺口 / 2 无法判定。"
```

---

## Task 4: 缺口修复脚本

**Files:**
- Create: `scripts/repair_gaps.py`
- Test: `tests/test_repair_gaps.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_repair_gaps.py`：

```python
"""缺口修复单测：选择逻辑 + 命令构造 + 退出码 + 报告还原。

命令执行器是注入的，测试不真正跑 CLI。
"""

import datetime as dt
import importlib.util
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

_SPEC = importlib.util.spec_from_file_location(
    "repair_gaps", ROOT / "scripts" / "repair_gaps.py")
repair_mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(repair_mod)

_SPEC2 = importlib.util.spec_from_file_location(
    "audit_coverage", ROOT / "scripts" / "audit_coverage.py")
audit_mod = importlib.util.module_from_spec(_SPEC2)
_SPEC2.loader.exec_module(audit_mod)

TODAY = dt.date(2026, 10, 27)


def _has(cmd, needle):
    """cmd 是 argv 元组，元素是完整路径（如 /path/pick_history.py）
    或独立参数（如 --gap-fill）→ 用子串匹配，不能用 == 判断。"""
    return any(needle in part for part in cmd)


def _report(gaps, suppressed=None):
    return audit_mod.Report(
        today=TODAY.isoformat(), window_days=30,
        expected_dates=["2026-10-23", "2026-10-26", "2026-10-27"],
        gaps=[audit_mod.Gap(*g) for g in gaps],
        suppressed=[audit_mod.Gap(*g) for g in (suppressed or [])],
    )


def _make_db(tmp_path, name="x.db"):
    from db_repository import open_db
    db = tmp_path / name
    conn = open_db(str(db))
    conn.close()
    return str(db)


def _write_report(tmp_path, today, gaps, *, name="audit.json"):
    path = tmp_path / name
    path.write_text(json.dumps({
        "today": today,
        "window_days": 30,
        "expected_dates": [g["date"] for g in gaps],
        "gaps": gaps,
        "suppressed": [],
    }, ensure_ascii=False), encoding="utf-8")
    return str(path)


def test_no_gaps_produces_no_actions():
    assert repair_mod.plan_repairs(_report([]), db_path="hs300.db", today=TODAY) == []


def test_recent_l1_gap_plans_price_sync():
    actions = repair_mod.plan_repairs(
        _report([("2026-10-26", "L1", "行情只有 100 只")]),
        db_path="hs300.db", today=TODAY,
    )
    assert len(actions) == 1
    a = actions[0]
    assert a.layer == "L1"
    assert a.dates == ("2026-10-26",)
    cmd = a.commands[0]
    assert "sync-hs300-range" in cmd
    assert "--gap-fill" in cmd
    assert "2026-10-26" in cmd


def test_old_l1_gap_not_planned():
    """超过 7 天的行情缺口不自动修（复权口径一致性风险）。"""
    report = _report([("2026-10-10", "L1", "行情只有 0 只")])
    assert repair_mod.plan_repairs(report, db_path="hs300.db", today=TODAY) == []
    # 但绝不静默：这些缺口要能说清为什么没修
    skipped = repair_mod.skipped_repairs(report, today=TODAY)
    assert [(s["layer"], s["date"]) for s in skipped] == [("L1", "2026-10-10")]


def test_price_window_follows_report_today_not_wall_clock():
    """7 天窗口以报告的 today（体检日）为基准，不是执行时刻。"""
    report = _report([("2026-10-18", "L1", "行情只有 0 只")])
    # 报告 today = 2026-10-27 → 10-18 已超出窗口
    assert repair_mod.plan_repairs(report, db_path="hs300.db") == []
    # 同一份缺口，体检日是 10-20 → 相对只隔 2 天，应当修
    report.today = "2026-10-20"
    actions = repair_mod.plan_repairs(report, db_path="hs300.db")
    assert [a.layer for a in actions] == ["L1"]
    assert actions[0].dates == ("2026-10-18",)
    # 显式 --today 优先于报告
    assert repair_mod.plan_repairs(report, db_path="hs300.db", today=TODAY) == []


def test_l2_gap_plans_pick_history():
    actions = repair_mod.plan_repairs(
        _report([("2026-10-26", "L2", "无选股数据")]),
        db_path="hs300.db", today=TODAY,
    )
    assert len(actions) == 1
    assert actions[0].layer == "L2"
    cmd = actions[0].commands[0]
    assert _has(cmd, "pick_history.py")
    assert "--date" in cmd and "2026-10-26" in cmd
    assert "--no-sync" in cmd


def test_l3_gap_plans_default_then_backfill():
    actions = repair_mod.plan_repairs(
        _report([("2026-10-26", "L3", "缺档位 5W,10W")]),
        db_path="hs300.db", today=TODAY,
    )
    assert len(actions) == 1
    cmds = actions[0].commands
    assert len(cmds) == 2
    assert "build" in cmds[0] and "--date" in cmds[0]
    assert "build-all" in cmds[1] and "--backfill" in cmds[1]
    assert "--since" in cmds[1] and "2026-10-26" in cmds[1]


def test_multiple_l2_dates_merge_into_one_action():
    actions = repair_mod.plan_repairs(
        _report([("2026-10-26", "L2", "无选股"), ("2026-10-27", "L2", "无选股")]),
        db_path="hs300.db", today=TODAY,
    )
    assert len(actions) == 1
    assert actions[0].dates == ("2026-10-26", "2026-10-27")
    assert len(actions[0].commands) == 2      # 每个日期一条 pick_history


def test_run_repairs_reports_failure_and_keeps_going():
    actions = repair_mod.plan_repairs(
        _report([("2026-10-26", "L2", "无选股"), ("2026-10-27", "L3", "缺档位")]),
        db_path="hs300.db", today=TODAY,
    )
    calls = []

    def fake_runner(cmd):
        calls.append(cmd)
        if _has(cmd, "pick_history.py"):
            return (1, "boom")
        return (0, "ok")

    outcomes = repair_mod.run_repairs(actions, runner=fake_runner)
    assert len(outcomes) == 2
    assert outcomes[0].ok is False          # L2 失败
    assert outcomes[1].ok is True           # L3 仍执行（不中断整体）
    assert len(calls) == 3                  # 1 条 pick_history + 2 条 plan


def test_runner_exception_marks_action_failed_and_keeps_going():
    """执行器抛异常只算当前 action 失败，不能穿出 run_repairs 连累后面的层。"""
    actions = repair_mod.plan_repairs(
        _report([("2026-10-26", "L2", "无选股"), ("2026-10-27", "L3", "缺档位")]),
        db_path="hs300.db", today=TODAY,
    )
    calls = []

    def boom(cmd):
        calls.append(cmd)
        if _has(cmd, "pick_history.py"):
            raise OSError("python 不见了")
        return (0, "ok")

    outcomes = repair_mod.run_repairs(actions, runner=boom)
    assert len(outcomes) == 2
    assert outcomes[0].ok is False
    assert "python 不见了" in outcomes[0].output
    assert outcomes[1].ok is True           # L3 照跑
    assert len(calls) == 3                  # 1 条 pick_history + 2 条 plan


def test_dry_run_does_not_execute():
    actions = repair_mod.plan_repairs(
        _report([("2026-10-26", "L2", "无选股")]),
        db_path="hs300.db", today=TODAY,
    )
    calls = []
    lines = []

    def fake_runner(cmd):
        calls.append(cmd)
        return (0, "ok")

    outcomes = repair_mod.run_repairs(actions, runner=fake_runner, dry_run=True,
                                      log=lines.append)
    assert calls == []
    assert lines == []                      # dry-run 也不该刷执行日志
    assert len(outcomes) == 1


def test_report_from_dict_roundtrip():
    report = _report([("2026-10-26", "L1", "行情只有 100 只")],
                     suppressed=[("2026-10-26", "L2", "派生")])
    payload = report.to_dict()
    payload["future_field"] = "other"       # 报告多出字段不该炸
    again = audit_mod.Report.from_dict(payload)
    assert again.today == report.today
    assert again.window_days == report.window_days
    assert again.expected_dates == report.expected_dates
    assert again.gaps == report.gaps
    assert again.suppressed == report.suppressed


def test_cli_dry_run_exit_zero(capsys, tmp_path):
    """空库 → 1 组 L1 动作 → dry-run 一定 rc=0。"""
    db = _make_db(tmp_path)
    out = tmp_path / "deep" / "dir" / "repair.json"   # 父目录按需创建
    rc = repair_mod.main(["--db", db, "--today", "2026-10-27", "--dry-run",
                          "--out", str(out)])
    assert rc == 0
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert [a["layer"] for a in payload["actions"]] == ["L1"]
    # 30 天窗口里更早的 L1 缺口只报警（不出现在 actions，但要出现在 skipped）
    assert payload["skipped"]
    assert all(s["layer"] == "L1" and s["date"] < "2026-10-20"
               for s in payload["skipped"])


def test_cli_report_path_uses_report_today(capsys, tmp_path):
    """--report 的 today 是窗口基准：旧报告也能修（不是按执行时刻算）。"""
    db = _make_db(tmp_path)
    rep = _write_report(tmp_path, "2026-10-20", [
        {"date": "2026-10-18", "layer": "L1", "detail": "行情只有 0 只"}])
    out = tmp_path / "repair.json"
    rc = repair_mod.main(["--db", db, "--report", rep, "--dry-run", "--out", str(out)])
    assert rc == 0
    payload = json.loads(Path(out).read_text(encoding="utf-8"))
    assert [a["layer"] for a in payload["actions"]] == ["L1"]
    assert payload["actions"][0]["dates"] == ["2026-10-18"]
    assert payload["today"] == "2026-10-20"


def test_cli_reports_skipped_old_l1(capsys, tmp_path):
    """超窗缺口不修，但必须留痕：::warning:: + --out.skipped，不能静默成功。"""
    db = _make_db(tmp_path)
    rep = _write_report(tmp_path, "2026-10-27", [
        {"date": "2026-10-10", "layer": "L1", "detail": "行情只有 0 只"}])
    out = tmp_path / "repair.json"
    rc = repair_mod.main(["--db", db, "--report", rep, "--dry-run", "--out", str(out)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "无需修复" in captured.out
    assert "::warning::" in captured.err and "2026-10-10" in captured.err
    payload = json.loads(Path(out).read_text(encoding="utf-8"))
    assert payload["actions"] == []
    assert [s["date"] for s in payload["skipped"]] == ["2026-10-10"]


def test_cli_repair_failure_exit_one(capsys, tmp_path):
    """rc=1 专指修复真的失败（runner 可注入才测得出来）。"""
    db = _make_db(tmp_path)
    rep = _write_report(tmp_path, "2026-10-27", [
        {"date": "2026-10-27", "layer": "L2", "detail": "无选股数据"}])
    out = tmp_path / "repair.json"
    calls = []

    def fake_runner(cmd):
        calls.append(cmd)
        return (1, "boom")

    rc = repair_mod.main(["--db", db, "--report", rep, "--out", str(out)],
                         runner=fake_runner)
    assert rc == 1
    assert calls and _has(calls[0], "pick_history.py")
    payload = json.loads(Path(out).read_text(encoding="utf-8"))
    assert payload["outcomes"][0]["ok"] is False
    assert "boom" in payload["outcomes"][0]["output"]     # 失败原因不丢
    assert "::error::" in capsys.readouterr().err


def test_cli_bad_inputs_exit_two(capsys, tmp_path):
    """坏输入 = rc 2（与「修复失败」的 1 区分），且不吐 traceback。"""
    db = _make_db(tmp_path)
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{not json", encoding="utf-8")
    no_window = tmp_path / "no-window.json"
    no_window.write_text(json.dumps({"today": "2026-10-27", "gaps": []}),
                         encoding="utf-8")
    cases = [
        ["--db", db, "--report", str(tmp_path / "nope.json")],   # 报告不存在
        ["--db", db, "--report", str(bad_json)],                 # 报告不是 JSON
        ["--db", db, "--report", str(no_window)],                # 报告缺字段
        ["--db", str(tmp_path / "nope.db")],                     # 库不存在
        ["--db", db, "--today", "2026-13-01"],                   # 日期不合法
    ]
    for argv in cases:
        capsys.readouterr()
        assert repair_mod.main(argv) == 2, argv
        captured = capsys.readouterr()
        assert "::error::" in captured.err, argv
        assert "Traceback" not in captured.err + captured.out, argv
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_repair_gaps.py -v`
Expected: FAIL — `No module named 'repair_gaps'`

- [ ] **Step 3: 实现脚本**

创建 `scripts/repair_gaps.py`：

```python
#!/usr/bin/env python3
"""按体检结果补齐缺口：L1 行情（仅近 7 天）/ L2 选股 / L3 计划。

原则：
  - 只为「缺失的日期」生成动作，但 L3 的 `build-all --backfill --since` 会按缺口日
    往后重放：完整日期命中原 params_hash 走缓存短路、结果不变；部分缺失的日期会用
    当前持仓重建，内容可能不同于当时快照 —— 不是「绝不重算已有日期」
  - 选股缺失必然意味着计划缺失：L2 动作里同时带 L3 命令，否则一轮修不收敛
  - L1 超过 MAX_PRICE_DAYS 天只报警不自动修（复权口径可能已变，填历史会污染序列）；
    跳过的缺口打 ::warning:: 并写进 --out 的 skipped，绝不静默成功
  - 某项失败不影响其它项（继续修下一项）；执行器抛异常也只算该项失败
  - 7 天窗口的基准日取**体检报告的 today**，不是执行时刻：跨零点、手工重放、
    排障用旧报告时若按 wall clock 算，本应修的行-情缺口会被静默跳过

退出码：0 = 无需修复或全部成功；1 = 有修复动作失败；
        2 = 输入不可用（报告缺失/坏 JSON/缺字段、库不存在、日历不可用）。
与 audit_coverage 的 0/1/2 对齐，CI 才能区分「坏输入」与「修复失败」。

--out 产物里除了 actions/outcomes/skipped，还带 side_effects：修复前后
open_positions / trade_events 的行数变化。L3 的重放会改写组合状态，而巡检
随后会把库 export+deploy 到 gh-pages —— 用户在组合页看到的数字会在夜里
自己变（实测 2026-10-28：open_positions 535→542、trade_events 1048→1055）。
不记录就等于没人知道，所以这一项必须落盘并出现在通知正文里。

命令执行器通过参数注入，测试不真正跑 CLI。

用法：
    python3 scripts/repair_gaps.py --db hs300.db --report /tmp/audit.json --dry-run
    python3 scripts/repair_gaps.py --db hs300.db --report /tmp/audit.json
"""

# 注意：这里**不能**加 `from __future__ import annotations`。
# dataclasses 在字段注解是字符串时会去 sys.modules 里查本模块（校验 ClassVar/KW_ONLY），
# 而测试用 importlib exec_module 直接加载本文件、不注册 sys.modules，于是拿到 None 报
# AttributeError。本模块的注解在 3.11 下都能求值，无需该 future import。
import argparse
import datetime as dt
import json
import shlex
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from audit_coverage import CalendarUnavailable, Report, audit  # noqa: E402

MAX_PRICE_DAYS = 7      # 行情缺口自动修复的时间窗（天）
TOP_PICKS = 20
COMMAND_TIMEOUT_S = 1800    # 单条 CLI 命令上限；卡死按失败处理，别吃满 CI 预算

# 修复会改写的「组合状态」表：L3 的 `plan build`（paper_trade=True）会写这两张表。
# 巡检修完会把库里的变化 export+deploy 到 gh-pages，用户在组合页看到的数字
# 会在夜里自己变 —— 所以修复前后必须把行数变化记进 --out 的 side_effects，
# 由通知脚本在正文里说清楚（2026-10-28 最终评审 I-2）。
SIDE_EFFECT_TABLES = ("open_positions", "trade_events")

Runner = Callable[[Sequence[str]], Tuple[int, str]]


@dataclass(frozen=True)
class RepairAction:
    layer: str
    dates: Tuple[str, ...]
    commands: Tuple[Tuple[str, ...], ...]
    reason: str


@dataclass
class Outcome:
    action: RepairAction
    ok: bool
    output: str = ""


def _py(*args: str) -> Tuple[str, ...]:
    return (sys.executable, *args)


def _horizon(today: dt.date, max_price_days: int) -> str:
    return (today - dt.timedelta(days=max_price_days)).isoformat()


def _base_date(report: Report, today: Optional[dt.date]) -> dt.date:
    """窗口基准日：显式 --today 优先，否则用报告的 today（体检时刻）。"""
    return today or dt.date.fromisoformat(report.today)


def _plan_l1(dates: Sequence[str], db_path: str) -> Optional[RepairAction]:
    """L1 缺口：先补行情，**再连带补这些日期的选股与计划**。

    行情缺失 ⇒ 该日选股必然缺失 ⇒ 计划也必然缺失，但体检因「L1 缺时只报 L1」
    把它们记进 suppressed 不报。所以只补行情的话，复检会立刻报 L2/L3 缺、
    单轮不收敛 —— 这正是「整天缺失」（2026-10-26 那种）的主场景。

    命令顺序即依赖顺序：行情 → 选股 → 计划。任一步失败则整个 action 中断
    （`run_repairs` 的行为），不会在没有行情的日期上硬算选股。
    """
    if not dates:
        return None
    lo, hi = min(dates), max(dates)
    cmds: List[Tuple[str, ...]] = [
        _py(str(ROOT / "stock_cli.py"), "sync-hs300-range",
            "--start", lo, "--end", hi, "--db", db_path,
            "--gap-fill", "--concurrency", "6", "--rate", "6", "--retries", "2"),
    ]
    # 连带 L2：这些日期的选股同样缺
    cmds.extend(
        _py(str(ROOT / "pick_history.py"), "--db", db_path,
            "--top", str(TOP_PICKS), "--date", d, "--no-sync")
        for d in dates
    )
    # 连带 L3：default 档 + 全档位重放
    cmds.extend(
        _py(str(ROOT / "stock_cli.py"), "plan", "build", "--db", db_path, "--date", d)
        for d in dates
    )
    cmds.append(_backfill_cmd(dates, db_path))
    return RepairAction(
        layer="L1",
        dates=tuple(dates),
        commands=tuple(cmds),
        reason=f"补抓 {lo}~{hi} 的行情缺口，并连带补 {len(dates)} 天的选股与计划",
    )


def _plan_l2(
    dates: Sequence[str],
    plan_dates: Sequence[str],
    db_path: str,
) -> Optional[RepairAction]:
    """L2 缺口：补选股 **并** 补出当天的计划。

    选股缺失必然意味着当天没有计划（`audit_coverage` 见 L2 缺就 `continue`，
    不会再报 L3）。只补选股的话，选股一落地计划立刻成为缺口，复检仍 rc=1，
    得跑第二轮才收敛 —— Task 7 端到端实测踩过。所以这里一次把 L3 的命令也带上。

    `plan_dates` 是 L2 ∪ L3 缺口日的并集：`build-all --backfill` 只需一条，
    `--since` 取并集最早日（只按 L2 算会漏掉更早的 L3 缺口日）。
    """
    if not dates:
        return None
    cmds: List[Tuple[str, ...]] = [
        _py(str(ROOT / "pick_history.py"), "--db", db_path,
            "--top", str(TOP_PICKS), "--date", d, "--no-sync")
        for d in dates
    ]
    # `plan build` 写 portfolio="default" 档；`build-all` 只写 5W..50W 十个档位，
    # 所以缺档位日期两者都不能少（体检的 EXPECTED_PORTFOLIOS 含 default）。
    cmds.extend(
        _py(str(ROOT / "stock_cli.py"), "plan", "build", "--db", db_path, "--date", d)
        for d in dates
    )
    cmds.append(_backfill_cmd(plan_dates, db_path))
    return RepairAction(
        layer="L2", dates=tuple(dates), commands=tuple(cmds),
        reason=f"补算 {len(dates)} 天的选股，并同步补出 {len(plan_dates)} 天的计划")


def _backfill_cmd(plan_dates: Sequence[str], db_path: str) -> Tuple[str, ...]:
    # build-all --backfill 会跑两轮（无策略 + 每个策略各一遍），与 a-finder.sh site
    # 的第 3/4 步同源，因此产出的 params_hash 形状与 CI 一致。
    # 它按 --since 重算**所有** >= since 的日期（不只缺失日）：完整日期命中原
    # params_hash 走缓存短路结果不变；部分缺失的日期会用当前持仓重建，内容可能
    # 与原样不同。之所以从 min(缺口日) 重放，是因为 paper-trade 的持仓/资金曲线
    # 需要从缺口日起按库内已有状态往后推进 —— 缺口日之前的 carryover 不重放。
    return _py(str(ROOT / "stock_cli.py"), "plan", "build-all",
               "--backfill", "--since", min(plan_dates), "--db", db_path)


def _plan_l3(
    dates: Sequence[str],
    plan_dates: Sequence[str],
    db_path: str,
    *,
    with_backfill: bool = True,
    skip: Sequence[str] = (),
) -> Optional[RepairAction]:
    """L3 缺口：每个日期补 default 档，再从最早缺口日重放全档位。

    `skip` 是已经由 L2 动作补过的日期（同一个 action 里避免重复生成命令）；
    `with_backfill=False` 表示全档位重放已由 L2 动作统一发出，这里不再重复。
    """
    if not dates:
        return None
    skipped = set(skip)
    cmds: List[Tuple[str, ...]] = [
        _py(str(ROOT / "stock_cli.py"), "plan", "build", "--db", db_path, "--date", d)
        for d in dates if d not in skipped
    ]
    if with_backfill:
        cmds.append(_backfill_cmd(plan_dates, db_path))
    if not cmds:
        return None
    return RepairAction(layer="L3", dates=tuple(dates), commands=tuple(cmds),
                        reason=f"补算 {len(dates)} 天的交易计划（含各策略轮次）")


def plan_repairs(
    report: Report,
    *,
    db_path: str,
    today: Optional[dt.date] = None,
    max_price_days: int = MAX_PRICE_DAYS,
) -> List[RepairAction]:
    """把体检报告翻译成修复动作（不执行）。

    `today` 缺省时用 `report.today`（体检日），**不是** `date.today()`。
    """
    today = _base_date(report, today)
    horizon = _horizon(today, max_price_days)

    by_layer = report.by_layer()

    actions: List[RepairAction] = []

    l1 = sorted(d for d in by_layer.get("L1", []) if d >= horizon)
    act = _plan_l1(l1, db_path)
    if act:
        actions.append(act)

    l2 = sorted(by_layer.get("L2", []))
    l3 = sorted(by_layer.get("L3", []))
    # 计划命令的日期范围 = L2 ∪ L3：L2 缺的日期计划同样缺，两者共用一条重放
    plan_dates = sorted(set(l2) | set(l3))

    act = _plan_l2(l2, plan_dates, db_path)
    if act:
        actions.append(act)

    # L2 已经带上全档位重放时，L3 动作只补它自己的 default 档，避免重复命令
    act = _plan_l3(l3, plan_dates, db_path, with_backfill=not l2, skip=l2)
    if act:
        actions.append(act)

    return actions


def skipped_repairs(
    report: Report,
    *,
    today: Optional[dt.date] = None,
    max_price_days: int = MAX_PRICE_DAYS,
) -> List[Dict[str, str]]:
    """plan_repairs 有意不修的缺口（目前只有超出时间窗的 L1：只报警）。

    单独暴露是为了「报告里有缺口却没动作」时仍能说明原因，不静默返回成功。
    """
    today = _base_date(report, today)
    horizon = _horizon(today, max_price_days)
    return [
        {"layer": g.layer, "date": g.date, "detail": g.detail,
         "reason": f"行情缺口超出 {max_price_days} 天自动修复窗（复权口径风险），只报警"}
        for g in report.gaps
        if g.layer == "L1" and g.date < horizon
    ]


def _default_runner(cmd: Sequence[str]) -> Tuple[int, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              timeout=COMMAND_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return 124, f"命令超时（>{COMMAND_TIMEOUT_S}s）: {shlex.join(cmd)}"
    tail = (proc.stdout or "")[-1500:] + (proc.stderr or "")[-1500:]
    return proc.returncode, tail


def _side_effect_counts(db_path: str) -> Dict[str, Optional[int]]:
    """数组合状态相关表的行数（修复前/后各一次）。

    只读打开（`mode=ro`）：库不存在时**不能**顺手建一个空库出来。
    表不存在或读不到 → None（不猜，不报 0）。
    """
    counts: Dict[str, Optional[int]] = {t: None for t in SIDE_EFFECT_TABLES}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    except sqlite3.Error:
        return counts
    try:
        for table in SIDE_EFFECT_TABLES:
            try:
                counts[table] = int(conn.execute(
                    f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            except sqlite3.Error:
                counts[table] = None
    finally:
        conn.close()
    return counts


def _side_effect_diff(
    before: Dict[str, Optional[int]],
    after: Dict[str, Optional[int]],
) -> Dict[str, Dict[str, int]]:
    """前后行数差；读不到的那张表直接不报（不猜）。"""
    diff: Dict[str, Dict[str, int]] = {}
    for table in SIDE_EFFECT_TABLES:
        b, a = before.get(table), after.get(table)
        if b is None or a is None:
            continue
        diff[table] = {"before": b, "after": a, "delta": a - b}
    return diff


def _changed_side_effects(
    side_effects: Dict[str, Dict[str, int]],
) -> Dict[str, int]:
    """只留真的变了的那几张表（delta != 0）。"""
    return {t: v["delta"] for t, v in side_effects.items() if v.get("delta")}


def run_repairs(
    actions: Sequence[RepairAction],
    *,
    runner: Runner = _default_runner,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
) -> List[Outcome]:
    """依次执行修复动作；某条命令失败（含执行器抛异常）只中断该 action。

    后面的 action 照常执行：一个层坏了不该连累其它层。
    """
    outcomes: List[Outcome] = []
    for action in actions:
        if dry_run:
            outcomes.append(Outcome(action=action, ok=True, output="[dry-run] 未执行"))
            continue
        ok = True
        output = ""
        for cmd in action.commands:
            log(f"$ {shlex.join(cmd)}")
            try:
                rc, out = runner(cmd)
            except (KeyboardInterrupt, SystemExit):
                raise
            except Exception as exc:      # noqa: BLE001 —— 执行器异常 = 这条命令失败
                ok = False
                output += f"执行器异常: {exc!r}\n"
                log(f"  ✗ {exc!r}")
                break
            output += out
            if rc != 0:
                ok = False
                log(f"  ✗ 退出码 {rc}")
                break
            log("  ✓")
        outcomes.append(Outcome(action=action, ok=ok, output=output))
    return outcomes


def _load_report(path: str) -> Report:
    return Report.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def _write_json(path: str, payload: Dict) -> None:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv=None, *, runner: Runner = _default_runner) -> int:
    ap = argparse.ArgumentParser(description="按体检报告补齐数据缺口")
    ap.add_argument("--db", default="hs300.db")
    ap.add_argument("--report", default=None, help="audit_coverage.py --out 生成的 JSON")
    ap.add_argument("--today", default=None,
                    help="基准日 YYYY-MM-DD（默认取报告的 today，无报告时用今天）")
    ap.add_argument("--max-price-days", type=int, default=MAX_PRICE_DAYS)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--out", default=None, help="把修复结果写入该 JSON 文件")
    args = ap.parse_args(argv)

    try:
        today_override = dt.date.fromisoformat(args.today) if args.today else None
    except ValueError:
        print(f"::error::--today 不是合法日期: {args.today}", file=sys.stderr)
        return 2

    if not Path(args.db).exists():
        print(f"::error::数据库不存在: {args.db}", file=sys.stderr)
        return 2

    # 没有报告文件时自己跑一次体检（便于手工使用）
    if args.report:
        try:
            report = _load_report(args.report)
        except KeyError as exc:
            print(f"::error::体检报告缺字段: {exc}", file=sys.stderr)
            return 2
        except (OSError, ValueError, TypeError) as exc:
            print(f"::error::读取体检报告失败: {exc}", file=sys.stderr)
            return 2
    else:
        try:
            report = audit(args.db, today=today_override)
        except CalendarUnavailable as exc:
            print(f"::error::{exc}", file=sys.stderr)
            return 2
        except (FileNotFoundError, sqlite3.Error) as exc:
            print(f"::error::读取数据库失败: {exc}", file=sys.stderr)
            return 2

    try:
        today = _base_date(report, today_override)
    except ValueError:
        print(f"::error::报告里的 today 不合法: {report.today!r}", file=sys.stderr)
        return 2

    actions = plan_repairs(
        report, db_path=args.db, today=today,
        max_price_days=args.max_price_days,
    )
    skipped = skipped_repairs(
        report, today=today, max_price_days=args.max_price_days,
    )
    for s in skipped:
        print(f"::warning::跳过 [{s['layer']}] {s['date']} — {s['reason']}"
              f"（{s['detail']}）", file=sys.stderr)

    if actions:
        print(f"计划修复 {len(actions)} 组：")
        for a in actions:
            print(f"  [{a.layer}] {a.reason}")
    else:
        print("无需修复")

    # 副作用记账：L3 的重放会改写组合状态（open_positions / trade_events），
    # 这些变化会被巡检自己的 export+deploy 推到线上，必须留痕。
    before_counts = _side_effect_counts(args.db)
    outcomes = run_repairs(actions, runner=runner, dry_run=args.dry_run)
    side_effects = _side_effect_diff(before_counts, _side_effect_counts(args.db))
    changed = _changed_side_effects(side_effects)
    if changed:
        detail = "、".join(f"{t} {v:+d}" for t, v in sorted(changed.items()))
        print(f"::notice::修复改写了组合状态（{detail}）—— 组合页数字可能与修复前不同")

    payload = {
        "today": today.isoformat(),
        "skipped": skipped,
        "side_effects": side_effects,
        "actions": [
            {"layer": a.layer, "dates": list(a.dates), "reason": a.reason}
            for a in actions
        ],
        "outcomes": [
            {"layer": o.action.layer, "dates": list(o.action.dates),
             "ok": o.ok, "reason": o.action.reason,
             "output": o.output[-4000:]}
            for o in outcomes
        ],
    }
    if args.out:
        try:
            _write_json(args.out, payload)
        except OSError as exc:
            print(f"::error::写入 {args.out} 失败: {exc}", file=sys.stderr)
            return 2

    failed = [o for o in outcomes if not o.ok]
    if failed:
        for o in failed:
            print(f"::error::修复失败 [{o.action.layer}] {o.action.reason}",
                  file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_repair_gaps.py -v`
Expected: 全部 passed

- [ ] **Step 5: 在真实库上 dry-run（确认选择正确）**

Run: `.venv/bin/python scripts/repair_gaps.py --db hs300.db --dry-run`
Expected: 打印「计划修复 N 组」或「无需修复」，不执行任何命令

- [ ] **Step 6: 提交**

```bash
git add scripts/repair_gaps.py tests/test_repair_gaps.py
git commit -m "feat(audit): 缺口修复脚本（注入式执行器，可单测）

- L1 行情：仅修近 7 天（更早复权口径可能已变，填历史会污染序列）
- L2 选股：pick_history --date D --no-sync（纯计算）
- L3 计划：plan build --date D + plan build-all --backfill --since D
  （与 a-finder.sh site 第 3/4 步同源，params_hash 形状与 CI 一致）
- 只补缺失日期，绝不重算已有日期
- 单项失败不中断后续项"
```

---

## Task 5: 报告与通知

**Files:**
- Create: `scripts/notify_audit.py`
- Test: `tests/test_notify_audit.py`

- [ ] **Step 1: 写失败测试**

创建 `tests/test_notify_audit.py`：

```python
"""巡检通知格式化单测。"""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

_SPEC = importlib.util.spec_from_file_location(
    "notify_audit", ROOT / "scripts" / "notify_audit.py")
nf = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(nf)


def test_pass_message_is_heartbeat():
    title, body = nf.format_message(
        status="pass", today="2026-10-27", gaps=[], repaired=[], remaining=[])
    assert "✅" in title
    assert "2026-10-27" in body
    assert len(body) < 300, "心跳消息要短"


def test_repaired_message_lists_what_was_fixed():
    title, body = nf.format_message(
        status="repaired", today="2026-10-27",
        gaps=[{"date": "2026-10-26", "layer": "L2", "detail": "无选股数据"}],
        repaired=[{"layer": "L2", "dates": ["2026-10-26"], "reason": "补算 1 天的选股"}],
        remaining=[])
    assert "🛠" in title
    assert "2026-10-26" in body
    assert "L2" in body


def test_manual_message_is_alarm():
    title, body = nf.format_message(
        status="manual", today="2026-10-27",
        gaps=[
            {"date": "2026-10-10", "layer": "L1", "detail": "行情只有 0 只（阈值 280）"},
        ],
        repaired=[],
        remaining=[{"date": "2026-10-10", "layer": "L1",
                    "detail": "行情只有 0 只（阈值 280）"}],
    )
    assert "🚨" in title
    assert "2026-10-10" in body
    assert "L1" in body


def test_body_lists_layers_not_flat_noise():
    title, body = nf.format_message(
        status="manual", today="2026-10-27",
        gaps=[{"date": f"2026-10-{d:02d}", "layer": "L2", "detail": "无选股"}
              for d in (20, 21, 22)],
        repaired=[],
        remaining=[{"date": f"2026-10-{d:02d}", "layer": "L2", "detail": "无选股"}
                   for d in (20, 21, 22)],
    )
    assert "L2" in body
    # 连续日期应收敛表达，不逐个铺开
    assert body.count("2026-10-20") <= 2


def test_no_db_is_manual_not_pass():
    """缓存里没有 hs300.db 时不能报「通过」——那等于宣称体检过但实际没体检。"""
    assert nf.decide_status(gaps=[], repaired=[], remaining=[], no_db=True) == "manual"
    title, body = nf.format_message(
        status="manual", today="2026-10-27", gaps=[], repaired=[],
        remaining=[], no_db=True)
    assert "🚨" in title
    assert "数据库" in body


def test_status_from_outcomes():
    """全部修复成功 → repaired；有失败 → manual。"""
    assert nf.decide_status(gaps=[], repaired=[], remaining=[]) == "pass"
    assert nf.decide_status(
        gaps=[{"date": "d", "layer": "L2", "detail": ""}],
        repaired=[{"layer": "L2", "dates": ["d"], "reason": ""}],
        remaining=[]) == "repaired"
    assert nf.decide_status(
        gaps=[{"date": "d", "layer": "L1", "detail": ""}],
        repaired=[], remaining=[{"date": "d", "layer": "L1", "detail": ""}]) == "manual"
```

- [ ] **Step 2: 跑测试确认失败**

Run: `.venv/bin/python -m pytest tests/test_notify_audit.py -v`
Expected: FAIL — `No module named 'notify_audit'`

- [ ] **Step 3: 实现脚本**

创建 `scripts/notify_audit.py`：

```python
#!/usr/bin/env python3
"""巡检报告格式化 + 通知推送。

三种结局：通过（每日心跳）/ 已自动修复 / 需人工。
通知通道分级降级：ntfy（若配了 NTFY_TOPIC）→ GitHub Step Summary（总是写）。

为什么要有心跳：静默失败最难排查。2026-10-26 站点被清空、10-27 cron 未触发，
都是靠人发现的。有了心跳，「今天没收到消息」本身就是信号。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import Dict, List, Sequence


def decide_status(*, gaps, repaired, remaining, no_db: bool = False) -> str:
    # 没有数据库 = 什么都没体检，绝不能报「通过」
    if no_db:
        return "manual"
    if not gaps:
        return "pass"
    if not remaining:
        return "repaired"
    return "manual"


def _summarize(items: Sequence[Dict], key: str = "layer") -> str:
    """按层聚合日期，连续区间收敛成 a~b，避免逐日铺开。"""
    by_layer: Dict[str, List[str]] = {}
    for it in items:
        by_layer.setdefault(it[key], []).append(it["date"])

    parts = []
    for layer in sorted(by_layer):
        dates = sorted(set(by_layer[layer]))
        if len(dates) == 1:
            parts.append(f"{layer} {dates[0]}")
        elif len(dates) <= 2:
            parts.append(f"{layer} {', '.join(dates)}")
        else:
            parts.append(f"{layer} {dates[0]}~{dates[-1]}（{len(dates)} 天）")
    return "；".join(parts)


def format_message(
    *,
    status: str,
    today: str,
    gaps: Sequence[Dict],
    repaired: Sequence[Dict],
    remaining: Sequence[Dict],
    no_db: bool = False,
) -> tuple[str, str]:
    """返回 (标题, 正文)。"""
    if no_db:
        return (
            "🚨 巡检无法执行：没有数据库",
            f"🚨 {today} 巡检无法执行：Actions 缓存里没有 hs300.db\n"
            f"常见原因：早班 deploy-site 从未成功跑过，或缓存已过期\n"
            f"建议：手动跑 bash a-finder.sh fallback 或触发一次 deploy-site",
        )

    if status == "pass":
        title = "✅ 数据巡检通过"
        body = (f"✅ {today} 巡检通过｜当天数据已发布｜近 30 天三层无缺口\n"
                f"（心跳：收到这条说明巡检链路活着）")
        return title, body

    if status == "repaired":
        title = "🛠 数据缺口已自动修复"
        body = (f"🛠 {today} 巡检发现缺口并已修复\n"
                f"发现：{_summarize(gaps)}\n"
                f"修复：{'；'.join(a['reason'] for a in repaired)}\n"
                f"复检：通过")
        return title, body

    title = "🚨 数据缺口需人工处理"
    lines = [f"🚨 {today} 巡检发现无法自动修复的缺口",
             f"剩余：{_summarize(remaining)}"]
    if repaired:
        lines.append(f"已修复：{'；'.join(a['reason'] for a in repaired)}")
    lines.append("常见原因：行情缺口超过 7 天（复权口径风险）或数据源当日无数据")
    lines.append("建议：手动跑 bash a-finder.sh fallback 或检查数据源")
    return title, "\n".join(lines)


def write_step_summary(title: str, body: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"## {title}\n\n```\n{body}\n```\n\n")
    except OSError:
        pass


def post_ntfy(topic: str, title: str, body: str, *, priority: str = "default") -> bool:
    if not topic:
        return False
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=body.encode("utf-8"),
        headers={
            "Title": title.encode("utf-8"),
            "Priority": priority,
            "Tags": "chart_with_upwards_trend",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20):
            return True
    except Exception as exc:  # noqa: BLE001
        print(f"::warning::ntfy 推送失败: {exc}", file=sys.stderr)
        return False


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="巡检报告通知")
    ap.add_argument("--audit", default=None, help="体检 JSON（含 gaps）")
    ap.add_argument("--repair", default=None, help="修复结果 JSON（含 outcomes）")
    ap.add_argument("--today", default=None)
    ap.add_argument("--topic", default=os.environ.get("NTFY_TOPIC", ""))
    ap.add_argument("--no-db", action="store_true",
                    help="缓存里没有数据库（此时不能报通过）")
    args = ap.parse_args(argv)

    gaps: List[Dict] = []
    if args.audit:
        gaps = json.loads(Path(args.audit).read_text(encoding="utf-8")).get("gaps", [])

    repaired: List[Dict] = []
    remaining: List[Dict] = []
    if args.repair:
        payload = json.loads(Path(args.repair).read_text(encoding="utf-8"))
        repaired = [o for o in payload.get("outcomes", []) if o.get("ok")]
        failed_dates = {d for o in payload.get("outcomes", []) if not o.get("ok")
                        for d in o.get("dates", [])}
        remaining = [g for g in gaps if g["date"] in failed_dates]
        if failed_dates:
            remaining = remaining or gaps
    elif gaps:
        remaining = list(gaps)

    status = decide_status(gaps=gaps, repaired=repaired, remaining=remaining,
                           no_db=args.no_db)
    today = args.today or "今天"
    title, body = format_message(status=status, today=today, gaps=gaps,
                                 repaired=repaired, remaining=remaining,
                                 no_db=args.no_db)

    print(body)
    write_step_summary(title, body)
    if args.topic:
        post_ntfy(args.topic, title, body,
                  priority="high" if status == "manual" else "default")
    else:
        print("::notice::未配置 NTFY_TOPIC，仅写入 Step Summary")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: 跑测试确认通过**

Run: `.venv/bin/python -m pytest tests/test_notify_audit.py -v`
Expected: 全部 passed

- [ ] **Step 5: 提交**

```bash
git add scripts/notify_audit.py tests/test_notify_audit.py
git commit -m "feat(audit): 巡检报告格式化与通知推送

三种结局三种措辞：✅ 通过（每日心跳）/ 🛠 已自动修复 / 🚨 需人工。
通道分级降级：ntfy（若配 NTFY_TOPIC）→ Step Summary（总是写）。
连续日期收敛表达（a~b），避免逐日铺开造成噪声。"
```

---

## Task 6: 巡检 workflow

**Files:**
- Create: `.github/workflows/daily-audit.yml`

**前置事实（来自 Task 5 的最终实现，务必按此接线）：**

`scripts/notify_audit.py` 的参数语义：
- `--audit <path>`：本次判定「剩余缺口」用的报告（有复检时传复检报告）
- `--audit-pre <path>`：**修复前**的原报告；传了它才会用「原/复检差集」算「已修复」
- `--repair <path>`：`repair_gaps.py --out` 的产物（含 outcomes/skipped）
- `--no-db`：缓存里没有数据库 → 报 🚨 而不是 ✅
- 退出码：0 正常（含 🚨 的情况）/ 2 输入不可用

**关键接线规则**：三份报告要这样传，三种结局才都正确 ——
- 没有走修复（体检就干净）：只传 `--audit audit.json`
- 走了修复且复检有结果：`--audit audit2.json --audit-pre audit.json --repair repair.json`
- 只有原报告（复检没跑成）：`--audit audit.json --repair repair.json`（差集为空 → 不会假报已修复）

- [ ] **Step 1: 写 workflow**

创建 `.github/workflows/daily-audit.yml`：

```yaml
name: daily-audit

# 每晚 21:00（北京）巡检数据产出：三层体检 → 自动补齐 → 复检 → 发布 → 报告。
#
# 与 deploy-site.yml 是各自独立的 workflow，**失败互不影响**（巡检失败不影响早班发布，
# 反之亦然）；但两者**共用 hs300.db 缓存**（`hs300-db-*` 前缀、restore-keys 互相兜底），
# 所以「解耦」指失败隔离，不是数据隔离。
# 为什么要有这条链路：GitHub schedule 对该仓库不可靠（10-26 的 07:30 那班
# 延迟到 14:27 UTC，10-27 三班一次都没触发），且缺口一旦静默发生没人知道。
on:
  schedule:
    # 13:00 UTC = 21:00 北京，给早班 8 次机会（07:30–09:00 UTC）留足余量
    - cron: "0 13 * * 1-5"
  workflow_dispatch:

concurrency:
  group: daily-audit
  cancel-in-progress: false

permissions:
  contents: write        # 需要推 gh-pages

jobs:
  audit:
    runs-on: ubuntu-latest
    env:
      PYTHONUNBUFFERED: "1"
      TZ: Asia/Shanghai
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip

      - name: Install dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -e .

      - name: Restore hs300.db from cache
        uses: actions/cache@v4
        with:
          path: hs300.db
          key: hs300-db-${{ github.run_id }}
          restore-keys: |
            hs300-db-

      - name: 判定今天日期与是否交易日
        id: trade-day
        run: |
          set -uo pipefail
          # 交易日口径只有一个来源：market_calendar.trading_days（与体检同一份）。
          # 手写一份 is_workday(d) and d.weekday() < 5 就是又一份口径，
          # 设计文档里「口径漂移的代价已经付过一次」说的就是这种事。
          python3 - <<'PY' | tee -a "$GITHUB_OUTPUT"
          import datetime as dt
          from market_calendar import trading_days
          d = dt.date.today()
          window = trading_days(d - dt.timedelta(days=60), d)
          ok = bool(window)
          # 日历不可用时不能谎报「非交易日」—— 那会把「不知道」说成「不是交易日」。
          # 用 unknown 表达，日历不可用的事实由体检步的 rc=2 暴露。
          is_trade = (d.isoformat() in window) if ok else None
          print(f"today_bj={d.isoformat()}")
          print(f"is_trade_day={'unknown' if is_trade is None else str(is_trade).lower()}")
          print(f"calendar_ok={str(ok).lower()}")
          PY
          # 日历不可用时 trading_days 返回 []：体检那一步会以 rc=2（无法判定）
          # 失败，这里只留一条明确的红注释说明根因。
          if grep -qx 'calendar_ok=false' "$GITHUB_OUTPUT"; then
            echo "::error::交易日历不可用（chinese_calendar 缺失或无当年数据），无法判定今天是否交易日"
          fi
          # cron 排的是「周一到周五」，但长假（如国庆）会落在这上面。那天跑巡检只是
          # 「巡检历史」，没有当日新增产出 —— 明确写进 Step Summary，别让绿色心跳
          # 被读成「今天的数据已更新」。
          if grep -qx 'is_trade_day=false' "$GITHUB_OUTPUT"; then
            {
              echo "### 非交易日，仅巡检历史"
              echo ""
              echo "今天（$(grep -m1 '^today_bj=' "$GITHUB_OUTPUT" | cut -d= -f2)）不是 A 股交易日：本次按近 30 天窗口检查历史数据，不期待当日新增行情/选股/计划。"
            } >> "$GITHUB_STEP_SUMMARY"
          fi

      - name: 三层体检
        id: audit
        env:
          TODAY: ${{ steps.trade-day.outputs.today_bj }}
        run: |
          set -uo pipefail
          if [ ! -f hs300.db ]; then
            echo "::error::缓存里没有 hs300.db：早班从未成功跑过或缓存已过期"
            echo "no_db=true" >> "$GITHUB_OUTPUT"
            exit 1
          fi
          # 显式传 --today：手工 workflow_dispatch 跨零点时，「今天」还没开盘，
          # 不传就会把当天算进应有交易日 → 报出「今天 行情只有 0 只」这种假缺口。
          ARGS=()
          [ -n "${TODAY:-}" ] && ARGS+=(--today "$TODAY")
          set +e
          python3 scripts/audit_coverage.py --db hs300.db --out /tmp/audit.json \
            ${ARGS[@]+"${ARGS[@]}"}
          rc=$?
          set -e
          echo "audit_rc=$rc" >> "$GITHUB_OUTPUT"
          if [ "$rc" -eq 2 ]; then
            echo "::error::体检无法判定（日历不可用或库读取失败）—— 绝不当作无缺口"
            exit 1
          fi
          # 报告必须落盘：体检崩了却没写出报告时，下游会一路静默降级
          # （修复步拿不到报告 → 复检干净 → 通知把「读不到」当成「没有缺口」），
          # 最后发出一条绿色「✅ 巡检通过」。这里当致命错误处理（2026-10-28 评审复现）。
          if [ ! -s /tmp/audit.json ]; then
            echo "::error::体检报告没有落盘（/tmp/audit.json 不存在或为空）—— 绝不当作无缺口"
            exit 1
          fi
          echo "（$rc = 1 表示有缺口，符合预期时会继续修复）"

      - name: 自动修复
        id: repair
        if: steps.audit.outputs.audit_rc == '1'
        env:
          TODAY: ${{ steps.trade-day.outputs.today_bj }}
        run: |
          set -uo pipefail
          ARGS=()
          [ -n "${TODAY:-}" ] && ARGS+=(--today "$TODAY")
          set +e
          python3 scripts/repair_gaps.py --db hs300.db \
            --report /tmp/audit.json --out /tmp/repair.json \
            ${ARGS[@]+"${ARGS[@]}"}
          rc=$?
          set -e
          echo "repair_rc=$rc" >> "$GITHUB_OUTPUT"
          echo "（1 = 部分修复失败，仍需复检并报警）"

      - name: 复检
        id: recheck
        if: steps.audit.outputs.audit_rc == '1'
        env:
          TODAY: ${{ steps.trade-day.outputs.today_bj }}
        run: |
          set -uo pipefail
          ARGS=()
          [ -n "${TODAY:-}" ] && ARGS+=(--today "$TODAY")
          set +e
          python3 scripts/audit_coverage.py --db hs300.db --out /tmp/audit2.json \
            ${ARGS[@]+"${ARGS[@]}"}
          rc=$?
          set -e
          # 复检报告同样必须落盘：没有它就无从判断「还缺不缺」，更不能导出部署。
          if [ "$rc" -eq 2 ] || [ ! -s /tmp/audit2.json ]; then
            echo "::error::复检无法判定或报告没有落盘（/tmp/audit2.json）—— 绝不当作无缺口"
            echo "recheck_rc=2" >> "$GITHUB_OUTPUT"
            exit 1
          fi
          echo "recheck_rc=$rc" >> "$GITHUB_OUTPUT"

      - name: 导出修复后的数据（仅在复检干净时）
        id: export
        if: steps.recheck.outputs.recheck_rc == '0'
        run: |
          set -uo pipefail
          python3 export_json.py --db hs300.db --out site
          python3 scripts/verify_site_export.py --site site

      - name: 部署 gh-pages（仅在导出校验通过时）
        if: steps.export.outcome == 'success'
        uses: peaceiris/actions-gh-pages@v4
        with:
          github_token: ${{ secrets.GITHUB_TOKEN }}
          publish_dir: ./site
          force_orphan: true
          commit_message: "audit: 补齐缺口并更新 ${{ steps.trade-day.outputs.today_bj }}"

      - name: 保存修复后的 hs300.db 到缓存
        if: always()
        uses: actions/cache@v4
        with:
          path: hs300.db
          key: hs300-db-${{ github.run_id }}

      - name: 通知
        if: always()
        env:
          NTFY_TOPIC: ${{ secrets.NTFY_TOPIC }}
        run: |
          set -uo pipefail
          # 日历步失败时 today_bj 为空：裸 --today 会让 argparse 先死，告警渠道整个失效。
          # 空则让脚本回退报告里的 today（--no-db 时回退系统日期）。
          TODAY="${{ steps.trade-day.outputs.today_bj }}"
          ARGS=""
          [ -n "$TODAY" ] && ARGS="--today $TODAY"
          # 有复检报告时：--audit 用复检（决定「剩余」），--audit-pre 用原报告（算「已修复」）
          if [ -f /tmp/audit2.json ]; then
            ARGS="$ARGS --audit /tmp/audit2.json --audit-pre /tmp/audit.json"
          elif [ -f /tmp/audit.json ]; then
            ARGS="$ARGS --audit /tmp/audit.json"
          fi
          [ -f /tmp/repair.json ] && ARGS="$ARGS --repair /tmp/repair.json"
          if [ "${{ steps.audit.outputs.no_db }}" = "true" ]; then
            ARGS="$ARGS --no-db"
          fi
          # 非交易日的标注必须进推送正文（不只写 Step Summary）：
          # 手机上收到的推送才是用户真会读的那一条。
          if [ "${{ steps.trade-day.outputs.is_trade_day }}" = "false" ]; then
            ARGS="$ARGS --non-trading-day"
          fi
          python3 scripts/notify_audit.py $ARGS

      - name: 有未修复缺口或修复失败则让 run 变红
        # 通知本身总是 rc=0（告警渠道失败不该掩盖结论），这里把「需人工」显式暴露成红灯。
        # 修复失败（repair_rc=1）同样要红：不能只发一条通知就让 run 变绿。
        if: always() && ((steps.audit.outputs.audit_rc == '1' && steps.recheck.outputs.recheck_rc != '0') || steps.repair.outputs.repair_rc == '1')
        run: |
          echo "::error::巡检发现无法自动修复的缺口或修复失败，详见 Step Summary 与通知"
          exit 1
```

- [ ] **Step 2: 校验 YAML 与所有 run 块语法**

Run:
```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/daily-audit.yml')); print('YAML OK')"
```
Expected: `YAML OK`

Run:
```bash
python3 - <<'EOF'
import yaml, subprocess, tempfile, os
wf = yaml.safe_load(open('.github/workflows/daily-audit.yml'))
steps = wf['jobs']['audit']['steps']
bad = 0
for s in steps:
    if not s.get('run'):
        continue
    with tempfile.NamedTemporaryFile('w', suffix='.sh', delete=False) as f:
        f.write(s['run'])
        p = f.name
    r = subprocess.run(['bash', '-n', p], capture_output=True, text=True)
    print(('OK  ' if r.returncode == 0 else 'FAIL'), s.get('name'))
    if r.returncode:
        print(r.stderr[:300])
        bad += 1
    os.unlink(p)
print('失败数:', bad)
assert bad == 0
EOF
```
Expected: 每个 step 都 `OK`，`失败数: 0`

- [ ] **Step 3: 校验 notify 接线与环境变量名一致**

Run:
```bash
grep -n "NTFY_TOPIC\|--audit-pre\|--no-db\|audit2.json" .github/workflows/daily-audit.yml
```
Expected: 能看到 `NTFY_TOPIC` 来自 `secrets.NTFY_TOPIC`；`--audit-pre /tmp/audit.json` 与
`--audit /tmp/audit2.json` 成对出现；`--no-db` 由 `steps.audit.outputs.no_db` 控制。

再确认脚本真的接受这些参数：

Run: `python3 scripts/notify_audit.py --help | head -20`
Expected: 输出里有 `--audit`、`--audit-pre`、`--repair`、`--no-db`

- [ ] **Step 4: 提交**

```bash
git add .github/workflows/daily-audit.yml
git commit -m "feat(ci): 每日巡检 workflow（21:00 北京）

体检 → 自动修复 → 复检 → 导出校验 → 部署 → 通知，与 deploy-site 完全解耦。

关键接线：通知要传三份报告才能让三种结局都正确 ——
- 复检存在时 --audit 用复检报告（决定剩余缺口）、--audit-pre 用原报告（算已修复差集）
- 只传原报告时差集为空，不会假报「已修复」
- 无 DB 时 --no-db 报需人工，绝不报通过

另：
- 体检 rc=2（无法判定）直接失败，不当作无缺口
- 仅在复检干净时才导出/部署（避免用半修好的数据覆盖线上）
- 复用 verify_site_export.py 守卫（10-26 站点被清空的教训）
- 通知步的 --today 仅在非空时传（日历步失败时脚本回退报告/系统日期），
  避免裸 --today 让 argparse 先死、告警渠道整个失效
- 通知总是执行；有未修复缺口额外让 run 变红"
```

## Task 7: 文档与端到端验证

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: 更新 CLAUDE.md**

在「# CI 自动部署（deploy-site.yml）」章节之后插入新章节：

```markdown
# 每日巡检与自动补齐（daily-audit.yml）
- 每晚 21:00 北京（13:00 UTC，周一到周五）独立跑一次体检，与 deploy-site 完全解耦。
- **体检基准**：`market_calendar.trading_days` 算出的真实交易日（近 30 天），
  **完全独立于库内数据**。这是关键 —— 缺口判定若以「库的最后一天」为基准，
  整天漏抓的那天在基准里不可见，永远查不出来（2026-10-26 实际踩过）。
- **三层判定**：L1 行情（该日 < 280 只）、L2 选股（该日无行）、L3 计划（任一应有档位无行）。
  L1 缺时只报 L1，派生的 L2/L3 记入 suppressed，避免噪声。
  L2/L3 不设行数阈值 —— 选股数量天然波动（55~77 条），设阈值会大量误报。
- **修复边界**：L2/L3 自动补（纯计算）；L1 只补近 7 天（更早的复权口径可能已变，
  填历史会污染序列，宁可报警）；转债永不自动补（数据源只有实时快照）。
  只补缺失日期，**绝不重算已有日期**，产出幂等。
- **修完必须复检**：否则会重演「补了行情但选股没补上却以为修好了」。
- **通知**：三种结局三种措辞 —— ✅ 通过（每日心跳）/ 🛠 已自动修复 / 🚨 需人工。
  心跳是刻意设计的：静默失败最难排查，「今天没收到消息」本身就是信号。
- **手工使用**：
  ```bash
  python3 scripts/audit_coverage.py --db hs300.db            # 体检
  python3 scripts/repair_gaps.py --db hs300.db --dry-run     # 看会修什么
  python3 scripts/repair_gaps.py --db hs300.db               # 真修
  ```
```

- [ ] **Step 2: 跑全量测试**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 全部 passed

- [ ] **Step 3: 端到端验证 —— 体检对真实库给出正确结论**

Run: `.venv/bin/python scripts/audit_coverage.py --db hs300.db`
Expected: 输出体检区间与缺口列表（当前库 10-26/10-27 已补齐，应基本无缺口；若有则记录）

- [ ] **Step 4: 端到端验证 —— 幂等性（关键，在副本上做）**

对同一日期连续跑两次 `build-all`，确认 `trade_plan` 行数不变。
**必须在副本上做**，不要动工作区的 hs300.db。

```bash
cp hs300.db /tmp/idem.db
.venv/bin/python -c "
import sqlite3
c = sqlite3.connect('/tmp/idem.db')
print('before:', c.execute(\"SELECT COUNT(*) FROM trade_plan WHERE plan_date='2026-10-26'\").fetchone()[0])
"
.venv/bin/python stock_cli.py plan build-all --date 2026-10-26 --db /tmp/idem.db
.venv/bin/python -c "
import sqlite3
c = sqlite3.connect('/tmp/idem.db')
print('after :', c.execute(\"SELECT COUNT(*) FROM trade_plan WHERE plan_date='2026-10-26'\").fetchone()[0])
"
```
Expected: `before` 与 `after` 相同（params_hash 相同 → ON CONFLICT 更新而非追加）

- [ ] **Step 5: 端到端验证 —— 模拟缺口后能检出并修复（在副本上做）**

```bash
cp hs300.db /tmp/e2e.db
.venv/bin/python -c "
import sqlite3
c = sqlite3.connect('/tmp/e2e.db')
c.execute(\"DELETE FROM daily_picks WHERE date='2026-10-26'\")
c.execute(\"DELETE FROM trade_plan WHERE plan_date='2026-10-26'\")
c.commit()
print('已人为删除 10-26 的选股与计划')
"
.venv/bin/python scripts/audit_coverage.py --db /tmp/e2e.db
# 期望：报 [L2] 2026-10-26 无选股数据，退出码 1
.venv/bin/python scripts/repair_gaps.py --db /tmp/e2e.db --out /tmp/e2e-repair.json
# 期望：执行 pick_history + plan build/build-all，退出码 0
.venv/bin/python scripts/audit_coverage.py --db /tmp/e2e.db
# 期望：✅ 三层无缺口，退出码 0
cat /tmp/e2e-repair.json
```

- [ ] **Step 6: 端到端验证 —— 通知的三种结局各跑一次**

```bash
# a) 干净报告 → ✅ 心跳
.venv/bin/python scripts/audit_coverage.py --db hs300.db --out /tmp/ok.json
.venv/bin/python scripts/notify_audit.py --audit /tmp/ok.json --today 2026-10-27

# b) 有缺口且未修复 → 🚨
.venv/bin/python scripts/audit_coverage.py --db /tmp/e2e.db --out /tmp/gap.json
.venv/bin/python scripts/notify_audit.py --audit /tmp/gap.json --today 2026-10-27

# c) 无 DB → 🚨（绝不能是 ✅）
.venv/bin/python scripts/notify_audit.py --no-db --today 2026-10-27
```
Expected: 三者分别打印 ✅ / 🚨 / 🚨，且都不发网络请求（未配 NTFY_TOPIC 时只写 Step Summary）

- [ ] **Step 7: 提交**

```bash
git add CLAUDE.md
git commit -m "docs: 记录每日巡检链路与排查顺序

新增「每日巡检与自动补齐」章节：体检基准为何独立于库内数据、
三层判定规则、修复边界（7 天/转债/不重算当天）、复检必要性、
每日心跳的意义，以及手工使用方式。"
```

---

## 验收标准

完成全部 7 个 Task 后，以下全部成立：

- [ ] `.venv/bin/python -m pytest tests/ -q` 全绿（含新增 4 个测试文件）
- [ ] `python3 scripts/audit_coverage.py --db hs300.db` 能对当前库给出正确结论
- [ ] `python3 scripts/repair_gaps.py --db hs300.db --dry-run` 正确列出待修项
- [ ] `.github/workflows/daily-audit.yml` YAML 与所有 run 块语法通过
- [ ] 幂等性验证：同一日期跑两次 `build-all`，行数不变
- [ ] 模拟缺口验证：删掉某天选股/计划 → 体检检出 → 修复 → 复检通过
- [ ] CLAUDE.md 记录了巡检链路

## 已知限制（不在本计划范围）

- 转债历史无法自动补（数据源只有实时快照，`cb_pipeline` 已拒绝错标）
- 超过 7 天的行情缺口只报警不自动修（复权口径一致性）
- 巡检依赖 Actions cache 里有 `hs300.db`；若缓存彻底丢失，需先手动跑一次发布链路
