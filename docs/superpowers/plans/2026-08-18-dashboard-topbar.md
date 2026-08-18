# Dashboard 顶部条 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `/` 和 `/plan` 页面顶部加 4 块 dashboard 卡片（运行状态、今日 plan、持仓概览、最近 5 日收益），共享片段 + 单端点聚合 + 15s 轮询。

**Architecture:** Flask 单端点 `/api/dashboard` 一次返回 4 块 JSON；前端共享 `DASHBOARD_HTML` 片段嵌入两页面顶部；`document.hidden` 时暂停轮询。新增 `daily_picks.updated_at` 列作为「运行状态」卡的数据源。

**Tech Stack:** Python 3.11 + Flask + jinja-free 字符串模板 + SQLite + jQuery 3.7.1 + Bootstrap 5（BootCDN）。

---

## File Structure

| 文件 | 改动 |
|---|---|
| `db/migrations/2026_08_18_daily_picks_updated_at.sql` | 新建：ALTER TABLE + CREATE INDEX |
| `db_schema.py` | 不动（migrations 自跑） |
| `pick_history.py` | INSERT 加 `updated_at` 列 |
| `db_repository.py` | 新增 4 个聚合函数 |
| `app.py` | 新增 `DASHBOARD_HTML`、`/api/dashboard`、`startDashboard()` JS 注入 PAGE+PLAN_PAGE |
| `tests/test_web_plan.py` | 加 `test_dashboard_*` 系列 |
| `tests/test_db_repository.py` | 加 4 个聚合函数的单元测试 |

---

## Task 1: 数据库迁移 — 加 `daily_picks.updated_at`

**Files:**
- Create: `db/migrations/2026_08_18_daily_picks_updated_at.sql`
- Test: `tests/test_migration.py`（或新建 `tests/test_dashboard_migration.py`）

- [ ] **Step 1: 写迁移 SQL 文件**

`db/migrations/2026_08_18_daily_picks_updated_at.sql`：

```sql
ALTER TABLE daily_picks ADD COLUMN updated_at TEXT NOT NULL DEFAULT (CURRENT_TIMESTAMP);
CREATE INDEX IF NOT EXISTS idx_daily_picks_updated_at ON daily_picks(updated_at);
```

- [ ] **Step 2: 写失败测试**

`tests/test_dashboard_migration.py`：

```python
import sqlite3
import tempfile

from db_repository import open_db


def test_daily_picks_has_updated_at():
    """After open_db runs migrations, daily_picks.updated_at exists and defaults populate."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = open_db(path)
    try:
        # column exists
        cols = [r[1] for r in conn.execute("PRAGMA table_info(daily_picks)").fetchall()]
        assert "updated_at" in cols
        # default populates on insert
        conn.execute(
            "INSERT INTO daily_picks (date, rank, kind, code) VALUES ('2026-08-18', 1, '均线', '600519')"
        )
        conn.commit()
        row = conn.execute(
            "SELECT updated_at FROM daily_picks WHERE date='2026-08-18' AND code='600519'"
        ).fetchone()
        assert row[0] is not None and len(row[0]) >= 10  # ISO-ish string
    finally:
        conn.close()
```

- [ ] **Step 3: 跑测试，断言它 PASS（迁移是 open_db 时跑的）**

Run: `.venv/bin/python -m pytest tests/test_dashboard_migration.py -v`
Expected: PASS（因为 open_db 调用 `_run_migrations`）

- [ ] **Step 4: Commit**

```bash
git add db/migrations/2026_08_18_daily_picks_updated_at.sql tests/test_dashboard_migration.py
git commit -m "feat(db): add daily_picks.updated_at via migration"
```

---

## Task 2: `pick_history.py` 写入 `updated_at`

**Files:**
- Modify: `pick_history.py:134-141`
- Test: `tests/test_dashboard_migration.py` 加一条

- [ ] **Step 1: 看现状**

读 `pick_history.py:130-145`，确认 INSERT 是 `INSERT OR REPLACE INTO daily_picks (date, rank, kind, code, name, strategy, buy, stop, target, score)` 加 10 个 `?`。

- [ ] **Step 2: 加更新测试**

在 `tests/test_dashboard_migration.py` 追加：

