# 可转债策略 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增可转换公司债券策略模块，输出多因子打分的每日转债榜单，并提供持仓退出预警。

**Architecture:** 完全独立的转债体系，零改动现有股票代码。新增 `cb_data_provider.py`（多源降级数据层）、`strategies/convertible.py`（粗筛→补全→打分→排序）、`cb_repository.py`（SQLite 读写）、4 张 `cb_` 前缀表、4 个 CLI 子命令。数据流：全市场快照 → 本地粗筛 → 逐只补全条款 → 因子打分 → 榜单落库 → 预警扫描。

**Tech Stack:** Python 3.10+ / akshare（已装 1.18.91）/ pandas / SQLite / argparse / pytest

**Spec:** `docs/superpowers/specs/2026-10-24-convertible-bond-strategy-design.md`

---

## 关键约束（实现时必须遵守）

这些是实测结论，不是假设：

1. **`ak.bond_zh_cov_info(symbol=...)` 用裸 6 位代码**，不能加 `sh`/`sz` 前缀。加了会 `TypeError: 'NoneType' object is not subscriptable`。
   （对比：`ak.bond_zh_hs_cov_daily` 反而**要**前缀。两者相反，勿混用。）
2. **已强赎退市的债**，`bond_zh_cov_info` 的 `REDEEM_TRIG_PRICE` / `TRANSFER_PRICE` / `TRANSFER_VALUE` / `CURRENT_BOND_PRICE` 全为 `None`。
3. **未上市新券**的 `CURRENT_BOND_PRICE` 返回字符串 `'-'`，转 float 会炸。
4. **`ak.bond_zh_cov()` 的 19 个字段不含到期日与到期收益** — `year_left` 与 `ytm` 都要从 `bond_zh_cov_info` 取/自算。
5. **`ak.bond_cov_comparison()`（东财比价表）本机已被封 IP** — 只能做备源，失败即静默降级，绝不阻塞流程。
6. **`ak.bond_cb_jsl()` 只返回 30 只**（双低最低），不能当宇宙源。
7. **`ak.bond_cb_adj_logs_jsl()` 只返回 3 条**（仅洪涛转债），`adj_count` 第一期取中性 50。

## 文件结构

| 文件 | 职责 |
|---|---|
| `config.py`（修改） | 新增 `CB_SCORING_WEIGHTS` / `CB_FILTERS` / `CB_RISK` 三个区块，**只增不改** |
| `db/migrations/2026_10_24_convertible_bond.sql`（新建） | 4 张 `cb_` 表 + 2 个索引 |
| `cb_repository.py`（新建） | `cb_` 表的全部读写：upsert / 查询 / 持仓管理 |
| `cb_data_provider.py`（新建） | 多源降级取数：全市场快照 / 逐只补全 / 强赎状态 |
| `strategies/convertible.py`（新建） | 粗筛 / 因子打分 / 硬剔除 / 仓位计算 / 预警判定 |
| `cli_layer.py`（修改） | 新增 4 个子命令 + 4 个 handler |
| `tests/test_convertible.py`（新建） | 全部单元测试（mock，不打网络） |
| `tests/test_migration_convertible.py`（新建） | 迁移测试 |
| `tests/test_cb_repository.py`（新建） | 仓储层测试 |
| `tests/test_cli_convertible.py`（新建） | CLI 测试 |

---

## Task 1: 配置常量 + 数据库迁移

**Files:**
- Modify: `config.py`
- Create: `db/migrations/2026_10_24_convertible_bond.sql`
- Test: `tests/test_migration_convertible.py`

- [ ] **Step 1: 写失败的迁移测试**

创建 `tests/test_migration_convertible.py`：

```python
from db_repository import open_db


def _cols(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def test_cb_universe_table_exists(tmp_path):
    conn = open_db(str(tmp_path / "m.db"))
    cols = _cols(conn, "cb_universe")
    conn.close()
    for c in ["trade_date", "code", "name", "stock_code", "price", "convert_price",
              "convert_value", "premium_rate", "stock_price", "rating",
              "issue_size", "listed"]:
        assert c in cols


def test_cb_factors_table_exists(tmp_path):
    conn = open_db(str(tmp_path / "m.db"))
    cols = _cols(conn, "cb_factors")
    conn.close()
    for c in ["trade_date", "code", "bond_floor", "ytm", "year_left",
              "redeem_dist", "put_dist", "adj_count", "redeem_status"]:
        assert c in cols


def test_cb_picks_table_exists(tmp_path):
    conn = open_db(str(tmp_path / "m.db"))
    cols = _cols(conn, "cb_picks")
    conn.close()
    for c in ["trade_date", "rank", "code", "price", "total_score",
              "a_score", "b_score", "c_score", "size_pct", "shares",
              "stop_price", "tp_price", "reason", "risk_note", "factor_counts"]:
        assert c in cols


def test_cb_positions_table_exists(tmp_path):
    conn = open_db(str(tmp_path / "m.db"))
    cols = _cols(conn, "cb_positions")
    conn.close()
    for c in ["code", "name", "stock_code", "entry_date", "entry_price",
              "shares", "stop_price", "tp_price"]:
        assert c in cols


def test_cb_picks_pk_on_date_rank(tmp_path):
    conn = open_db(str(tmp_path / "m.db"))
    pk = [r[1] for r in conn.execute("PRAGMA table_info(cb_picks)").fetchall() if r[5] > 0]
    conn.close()
    assert pk == ["trade_date", "rank"]


def test_cb_positions_pk_on_code(tmp_path):
    conn = open_db(str(tmp_path / "m.db"))
    pk = [r[1] for r in conn.execute("PRAGMA table_info(cb_positions)").fetchall() if r[5] > 0]
    conn.close()
    assert pk == ["code"]


def test_cb_indexes_exist(tmp_path):
    conn = open_db(str(tmp_path / "m.db"))
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='cb_picks'"
    ).fetchall()]
    conn.close()
    assert "idx_cb_picks_date" in names
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_migration_convertible.py -v`
Expected: FAIL — `sqlite3.OperationalError: no such table: cb_universe`

- [ ] **Step 3: 创建迁移文件**

创建 `db/migrations/2026_10_24_convertible_bond.sql`：

```sql
-- db/migrations/2026_10_24_convertible_bond.sql
-- 可转债策略：独立于股票体系，cb_ 前缀表
CREATE TABLE IF NOT EXISTS cb_universe (
    trade_date     TEXT NOT NULL,
    code           TEXT NOT NULL,
    name           TEXT,
    stock_code     TEXT,
    stock_name     TEXT,
    price          REAL,
    convert_price  REAL,
    convert_value  REAL,
    premium_rate   REAL,
    stock_price    REAL,
    rating         TEXT,
    issue_size     REAL,
    listed         INTEGER DEFAULT 0,
    updated_at     TEXT,
    PRIMARY KEY (trade_date, code)
);

CREATE TABLE IF NOT EXISTS cb_factors (
    trade_date    TEXT NOT NULL,
    code          TEXT NOT NULL,
    bond_floor    REAL,
    ytm           REAL,
    year_left     REAL,
    redeem_dist   REAL,
    put_dist      REAL,
    adj_count     INTEGER,
    redeem_status TEXT,
    factor_json   TEXT,
    updated_at    TEXT,
    PRIMARY KEY (trade_date, code)
);

CREATE TABLE IF NOT EXISTS cb_picks (
    trade_date    TEXT NOT NULL,
    rank          INTEGER NOT NULL,
    code          TEXT NOT NULL,
    name          TEXT,
    price         REAL,
    total_score   REAL,
    a_score       REAL,
    b_score       REAL,
    c_score       REAL,
    size_pct      REAL,
    shares        INTEGER,
    stop_price    REAL,
    tp_price      REAL,
    reason        TEXT,
    risk_note     TEXT,
    factor_counts TEXT,
    updated_at    TEXT,
    PRIMARY KEY (trade_date, rank)
);

CREATE TABLE IF NOT EXISTS cb_positions (
    code        TEXT PRIMARY KEY,
    name        TEXT,
    stock_code  TEXT,
    entry_date  TEXT,
    entry_price REAL,
    shares      INTEGER,
    stop_price  REAL,
    tp_price    REAL,
    note        TEXT,
    updated_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_cb_picks_date ON cb_picks(trade_date);
CREATE INDEX IF NOT EXISTS idx_cb_universe_code ON cb_universe(code);
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_migration_convertible.py -v`
Expected: PASS（7 passed）

- [ ] **Step 5: 添加 config 常量**

修改 `config.py`，在文件末尾追加（**不要修改任何现有常量**）：

```python
# === 可转债策略（独立于股票体系）===
CB_SCORING_WEIGHTS = {"a": 0.40, "b": 0.30, "c": 0.30}   # 三组权重

CB_FILTERS = {
    "premium_min": -5.0,        # 转股溢价率下限 (%)
    "premium_max": 40.0,        # 转股溢价率上限 (%)
    "price_min": 80.0,          # 债现价下限
    "price_max": 200.0,         # 债现价上限
    "min_issue_size": 0.5,      # 剩余规模下限（亿元）
    "min_convert_value": 60.0,  # 转股价值下限
    "redeem_dist_min": 3.0,     # 强赎触发距离下限 (%)
    "exclude_ratings": ("B", "CC", "C"),  # 评级含这些子串即剔除
    "min_factors": 2,           # 最少可用因子数，低于则不进入榜单
}

CB_RISK = {
    "max_single": 0.15,       # 单只上限 15%
    "max_positions": 10,      # 最多 10 只
    "stop_loss_pct": 0.08,    # 止损 -8%
    "take_profit_pct": 0.20,  # 止盈 +20%
    "lot_size": 10,           # 整手：10 张 = 面值 1000 元
    "min_price": 80.0,
    "max_price": 200.0,
}
```

- [ ] **Step 6: 验证 config 导入正常**

Run: `uv run python -c "from config import CB_FILTERS, CB_RISK, CB_SCORING_WEIGHTS; print(CB_SCORING_WEIGHTS, len(CB_FILTERS), len(CB_RISK))"`
Expected: 打印 `{'a': 0.4, 'b': 0.3, 'c': 0.3} 9 7`

- [ ] **Step 7: 提交**

```bash
git add config.py db/migrations/2026_10_24_convertible_bond.sql tests/test_migration_convertible.py
git commit -m "feat(cb): 可转债策略数据表迁移 + 独立配置常量"
```

---

## Task 2: 仓储层 `cb_repository.py`

**Files:**
- Create: `cb_repository.py`
- Test: `tests/test_cb_repository.py`

- [ ] **Step 1: 写失败的仓储测试**

创建 `tests/test_cb_repository.py`：

