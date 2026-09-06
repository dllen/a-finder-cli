# 持仓组合 / Portfolio Tiers Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增按资金档位（5W–50W 共 10 档）独立跟踪的 paper-trade 组合：每个 tier 独立 plan / 持仓 / 事件 / 收益计算，Web 持仓组合页横向对比 + 详情 drill-down，workflow 每日自动跑全档 plan。

**Architecture:** schema 迁移加 `portfolio TEXT DEFAULT 'default'` 列到 `trade_plan` / `open_positions` / `trade_events`；`build_plan` 加 `portfolio=` 参数 + 新增 `build_all_portfolios()` 共享 picks 读取；repo 新增 `compute_portfolio_pnl`；CLI `plan build --portfolio` + 新增 `plan build-all`；Flask `/portfolio` 页 + `/api/portfolio/*`；静态导出到 `site/data/portfolio/`；workflow 追加 `plan build-all` 步。

**Tech Stack:** Python 3.11+、sqlite3、Flask、Bootstrap5、Chart.js（已静态化）、pytest、TDD。

**Spec:** `docs/superpowers/specs/2026-09-07-portfolio-tiers-design.md`

---

## Global Constraints

- 不引入新依赖（Flask / Bootstrap5.3.3 / jQuery / Popper.js / Chart.js 已有）。
- 数据迁移一律走 `db/migrations/YYYY_MM_DD_*.sql`，**不**改 `db_schema.py`。
- `trade_plan` UNIQUE 重建为 `(plan_date, portfolio, code, action)`，用 table-swap 迁移。
- `open_positions` 加 `portfolio` 列 + `(portfolio, code)` partial unique index (`WHERE status='open'`)。
- `trade_events` 加 `portfolio` 列 + 索引；event_id AUTOINCREMENT 不变。
- 历史数据自动归 `portfolio='default'`；回滚易（迁移幂等）。
- Capital tier label 默认 `f"{c//10000}W"`（如 `100000` → `"10W"`）。
- 默认 portfolio 名称 `'default'`；CLI `--portfolio` 仅单 label；`plan build-all` 跑 `CAPITAL_TIERS` 全 10 档。
- 测试基线 230 passed；目标 ≥ 260 passed。
- `--backfill` 默认 False；`plan build-all` 支持 `--backfill` + `--since YYYY-MM-DD`。
- `--strategy` 默认 `linyuan`；通过 `pick_history.run_picks` 触发选股写入 `daily_picks`。

---

## File Structure

新文件：
- `db/migrations/2026_09_07_portfolio_tier.sql` — trade_plan table-swap + open_positions/trade_events 加列
- `tests/test_migration_portfolio_tier.py` — 迁移测试
- `tests/test_db_repository_portfolio.py` — repo helpers 测试
- `tests/test_plan_builder_portfolio.py` — `build_plan(portfolio=)` 测试
- `tests/test_plan_builder_build_all.py` — `build_all_portfolios()` 测试
- `tests/test_cli_plan_portfolio.py` — `plan build --portfolio` + `plan build-all` parser 测试
- `tests/test_api_portfolio.py` — Flask API 路由测试
- `tests/test_export_portfolio.py` — 静态导出测试
- `docs/superpowers/plans/2026-09-07-portfolio-tiers.md` — 本文件

修改：
- `db_repository.py` — `insert_trade_plan` / `insert_open_position` / `accumulate_open_position` / `close_open_position` / `insert_trade_event` / `get_open_positions_with_unrealized` 加 `portfolio=` kwarg；新增 `compute_portfolio_pnl`、`get_trade_plan_by_date_portfolio`
- `plan_builder.py` — `build_plan` 加 `portfolio=` + `_picks` 内部 kwarg；新增 `build_all_portfolios()`；`_paper_trade` 把 portfolio 透传给所有 repo helpers
- `cli_layer.py` — `_run_plan` 加 `--portfolio`；新增 `plan build-all` subcommand + `_run_plan_build_all`
- `app.py` — `/portfolio` 路由 + `/api/portfolio/summary` + `/api/portfolio/<label>` + `PORTFOLIO_BODY` + `PORTFOLIO_SCRIPT`
- `export_json.py` — 新增 `export_portfolio()`；`export()` 末尾调用
- `.github/workflows/daily-sync-export.yml` — 在 `plan` step 后追加 `Build all tier portfolios`
- `README.md` — 常用命令 + 持仓组合章节
- `_TIER_FILTER_OPTIONS` / `_nav` 在 `app.py` 复用，portfolio page 不需要新 filter

---

## Task 1: 迁移 — `portfolio` 列 + 重建 trade_plan UNIQUE

**Files:**
- Create: `db/migrations/2026_09_07_portfolio_tier.sql`
- Create: `tests/test_migration_portfolio_tier.py`

**Interfaces:**
- Consumes: 现有 `trade_plan (plan_date, code, action)` UNIQUE；现有 `open_positions` / `trade_events` schema
- Produces:
  - `trade_plan` 增加 `portfolio TEXT NOT NULL DEFAULT 'default'`，UNIQUE 改 `(plan_date, portfolio, code, action)`，索引 `idx_trade_plan_portfolio_date(portfolio, plan_date)`
  - `open_positions` 增加 `portfolio TEXT NOT NULL DEFAULT 'default'`，partial unique index `uq_open_positions_portfolio_code_open(portfolio, code) WHERE status='open'`，索引 `idx_open_positions_portfolio(portfolio)`
  - `trade_events` 增加 `portfolio TEXT NOT NULL DEFAULT 'default'`，索引 `idx_trade_events_portfolio(portfolio)`
  - 历史数据 `portfolio` 默认 `'default'`

- [ ] **Step 1: 写失败测试**

```python
# tests/test_migration_portfolio_tier.py
import sqlite3
import pytest
from db_repository import open_db


@pytest.fixture
def migrated_db(tmp_path):
    path = str(tmp_path / "m.db")
    conn = open_db(path)
    yield path, conn
    conn.close()


def test_trade_plan_has_portfolio_column(migrated_db):
    _, conn = migrated_db
    cols = [r[1] for r in conn.execute("PRAGMA table_info(trade_plan)").fetchall()]
    assert "portfolio" in cols


def test_trade_plan_unique_includes_portfolio(migrated_db):
    _, conn = migrated_db
    idx = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='trade_plan'"
    ).fetchall()
    sqls = " ".join(r[0] or "" for r in idx)
    assert "plan_date" in sqls and "portfolio" in sqls and "code" in sqls and "action" in sqls
    # 原 UNIQUE 不再独立存在（合并到新 UNIQUE 里）
    # 通过尝试插入 (date, 'default', code, action) 两次确认 UNIQUE 工作
    conn.execute(
        """INSERT INTO trade_plan
           (plan_date, code, action, plan_price, size_pct, stop_price, tp_price,
            rr_ratio, status, rationale_json, params_hash, created_at, shares)
           VALUES ('2026-09-07','600519','buy',100,0.1,90,120,2.0,'ok','{}','h','2026-09-07',200)"""
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO trade_plan
               (plan_date, code, action, plan_price, size_pct, stop_price, tp_price,
                rr_ratio, status, rationale_json, params_hash, created_at, shares)
               VALUES ('2026-09-07','600519','buy',100,0.1,90,120,2.0,'ok','{}','h','2026-09-07',200)"""
        )


def test_trade_plan_same_code_different_portfolio_allowed(migrated_db):
    _, conn = migrated_db
    conn.execute(
        """INSERT INTO trade_plan
           (plan_date, code, action, plan_price, size_pct, stop_price, tp_price,
            rr_ratio, status, rationale_json, params_hash, created_at, shares)
           VALUES ('2026-09-07','600519','buy',100,0.1,90,120,2.0,'ok','{}','h','2026-09-07',200)"""
    )
    conn.execute(
        """INSERT INTO trade_plan
           (plan_date, portfolio, code, action, plan_price, size_pct, stop_price, tp_price,
            rr_ratio, status, rationale_json, params_hash, created_at, shares)
           VALUES ('2026-09-07','10W','600519','buy',100,0.1,90,120,2.0,'ok','{}','h','2026-09-07',200)"""
    )
    count = conn.execute("SELECT COUNT(*) FROM trade_plan WHERE code='600519'").fetchone()[0]
    assert count == 2


def test_open_positions_has_portfolio_column(migrated_db):
    _, conn = migrated_db
    cols = [r[1] for r in conn.execute("PRAGMA table_info(open_positions)").fetchall()]
    assert "portfolio" in cols


def test_open_positions_partial_unique_index(migrated_db):
    _, conn = migrated_db
    idx_names = [
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='open_positions'"
        ).fetchall()
    ]
    assert "uq_open_positions_portfolio_code_open" in idx_names


def test_open_positions_same_code_two_portfolios(migrated_db):
    _, conn = migrated_db
    for p in ("default", "10W"):
        conn.execute(
            """INSERT INTO open_positions
               (code, portfolio, entry_date, entry_price, size_pct, stop_price, tp_price, status, shares)
               VALUES ('600519', ?, '2026-09-07', 100, 0.1, 90, 120, 'open', 200)""",
            (p,),
        )
    count = conn.execute("SELECT COUNT(*) FROM open_positions WHERE code='600519'").fetchone()[0]
    assert count == 2


def test_trade_events_has_portfolio_column(migrated_db):
    _, conn = migrated_db
    cols = [r[1] for r in conn.execute("PRAGMA table_info(trade_events)").fetchall()]
    assert "portfolio" in cols


def test_trade_events_index_exists(migrated_db):
    _, conn = migrated_db
    idx_names = [
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='trade_events'"
        ).fetchall()
    ]
    assert "idx_trade_events_portfolio" in idx_names


def test_existing_data_defaults_to_default_portfolio(migrated_db):
    """升级已有 DB：trade_plan / open_positions / trade_events 历史行 portfolio='default'。"""
    _, conn = migrated_db
    # 通过迁到一个已有 trade_plan 行的 DB 验证：migrated_db 是新 DB 没有历史行
    # 改用单独测：open_db 在已包含 trade_plan 行的 DB 上跑迁移
    conn.execute(
        """INSERT INTO trade_plan
           (plan_date, code, action, plan_price, size_pct, stop_price, tp_price,
            rr_ratio, status, rationale_json, params_hash, created_at, shares)
           VALUES ('2026-09-01','000001','buy',50,0.1,40,70,2.0,'ok','{}','h','2026-09-01',200)"""
    )
    conn.commit()
    # 模拟重新打开（同进程 → _applied_migrations 已含此文件 → 跳过；这是幂等检查，
    # 历史数据迁移在第一次运行迁移时一次性执行）
    row = conn.execute(
        "SELECT portfolio FROM trade_plan WHERE plan_date='2026-09-01'"
    ).fetchone()
    assert row[0] == "default"
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `.venv/bin/python -m pytest tests/test_migration_portfolio_tier.py -v`
Expected: 全部失败（`portfolio` 列不存在 / 索引不存在 / IntegrityError 不来自新 UNIQUE）

- [ ] **Step 3: 写迁移 SQL**

```sql
-- db/migrations/2026_09_07_portfolio_tier.sql
-- 重建 trade_plan：portfolio 列 + UNIQUE 改 (plan_date, portfolio, code, action)
CREATE TABLE trade_plan_new (
    plan_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio        TEXT NOT NULL DEFAULT 'default',
    plan_date        TEXT NOT NULL,
    code             TEXT NOT NULL,
    action           TEXT NOT NULL CHECK(action IN ('buy','hold','exit')),
    plan_price       REAL NOT NULL,
    size_pct         REAL NOT NULL,
    stop_price       REAL NOT NULL,
    tp_price         REAL NOT NULL,
    rr_ratio         REAL NOT NULL,
    status           TEXT NOT NULL CHECK(status IN ('ok','failed')),
    reason           TEXT DEFAULT '',
    rationale_json   TEXT NOT NULL,
    params_hash      TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    shares           INTEGER,
    UNIQUE(plan_date, portfolio, code, action)
);
INSERT INTO trade_plan_new
  SELECT plan_id, 'default', plan_date, code, action, plan_price, size_pct,
         stop_price, tp_price, rr_ratio, status, reason, rationale_json,
         params_hash, created_at, shares
  FROM trade_plan;