```python
def test_pick_history_writes_updated_at():
    """pick_history upserts daily_picks rows with updated_at populated."""
    import tempfile
    from db_repository import open_db
    from pick_history import save_daily_picks  # adjust if name differs

    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = open_db(path)
    try:
        picks = [{
            "date": "2026-08-18", "rank": 1, "kind": "均线",
            "code": "600519", "name": "贵州茅台", "strategy": "突破",
            "buy": 1500.0, "stop": 1450.0, "target": 1600.0, "score": 9.5,
        }]
        save_daily_picks(conn, picks)  # use the actual function name from pick_history
        conn.commit()
        row = conn.execute(
            "SELECT updated_at FROM daily_picks WHERE date='2026-08-18' AND code='600519'"
        ).fetchone()
        assert row[0] is not None
    finally:
        conn.close()
```

> 注：`pick_history` 暴露的内部函数名（`save_daily_picks` / `insert_picks` / 类似）以文件为准。若函数是私有的，改成直接调内部 SQL 也可；测试是契约。

- [ ] **Step 3: 跑测试，确认 FAIL（updated_at 列被忽略）**

Run: `.venv/bin/python -m pytest tests/test_dashboard_migration.py::test_pick_history_writes_updated_at -v`
Expected: FAIL with `AssertionError`

- [ ] **Step 4: 改 INSERT 加 updated_at**

在 `pick_history.py` 找到那段 INSERT，把 `rows` 列表每个元组补 `updated_at`（用 `datetime.now().isoformat(timespec='seconds')`），列名和 VALUES 也加一列。

```python
from datetime import datetime as _dt
now = _dt.now().isoformat(timespec="seconds")
rows = [
    (p["date"], p["rank"], p["kind"], p["code"], p["name"], p["strategy"],
     p["buy"], p["stop"], p["target"], p["score"], now)
    for p in picks
]
conn.executemany(
    """
    INSERT OR REPLACE INTO daily_picks
    (date, rank, kind, code, name, strategy, buy, stop, target, score, updated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
    rows,
)
```

> 注意：`now` 在循环外求值一次，让同一批 INSERT 的时间戳一致；不要求跨 batch 一致。

- [ ] **Step 5: 跑测试，PASS**

Run: `.venv/bin/python -m pytest tests/test_dashboard_migration.py::test_pick_history_writes_updated_at -v`
Expected: PASS

- [ ] **Step 6: 跑全套测试确认无回归**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 全绿（除可能有 pick_history 的旧测试对 INSERT 列数敏感的，确认下）

- [ ] **Step 7: Commit**

```bash
git add pick_history.py tests/test_dashboard_migration.py
git commit -m "feat(pick_history): populate daily_picks.updated_at on upsert"
```

---

## Task 3: `db_repository.get_last_refresh`

**Files:**
- Modify: `db_repository.py`（末尾追加）
- Test: `tests/test_db_repository.py`

- [ ] **Step 1: 写失败测试**

`tests/test_db_repository.py` 追加：

```python
def test_get_last_refresh_returns_max_updated_at():
    import tempfile
    from db_repository import open_db, get_last_refresh
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = open_db(path)
    try:
        # empty DB
        assert get_last_refresh(conn) is None
        # one row
        conn.execute(
            "INSERT INTO daily_picks (date, rank, kind, code, updated_at) "
            "VALUES ('2026-08-18', 1, '均线', '600519', '2026-08-18 12:00:00')"
        )
        conn.commit()
        r = get_last_refresh(conn)
        assert r["date"] == "2026-08-18"
        assert r["updated_at"] == "2026-08-18 12:00:00"
        # newer row wins
        conn.execute(
            "INSERT INTO daily_picks (date, rank, kind, code, updated_at) "
            "VALUES ('2026-08-17', 1, '均线', '000001', '2026-08-18 14:00:00')"
        )
        conn.commit()
        r = get_last_refresh(conn)
        assert r["updated_at"] == "2026-08-18 14:00:00"
        assert r["date"] == "2026-08-17"
    finally:
        conn.close()
```

- [ ] **Step 2: 跑测试，FAIL**

Run: `.venv/bin/python -m pytest tests/test_db_repository.py::test_get_last_refresh_returns_max_updated_at -v`
Expected: FAIL (function not defined)

- [ ] **Step 3: 实现 `get_last_refresh`**

`db_repository.py` 末尾追加：

```python
def get_last_refresh(conn: sqlite3.Connection) -> Optional[Dict]:
    """Return the most recently updated daily_picks row: {date, updated_at}. None if empty."""
    cur = conn.execute(
        "SELECT date, updated_at FROM daily_picks "
        "ORDER BY updated_at DESC, date DESC LIMIT 1"
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {"date": row[0], "updated_at": row[1]}
```

