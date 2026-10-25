# 10w 决策台（/decide）Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新建 `/decide` 页面，展示 10w 今日执行清单 + 计划vs实际的执行对照表，支持手工录入真实持仓。

**Architecture:** 服务端 `decide_service.py` 做纯函数判定（规则集中在 `config.PORTFOLIO_10W`）；`real_position_repository.py` 存用户录入；`/api/decide/*` 提供 plan/positions/comparison；前端 `static/js/decide.js` 渲染。静态站只读（导出快照）。

**Tech Stack:** Flask, Jinja2, Vanilla ES6 modules, SQLite

**Spec:** `docs/superpowers/specs/2026-10-25-10w-decide-dashboard-design.md`

---

### Task 1: config + 迁移 + 实际持仓仓储

**Files:**
- Modify: `config.py` (append `PORTFOLIO_10W`)
- Create: `db/migrations/2026_10_25_real_positions.sql`
- Create: `real_position_repository.py`
- Test: `tests/test_decide.py` (create — first file of this feature)

- [ ] **Step 1: Write the failing test**

Create `tests/test_decide.py`:

```python
"""10w 决策台：实际持仓仓储 + 对照判定逻辑。"""
import os
import tempfile

import pytest


@pytest.fixture
def db():
    from db_repository import open_db
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = open_db(path)
    conn.close()
    yield path
    os.unlink(path)


class TestRealPositionRepository:

    def test_upsert_and_get_stock_position(self, db):
        from db_repository import open_db
        from real_position_repository import (
            delete_real_position, get_real_positions, upsert_real_position,
        )
        conn = open_db(db)
        try:
            upsert_real_position(conn, {
                "code": "600519", "name": "贵州茅台", "shares": 100,
                "entry_price": 1500.0, "stop_price": 1400.0,
                "entry_date": "2026-10-20", "note": "",
            })
            rows = get_real_positions(conn)
            assert len(rows) == 1
            assert rows[0]["code"] == "600519"
            assert rows[0]["shares"] == 100
            assert rows[0]["entry_price"] == 1500.0

            # 再次写入应更新而非新增
            upsert_real_position(conn, {
                "code": "600519", "name": "贵州茅台", "shares": 200,
                "entry_price": 1520.0, "stop_price": None,
                "entry_date": "2026-10-20", "note": "加仓",
            })
            rows = get_real_positions(conn)
            assert len(rows) == 1
            assert rows[0]["shares"] == 200
            assert rows[0]["stop_price"] is None

            delete_real_position(conn, "600519")
            assert get_real_positions(conn) == []
        finally:
            conn.close()

    def test_upsert_requires_code_shares_entry_price(self, db):
        from db_repository import open_db
        from real_position_repository import upsert_real_position
        conn = open_db(db)
        try:
            with pytest.raises(ValueError):
                upsert_real_position(conn, {"code": "", "shares": 10, "entry_price": 1.0})
            with pytest.raises(ValueError):
                upsert_real_position(conn, {"code": "600519", "shares": 0, "entry_price": 1.0})
            with pytest.raises(ValueError):
                upsert_real_position(conn, {"code": "600519", "shares": 10, "entry_price": None})
        finally:
            conn.close()

    def test_cb_position_roundtrip(self, db):
        from db_repository import open_db
        from real_position_repository import (
            delete_real_cb_position, get_real_cb_positions, upsert_real_cb_position,
        )
        conn = open_db(db)
        try:
            upsert_real_cb_position(conn, {
                "code": "113059", "name": "转债A", "shares": 50,
                "entry_price": 108.5, "stop_price": 99.8,
                "entry_date": "2026-10-20", "note": "",
            })
            rows = get_real_cb_positions(conn)
            assert len(rows) == 1
            assert rows[0]["code"] == "113059"
            assert rows[0]["shares"] == 50

            delete_real_cb_position(conn, "113059")
            assert get_real_cb_positions(conn) == []
        finally:
            conn.close()


class TestConfig:

    def test_portfolio_10w_config(self):
        from config import PORTFOLIO_10W
        assert PORTFOLIO_10W["total_capital"] == 100000.0
        assert PORTFOLIO_10W["stock_ratio"] == 0.8
        assert PORTFOLIO_10W["cb_ratio"] == 0.2
        assert PORTFOLIO_10W["max_positions"] == 8
        assert PORTFOLIO_10W["max_single_weight"] == 0.10
        assert PORTFOLIO_10W["max_stock_exposure"] == 0.75
        assert PORTFOLIO_10W["cb_capital"] == 20000.0
        assert PORTFOLIO_10W["max_cb_positions"] == 3
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 -m pytest tests/test_decide.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'real_position_repository'`

(Use `python3 -m pytest`, NOT `uv run pytest` — uv fails in this sandbox with a cache permission error.)

- [ ] **Step 3: Add config block**

Append to `config.py`:

```python
# ---------------------------------------------------------------------------
# 10w 决策台：专属配置（报告 docs/reports/10w-decision-system.md 第 4/5 章）
# 与全局 MAX_SINGLE / MAX_POSITIONS 等区分——这是 10w 本金场景的独立参数，
# 不影响其他资金档位的 plan 生成。
# ---------------------------------------------------------------------------
PORTFOLIO_10W = {
    "total_capital": 100000.0,
    "stock_ratio": 0.8,
    "cb_ratio": 0.2,
    "max_positions": 8,
    "max_single_weight": 0.10,
    "max_stock_exposure": 0.75,
    "cb_capital": 20000.0,
    "max_cb_positions": 3,
}
```

- [ ] **Step 4: Create migration**

Create `db/migrations/2026_10_25_real_positions.sql`:

```sql
-- db/migrations/2026_10_25_real_positions.sql
-- 10w 决策台：用户手工录入的真实持仓（区别于平台模拟的 trade_plan）
CREATE TABLE IF NOT EXISTS real_positions (
    code          TEXT PRIMARY KEY,
    name          TEXT,
    shares        INTEGER NOT NULL,
    entry_price   REAL NOT NULL,
    stop_price    REAL,
    entry_date    TEXT,
    note          TEXT,
    updated_at    TEXT
);

CREATE TABLE IF NOT EXISTS real_cb_positions (
    code          TEXT PRIMARY KEY,
    name          TEXT,
    shares        INTEGER NOT NULL,
    entry_price   REAL NOT NULL,
    stop_price    REAL,
    entry_date    TEXT,
    note          TEXT,
    updated_at    TEXT
);
```

- [ ] **Step 5: Create repository**

Create `real_position_repository.py`:

