# Data Source Rate-Limit Bypass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add SourceHealthTracker to detect blocked data sources at sync start, skip them, and dynamically route to the first available source (sina/tencent when eastmoney is blocked).

**Architecture:** SourceHealthTracker probes each source once at sync start using a canary stock, builds a priority list, and tracks per-source failure counts during sync. fetch_daily_kline_smart() tries sources in priority order. sync_service integrates the tracker into _fetch_with_retry and sync_hs300_range.

**Tech Stack:** Python 3.10+, threading, unittest.mock

**Spec:** `docs/superpowers/specs/2026-10-25-data-source-rate-limit-design.md`

---

### Task 1: SourceHealthTracker — probe and ordering

**Files:**
- Modify: `data_providers.py` (append at end)
- Test: `tests/test_source_health.py` (create)

- [ ] **Step 1: Write failing tests**

Create `tests/test_source_health.py`:

```python
"""SourceHealthTracker: 数据源健康状态跟踪与动态优先级排序。"""
import threading
from unittest.mock import MagicMock, patch

import pytest

from data_providers import SourceHealthTracker


def _make_tracker():
    return SourceHealthTracker()


class TestProbe:

    def test_probe_marks_eastmoney_unavailable_when_blocked(self):
        """东财被封 → probe 标记不可用，优先级排到末尾。"""
        tracker = _make_tracker()
        with patch("data_providers.fetch_daily_kline_with_name") as mock_em, \
             patch("data_providers.fetch_daily_kline_sina") as mock_sina, \
             patch("data_providers.fetch_daily_kline_tencent") as mock_tx:
            mock_em.side_effect = ConnectionError("RemoteDisconnected")
            mock_sina.return_value = ([MagicMock()], "茅台")
            mock_tx.return_value = ([MagicMock()], "茅台")
            tracker.probe()

        assert tracker.is_available("eastmoney") is False
        assert tracker.is_available("sina") is True
        ordered = tracker.get_ordered_sources()
        assert ordered[0] == "sina"
        assert ordered[-1] == "eastmoney"

    def test_probe_all_available_keeps_default_order(self):
        """全部可用 → 保持默认顺序（eastmoney 优先）。"""
        tracker = _make_tracker()
        with patch("data_providers.fetch_daily_kline_with_name") as mock_em, \
             patch("data_providers.fetch_daily_kline_sina") as mock_sina, \
             patch("data_providers.fetch_daily_kline_tencent") as mock_tx:
            mock_em.return_value = ([MagicMock()], "茅台")
            mock_sina.return_value = ([MagicMock()], "茅台")
            mock_tx.return_value = ([MagicMock()], "茅台")
            tracker.probe()

        assert tracker.get_ordered_sources() == ["eastmoney", "sina", "tencent"]

    def test_probe_empty_rows_counts_as_failure(self):
        """源返回空列表 → 视为失败。"""
        tracker = _make_tracker()
        with patch("data_providers.fetch_daily_kline_with_name") as mock_em, \
             patch("data_providers.fetch_daily_kline_sina") as mock_sina, \
             patch("data_providers.fetch_daily_kline_tencent") as mock_tx:
            mock_em.return_value = ([], "")       # 空 = 失败
            mock_sina.return_value = ([MagicMock()], "茅台")
            mock_tx.return_value = ([MagicMock()], "茅台")
            tracker.probe()

        assert tracker.is_available("eastmoney") is False


class TestRecordFailure:

    def test_consecutive_failures_demote_source(self):
        """连续失败 MAX_CONSECUTIVE_FAILURES 次 → 降级到末尾。"""
        tracker = _make_tracker()
        for _ in range(tracker.MAX_CONSECUTIVE_FAILURES):
            tracker.record_failure("eastmoney")

        assert tracker.is_available("eastmoney") is False
        assert tracker.get_ordered_sources()[-1] == "eastmoney"

    def test_below_threshold_stays_available(self):
        """未达阈值 → 仍可用。"""
        tracker = _make_tracker()
        for _ in range(tracker.MAX_CONSECUTIVE_FAILURES - 1):
            tracker.record_failure("sina")
        assert tracker.is_available("sina") is True


class TestRecordSuccess:

    def test_success_resets_fail_count(self):
        """成功后 fail_count 归零。"""
        tracker = _make_tracker()
        tracker.record_failure("sina")
        tracker.record_failure("sina")
        assert tracker._health["sina"]["fail_count"] == 2

        tracker.record_success("sina")
        assert tracker._health["sina"]["fail_count"] == 0
        assert tracker.is_available("sina") is True


class TestThreadSafety:

    def test_concurrent_record_no_crash(self):
        """多线程并发调用 record_* 不抛异常。"""
        tracker = _make_tracker()
        errors = []

        def worker():
            try:
                for _ in range(20):
                    tracker.record_failure("sina")
                    tracker.record_success("sina")
                    tracker.get_ordered_sources()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Thread errors: {errors}"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `python3 -m pytest tests/test_source_health.py -v`
Expected: FAIL — `ImportError: cannot import name 'SourceHealthTracker' from 'data_providers'`

- [ ] **Step 3: Implement SourceHealthTracker**

Append to `data_providers.py`:

```python
import threading