- [ ] **Step 4: 跑测试，PASS**

Run: `.venv/bin/python -m pytest tests/test_db_repository.py::test_get_last_refresh_returns_max_updated_at -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add db_repository.py tests/test_db_repository.py
git commit -m "feat(db_repository): get_last_refresh returns most recent daily_picks row"
```

---

## Task 4: `db_repository.get_today_plan_summary`

**Files:**
- Modify: `db_repository.py`
- Test: `tests/test_db_repository.py`

- [ ] **Step 1: 写失败测试**

```python
def test_get_today_plan_summary_counts_actions_and_size():
    import tempfile
    from db_repository import open_db, get_today_plan_summary
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = open_db(path)
    try:
        today = "2026-08-18"
        rows = [
            (today, "600519", "buy", 1500, 0.10, 1380, 1740, 2.0, "ok", "", "{}", "h", today + "T00:00:00"),
            (today, "000001", "buy", 10, 0.20, 9, 12, 1.5, "ok", "", "{}", "h", today + "T00:00:00"),
            (today, "000002", "hold", 5, 0.05, 4.5, 6, 1.0, "ok", "", "{}", "h", today + "T00:00:00"),
            (today, "000003", "exit", 8, 0.0, 7, 10, 2.0, "ok", "", "{}", "h", today + "T00:00:00"),
            (today, "000004", "buy", 20, 0.99, 18, 25, 1.0, "failed", "size_exceed_max", "{}", "h", today + "T00:00:00"),
        ]
        conn.executemany(
            "INSERT INTO trade_plan (plan_date,code,action,plan_price,size_pct,stop_price,tp_price,rr_ratio,status,reason,rationale_json,params_hash,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
        s = get_today_plan_summary(conn, today)
        assert s == {
            "date": today,
            "buy": 2, "hold": 1, "exit": 1,
            "size_total": 0.30,  # 0.10 + 0.20; failed 不计入
            "failed": 1,
        }
        # 空日期
        assert get_today_plan_summary(conn, "1999-01-01") == {
            "date": "1999-01-01", "buy": 0, "hold": 0, "exit": 0, "size_total": 0.0, "failed": 0
        }
    finally:
        conn.close()
```

- [ ] **Step 2: 跑测试，FAIL**

Run: `.venv/bin/python -m pytest tests/test_db_repository.py::test_get_today_plan_summary_counts_actions_and_size -v`
Expected: FAIL

- [ ] **Step 3: 实现**

```python
def get_today_plan_summary(conn: sqlite3.Connection, today: str) -> Dict:
    """Counts by action + total buy size + failed count for a given plan_date."""
    cur = conn.execute(
        """SELECT action, status, COALESCE(SUM(size_pct), 0.0)
           FROM trade_plan WHERE plan_date = ? GROUP BY action, status""",
        (today,),
    )
    buy = hold = exit_ = failed = 0
    size_total = 0.0
    for action, status, sum_size in cur.fetchall():
        if action == "buy" and status == "ok":
            buy += 1
            size_total += sum_size
        elif action == "buy":
            failed += 1  # buy + failed
        elif action == "hold":
            hold += 1
        elif action == "exit":
            exit_ += 1
        if status == "failed" and action != "buy":
            failed += 1
    return {
        "date": today,
        "buy": buy, "hold": hold, "exit": exit_,
        "size_total": round(size_total, 4),
        "failed": failed,
    }
```

- [ ] **Step 4: 跑测试，PASS**

Run: `.venv/bin/python -m pytest tests/test_db_repository.py::test_get_today_plan_summary_counts_actions_and_size -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add db_repository.py tests/test_db_repository.py
git commit -m "feat(db_repository): get_today_plan_summary aggregates buy/hold/exit + size"
```

---

## Task 5: `db_repository.get_open_positions_with_unrealized`

**Files:**
- Modify: `db_repository.py`
- Test: `tests/test_db_repository.py`

- [ ] **Step 1: 写失败测试**