```python
"""10w 决策台：用户真实持仓的读写。

与 cb_repository 同风格——SAVEPOINT 原子化写入，不用裸 rollback()。
表名带 real_ 前缀，与平台模拟的 trade_plan 明确区分。
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Any, Dict, List

_SAVEPOINT = "sp_real_position"


def _now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _begin(conn: sqlite3.Connection) -> None:
    conn.execute(f"SAVEPOINT {_SAVEPOINT}")


def _rollback_to(conn: sqlite3.Connection) -> None:
    conn.execute(f"ROLLBACK TO SAVEPOINT {_SAVEPOINT}")
    conn.execute(f"RELEASE SAVEPOINT {_SAVEPOINT}")


def _release(conn: sqlite3.Connection) -> None:
    conn.execute(f"RELEASE SAVEPOINT {_SAVEPOINT}")


def _validated(row: Dict[str, Any]) -> tuple:
    code = str(row.get("code") or "").strip()
    if not code:
        raise ValueError("position requires code")
    if row.get("shares") is None:
        raise ValueError("position requires shares")
    shares = int(row["shares"])
    if shares <= 0:
        raise ValueError("position requires shares > 0")
    if row.get("entry_price") is None:
        raise ValueError("position requires entry_price")
    entry_price = float(row["entry_price"])
    if entry_price <= 0:
        raise ValueError("position requires entry_price > 0")
    stop = row.get("stop_price")
    stop_price = float(stop) if stop is not None else None
    return code, shares, entry_price, stop_price


# ---------------------------------------------------------------------------
# 股票
# ---------------------------------------------------------------------------

def upsert_real_position(conn: sqlite3.Connection, row: Dict[str, Any]) -> None:
    code, shares, entry_price, stop_price = _validated(row)
    _begin(conn)
    try:
        conn.execute(
            """INSERT INTO real_positions
               (code, name, shares, entry_price, stop_price, entry_date, note, updated_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(code) DO UPDATE SET
                 name=excluded.name, shares=excluded.shares,
                 entry_price=excluded.entry_price, stop_price=excluded.stop_price,
                 entry_date=excluded.entry_date, note=excluded.note,
                 updated_at=excluded.updated_at""",
            (code, row.get("name"), shares, entry_price, stop_price,
             row.get("entry_date"), row.get("note"), _now()),
        )
        _release(conn)
        conn.commit()
    except Exception:
        _rollback_to(conn)
        raise


def get_real_positions(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    cur = conn.execute("SELECT * FROM real_positions ORDER BY code")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def delete_real_position(conn: sqlite3.Connection, code: str) -> None:
    _begin(conn)
    try:
        conn.execute("DELETE FROM real_positions WHERE code=?", (str(code),))
        _release(conn)
        conn.commit()
    except Exception:
        _rollback_to(conn)
        raise


# ---------------------------------------------------------------------------
# 可转债
# ---------------------------------------------------------------------------

def upsert_real_cb_position(conn: sqlite3.Connection, row: Dict[str, Any]) -> None:
    code, shares, entry_price, stop_price = _validated(row)
    _begin(conn)
    try:
        conn.execute(
            """INSERT INTO real_cb_positions
               (code, name, shares, entry_price, stop_price, entry_date, note, updated_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(code) DO UPDATE SET
                 name=excluded.name, shares=excluded.shares,
                 entry_price=excluded.entry_price, stop_price=excluded.stop_price,
                 entry_date=excluded.entry_date, note=excluded.note,
                 updated_at=excluded.updated_at""",
            (code, row.get("name"), shares, entry_price, stop_price,
             row.get("entry_date"), row.get("note"), _now()),
        )
        _release(conn)
        conn.commit()
    except Exception:
        _rollback_to(conn)
        raise


def get_real_cb_positions(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    cur = conn.execute("SELECT * FROM real_cb_positions ORDER BY code")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def delete_real_cb_position(conn: sqlite3.Connection, code: str) -> None:
    _begin(conn)
    try:
        conn.execute("DELETE FROM real_cb_positions WHERE code=?", (str(code),))
        _release(conn)
        conn.commit()
    except Exception:
        _rollback_to(conn)
        raise
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_decide.py -v`
Expected: PASS (4 tests)

- [ ] **Step 7: Commit**

```bash
git add config.py db/migrations/2026_10_25_real_positions.sql real_position_repository.py tests/test_decide.py
git commit -m "feat(decide): 10w 配置块 + 真实持仓表迁移 + 仓储层"
```

---

### Task 2: 对照判定逻辑（decide_service.py）

**Files:**
- Create: `decide_service.py`
- Test: `tests/test_decide.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_decide.py`:

```python
class TestCompare:

    def _plan(self, action="buy", code="600519", shares=100, plan_price=100.0,
              stop=95.0, tp=110.0):
        return {
            "code": code, "name": "测试股", "action": action,
            "plan_price": plan_price, "shares": shares,
            "stop_price": stop, "tp_price": tp,
        }

    def _actual(self, code="600519", shares=100, entry_price=100.0, stop=95.0):
        return {
            "code": code, "name": "测试股", "shares": shares,
            "entry_price": entry_price, "stop_price": stop,
        }

    def test_no_deviations_when_perfect_match(self):
        from decide_service import compare_plan_vs_actual
        d = compare_plan_vs_actual(
            plan_rows=[self._plan()], actual_rows=[self._actual()],
            cb_rows=[], latest_prices={"600519": 100.0},
        )
        assert d["deviations"] == []
        assert d["stock_used"] == 10000.0
        assert d["cb_used"] == 0.0
        assert d["cash"] == 90000.0

    def test_detects_missing_buy(self):
        from decide_service import compare_plan_vs_actual
        d = compare_plan_vs_actual(
            plan_rows=[self._plan(action="buy")], actual_rows=[],
            cb_rows=[], latest_prices={},
        )
        kinds = [x["kind"] for x in d["deviations"]]
        assert "missing_buy" in kinds

    def test_detects_unplanned_position(self):
        from decide_service import compare_plan_vs_actual
        d = compare_plan_vs_actual(
            plan_rows=[], actual_rows=[self._actual(code="000001")],
            cb_rows=[], latest_prices={},
        )
        kinds = [x["kind"] for x in d["deviations"]]
        assert "unplanned" in kinds

    def test_detects_too_many_positions(self):
        from decide_service import compare_plan_vs_actual
        actual = [self._actual(code=f"60000{i}") for i in range(9)]
        d = compare_plan_vs_actual(
            plan_rows=[], actual_rows=actual, cb_rows=[], latest_prices={},
        )
        kinds = [x["kind"] for x in d["deviations"]]
        assert "too_many_positions" in kinds

    def test_detects_single_weight_over_limit(self):
        from decide_service import compare_plan_vs_actual
        # 1 只票占 15000 / 100000 = 15% > 10%
        d = compare_plan_vs_actual(
            plan_rows=[], actual_rows=[self._actual(shares=150, entry_price=100.0)],
            cb_rows=[], latest_prices={},
        )
        kinds = [x["kind"] for x in d["deviations"]]
        assert "single_weight_over" in kinds

    def test_detects_missing_stop_loss(self):
        from decide_service import compare_plan_vs_actual
        d = compare_plan_vs_actual(
            plan_rows=[self._plan()], actual_rows=[self._actual(stop=None)],
            cb_rows=[], latest_prices={"600519": 100.0},
        )
        kinds = [x["kind"] for x in d["deviations"]]
        assert "missing_stop" in kinds

    def test_detects_stop_loss_breached(self):
        from decide_service import compare_plan_vs_actual
        d = compare_plan_vs_actual(
            plan_rows=[self._plan()], actual_rows=[self._actual(stop=95.0)],
            cb_rows=[], latest_prices={"600519": 90.0},
        )
        kinds = [x["kind"] for x in d["deviations"]]
        assert "stop_breached" in kinds

    def test_detects_total_exposure_over_limit(self):
        from decide_service import compare_plan_vs_actual
        # 8 只票各 10000 元 = 80000 > 75000
        actual = [self._actual(code=f"60000{i}", shares=100, entry_price=100.0)
                  for i in range(8)]
        d = compare_plan_vs_actual(
            plan_rows=[], actual_rows=actual, cb_rows=[], latest_prices={},
        )
        kinds = [x["kind"] for x in d["deviations"]]
        assert "exposure_over" in kinds

    def test_detects_cb_over_allocation(self):
        from decide_service import compare_plan_vs_actual
        cb = [{"code": "113059", "name": "转债A", "shares": 200,
               "entry_price": 110.0, "stop_price": None}]
        d = compare_plan_vs_actual(
            plan_rows=[], actual_rows=[], cb_rows=cb, latest_prices={},
        )
        kinds = [x["kind"] for x in d["deviations"]]
        assert "cb_over" in kinds

    def test_detects_take_profit_hit(self):
        from decide_service import compare_plan_vs_actual
        d = compare_plan_vs_actual(
            plan_rows=[self._plan(tp=110.0)], actual_rows=[self._actual()],
            cb_rows=[], latest_prices={"600519": 115.0},
        )
        kinds = [x["kind"] for x in d["deviations"]]
        assert "tp_hit" in kinds

    def test_duplicate_plan_rows_deduped(self):
        """同一 code 的重复 plan 行只算一次（10W 档位历史数据有重复）。"""
        from decide_service import compare_plan_vs_actual
        plan = [self._plan(action="buy"), self._plan(action="buy"),
                self._plan(action="buy")]
        d = compare_plan_vs_actual(
            plan_rows=plan, actual_rows=[self._actual()],
            cb_rows=[], latest_prices={"600519": 100.0},
        )
        missing = [x for x in d["deviations"] if x["kind"] == "missing_buy"]
        assert missing == []
        assert d["plan_count"] == 1

    def test_cash_remaining_calculation(self):
        from decide_service import compare_plan_vs_actual
        actual = [self._actual(shares=100, entry_price=100.0)]
        cb = [{"code": "113059", "name": "转债A", "shares": 50,
               "entry_price": 110.0, "stop_price": None}]
        d = compare_plan_vs_actual(
            plan_rows=[], actual_rows=actual, cb_rows=cb, latest_prices={},
        )
        assert d["stock_used"] == 10000.0
        assert d["cb_used"] == 5500.0
        assert d["cash"] == 100000.0 - 10000.0 - 5500.0

    def test_deviation_severity_levels(self):
        from decide_service import compare_plan_vs_actual
        d = compare_plan_vs_actual(
            plan_rows=[self._plan(action="buy")], actual_rows=[],
            cb_rows=[], latest_prices={},
        )
        assert d["deviations"][0]["severity"] in ("info", "warn", "danger")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_decide.py::TestCompare -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'decide_service'`