```python
from cb_repository import (
    upsert_cb_universe_rows,
    get_cb_universe,
    upsert_cb_factors_rows,
    get_cb_factors,
    replace_cb_picks,
    get_cb_picks,
    upsert_cb_position,
    get_cb_positions,
    delete_cb_position,
)


def test_upsert_and_get_universe(tmp_path):
    conn = open_db_for_test(tmp_path)
    upsert_cb_universe_rows(conn, "2026-10-24", [
        {"code": "113059", "name": "福莱转债", "price": 115.3},
    ])
    rows = get_cb_universe(conn, "2026-10-24")
    conn.close()
    assert len(rows) == 1
    assert rows[0]["code"] == "113059"
    assert rows[0]["name"] == "福莱转债"


def test_universe_upsert_is_idempotent(tmp_path):
    conn = open_db_for_test(tmp_path)
    upsert_cb_universe_rows(conn, "2026-10-24", [{"code": "113059", "price": 115.3}])
    upsert_cb_universe_rows(conn, "2026-10-24", [{"code": "113059", "price": 118.0}])
    rows = get_cb_universe(conn, "2026-10-24")
    conn.close()
    assert len(rows) == 1
    assert rows[0]["price"] == 118.0


def test_upsert_and_get_factors(tmp_path):
    conn = open_db_for_test(tmp_path)
    upsert_cb_factors_rows(conn, "2026-10-24", [
        {"code": "113059", "bond_floor": 1.01, "year_left": 1.6, "redeem_dist": 12.3},
    ])
    rows = get_cb_factors(conn, "2026-10-24")
    conn.close()
    assert len(rows) == 1
    assert rows[0]["bond_floor"] == 1.01
    assert rows[0]["redeem_status"] is None


def test_replace_picks_overwrites_same_date(tmp_path):
    conn = open_db_for_test(tmp_path)
    replace_cb_picks(conn, "2026-10-24", [
        {"rank": 1, "code": "A", "total_score": 90.0},
    ])
    replace_cb_picks(conn, "2026-10-24", [
        {"rank": 1, "code": "B", "total_score": 80.0},
    ])
    rows = get_cb_picks(conn, "2026-10-24")
    conn.close()
    assert len(rows) == 1
    assert rows[0]["code"] == "B"


def test_position_crud(tmp_path):
    conn = open_db_for_test(tmp_path)
    upsert_cb_position(conn, {
        "code": "113059", "name": "福莱转债", "entry_date": "2026-10-20",
        "entry_price": 115.0, "shares": 50, "stop_price": 105.8, "tp_price": 138.0,
    })
    rows = get_cb_positions(conn)
    assert len(rows) == 1
    assert rows[0]["shares"] == 50
    upsert_cb_position(conn, {
        "code": "113059", "name": "福莱转债", "entry_date": "2026-10-20",
        "entry_price": 115.0, "shares": 80, "stop_price": 105.8, "tp_price": 138.0,
    })
    rows = get_cb_positions(conn)
    assert len(rows) == 1
    assert rows[0]["shares"] == 80
    delete_cb_position(conn, "113059")
    assert get_cb_positions(conn) == []
    conn.close()
```

在文件顶部加导入：

```python
from db_repository import open_db


def open_db_for_test(tmp_path):
    return open_db(str(tmp_path / "cb.db"))
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_cb_repository.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cb_repository'`

- [ ] **Step 3: 实现仓储层**

创建 `cb_repository.py`：

```python
"""可转债策略的 SQLite 读写层。

只操作 `cb_` 前缀的表，与股票体系（daily_picks / trade_plan / open_positions）
完全隔离。所有函数接受一个已打开的 sqlite3.Connection。
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from typing import Any, Dict, Iterable, List, Optional


def _now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# cb_universe
# ---------------------------------------------------------------------------

_UNIVERSE_COLS = [
    "name", "stock_code", "stock_name", "price", "convert_price",
    "convert_value", "premium_rate", "stock_price", "rating",
    "issue_size", "listed",
]


def upsert_cb_universe_rows(
    conn: sqlite3.Connection,
    trade_date: str,
    rows: Iterable[Dict[str, Any]],
) -> int:
    """写入全市场快照。同一 (trade_date, code) 覆盖旧值。返回写入行数。"""
    now = _now()
    n = 0
    for r in rows:
        code = str(r.get("code") or "").strip()
        if not code:
            continue
        conn.execute(
            """INSERT INTO cb_universe
               (trade_date, code, name, stock_code, stock_name, price, convert_price,
                convert_value, premium_rate, stock_price, rating, issue_size, listed,
                updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(trade_date, code) DO UPDATE SET
                 name=excluded.name, stock_code=excluded.stock_code,
                 stock_name=excluded.stock_name, price=excluded.price,
                 convert_price=excluded.convert_price,
                 convert_value=excluded.convert_value,
                 premium_rate=excluded.premium_rate, stock_price=excluded.stock_price,
                 rating=excluded.rating, issue_size=excluded.issue_size,
                 listed=excluded.listed, updated_at=excluded.updated_at""",
            (
                trade_date, code,
                r.get("name"), r.get("stock_code"), r.get("stock_name"),
                r.get("price"), r.get("convert_price"), r.get("convert_value"),
                r.get("premium_rate"), r.get("stock_price"), r.get("rating"),
                r.get("issue_size"), 1 if r.get("listed") else 0,
                now,
            ),
        )
        n += 1
    conn.commit()
    return n


def get_cb_universe(
    conn: sqlite3.Connection,
    trade_date: str,
) -> List[Dict[str, Any]]:
    cur = conn.execute(
        "SELECT * FROM cb_universe WHERE trade_date=? ORDER BY code", (trade_date,)
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# cb_factors
# ---------------------------------------------------------------------------

_FACTOR_COLS = [
    "bond_floor", "ytm", "year_left", "redeem_dist", "put_dist",
    "adj_count", "redeem_status", "factor_json",
]


def upsert_cb_factors_rows(
    conn: sqlite3.Connection,
    trade_date: str,
    rows: Iterable[Dict[str, Any]],
) -> int:
    now = _now()
    n = 0
    for r in rows:
        code = str(r.get("code") or "").strip()
        if not code:
            continue
        fj = r.get("factor_json")
        if isinstance(fj, (dict, list)):
            fj = json.dumps(fj, ensure_ascii=False)
        conn.execute(
            """INSERT INTO cb_factors
               (trade_date, code, bond_floor, ytm, year_left, redeem_dist, put_dist,
                adj_count, redeem_status, factor_json, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(trade_date, code) DO UPDATE SET
                 bond_floor=excluded.bond_floor, ytm=excluded.ytm,
                 year_left=excluded.year_left, redeem_dist=excluded.redeem_dist,
                 put_dist=excluded.put_dist, adj_count=excluded.adj_count,
                 redeem_status=excluded.redeem_status,
                 factor_json=excluded.factor_json, updated_at=excluded.updated_at""",
            (
                trade_date, code,
                r.get("bond_floor"), r.get("ytm"), r.get("year_left"),
                r.get("redeem_dist"), r.get("put_dist"), r.get("adj_count"),
                r.get("redeem_status"), fj, now,
            ),
        )
        n += 1
    conn.commit()
    return n


def get_cb_factors(
    conn: sqlite3.Connection,
    trade_date: str,
) -> List[Dict[str, Any]]:
    cur = conn.execute(
        "SELECT * FROM cb_factors WHERE trade_date=? ORDER BY code", (trade_date,)
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# cb_picks
# ---------------------------------------------------------------------------

_PICK_COLS = [
    "code", "name", "price", "total_score", "a_score", "b_score", "c_score",
    "size_pct", "shares", "stop_price", "tp_price", "reason", "risk_note",
    "factor_counts",
]


def replace_cb_picks(
    conn: sqlite3.Connection,
    trade_date: str,
    rows: Iterable[Dict[str, Any]],
) -> int:
    """覆盖写入某日榜单。先删该日旧行，再插入新行。"""
    conn.execute("DELETE FROM cb_picks WHERE trade_date=?", (trade_date,))
    now = _now()
    n = 0
    for r in rows:
        code = str(r.get("code") or "").strip()
        if not code:
            continue
        fc = r.get("factor_counts")
        if isinstance(fc, (dict, list)):
            fc = json.dumps(fc, ensure_ascii=False)
        conn.execute(
            """INSERT INTO cb_picks
               (trade_date, rank, code, name, price, total_score, a_score, b_score,
                c_score, size_pct, shares, stop_price, tp_price, reason, risk_note,
                factor_counts, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                trade_date, int(r.get("rank") or 0), code, r.get("name"),
                r.get("price"), r.get("total_score"), r.get("a_score"),
                r.get("b_score"), r.get("c_score"), r.get("size_pct"),
                r.get("shares"), r.get("stop_price"), r.get("tp_price"),
                r.get("reason"), r.get("risk_note"), fc, now,
            ),
        )
        n += 1
    conn.commit()
    return n


def get_cb_picks(
    conn: sqlite3.Connection,
    trade_date: str,
) -> List[Dict[str, Any]]:
    cur = conn.execute(
        "SELECT * FROM cb_picks WHERE trade_date=? ORDER BY rank", (trade_date,)
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# cb_positions
# ---------------------------------------------------------------------------


def upsert_cb_position(
    conn: sqlite3.Connection,
    row: Dict[str, Any],
) -> None:
    code = str(row.get("code") or "").strip()
    if not code:
        raise ValueError("cb_position requires code")
    conn.execute(
        """INSERT INTO cb_positions
           (code, name, stock_code, entry_date, entry_price, shares,
            stop_price, tp_price, note, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(code) DO UPDATE SET
             name=excluded.name, stock_code=excluded.stock_code,
             entry_date=excluded.entry_date, entry_price=excluded.entry_price,
             shares=excluded.shares, stop_price=excluded.stop_price,
             tp_price=excluded.tp_price, note=excluded.note,
             updated_at=excluded.updated_at""",
        (
            code, row.get("name"), row.get("stock_code"), row.get("entry_date"),
            row.get("entry_price"), int(row.get("shares") or 0),
            row.get("stop_price"), row.get("tp_price"), row.get("note"), _now(),
        ),
    )
    conn.commit()


def get_cb_positions(
    conn: sqlite3.Connection,
) -> List[Dict[str, Any]]:
    cur = conn.execute("SELECT * FROM cb_positions ORDER BY entry_date, code")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def delete_cb_position(conn: sqlite3.Connection, code: str) -> None:
    conn.execute("DELETE FROM cb_positions WHERE code=?", (code,))
    conn.commit()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_cb_repository.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: 提交**

```bash
git add cb_repository.py tests/test_cb_repository.py
git commit -m "feat(cb): 可转债 SQLite 仓储层（universe/factors/picks/positions）"
```

---

## Task 3: 数据源层 `cb_data_provider.py`

**Files:**
- Create: `cb_data_provider.py`
- Test: `tests/test_convertible.py`

- [ ] **Step 1: 写失败的数据源测试**

创建 `tests/test_convertible.py`（本任务先放前 5 个测试，后续任务继续追加）：

```python
from unittest.mock import patch

import pandas as pd
import pytest

from config import CB_FILTERS
from cb_data_provider import (
    fetch_universe_snapshot,
    fetch_bond_detail,
    fetch_redeem_status_map,
)


def _cov_df(rows):
    return pd.DataFrame(rows)


def test_fetch_universe_snapshot_maps_columns():
    df = _cov_df([
        {
            "债券代码": "113059", "债券简称": "福莱转债",
            "正股代码": "605488", "正股简称": "福莱新材",
            "正股价": 9.17, "转股价": 41.56, "转股价值": 22.06,
            "债现价": 115.31, "转股溢价率": 422.63,
            "发行规模": 10.0, "信用评级": "AA", "上市时间": pd.Timestamp("2023-07-10"),
        },
    ])
    with patch("cb_data_provider.ak.bond_zh_cov", return_value=df):
        rows = fetch_universe_snapshot()
    assert len(rows) == 1
    r = rows[0]
    assert r["code"] == "113059"
    assert r["name"] == "福莱转债"
    assert r["stock_code"] == "605488"
    assert r["price"] == 115.31
    assert r["convert_price"] == 41.56
    assert r["premium_rate"] == 422.63
    assert r["rating"] == "AA"
    assert r["listed"] is True


def test_fetch_universe_snapshot_marks_unlisted():
    df = _cov_df([
        {
            "债券代码": "127120", "债券简称": "千红转债",
            "正股代码": "002550", "正股简称": "千红制药",
            "正股价": 6.27, "转股价": 6.42, "转股价值": 97.66,
            "债现价": 100.0, "转股溢价率": 2.39,
            "发行规模": 10.0, "信用评级": "AA", "上市时间": pd.NaT,
        },
    ])
    with patch("cb_data_provider.ak.bond_zh_cov", return_value=df):
        rows = fetch_universe_snapshot()
    assert rows[0]["listed"] is False