```python
def test_get_open_positions_with_unrealized_joins_daily_prices():
    import tempfile
    from db_repository import open_db, get_open_positions_with_unrealized
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = open_db(path)
    try:
        # seed open_positions
        conn.executemany(
            "INSERT INTO open_positions (code, entry_date, entry_price, size_pct, stop_price, tp_price, status) "
            "VALUES (?,?,?,?,?,?,?)",
            [
                ("600519", "2026-08-10", 1500.0, 0.10, 1380.0, 1740.0, "open"),
                ("000001", "2026-08-12", 10.0, 0.05, 9.0, 12.0, "open"),
            ],
        )
        # seed daily_prices (latest close per code)
        conn.executemany(
            "INSERT INTO daily_prices (code, trade_date, open_price, close_price, high_price, low_price, volume, amount, amplitude, pct_change, turnover) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            [
                ("600519", "2026-08-17", 1500, 1545, 1550, 1490, 1e6, 1e9, 0, 0.03, 0.5),
                ("600519", "2026-08-18", 1545, 1530, 1555, 1525, 1e6, 1e9, 0, -0.01, 0.5),
                ("000001", "2026-08-18", 10.0, 11.5, 11.6, 10.0, 1e6, 1e7, 0, 0.15, 0.5),
            ],
        )
        conn.commit()
        r = get_open_positions_with_unrealized(conn)
        assert r["count"] == 2
        assert abs(r["size_total"] - 0.15) < 1e-9
        # unrealized_pct: 600519 (1530-1500)/1500*100 = 2.0; 000001 (11.5-10)/10*100 = 15.0
        assert abs(r["avg_unrealized_pct"] - 8.5) < 1e-9
        codes = [it["code"] for it in r["items"]]
        assert codes == ["600519", "000001"]
        assert abs(r["items"][1]["unrealized_pct"] - 15.0) < 1e-9
    finally:
        conn.close()


def test_get_open_positions_no_prices_returns_null_unrealized():
    import tempfile
    from db_repository import open_db, get_open_positions_with_unrealized
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = open_db(path)
    try:
        conn.execute(
            "INSERT INTO open_positions (code, entry_date, entry_price, size_pct, stop_price, tp_price, status) "
            "VALUES ('999999', '2026-08-10', 5.0, 0.10, 4.0, 7.0, 'open')"
        )
        conn.commit()
        r = get_open_positions_with_unrealized(conn)
        assert r["count"] == 1
        assert r["items"][0]["unrealized_pct"] is None
        assert r["avg_unrealized_pct"] is None
    finally:
        conn.close()
```

- [ ] **Step 2: 跑测试，FAIL**

Run: `.venv/bin/python -m pytest tests/test_db_repository.py -k unrealized -v`
Expected: FAIL

- [ ] **Step 3: 实现**

```python
def get_open_positions_with_unrealized(conn: sqlite3.Connection) -> Dict:
    """Open positions with latest close price; computes unrealized_pct per row."""
    cur = conn.execute(
        """SELECT op.code, op.entry_date, op.entry_price, op.size_pct,
                  op.stop_price, op.tp_price,
                  dp.close_price
           FROM open_positions op
           LEFT JOIN (
               SELECT code, close_price FROM daily_prices dp1
               WHERE trade_date = (SELECT MAX(trade_date) FROM daily_prices dp2
                                   WHERE dp2.code = dp1.code)
           ) dp ON dp.code = op.code
           WHERE op.status = 'open'
           ORDER BY op.entry_date, op.code"""
    )
    items = []
    total_size = 0.0
    pct_sum = 0.0
    pct_count = 0
    for code, ed, ep, sz, sp, tp, close in cur.fetchall():
        unrealized = None
        if close is not None and ep:
            unrealized = round((close - ep) / ep * 100, 2)
            pct_sum += unrealized
            pct_count += 1
        items.append({
            "code": code, "entry_date": ed, "entry_price": ep,
            "size_pct": sz, "stop_price": sp, "tp_price": tp,
            "unrealized_pct": unrealized,
        })
        total_size += sz
    avg = round(pct_sum / pct_count, 2) if pct_count else None
    return {"count": len(items), "size_total": round(total_size, 4),
            "avg_unrealized_pct": avg, "items": items[:3]}
```

- [ ] **Step 4: 跑测试，PASS**

Run: `.venv/bin/python -m pytest tests/test_db_repository.py -k unrealized -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add db_repository.py tests/test_db_repository.py
git commit -m "feat(db_repository): get_open_positions_with_unrealized joins latest close"
```

---

## Task 6: `db_repository.get_recent_pnl`

**Files:**
- Modify: `db_repository.py`
- Test: `tests/test_db_repository.py`

- [ ] **Step 1: 写失败测试**