- [ ] **Step 3: Implement decide_service.py**

Create `decide_service.py`:

```python
"""10w 决策台：计划 vs 实际的对照判定。

纯函数——输入 plan/actual/config，输出偏差列表。不碰数据库，便于
静态导出复用同一份实现。规则来源：docs/reports/10w-decision-system.md
第 4 章（仓位）与第 5 章（退出）。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

from config import PORTFOLIO_10W


def _num(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f


def dedupe_plan_rows(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按 code 去重，同 code 保留最后一条（trade_plan 回补会产生重复行）。"""
    seen: Dict[str, Dict[str, Any]] = {}
    order: List[str] = []
    for r in rows:
        code = str(r.get("code") or "").strip()
        if not code:
            continue
        if code not in seen:
            order.append(code)
        seen[code] = r
    return [seen[c] for c in order]


def _dev(severity: str, kind: str, code: str, name: str,
         message: str, actual: Any = None, expected: Any = None) -> Dict[str, Any]:
    return {
        "severity": severity, "kind": kind, "code": code, "name": name or code,
        "message": message, "actual": actual, "expected": expected,
    }


def compare_plan_vs_actual(
    plan_rows: Iterable[Dict[str, Any]],
    actual_rows: Iterable[Dict[str, Any]],
    cb_rows: Iterable[Dict[str, Any]],
    latest_prices: Dict[str, float],
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """对照计划与实际持仓，返回偏差列表 + 资金占用汇总。"""
    cfg = config or PORTFOLIO_10W
    total_capital = float(cfg["total_capital"])

    plan = dedupe_plan_rows(plan_rows)
    actual = list(actual_rows or [])
    cb = list(cb_rows or [])

    plan_by_code = {str(r.get("code")): r for r in plan}
    actual_by_code = {str(r.get("code")): r for r in actual}

    deviations: List[Dict[str, Any]] = []

    # ---- 资金占用 ----
    stock_used = 0.0
    for r in actual:
        amt = (_num(r.get("entry_price")) or 0.0) * int(r.get("shares") or 0)
        stock_used += amt
    cb_used = 0.0
    for r in cb:
        amt = (_num(r.get("entry_price")) or 0.0) * int(r.get("shares") or 0)
        cb_used += amt
    cash = total_capital - stock_used - cb_used

    # ---- 漏买 / 多买 ----
    for code, p in plan_by_code.items():
        if p.get("action") == "buy" and code not in actual_by_code:
            deviations.append(_dev(
                "info", "missing_buy", code, p.get("name"),
                "计划买入但未持有",
                actual=None, expected=p.get("shares"),
            ))
    for code, a in actual_by_code.items():
        if code not in plan_by_code:
            deviations.append(_dev(
                "warn", "unplanned", code, a.get("name"),
                "持有但计划中没有（超配）",
                actual=a.get("shares"), expected=0,
            ))

    # ---- 持仓数超限 ----
    if len(actual) > int(cfg["max_positions"]):
        deviations.append(_dev(
            "danger", "too_many_positions", "", "",
            f"持仓 {len(actual)} 只，超出上限 {cfg['max_positions']} 只",
            actual=len(actual), expected=int(cfg["max_positions"]),
        ))

    # ---- 单票超上限 ----
    max_single = float(cfg["max_single_weight"]) * total_capital
    for code, a in actual_by_code.items():
        amt = (_num(a.get("entry_price")) or 0.0) * int(a.get("shares") or 0)
        if amt > max_single:
            deviations.append(_dev(
                "danger", "single_weight_over", code, a.get("name"),
                f"单票占用 ¥{amt:,.0f}，超出 {cfg['max_single_weight']:.0%} 上限",
                actual=round(amt, 2), expected=round(max_single, 2),
            ))

    # ---- 缺止损 / 跌破止损 / 达止盈 ----
    for code, a in actual_by_code.items():
        stop = _num(a.get("stop_price"))
        price = _num(latest_prices.get(code))
        if stop is None:
            deviations.append(_dev(
                "warn", "missing_stop", code, a.get("name"),
                "未设置止损价",
                actual=None, expected=None,
            ))
        elif price is not None and price <= stop:
            deviations.append(_dev(
                "danger", "stop_breached", code, a.get("name"),
                f"最新价 {price:.2f} 已跌破止损 {stop:.2f}，应执行止损",
                actual=price, expected=stop,
            ))
        p = plan_by_code.get(code)
        tp = _num(p.get("tp_price")) if p else None
        if tp is not None and price is not None and price >= tp:
            deviations.append(_dev(
                "info", "tp_hit", code, a.get("name") or code,
                f"最新价 {price:.2f} 已达止盈 {tp:.2f}，可分批止盈",
                actual=price, expected=tp,
            ))

    # ---- 计划股数不符 ----
    for code, p in plan_by_code.items():
        a = actual_by_code.get(code)
        if a is None:
            continue
        want = p.get("shares")
        have = a.get("shares")
        if want is not None and have is not None and int(want) != int(have):
            deviations.append(_dev(
                "info", "shares_mismatch", code, p.get("name"),
                f"计划 {int(want)} 股，实际 {int(have)} 股",
                actual=int(have), expected=int(want),
            ))

    # ---- 总仓位超限 ----
    max_exposure = float(cfg["max_stock_exposure"]) * total_capital
    if stock_used > max_exposure:
        deviations.append(_dev(
            "danger", "exposure_over", "", "",
            f"股票占用 ¥{stock_used:,.0f}，超出 {cfg['max_stock_exposure']:.0%} 上限",
            actual=round(stock_used, 2), expected=round(max_exposure, 2),
        ))

    # ---- 转债仓超配 ----
    cb_capital = float(cfg["cb_capital"])
    if cb_used > cb_capital:
        deviations.append(_dev(
            "warn", "cb_over", "", "",
            f"转债占用 ¥{cb_used:,.0f}，超出 ¥{cb_capital:,.0f} 上限",
            actual=round(cb_used, 2), expected=cb_capital,
        ))
    if len(cb) > int(cfg["max_cb_positions"]):
        deviations.append(_dev(
            "warn", "cb_too_many", "", "",
            f"转债持仓 {len(cb)} 只，超出上限 {cfg['max_cb_positions']} 只",
            actual=len(cb), expected=int(cfg["max_cb_positions"]),
        ))

    sev_order = {"danger": 0, "warn": 1, "info": 2}
    deviations.sort(key=lambda x: sev_order.get(x["severity"], 3))

    return {
        "deviations": deviations,
        "stock_used": round(stock_used, 2),
        "cb_used": round(cb_used, 2),
        "cash": round(cash, 2),
        "total_capital": total_capital,
        "plan_count": len(plan),
        "actual_count": len(actual),
        "cb_count": len(cb),
        "danger_count": sum(1 for x in deviations if x["severity"] == "danger"),
        "warn_count": sum(1 for x in deviations if x["severity"] == "warn"),
        "info_count": sum(1 for x in deviations if x["severity"] == "info"),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_decide.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Commit**

```bash
git add decide_service.py tests/test_decide.py
git commit -m "feat(decide): 对照判定逻辑（10 类偏差 × 3 级别）"
```

---

### Task 3: Flask API 路由

**Files:**
- Modify: `app.py` (add routes inside `create_app`)
- Test: `tests/test_decide_web.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_decide_web.py`:

```python
"""10w 决策台：API 路由与页面。"""
import json
import os
import tempfile