DROP TABLE trade_plan;
ALTER TABLE trade_plan_new RENAME TO trade_plan;

CREATE INDEX IF NOT EXISTS idx_trade_plan_portfolio_date
  ON trade_plan(portfolio, plan_date);

-- open_positions: 加 portfolio 列 + partial unique index
ALTER TABLE open_positions ADD COLUMN portfolio TEXT NOT NULL DEFAULT 'default';

CREATE UNIQUE INDEX IF NOT EXISTS uq_open_positions_portfolio_code_open
  ON open_positions(portfolio, code)
  WHERE status = 'open';

CREATE INDEX IF NOT EXISTS idx_open_positions_portfolio
  ON open_positions(portfolio);

-- trade_events: 加 portfolio 列 + 索引
ALTER TABLE trade_events ADD COLUMN portfolio TEXT NOT NULL DEFAULT 'default';

CREATE INDEX IF NOT EXISTS idx_trade_events_portfolio
  ON trade_events(portfolio);
```

- [ ] **Step 4: 重跑测试**

Run: `.venv/bin/python -m pytest tests/test_migration_portfolio_tier.py -v`
Expected: 9 passed

- [ ] **Step 5: 跑全量回归确保没有破坏**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 230 passed（迁移对现有表是向后兼容的）

- [ ] **Step 6: Commit**

```bash
git add db/migrations/2026_09_07_portfolio_tier.sql tests/test_migration_portfolio_tier.py
git commit -m "feat(db): add portfolio column to trade_plan / open_positions / trade_events"
```

---

## Task 2: repo helpers — `portfolio=` 透传 + `compute_portfolio_pnl`

**Files:**
- Modify: `db_repository.py:517-715`（`insert_open_position` / `accumulate_open_position` / `close_open_position` / `insert_trade_event` / `get_open_positions_with_unrealized` 区域）
- Create: `tests/test_db_repository_portfolio.py`

**Interfaces:**
- Consumes: `plan_builder._paper_trade` 的现状（不传 portfolio）
- Produces:
  - `insert_open_position(..., portfolio='default') -> int`：新签名带 portfolio kwarg
  - `accumulate_open_position(..., portfolio='default') -> int`：按 portfolio 过滤现有 open 仓位
  - `close_open_position` 不变（用 `pos_id` 锁定）
  - `insert_trade_event(..., portfolio='default') -> int`：新签名带 portfolio kwarg
  - `get_open_positions_with_unrealized(..., portfolio=None) -> Dict`：filter；`None` = 不过滤（现有行为）
  - `compute_portfolio_pnl(conn, portfolio, *, initial_capital) -> Dict`：返回 `{portfolio, initial_capital, cash_remaining, position_value, total_value, realized_pnl, unrealized_pnl, return_rate}`
  - `get_trade_plan_by_date_and_portfolio(conn, plan_date, portfolio, params_hash) -> List[Dict]`：build_plan cache 改用 portfolio 维度

- [ ] **Step 1: 写失败测试**

```python
# tests/test_db_repository_portfolio.py
import os
import tempfile
import pytest
from db_repository import (
    open_db,
    insert_open_position,
    accumulate_open_position,
    insert_trade_event,
    close_open_position,
    get_open_positions_with_unrealized,
    compute_portfolio_pnl,
    insert_trade_plan,
)


@pytest.fixture
def fresh_db(tmp_path):
    path = str(tmp_path / "m.db")
    conn = open_db(path)
    yield path, conn
    conn.close()


def _seed_price(conn, code, close, trade_date):
    conn.execute(
        """INSERT INTO daily_prices (code, trade_date, open, close, high, low,
                                    volume, amount) VALUES (?,?,?,?,?,?,0,0)""",
        (code, trade_date, close, close, close, close),
    )
    conn.commit()


def test_insert_open_position_with_portfolio(fresh_db):
    _, conn = fresh_db
    pos_id = insert_open_position(
        conn, "600519", "2026-09-07", 100.0, 0.1, 90.0, 120.0,
        shares=200, portfolio="10W",
    )
    row = conn.execute(
        "SELECT portfolio FROM open_positions WHERE pos_id=?", (pos_id,)
    ).fetchone()
    assert row[0] == "10W"


def test_insert_open_position_default_portfolio(fresh_db):
    _, conn = fresh_db
    pos_id = insert_open_position(
        conn, "600519", "2026-09-07", 100.0, 0.1, 90.0, 120.0,
    )
    row = conn.execute(
        "SELECT portfolio FROM open_positions WHERE pos_id=?", (pos_id,)
    ).fetchone()
    assert row[0] == "default"


def test_accumulate_open_position_filters_by_portfolio(fresh_db):
    """同 code 在两个 portfolio 各有 open 仓位，accumulate 按 portfolio 隔离。"""
    _, conn = fresh_db
    insert_open_position(conn, "600519", "2026-09-01", 100.0, 0.1, 90.0, 120.0,
                        shares=200, portfolio="default")
    insert_open_position(conn, "600519", "2026-09-01", 100.0, 0.1, 90.0, 120.0,
                        shares=200, portfolio="10W")
    # accumulate 到 10W 不应改 default
    pos_id = accumulate_open_position(
        conn, "600519", 110.0, 0.1, 90.0, 120.0,
        shares_to_add=200, entry_date="2026-09-07", portfolio="10W",
    )
    rows = conn.execute(
        "SELECT portfolio, shares, entry_price FROM open_positions WHERE code='600519' ORDER BY portfolio"
    ).fetchall()
    assert rows[0][0] == "default"
    assert rows[0][1] == 200
    assert rows[0][2] == 100.0  # 未变
    assert rows[1][0] == "10W"
    assert rows[1][1] == 400  # accumulated
    assert abs(rows[1][2] - 105.0) < 0.01  # (200*100 + 200*110) / 400


def test_insert_trade_event_with_portfolio(fresh_db):
    _, conn = fresh_db
    eid = insert_trade_event(
        conn, "2026-09-07", "600519", "open", 100.5,
        shares=200, portfolio="10W", note="paper_fill",
    )
    row = conn.execute(
        "SELECT portfolio FROM trade_events WHERE event_id=?", (eid,)
    ).fetchone()
    assert row[0] == "10W"


def test_get_open_positions_with_unrealized_filtered(fresh_db):
    _, conn = fresh_db
    _seed_price(conn, "600519", 110.0, "2026-09-07")
    insert_open_position(conn, "600519", "2026-09-01", 100.0, 0.1, 90.0, 120.0,
                        shares=200, portfolio="default")
    insert_open_position(conn, "000001", "2026-09-01", 50.0, 0.1, 45.0, 60.0,
                        shares=200, portfolio="10W")
    out = get_open_positions_with_unrealized(conn, portfolio="10W")
    assert out["count"] == 1
    assert out["items"][0]["code"] == "000001"


def test_compute_portfolio_pnl_no_positions(fresh_db):
    _, conn = fresh_db
    out = compute_portfolio_pnl(conn, "10W", initial_capital=100000)
    assert out["portfolio"] == "10W"
    assert out["initial_capital"] == 100000
    assert out["cash_remaining"] == 100000
    assert out["position_value"] == 0.0
    assert out["total_value"] == 100000
    assert out["realized_pnl"] == 0.0
    assert out["unrealized_pnl"] == 0.0
    assert out["return_rate"] == 0.0


def test_compute_portfolio_pnl_with_open_position(fresh_db):
    _, conn = fresh_db
    _seed_price(conn, "600519", 110.0, "2026-09-07")
    insert_open_position(conn, "600519", "2026-09-01", 100.0, 0.1, 90.0, 120.0,
                        shares=200, portfolio="10W")
    out = compute_portfolio_pnl(conn, "10W", initial_capital=100000)
    # cost_basis = 100 * 200 = 20000
    # position_value = 110 * 200 = 22000
    # cash_remaining = 100000 - 20000 = 80000
    # unrealized_pnl = 22000 - 20000 = 2000
    assert out["cash_remaining"] == 80000
    assert out["position_value"] == 22000
    assert out["total_value"] == 102000
    assert out["unrealized_pnl"] == 2000
    assert out["realized_pnl"] == 0.0
    assert abs(out["return_rate"] - 0.02) < 1e-9


def test_compute_portfolio_pnl_after_close(fresh_db):
    """部分 close：realized + cash 调整。"""
    _, conn = fresh_db
    _seed_price(conn, "600519", 110.0, "2026-09-08")
    pos_id = insert_open_position(
        conn, "600519", "2026-09-01", 100.0, 0.1, 90.0, 120.0,
        shares=200, portfolio="10W",
    )
    insert_trade_event(conn, "2026-09-08", "600519", "close", 110.0,
                      shares=200, pnl_amt=2000.0, portfolio="10W")
    close_open_position(conn, pos_id, "2026-09-08", 110.0, "tp_hit")
    out = compute_portfolio_pnl(conn, "10W", initial_capital=100000)
    # buy 现金流出 20000; close 现金流入 22000（100*200 + pnl 2000）
    # cash_remaining = 100000 - 20000 + 22000 = 102000
    assert out["cash_remaining"] == 102000
    assert out["position_value"] == 0.0
    assert out["realized_pnl"] == 2000.0
    assert out["unrealized_pnl"] == 0.0
    assert abs(out["return_rate"] - 0.02) < 1e-9


def test_compute_portfolio_pnl_isolated_per_portfolio(fresh_db):
    _, conn = fresh_db
    _seed_price(conn, "600519", 110.0, "2026-09-07")
    insert_open_position(conn, "600519", "2026-09-01", 100.0, 0.1, 90.0, 120.0,
                        shares=200, portfolio="default")
    insert_open_position(conn, "600519", "2026-09-01", 100.0, 0.1, 90.0, 120.0,
                        shares=200, portfolio="10W")
    out_def = compute_portfolio_pnl(conn, "default", initial_capital=100000)
    out_10w = compute_portfolio_pnl(conn, "10W", initial_capital=100000)
    assert out_def["position_value"] == 22000
    assert out_10w["position_value"] == 22000
    # 互不影响


def test_get_open_positions_with_unrealized_default_includes_all(fresh_db):
    """不传 portfolio = 全部 portfolio 聚合（向后兼容）。"""
    _, conn = fresh_db
    _seed_price(conn, "600519", 110.0, "2026-09-07")
    insert_open_position(conn, "600519", "2026-09-01", 100.0, 0.1, 90.0, 120.0,
                        shares=200, portfolio="default")
    insert_open_position(conn, "000001", "2026-09-01", 50.0, 0.1, 45.0, 60.0,
                        shares=200, portfolio="10W")
    out = get_open_positions_with_unrealized(conn)
    assert out["count"] == 2  # 全部
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `.venv/bin/python -m pytest tests/test_db_repository_portfolio.py -v`
Expected: 全部失败（kwarg 不支持 / `compute_portfolio_pnl` 不存在）

- [ ] **Step 3: 改 `db_repository.py`**