```python
def test_get_recent_pnl_groups_by_plan_date():
    import tempfile
    from db_repository import open_db, get_recent_pnl
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = open_db(path)
    try:
        rows = [
            ("2026-08-14", "600519", "close", 1600, None, 5.0, None, "2026-08-14T00:00:00"),
            ("2026-08-14", "000001", "close", 11, None, 10.0, None, "2026-08-14T00:00:00"),
            ("2026-08-15", "600519", "close", 1580, None, -3.0, None, "2026-08-15T00:00:00"),
            ("2026-08-17", "000002", "open", 10, None, None, "买入", "2026-08-17T00:00:00"),  # 不计入
        ]
        conn.executemany(
            "INSERT INTO trade_events (plan_date, code, event_type, price, size_pct, pnl_pct, note, created_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            rows,
        )
        conn.commit()
        pnl = get_recent_pnl(conn, days=5)
        # 期望 DESC 顺序，每个日期 SUM(pnl_pct)
        assert pnl == [
            {"date": "2026-08-15", "pnl_pct": -3.0},
            {"date": "2026-08-14", "pnl_pct": 15.0},
        ]
    finally:
        conn.close()


def test_get_recent_pnl_empty():
    import tempfile
    from db_repository import open_db, get_recent_pnl
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = open_db(path)
    try:
        assert get_recent_pnl(conn) == []
    finally:
        conn.close()
```

- [ ] **Step 2: 跑测试，FAIL**

Run: `.venv/bin/python -m pytest tests/test_db_repository.py -k recent_pnl -v`
Expected: FAIL

- [ ] **Step 3: 实现**

```python
def get_recent_pnl(conn: sqlite3.Connection, days: int = 5) -> List[Dict]:
    """Last `days` distinct plan_date close events, DESC. [{date, pnl_pct}]."""
    cur = conn.execute(
        """SELECT plan_date, SUM(pnl_pct) FROM trade_events
           WHERE event_type = 'close' AND pnl_pct IS NOT NULL
           GROUP BY plan_date
           ORDER BY plan_date DESC
           LIMIT ?""",
        (days,),
    )
    return [{"date": d, "pnl_pct": round(p, 2)} for d, p in cur.fetchall()]
```

- [ ] **Step 4: 跑测试，PASS**

Run: `.venv/bin/python -m pytest tests/test_db_repository.py -k recent_pnl -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add db_repository.py tests/test_db_repository.py
git commit -m "feat(db_repository): get_recent_pnl groups close events by plan_date"
```

---

## Task 7: `/api/dashboard` 端点

**Files:**
- Modify: `app.py`（在 `create_app` 内追加路由）
- Test: `tests/test_web_plan.py`

- [ ] **Step 1: 写失败测试**

`tests/test_web_plan.py` 追加：

```python
def test_api_dashboard_returns_four_sections(plan_db, monkeypatch):
    from datetime import datetime as _dt, date as _date
    # stub freshness 阈值：固定 now 让 ago 可断言
    fixed_now = _dt(2026, 8, 18, 12, 0, 0)
    class FakeDateTime:
        @classmethod
        def now(cls): return fixed_now
    import app as app_module
    monkeypatch.setattr(app_module._dashboard_now, "datetime", FakeDateTime)
    # seed daily_picks + updated_at
    conn = sqlite3.connect(plan_db)
    conn.execute(
        "INSERT INTO daily_picks (date, rank, kind, code, updated_at) "
        "VALUES ('2026-08-17', 1, '均线', '600519', '2026-08-17 10:00:00')"
    )
    conn.commit()
    conn.close()

    app = create_app(db_path=plan_db)
    app.config["TESTING"] = True
    resp = app.test_client().get("/api/dashboard")
    assert resp.status_code == 200
    data = resp.get_json()
    assert set(data.keys()) == {"last_refresh", "today_plan", "open_positions", "pnl_5d"}
    # last_refresh: 26h ago = warm (>24, <72)
    assert data["last_refresh"]["date"] == "2026-08-17"
    assert data["last_refresh"]["ago_hours"] == 26.0
    assert data["last_refresh"]["freshness"] == "warm"
    # today_plan: 来自 plan_db fixture 已插入的 ok 行
    assert data["today_plan"]["buy"] == 1
    # open_positions: 空
    assert data["open_positions"]["count"] == 0
    # pnl_5d: 空
    assert data["pnl_5d"] == []


def test_api_dashboard_empty_db():
    import tempfile
    from db_repository import open_db
    from app import create_app
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    open_db(path).close()
    client = create_app(db_path=path).test_client()
    data = client.get("/api/dashboard").get_json()
    assert data["last_refresh"] is None
    assert data["today_plan"]["buy"] == 0
    assert data["open_positions"]["count"] == 0
    assert data["pnl_5d"] == []
```