class SourceHealthTracker:
    """跟踪各数据源的可用性，动态调整优先级。

    同步开始前用探针股票测试各源（probe），同步过程中持续记录
    失败次数（record_failure），超过阈值自动降级到列表末尾。
    """

    SOURCES = ["eastmoney", "sina", "tencent"]
    PROBE_CODES = ["600519"]  # 贵州茅台：流动性好、不会退市
    MAX_CONSECUTIVE_FAILURES = 3

    def __init__(self) -> None:
        self._health: Dict[str, Dict] = {
            name: {"available": True, "fail_count": 0}
            for name in self.SOURCES
        }
        self._ordered: List[str] = list(self.SOURCES)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # probe
    # ------------------------------------------------------------------

    def probe(self) -> None:
        """同步开始前，用探针股票测试各源，建立初始优先级。"""
        results: Dict[str, bool] = {}
        for source in self.SOURCES:
            try:
                rows = self._try_source(
                    source, self.PROBE_CODES[0], "20261001", "20261010"
                )
                results[source] = bool(rows)
            except Exception:
                results[source] = False

        available = [s for s in self.SOURCES if results.get(s)]
        unavailable = [s for s in self.SOURCES if not results.get(s)]

        with self._lock:
            self._ordered = available + unavailable
            for s in available:
                self._health[s]["available"] = True
                self._health[s]["fail_count"] = 0
            for s in unavailable:
                self._health[s]["available"] = False
                self._health[s]["fail_count"] = 0

    # ------------------------------------------------------------------
    # query
    # ------------------------------------------------------------------

    def get_ordered_sources(self) -> List[str]:
        """返回当前优先级排序的源列表（副本）。"""
        with self._lock:
            return self._ordered.copy()

    def is_available(self, source: str) -> bool:
        """查询某源当前是否可用。"""
        with self._lock:
            return self._health.get(source, {}).get("available", False)

    # ------------------------------------------------------------------
    # mutation
    # ------------------------------------------------------------------

    def record_success(self, source: str) -> None:
        """记录成功，重置 fail_count。"""
        with self._lock:
            if source in self._health:
                self._health[source]["fail_count"] = 0
                self._health[source]["available"] = True

    def record_failure(self, source: str) -> None:
        """记录失败；连续失败超阈值则降级到列表末尾。"""
        with self._lock:
            if source not in self._health:
                return
            self._health[source]["fail_count"] += 1
            if (
                self._health[source]["fail_count"]
                >= self.MAX_CONSECUTIVE_FAILURES
            ):
                self._health[source]["available"] = False
                if source in self._ordered:
                    self._ordered.remove(source)
                    self._ordered.append(source)

    # ------------------------------------------------------------------
    # internal
    # ------------------------------------------------------------------

    def _try_source(
        self, source: str, code: str, start: str, end: str
    ) -> List[PriceRow]:
        """用指定源获取数据，返回行列表（空=失败）。"""
        if source == "eastmoney":
            rows, _ = fetch_daily_kline_with_name(code, start, end)
        elif source == "sina":
            rows, _ = fetch_daily_kline_sina(code, start, end)
        elif source == "tencent":
            rows, _ = fetch_daily_kline_tencent(code, start, end)
        else:
            return []
        return rows