def test_fetch_bond_detail_reads_pbv_ratio_and_expire():
    df = pd.DataFrame([{
        "SECURITY_NAME_ABBR": "福莱转债",
        "PBV_RATIO": 1.01,
        "EXPIRE_DATE": "2028-05-20 00:00:00",
        "RATING": "AA",
        "REDEEM_TRIG_PRICE": 54.03,
        "RESALE_TRIG_PRICE": 29.09,
        "TRANSFER_PRICE": 41.56,
        "CONVERT_STOCK_PRICE": 9.17,
        "TRANSFER_VALUE": 22.0645,
        "CURRENT_BOND_PRICE": "115.315",
    }])
    with patch("cb_data_provider.ak.bond_zh_cov_info", return_value=df):
        d = fetch_bond_detail("113059")
    assert d["code"] == "113059"
    assert d["bond_floor"] == 1.01
    assert d["expire_date"] == "2028-05-20"
    assert d["redeem_trig_price"] == 54.03
    assert d["resale_trig_price"] == 29.09


def test_fetch_bond_detail_tolerates_none_fields():
    df = pd.DataFrame([{
        "SECURITY_NAME_ABBR": "已退市",
        "PBV_RATIO": None,
        "EXPIRE_DATE": None,
        "RATING": "AA",
        "REDEEM_TRIG_PRICE": None,
        "RESALE_TRIG_PRICE": None,
        "TRANSFER_PRICE": None,
        "CONVERT_STOCK_PRICE": None,
        "TRANSFER_VALUE": None,
        "CURRENT_BOND_PRICE": None,
    }])
    with patch("cb_data_provider.ak.bond_zh_cov_info", return_value=df):
        d = fetch_bond_detail("113050")
    assert d["bond_floor"] is None
    assert d["expire_date"] is None
    assert d["redeem_trig_price"] is None


def test_fetch_bond_detail_handles_dash_price():
    df = pd.DataFrame([{
        "SECURITY_NAME_ABBR": "千红转债",
        "PBV_RATIO": 2.93,
        "EXPIRE_DATE": "2032-10-27 00:00:00",
        "RATING": "AA",
        "REDEEM_TRIG_PRICE": 8.35,
        "RESALE_TRIG_PRICE": 4.49,
        "TRANSFER_PRICE": 6.42,
        "CONVERT_STOCK_PRICE": 6.27,
        "TRANSFER_VALUE": 97.6636,
        "CURRENT_BOND_PRICE": "-",
    }])
    with patch("cb_data_provider.ak.bond_zh_cov_info", return_value=df):
        d = fetch_bond_detail("127120")
    assert d["current_bond_price"] is None
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_convertible.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'cb_data_provider'`

- [ ] **Step 3: 实现数据源层**

创建 `cb_data_provider.py`：

```python
"""可转债数据源层 — 多源降级。

数据源探明结论（2026-10-24 实测）：
- `ak.bond_zh_cov()` 全市场清单（1065 只），稳定。含未上市新券与已退市券。
- `ak.bond_zh_cov_info(symbol=...)` 单只条款（72 字段）。**必须用裸 6 位代码**，
  加 sh/sz 前缀会报 TypeError。
- `ak.bond_cb_redeem_jsl()` 强赎状态（319 只）。
- `ak.bond_cov_comparison()` 东财比价表 — 本机已被封 IP，仅作备源，失败静默降级。
- `ak.bond_cb_jsl()` 只返回 30 只（双低最低），不可作宇宙源。

所有取数失败都降级而非抛错：主源失败时读缓存快照，全失败时返回空列表。
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

import akshare as ak
import pandas as pd

from utils import retry_call


def _to_float(v: Any) -> Optional[float]:
    """容错转 float：None / '-' / '' / NaN → None。"""
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
        if v in ("", "-", "--", "None"):
            return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    if pd.isna(f):
        return None
    return f


def _to_date_str(v: Any) -> Optional[str]:
    """日期/时间戳 → 'YYYY-MM-DD'。空值 → None。"""
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
        if not v or v in ("-", "--", "NaT", "None"):
            return None
        try:
            return pd.to_datetime(v).strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            return None
    try:
        if pd.isna(v):
            return None
        return pd.to_datetime(v).strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# 全市场快照
# ---------------------------------------------------------------------------

def fetch_universe_snapshot() -> List[Dict[str, Any]]:
    """全市场可转债清单。失败返回 []（调用方负责降级到缓存）。"""
    try:
        df = retry_call(lambda: ak.bond_zh_cov())
    except Exception:
        return []
    if df is None or df.empty:
        return []

    rows: List[Dict[str, Any]] = []
    for _, r in df.iterrows():
        code = str(r.get("债券代码") or "").strip()
        if not code:
            continue
        listed = _to_date_str(r.get("上市时间")) is not None
        rows.append({
            "code": code,
            "name": str(r.get("债券简称") or "").strip(),
            "stock_code": str(r.get("正股代码") or "").strip(),
            "stock_name": str(r.get("正股简称") or "").strip(),
            "price": _to_float(r.get("债现价")),
            "convert_price": _to_float(r.get("转股价")),
            "convert_value": _to_float(r.get("转股价值")),
            "premium_rate": _to_float(r.get("转股溢价率")),
            "stock_price": _to_float(r.get("正股价")),
            "rating": str(r.get("信用评级") or "").strip(),
            "issue_size": _to_float(r.get("发行规模")),
            "listed": listed,
        })
    return rows


# ---------------------------------------------------------------------------
# 单只条款补全
# ---------------------------------------------------------------------------

def fetch_bond_detail(code: str) -> Dict[str, Any]:
    """取单只转债的条款字段。

    `symbol` 必须是裸 6 位代码 —— akshare 的这个接口不认 sh/sz 前缀。
    任何字段缺失都返回 None（该因子不参与打分），不抛异常。
    """
    out: Dict[str, Any] = {
        "code": code,
        "name": "",
        "bond_floor": None,       # PBV_RATIO — 纯债价值
        "expire_date": None,      # EXPIRE_DATE
        "rating": "",             # RATING
        "redeem_trig_price": None,   # REDEEM_TRIG_PRICE
        "resale_trig_price": None,   # RESALE_TRIG_PRICE
        "convert_price": None,    # TRANSFER_PRICE
        "stock_price": None,      # CONVERT_STOCK_PRICE
        "convert_value": None,    # TRANSFER_VALUE
        "current_bond_price": None,  # CURRENT_BOND_PRICE
    }
    try:
        df = retry_call(lambda: ak.bond_zh_cov_info(symbol=code))
    except Exception:
        return out
    if df is None or df.empty:
        return out

    r = df.iloc[0]
    out["name"] = str(r.get("SECURITY_NAME_ABBR") or "").strip()
    out["bond_floor"] = _to_float(r.get("PBV_RATIO"))
    out["expire_date"] = _to_date_str(r.get("EXPIRE_DATE"))
    out["rating"] = str(r.get("RATING") or "").strip()
    out["redeem_trig_price"] = _to_float(r.get("REDEEM_TRIG_PRICE"))
    out["resale_trig_price"] = _to_float(r.get("RESALE_TRIG_PRICE"))
    out["convert_price"] = _to_float(r.get("TRANSFER_PRICE"))
    out["stock_price"] = _to_float(r.get("CONVERT_STOCK_PRICE"))
    out["convert_value"] = _to_float(r.get("TRANSFER_VALUE"))
    out["current_bond_price"] = _to_float(r.get("CURRENT_BOND_PRICE"))
    return out


# ---------------------------------------------------------------------------
# 强赎状态
# ---------------------------------------------------------------------------

def fetch_redeem_status_map() -> Dict[str, Dict[str, Any]]:
    """{转债代码: {redeem_status, redeem_count, last_trade_date, ...}}。失败返回 {}。"""
    try:
        df = retry_call(lambda: ak.bond_cb_redeem_jsl())
    except Exception:
        return {}
    if df is None or df.empty:
        return {}

    out: Dict[str, Dict[str, Any]] = {}
    for _, r in df.iterrows():
        code = str(r.get("代码") or "").strip()
        if not code:
            continue
        out[code] = {
            "redeem_status": str(r.get("强赎状态") or "").strip(),
            "redeem_count": _to_float(r.get("强赎天计数")),
            "redeem_price": _to_float(r.get("强赎触发价")),
            "last_trade_date": _to_date_str(r.get("最后交易日")),
            "maturity_date": _to_date_str(r.get("到期日")),
            "convert_price": _to_float(r.get("转股价")),
            "stock_price": _to_float(r.get("正股价")),
        }
    return out


def today_str() -> str:
    return dt.date.today().isoformat()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_convertible.py -v`
Expected: PASS（5 passed）

- [ ] **Step 5: 提交**

```bash
git add cb_data_provider.py tests/test_convertible.py
git commit -m "feat(cb): 多源降级数据源层（akshare 可转债行情/条款/强赎）"
```

---

## Task 4: 粗筛与因子打分

**Files:**
- Create: `strategies/convertible.py`
- Modify: `tests/test_convertible.py`

- [ ] **Step 1: 写失败的粗筛/打分测试**

在 `tests/test_convertible.py` 末尾追加：

```python
from strategies.convertible import (
    prefilter,
    apply_hard_exclusions,
    score_bond,
    score_universe,
)


def _u(**kw):
    base = {
        "code": "113059", "name": "福莱转债", "stock_code": "605488",
        "stock_name": "福莱新材", "price": 115.31, "convert_price": 41.56,
        "convert_value": 22.06, "premium_rate": 422.63, "stock_price": 9.17,
        "rating": "AA", "issue_size": 10.0, "listed": True,
    }
    base.update(kw)
    return base


def test_prefilter_excludes_unlisted():
    assert prefilter([_u(listed=False)]) == []


def test_prefilter_excludes_st_stock():
    assert prefilter([_u(stock_name="*ST航图")]) == []
    assert prefilter([_u(stock_name="ST东时")]) == []


def test_prefilter_excludes_low_rating():
    assert prefilter([_u(rating="CC")]) == []
    assert prefilter([_u(rating="BB+")]) == []


def test_prefilter_excludes_extreme_premium():
    assert prefilter([_u(premium_rate=45.0)]) == []
    assert prefilter([_u(premium_rate=-10.0)]) == []


def test_prefilter_excludes_bad_price():
    assert prefilter([_u(price=75.0)]) == []
    assert prefilter([_u(price=250.0)]) == []


def test_prefilter_excludes_low_convert_value():
    assert prefilter([_u(convert_value=50.0)]) == []


def test_prefilter_excludes_small_issue_size():
    assert prefilter([_u(issue_size=0.2)]) == []


def test_prefilter_keeps_normal_bond():
    assert len(prefilter([_u(premium_rate=8.0, convert_value=97.0)])) == 1


def test_hard_exclusion_redeem_announced():
    rows = [_u(premium_rate=8.0, convert_value=97.0)]
    factors = {"113059": {"redeem_status": "已公告强赎", "redeem_dist": 50.0}}
    kept, dropped = apply_hard_exclusions(rows, factors)
    assert kept == []
    assert "113059" in dropped


def test_hard_exclusion_redeem_distance_below_3pct():
    rows = [_u(premium_rate=8.0, convert_value=97.0)]
    factors = {"113059": {"redeem_status": "", "redeem_dist": 2.0}}
    kept, dropped = apply_hard_exclusions(rows, factors)
    assert kept == []
    assert "113059" in dropped


def test_hard_exclusion_keeps_normal():
    rows = [_u(premium_rate=8.0, convert_value=97.0)]
    factors = {"113059": {"redeem_status": "", "redeem_dist": 25.0}}
    kept, dropped = apply_hard_exclusions(rows, factors)
    assert len(kept) == 1
    assert dropped == {}


def test_hard_exclusion_keeps_when_redeem_not_announced():
    # 「未公告强赎」「强赎条款」不能被误判为已公告
    rows = [_u(premium_rate=8.0, convert_value=97.0)]
    for status in ("未公告强赎", "强赎条款", "", "临近到期"):
        factors = {"113059": {"redeem_status": status, "redeem_dist": 25.0}}
        kept, dropped = apply_hard_exclusions(rows, factors)
        assert len(kept) == 1, status
        assert dropped == {}, status


def test_score_bond_floor_above_par():
    # bond_floor 是 PBV_RATIO（纯债价值/面值）。1.05 → 债底 105 元；
    # 债现价 100 元 → 保护度 1.05 ≥ 1 → 满分
    s = score_bond(_u(price=100.0, premium_rate=8.0, convert_value=97.0),
                   {"bond_floor": 1.05, "ytm": 2.0, "year_left": 3.5})
    assert s["a_score"] is not None
    assert s["factors"]["bond_floor"]["score"] == 100


def test_score_bond_floor_below_price_is_low():
    # 债底 85 元 vs 债现价 110 元 → 保护度 0.77 < 0.8 → 20 分
    s = score_bond(_u(price=110.0, premium_rate=8.0, convert_value=97.0),
                   {"bond_floor": 0.85, "ytm": 2.0, "year_left": 3.5})
    assert s["factors"]["bond_floor"]["score"] == 20


def test_score_premium_buckets():
    rows = [_u(premium_rate=8.0, convert_value=97.0)]
    s = score_bond(rows[0], {"bond_floor": 0.95, "ytm": 1.0, "year_left": 2.0})
    assert s["factors"]["premium"]["score"] == 90


def test_missing_factor_renormalizes_group():
    # rating 缺省时取中性 60（不计入缺失），price_level 恒可得，
    # 因此 A 组至少有 rating + price_level 两个因子参与
    rows = [_u(premium_rate=8.0, convert_value=97.0)]
    s = score_bond(rows[0], {"rating": "AA"})
    assert s["a_score"] is not None
    assert s["factor_counts"]["a"] == 2
    # bond_floor / ytm / year_left 缺失 → 不参与计分
    assert s["factors"]["bond_floor"] is None
    assert s["factors"]["ytm"] is None
    assert s["factors"]["year_left"] is None


def test_group_with_no_factors_is_none():
    # B 组需要一个可缺失的因子全部缺失时，B 组为 None，不参与总分
    rows = [_u(premium_rate=8.0, convert_value=97.0)]
    s = score_bond(rows[0], {"rating": "AA"})
    # premium / convert_ratio 恒可得，elasticity / stock_momentum 不可得 → B 组仍有因子
    assert s["b_score"] is not None


def test_score_below_min_factors_is_excluded():
    # A 组恒有 price_level + rating 两个因子，故 score_bond 仍能出分。
    # 最低因子门槛在 score_universe 层执行：因子数不足则不进入榜单。
    rows = [_u(premium_rate=8.0, convert_value=97.0)]
    s = score_bond(rows[0], {})
    assert s["total_score"] is not None
    assert sum(s["factor_counts"].values()) >= CB_FILTERS["min_factors"]

    # score_universe 层：因子数 < min_factors 的被剔除
    thin = score_universe(rows, {}, )
    assert all(sum(x["factor_counts"].values()) >= CB_FILTERS["min_factors"] for x in thin)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_convertible.py -v`
Expected: FAIL — `ImportError: cannot import name 'prefilter' from 'strategies.convertible'`

- [ ] **Step 3: 实现粗筛与打分**

创建 `strategies/convertible.py`：

```python
"""可转债策略 — 粗筛 / 因子打分 / 硬剔除 / 仓位计算。