- [ ] **Step 2: 跑测试，FAIL（端点不存在）**

Run: `.venv/bin/python -m pytest tests/test_web_plan.py -k dashboard -v`
Expected: FAIL with 404

- [ ] **Step 3: 实现端点**

`app.py` 在 `create_app` 函数体末尾追加：

```python
    @app.get("/api/dashboard")
    def dashboard():
        from datetime import date as _date, datetime as _dt
        from db_repository import (
            get_last_refresh,
            get_today_plan_summary,
            get_open_positions_with_unrealized,
            get_recent_pnl,
        )
        conn = open_conn(db_path)
        try:
            last = get_last_refresh(conn)
            today = get_today_plan_summary(conn, _date.today().isoformat())
            opens = get_open_positions_with_unrealized(conn)
            pnl = get_recent_pnl(conn, days=5)
        finally:
            conn.close()
        if last:
            try:
                dt = _dt.fromisoformat(last["updated_at"])
            except ValueError:
                dt = _dt.strptime(last["updated_at"], "%Y-%m-%d %H:%M:%S")
            ago = (_dt.now() - dt).total_seconds() / 3600
            last["ago_hours"] = round(ago, 1)
            last["freshness"] = "fresh" if ago < 24 else ("warm" if ago < 72 else "stale")
        return jsonify({
            "last_refresh": last,
            "today_plan": today,
            "open_positions": opens,
            "pnl_5d": pnl,
        })
```

并在 `app.py` 顶部（导入区附近）加一行：

```python
import datetime as _dashboard_now  # 供测试 monkeypatch 用
```

> 测试用 `monkeypatch.setattr(app_module._dashboard_now, "datetime", FakeDateTime)`，但 `_dashboard_now.datetime` 是 `datetime` 模块本身；monkeypatch 替换它后，`_dt.now()` 走 fake 模块的 `now` 类方法。`FakeDateTime` 是个类而非实例，所以要让 fake 是模块，简化：把测试改成直接 patch `app_module._dashboard_now.datetime`。

更稳的实现：

```python
# app.py
from datetime import datetime as _datetime
# ... existing imports
```

然后测试改成 `monkeypatch.setattr(app_module, "_datetime", FakeDatetime)` 其中 `FakeDatetime` 是简单模块：

```python
import types
fake = types.ModuleType("fake_datetime")
class _Now:
    @staticmethod
    def isoformat(): return "2026-08-18T12:00:00"
fake.datetime = _Now
monkeypatch.setattr(app_module, "_datetime", fake)
```

如 monkeypatch 模块比改 endpoint 简单，则改 endpoint 让 `_dt.now()` 显式调 `_dt.datetime.now()` —— 但这违反 stdlib 风格。

最简方案：endpoint 内部用 `from datetime import datetime` 而不是依赖模块属性，测试 monkeypatch `app_module._dashboard_datetime`：

```python
# 在 create_app 顶部
_dashboard_datetime = None  # 设为模块全局供测试 patch
```

实际写法：在 `app.py` 顶部 import 区追加 `from datetime import datetime as _dashboard_datetime`，endpoint 用 `_dashboard_datetime.now()`。测试 monkeypatch 模块属性。

为简化，第一次实现用以下结构：

```python
# app.py 顶部
from datetime import datetime as _dashboard_now

# endpoint 内
ago = (_dashboard_now.now() - dt).total_seconds() / 3600
```

测试用：
```python
import types
fake_mod = types.SimpleNamespace(now=lambda: _dt(2026, 8, 18, 12, 0, 0))
monkeypatch.setattr("app._dashboard_now", fake_mod)
```

- [ ] **Step 4: 跑测试，PASS**

Run: `.venv/bin/python -m pytest tests/test_web_plan.py -k dashboard -v`
Expected: PASS