在 `insert_open_position` 后增加 `portfolio='default'` kwarg；在 `accumulate_open_position` 把 `WHERE code=? AND status='open'` 改为 `WHERE code=? AND status='open' AND portfolio=?`；`close_open_position` 不变；`insert_trade_event` 加 `portfolio='default'` kwarg；`get_open_positions_with_unrealized` 加 `portfolio=None` kwarg（None 时去掉 `AND portfolio=?` 条件）；新增 `compute_portfolio_pnl`。

```python
# db_repository.py
def insert_open_position(
    conn: sqlite3.Connection,
    code: str,
    entry_date: str,
    entry_price: float,
    size_pct: float,
    stop_price: float,
    tp_price: float,
    shares: int = 200,
    portfolio: str = "default",
) -> int:
    cur = conn.execute(
        """INSERT INTO open_positions
        (code, portfolio, entry_date, entry_price, size_pct, stop_price, tp_price,
         status, shares)
        VALUES (?,?,?,?,?,?,?,?,?)""",
        (code, portfolio, entry_date, entry_price, size_pct, stop_price, tp_price,
         "open", shares),
    )
    conn.commit()
    return cur.lastrowid


def accumulate_open_position(
    conn: sqlite3.Connection,
    code: str,
    fill_price: float,
    size_pct: float,
    stop_price: float,
    tp_price: float,
    shares_to_add: int = 200,
    entry_date: str = "",
    portfolio: str = "default",
) -> int:
    row = conn.execute(
        "SELECT pos_id, shares, entry_price FROM open_positions "
        "WHERE code=? AND status='open' AND portfolio=? LIMIT 1",
        (code, portfolio),
    ).fetchone()
    if row is None:
        return insert_open_position(
            conn, code, entry_date, fill_price, size_pct, stop_price, tp_price,
            shares_to_add, portfolio,
        )
    pos_id, old_shares, old_entry = row
    old_shares = old_shares or 0
    old_entry = old_entry or 0.0
    new_shares = old_shares + shares_to_add
    new_entry = round((old_shares * old_entry + shares_to_add * fill_price) / new_shares, 4)
    conn.execute(
        """UPDATE open_positions
           SET shares=?, entry_price=?, stop_price=?, tp_price=?
           WHERE pos_id=?""",
        (new_shares, new_entry, stop_price, tp_price, pos_id),
    )
    conn.commit()
    return pos_id


def insert_trade_event(
    conn: sqlite3.Connection,
    plan_date: str,
    code: str,
    event_type: str,
    price: float,
    size_pct: Optional[float] = None,
    pnl_pct: Optional[float] = None,
    note: Optional[str] = None,
    shares: Optional[int] = None,
    pnl_amt: Optional[float] = None,
    portfolio: str = "default",
) -> int:
    cur = conn.execute(
        """INSERT INTO trade_events
        (plan_date, code, event_type, price, size_pct, pnl_pct, note, shares, pnl_amt,
         portfolio, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (plan_date, code, event_type, price, size_pct, pnl_pct, note, shares, pnl_amt,
         portfolio, dt.datetime.utcnow().isoformat(timespec="seconds")),
    )
    conn.commit()
    return cur.lastrowid


def get_open_positions_with_unrealized(
    conn: sqlite3.Connection,
    *,
    portfolio: Optional[str] = None,
) -> Dict:
    sql = (
        """SELECT op.code, op.entry_date, op.entry_price, op.size_pct,
                  op.stop_price, op.tp_price, op.shares,
                  dp.close AS close_price
           FROM open_positions op
           LEFT JOIN (
               SELECT code, close FROM daily_prices dp1
               WHERE trade_date = (SELECT MAX(trade_date) FROM daily_prices dp2
                                   WHERE dp2.code = dp1.code)
           ) dp ON dp.code = op.code
           WHERE op.status = 'open'"""
    )
    params: tuple = ()
    if portfolio is not None:
        sql += " AND op.portfolio = ?"
        params = (portfolio,)
    sql += " ORDER BY op.entry_date, op.code"
    cur = conn.execute(sql, params)
    items = []
    shares_total = 0
    floating_total = 0.0
    pct_sum = 0.0
    pct_count = 0
    for code, ed, ep, sz, sp, tp, shares, close in cur.fetchall():
        shares = shares or 0
        floating = None
        if close is not None and ep:
            floating = round((close - ep) * shares, 2)
            floating_total += floating
            unrealized_pct = (close - ep) / ep * 100
            pct_sum += unrealized_pct
            pct_count += 1
        shares_total += shares
        items.append({
            "code": code, "entry_date": ed, "entry_price": ep,
            "size_pct": sz, "stop_price": sp, "tp_price": tp,
            "shares": shares, "current_price": close,
            "floating_pnl": floating,
            "stop_pnl": round((sp - ep) * shares, 2) if (sp is not None and ep) else None,
            "tp_pnl": round((tp - ep) * shares, 2) if (tp is not None and ep) else None,
        })
    avg = round(pct_sum / pct_count, 2) if pct_count else None
    return {
        "count": len(items), "size_total": round(sum(i["size_pct"] for i in items), 4),
        "shares_total": shares_total, "floating_pnl": round(floating_total, 2),
        "avg_unrealized_pct": avg, "items": items[:3],
    }


def compute_portfolio_pnl(
    conn: sqlite3.Connection,
    portfolio: str,
    *,
    initial_capital: int,
) -> Dict:
    """现金倒推 + 持仓市值 + 已/未实现盈亏 + 收益率。"""
    # 1. realized_pnl: Σ trade_events.pnl_amt WHERE event_type='close' AND portfolio=?
    realized_pnl_row = conn.execute(
        "SELECT COALESCE(SUM(pnl_amt), 0.0) FROM trade_events "
        "WHERE event_type='close' AND portfolio=?",
        (portfolio,),
    ).fetchone()
    realized_pnl = float(realized_pnl_row[0] or 0.0)

    # 2. cash_remaining: initial − Σbuy_cost + Σclose_proceeds
    # buy_cost = Σ event price × shares WHERE event_type='open'
    # close_proceeds = Σ event price × shares WHERE event_type='close'
    buy_row = conn.execute(
        "SELECT COALESCE(SUM(price * shares), 0.0) FROM trade_events "
        "WHERE event_type='open' AND portfolio=?",
        (portfolio,),
    ).fetchone()
    close_row = conn.execute(
        "SELECT COALESCE(SUM(price * shares), 0.0) FROM trade_events "
        "WHERE event_type='close' AND portfolio=?",
        (portfolio,),
    ).fetchone()
    buy_cost = float(buy_row[0] or 0.0)
    close_proceeds = float(close_row[0] or 0.0)
    cash_remaining = round(initial_capital - buy_cost + close_proceeds, 2)

    # 3. position_value + unrealized_pnl: 查 open_positions + 最新 close
    pos_rows = conn.execute(
        """SELECT op.shares, op.entry_price,
                  (SELECT close FROM daily_prices dp
                   WHERE dp.code = op.code
                   ORDER BY trade_date DESC LIMIT 1) AS cur
           FROM open_positions op WHERE op.status='open' AND op.portfolio=?""",
        (portfolio,),
    ).fetchall()
    position_value = 0.0
    cost_basis = 0.0
    for shares, entry, cur in pos_rows:
        if not shares or not entry:
            continue
        if cur is not None:
            position_value += cur * shares
        cost_basis += entry * shares
    position_value = round(position_value, 2)
    unrealized_pnl = round(position_value - cost_basis, 2)
    total_value = round(cash_remaining + position_value, 2)
    return_rate = round(total_value / initial_capital - 1, 6) if initial_capital else 0.0
    return {
        "portfolio": portfolio,
        "initial_capital": initial_capital,
        "cash_remaining": cash_remaining,
        "position_value": position_value,
        "total_value": total_value,
        "realized_pnl": round(realized_pnl, 2),
        "unrealized_pnl": unrealized_pnl,
        "return_rate": return_rate,
    }
```

- [ ] **Step 4: 重跑测试**

Run: `.venv/bin/python -m pytest tests/test_db_repository_portfolio.py -v`
Expected: 10 passed

- [ ] **Step 5: 跑全量回归**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: 230 + 10 = 240 passed（默认 portfolio='default' 行为不变）

- [ ] **Step 6: Commit**

```bash
git add db_repository.py tests/test_db_repository_portfolio.py
git commit -m "feat(repo): portfolio kwarg on paper-trade helpers + compute_portfolio_pnl"
```

---

## Task 3: `plan_builder.build_plan` 加 `portfolio=` 参数

**Files:**
- Modify: `plan_builder.py:336-422`
- Create: `tests/test_plan_builder_portfolio.py`

**Interfaces:**
- Consumes: Task 2 的 `insert_trade_plan` / `accumulate_open_position` / `insert_trade_event` 加 `portfolio` kwarg
- Produces:
  - `build_plan(..., portfolio='default', _picks=None)` 新签名
  - 写出的 `trade_plan` / `open_positions` / `trade_events` 行带 portfolio
  - `PlanResult.portfolio: str` 字段
  - cache lookup 改用 `get_trade_plan_by_date_and_portfolio`（Task 3 一并新增到 `db_repository.py`）

- [ ] **Step 1: 在 `db_repository.py` 加 cache helper**

在 `get_trade_plan_by_date_and_hash` 后追加（共享测试覆盖）：

```python
def get_trade_plan_by_date_and_portfolio(
    conn: sqlite3.Connection,
    plan_date: str,
    portfolio: str,
    params_hash: str,
) -> List[Dict]:
    cur = conn.execute(
        "SELECT * FROM trade_plan WHERE plan_date=? AND portfolio=? AND params_hash=?",
        (plan_date, portfolio, params_hash),
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]
```

并把 `insert_trade_plan` 的 SQL 改为 `ON CONFLICT(plan_date, portfolio, code, action) DO UPDATE`（Task 1 迁移后 UNIQUE 已变更，需要 repo SQL 同步），并在 INSERT 字段加 `portfolio`：

```python
def insert_trade_plan(
    conn: sqlite3.Connection,
    row: "PlanRow",
    plan_date: str,
    params_hash: str,
    portfolio: str = "default",
) -> int:
    cur = conn.execute(
        """INSERT INTO trade_plan
        (plan_date, portfolio, code, action, plan_price, size_pct, stop_price, tp_price,
         rr_ratio, status, reason, rationale_json, params_hash, created_at, shares)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(plan_date, portfolio, code, action) DO UPDATE SET
         plan_price=excluded.plan_price,
         size_pct=excluded.size_pct,
         stop_price=excluded.stop_price,
         tp_price=excluded.tp_price,
         rr_ratio=excluded.rr_ratio,
         status=excluded.status,
         reason=excluded.reason,
         rationale_json=excluded.rationale_json,
         params_hash=excluded.params_hash,
         shares=excluded.shares""",
        (
            plan_date, portfolio, row.code, row.action, row.plan_price, row.size_pct,
            row.stop_price, row.tp_price, row.rr_ratio, row.status, row.reason,
            json.dumps(row.rationale), params_hash,
            dt.datetime.utcnow().isoformat(timespec="seconds"),
            row.shares,
        ),
    )
    conn.commit()
    return cur.lastrowid if cur.rowcount > 0 else 0
```

- [ ] **Step 2: 写失败测试**