```

- [ ] **Step 4: Run tests to verify pass**

Run: `python3 -m pytest tests/test_source_health.py -v`
Expected: PASS (all 7 tests)

- [ ] **Step 5: Commit**

```bash
git add data_providers.py tests/test_source_health.py
git commit -m "feat(sync): SourceHealthTracker probes sources and tracks health"
```

---

### Task 2: fetch_daily_kline_smart — priority-ordered fetcher

**Files:**
- Modify: `data_providers.py` (append after SourceHealthTracker)
- Test: `tests/test_source_health.py` (append)

- [ ] **Step 1: Write failing tests**

Append to `tests/test_source_health.py`:

```python
from data_providers import fetch_daily_kline_smart


class TestFetchDailyKlineSmart:

    def _make_tracker_with_priority(self, available_sources):
        """构造一个 tracker，只有 available_sources 中的源可用。"""
        tracker = SourceHealthTracker()
        for s in tracker.SOURCES:
            tracker._health[s]["available"] = s in available_sources
        tracker._ordered = [
            s for s in tracker.SOURCES if s in available_sources
        ] + [s for s in tracker.SOURCES if s not in available_sources]
        return tracker

    def test_skips_unavailable_source(self):
        """eastmoney 不可用 → 直接调 sina，不调 eastmoney。"""
        tracker = self._make_tracker_with_priority(["sina", "tencent"])
        with patch("data_providers.fetch_daily_kline_with_name") as mock_em, \
             patch("data_providers.fetch_daily_kline_sina") as mock_sina:
            mock_sina.return_value = ([MagicMock()], "茅台")
            rows, name = fetch_daily_kline_smart(
                "600519", "20261001", "20261024", tracker
            )
        mock_em.assert_not_called()
        mock_sina.assert_called_once()
        assert len(rows) > 0

    def test_falls_through_on_first_source_failure(self):
        """第一个可用源失败 → 自动尝试下一个。"""
        tracker = self._make_tracker_with_priority(
            ["eastmoney", "sina", "tencent"]
        )
        with patch("data_providers.fetch_daily_kline_with_name") as mock_em, \
             patch("data_providers.fetch_daily_kline_sina") as mock_sina:
            mock_em.side_effect = ConnectionError("blocked")
            mock_sina.return_value = ([MagicMock()], "茅台")
            rows, name = fetch_daily_kline_smart(
                "600519", "20261001", "20261024", tracker
            )
        assert len(rows) > 0
        assert tracker._health["eastmoney"]["fail_count"] == 1

    def test_all_sources_fail_returns_empty(self):
        """所有源都失败 → 返回 ([], '')。"""
        tracker = self._make_tracker_with_priority(
            ["eastmoney", "sina", "tencent"]
        )
        with patch("data_providers.fetch_daily_kline_with_name") as mock_em, \
             patch("data_providers.fetch_daily_kline_sina") as mock_sina, \
             patch("data_providers.fetch_daily_kline_tencent") as mock_tx:
            mock_em.side_effect = Exception("fail")
            mock_sina.side_effect = Exception("fail")
            mock_tx.side_effect = Exception("fail")
            rows, name = fetch_daily_kline_smart(
                "600519", "20261001", "20261024", tracker
            )
        assert rows == []
        assert name == ""

    def test_empty_rows_counts_as_failure_and_falls_through(self):
        """源返回空列表 → 视为失败，尝试下一个源。"""
        tracker = self._make_tracker_with_priority(
            ["eastmoney", "sina"]
        )
        with patch("data_providers.fetch_daily_kline_with_name") as mock_em, \
             patch("data_providers.fetch_daily_kline_sina") as mock_sina:
            mock_em.return_value = ([], "")  # 空 = 失败
            mock_sina.return_value = ([MagicMock()], "茅台")
            rows, name = fetch_daily_kline_smart(
                "600519", "20261001", "20261024", tracker
            )
        assert len(rows) > 0
        mock_sina.assert_called_once()