打分模型：三组因子（债底 40% / 股性 30% / 条款 30%），组内等权。
因子缺失时该组按剩余因子重新归一化权重（不填 0，避免「缺数据 = 差」）。
整组无因子 → 该组为 None，不参与总分；三组全空 → 不评分。
"""
from __future__ import annotations

import datetime as dt
import math
from typing import Any, Dict, List, Optional, Tuple

from config import CB_FILTERS, CB_RISK, CB_SCORING_WEIGHTS


# ---------------------------------------------------------------------------
# 粗筛
# ---------------------------------------------------------------------------

def _is_st(name: str) -> bool:
    """正股是否 ST / *ST。"""
    n = (name or "").upper().replace(" ", "")
    return n.startswith("ST") or n.startswith("*ST") or "ST" in n


def _rating_excluded(rating: str) -> bool:
    r = (rating or "").upper()
    return any(bad in r for bad in CB_FILTERS["exclude_ratings"])


def _redeem_announced(status: str) -> bool:
    """强赎状态是否表示「已公告强赎」。

    JSL 的强赎状态是自由文本，实测形如「已公告强赎」「未公告强赎」
    「临近到期」「已触发强赎」等。必须精确匹配，避免把「未公告强赎」
    或「强赎条款」误判为已公告。
    """
    s = (status or "").strip()
    if not s:
        return False
    # 先排除明确的否定/说明性表述
    if "未公告" in s or "未触发" in s:
        return False
    if "条款" in s:
        return False
    return "已公告强赎" in s or "已触发强赎" in s


def prefilter(
    rows: List[Dict[str, Any]],
    premium_min: Optional[float] = None,
    premium_max: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """本地粗筛：剔除数据不可用 / 风险明显的转债。不打网络。

    premium_min / premium_max: None 时用 config.CB_FILTERS 的默认值。
    """
    pmin = CB_FILTERS["premium_min"] if premium_min is None else premium_min
    pmax = CB_FILTERS["premium_max"] if premium_max is None else premium_max
    out: List[Dict[str, Any]] = []
    for r in rows:
        if not r.get("listed"):
            continue
        if _is_st(str(r.get("stock_name") or "")):
            continue
        if _rating_excluded(str(r.get("rating") or "")):
            continue
        price = r.get("price")
        if price is None or not (CB_FILTERS["price_min"] <= price <= CB_FILTERS["price_max"]):
            continue
        premium = r.get("premium_rate")
        if premium is None or not (pmin <= premium <= pmax):
            continue
        cv = r.get("convert_value")
        if cv is None or cv < CB_FILTERS["min_convert_value"]:
            continue
        cp = r.get("convert_price")
        sp = r.get("stock_price")
        if cp is None or cp <= 0 or sp is None or sp <= 0:
            continue
        size = r.get("issue_size")
        if size is None or size < CB_FILTERS["min_issue_size"]:
            continue
        out.append(r)
    return out


# ---------------------------------------------------------------------------
# 硬剔除
# ---------------------------------------------------------------------------

def apply_hard_exclusions(
    rows: List[Dict[str, Any]],
    factors: Dict[str, Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    """剔除需要补全数据才能判定的风险项。返回 (存活列表, {code: 剔除原因})。"""
    kept: List[Dict[str, Any]] = []
    dropped: Dict[str, str] = {}
    for r in rows:
        code = r["code"]
        f = factors.get(code) or {}
        status = str(f.get("redeem_status") or "")
        if _redeem_announced(status):
            dropped[code] = "已公告强赎"
            continue
        dist = f.get("redeem_dist")
        if dist is not None and dist < CB_FILTERS["redeem_dist_min"]:
            dropped[code] = f"强赎距离仅 {dist:.1f}%"
            continue
        if _is_st(str(r.get("stock_name") or "")):
            dropped[code] = "正股 ST"
            continue
        kept.append(r)
    return kept, dropped


# ---------------------------------------------------------------------------
# 因子打分
# ---------------------------------------------------------------------------

def _score_bond_floor(r: Dict[str, Any], f: Dict[str, Any]) -> Optional[float]:
    """纯债价值保护度。

    `bond_floor` 存的是 **PBV_RATIO**（纯债价值/面值，如 1.01 表示债底 101 元），
    不是元。纯债价值(元) = PBV_RATIO × 100。保护度 = 纯债价值(元) / 债现价。
    """
    floor_ratio = f.get("bond_floor")
    price = r.get("price")
    if floor_ratio is None or price is None or price <= 0:
        return None
    ratio = (floor_ratio * 100.0) / price
    if ratio >= 1.0:
        return 100.0
    if ratio >= 0.95:
        return 90.0
    if ratio >= 0.90:
        return 75.0
    if ratio >= 0.85:
        return 60.0
    if ratio >= 0.80:
        return 40.0
    return 20.0


def _score_price_level(r: Dict[str, Any], f: Dict[str, Any]) -> Optional[float]:
    p = r.get("price")
    if p is None:
        return None
    if p <= 90:
        return 100.0
    if p <= 100:
        return 85.0
    if p <= 110:
        return 70.0
    if p <= 120:
        return 55.0
    if p <= 130:
        return 40.0
    return 20.0


def _score_ytm(r: Dict[str, Any], f: Dict[str, Any]) -> Optional[float]:
    y = f.get("ytm")
    if y is None:
        return None
    if y >= 3:
        return 100.0
    if y >= 2:
        return 85.0
    if y >= 1:
        return 70.0
    if y >= 0:
        return 55.0
    if y > -1:
        return 35.0
    return 15.0


_RATING_SCORES = {
    "AAA": 100.0, "AA+": 95.0, "AA": 85.0, "AA-": 70.0,
    "A+": 50.0, "A": 35.0, "A-": 20.0,
}


def _score_rating(r: Dict[str, Any], f: Dict[str, Any]) -> Optional[float]:
    r = (f.get("rating") or "").upper().strip()
    if not r:
        return 60.0  # 中性
    if _rating_excluded(r):
        return 0.0
    for key, score in _RATING_SCORES.items():
        if r.startswith(key):
            return score
    return 60.0


def _score_year_left(r: Dict[str, Any], f: Dict[str, Any]) -> Optional[float]:
    y = f.get("year_left")
    if y is None:
        return None
    if y < 1:
        return 30.0
    if y < 2:
        return 60.0
    if y < 3:
        return 80.0
    if y <= 6:
        return 100.0
    return 60.0


def _score_premium(r: Dict[str, Any], f: Dict[str, Any]) -> Optional[float]:
    p = r.get("premium_rate")
    if p is None:
        return None
    if p <= 5:
        return 100.0
    if p <= 10:
        return 90.0
    if p <= 15:
        return 75.0
    if p <= 20:
        return 60.0
    if p <= 30:
        return 40.0
    if p <= 50:
        return 20.0
    return 0.0


def _score_convert_ratio(r: Dict[str, Any], f: Dict[str, Any]) -> Optional[float]:
    cv = r.get("convert_value")
    if cv is None:
        return None
    if cv >= 110:
        return 100.0
    if cv >= 100:
        return 85.0
    if cv >= 95:
        return 70.0
    if cv >= 85:
        return 50.0
    return 30.0


def _score_elasticity(r: Dict[str, Any], f: Dict[str, Any]) -> Optional[float]:
    """债涨幅/股涨幅。当日涨跌幅不可得时，用溢价率倒推的近似。"""
    bond_pct = r.get("bond_pct_change")
    stock_pct = r.get("stock_pct_change")
    if bond_pct is None or stock_pct is None or abs(stock_pct) < 1e-6:
        return None
    ratio = bond_pct / stock_pct
    if ratio >= 1.0:
        return 100.0
    if ratio >= 0.8:
        return 75.0
    if ratio >= 0.6:
        return 50.0
    return 25.0


def _score_stock_momentum(r: Dict[str, Any], f: Dict[str, Any]) -> Optional[float]:
    """正股 20 日涨幅。第一期无正股历史 K 线 → 恒为 None（不计分）。"""
    m = f.get("stock_momentum")
    if m is None:
        return None
    if m >= 15:
        return 100.0
    if m >= 8:
        return 80.0
    if m >= 0:
        return 55.0
    if m >= -10:
        return 30.0
    return 15.0


def _score_redeem_dist(r: Dict[str, Any], f: Dict[str, Any]) -> Optional[float]:
    d = f.get("redeem_dist")
    if d is None:
        return None
    if d >= 40:
        return 100.0
    if d >= 25:
        return 85.0
    if d >= 15:
        return 70.0
    if d >= 8:
        return 50.0
    if d >= 3:
        return 30.0
    return 0.0


def _score_put_dist(r: Dict[str, Any], f: Dict[str, Any]) -> Optional[float]:
    d = f.get("put_dist")
    if d is None:
        return None
    if d >= -20:
        return 100.0
    if d >= -35:
        return 80.0
    if d >= -50:
        return 60.0
    if d >= -65:
        return 40.0
    return 20.0


def _score_adj_count(r: Dict[str, Any], f: Dict[str, Any]) -> Optional[float]:
    """下修次数与得分正相关：多次下修 = 公司促转股意愿强。第一期无可靠源 → 50 中性。"""
    n = f.get("adj_count")
    if n is None:
        return 50.0
    if n <= 0:
        return 50.0
    if n == 1:
        return 80.0
    if n == 2:
        return 100.0
    return 95.0


# 每组：(因子名, 打分函数)
_GROUP_A = [
    ("bond_floor", _score_bond_floor),
    ("price_level", _score_price_level),
    ("ytm", _score_ytm),
    ("rating", _score_rating),
    ("year_left", _score_year_left),
]
_GROUP_B = [
    ("premium", _score_premium),
    ("convert_ratio", _score_convert_ratio),
    ("elasticity", _score_elasticity),
    ("stock_momentum", _score_stock_momentum),
]
_GROUP_C = [
    ("redeem_dist", _score_redeem_dist),
    ("put_dist", _score_put_dist),
    ("adj_count", _score_adj_count),
]

GROUPS = {"a": _GROUP_A, "b": _GROUP_B, "c": _GROUP_C}


def _combine_group(
    group: List[Tuple[str, Any]],
    r: Dict[str, Any],
    f: Dict[str, Any],
) -> Tuple[Optional[float], Dict[str, Any], int]:
    """组内等权 + 缺失因子重新归一化。返回 (组分, 因子明细, 参与因子数)。"""
    detail: Dict[str, Any] = {}
    scores: List[float] = []
    for name, fn in group:
        try:
            s = fn(r, f)
        except Exception:
            s = None
        if s is None:
            detail[name] = None
            continue
        detail[name] = {"score": s}
        scores.append(s)
    if not scores:
        return None, detail, 0
    return sum(scores) / len(scores), detail, len(scores)


def score_bond(
    r: Dict[str, Any],
    f: Dict[str, Any],
    weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """给单只转债打分。返回 {total_score, a_score, b_score, c_score, factors, factor_counts}。"""
    weights = weights or CB_SCORING_WEIGHTS
    f = f or {}

    a_score, a_detail, a_n = _combine_group(_GROUP_A, r, f)
    b_score, b_detail, b_n = _combine_group(_GROUP_B, r, f)
    c_score, c_detail, c_n = _combine_group(_GROUP_C, r, f)

    parts = {"a": (a_score, a_n), "b": (b_score, b_n), "c": (c_score, c_n)}
    live = {k: v for k, v in parts.items() if v[0] is not None}
    if not live:
        return {
            "total_score": None, "a_score": a_score, "b_score": b_score,
            "c_score": c_score, "factors": {**a_detail, **b_detail, **c_detail},
            "factor_counts": {"a": a_n, "b": b_n, "c": c_n},
        }

    wsum = sum(weights[k] for k in live)
    total = sum(weights[k] * v[0] for k, v in live.items()) / wsum

    factors: Dict[str, Any] = {}
    for k, detail in (("a", a_detail), ("b", b_detail), ("c", c_detail)):
        for name, d in detail.items():
            factors[name] = d

    # 原始因子值（供风险提示等需要方向性数值的场景使用）
    raw_factors = {
        "bond_floor": f.get("bond_floor"),
        "premium_rate": r.get("premium_rate"),
        "convert_value": r.get("convert_value"),
        "redeem_dist": f.get("redeem_dist"),
        "put_dist": f.get("put_dist"),
        "year_left": f.get("year_left"),
        "issue_size": r.get("issue_size"),
    }

    return {
        "total_score": round(total, 4),
        "a_score": None if a_score is None else round(a_score, 4),
        "b_score": None if b_score is None else round(b_score, 4),
        "c_score": None if c_score is None else round(c_score, 4),
        "factors": factors,
        "raw_factors": raw_factors,
        "factor_counts": {"a": a_n, "b": b_n, "c": c_n},
    }


def score_universe(
    rows: List[Dict[str, Any]],
    factors: Dict[str, Dict[str, Any]],
    weights: Optional[Dict[str, float]] = None,
) -> List[Dict[str, Any]]:
    """给全量转债打分并按总分降序返回。只保留 total_score 非 None 的。"""
    out: List[Dict[str, Any]] = []
    for r in rows:
        f = factors.get(r["code"]) or {}
        s = score_bond(r, f, weights=weights)
        if s["total_score"] is None:
            continue
        if s["factor_counts"] and sum(s["factor_counts"].values()) < CB_FILTERS["min_factors"]:
            continue
        out.append({**r, **s})
    out.sort(key=lambda x: x["total_score"], reverse=True)
    return out
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_convertible.py -v`
Expected: PASS（全部通过）

- [ ] **Step 5: 提交**

```bash
git add strategies/convertible.py tests/test_convertible.py
git commit -m "feat(cb): 粗筛、硬剔除与三组因子打分模型"
```

---

## Task 5: 因子派生（补全→因子）+ 仓位计算

**Files:**
- Modify: `strategies/convertible.py`
- Modify: `tests/test_convertible.py`

- [ ] **Step 1: 写失败的派生/仓位测试**

在 `tests/test_convertible.py` 末尾追加：

```python
from strategies.convertible import build_factor_map, size_shares_cb, build_pick_rows


def test_build_factor_map_computes_year_left_and_distances():
    rows = [_u(premium_rate=8.0, convert_value=97.0)]
    details = {
        "113059": {
            "bond_floor": 1.01,
            "expire_date": "2028-05-20",
            "redeem_trig_price": 54.03,
            "resale_trig_price": 29.09,
            "convert_price": 41.56,
            "stock_price": 9.17,
        }
    }
    today = "2026-10-24"
    fm = build_factor_map(rows, details, today=today)
    f = fm["113059"]
    assert f["year_left"] is not None
    assert 1.5 < f["year_left"] < 1.7
    # 强赎距离 = (54.03 / 9.17 - 1) * 100 ≈ 489%
    assert f["redeem_dist"] > 400
    # 回售距离 = (29.09 / 9.17 - 1) * 100 ≈ 217%
    assert f["put_dist"] > 200
    assert f["adj_count"] == 50


def test_build_factor_map_estimates_ytm():
    rows = [_u(price=100.0, premium_rate=8.0, convert_value=97.0)]
    details = {"113059": {"expire_date": "2028-05-20", "bond_floor": 0.98}}
    fm = build_factor_map(rows, details, today="2026-10-24")
    f = fm["113059"]
    assert f["ytm"] is not None
    assert f["ytm_estimated"] is True


def test_build_factor_map_handles_missing_expire_date():
    rows = [_u(premium_rate=8.0, convert_value=97.0)]
    details = {"113059": {"expire_date": None}}
    fm = build_factor_map(rows, details, today="2026-10-24")
    assert fm["113059"]["year_left"] is None
    assert fm["113059"]["ytm"] is None


def test_size_shares_cb_uses_lot_of_10():
    # 100000 * 0.15 / (115.31 * 10) = 13.0 手 → 130 张（13 × 10）
    assert size_shares_cb(100000.0, 0.15, 115.31) == 130
    # 100000 * 0.15 / (50.0 * 10) = 30 手 → 300 张
    assert size_shares_cb(100000.0, 0.15, 50.0) == 300
    # 张数必须是 10 的整数倍
    assert size_shares_cb(100000.0, 0.15, 137.77) % 10 == 0


def test_size_shares_cb_skips_if_less_than_one_lot():
    # 1000 * 0.15 / (200 * 10) = 0.075 手 → 不足 1 手
    assert size_shares_cb(1000.0, 0.15, 200.0) == 0


def test_build_pick_rows_ranked_and_capped():
    rows = [_u(code=f"code{i}", premium_rate=5.0 + i, convert_value=97.0)
            for i in range(15)]
    scored = score_universe(rows, {})
    picks = build_pick_rows(scored, top_n=10, capital=100000.0)
    assert len(picks) == 10
    assert picks[0]["rank"] == 1
    assert picks[0]["size_pct"] == 0.15
    assert picks[0]["stop_price"] < picks[0]["price"]
    assert picks[0]["tp_price"] > picks[0]["price"]
    assert picks[0]["shares"] % 10 == 0
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_convertible.py -v`
Expected: FAIL — `ImportError: cannot import name 'build_factor_map'`

- [ ] **Step 3: 实现派生与仓位**

在 `strategies/convertible.py` 末尾追加：

```python
# ---------------------------------------------------------------------------
# 因子派生（补全数据 → 打分因子）
# ---------------------------------------------------------------------------

def _years_between(today: str, expire_date: str) -> Optional[float]:
    try:
        t = dt.date.fromisoformat(today)
        e = dt.date.fromisoformat(expire_date)
    except (ValueError, TypeError):
        return None
    return (e - t).days / 365.0


def build_factor_map(
    rows: List[Dict[str, Any]],
    details: Dict[str, Dict[str, Any]],
    today: Optional[str] = None,
) -> Dict[str, Dict[str, Any]]:
    """把补全后的条款数据转成打分因子。

    缺失一律写 None（该因子不参与打分），不抛异常。
    """
    today = today or dt.date.today().isoformat()
    out: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        code = r["code"]
        d = details.get(code) or {}

        year_left = _years_between(today, d.get("expire_date")) if d.get("expire_date") else None

        # 强赎/回售触发距离：优先用补全数据，缺失则用转股价 ×1.3 / ×0.7 估算
        cp = d.get("convert_price") or r.get("convert_price")
        sp = d.get("stock_price") or r.get("stock_price")
        redeem_trig = d.get("redeem_trig_price")
        resale_trig = d.get("resale_trig_price")
        if redeem_trig is None and cp:
            redeem_trig = cp * 1.3
        if resale_trig is None and cp:
            resale_trig = cp * 0.7

        redeem_dist = None
        if redeem_trig and sp and sp > 0:
            redeem_dist = round((redeem_trig / sp - 1.0) * 100.0, 4)
        put_dist = None
        if resale_trig and sp and sp > 0:
            put_dist = round((resale_trig / sp - 1.0) * 100.0, 4)

        # 到期税前收益：到期赎回价默认面值 ×1.10（主流条款）
        price = r.get("price")
        ytm = None
        ytm_estimated = False
        if price and price > 0 and year_left and year_left > 0:
            redeem_at_maturity = 110.0
            ytm = round(
                ((redeem_at_maturity / price) ** (1.0 / year_left) - 1.0) * 100.0, 4
            )
            ytm_estimated = True

        out[code] = {
            "bond_floor": d.get("bond_floor"),
            "expire_date": d.get("expire_date"),
            "year_left": year_left,
            "ytm": ytm,
            "ytm_estimated": ytm_estimated,
            "redeem_dist": redeem_dist,
            "put_dist": put_dist,
            "adj_count": d.get("adj_count") if d.get("adj_count") is not None else 50,
            "redeem_status": d.get("redeem_status"),
            "rating": d.get("rating") or r.get("rating"),
        }
    return out


# ---------------------------------------------------------------------------
# 仓位计算
# ---------------------------------------------------------------------------

def size_shares_cb(capital: float, size_pct: float, price: float) -> int:
    """可买张数（10 的整数倍）。不足 1 手返回 0。"""
    if capital <= 0 or size_pct <= 0 or price <= 0:
        return 0
    lot = CB_RISK["lot_size"]
    raw = (capital * size_pct) / (price * lot)
    n = int(raw) * lot
    return n if n >= lot else 0


def _build_reason(s: Dict[str, Any]) -> str:
    """自动生成入选理由。"""
    parts: List[str] = []
    factors = s.get("factors") or {}
    bf = factors.get("bond_floor")
    if bf and bf.get("score", 0) >= 75:
        parts.append("债底保护充分")
    pm = factors.get("premium")
    if pm and pm.get("score", 0) >= 75:
        parts.append("溢价率偏低")
    rd = factors.get("redeem_dist")
    if rd and rd.get("score", 0) >= 50:
        parts.append("距强赎安全")
    if not parts:
        parts.append("综合评分居前")
    return " + ".join(parts)


def _build_risk_note(r: Dict[str, Any], s: Dict[str, Any]) -> str:
    """自动生成风险提示。

    `put_dist` 用**原始距离**（负数表示已进入回售区）判定，
    不是打分（打分为 0-100，无法表示方向）。
    """
    notes: List[str] = []
    size = r.get("issue_size")
    if size is not None and size < 1.0:
        notes.append(f"剩余规模 {size:.1f} 亿偏小")
    cv = r.get("convert_value")
    if cv is not None and cv < 90:
        notes.append(f"转股价值 {cv:.0f} 偏低")
    put_dist = (s.get("raw_factors") or {}).get("put_dist")
    if put_dist is not None and put_dist < -50:
        notes.append("已进入回售区")
    if not notes:
        return ""
    return "；".join(notes)


def build_pick_rows(
    scored: List[Dict[str, Any]],
    top_n: int = 20,
    capital: float = 100000.0,
) -> List[Dict[str, Any]]:
    """把打分结果转成榜单行（含仓位、止损、止盈、理由、风险提示）。"""
    size_pct = CB_RISK["max_single"]
    stop_pct = CB_RISK["stop_loss_pct"]
    tp_pct = CB_RISK["take_profit_pct"]

    out: List[Dict[str, Any]] = []
    for i, s in enumerate(scored[:top_n], start=1):
        price = s.get("price") or 0.0
        shares = size_shares_cb(capital, size_pct, price) if price > 0 else 0
        if shares <= 0:
            continue
        out.append({
            "rank": len(out) + 1,
            "code": s["code"],
            "name": s.get("name") or s["code"],
            "price": round(price, 4),
            "total_score": s["total_score"],
            "a_score": s.get("a_score"),
            "b_score": s.get("b_score"),
            "c_score": s.get("c_score"),
            "size_pct": size_pct,
            "shares": shares,
            "stop_price": round(price * (1 - stop_pct), 4),
            "tp_price": round(price * (1 + tp_pct), 4),
            "reason": _build_reason(s),
            "risk_note": _build_risk_note(s, s),
            "factor_counts": s.get("factor_counts"),
        })
    return out
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_convertible.py -v`
Expected: PASS（全部通过）

- [ ] **Step 5: 提交**

```bash
git add strategies/convertible.py tests/test_convertible.py
git commit -m "feat(cb): 因子派生（到期/强赎距离/ytm）与 10 张整手仓位计算"
```

---

## Task 6: 退出预警

**Files:**
- Modify: `strategies/convertible.py`
- Modify: `tests/test_convertible.py`

- [ ] **Step 1: 写失败的预警测试**

在 `tests/test_convertible.py` 末尾追加：

```python
from strategies.convertible import evaluate_alerts, ALERT_LEVELS


def test_alert_emergency_on_redeem_announced():
    alerts = evaluate_alerts(
        positions=[{"code": "113059", "name": "福莱转债", "shares": 50,
                    "entry_price": 115.0, "stop_price": 105.8, "tp_price": 138.0}],
        quotes={"113059": {"price": 130.0, "redeem_status": "已公告强赎",
                           "redeem_dist": 20.0, "premium_rate": 5.0,
                           "stock_price": 30.0, "convert_price": 20.0}},
    )
    assert len(alerts) == 1
    assert alerts[0]["level"] == "emergency"
    assert "强赎" in alerts[0]["message"]


def test_alert_emergency_on_redeem_distance_below_3pct():
    alerts = evaluate_alerts(
        positions=[{"code": "113059", "name": "福莱转债", "shares": 50,
                    "entry_price": 115.0, "stop_price": 105.8, "tp_price": 138.0}],
        quotes={"113059": {"price": 130.0, "redeem_status": "",
                           "redeem_dist": 2.0, "premium_rate": 5.0,
                           "stock_price": 30.0, "convert_price": 20.0}},
    )
    assert alerts[0]["level"] == "emergency"


def test_alert_important_on_stop_loss():
    alerts = evaluate_alerts(
        positions=[{"code": "113059", "name": "福莱转债", "shares": 50,
                    "entry_price": 115.0, "stop_price": 110.0, "tp_price": 138.0}],
        quotes={"113059": {"price": 105.0, "redeem_status": "", "redeem_dist": 30.0,
                           "premium_rate": 5.0, "stock_price": 30.0,
                           "convert_price": 20.0}},
    )
    assert alerts[0]["level"] == "important"
    assert "止损" in alerts[0]["message"]


def test_alert_important_on_put_zone():
    alerts = evaluate_alerts(
        positions=[{"code": "113059", "name": "福莱转债", "shares": 50,
                    "entry_price": 115.0, "stop_price": 100.0, "tp_price": 138.0}],
        quotes={"113059": {"price": 108.0, "redeem_status": "", "redeem_dist": 30.0,
                           "premium_rate": 5.0, "stock_price": 13.0,
                           "convert_price": 20.0}},
    )
    assert alerts[0]["level"] == "important"
    assert "回售" in alerts[0]["message"]


def test_alert_watch_on_redeem_distance_3_to_8pct():
    alerts = evaluate_alerts(
        positions=[{"code": "113059", "name": "福莱转债", "shares": 50,
                    "entry_price": 115.0, "stop_price": 100.0, "tp_price": 138.0}],
        quotes={"113059": {"price": 108.0, "redeem_status": "", "redeem_dist": 5.0,
                           "premium_rate": 5.0, "stock_price": 30.0,
                           "convert_price": 20.0}},
    )
    assert alerts[0]["level"] == "watch"


def test_alert_watch_on_high_premium():
    alerts = evaluate_alerts(
        positions=[{"code": "113059", "name": "福莱转债", "shares": 50,
                    "entry_price": 115.0, "stop_price": 100.0, "tp_price": 138.0}],
        quotes={"113059": {"price": 180.0, "redeem_status": "", "redeem_dist": 30.0,
                           "premium_rate": 60.0, "stock_price": 30.0,
                           "convert_price": 20.0}},
    )
    assert alerts[0]["level"] == "watch"


def test_alert_normal_on_take_profit():
    alerts = evaluate_alerts(
        positions=[{"code": "113059", "name": "福莱转债", "shares": 50,
                    "entry_price": 115.0, "stop_price": 100.0, "tp_price": 130.0}],
        quotes={"113059": {"price": 135.0, "redeem_status": "", "redeem_dist": 30.0,
                           "premium_rate": 5.0, "stock_price": 30.0,
                           "convert_price": 20.0}},
    )
    assert alerts[0]["level"] == "normal"


def test_no_alert_for_healthy_position():
    alerts = evaluate_alerts(
        positions=[{"code": "113059", "name": "福莱转债", "shares": 50,
                    "entry_price": 115.0, "stop_price": 100.0, "tp_price": 138.0}],
        quotes={"113059": {"price": 118.0, "redeem_status": "", "redeem_dist": 30.0,
                           "premium_rate": 5.0, "stock_price": 30.0,
                           "convert_price": 20.0}},
    )
    assert alerts == []
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_convertible.py -v`
Expected: FAIL — `ImportError: cannot import name 'evaluate_alerts'`

- [ ] **Step 3: 实现预警判定**

在 `strategies/convertible.py` 末尾追加：

```python
# ---------------------------------------------------------------------------
# 退出预警
# ---------------------------------------------------------------------------

# 级别从高到低；同一持仓命中多条时取最高级别
ALERT_LEVELS = ("emergency", "important", "watch", "normal")

_LEVEL_LABEL = {
    "emergency": "🔴 紧急",
    "important": "🟠 重要",
    "watch": "🟡 关注",
    "normal": "⚪ 常规",
}


def evaluate_alerts(
    positions: List[Dict[str, Any]],
    quotes: Dict[str, Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """扫描持仓生成退出预警。

    positions: [{code, name, shares, entry_price, stop_price, tp_price}, ...]
    quotes:    {code: {price, redeem_status, redeem_dist, premium_rate,
                      stock_price, convert_price}, ...}

    规则（同一持仓命中多条取最高级别）：
      emergency  已公告强赎 / 强赎距离 < 3%
      important  跌破止损 / 进入回售区（正股 < 转股价×0.7）
      watch      强赎距离 3-8% / 转股溢价率 > 50%
      normal     达到止盈目标
    """
    out: List[Dict[str, Any]] = []
    for p in positions:
        code = p.get("code")
        q = quotes.get(code) or {}
        price = q.get("price")
        status = str(q.get("redeem_status") or "")
        redeem_dist = q.get("redeem_dist")
        premium = q.get("premium_rate")
        stock_price = q.get("stock_price")
        convert_price = q.get("convert_price")

        hits: List[Tuple[str, str]] = []

        if _redeem_announced(status):
            hits.append(("emergency", "已公告强赎，立即清仓"))
        elif redeem_dist is not None and redeem_dist < CB_FILTERS["redeem_dist_min"]:
            hits.append(("emergency", f"距强赎仅 {redeem_dist:.1f}%，立即清仓"))

        if price is not None:
            stop = p.get("stop_price")
            if stop is not None and price <= stop:
                hits.append(("important", f"跌破止损价 {stop:.2f}，减仓"))
        if stock_price is not None and convert_price:
            if stock_price < convert_price * 0.7:
                hits.append(("important", "正股进入回售区，评估回售"))

        if redeem_dist is not None and 3.0 <= redeem_dist < 8.0:
            hits.append(("watch", f"距强赎 {redeem_dist:.1f}%，注意"))
        if premium is not None and premium > 50.0:
            hits.append(("watch", f"转股溢价率 {premium:.1f}%，估值透支"))

        if price is not None:
            tp = p.get("tp_price")
            if tp is not None and price >= tp:
                hits.append(("normal", f"达到止盈目标 {tp:.2f}，分批止盈"))

        if not hits:
            continue
        level = min((h[0] for h in hits), key=lambda lv: ALERT_LEVELS.index(lv))
        messages = [h[1] for h in hits if h[0] == level]
        out.append({
            "code": code,
            "name": p.get("name") or code,
            "shares": p.get("shares"),
            "level": level,
            "level_label": _LEVEL_LABEL[level],
            "price": price,
            "message": "；".join(messages),
        })
    out.sort(key=lambda a: ALERT_LEVELS.index(a["level"]))
    return out
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run pytest tests/test_convertible.py -v`
Expected: PASS（全部通过）

- [ ] **Step 5: 提交**

```bash
git add strategies/convertible.py tests/test_convertible.py
git commit -m "feat(cb): 四级退出预警（强赎/止损回售/溢价透支/止盈）"
```

---

## Task 7: Pipeline 编排（`build_cb_picks`）+ CLI

**Files:**
- Create: `cb_pipeline.py`
- Modify: `cli_layer.py`
- Test: `tests/test_cli_convertible.py`

- [ ] **Step 1: 写失败的 CLI 测试**

创建 `tests/test_cli_convertible.py`：

```python
import json

import pytest

from cli_layer import build_parser, run_cli


def _args(*argv):
    return build_parser().parse_args(list(argv))


def test_cb_picks_parser_defaults():
    a = _args("cb-picks")
    assert a.top == 20
    assert a.db == "hs300.db"
    assert a.dry_run is False


def test_cb_picks_parser_accepts_weights():
    a = _args("cb-picks", "--weights", "a=0.5,b=0.25,c=0.25")
    assert a.weights == "a=0.5,b=0.25,c=0.25"


def test_cb_alerts_parser_accepts_json():
    a = _args("cb-alerts", "--json")
    assert a.json is True


def test_parse_weights_string():
    from cli_layer import _parse_weights
    w = _parse_weights("a=0.5,b=0.25,c=0.25")
    assert w == {"a": 0.5, "b": 0.25, "c": 0.25}


def test_parse_weights_none_returns_none():
    from cli_layer import _parse_weights
    assert _parse_weights(None) is None


def test_parse_weights_invalid_raises():
    from cli_layer import _parse_weights
    with pytest.raises(ValueError):
        _parse_weights("a=0.5,b=oops")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_cli_convertible.py -v`
Expected: FAIL — `AttributeError: 'Namespace' object has no attribute 'weights'`（或 `ImportError: cannot import name '_parse_weights'`）

- [ ] **Step 3: 实现 pipeline 编排**

创建 `cb_pipeline.py`：

```python
"""可转债每日榜单 pipeline 编排。