```python
# tests/test_plan_builder_portfolio.py
import os
import tempfile
import pytest
from db_repository import open_db


@pytest.fixture
def fresh_db(tmp_path):
    path = str(tmp_path / "m.db")
    conn = open_db(path)
    yield path, conn
    conn.close()


def _seed_pick(conn, *, date, code, score=2.0, buy=100.0, stop=80.0, target=140.0):
    conn.execute(
        """INSERT INTO daily_picks (date, rank, kind, code, name, strategy,
             buy, stop, target, score)
           VALUES (?, 1, 'test', ?, ?, 'test', ?, ?, ?, ?)""",
        (date, code, code, buy, stop, target, score),
    )
    conn.commit()


def _seed_price(conn, code, close, trade_date):
    conn.execute(
        """INSERT INTO daily_prices (code, trade_date, open, close, high, low,
                                    volume, amount) VALUES (?,?,?,?,?,?,0,0)""",
        (code, trade_date, close, close, close, close),
    )
    conn.commit()


def test_build_plan_writes_portfolio_label(fresh_db):
    path, conn = fresh_db
    _seed_pick(conn, date="2026-09-07", code="600519")
    _seed_price(conn, "600519", 100.0, "2026-09-07")
    conn.close()

    from plan_builder import build_plan
    result = build_plan(
        "2026-09-07", path,
        params={"regime": "BULL", "capital": 100000},
        portfolio="10W",
    )
    assert result.portfolio == "10W"
    conn = open_db(path)
    try:
        rows = conn.execute("SELECT portfolio FROM trade_plan").fetchall()
        opens = conn.execute("SELECT portfolio FROM open_positions").fetchall()
        events = conn.execute("SELECT portfolio FROM trade_events").fetchall()
    finally:
        conn.close()
    assert all(r[0] == "10W" for r in rows)
    assert all(r[0] == "10W" for r in opens)
    assert all(r[0] == "10W" for r in events)


def test_build_plan_default_portfolio(fresh_db):
    """不传 portfolio 时默认 'default'（向后兼容）。"""
    path, conn = fresh_db
    _seed_pick(conn, date="2026-09-07", code="600519")
    _seed_price(conn, "600519", 100.0, "2026-09-07")
    conn.close()

    from plan_builder import build_plan
    build_plan("2026-09-07", path, params={"regime": "BULL"})
    conn = open_db(path)
    try:
        rows = conn.execute("SELECT DISTINCT portfolio FROM trade_plan").fetchall()
    finally:
        conn.close()
    assert rows == [("default",)]


def test_build_plan_isolates_paper_fill_by_portfolio(fresh_db):
    """同 code 在两个 portfolio 都建仓 → open_positions 两条独立行。"""
    path, conn = fresh_db
    _seed_pick(conn, date="2026-09-07", code="600519")
    _seed_price(conn, "600519", 100.0, "2026-09-07")
    conn.close()

    from plan_builder import build_plan
    build_plan("2026-09-07", path, params={"regime": "BULL"}, portfolio="default")
    build_plan("2026-09-07", path, params={"regime": "BULL"}, portfolio="10W")
    conn = open_db(path)
    try:
        opens = conn.execute(
            "SELECT portfolio, code FROM open_positions ORDER BY portfolio"
        ).fetchall()
    finally:
        conn.close()
    assert opens == [("10W", "600519"), ("default", "600519")]


def test_build_plan_cache_key_includes_portfolio(fresh_db):
    """同 plan_date + params_hash 但不同 portfolio 应各自独立 cache。"""
    path, conn = fresh_db
    _seed_pick(conn, date="2026-09-07", code="600519")
    _seed_price(conn, "600519", 100.0, "2026-09-07")
    conn.close()

    from plan_builder import build_plan
    build_plan("2026-09-07", path, params={"regime": "BULL"}, portfolio="default")
    # 改变 portfolio 重新跑：应该重算（cache hit 只对相同 portfolio）
    res = build_plan("2026-09-07", path, params={"regime": "BULL"}, portfolio="10W")
    assert res.portfolio == "10W"
    conn = open_db(path)
    try:
        n = conn.execute(
            "SELECT COUNT(*) FROM trade_plan WHERE plan_date='2026-09-07'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert n == 2  # default + 10W 各一条 buy


def test_build_plan_picks_injected(fresh_db):
    """_picks kwarg 用于 build_all_portfolios：注入 picks 跳过 daily_picks 读取。"""
    path, conn = fresh_db
    _seed_price(conn, "600519", 100.0, "2026-09-07")
    conn.close()

    from plan_builder import build_plan
    picks = [{
        "code": "600519", "score": 2.0, "buy": 100.0,
        "stop": 80.0, "target": 140.0, "strategy": "test",
    }]
    res = build_plan(
        "2026-09-07", path,
        params={"regime": "BULL", "capital": 100000},
        portfolio="10W", _picks=picks,
    )
    assert res.num_picks == 1
```

- [ ] **Step 3: 跑测试，确认失败**

Run: `.venv/bin/python -m pytest tests/test_plan_builder_portfolio.py -v`
Expected: 全部失败

- [ ] **Step 4: 改 `plan_builder.py`**

`PlanResult` 加 `portfolio` 字段；`build_plan` 签名扩展；cache 改用 portfolio；`_paper_trade` 透传 portfolio。

```python
# plan_builder.py
@dataclass
class PlanResult:
    plan_date: str
    portfolio: str = "default"
    rows: List[PlanRow] = field(default_factory=list)
    num_picks: int = 0
    num_open_positions: int = 0
    sanity_passed: bool = True
    sanity_reasons: List[str] = field(default_factory=list)


def build_plan(
    plan_date: str,
    db_path: str,
    params: Optional[Dict[str, Any]] = None,
    slippage: float = 0.001,
    paper_trade: bool = True,
    include_carryover: bool = True,
    *,
    portfolio: str = "default",
    _picks: Optional[List[Dict[str, Any]]] = None,
) -> PlanResult:
    params = params or {}
    max_single = float(params.get("max_single", 0.15))
    max_total = float(params.get("max_total", 0.95))
    regime = _regime_from_str(params.get("regime", "SIDEWAYS"))
    capital = float(params.get("capital") or DEFAULT_CAPITAL)
    risk_manager = RiskManager()
    phash = params_hash(params)

    # Cache hit: 同 (plan_date, portfolio, params_hash) 已持久化 → 跳过
    from db_repository import get_trade_plan_by_date_and_portfolio
    cache_conn = open_db(db_path)
    try:
        cached_rows = get_trade_plan_by_date_and_portfolio(
            cache_conn, plan_date, portfolio, phash,
        )
    finally:
        cache_conn.close()
    if cached_rows:
        return PlanResult(
            plan_date=plan_date,
            portfolio=portfolio,
            rows=[_row_from_db(r) for r in cached_rows],
            num_picks=0,
            num_open_positions=0,
            sanity_passed=all(r["status"] == "ok" for r in cached_rows),
            sanity_reasons=[],
        )

    conn = open_db(db_path)
    try:
        picks = _picks if _picks is not None else _read_picks(conn, plan_date)
        if _picks is None:
            # carryover 读取要按 portfolio 隔离
            opens = _read_open_positions(conn, portfolio=portfolio)
            codes = list({o["code"] for o in opens})
            current_prices = _lookup_current_prices(conn, codes)
        else:
            opens = []
            current_prices = {}
    finally:
        conn.close()

    rows: List[PlanRow] = []
    rows.extend(_build_buy_rows(picks, regime, risk_manager, capital))
    if include_carryover:
        rows.extend(_build_carryover_rows(opens, current_prices))

    reasons = _apply_sanity_gate(rows, max_single, max_total)

    write_conn = open_db(db_path)
    try:
        for r in rows:
            insert_trade_plan(write_conn, r, plan_date, phash, portfolio=portfolio)
    finally:
        write_conn.close()

    if paper_trade:
        _paper_trade(rows, plan_date, db_path, slippage, portfolio=portfolio)

    return PlanResult(
        plan_date=plan_date,
        portfolio=portfolio,
        rows=rows,
        num_picks=len(picks),
        num_open_positions=len(opens),
        sanity_passed=(not reasons),
        sanity_reasons=reasons,
    )


def _read_open_positions(
    conn, *, portfolio: str = "default",
) -> List[Dict[str, Any]]:
    """按 portfolio 过滤的 open positions。"""
    cur = conn.execute(
        "SELECT * FROM open_positions WHERE status='open' AND portfolio=? "
        "ORDER BY entry_date, code",
        (portfolio,),
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _paper_trade(
    rows: List[PlanRow],
    plan_date: str,
    db_path: str,
    slippage: float,
    *,
    portfolio: str = "default",
) -> None:
    conn = open_db(db_path)
    try:
        for r in rows:
            if r.action == "buy" and r.status == "ok":
                if r.shares <= 0:
                    continue
                existing = conn.execute(
                    "SELECT 1 FROM trade_events "
                    "WHERE code=? AND plan_date=? AND event_type='open' "
                    "AND portfolio=? LIMIT 1",
                    (r.code, plan_date, portfolio),
                ).fetchone()
                if existing:
                    continue
                fill_price = round(r.plan_price * (1 + slippage), 4)
                accumulate_open_position(
                    conn, r.code, fill_price, r.size_pct,
                    r.stop_price, r.tp_price, r.shares,
                    entry_date=plan_date, portfolio=portfolio,
                )
                insert_trade_event(
                    conn, plan_date, r.code, "open",
                    fill_price, r.size_pct, shares=r.shares,
                    note="paper_fill", portfolio=portfolio,
                )
            elif r.action == "exit" and r.status == "ok":
                row = conn.execute(
                    "SELECT pos_id, entry_price, shares FROM open_positions "
                    "WHERE code=? AND status='open' AND portfolio=? "
                    "ORDER BY entry_date LIMIT 1",
                    (r.code, portfolio),
                ).fetchone()
                if row:
                    pos_id, entry_price, shares = row
                    close_reason = (r.rationale or {}).get("trigger", "manual")
                    close_open_position(conn, pos_id, plan_date,
                                        r.plan_price, close_reason)
                    pnl_amt = round((r.plan_price - entry_price) * (shares or 0), 2)
                    insert_trade_event(
                        conn, plan_date, r.code, "close",
                        r.plan_price, None, shares=shares, pnl_amt=pnl_amt,
                        note="paper_close", portfolio=portfolio,
                    )
    finally:
        conn.close()
```

- [ ] **Step 5: 重跑测试**

Run: `.venv/bin/python -m pytest tests/test_plan_builder_portfolio.py -v`
Expected: 5 passed

- [ ] **Step 6: 跑全量回归**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: ≥ 245 passed

- [ ] **Step 7: Commit**

```bash
git add plan_builder.py db_repository.py tests/test_plan_builder_portfolio.py
git commit -m "feat(plan): build_plan takes portfolio= kwarg, persists per-portfolio rows"
```

---

## Task 4: `plan_builder.build_all_portfolios` — 共享 picks 跑全档

**Files:**
- Modify: `plan_builder.py`（在 `_paper_trade` 之后追加）
- Create: `tests/test_plan_builder_build_all.py`

**Interfaces:**
- Consumes: Task 3 的 `build_plan(portfolio=, _picks=)`；`config.CAPITAL_TIERS`
- Produces:
  - `build_all_portfolios(db_path, plan_date, params, *, strategy='linyuan', tiers=None, portfolio_label_fn=None, progress=None) -> List[PlanResult]`
  - 默认 `tiers=CAPITAL_TIERS`；默认 `portfolio_label_fn=lambda c: f"{c//10000}W"`
  - 共享一次 `daily_picks` 读取；每 tier 独立事务（一个 tier 失败不影响其他）
  - 每个 tier 共享 `_picks` 注入（避免重复 IO）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_plan_builder_build_all.py
import os
import tempfile
import pytest
from db_repository import open_db, compute_portfolio_pnl


@pytest.fixture
def fresh_db(tmp_path):
    path = str(tmp_path / "m.db")
    conn = open_db(path)
    yield path, conn
    conn.close()