```

- [ ] **Step 2: Run new tests to verify failure**

Run: `python3 -m pytest tests/test_source_health.py::TestFetchDailyKlineSmart -v`
Expected: FAIL — `ImportError: cannot import name 'fetch_daily_kline_smart' from 'data_providers'`

- [ ] **Step 3: Implement fetch_daily_kline_smart**

Append to `data_providers.py` (after SourceHealthTracker):

```python
def fetch_daily_kline_smart(
    code: str,
    start_date: str,
    end_date: str,
    tracker: SourceHealthTracker,
) -> tuple:
    """按 tracker 优先级依次尝试各数据源。

    第一个返回非空行的源胜出。每次成功/失败都反馈给 tracker。
    所有可用源都失败时返回 ([], "")。
    """
    for source in tracker.get_ordered_sources():
        if not tracker.is_available(source):
            continue
        try:
            if source == "eastmoney":
                rows, name = fetch_daily_kline_with_name(code, start_date, end_date)
            elif source == "sina":
                rows, name = fetch_daily_kline_sina(code, start_date, end_date)
            elif source == "tencent":
                rows, name = fetch_daily_kline_tencent(code, start_date, end_date)
            else:
                continue

            if rows:
                tracker.record_success(source)
                return rows, name
            else:
                tracker.record_failure(source)
        except Exception:
            tracker.record_failure(source)

    return [], ""
```

- [ ] **Step 4: Run all tests in file to verify pass**

Run: `python3 -m pytest tests/test_source_health.py -v`
Expected: PASS (all 11 tests)

- [ ] **Step 5: Commit**

```bash
git add data_providers.py tests/test_source_health.py
git commit -m "feat(sync): fetch_daily_kline_smart tries sources in priority order"
```

---

### Task 3: sync_service integration

**Files:**
- Modify: `sync_service.py` (two functions)
- Test: `tests/test_source_health.py` (append one integration test)

- [ ] **Step 1: Write failing integration test**

Append to `tests/test_source_health.py`:

```python
class TestSyncServiceIntegration:

    def test_fetch_with_retry_uses_smart_fetcher(self, tmp_path):
        """_fetch_with_retry 调用 fetch_daily_kline_smart 而非 fetch_daily_kline_with_name。"""
        from sync_service import _fetch_with_retry, RateLimiter
        from data_providers import SourceHealthTracker

        tracker = SourceHealthTracker()
        tracker.probe = MagicMock()  # skip probe
        limiter = RateLimiter(rate_per_sec=1000)

        with patch(
            "sync_service.fetch_daily_kline_smart"
        ) as mock_smart:
            mock_smart.return_value = ([MagicMock()], "test")
            rows, name = _fetch_with_retry(
                "600519", "20261001", "20261024",
                retries=0, backoff=0.1,
                limiter=limiter, tracker=tracker,
            )
        mock_smart.assert_called_once()
        assert len(rows) > 0
```

- [ ] **Step 2: Run test to verify failure**

Run: `python3 -m pytest tests/test_source_health.py::TestSyncServiceIntegration -v`
Expected: FAIL — `TypeError: _fetch_with_retry() got an unexpected keyword argument 'tracker'`

- [ ] **Step 3: Modify _fetch_with_retry**

In `sync_service.py`, replace the `_fetch_with_retry` function (lines 58-79):

```python
def _fetch_with_retry(
    code: str,
    start_str: str,
    end_str: str,
    retries: int,
    backoff: float,
    limiter: RateLimiter,
    tracker: "SourceHealthTracker",  # noqa: F821
) -> Tuple[List, str]:
    last_error: Optional[Exception] = None
    for attempt in range(retries + 1):
        try:
            limiter.wait()
            return fetch_daily_kline_smart(code, start_str, end_str, tracker)
        except Exception as exc:
            last_error = exc
            if attempt >= retries:
                break
            sleep_for = backoff * (2 ** attempt)
            time.sleep(sleep_for)
    if last_error:
        raise last_error
    return [], ""
```

Add to imports at top of `sync_service.py`:

```python
from data_providers import (
    fetch_daily_kline_smart,
    SourceHealthTracker,
)
```

- [ ] **Step 4: Modify sync_hs300_range**

In `sync_service.py`, find `sync_hs300_range` function. Add before the `ThreadPoolExecutor` block (around line 291):

```python
    # 新增：探针测试各数据源，建立优先级
    tracker = SourceHealthTracker()
    tracker.probe()
    if progress:
        progress(1, f"数据源优先级: {' > '.join(tracker.get_ordered_sources())}")