import pytest


@pytest.fixture
def plan_db():
    """含 trade_plan 10W 档位（含重复行）的库。"""
    from db_repository import open_db
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = open_db(path)
    # 同一 code 重复 3 行（模拟回补产生的重复）
    for _ in range(3):
        conn.execute(
            "INSERT INTO trade_plan (plan_date, portfolio, code, action, plan_price, "
            "size_pct, stop_price, tp_price, status, reason, rationale_json, "
            "params_hash, created_at, shares) "
            "VALUES ('2026-10-25','10W','600519','buy',100.0,0.10,95.0,110.0,"
            "'ok','理由','{}','h','2026-10-25T00:00:00',100)"
        )
    conn.execute(
        "INSERT INTO trade_plan (plan_date, portfolio, code, action, plan_price, "
        "size_pct, stop_price, tp_price, status, reason, rationale_json, "
        "params_hash, created_at, shares) "
        "VALUES ('2026-10-25','10W','600036','buy',40.0,0.10,38.0,44.0,"
        "'ok','理由','{}','h','2026-10-25T00:00:00',200)"
    )
    conn.execute(
        "INSERT INTO hs300_metadata (code, name) VALUES ('600519','贵州茅台')"
    )
    conn.execute(
        "INSERT INTO hs300_metadata (code, name) VALUES ('600036','招商银行')"
    )
    conn.commit()
    conn.close()
    yield path
    os.unlink(path)


@pytest.fixture
def client(plan_db):
    from app import create_app
    app = create_app(db_path=plan_db)
    app.config["TESTING"] = True
    return app.test_client()


class TestDecideApi:

    def test_api_decide_plan_returns_deduped(self, client):
        resp = client.get("/api/decide/plan?date=2026-10-25")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["date"] == "2026-10-25"
        assert len(data["rows"]) == 2, "重复行必须去重"
        codes = sorted(r["code"] for r in data["rows"])
        assert codes == ["600036", "600519"]

    def test_api_decide_positions_crud(self, client):
        resp = client.post("/api/decide/positions", json={
            "code": "600519", "name": "贵州茅台", "shares": 100,
            "entry_price": 1500.0, "stop_price": 1400.0,
        })
        assert resp.status_code == 200

        resp = client.get("/api/decide/positions")
        assert resp.status_code == 200
        data = resp.get_json()
        assert len(data["rows"]) == 1
        assert data["rows"][0]["code"] == "600519"

        resp = client.delete("/api/decide/positions/600519")
        assert resp.status_code == 200
        assert client.get("/api/decide/positions").get_json()["rows"] == []

    def test_api_decide_positions_rejects_invalid(self, client):
        resp = client.post("/api/decide/positions", json={
            "code": "600519", "shares": 0, "entry_price": 100.0,
        })
        assert resp.status_code == 400

    def test_api_decide_comparison(self, client):
        client.post("/api/decide/positions", json={
            "code": "600519", "name": "贵州茅台", "shares": 100,
            "entry_price": 100.0, "stop_price": 95.0,
        })
        resp = client.get("/api/decide/comparison?date=2026-10-25")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "deviations" in data
        assert "stock_used" in data
        assert "cash" in data
        kinds = [d["kind"] for d in data["deviations"]]
        assert "missing_buy" in kinds, "600036 计划买入但未持有"

    def test_api_decide_comparison_empty_db(self):
        from db_repository import open_db
        from app import create_app
        fd, path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        open_db(path).close()
        app = create_app(db_path=path)
        resp = app.test_client().get("/api/decide/comparison")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["deviations"] == []
        os.unlink(path)