- [ ] **Step 5: 跑全套，确认无回归**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 全绿

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_web_plan.py
git commit -m "feat(web): /api/dashboard returns 4 sections JSON"
```

---

## Task 8: DASHBOARD_HTML 片段 + JS 轮询

**Files:**
- Modify: `app.py`（新增 `DASHBOARD_HTML` 常量、`startDashboard()` JS、在 PAGE 和 PLAN_PAGE 顶部插入）

- [ ] **Step 1: 在 PAGE 顶部插入 dashboard 容器**

`PAGE` 模板里 ` <h1 class="mb-3">每日选股结果</h1>` 后插入：

```html
<div id="dashboard"></div>
<script src="/static/dashboard.js"></script>
<script>startDashboard();</script>
```

> BootCDN 的 jQuery 已加载；`dashboard.js` 在 `static/` 目录下。

- [ ] **Step 2: 同样在 PLAN_PAGE 插入**

`PLAN_PAGE` 模板里 ` <div class="d-flex align-items-center mb-3">` 之前插入：

```html
<div id="dashboard"></div>
<script src="/static/dashboard.js"></script>
<script>startDashboard();</script>
```

- [ ] **Step 3: 创建 `static/dashboard.js`**

新建 `static/dashboard.js`：

```js
function freshnessBadge(f) {
  if (!f) return '<span class="badge bg-secondary">—</span>';
  return {fresh: 'bg-success', warm: 'bg-warning text-dark', stale: 'bg-danger'}[f] || 'bg-secondary';
}
function freshnessCn(f) { return {fresh:'新鲜', warm:'滞后', stale:'过期'}[f] || '—'; }

function sparkline(pnl) {
  if (!pnl.length) return '<span class="text-muted small">无</span>';
  // pnl ASC order for left-to-right time
  const xs = [...pnl].reverse();
  const w = 80, h = 24, max = Math.max(...xs.map(p => Math.abs(p.pnl_pct)), 1);
  const step = w / Math.max(xs.length - 1, 1);
  const mid = h / 2;
  const pts = xs.map((p, i) => `${i * step},${mid - (p.pnl_pct / max) * mid}`).join(' ');
  const sum = xs.reduce((s, p) => s + p.pnl_pct, 0);
  const color = sum >= 0 ? '#198754' : '#dc3545';
  return `<svg width="${w}" height="${h}" style="vertical-align:middle"><polyline fill="none" stroke="${color}" stroke-width="1.5" points="${pts}"/></svg> <small class="${sum>=0?'text-success':'text-danger'}">${sum>=0?'+':''}${sum.toFixed(2)}%</small>`;
}

function renderDashboard(d) {
  const lr = d.last_refresh;
  const tp = d.today_plan;
  const op = d.open_positions;
  const pnl = d.pnl_5d;

  const card = (title, body) => `
    <div class="col">
      <div class="card h-100 shadow-sm">
        <div class="card-body py-2">
          <div class="text-muted small mb-1">${title}</div>
          ${body}
        </div>
      </div>
    </div>`;

  const lrHtml = lr
    ? `<div class="d-flex align-items-center">
         <span class="badge ${freshnessBadge(lr.freshness)} me-2">${freshnessCn(lr.freshness)}</span>
         <strong>${lr.date}</strong>
       </div>
       <small class="text-muted">${lr.ago_hours}h 前</small>`
    : '<span class="text-muted">无数据</span>';

  const tpHtml = `
    <div class="d-flex gap-2 flex-wrap mb-1">
      <span class="badge bg-success">买入 ${tp.buy}</span>
      <span class="badge bg-secondary">持有 ${tp.hold}</span>
      <span class="badge bg-warning text-dark">退出 ${tp.exit}</span>
      ${tp.failed ? `<span class="badge bg-danger">失败 ${tp.failed}</span>` : ''}
    </div>
    <small class="text-muted">合计仓位 ${(tp.size_total*100).toFixed(1)}% · <a href="/plan">查看 →</a></small>`;

  const opHtml = op.count
    ? `<div><strong>${op.count}</strong> <small class="text-muted">只 · ${(op.size_total*100).toFixed(1)}%</small></div>
       <small class="${op.avg_unrealized_pct>=0?'text-success':'text-danger'}">
         ${op.avg_unrealized_pct==null?'—':(op.avg_unrealized_pct>=0?'+':'')+op.avg_unrealized_pct+'%'}
       </small>
       <table class="table table-sm mb-0 mt-1" style="font-size:.8rem">
         ${op.items.map(it => `<tr><td><code>${it.code}</code></td><td class="text-end ${it.unrealized_pct>=0?'text-success':'text-danger'}">${it.unrealized_pct==null?'—':(it.unrealized_pct>=0?'+':'')+it.unrealized_pct+'%'}</td></tr>`).join('')}
       </table>`
    : '<span class="text-muted">无持仓</span>';

  const pnlHtml = pnl.length
    ? sparkline(pnl)
    : '<span class="text-muted small">暂无收益</span>';

  $('#dashboard').html(`
    <div class="row row-cols-1 row-cols-md-2 row-cols-xl-4 g-2 mb-3">
      ${card('运行状态', lrHtml)}
      ${card('今日 plan', tpHtml)}
      ${card('持仓概览', opHtml)}
      ${card('最近 5 日收益', pnlHtml)}
    </div>`);
}