def _seed_pick(conn, *, date, code, score=2.0, buy=100.0, stop=80.0, target=140.0):
    conn.execute(
        """INSERT INTO daily_picks (date, rank, kind, code, name, strategy,
             buy, stop, target, score)
           VALUES (?, 1, 'test', ?, ?, 'test', ?, ?, ?, ?)""",
        (date, code, code, buy, stop, target, score),
    )
    conn.commit()


def _seed_price(conn, code, close, trade_date):
    conn.execute(
        """INSERT INTO daily_prices (code, trade_date, open, close, high, low,
                                    volume, amount) VALUES (?,?,?,?,?,?,0,0)""",
        (code, trade_date, close, close, close, close),
    )
    conn.commit()


def test_build_all_portfolios_default_tiers(fresh_db):
    """不传 tiers 时跑全 10 档。"""
    path, conn = fresh_db
    _seed_pick(conn, date="2026-09-07", code="600519")
    _seed_price(conn, "600519", 100.0, "2026-09-07")
    conn.close()

    from plan_builder import build_all_portfolios
    results = build_all_portfolios(
        "2026-09-07", path, params={"regime": "BULL"},
    )
    # 5W/10W/.../50W = 10 档
    assert len(results) == 10
    portfolios = {r.portfolio for r in results}
    assert portfolios == {"5W", "10W", "15W", "20W", "25W",
                          "30W", "35W", "40W", "45W", "50W"}


def test_build_all_portfolios_custom_tiers(fresh_db):
    path, conn = fresh_db
    _seed_pick(conn, date="2026-09-07", code="600519")
    _seed_price(conn, "600519", 100.0, "2026-09-07")
    conn.close()

    from plan_builder import build_all_portfolios
    results = build_all_portfolios(
        "2026-09-07", path, params={"regime": "BULL"},
        tiers=[50000, 200000],
    )
    assert {r.portfolio for r in results} == {"5W", "20W"}


def test_build_all_portfolios_shares_scale_by_capital(fresh_db):
    path, conn = fresh_db
    _seed_pick(conn, date="2026-09-07", code="600519", buy=10.0)
    _seed_price(conn, "600519", 10.0, "2026-09-07")
    conn.close()

    from plan_builder import build_all_portfolios
    results = build_all_portfolios(
        "2026-09-07", path, params={"regime": "BULL"},
        tiers=[50000, 100000, 500000],
    )
    by_label = {r.portfolio: r for r in results}
    # 100 块一股 / 100手 = 10000 → 5W 只能 5 手；10W = 10；50W = 50
    # 这里 buy=10 单股便宜，所以更大 tier 应有更多手
    shares_5w = sum(r.shares for r in by_label["5W"].rows if r.action == "buy")
    shares_50w = sum(r.shares for r in by_label["50W"].rows if r.action == "buy")
    assert shares_50w > shares_5w


def test_build_all_portfolios_per_tier_isolation(fresh_db):
    """一档失败不影响其他档（per-tier 独立事务）。"""
    path, conn = fresh_db
    _seed_pick(conn, date="2026-09-07", code="600519")
    _seed_price(conn, "600519", 100.0, "2026-09-07")
    conn.close()

    from plan_builder import build_all_portfolios
    # 故意让 10W 失败：用一个异常 params（price=0 让所有 buy 行 stop_above_entry? 简化：让 plan 失败）
    # 这里采用构造一个 buy 行 stop above entry 的场景
    # 直接 patch build_plan 在 10W 抛异常
    import plan_builder as pb
    orig_build = pb.build_plan
    call_count = {"n": 0}

    def flaky(*args, **kwargs):
        call_count["n"] += 1
        portfolio = kwargs.get("portfolio", "default")
        if portfolio == "10W":
            raise RuntimeError("simulated 10W failure")
        return orig_build(*args, **kwargs)

    pb.build_plan = flaky
    try:
        results = build_all_portfolios(
            "2026-09-07", path, params={"regime": "BULL"},
            tiers=[50000, 100000, 200000],
        )
    finally:
        pb.build_plan = orig_build

    # 5W + 20W 成功，10W 在 results 里以失败占位或缺失
    successful = [r for r in results if r is not None]
    failed = [r for r in results if r is None]
    assert len(successful) == 2
    assert len(failed) == 1
    assert call_count["n"] == 3


def test_build_all_portfolios_progress_callback(fresh_db):
    path, conn = fresh_db
    _seed_pick(conn, date="2026-09-07", code="600519")
    _seed_price(conn, "600519", 100.0, "2026-09-07")
    conn.close()

    progress_calls = []

    def progress(pct, msg):
        progress_calls.append((pct, msg))

    from plan_builder import build_all_portfolios
    build_all_portfolios(
        "2026-09-07", path, params={"regime": "BULL"},
        tiers=[50000, 100000], progress=progress,
    )
    # 应至少有 start + 每个 tier + end
    assert len(progress_calls) >= 4
    assert progress_calls[0][0] == 0  # start at 0%
    assert progress_calls[-1][0] == 100  # end at 100%
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `.venv/bin/python -m pytest tests/test_plan_builder_build_all.py -v`
Expected: 全部失败（`build_all_portfolios` 不存在）

- [ ] **Step 3: 实现 `build_all_portfolios`**

```python
# plan_builder.py
def build_all_portfolios(
    db_path: str,
    plan_date: str,
    params: Optional[Dict[str, Any]] = None,
    *,
    strategy: str = "linyuan",
    tiers: Optional[Sequence[int]] = None,
    portfolio_label_fn: Optional[Callable[[int], str]] = None,
    progress: Optional[Callable[[int, str], None]] = None,
) -> List[Optional[PlanResult]]:
    """一次性为所有 tier 跑 build_plan。共享一次 picks 读取；per-tier 独立事务。

    Args:
        tiers: 默认走 config.CAPITAL_TIERS
        portfolio_label_fn: 默认 f"{c//10000}W"
        progress(pct, msg): 0-100 的进度回调

    Returns:
        List of PlanResult or None (失败的 tier)。
    """
    from config import CAPITAL_TIERS

    tiers = list(tiers) if tiers else CAPITAL_TIERS
    label_fn = portfolio_label_fn or (lambda c: f"{c // 10000}W")

    def _emit(pct: int, msg: str) -> None:
        if progress:
            progress(pct, msg)

    _emit(0, f"plan build-all starting: tiers={len(tiers)}")

    # 共享一次 picks 读取（防止重复 IO）
    conn = open_db(db_path)
    try:
        shared_picks = _read_picks(conn, plan_date)
    finally:
        conn.close()

    results: List[Optional[PlanResult]] = []
    n = len(tiers)
    for i, capital in enumerate(tiers):
        label = label_fn(capital)
        tier_params = dict(params or {})
        tier_params["capital"] = capital
        try:
            res = build_plan(
                plan_date, db_path, params=tier_params,
                portfolio=label, _picks=shared_picks,
            )
            results.append(res)
            _emit(int((i + 1) / n * 100), f"{label} done picks={res.num_picks}")
        except Exception as e:
            results.append(None)
            _emit(int((i + 1) / n * 100), f"{label} FAILED: {e}")

    return results
```

需要在文件顶部添加 imports：

```python
from typing import Callable, Sequence
```

- [ ] **Step 4: 重跑测试**

Run: `.venv/bin/python -m pytest tests/test_plan_builder_build_all.py -v`
Expected: 5 passed

- [ ] **Step 5: 跑全量回归**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: ≥ 250 passed

- [ ] **Step 6: Commit**

```bash
git add plan_builder.py tests/test_plan_builder_build_all.py
git commit -m "feat(plan): build_all_portfolios — shared picks, per-tier isolation"
```

---

## Task 5: CLI — `plan build --portfolio` + `plan build-all`

**Files:**
- Modify: `cli_layer.py:231-249`（`plan_parser`）+ `_run_plan` 函数（`cli_layer.py:360-455`）
- Create: `tests/test_cli_plan_portfolio.py`

**Interfaces:**
- Consumes: Task 3/4 的 `build_plan(portfolio=)` / `build_all_portfolios()`
- Produces:
  - `plan build --portfolio "10W" --capital 100000` 单 tier 入口
  - `plan build-all --strategy linyuan` 全档入口
  - `plan build-all --tiers 50000,100000,200000 --backfill` 自定义 tier 集合 + 回填
  - `plan build-all --since 2025-01-01 --backfill` 限定回算起点

- [ ] **Step 1: 写失败测试**

```python
# tests/test_cli_plan_portfolio.py
import argparse
import os
import tempfile
import pytest


def test_plan_build_parser_accepts_portfolio():
    from cli_layer import build_parser
    p = build_parser()
    args = p.parse_args(["plan", "build", "--portfolio", "10W", "--capital", "100000"])
    assert args.command == "plan"
    assert args.action == "build"
    assert args.portfolio == "10W"
    assert args.capital == 100000


def test_plan_build_parser_default_portfolio():
    from cli_layer import build_parser
    args = p.parse_args(["plan", "build"]) if False else build_parser().parse_args(["plan", "build"])
    assert args.portfolio == "default"


def test_plan_build_all_parser_accepts_strategy():
    from cli_layer import build_parser
    args = build_parser().parse_args(["plan", "build-all", "--strategy", "linyuan"])
    assert args.command == "plan"
    assert args.action == "build-all"
    assert args.strategy == "linyuan"


def test_plan_build_all_parser_accepts_tiers():
    from cli_layer import build_parser
    args = build_parser().parse_args(["plan", "build-all", "--tiers", "50000,100000,200000"])
    assert args.tiers == "50000,100000,200000"


def test_plan_build_all_parser_accepts_backfill_and_since():
    from cli_layer import build_parser
    args = build_parser().parse_args(
        ["plan", "build-all", "--backfill", "--since", "2025-01-01"]
    )
    assert args.backfill is True
    assert args.since == "2025-01-01"


def test_plan_build_runs_with_portfolio(tmp_path, capsys):
    """端到端：plan build --portfolio 写 trade_plan 行带 portfolio。"""
    import sqlite3
    from db_repository import open_db
    path = str(tmp_path / "m.db")
    conn = open_db(path)
    conn.execute(
        """INSERT INTO daily_picks (date, rank, kind, code, name, strategy,
             buy, stop, target, score)
           VALUES ('2026-09-07', 1, 'test', '600519', 'M', 'test', 100, 80, 140, 2.0)"""
    )
    conn.execute(
        """INSERT INTO daily_prices (code, trade_date, open, close, high, low,
                                    volume, amount) VALUES ('600519','2026-09-07',100,100,100,100,0,0)"""
    )
    conn.commit()
    conn.close()

    from cli_layer import build_parser, run_cli
    parser = build_parser()
    args = parser.parse_args([
        "plan", "build", "--db", path,
        "--date", "2026-09-07", "--portfolio", "10W",
        "--capital", "100000",
    ])
    run_cli(args, stocks=[], scores={})

    conn = open_db(path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT portfolio FROM trade_plan"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [("10W",)]


def test_plan_build_all_runs_full_tiers(tmp_path, capsys):
    import sqlite3
    from db_repository import open_db
    path = str(tmp_path / "m.db")
    conn = open_db(path)
    conn.execute(
        """INSERT INTO daily_picks (date, rank, kind, code, name, strategy,
             buy, stop, target, score)
           VALUES ('2026-09-07', 1, 'test', '600519', 'M', 'test', 100, 80, 140, 2.0)"""
    )
    conn.execute(
        """INSERT INTO daily_prices (code, trade_date, open, close, high, low,
                                    volume, amount) VALUES ('600519','2026-09-07',100,100,100,100,0,0)"""
    )
    conn.commit()
    conn.close()

    from cli_layer import build_parser, run_cli
    parser = build_parser()
    args = parser.parse_args([
        "plan", "build-all", "--db", path,
        "--date", "2026-09-07", "--tiers", "50000,100000",
    ])
    run_cli(args, stocks=[], scores={})

    conn = open_db(path)
    try:
        rows = conn.execute(
            "SELECT DISTINCT portfolio FROM trade_plan ORDER BY portfolio"
        ).fetchall()
    finally:
        conn.close()
    assert rows == [("10W",), ("5W",)]
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `.venv/bin/python -m pytest tests/test_cli_plan_portfolio.py -v`
Expected: parser 测试部分失败（`--portfolio` 没定义）；end-to-end 测试失败

- [ ] **Step 3: 改 `cli_layer.py`**

把现有的 `plan_parser` 改为 "sub-sub-parser" 模式：`plan` 下分 `build` 和 `build-all` 两个 action。

```python
# cli_layer.py