class TestDecidePage:

    def test_decide_page_renders(self, client):
        resp = client.get("/decide")
        assert resp.status_code == 200
        body = resp.data.decode("utf-8")
        assert "决策台" in body
        assert "10w" in body.lower()

    def test_decide_page_no_jquery_no_cdn(self, client):
        body = client.get("/decide").data.decode("utf-8").lower()
        assert "jquery" not in body
        assert "bootcdn" not in body

    def test_decide_page_loads_es6_module(self, client):
        body = client.get("/decide").data.decode("utf-8")
        assert 'type="module"' in body
        assert "js/decide.js" in body

    def test_nav_has_decide_link(self, client):
        body = client.get("/").data.decode("utf-8")
        assert 'data-nav="decide"' in body
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_decide_web.py -v`
Expected: FAIL — 404 on `/api/decide/plan`

- [ ] **Step 3: Implement the API routes**

In `app.py`, inside `create_app`, add before the `@app.get("/api/stats")` route:

```python
    # ------------------------------------------------------------------
    # 10w 决策台
    # ------------------------------------------------------------------

    def _decide_latest_plan_date(conn) -> str:
        row = conn.execute(
            "SELECT MAX(plan_date) FROM trade_plan WHERE portfolio='10W'"
        ).fetchone()
        return row[0] if row and row[0] else ""

    def _decide_plan_rows(conn, date):
        """10W 档位当日 plan，按 code 去重（trade_plan 回补会产生重复行）。"""
        from decide_service import dedupe_plan_rows
        from db_repository import get_trade_plan_by_date

        rows = [
            r for r in get_trade_plan_by_date(conn, date)
            if r.get("portfolio") == "10W" and r.get("status") != "failed"
        ]
        return dedupe_plan_rows(rows)

    def _decide_latest_prices(conn):
        """每只票的最新收盘价，用于止损/止盈判定。"""
        out = {}
        for code, close in conn.execute(
            "SELECT d.code, d.close FROM daily_prices d "
            "JOIN (SELECT code, MAX(trade_date) AS md FROM daily_prices GROUP BY code) x "
            "ON d.code = x.code AND d.trade_date = x.md"
        ):
            if close is not None:
                out[code] = float(close)
        return out

    @app.get("/api/decide/plan")
    def decide_plan():
        conn = open_conn(db_path)
        try:
            date = request.args.get("date", "") or _decide_latest_plan_date(conn)
            rows = _decide_plan_rows(conn, date) if date else []
            dates = [
                r[0] for r in conn.execute(
                    "SELECT DISTINCT plan_date FROM trade_plan WHERE portfolio='10W' "
                    "ORDER BY plan_date DESC"
                ).fetchall()
            ]
        finally:
            conn.close()
        return jsonify({"date": date, "rows": rows, "dates": dates})

    @app.get("/api/decide/positions")
    def decide_positions():
        from real_position_repository import get_real_cb_positions, get_real_positions
        conn = open_conn(db_path)
        try:
            rows = get_real_positions(conn)
            cb_rows = get_real_cb_positions(conn)
        finally:
            conn.close()
        return jsonify({"rows": rows, "cb_rows": cb_rows})

    @app.post("/api/decide/positions")
    def decide_positions_add():
        from real_position_repository import upsert_real_cb_position, upsert_real_position
        payload = request.get_json(silent=True) or {}
        is_cb = bool(payload.get("is_cb"))
        conn = open_conn(db_path)
        try:
            if is_cb:
                upsert_real_cb_position(conn, payload)
            else:
                upsert_real_position(conn, payload)
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        finally:
            conn.close()
        return jsonify({"ok": True})

    @app.delete("/api/decide/positions/<code>")
    def decide_positions_delete(code):
        from real_position_repository import delete_real_cb_position, delete_real_position
        conn = open_conn(db_path)
        try:
            delete_real_position(conn, code)
        finally:
            conn.close()
        return jsonify({"ok": True})

    @app.get("/api/decide/comparison")
    def decide_comparison():
        from decide_service import compare_plan_vs_actual
        from real_position_repository import get_real_cb_positions, get_real_positions
        conn = open_conn(db_path)
        try:
            date = request.args.get("date", "") or _decide_latest_plan_date(conn)
            plan_rows = _decide_plan_rows(conn, date) if date else []
            actual_rows = get_real_positions(conn)
            cb_rows = get_real_cb_positions(conn)
            prices = _decide_latest_prices(conn)
        finally:
            conn.close()
        result = compare_plan_vs_actual(plan_rows, actual_rows, cb_rows, prices)
        result["date"] = date
        return jsonify(result)
```

- [ ] **Step 4: Add the /decide page route**

In `app.py`, add after the `@app.get("/cb")` route:

```python
    @app.get("/decide")
    def decide_page():
        from datetime import date as _date
        from config import PORTFOLIO_10W
        return render_template("decide.html",
            today=_date.today().isoformat(),
            config=PORTFOLIO_10W,
            app_mode="api", data_prefix="",
            year=_date.today().year)
```

- [ ] **Step 5: Run tests**

Run: `python3 -m pytest tests/test_decide_web.py -v`
Expected: PASS except the template test (template doesn't exist yet — that's Task 4)

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_decide_web.py
git commit -m "feat(decide): /api/decide/* 路由（plan/positions/comparison）"
```

---

### Task 4: decide.html 模板 + 导航

**Files:**
- Create: `templates/decide.html`
- Modify: `templates/components/nav.html`
- Test: `tests/test_decide_web.py` (existing template tests)

- [ ] **Step 1: Create templates/decide.html**

Create `templates/decide.html`:

```html
{% extends "base.html" %}
{% set active_page = "decide" %}
{% block title %}10w 决策台{% endblock %}

{% block body %}
<main class="container py-4">
  <div class="d-flex align-items-center mb-3">
    <h2 class="mb-0">10w 决策台</h2>
    <span class="text-muted fs-6 ms-2">8w 股 + 2w 债 · 最多 {{ config.max_positions }} 只 · 单票 ≤{{ '%.0f' % (config.max_single_weight * 100) }}%</span>
    <div class="ms-auto d-flex align-items-center gap-2">
      <select id="decide-date" class="form-select form-select-sm" style="width:auto" aria-label="计划日期"></select>
      <button id="decide-refresh" class="btn btn-sm btn-outline-primary write-control">刷新</button>
    </div>
  </div>

  <div class="row row-cols-2 row-cols-md-5 g-2 mb-3" id="decide-summary"></div>

  <h5 class="mt-4">今日执行清单</h5>
  <div id="decide-plan"></div>

  <h5 class="mt-4">执行对照</h5>
  <div id="decide-comparison"></div>

  <h5 class="mt-4">我的实际持仓</h5>
  <div id="decide-positions"></div>
  <h6 class="mt-3">我的转债持仓</h6>
  <div id="decide-cb-positions"></div>
</main>
{% endblock %}

{% block scripts %}
<script type="module" src="{{ url_for('static', filename='js/decide.js') }}"></script>
{% endblock %}
```

- [ ] **Step 2: Add nav link**

In `templates/components/nav.html`, add after the `cb` link:

```html
<a class="nav-link {% if active_page == 'decide' %}active{% endif %}" data-nav="decide" href="/decide">决策台</a>
```

- [ ] **Step 3: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_decide_web.py -v`
Expected: PASS (all tests including template)

- [ ] **Step 4: Commit**

```bash
git add templates/decide.html templates/components/nav.html
git commit -m "feat(decide): 决策台页面模板 + 导航入口"
```

---

### Task 5: static/js/decide.js 前端逻辑

**Files:**
- Create: `static/js/decide.js`
- Modify: `static/js/api.js` (add 4 fetchers)

- [ ] **Step 1: Add fetchers to api.js**

Append to `static/js/api.js`:

```javascript
// ---------------------------------------------------------------------------
// 10w 决策台
// ---------------------------------------------------------------------------