```

Then in the `ThreadPoolExecutor` submit call, add `tracker` as the last argument to `_fetch_with_retry`:

```python
                futures = {
                    executor.submit(
                        _fetch_with_retry,
                        code,
                        segment[0],
                        segment[1],
                        retries,
                        backoff,
                        limiter,
                        tracker,  # 新增
                    ): (code, segment)
                    for code, segments in candidates
                    for segment in segments
                }
```

- [ ] **Step 5: Run integration test**

Run: `python3 -m pytest tests/test_source_health.py::TestSyncServiceIntegration -v`
Expected: PASS

- [ ] **Step 6: Run full suite to check no regressions**

Run: `python3 -m pytest tests/ -q`
Expected: PASS (baseline + 12 new tests, 0 regressions)

- [ ] **Step 7: Commit**

```bash
git add sync_service.py tests/test_source_health.py
git commit -m "feat(sync): integrate SourceHealthTracker into sync_hs300_range"
```

---

### Task 4: Real-world verification

**Files:** None (verification only)

- [ ] **Step 1: Verify priority detection**

```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
python3 -c "
from data_providers import SourceHealthTracker
t = SourceHealthTracker()
t.probe()
print('Priority:', ' > '.join(t.get_ordered_sources()))
print('Eastmoney available:', t.is_available('eastmoney'))
"
```

Expected output (eastmoney currently blocked):
```
Priority: sina > tencent > eastmoney
Eastmoney available: False
```

- [ ] **Step 2: Run real sync**

```bash
python3 stock_cli.py sync-hs300-range \
  --start 2026-04-25 --end 2026-10-24 \
  --db hs300.db \
  --concurrency 4 --rate 6 \
  2>&1 | grep -E "数据源优先级|同步完成|rows="
```

Expected:
- Log line: `数据源优先级: sina > tencent > eastmoney`
- Final: `rows=NNNNN` (non-zero, not 0)

- [ ] **Step 3: Verify data landed**

```bash
python3 -c "
import sqlite3
c = sqlite3.connect('hs300.db')
r = c.execute('SELECT MIN(trade_date), MAX(trade_date), COUNT(*) FROM daily_prices').fetchone()
print(f'daily_prices: {r[0]} ~ {r[1]}, {r[2]} rows')
c.close()
"
```

Expected: `MIN ≤ 2026-04-25, MAX = 2026-10-23, rows >> 3000`

- [ ] **Step 4: Backfill picks and push site**

```bash
python3 -c "
import sqlite3
from pick_history import run_picks
conn = sqlite3.connect('hs300.db')
price_dates = set(r[0] for r in conn.execute('SELECT DISTINCT trade_date FROM daily_prices'))
pick_dates = set(r[0] for r in conn.execute('SELECT DISTINCT date FROM daily_picks'))
missing = sorted(price_dates - pick_dates, reverse=True)
conn.close()
for d in missing:
    r = run_picks('hs300.db', top=20, do_sync=False, trade_date=d)
    print(f'{d}: ma={r.get(\"ma\",0)} buy={r.get(\"buy\",0)} signal={r.get(\"signal\",0)} multi={r.get(\"multi\",0)}')
"
```

Then regenerate and push the static site.

---

## Self-Review

**Spec coverage:**
- ✅ §三 Architecture → Tasks 1-3
- ✅ §三 SourceHealthTracker → Task 1
- ✅ §三 fetch_daily_kline_smart → Task 2
- ✅ §三 sync_service integration → Task 3
- ✅ §五 Testing → Tasks 1-3 tests + Task 4 real-world
- ✅ §五 Integration verification → Task 4

**Placeholder scan:** No TBD/TODO/FIXME.

**Type consistency:**
- `SourceHealthTracker.SOURCES` / `PROBE_CODES` / `MAX_CONSECUTIVE_FAILURES` — consistent across Tasks 1-3
- `fetch_daily_kline_smart(code, start_date, end_date, tracker)` — same signature in Task 2 and Task 3
- `_fetch_with_retry(..., tracker)` — same parameter name in Task 3 test and implementation
- `tracker.is_available(source)` / `tracker.get_ordered_sources()` / `tracker.record_success()` / `tracker.record_failure()` — consistent API across all tasks