function startDashboard() {
  function tick() {
    if (document.hidden) return;
    $.getJSON('/api/dashboard')
      .done(renderDashboard)
      .fail(function () {
        $('#dashboard').html('<div class="text-muted small mb-3">dashboard 刷新失败</div>');
      });
  }
  tick();
  setInterval(tick, 15000);
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden) tick();
  });
}
```

- [ ] **Step 4: 让 Flask 提供 `/static/dashboard.js`**

Flask 默认从 `static/` 提供静态文件。无需路由；只要文件存在即可。

- [ ] **Step 5: 在 PAGE 的同步完成回调里加一行 refresh dashboard**

`poll` 函数的 `data.status === 'done'` 分支：

```js
if (window.refreshDashboard) window.refreshDashboard();
```

并在 `dashboard.js` 末尾：

```js
window.refreshDashboard = function () { tick(); };
```

同理 PLAN_PAGE 的 `pollBuild` 完成分支加同一行。

- [ ] **Step 6: 写测试确认 dashboard div + 静态文件可达**

`tests/test_web_plan.py` 追加：

```python
def test_dashboard_partial_present_on_both_pages(plan_db):
    app = create_app(db_path=plan_db)
    client = app.test_client()
    for path in ("/", "/plan"):
            r = client.get(path)
            assert b'<div id="dashboard"></div>' in r.data
            assert b'startDashboard();' in r.data


def test_dashboard_js_served(client_of_app):
    """dashboard.js is served at /static/dashboard.js."""
    import tempfile
    from db_repository import open_db
    from app import create_app
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    open_db(path).close()
    app = create_app(db_path=path)
    client = app.test_client()
    resp = client.get("/static/dashboard.js")
    assert resp.status_code == 200
    assert b"startDashboard" in resp.data
```

- [ ] **Step 7: 跑测试，PASS**

Run: `.venv/bin/python -m pytest tests/test_web_plan.py -k dashboard -v`
Expected: PASS

- [ ] **Step 8: 全套测试**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 全绿

- [ ] **Step 9: Commit**

```bash
git add app.py static/dashboard.js tests/test_web_plan.py
git commit -m "feat(web): dashboard top bar on / and /plan, 15s polling"
```

---

## Task 9: 手工冒烟 + 重启服务

- [ ] **Step 1: 重启 server**

Run: `./run_web.sh restart && curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8000/`
Expected: 200

- [ ] **Step 2: 浏览器打开 `/` 和 `/plan`，目检 4 块卡片渲染**

验证点：
- [ ] dashboard 出现在页面顶部
- [ ] 各卡片数值与查询一致
- [ ] 切到别的标签页 15s 后回来，dashboard 重新拉一次
- [ ] 在 `/` 上点「同步行情并重算」完成后，dashboard 数字刷新

- [ ] **Step 3: 提交（如有未提交）**

```bash
git status
# 若还有未提交，按内容补 commit
```

---

## Self-Review

**Spec coverage check:**
- ✓ 单端点 /api/dashboard → Task 7
- ✓ 4 块 JSON shape → Tasks 3-6 + Task 7
- ✓ freshness 阈值 → Task 7 (in endpoint) + Task 8 (JS badge class)
- ✓ daily_picks.updated_at 迁移 → Task 1
- ✓ pick_history 写 updated_at → Task 2
- ✓ DASHBOARD_HTML 共享片段 → Task 8
- ✓ startDashboard() + 15s 轮询 + visibilitychange → Task 8
- ✓ refreshDashboard() 在 sync/plan 完成时触发 → Task 8 Step 5
- ✓ 测试覆盖（db_repository + web）→ Tasks 3-8
- ✓ 错误处理（空 DB、404、刷新失败）→ Task 7 + Task 8

**Placeholder scan:** no TBD/TODO.

**Type consistency:**
- `get_last_refresh` returns `Optional[Dict]` with keys `{date, updated_at}` → consumed in endpoint ✓
- `get_today_plan_summary` keys `{date, buy, hold, exit, size_total, failed}` → JSON sample matches ✓
- `get_open_positions_with_unrealized` keys `{count, size_total, avg_unrealized_pct, items[]}` → JSON sample matches ✓
- `get_recent_pnl` returns `[{date, pnl_pct}]` → JSON sample matches ✓
- `freshness` ∈ {fresh, warm, stale} → both endpoint and JS use same vocabulary ✓