export function fetchDecidePlan(date) {
  return isStatic()
    ? getJSON(dsPath('data/decide-' + date + '.json'))
    : getJSON('/api/decide/plan', { date });
}

export function fetchDecidePositions() {
  return isStatic()
    ? getJSON(dsPath('data/decide-positions.json'))
    : getJSON('/api/decide/positions');
}

export function fetchDecideComparison(date) {
  return isStatic()
    ? getJSON(dsPath('data/decide-' + date + '.json'))
    : getJSON('/api/decide/comparison', { date });
}
```

- [ ] **Step 2: Create static/js/decide.js**

Create `static/js/decide.js`:

```javascript
// 10w 决策台：今日执行清单 + 执行对照 + 实际持仓录入

import { fetchDecidePlan, fetchDecidePositions, fetchDecideComparison, isStatic } from './api.js';
import { $, fmt, esc, setStatus } from './dom.js';

const SEV_CLS = { danger: 'bg-danger', warn: 'bg-warning text-dark', info: 'bg-info text-dark' };
const SEV_LABEL = { danger: '危险', warn: '警告', info: '提示' };
const KIND_LABEL = {
  missing_buy: '漏买', unplanned: '计划外持仓', too_many_positions: '持仓超限',
  single_weight_over: '单票超限', missing_stop: '缺止损', stop_breached: '跌破止损',
  exposure_over: '总仓位超限', cb_over: '转债超配', cb_too_many: '转债只数超限',
  tp_hit: '已达止盈', shares_mismatch: '股数不符',
};

function money(v) {
  return v == null ? '—' : '¥' + Number(v).toLocaleString('zh-CN', { maximumFractionDigits: 0 });
}

// ---------------------------------------------------------------------------
// 摘要卡
// ---------------------------------------------------------------------------

function renderSummary(c) {
  const el = $('decide-summary');
  if (!el) return;
  const card = (title, value, sub, cls) => `
    <div class="col">
      <div class="card shadow-sm h-100 ${cls || ''}">
        <div class="card-body py-2 px-3">
          <div class="text-muted small mb-1">${title}</div>
          <div class="fs-5 fw-bold">${value}</div>
          <div class="text-muted small">${sub}</div>
        </div>
      </div>
    </div>`;
  el.innerHTML =
    card('股票占用', money(c.stock_used), `${c.actual_count} 只`) +
    card('转债占用', money(c.cb_used), `${c.cb_count} 只`) +
    card('现金余额', money(c.cash), c.cash < 0 ? '已超配' : '可用',
         c.cash < 0 ? 'text-danger' : '') +
    card('持仓数', c.actual_count, `计划 ${c.plan_count} 只`) +
    card('偏差', c.deviations.length,
         `危险 ${c.danger_count || 0} · 警告 ${c.warn_count || 0} · 提示 ${c.info_count || 0}`,
         c.danger_count ? 'border-danger' : '');
}

// ---------------------------------------------------------------------------
// 今日执行清单
// ---------------------------------------------------------------------------

function renderPlan(date, rows) {
  const el = $('decide-plan');
  if (!el) return;
  if (!rows || !rows.length) {
    el.innerHTML = '<div class="empty-state">该日期暂无 10W 档位计划</div>';
    return;
  }
  let html = '<div class="table-responsive app-card d-none d-md-block mb-3"><table class="app-table"><thead><tr>' +
    '<th>动作</th><th>代码</th><th>名称</th><th class="num">计划价</th><th class="num">股数</th>' +
    '<th class="num">金额</th><th class="num">止损</th><th class="num">止盈</th><th>理由</th>' +
    '</tr></thead><tbody>';
  rows.forEach(r => {
    const amount = (r.plan_price || 0) * (r.shares || 0);
    const isBuy = r.action === 'buy';
    const badge = isBuy
      ? '<span class="badge bg-success">买入</span>'
      : '<span class="badge bg-secondary">持有</span>';
    html += `<tr><td>${badge}</td><td class="code">${esc(r.code)}</td>` +
      `<td>${esc(r.name || '—')}</td>` +
      `<td class="num">${fmt(r.plan_price)}</td>` +
      `<td class="num">${r.shares ?? '—'}</td>` +
      `<td class="num">${money(amount)}</td>` +
      `<td class="num">${fmt(r.stop_price)}</td>` +
      `<td class="num">${fmt(r.tp_price)}</td>` +
      `<td class="small">${esc(r.reason || '')}</td></tr>`;
  });
  html += '</tbody></table></div>';

  html += '<div class="app-card d-md-none mb-3">';
  rows.forEach(r => {
    const amount = (r.plan_price || 0) * (r.shares || 0);
    const badge = r.action === 'buy'
      ? '<span class="badge bg-success">买入</span>'
      : '<span class="badge bg-secondary">持有</span>';
    html += `<div class="plan-card"><div class="card-header-row">` +
      `<span class="code fw-semibold">${esc(r.code)}</span>` +
      `<span class="card-title-text">${esc(r.name || '')}</span>` +
      badge + `</div>` +
      `<div class="kv-grid">` +
        `<span class="k">计划价</span><span class="v num">${fmt(r.plan_price)}</span>` +
        `<span class="k">股数</span><span class="v num">${r.shares ?? '—'}（${money(amount)}）</span>` +
        `<span class="k">止损</span><span class="v num">${fmt(r.stop_price)}</span>` +
        `<span class="k">止盈</span><span class="v num">${fmt(r.tp_price)}</span>` +
      `</div></div>`;
  });
  html += '</div>';
  el.innerHTML = html;
}

// ---------------------------------------------------------------------------
// 执行对照
// ---------------------------------------------------------------------------

function renderComparison(c) {
  const el = $('decide-comparison');
  if (!el) return;
  const devs = c.deviations || [];
  if (!devs.length) {
    el.innerHTML = '<div class="empty-state">无偏差：计划与实际一致</div>';
    return;
  }
  let html = '<div class="app-card mb-2">';
  devs.forEach(d => {
    const cls = SEV_CLS[d.severity] || 'bg-secondary';
    html += `<div class="border-bottom py-2 d-flex align-items-start gap-2">` +
      `<span class="badge ${cls}">${SEV_LABEL[d.severity] || d.severity}</span>` +
      `<div class="flex-grow-1">` +
        `<div class="fw-semibold">${esc(KIND_LABEL[d.kind] || d.kind)}` +
        `${d.code ? ` <span class="text-muted">${esc(d.code)} ${esc(d.name || '')}</span>` : ''}</div>` +
        `<div class="small">${esc(d.message)}</div>` +
      `</div></div>`;
  });
  html += '</div>';
  el.innerHTML = html;
}

// ---------------------------------------------------------------------------
// 实际持仓
// ---------------------------------------------------------------------------