# 替换 line 231-249 的 plan_parser：
    plan_parser = subparsers.add_parser("plan", help="生成 / 查看每日交易计划（paper）")
    plan_sub = plan_parser.add_subparsers(dest="action")

    # plan build
    pb = plan_sub.add_parser("build", help="生成单 portfolio 的 plan")
    pb.add_argument("--date", type=str, default=None, help="YYYY-MM-DD（默认今日）")
    pb.add_argument("--db", type=str, default="hs300.db")
    pb.add_argument("--rr-target", type=float, default=None)
    pb.add_argument("--max-single", type=float, default=None)
    pb.add_argument("--slippage", type=float, default=None)
    pb.add_argument("--regime", type=str, default=None,
                    choices=["bull", "bear", "sideways"])
    pb.add_argument("--capital", type=int, default=None, choices=CAPITAL_TIERS, metavar="资金")
    pb.add_argument("--portfolio", type=str, default="default",
                    help="portfolio label（如 '10W'），默认 'default'")
    pb.add_argument("--dry-run", action="store_true")
    pb.add_argument("--backfill", action="store_true",
                    help="补齐所有历史 daily_picks 日期的 plan")
    pb.add_argument("--list", dest="list_mode", action="store_true")
    pb.add_argument("--days", type=int, default=30)
    pb.add_argument("--show", dest="show_mode", action="store_true")

    # plan build-all
    pba = plan_sub.add_parser("build-all", help="为所有资金档位批量生成 plan")
    pba.add_argument("--date", type=str, default=None, help="YYYY-MM-DD（默认今日）")
    pba.add_argument("--db", type=str, default="hs300.db")
    pba.add_argument("--strategy", type=str, default="linyuan",
                     help="策略名（仅日志）")
    pba.add_argument("--tiers", type=str, default=None,
                     help="逗号分隔资金列表（如 '50000,100000'），默认走 CAPITAL_TIERS")
    pba.add_argument("--since", type=str, default=None,
                     help="回算起点 YYYY-MM-DD（仅 --backfill）")
    pba.add_argument("--backfill", action="store_true",
                     help="回填所有历史 daily_picks 日期")
    pba.add_argument("--dry-run", action="store_true")
    pba.add_argument("--regime", type=str, default="sideways",
                     choices=["bull", "bear", "sideways"])
    pba.add_argument("--rr-target", type=float, default=None)
    pba.add_argument("--max-single", type=float, default=None)
    pba.add_argument("--slippage", type=float, default=None)
```

修改 `run_cli` 中 `args.command == "plan"` 分支：

```python
    elif args.command == "plan":
        if args.action == "build-all":
            _run_plan_build_all(args)
        else:
            _run_plan_build(args)
```

替换 `_run_plan` 为 `_run_plan_build`（保留 --list/--show 逻辑），并新增 `_run_plan_build_all`：

```python
def _run_plan_build(args) -> None:
    """`plan build` 处理：build / list / show / backfill。"""
    from datetime import date as _date, timedelta
    from config import (
        MAX_SINGLE as DEFAULT_MAX_SINGLE,
        MAX_TOTAL as DEFAULT_MAX_TOTAL,
        RR_TARGET as DEFAULT_RR_TARGET,
        SLIPPAGE as DEFAULT_SLIPPAGE,
        DEFAULT_CAPITAL,
    )
    from db_repository import open_db, get_trade_plan_by_date
    from plan_builder import build_plan

    today = _date.today().isoformat()
    plan_date = args.date or today
    portfolio = getattr(args, "portfolio", "default")

    # --- list mode ---
    if args.list_mode:
        conn = open_db(args.db)
        try:
            cur = conn.execute(
                "SELECT DISTINCT plan_date FROM trade_plan "
                "WHERE plan_date >= ? AND plan_date <= ? "
                "AND portfolio=? ORDER BY plan_date DESC",
                (plan_date, plan_date, portfolio),
            ) if False else conn.execute(
                "SELECT DISTINCT plan_date FROM trade_plan "
                "WHERE portfolio=? ORDER BY plan_date DESC LIMIT 30",
                (portfolio,),
            )
            dates = [r[0] for r in cur.fetchall()]
        finally:
            conn.close()
        if not dates:
            print(f"无 plan（portfolio={portfolio}）")
            return
        print(f"portfolio={portfolio} 已生成 plan 共 {len(dates)} 天：")
        for d in dates:
            print(f"  {d}")
        return

    # --- show mode ---
    if args.show_mode:
        conn = open_db(args.db)
        try:
            cur = conn.execute(
                "SELECT tp.*, m.name AS name FROM trade_plan tp "
                "LEFT JOIN hs300_metadata m ON m.code = tp.code "
                "WHERE tp.plan_date=? AND tp.portfolio=? "
                "ORDER BY tp.action DESC, tp.code",
                (plan_date, portfolio),
            )
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        finally:
            conn.close()
        if not rows:
            print(f"无 plan：{plan_date} portfolio={portfolio}")
            return
        print(f"plan_date={plan_date} portfolio={portfolio} rows={len(rows)}")
        for r in rows:
            print(f"  {r['action']:4s} {r['code']} px={r['plan_price']:.2f} "
                  f"status={r['status']} reason={r['reason']}")
        return

    # --- build mode ---
    params = {
        "max_single": args.max_single if args.max_single is not None else DEFAULT_MAX_SINGLE,
        "max_total": DEFAULT_MAX_TOTAL,
        "rr_target": args.rr_target if args.rr_target is not None else DEFAULT_RR_TARGET,
        "regime": args.regime or "sideways",
        "capital": args.capital if args.capital is not None else DEFAULT_CAPITAL,
    }
    slippage = args.slippage if args.slippage is not None else DEFAULT_SLIPPAGE

    if args.backfill:
        conn = open_db(args.db)
        try:
            dates = [r[0] for r in conn.execute(
                "SELECT DISTINCT date FROM daily_picks ORDER BY date"
            ).fetchall()]
        finally:
            conn.close()
        for d in dates:
            result = build_plan(d, args.db, params, slippage=slippage,
                                paper_trade=False, include_carryover=False,
                                portfolio=portfolio)
            print(f"backfilled {d} portfolio={portfolio}: "
                  f"picks={result.num_picks} rows={len(result.rows)}")
        return

    if args.dry_run:
        print(f"[dry-run] would build plan for {plan_date} "
              f"portfolio={portfolio} params={params}")
        return

    result = build_plan(plan_date, args.db, params, slippage=slippage,
                        portfolio=portfolio)
    print(f"plan_date={plan_date} portfolio={portfolio} "
          f"picks={result.num_picks} open={result.num_open_positions} "
          f"sanity={result.sanity_passed}")
    for r in result.rows:
        print(f"  {r.action:4s} {r.code} px={r.plan_price:.2f} "
              f"size={r.size_pct} status={r.status} reason={r.reason}")


def _run_plan_build_all(args) -> None:
    """`plan build-all` 处理：批量跑全档 plan。"""
    from datetime import date as _date
    from config import CAPITAL_TIERS, DEFAULT_CAPITAL
    from db_repository import open_db
    from plan_builder import build_all_portfolios, build_plan

    today = _date.today().isoformat()
    plan_date = args.date or today

    tiers = CAPITAL_TIERS
    if args.tiers:
        tiers = [int(x.strip()) for x in args.tiers.split(",")]

    def _progress(pct: int, msg: str) -> None:
        print(f"[{pct:3d}%] {msg}")

    if args.backfill:
        conn = open_db(args.db)
        try:
            dates = [r[0] for r in conn.execute(
                "SELECT DISTINCT date FROM daily_picks ORDER BY date"
            ).fetchall()]
        finally:
            conn.close()
        if args.since:
            dates = [d for d in dates if d >= args.since]
        for d in dates:
            results = build_all_portfolios(
                args.db, d,
                params={"regime": args.regime, "max_single": args.max_single,
                        "max_total": None, "rr_target": args.rr_target},
                strategy=args.strategy, tiers=tiers, progress=_progress,
            )
            ok = sum(1 for r in results if r is not None)
            print(f"backfilled {d}: ok={ok}/{len(results)}")
        return

    if args.dry_run:
        print(f"[dry-run] would build plans for {plan_date} tiers={tiers}")
        return

    results = build_all_portfolios(
        args.db, plan_date,
        params={"regime": args.regime, "max_single": args.max_single,
                "max_total": None, "rr_target": args.rr_target},
        strategy=args.strategy, tiers=tiers, progress=_progress,
    )
    ok = sum(1 for r in results if r is not None)
    print(f"\nDONE plan_date={plan_date} tiers={len(tiers)} ok={ok}/{len(results)}")