流程：全市场快照 → 落库 → 粗筛 → 逐只补全 → 因子派生 → 硬剔除 → 打分 → 落库榜单。
主源失败时读当日 cb_universe 缓存兜底；全失败则返回空结果并告警。
"""
from __future__ import annotations

import datetime as dt
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple, Tuple

from cb_data_provider import (
    fetch_bond_detail,
    fetch_redeem_status_map,
    fetch_universe_snapshot,
)
from cb_repository import (
    get_cb_universe,
    replace_cb_picks,
    upsert_cb_factors_rows,
    upsert_cb_universe_rows,
)
from config import CB_FILTERS
from db_repository import open_db
from strategies.convertible import (
    apply_hard_exclusions,
    build_factor_map,
    build_pick_rows,
    prefilter,
    score_universe,
)


def _parse_weights(weights: Optional[Dict[str, float]]) -> Optional[Dict[str, float]]:
    if not weights:
        return None
    total = sum(weights.values())
    if total <= 0:
        raise ValueError("weights sum must be > 0")
    return {k: v / total for k, v in weights.items()}


def collect_universe(
    db_path: str,
    trade_date: str,
    progress: Optional[Callable[[int, str], None]] = None,
) -> Tuple[List[Dict[str, Any]], str]:
    """取全市场快照并落库。失败时回退到当日缓存。返回 (rows, source)。"""
    def _emit(pct: int, msg: str) -> None:
        if progress:
            progress(pct, msg)

    _emit(5, "拉取全市场可转债清单…")
    rows = fetch_universe_snapshot()
    if rows:
        conn = open_db(db_path)
        try:
            upsert_cb_universe_rows(conn, trade_date, rows)
        finally:
            conn.close()
        _emit(20, f"全市场 {len(rows)} 只已落库")
        return rows, "live"

    _emit(20, "主源失败，回退当日缓存快照…")
    conn = open_db(db_path)
    try:
        cached = get_cb_universe(conn, trade_date)
    finally:
        conn.close()
    if cached:
        return cached, "cache"
    return [], "none"


def enrich_details(
    rows: List[Dict[str, Any]],
    concurrency: int = 4,
    rate: float = 6.0,
    progress: Optional[Callable[[int, str], None]] = None,
) -> Dict[str, Dict[str, Any]]:
    """逐只补全条款。限速 + 并发；单只失败不中断。"""
    def _emit(pct: int, msg: str) -> None:
        if progress:
            progress(pct, msg)

    details: Dict[str, Dict[str, Any]] = {}
    if not rows:
        return details

    gap = 1.0 / max(rate, 0.1)
    lock_free: List[Any] = []

    def _one(r: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
        time.sleep(gap)
        return r["code"], fetch_bond_detail(r["code"])

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        for i, (code, detail) in enumerate(pool.map(_one, rows), start=1):
            details[code] = detail
            if i % 20 == 0 or i == len(rows):
                _emit(20 + int(50 * i / len(rows)), f"补全 {i}/{len(rows)}")
    return details


def build_cb_picks(
    db_path: str,
    trade_date: Optional[str] = None,
    top_n: int = 20,
    capital: float = 100000.0,
    weights: Optional[Dict[str, float]] = None,
    concurrency: int = 4,
    rate: float = 6.0,
    premium_range: Optional[Tuple[float, float]] = None,
    dry_run: bool = False,
    progress: Optional[Callable[[int, str], None]] = None,
) -> Dict[str, Any]:
    """完整跑一遍转债榜单。返回统计信息。

    premium_range: 覆盖转股溢价率区间（如 (5, 30)），None 时用 config.CB_FILTERS 默认值。
    """
    trade_date = trade_date or dt.date.today().isoformat()

    def _emit(pct: int, msg: str) -> None:
        if progress:
            progress(pct, msg)

    universe, source = collect_universe(db_path, trade_date, progress=progress)
    if not universe:
        return {"ok": False, "source": source, "universe": 0, "candidates": 0,
                "picks": [], "dropped": {}, "warning": "全市场数据源不可用且无缓存"}

    pmin, pmax = premium_range or (CB_FILTERS["premium_min"], CB_FILTERS["premium_max"])
    candidates = prefilter(universe, premium_min=pmin, premium_max=pmax)
    _emit(25, f"粗筛后候选 {len(candidates)} 只")

    details = enrich_details(candidates, concurrency=concurrency, rate=rate, progress=progress)
    redeem_map = fetch_redeem_status_map()
    _emit(75, f"强赎状态 {len(redeem_map)} 只")

    # 合并强赎信息到补全结果
    for code, r in redeem_map.items():
        d = details.get(code)
        if d is None:
            continue
        d["redeem_status"] = r.get("redeem_status") or d.get("redeem_status")
        if d.get("redeem_trig_price") is None:
            d["redeem_trig_price"] = r.get("redeem_price")

    factors = build_factor_map(candidates, details, today=trade_date)
    kept, dropped = apply_hard_exclusions(candidates, factors)
    _emit(85, f"硬剔除 {len(dropped)} 只")

    scored = score_universe(kept, factors, weights=_parse_weights(weights))
    picks = build_pick_rows(scored, top_n=top_n, capital=capital)
    _emit(95, f"榜单 {len(picks)} 只")

    if not dry_run:
        conn = open_db(db_path)
        try:
            upsert_cb_factors_rows(conn, trade_date, [
                {"code": c, **{k: v for k, v in f.items() if k in (
                    "bond_floor", "ytm", "year_left", "redeem_dist", "put_dist",
                    "adj_count", "redeem_status")}}
                for c, f in factors.items()
            ])
            replace_cb_picks(conn, trade_date, picks)
        finally:
            conn.close()

    return {
        "ok": True,
        "source": source,
        "universe": len(universe),
        "candidates": len(candidates),
        "dropped": dropped,
        "picks": picks,
        "warning": "" if source == "live" else "主源失败，本次使用缓存快照",
    }
```

- [ ] **Step 4: 运行测试确认通过（CLI 部分仍失败）**

Run: `uv run pytest tests/test_cli_convertible.py -v`
Expected: 4 个 parser 相关测试可能通过，`_parse_weights` 相关 FAIL

- [ ] **Step 5: 添加 CLI 子命令与 handler**

修改 `cli_layer.py`。

**5a. 在 `build_parser()` 中新增子命令** — 插在 `si.add_argument("--backoff", type=float, default=1.0)` 之后、`return parser` 之前：

```python
    cbp = subparsers.add_parser(
        "cb-picks",
        help="可转债策略：多因子打分生成每日转债榜单（债底/股性/条款三组）",
    )
    cbp.add_argument("--top", type=int, default=20)
    cbp.add_argument("--db", type=str, default="hs300.db")
    cbp.add_argument("--capital", type=float, default=100000.0)
    cbp.add_argument("--weights", type=str, default=None,
                     help="组权重覆盖，如 a=0.5,b=0.25,c=0.25")
    cbp.add_argument("--min-premium", type=float, default=None)
    cbp.add_argument("--max-premium", type=float, default=None)
    cbp.add_argument("--concurrency", type=int, default=4)
    cbp.add_argument("--rate", type=float, default=6.0)
    cbp.add_argument("--dry-run", action="store_true")

    cba = subparsers.add_parser("cb-alerts", help="可转债持仓退出预警（强赎/止损/回售/止盈）")
    cba.add_argument("--db", type=str, default="hs300.db")
    cba.add_argument("--json", action="store_true")

    cbadd = subparsers.add_parser("cb-add", help="登记可转债持仓")
    cbadd.add_argument("code", type=str)
    cbadd.add_argument("--shares", type=int, required=True)
    cbadd.add_argument("--price", type=float, required=True)
    cbadd.add_argument("--date", type=str, default=None)
    cbadd.add_argument("--db", type=str, default="hs300.db")
    cbadd.add_argument("--note", type=str, default="")

    cbpos = subparsers.add_parser("cb-positions", help="查看可转债持仓")
    cbpos.add_argument("--db", type=str, default="hs300.db")
```

**5b. 在 `run_cli()` 的分发块中新增分支** — 插在 `elif args.command == "sync-industry":` 之后、`else:` 之前：

```python
    elif args.command == "cb-picks":
        _run_cb_picks(args)
    elif args.command == "cb-alerts":
        _run_cb_alerts(args)
    elif args.command == "cb-add":
        _run_cb_add(args)
    elif args.command == "cb-positions":
        _run_cb_positions(args)
```

**5c. 在文件末尾追加 handler 与工具函数**：

```python
def _parse_weights(spec: Optional[str]) -> Optional[Dict[str, float]]:
    """解析 'a=0.5,b=0.25,c=0.25' → {'a':0.5,'b':0.25,'c':0.25}。"""
    if not spec:
        return None
    out: Dict[str, float] = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "=" not in part:
            raise ValueError(f"invalid weight spec: {part!r}")
        k, v = part.split("=", 1)
        k = k.strip().lower()
        if k not in ("a", "b", "c"):
            raise ValueError(f"unknown weight group: {k!r}")
        out[k] = float(v)
    if not out:
        raise ValueError("empty weight spec")
    return out


def _run_cb_picks(args) -> None:
    from cb_pipeline import build_cb_picks

    def _progress(pct: int, msg: str) -> None:
        print(f"[{pct:3d}%] {msg}")

    weights = _parse_weights(args.weights)
    premium_range = None
    if args.min_premium is not None or args.max_premium is not None:
        from config import CB_FILTERS as _CBF
        premium_range = (
            args.min_premium if args.min_premium is not None else _CBF["premium_min"],
            args.max_premium if args.max_premium is not None else _CBF["premium_max"],
        )
    result = build_cb_picks(
        args.db,
        top_n=args.top,
        capital=args.capital,
        weights=weights,
        concurrency=args.concurrency,
        rate=args.rate,
        premium_range=premium_range,
        dry_run=args.dry_run,
        progress=_progress,
    )
    if result.get("warning"):
        print(f"[warn] {result['warning']}")
    if not result["ok"]:
        print("[error] 无可用转债数据")
        return

    picks = result["picks"]
    header = (f"# 转债榜单 {dt.date.today().isoformat()}  "
              f"(全市场 {result['universe']} 只, 硬剔除 {len(result['dropped'])} 只, "
              f"榜单 {len(picks)} 只)")
    print(header)
    headers = ["排名", "代码", "名称", "现价", "转股价", "溢价率", "强赎距",
               "总分", "A", "B", "C", "建议"]
    rows = []
    for p in picks:
        rows.append([
            str(p["rank"]), p["code"], p["name"], f"{p['price']:.2f}",
            "-", "-", "-",
            f"{p['total_score']:.1f}",
            f"{p['a_score']:.0f}" if p["a_score"] is not None else "-",
            f"{p['b_score']:.0f}" if p["b_score"] is not None else "-",
            f"{p['c_score']:.0f}" if p["c_score"] is not None else "-",
            f"买 {p['shares']}张",
        ])
    print(format_table(headers, rows))
    for p in picks:
        bits = [f"  {p['rank']}. {p['code']} {p['name']}"]
        if p.get("reason"):
            bits.append(f"     理由: {p['reason']}")
        if p.get("risk_note"):
            bits.append(f"     风险: {p['risk_note']}")
        print("\n".join(bits))


def _run_cb_alerts(args) -> None:
    import json as _json

    from cb_data_provider import fetch_redeem_status_map, fetch_universe_snapshot
    from cb_repository import get_cb_positions
    from db_repository import open_db
    from strategies.convertible import evaluate_alerts

    conn = open_db(args.db)
    try:
        positions = get_cb_positions(conn)
    finally:
        conn.close()
    if not positions:
        msg = "当前无转债持仓"
        print(_json.dumps({"alerts": []}, ensure_ascii=False) if args.json else msg)
        return

    universe = {r["code"]: r for r in fetch_universe_snapshot()}
    redeem_map = fetch_redeem_status_map()

    quotes: Dict[str, Dict[str, Any]] = {}
    for code in {p["code"] for p in positions}:
        u = universe.get(code) or {}
        rd = redeem_map.get(code) or {}
        cp = u.get("convert_price")
        sp = u.get("stock_price")
        trig = rd.get("redeem_price")
        redeem_dist = None
        if trig and sp and sp > 0:
            redeem_dist = round((trig / sp - 1.0) * 100.0, 4)
        quotes[code] = {
            "price": u.get("price"),
            "redeem_status": rd.get("redeem_status") or "",
            "redeem_dist": redeem_dist,
            "premium_rate": u.get("premium_rate"),
            "stock_price": sp,
            "convert_price": cp,
        }

    alerts = evaluate_alerts(positions, quotes)
    if args.json:
        print(_json.dumps({"alerts": alerts}, ensure_ascii=False, indent=2))
        return
    if not alerts:
        print("无预警：持仓全部正常")
        return
    headers = ["级别", "代码", "名称", "现价", "持仓", "提示"]
    rows = [[a["level_label"], a["code"], a["name"],
             f"{a['price']:.2f}" if a["price"] is not None else "-",
             f"{a['shares']}张", a["message"]] for a in alerts]
    print(format_table(headers, rows))


def _run_cb_add(args) -> None:
    from cb_data_provider import fetch_bond_detail
    from cb_repository import upsert_cb_position
    from db_repository import open_db
    from config import CB_RISK

    entry_date = args.date or dt.date.today().isoformat()
    detail = fetch_bond_detail(args.code)
    price = args.price
    stop = round(price * (1 - CB_RISK["stop_loss_pct"]), 4)
    tp = round(price * (1 + CB_RISK["take_profit_pct"]), 4)
    conn = open_db(args.db)
    try:
        upsert_cb_position(conn, {
            "code": args.code,
            "name": detail.get("name") or args.code,
            "stock_code": "",
            "entry_date": entry_date,
            "entry_price": price,
            "shares": args.shares,
            "stop_price": stop,
            "tp_price": tp,
            "note": args.note,
        })
    finally:
        conn.close()
    print(f"已登记持仓 {args.code} {detail.get('name') or ''} "
          f"{args.shares}张 @ {price:.2f} 止损 {stop:.2f} 止盈 {tp:.2f}")


def _run_cb_positions(args) -> None:
    from cb_repository import get_cb_positions
    from db_repository import open_db

    conn = open_db(args.db)
    try:
        positions = get_cb_positions(conn)
    finally:
        conn.close()
    if not positions:
        print("当前无转债持仓")
        return
    headers = ["代码", "名称", "建仓日", "成本", "张数", "止损", "止盈", "备注"]
    rows = [[p["code"], p["name"] or "", p["entry_date"] or "",
             f"{p['entry_price']:.2f}" if p["entry_price"] else "-",
             str(p["shares"]),
             f"{p['stop_price']:.2f}" if p["stop_price"] else "-",
             f"{p['tp_price']:.2f}" if p["tp_price"] else "-",
             p["note"] or ""] for p in positions]
    print(format_table(headers, rows))
```

**5d. 检查并补齐 `cli_layer.py` 顶部 import**

当前 `cli_layer.py` 第 1-3 行是：

```python
import argparse
from typing import Dict, List, Optional
```

改为：

```python
import argparse
import datetime as dt
from typing import Any, Dict, List, Optional
```

（`dt` 供 `_run_cb_picks` / `_run_cb_add` 用；`Any` 供 `_run_cb_alerts` 的类型标注用。）

- [ ] **Step 6: 运行 CLI 测试确认通过**

Run: `uv run pytest tests/test_cli_convertible.py -v`
Expected: PASS（6 passed）

- [ ] **Step 7: 提交**

```bash
git add cb_pipeline.py cli_layer.py tests/test_cli_convertible.py
git commit -m "feat(cb): pipeline 编排 + 4 个 CLI 子命令（cb-picks/cb-alerts/cb-add/cb-positions）"
```

---

## Task 8: 端到端验证 + 文档

**Files:**
- Modify: `README.md`
- Modify: `CLAUDE.md`

- [ ] **Step 1: 跑全量测试确认无回归**

Run: `uv run pytest tests/ -q`
Expected: 全部 PASS（原有测试 + 新增测试，无失败）

- [ ] **Step 2: 真实数据跑通 cb-picks**

Run: `uv run a-finder cb-picks --top 10 --db hs300.db`
Expected: 打印 `# 转债榜单 2026-10-24 (全市场 N 只, 硬剔除 M 只, 榜单 K 只)` 与表格。

**若失败**：东财/新浪源可能被限流。等待 60s 后重试；若 `bond_zh_cov` 持续失败，命令应打印 `[warn] 主源失败，本次使用缓存快照` 而非崩溃。

- [ ] **Step 3: 验证 dry-run 不写库**

Run: `uv run a-finder cb-picks --top 5 --dry-run --db hs300.db`
Expected: 打印榜单，且 `cb_picks` 表行数不变。

Run: `uv run python -c "from db_repository import open_db; c=open_db('hs300.db'); print(c.execute('SELECT COUNT(*) FROM cb_picks').fetchone()[0])"`
Expected: 与执行前一致。

- [ ] **Step 4: 验证权重覆盖生效**

Run: `uv run a-finder cb-picks --top 5 --weights a=0.5,b=0.25,c=0.25 --dry-run --db hs300.db`
Expected: 正常输出，无异常。分数与默认权重不同。

- [ ] **Step 5: 验证持仓登记与预警**

```bash
uv run a-finder cb-add 113059 --shares 50 --price 115.0 --db hs300.db
uv run a-finder cb-positions --db hs300.db
uv run a-finder cb-alerts --db hs300.db
```

Expected:
- `cb-add` 打印 `已登记持仓 113059 ... 50张 @ 115.00 止损 105.80 止盈 138.00`
- `cb-positions` 打印持仓表格
- `cb-alerts` 打印预警表格（若无异常则 `无预警：持仓全部正常`）

- [ ] **Step 6: 验证 JSON 输出**

Run: `uv run a-finder cb-alerts --json --db hs300.db`
Expected: 输出合法 JSON，含 `alerts` 键。

Run: `uv run a-finder cb-alerts --json --db hs300.db | python3 -c "import json,sys; d=json.load(sys.stdin); print('JSON OK, alerts:', len(d['alerts']))"`
Expected: `JSON OK, alerts: 0` 或正数。

- [ ] **Step 7: 清理测试持仓**

Run: `uv run python -c "from db_repository import open_db; c=open_db('hs300.db'); c.execute(\"DELETE FROM cb_positions WHERE code='113059'\"); c.commit(); print('cleaned')"`
Expected: `cleaned`

- [ ] **Step 8: 更新 README**

在 README.md 的「常用命令」章节后追加：

```markdown
### 可转债策略（独立转债仓）

```bash
# 每日榜单（债底40% / 股性30% / 条款30% 三组因子加权）
uv run a-finder cb-picks --top 20
uv run a-finder cb-picks --top 20 --dry-run                  # 只打印不落库
uv run a-finder cb-picks --top 20 --weights a=0.5,b=0.25,c=0.25

# 持仓与退出预警
uv run a-finder cb-add 113059 --shares 50 --price 115.0
uv run a-finder cb-positions
uv run a-finder cb-alerts
uv run a-finder cb-alerts --json
```

**因子构成**（每组内等权，缺失因子按剩余因子重新归一化）：

| 组 | 权重 | 因子 |
|---|---|---|
| 债底防守 | 40% | 纯债价值保护、债现价、到期税前收益、信用评级、剩余年限 |
| 股性进攻 | 30% | 转股溢价率、平价度、跟涨弹性、正股20日动量* |
| 条款风险 | 30% | 强赎触发距离、回售触发距离、下修次数 |

\* 正股20日动量第一期无正股历史K线，不参与计分。

**硬性剔除**（风险闸门，命中即出局）：已公告强赎 / 强赎距离<3% / 评级含B·CC·C /
正股ST / 剩余规模<0.5亿 / 转股价值<60 / 债现价∉[80,200]

**风控**：单只上限15%、最多10只、止损-8%、止盈+20%、10张整手。
```

- [ ] **Step 9: 更新 CLAUDE.md**

在 CLAUDE.md 的「Common Commands」章节追加：

```bash
# 可转债策略（独立转债仓，不走股票 plan_builder）
uv run a-finder cb-picks --top 20 [--dry-run] [--weights a=0.5,b=0.25,c=0.25]
uv run a-finder cb-add 113059 --shares 50 --price 115.0
uv run a-finder cb-positions
uv run a-finder cb-alerts [--json]
```

在「Architecture」章节追加：

- **可转债** — `cb_data_provider.py`（akshare 多源降级）、`cb_repository.py`（`cb_` 表读写）、`cb_pipeline.py`（编排）、`strategies/convertible.py`（粗筛/打分/预警）。表前缀 `cb_`，与股票体系完全隔离；转债整手 10 张（面值100元），风控独立于 `risk_manager.py`。
```

（注意上面 markdown 代码块内的嵌套反引号 — 实际写入时确保闭合正确。）

- [ ] **Step 10: 提交**

```bash
git add README.md CLAUDE.md
git commit -m "docs(cb): 可转债策略使用说明与架构说明"
```

---

## 自审检查

**Spec 覆盖**：

| Spec 章节 | 覆盖任务 |
|---|---|
| 二、数据源探明结论 | Task 3（实现中以注释固化） |
| 三、整体架构 | Task 1-3 + Task 7 |
| 四、隔离原则 | Task 1（`config.py` 只增不改）、Task 2（`cb_` 前缀表） |
| 五、Schema 迁移 | Task 1 |
| 六、数据源降级链 | Task 3 + Task 7（`collect_universe` 缓存兜底） |
| 七、Pipeline 粗筛→补全 | Task 4（`prefilter`）+ Task 5（`build_factor_map`）+ Task 7（`build_cb_picks`） |
| 八、因子与打分模型 | Task 4（10 因子 / 3 组 / 归一化 / 最低因子门槛） |
| 九、硬性剔除 | Task 4（`apply_hard_exclusions`） |
| 十、仓位与风控 | Task 5（`size_shares_cb` / `build_pick_rows`） |
| 十一、退出预警 | Task 6（`evaluate_alerts`） |
| 十二、CLI 接口 | Task 7（4 个子命令） |
| 十三、测试策略 | Task 1-7（16+ 个测试） |
| 十四、实施顺序 | Task 1→8 |
| 十五、风险与开放问题 | Task 3/5（字段容错、ytm 估算、adj_count 中性） |

**已知与 spec 的偏差**（实施时按本计划为准）：

1. Spec 的 `cb_picks` 有 `factor_counts` 列，本计划已包含。
2. Spec 提到 CLI `--min-premium` / `--max-premium`，本计划在 Task 7 加了参数但**未在 pipeline 中接线**。实施时应在 `build_cb_picks` 里加 `premium_range` 覆盖，或从 CLI 中移除该参数。**建议在 `build_cb_picks` 中接线**，见 Task 7 Step 3 的补充说明。
3. Spec 的 `cb_factors` 有 `factor_json` 列，本计划已包含（存 `factor_json` 字符串）。
4. Spec 提到 `elasticity` 因子「由溢价率倒推」，本计划实现为「当日涨跌幅比，不可用时为 None（不计分）」——因为 `bond_zh_cov()` 不含涨跌幅字段，实际不可得。**行为差异**：B 组第一期为 2-3 因子而非 3-4 因子。需在 README 注明。