function positionsTable(rows, opts) {
  const o = opts || {};
  if (!rows || !rows.length) {
    return `<div class="empty-state">${o.emptyText || '暂无持仓记录'}</div>`;
  }
  let html = '<div class="table-responsive app-card"><table class="app-table"><thead><tr>' +
    '<th>代码</th><th>名称</th><th class="num">股数</th><th class="num">成本价</th>' +
    '<th class="num">金额</th><th class="num">止损价</th><th>建仓日</th><th>操作</th>' +
    '</tr></thead><tbody>';
  rows.forEach(r => {
    const amount = (r.entry_price || 0) * (r.shares || 0);
    html += `<tr><td class="code">${esc(r.code)}</td><td>${esc(r.name || '—')}</td>` +
      `<td class="num">${r.shares}</td><td class="num">${fmt(r.entry_price)}</td>` +
      `<td class="num">${money(amount)}</td>` +
      `<td class="num">${r.stop_price == null ? '<span class="text-warning">未设</span>' : fmt(r.stop_price)}</td>` +
      `<td>${esc(r.entry_date || '—')}</td>` +
      `<td>${o.readonly ? '' : `<button class="btn btn-sm btn-outline-danger" data-del="${esc(r.code)}" data-cb="${o.isCb ? '1' : '0'}">删除</button>`}</td>` +
      `</tr>`;
  });
  html += '</tbody></table></div>';
  return html;
}

function renderPositions(rows, cbRows) {
  const stockEl = $('decide-positions');
  const cbEl = $('decide-cb-positions');
  if (stockEl) {
    stockEl.innerHTML = positionsTable(rows, { emptyText: '暂无实际持仓，点上方按钮添加' });
    if (!isStatic()) {
      stockEl.insertAdjacentHTML('beforeend',
        `<div class="mt-2"><button id="add-position" class="btn btn-sm btn-outline-primary">＋ 添加持仓</button></div>`);
    }
  }
  if (cbEl) {
    cbEl.innerHTML = positionsTable(cbRows, { emptyText: '暂无转债持仓', isCb: true, readonly: isStatic() });
    if (!isStatic()) {
      cbEl.insertAdjacentHTML('beforeend',
        `<div class="mt-2"><button id="add-cb-position" class="btn btn-sm btn-outline-primary">＋ 添加转债</button></div>`);
    }
  }
}

// ---------------------------------------------------------------------------
// 录入表单
// ---------------------------------------------------------------------------

function showAddForm(isCb) {
  const title = isCb ? '添加转债持仓' : '添加股票持仓';
  const fields = [
    ['code', '代码', 'text'],
    ['name', '名称', 'text'],
    ['shares', '股数', 'number'],
    ['entry_price', '成本价', 'number'],
    ['stop_price', '止损价（可空）', 'number'],
    ['entry_date', '建仓日', 'date'],
  ].map(([k, label, type]) =>
    `<div class="col-auto"><label class="form-label mb-0">${label}</label>` +
    `<input type="${type}" class="form-control form-control-sm" id="fld-${k}" data-key="${k}"></div>`
  ).join('');
  document.body.insertAdjacentHTML('beforeend',
    `<div class="modal fade" id="pos-modal" tabindex="-1">
      <div class="modal-dialog"><div class="modal-content">
        <div class="modal-header"><h6 class="modal-title">${title}</h6>
          <button type="button" class="btn-close" data-bs-dismiss="modal"></button></div>
        <div class="modal-body"><div class="row g-2">${fields}</div></div>
        <div class="modal-footer">
          <button class="btn btn-sm btn-secondary" data-bs-dismiss="modal">取消</button>
          <button class="btn btn-sm btn-primary" id="pos-save">保存</button>
        </div>
      </div></div>
    </div>`);
  const modal = document.getElementById('pos-modal');
  modal.classList.add('show');
  modal.style.display = 'block';
  document.getElementById('pos-save').addEventListener('click', async () => {
    const payload = { is_cb: !!isCb };
    modal.querySelectorAll('[data-key]').forEach(inp => {
      const k = inp.dataset.key;
      const v = inp.value.trim();
      payload[k] = v === '' ? null : (inp.type === 'number' ? Number(v) : v);
    });
    const resp = await fetch('/api/decide/positions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({}));
      setStatus('保存失败：' + (err.error || resp.status), 'alert-danger');
      return;
    }
    modal.remove();
    loadAll();
  });
}

// ---------------------------------------------------------------------------
// 加载
// ---------------------------------------------------------------------------

function fillDateOptions(dates) {
  const sel = $('decide-date');
  if (!sel) return;
  sel.innerHTML = '';
  (dates || []).forEach(d => sel.appendChild(new Option(d, d)));
  if (dates && dates.length) sel.value = dates[0];
}

async function loadAll() {
  setStatus('加载中…', 'alert-info');
  try {
    const planRes = await fetchDecidePlan().catch(() => ({ date: '', rows: [], dates: [] }));
    fillDateOptions(planRes.dates || []);
    const date = ($('decide-date') && $('decide-date').value) || planRes.date || '';

    const cmpRes = await fetchDecideComparison(date).catch(() => ({ deviations: [] }));
    const posRes = await fetchDecidePositions().catch(() => ({ rows: [], cb_rows: [] }));

    renderPlan(date, planRes.rows || []);
    renderComparison(cmpRes);
    renderPositions(posRes.rows || [], posRes.cb_rows || []);
    renderSummary(cmpRes);
    setStatus('', '');
  } catch (e) {
    setStatus('加载失败：' + (e && e.message ? e.message : e), 'alert-danger');
  }
}

document.addEventListener('DOMContentLoaded', () => {
  const sel = $('decide-date');
  if (sel) sel.addEventListener('change', loadAll);
  const btn = $('decide-refresh');
  if (btn) btn.addEventListener('click', loadAll);

  document.addEventListener('click', async e => {
    const add = e.target.closest('#add-position');
    if (add) { showAddForm(false); return; }
    const addCb = e.target.closest('#add-cb-position');
    if (addCb) { showAddForm(true); return; }
    const del = e.target.closest('[data-del]');
    if (del) {
      const code = del.dataset.del;
      const isCb = del.dataset.cb === '1';
      await fetch('/api/decide/positions/' + encodeURIComponent(code) + (isCb ? '?cb=1' : ''),
        { method: 'DELETE' });
      loadAll();
    }
  });

  loadAll();
});
```

- [ ] **Step 3: Verify JS syntax**

Run:
```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
node --check static/js/decide.js && node --check static/js/api.js
```

Expected: no output (syntax valid)

- [ ] **Step 4: Commit**

```bash
git add static/js/api.js static/js/decide.js
git commit -m "feat(decide): 决策台前端逻辑 + api.js 取数函数"
```

---

### Task 6: 静态导出 + 端到端验证

**Files:**
- Modify: `export_json.py`
- Test: `tests/test_decide_web.py` (append export test)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_decide_web.py`:

```python
class TestDecideExport:

    def test_export_includes_decide_files(self, plan_db, tmp_path):
        from export_json import export
        out = str(tmp_path / "site")
        export(plan_db, out)
        assert os.path.exists(os.path.join(out, "decide.html"))
        data_dir = os.path.join(out, "data")
        assert os.path.exists(os.path.join(data_dir, "decide-positions.json"))
        assert os.path.exists(os.path.join(data_dir, "decide-2026-10-25.json"))

        with open(os.path.join(data_dir, "decide-2026-10-25.json"), encoding="utf-8") as f:
            snap = json.load(f)
        assert snap["date"] == "2026-10-25"
        assert "deviations" in snap
        assert "cash" in snap

        html = open(os.path.join(out, "decide.html"), encoding="utf-8").read()
        assert "决策台" in html
        assert "js/decide.js" in html
        assert 'window.APP_MODE="static"' in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_decide_web.py::TestDecideExport -v`
Expected: FAIL — `decide.html` not found

- [ ] **Step 3: Implement the export**

In `export_json.py`, add a helper after `_export_cb_data`:

```python
def _export_decide_data(conn, data_dir: Path, out: Path) -> None:
    """导出 10w 决策台快照（静态站只读）：计划 + 对照 + 实际持仓。"""
    from decide_service import compare_plan_vs_actual, dedupe_plan_rows
    from db_repository import get_trade_plan_by_date
    from real_position_repository import get_real_cb_positions, get_real_positions

    dates = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT plan_date FROM trade_plan WHERE portfolio='10W' "
            "ORDER BY plan_date DESC"
        ).fetchall()
    ]
    json.dump({"dates": dates}, (data_dir / "decide-dates.json").open("w"), ensure_ascii=False)

    prices = {}
    for code, close in conn.execute(
        "SELECT d.code, d.close FROM daily_prices d "
        "JOIN (SELECT code, MAX(trade_date) AS md FROM daily_prices GROUP BY code) x "
        "ON d.code = x.code AND d.trade_date = x.md"
    ):
        if close is not None:
            prices[code] = float(close)

    actual_rows = get_real_positions(conn)
    cb_rows = get_real_cb_positions(conn)
    json.dump(
        {"rows": actual_rows, "cb_rows": cb_rows},
        (data_dir / "decide-positions.json").open("w"),
        ensure_ascii=False, default=str,
    )

    for d in dates:
        plan_rows = dedupe_plan_rows([
            r for r in get_trade_plan_by_date(conn, d)
            if r.get("portfolio") == "10W" and r.get("status") != "failed"
        ])
        snap = compare_plan_vs_actual(plan_rows, actual_rows, cb_rows, prices)
        snap["date"] = d
        snap["rows"] = plan_rows
        json.dump(
            snap, (data_dir / f"decide-{d}.json").open("w"),
            ensure_ascii=False, default=str,
        )
```

In `export()`, inside the `with conn:` block (after `_export_cb_data(conn, data_dir)`), add:

```python
        _export_decide_data(conn, data_dir, out)
```

Then after the `cb.html` write block, add:

```python
    (out / "decide.html").write_text(
        _render_static_page("decide.html",
            today=_dt_class.now().strftime("%Y-%m-%d"),
            config=PORTFOLIO_10W,
            year=_dt_class.now().year),
        encoding="utf-8",
    )
```

Add the import at the top of `export_json.py`:

```python
from config import CAPITAL_TIERS, PORTFOLIO_10W
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_decide_web.py -v`
Expected: PASS (all tests)

- [ ] **Step 5: Run full suite**

Run: `python3 -m pytest tests/ -q`
Expected: PASS (baseline + new tests, 0 regressions)

- [ ] **Step 6: Commit**

```bash
git add export_json.py tests/test_decide_web.py
git commit -m "feat(decide): 静态导出决策台快照 + decide.html"
```

---

### Task 7: 端到端验证 + 文档

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Verify the Flask page with real data**

```bash
cd /Users/shichaopeng/Work/self-dir/projects/a-finder-cli
python3 -c "
from app import create_app
app = create_app()
c = app.test_client()
p = c.get('/api/decide/plan').get_json()
print('plan date:', p['date'], 'rows:', len(p['rows']))
cmp_ = c.get('/api/decide/comparison').get_json()
print('cash:', cmp_['cash'], 'deviations:', len(cmp_['deviations']))
r = c.get('/decide')
print('/decide:', r.status_code, len(r.data), 'bytes')
"
```

Expected: `/decide` returns 200

- [ ] **Step 2: Verify static export**

```bash
python3 export_json.py --db hs300.db --out /tmp/site-decide 2>&1 | tail -1
ls /tmp/site-decide/decide.html /tmp/site-decide/data/decide-*.json | head -5
```

Expected: `decide.html` + at least one `decide-<date>.json`

- [ ] **Step 3: Update CLAUDE.md**

In CLAUDE.md's Architecture section, in the **Web** bullet, append:

```markdown
10w 决策台（`/decide`）：按 `config.PORTFOLIO_10W`（8w股+2w债、≤8只、单票≤10%）展示今日执行清单，
对照用户录入的真实持仓（`real_positions` / `real_cb_positions`）高亮偏差；
判定逻辑在 `decide_service.py`，静态站导出为只读快照。
```

- [ ] **Step 4: Run full suite + commit + push**

```bash
python3 -m pytest tests/ -q 2>&1 | tail -2
git add CLAUDE.md
git commit -m "docs: 记录 10w 决策台"
git push origin main 2>&1 | tail -2
```

---

## Self-Review

**Spec coverage:**
- §3.1 数据模型（real_positions / real_cb_positions + 迁移）→ Task 1
- §3.2 API 路由（4 个 /api/decide/*）→ Task 3
- §3.3 对照判定规则（10 类偏差 + PORTFOLIO_10W）→ Task 1 (config) + Task 2 (service)
- §3.4 页面与前端（decide.html / decide.js / api.js fetchers / 静态只读）→ Task 4 + Task 5
- §四 文件清单 → Tasks 1-6
- §五 测试策略（12 + 6 项）→ Task 1/2 (test_decide.py) + Task 3/4/6 (test_decide_web.py)
- §七 验证计划 → Task 7

**Placeholder scan:** 无 TBD/TODO/FIXME。所有代码块完整可运行。

**Type consistency:**
- `PORTFOLIO_10W` 键：`total_capital`/`stock_ratio`/`cb_ratio`/`max_positions`/`max_single_weight`/`max_stock_exposure`/`cb_capital`/`max_cb_positions` — Task 1 定义，Task 2 使用，一致
- `compare_plan_vs_actual(plan_rows, actual_rows, cb_rows, latest_prices, config=None)` → 返回 `deviations`/`stock_used`/`cb_used`/`cash`/`plan_count`/`actual_count`/`cb_count`/`danger_count`/`warn_count`/`info_count` — Task 2 定义，Task 3/5/6 使用，一致
- 偏差 `kind` 值：missing_buy / unplanned / too_many_positions / single_weight_over / missing_stop / stop_breached / exposure_over / cb_over / cb_too_many / tp_hit / shares_mismatch — Task 2 定义，Task 5 的 KIND_LABEL 覆盖，一致
- `dedupe_plan_rows(rows)` — Task 2 定义，Task 3/6 使用，一致
- 仓储函数：`upsert_real_position`/`get_real_positions`/`delete_real_position`/`upsert_real_cb_position`/`get_real_cb_positions`/`delete_real_cb_position` — Task 1 定义，Task 3/6 使用，一致