```

- [ ] **Step 4: 重跑测试**

Run: `.venv/bin/python -m pytest tests/test_cli_plan_portfolio.py -v`
Expected: 7 passed

- [ ] **Step 5: 跑全量回归**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: ≥ 257 passed

- [ ] **Step 6: Commit**

```bash
git add cli_layer.py tests/test_cli_plan_portfolio.py
git commit -m "feat(cli): plan build --portfolio + plan build-all — per-tier plan build"
```

---

## Task 6: Flask — `/portfolio` 页 + `/api/portfolio/*`

**Files:**
- Modify: `app.py`（在 `create_app` 内追加路由 + 在文件顶部追加 `PORTFOLIO_BODY` / `PORTFOLIO_SCRIPT`）
- Create: `tests/test_api_portfolio.py`

**Interfaces:**
- Consumes: Task 2 的 `compute_portfolio_pnl`、`get_open_positions_with_unrealized(portfolio=)`、`get_holdings_detail`；Task 3 的 `get_trade_plan_by_date_and_portfolio`
- Produces:
  - `GET /portfolio` 返回页面（HTML）
  - `GET /api/portfolio/summary?date=YYYY-MM-DD` → `{date, tiers: [{label, capital, ...pnl fields, last_refreshed}]}`
  - `GET /api/portfolio/<label>?date=YYYY-MM-DD` → 单 tier 详情 `{label, capital, pnl, holdings, plan_rows}`
  - 默认查询日期 = 最近有 plan 的日期（按 portfolio）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_api_portfolio.py
import os
import tempfile
import pytest
from db_repository import open_db, insert_open_position, insert_trade_event, close_open_position


@pytest.fixture
def fresh_db_client(tmp_path, monkeypatch):
    path = str(tmp_path / "m.db")
    conn = open_db(path)
    conn.execute(
        """INSERT INTO daily_picks (date, rank, kind, code, name, strategy,
             buy, stop, target, score)
           VALUES ('2026-09-07', 1, 'test', '600519', 'M', 'test', 100, 80, 140, 2.0)"""
    )
    conn.execute(
        """INSERT INTO daily_prices (code, trade_date, open, close, high, low,
                                    volume, amount) VALUES ('600519','2026-09-07',100,110,110,100,0,0)"""
    )
    conn.execute(
        """INSERT INTO hs300_metadata (code, name, industry, region)
           VALUES ('600519', 'M', '酒', '贵州')"""
    )
    conn.commit()
    conn.close()

    from app import create_app
    app = create_app(db_path=path)
    app.config["TESTING"] = True
    client = app.test_client()
    return client, path


def test_portfolio_page_renders(fresh_db_client):
    client, _ = fresh_db_client
    resp = client.get("/portfolio")
    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "持仓组合" in body


def test_api_portfolio_summary_returns_all_tiers(fresh_db_client):
    client, _ = fresh_db_client
    resp = client.get("/api/portfolio/summary?date=2026-09-07")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["date"] == "2026-09-07"
    assert "tiers" in data
    tier_labels = {t["label"] for t in data["tiers"]}
    assert "5W" in tier_labels and "10W" in tier_labels and "50W" in tier_labels


def test_api_portfolio_summary_empty_data(fresh_db_client):
    """还没有任何 plan 写过 → 仍返回全 10 档（capital=100000 默认，pnl=0）。"""
    client, _ = fresh_db_client
    resp = client.get("/api/portfolio/summary")
    assert resp.status_code == 200
    data = resp.get_json()
    assert len(data["tiers"]) == 10


def test_api_portfolio_detail_returns_label_data(fresh_db_client):
    client, _ = fresh_db_client
    resp = client.get("/api/portfolio/10W?date=2026-09-07")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["label"] == "10W"
    assert data["capital"] == 100000
    assert "pnl" in data
    assert "holdings" in data
    assert "plan_rows" in data


def test_api_portfolio_detail_unknown_label_404(fresh_db_client):
    client, _ = fresh_db_client
    resp = client.get("/api/portfolio/999W")
    assert resp.status_code == 404
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `.venv/bin/python -m pytest tests/test_api_portfolio.py -v`
Expected: 全部 404 / 500

- [ ] **Step 3: 在 `app.py` 末尾（`create_app` 内）追加路由**

```python
    # ---- Portfolio tiers ----
    from config import CAPITAL_TIERS
    from db_repository import compute_portfolio_pnl, get_open_positions_with_unrealized

    @app.get("/portfolio")
    def portfolio_page():
        from datetime import date as _date
        today = _date.today().isoformat()
        return _page("持仓组合", "portfolio",
                     PORTFOLIO_BODY.replace("{{today}}", today),
                     PORTFOLIO_SCRIPT)

    def _portfolio_label_to_capital(label: str) -> Optional[int]:
        """'10W' → 100000；None if label doesn't match a known tier."""
        for c in CAPITAL_TIERS:
            if f"{c // 10000}W" == label:
                return c
        return None

    @app.get("/api/portfolio/summary")
    def api_portfolio_summary():
        date = request.args.get("date", "")
        conn = open_conn(db_path)
        try:
            tiers = []
            for capital in CAPITAL_TIERS:
                label = f"{capital // 10000}W"
                pnl = compute_portfolio_pnl(conn, label, initial_capital=capital)
                # 最近一次 plan 日期
                last = conn.execute(
                    "SELECT MAX(plan_date) FROM trade_plan WHERE portfolio=?",
                    (label,),
                ).fetchone()[0]
                tiers.append({
                    "label": label,
                    "capital": capital,
                    "cash_remaining": pnl["cash_remaining"],
                    "position_value": pnl["position_value"],
                    "total_value": pnl["total_value"],
                    "realized_pnl": pnl["realized_pnl"],
                    "unrealized_pnl": pnl["unrealized_pnl"],
                    "return_rate": pnl["return_rate"],
                    "last_plan_date": last,
                })
        finally:
            conn.close()
        return jsonify({"date": date, "tiers": tiers})

    @app.get("/api/portfolio/<label>")
    def api_portfolio_detail(label):
        capital = _portfolio_label_to_capital(label)
        if capital is None:
            return jsonify({"error": f"unknown portfolio: {label}"}), 404
        date = request.args.get("date", "")
        conn = open_conn(db_path)
        try:
            pnl = compute_portfolio_pnl(conn, label, initial_capital=capital)
            holdings = get_open_positions_with_unrealized(conn, portfolio=label)
            # 当日 plan 行（按 portfolio 过滤）
            plan_rows = []
            if date:
                cur = conn.execute(
                    "SELECT tp.*, m.name AS name FROM trade_plan tp "
                    "LEFT JOIN hs300_metadata m ON m.code = tp.code "
                    "WHERE tp.plan_date=? AND tp.portfolio=? "
                    "ORDER BY tp.action DESC, tp.code",
                    (date, label),
                )
                cols = [d[0] for d in cur.description]
                plan_rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            last = conn.execute(
                "SELECT MAX(plan_date) FROM trade_plan WHERE portfolio=?",
                (label,),
            ).fetchone()[0]
        finally:
            conn.close()
        return jsonify({
            "label": label,
            "capital": capital,
            "date": date,
            "pnl": pnl,
            "holdings": holdings,
            "plan_rows": plan_rows,
            "last_plan_date": last,
        })
```

- [ ] **Step 4: 在 `app.py` 顶部追加 `PORTFOLIO_BODY` / `PORTFOLIO_SCRIPT`**

```python
PORTFOLIO_BODY = """<main class="container py-4">
  <h2 class="mb-3">持仓组合 <small class="text-muted fs-6">[paper]</small></h2>

  <div class="row g-2 align-items-end mb-3">
    <div class="col-auto">
      <label class="form-label mb-0">日期</label>
      <input type="date" id="pf-date" class="form-control form-control-sm" value="{{today}}">
    </div>
    <div class="col-auto">
      <label class="form-label mb-0">策略</label>
      <select id="pf-strategy" class="form-select form-select-sm">
        <option value="linyuan" selected>林园</option>
        <option value="ma-picks">均线</option>
        <option value="buy-signals">买入信号</option>
      </select>
    </div>
    <div class="col-auto ms-auto small text-muted">
      <span class="me-2">基准：所有 tier 共用同一组 daily_picks；按 capital 等权分配</span>
    </div>
  </div>

  <h5 class="mt-4">资金档位</h5>
  <div class="d-flex flex-row overflow-auto pb-2 gap-2" id="pf-tier-cards"></div>

  <h5 class="mt-4">累计收益对比</h5>
  <div style="position:relative; height:280px;">
    <canvas id="pf-returns-chart"></canvas>
  </div>

  <div id="pf-detail" class="mt-4"></div>
</main>"""


PORTFOLIO_SCRIPT = """
window.PORTFOLIO_PAGE = true;
function tierCard(t) {
  const cls = t.return_rate >= 0 ? 'border-success' : 'border-danger';
  const sign = t.return_rate >= 0 ? '+' : '';
  return `
    <div class="card ${cls} pf-tier-card" data-label="${t.label}" style="min-width:120px; cursor:pointer;">
      <div class="card-body p-2 text-center">
        <div class="fw-bold fs-5">${t.label}</div>
        <div class="small text-muted">${(t.capital/10000).toFixed(0)}万</div>
        <div class="fs-6 mt-1 ${t.return_rate>=0?'text-success':'text-danger'}">
          ${sign}${(t.return_rate*100).toFixed(2)}%
        </div>
        <div class="small text-muted">
          ¥${t.total_value.toFixed(0)}
        </div>
      </div>
    </div>`;
}

async function loadPortfolio() {
  const date = document.getElementById('pf-date').value;
  const resp = await fetch(`/api/portfolio/summary?date=${date}`);
  const data = await resp.json();
  const cards = document.getElementById('pf-tier-cards');
  cards.innerHTML = data.tiers.map(tierCard).join('');
  cards.querySelectorAll('.pf-tier-card').forEach(el => {
    el.addEventListener('click', () => selectTier(el.dataset.label, date));
  });
  drawReturnsChart(data.tiers);
  if (data.tiers.length) selectTier('10W', date);
}

function drawReturnsChart(tiers) {
  const ctx = document.getElementById('pf-returns-chart').getContext('2d');
  if (window._pfChart) window._pfChart.destroy();
  window._pfChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: tiers.map(t => t.label),
      datasets: [{
        label: '累计收益率',
        data: tiers.map(t => +(t.return_rate * 100).toFixed(2)),
        backgroundColor: tiers.map(t => t.return_rate >= 0 ? '#22c55e' : '#ef4444'),
      }],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      scales: {
        y: { ticks: { callback: v => v + '%' } },
      },
    },
  });
}

async function selectTier(label, date) {
  cards = document.querySelectorAll('.pf-tier-card');
  cards.forEach(c => c.classList.remove('border-primary', 'shadow'));
  const active = document.querySelector(`.pf-tier-card[data-label="${label}"]`);
  if (active) active.classList.add('border-primary', 'shadow');
  const resp = await fetch(`/api/portfolio/${encodeURIComponent(label)}?date=${date}`);
  const data = await resp.json();
  if (!resp.ok) return;
  renderDetail(data);
}

function renderDetail(d) {
  const detail = document.getElementById('pf-detail');
  const pnl = d.pnl;
  const used = pnl.initial_capital - pnl.cash_remaining;
  const util = (used / pnl.initial_capital * 100).toFixed(1);
  const html = `
    <h5>${d.label} <small class="text-muted">资金 ${pnl.initial_capital.toLocaleString()} 元</small></h5>
    <div class="row g-2 mb-3">
      <div class="col-auto"><span class="badge bg-secondary">已用 ¥${used.toLocaleString()}</span></div>
      <div class="col-auto"><span class="badge bg-info">现金 ¥${pnl.cash_remaining.toLocaleString()}</span></div>
      <div class="col-auto"><span class="badge bg-warning text-dark">利用率 ${util}%</span></div>
      <div class="col-auto"><span class="badge ${pnl.unrealized_pnl>=0?'bg-success':'bg-danger'}">
        未实现 ¥${pnl.unrealized_pnl.toFixed(0)}
      </span></div>
      <div class="col-auto"><span class="badge bg-dark">已实现 ¥${pnl.realized_pnl.toFixed(0)}</span></div>
    </div>
    <h6>当日计划</h6>
    <table class="table table-sm">
      <thead><tr><th>动作</th><th>代码</th><th>价格</th><th>仓位%</th><th>止损</th><th>止盈</th><th>状态</th></tr></thead>
      <tbody>
        ${d.plan_rows.map(r => `<tr>
          <td>${r.action}</td><td>${r.code}</td>
          <td>${r.plan_price.toFixed(2)}</td>
          <td>${(r.size_pct*100).toFixed(1)}%</td>
          <td>${r.stop_price.toFixed(2)}</td>
          <td>${r.tp_price.toFixed(2)}</td>
          <td>${r.status}</td>
        </tr>`).join('')}
      </tbody>
    </table>
    <h6>当前持仓</h6>
    <table class="table table-sm">
      <thead><tr><th>代码</th><th>成本</th><th>现价</th><th>股数</th><th>浮盈</th></tr></thead>
      <tbody>
        ${d.holdings.items.map(it => `<tr>
          <td>${it.code}</td>
          <td>${it.entry_price.toFixed(2)}</td>
          <td>${(it.current_price||0).toFixed(2)}</td>
          <td>${it.shares}</td>
          <td class="${(it.floating_pnl||0)>=0?'text-success':'text-danger'}">
            ¥${(it.floating_pnl||0).toFixed(0)}
          </td>
        </tr>`).join('')}
      </tbody>
    </table>`;
  detail.innerHTML = html;
}

document.getElementById('pf-date').addEventListener('change', loadPortfolio);
window.addEventListener('DOMContentLoaded', loadPortfolio);
"""
```

同时在 `_nav("portfolio")` 顶部导航 "每日机会" 与 "交易计划" 之间插入持仓组合链接：

```python
def _nav(active: str) -> str:
    items = [
        ("picks", "/", "每日机会"),
        ("portfolio", "/portfolio", "持仓组合"),
        ("plan", "/plan", "交易计划"),
    ]
    ...
```

- [ ] **Step 5: 重跑测试**

Run: `.venv/bin/python -m pytest tests/test_api_portfolio.py -v`
Expected: 5 passed

- [ ] **Step 6: 跑全量回归**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: ≥ 262 passed

- [ ] **Step 7: Commit**

```bash
git add app.py tests/test_api_portfolio.py
git commit -m "feat(web): /portfolio page + /api/portfolio/{summary,<label>}"
```

---

## Task 7: 静态导出 — `export_portfolio`

**Files:**
- Modify: `export_json.py`（新增 `export_portfolio()` + 在 `export()` 末尾调用）
- Create: `tests/test_export_portfolio.py`

**Interfaces:**
- Consumes: Task 2 的 `compute_portfolio_pnl`、`get_open_positions_with_unrealized(portfolio=)`；Task 6 的 summary / detail 数据 shape
- Produces:
  - `site/data/portfolio/summary.json` — 10 档汇总
  - `site/data/portfolio/<label>.json` — 每档详情
  - `site/portfolio.html` — 静态版 portfolio 页（沿用 `PORTFOLIO_BODY` / `PORTFOLIO_SCRIPT`）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_export_portfolio.py
import json
from pathlib import Path
import pytest
from db_repository import open_db


@pytest.fixture
def exported_site(tmp_path):
    db_path = str(tmp_path / "m.db")
    conn = open_db(db_path)
    conn.execute(
        """INSERT INTO daily_picks (date, rank, kind, code, name, strategy,
             buy, stop, target, score)
           VALUES ('2026-09-07', 1, 'test', '600519', 'M', 'test', 100, 80, 140, 2.0)"""
    )
    conn.execute(
        """INSERT INTO daily_prices (code, trade_date, open, close, high, low,
                                    volume, amount) VALUES ('600519','2026-09-07',100,110,110,100,0,0)"""
    )
    conn.execute(
        """INSERT INTO hs300_metadata (code, name, industry, region)
           VALUES ('600519', 'M', '酒', '贵州')"""
    )
    conn.commit()
    conn.close()

    site_dir = tmp_path / "site"
    from export_json import export
    export(db_path, str(site_dir))
    return db_path, site_dir


def test_summary_json_written(exported_site):
    _, site_dir = exported_site
    summary = json.loads((site_dir / "data" / "portfolio" / "summary.json").read_text())
    assert "tiers" in summary
    assert len(summary["tiers"]) == 10
    labels = {t["label"] for t in summary["tiers"]}
    assert "5W" in labels and "10W" in labels


def test_per_tier_json_written(exported_site):
    _, site_dir = exported_site
    pf_dir = site_dir / "data" / "portfolio"
    for label in ("5W", "10W", "50W"):
        assert (pf_dir / f"{label}.json").exists()


def test_per_tier_json_shape(exported_site):
    _, site_dir = exported_site
    data = json.loads((site_dir / "data" / "portfolio" / "10W.json").read_text())
    assert data["label"] == "10W"
    assert data["capital"] == 100000
    assert "pnl" in data


def test_portfolio_html_static_written(exported_site):
    _, site_dir = exported_site
    html = (site_dir / "portfolio.html").read_text()
    assert "持仓组合" in html
    assert "static/dashboard.js" not in html  # 用的是独立 script
```

- [ ] **Step 2: 跑测试，确认失败**

Run: `.venv/bin/python -m pytest tests/test_export_portfolio.py -v`
Expected: 全部失败

- [ ] **Step 3: 改 `export_json.py`**

```python
# export_json.py

from config import CAPITAL_TIERS
from db_repository import (
    compute_portfolio_pnl,
    get_open_positions_with_unrealized,
)
from app import PORTFOLIO_BODY, PORTFOLIO_SCRIPT


def export_portfolio(db_path: str, out_dir: Path) -> None:
    """写出 portfolio 静态数据 + portfolio.html。"""
    pf_dir = out_dir / "data" / "portfolio"
    pf_dir.mkdir(parents=True, exist_ok=True)

    conn = open_db(db_path)
    try:
        # 找最新 plan_date 作为默认查询日期
        latest = conn.execute(
            "SELECT MAX(plan_date) FROM trade_plan"
        ).fetchone()[0] or ""

        tiers = []
        for capital in CAPITAL_TIERS:
            label = f"{capital // 10000}W"
            pnl = compute_portfolio_pnl(conn, label, initial_capital=capital)
            holdings = get_open_positions_with_unrealized(conn, portfolio=label)
            cur = conn.execute(
                "SELECT tp.*, m.name AS name FROM trade_plan tp "
                "LEFT JOIN hs300_metadata m ON m.code = tp.code "
                "WHERE tp.plan_date=? AND tp.portfolio=? "
                "ORDER BY tp.action DESC, tp.code",
                (latest, label),
            )
            cols = [d[0] for d in cur.description]
            plan_rows = [dict(zip(cols, r)) for r in cur.fetchall()]
            tiers.append({
                "label": label,
                "capital": capital,
                "cash_remaining": pnl["cash_remaining"],
                "position_value": pnl["position_value"],
                "total_value": pnl["total_value"],
                "realized_pnl": pnl["realized_pnl"],
                "unrealized_pnl": pnl["unrealized_pnl"],
                "return_rate": pnl["return_rate"],
                "last_plan_date": latest,
            })
            detail = {
                "label": label,
                "capital": capital,
                "date": latest,
                "pnl": pnl,
                "holdings": holdings,
                "plan_rows": plan_rows,
                "last_plan_date": latest,
            }
            json.dump(
                detail,
                (pf_dir / f"{label}.json").open("w"),
                ensure_ascii=False, default=str,
            )

        json.dump(
            {"date": latest, "tiers": tiers},
            (pf_dir / "summary.json").open("w"),
            ensure_ascii=False, default=str,
        )
    finally:
        conn.close()

    # 静态页
    (out_dir / "portfolio.html").write_text(
        _page("持仓组合", "portfolio",
              PORTFOLIO_BODY.replace("{{today}}", latest),
              PORTFOLIO_SCRIPT,
              config=STATIC_CONFIG, assets="static/"),
        encoding="utf-8",
    )


# 在 export() 函数末尾，`return 0` 之前追加：
    export_portfolio(db_path, out)
```

- [ ] **Step 4: 重跑测试**

Run: `.venv/bin/python -m pytest tests/test_export_portfolio.py -v`
Expected: 4 passed

- [ ] **Step 5: 跑全量回归**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: ≥ 266 passed

- [ ] **Step 6: Commit**

```bash
git add export_json.py tests/test_export_portfolio.py
git commit -m "feat(export): portfolio summary.json + per-tier json + portfolio.html"
```

---

## Task 8: Workflow — 加 `plan build-all` 步

**Files:**
- Modify: `.github/workflows/daily-sync-export.yml`

**Interfaces:**
- Consumes: Task 5 的 `plan build-all` CLI
- Produces: workflow 在每日 plan step 后追加 tier-portfolio build-all

- [ ] **Step 1: 改 workflow**

在 `Run daily pipeline (sync + picks + plan)` step 之后，`Export static site` step 之前，插入：

```yaml
      - name: Build all tier portfolios
        run: |
          python stock_cli.py plan build-all --db hs300.db \
            --strategy linyuan --tiers 50000,100000,150000,200000,250000,300000,350000,400000,450000,500000
```

（明确列 10 档，避免 CI 端 `CAPITAL_TIERS` 默认变更时偏离预期。）

- [ ] **Step 2: 验证 YAML 语法**

Run: `python -c "import yaml; yaml.safe_load(open('.github/workflows/daily-sync-export.yml'))"`
Expected: 无输出（成功）

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/daily-sync-export.yml
git commit -m "ci(workflow): build all tier portfolios after daily plan"
```

---

## Task 9: README 更新

**Files:**
- Modify: `README.md`

**Interfaces:**
- Consumes: Task 5 的 CLI；Task 6 的页面
- Produces: README 增加 `plan build-all` 命令 + 持仓组合章节

- [ ] **Step 1: 在「常用命令」节加 `plan build-all`**

在 README 的每日命令清单里加：

```markdown
# 多档位组合 — 全 tier plan
uv run a-finder plan build-all --db hs300.db --strategy linyuan
uv run a-finder plan build-all --db hs300.db --tiers 50000,200000
uv run a-finder plan build-all --db hs300.db --backfill --since 2025-01-01
```

- [ ] **Step 2: 新增章节**

```markdown
## 持仓组合 / Portfolio Tiers

按资金档位（5W-50W 共 10 档）独立跟踪 paper-trade 组合：

- 每个 tier 独立 plan / open_positions / trade_events（schema 隔离）
- 共享同一组 `daily_picks`，按 capital 等权分配
- 累计收益 + 持仓跟踪 + drill-down 详情：`http://127.0.0.1:8000/portfolio`

### 一次性生成全档 plan

```bash
uv run a-finder plan build-all --db hs300.db --strategy linyuan
```

输出格式：

```
[  0%] plan build-all starting: tiers=10
[ 10%] 5W done picks=8
[ 20%] 10W done picks=8
...
[100%] 50W done picks=8
DONE plan_date=2026-09-07 tiers=10 ok=10/10
```

### 数据流

1. workflow `daily-sync-export.yml` 每日 15:30 (北京时间) 同步 → picks → plan (default) → **plan build-all (10 tiers)** → export
2. Flask `/api/portfolio/summary` 提供给 web 页面
3. 静态导出到 `site/data/portfolio/{summary.json, <label>.json}` + `site/portfolio.html`
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: README portfolio tiers section + plan build-all commands"
```

---

## Self-Review

**1. Spec coverage:**

| Spec section | Plan task |
|---|---|
| 三. Schema 迁移 | Task 1 |
| 四. plan_builder 改造 (build_plan portfolio=) | Task 3 |
| 四. plan_builder 改造 (build_all_portfolios) | Task 4 |
| 五. CLI 暴露 (--portfolio) | Task 5 |
| 五. CLI 暴露 (plan build-all) | Task 5 |
| 六. repo helpers (compute_portfolio_pnl) | Task 2 |
| 六. repo helpers (upsert/get extension) | Task 2 |
| 七. UI 持仓组合页 (top nav + page) | Task 6 |
| 七. UI 持仓组合页 (summary/detail API) | Task 6 |
| 七. UI 持仓组合页 (static export) | Task 7 |
| 八. Workflow 改动 | Task 8 |
| 九. 测试覆盖 (10 文件) | Tasks 1-7 |
| 十. README 更新 | Task 9 |

**2. Placeholder scan:** 没有"TBD"、"待实现"、"similar to task N"等。

**3. Type consistency:**
- `portfolio` 字段在 Task 1 引入；Task 2/3 透传；Task 4 通过 `build_plan` 复用；Task 5 CLI 接受；Task 6 API 返回；Task 7 导出
- `compute_portfolio_pnl` 返回字段 `{portfolio, initial_capital, cash_remaining, position_value, total_value, realized_pnl, unrealized_pnl, return_rate}` 在 Task 2/4/6/7 一致
- `build_all_portfolios` 在 Task 4 定义，Task 5 CLI 调用
- `_picks` kwarg 在 Task 3 引入，Task 4 使用

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-07-portfolio-tiers.md`. Two execution options:

1. **Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** — execute in this session with checkpoints

Which approach?