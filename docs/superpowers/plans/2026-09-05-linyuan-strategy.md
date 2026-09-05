# 林园选股策略 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增「林园」选股策略 — 财务质量主导，过滤医药 / 中药 / 食品饮料 / 高端制造行业，要求连续 5 年毛利率 > 40% 且扣非 ROE > 15%。

**Architecture:** 新增 `fundamentals_history` 表存历年财务快照，新增 `sync-fundamentals-history` / `sync-industry` CLI 命令补齐数据底座；新增 `strategies/linyuan_multi_factor.py` 继承 `MultiFactorBase`，过滤两步走（行业白名单 + 5 年连续性）；CLI 暴露 `linyuan-picks --top 20`。

**Tech Stack:** Python 3.11+、sqlite3、akshare（已有）、pytest、TDD。

**Spec:** `docs/superpowers/specs/2026-09-05-linyuan-strategy-design.md`

---

## Global Constraints

- 不引入新依赖（akshare / pandas / sqlite 已有）。
- 数据迁移一律走 `db/migrations/YYYY_MM_DD_*.sql`，**不**改 `db_schema.py`。
- 单测 mock 所有网络调用（akshare）。
- 单测 `stock_financial_abstract` 列名格式约定为 `YYYYMMDD`（如 `20241231`）；季报列类似 `20240930`，正则只匹配 `12\d{2}$`。
- `fundamentals_history` 缺失年份留 NULL，不要写 0（避免「连续 5 年」误判）。
- `fundamentals_history.year` 字段为 INTEGER，公历年（如 2024）。
- 行业白名单在 `LinYuanConfig.industries` 中以元组形式保存，默认 6 个 SW 一级：医药生物 / 中药 / 食品饮料 / 机械设备 / 电力设备 / 汽车整车。
- 默认阈值 `gross_margin_min = 0.40`（40%），`roe_excl_min = 0.15`（15%），`continuity_years = 5`。

---

## File Structure

新文件：
- `db/migrations/2026_09_05_fundamentals_history.sql` — 新表 + 索引
- `tests/test_linyuan_strategy.py` — 全部新增测试
- `tests/test_fundamentals_history_sync.py` — sync 集成测试（mock akshare）
- `tests/test_industry_sync.py` — 行业同步测试（mock akshare）
- `strategies/linyuan_multi_factor.py` — 策略模块
- `docs/superpowers/plans/2026-09-05-linyuan-strategy.md` — 本文件

修改：
- `akshare_data_provider.py` — 新增 `_annual_metrics` / `fetch_fundamentals_history_akshare` / `fetch_industry_akshare`；`_abstract_metric` 加 `col=` 参数
- `db_repository.py` — 新增 `FundamentalsHistoryRow` / `upsert_fundamentals_history` / `get_fundamentals_history_by_code`
- `sync_service.py` — 新增 `sync_fundamentals_history` / `sync_industry`
- `strategies/__init__.py` — 新增 `MULTI_FACTOR_STRATEGIES = {"linyuan": LinYuanMultiFactor}`
- `cli_layer.py` — 新增 `linyuan-picks` / `sync-fundamentals-history` / `sync-industry` 子命令
- `README.md` — 常用命令 + 林园策略章节

---

## Task 1: 添加 fundamentals_history 表迁移

**Files:**
- Create: `db/migrations/2026_09_05_fundamentals_history.sql`
- Create: `tests/test_migration_fundamentals_history.py`

**Interfaces:**
- Consumes: none
- Produces: 跑迁移后 `fundamentals_history(code, year, gross_margin, roe_excl, revenue, net_profit_excl, report_date, synced_at)` 表存在；索引 `idx_fundamentals_history_code` 存在

- [ ] **Step 1: 写 SQL 迁移文件**

```sql
-- db/migrations/2026_09_05_fundamentals_history.sql
CREATE TABLE IF NOT EXISTS fundamentals_history (
    code TEXT NOT NULL,
    year INTEGER NOT NULL,
    gross_margin REAL,
    roe_excl REAL,
    revenue REAL,
    net_profit_excl REAL,
    report_date TEXT,
    synced_at TEXT,
    PRIMARY KEY (code, year)
);
CREATE INDEX IF NOT EXISTS idx_fundamentals_history_code
    ON fundamentals_history(code);
```

- [ ] **Step 2: 写失败测试**

```python
# tests/test_migration_fundamentals_history.py
import sqlite3
from db_repository import open_db


def test_fundamentals_history_table_exists(tmp_path):
    db = str(tmp_path / "m.db")
    conn = open_db(db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(fundamentals_history)").fetchall()]
    conn.close()
    assert "code" in cols
    assert "year" in cols
    assert "gross_margin" in cols
    assert "roe_excl" in cols


def test_fundamentals_history_pk_on_code_year(tmp_path):
    db = str(tmp_path / "m.db")
    conn = open_db(db)
    pk_cols = [
        r[1] for r in conn.execute("PRAGMA table_info(fundamentals_history)").fetchall()
        if r[5] > 0
    ]
    conn.close()
    assert pk_cols == ["code", "year"]


def test_fundamentals_history_index_exists(tmp_path):
    db = str(tmp_path / "m.db")
    conn = open_db(db)
    idx_names = [r[1] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='fundamentals_history'"
    ).fetchall()]
    conn.close()
    assert "idx_fundamentals_history_code" in idx_names
```

- [ ] **Step 3: 运行测试**

Run: `.venv/bin/python -m pytest tests/test_migration_fundamentals_history.py -v`
Expected: 3 failed（表不存在 → sqlite 报错）

- [ ] **Step 4: 创建迁移 SQL 文件（已在 Step 1）**

- [ ] **Step 5: 重跑测试**

Run: `.venv/bin/python -m pytest tests/test_migration_fundamentals_history.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add db/migrations/2026_09_05_fundamentals_history.sql tests/test_migration_fundamentals_history.py
git commit -m "feat(db): add fundamentals_history table migration"
```

---

## Task 2: `_abstract_metric` 支持 `col=` 参数

**Files:**
- Modify: `akshare_data_provider.py:80-92`

**Interfaces:**
- Consumes: existing callers `fetch_fundamentals_akshare`
- Produces: `_abstract_metric(df, name, col=None) -> Optional[List[float]]`；`col=None` 行为不变；`col=str` 时只返回该列的值（单值列表或 NaN 列表）

- [ ] **Step 1: 写失败测试**

在 `tests/test_akshare_helpers.py` 新增（如果文件不存在则创建）：

```python
# tests/test_akshare_helpers.py
import pandas as pd
import pytest
from akshare_data_provider import _abstract_metric


def _make_df():
    return pd.DataFrame({
        "指标": ["毛利率", "毛利率"],
        "单位": ["%", "%"],
        "20241231": [42.5, 40.0],
        "20240630": [20.0, 18.0],
    })


def test_abstract_metric_returns_all_columns_by_default():
    df = _make_df()
    out = _abstract_metric(df, "毛利率")
    assert out == [42.5, 40.0, 20.0, 18.0]


def test_abstract_metric_filters_by_col():
    df = _make_df()
    out = _abstract_metric(df, "毛利率", col="20241231")
    assert out == [42.5, 40.0]


def test_abstract_metric_missing_name_returns_none():
    df = _make_df()
    assert _abstract_metric(df, "不存在") is None
```

- [ ] **Step 2: 运行测试**

Run: `.venv/bin/python -m pytest tests/test_akshare_helpers.py -v`
Expected: 1 passed（默认行为）+ 2 failed（col 参数未实现 / 缺失 name 行为未确定）

- [ ] **Step 3: 改实现**

```python
# akshare_data_provider.py
def _abstract_metric(df, name: str, col: Optional[str] = None) -> Optional[List[float]]:
    """取 stock_financial_abstract 中指定指标行的全部（或指定列）报告期数值。"""
    matched = df[df["指标"] == name]
    if matched.empty:
        return None
    row = matched.iloc[0]
    if col is not None:
        try:
            return [float(row[col])]
        except (TypeError, ValueError):
            return [float("nan")]
    values = []
    for c in df.columns[2:]:
        try:
            values.append(float(row[c]))
        except (TypeError, ValueError):
            values.append(float("nan"))
    return values
```

记得 import `Optional`。

- [ ] **Step 4: 重跑测试**

Run: `.venv/bin/python -m pytest tests/test_akshare_helpers.py -v`
Expected: 3 passed

- [ ] **Step 5: 跑全量回归**

Run: `.venv/bin/python -m pytest`
Expected: 全量 ≥ 当前 passed

- [ ] **Step 6: Commit**

```bash
git add akshare_data_provider.py tests/test_akshare_helpers.py
git commit -m "refactor(akshare): add col= kwarg to _abstract_metric"
```

---

## Task 3: `_annual_metrics` 抽年报

**Files:**
- Modify: `akshare_data_provider.py`（紧接 `_abstract_metric` 之后）

**Interfaces:**
- Consumes: `pd.DataFrame` from `ak.stock_financial_abstract`
- Produces: `Dict[int, dict]` 形如 `{2024: {"gross_margin": 42.5, "roe_excl": 18.0}, ...}`，只取列名以 `1231` 结尾的年报列

- [ ] **Step 1: 写失败测试**

在 `tests/test_akshare_helpers.py` 末尾添加：

```python
from akshare_data_provider import _annual_metrics


def test_annual_metrics_only_keeps_december_columns():
    df = pd.DataFrame({
        "指标": ["毛利率"],
        "单位": ["%"],
        "20241231": [42.5],
        "20240930": [20.0],
        "20231231": [40.0],
        "20230930": [18.0],
    })
    out = _annual_metrics(df)
    assert set(out.keys()) == {2024, 2023}
    assert out[2024]["gross_margin"] == pytest.approx(42.5)


def test_annual_metrics_extracts_gross_margin_and_roe_excl():
    df = pd.DataFrame({
        "指标": ["毛利率", "净资产收益率(扣非)"],
        "单位": ["%", "%"],
        "20241231": [45.0, 18.0],
        "20231231": [43.0, 17.0],
    })
    out = _annual_metrics(df)
    assert out[2024]["gross_margin"] == pytest.approx(45.0)
    assert out[2024]["roe_excl"] == pytest.approx(18.0)
    assert out[2023]["roe_excl"] == pytest.approx(17.0)


def test_annual_metrics_empty_or_invalid_returns_empty():
    assert _annual_metrics(None) == {}
    assert _annual_metrics(pd.DataFrame()) == {}
```

- [ ] **Step 2: 运行测试 → 失败（_annual_metrics not defined）**

Run: `.venv/bin/python -m pytest tests/test_akshare_helpers.py::test_annual_metrics_only_keeps_december_columns -v`
Expected: ImportError or NameError

- [ ] **Step 3: 实现**

```python
# akshare_data_provider.py
import re

def _annual_metrics(df) -> Dict[int, dict]:
    """从 stock_financial_abstract DataFrame 抽年报关键指标。列名格式：YYYYMMDD。"""
    out: Dict[int, dict] = {}
    if df is None or df.empty:
        return out
    annual_cols = [c for c in df.columns[2:] if re.match(r"^\d{4}1231$", str(c))]
    for col in annual_cols:
        year = int(str(col)[:4])
        out[year] = {
            "gross_margin": _first_valid(_abstract_metric(df, "毛利率", col=col) or []),
            "roe_excl": _first_valid(_abstract_metric(df, "净资产收益率(扣非)", col=col) or []),
        }
    return out
```

记得 `from typing import Dict`。

- [ ] **Step 4: 重跑测试**

Run: `.venv/bin/python -m pytest tests/test_akshare_helpers.py -v`
Expected: 6 passed（含 Task 2 的 3 个）

- [ ] **Step 5: Commit**

```bash
git add akshare_data_provider.py tests/test_akshare_helpers.py
git commit -m "feat(akshare): add _annual_metrics helper for yearly fundamentals"
```

---

## Task 4: `FundamentalsHistoryRow` + `upsert_fundamentals_history` + `get_fundamentals_history_by_code`

**Files:**
- Modify: `db_repository.py`

**Interfaces:**
- Produces:
  - `@dataclass class FundamentalsHistoryRow(code, year, gross_margin, roe_excl, revenue, net_profit_excl, report_date, synced_at)`
  - `def upsert_fundamentals_history(conn, rows: Iterable[FundamentalsHistoryRow]) -> int`
  - `def get_fundamentals_history_by_code(conn, code) -> List[FundamentalsHistoryRow]` 按 `year DESC` 返回

- [ ] **Step 1: 写失败测试**

新增 `tests/test_db_repository_fundamentals_history.py`：

```python
import pytest
from datetime import datetime
from db_repository import (
    open_db,
    upsert_fundamentals_history,
    get_fundamentals_history_by_code,
    FundamentalsHistoryRow,
)


@pytest.fixture()
def db(tmp_path):
    p = str(tmp_path / "h.db")
    open_db(p).close()
    import sqlite3
    return sqlite3.connect(p)


def _row(code, year, gm=42.0, roe=18.0):
    return FundamentalsHistoryRow(
        code=code, year=year, gross_margin=gm, roe_excl=roe,
        revenue=None, net_profit_excl=None,
        report_date=f"{year}-12-31", synced_at=datetime.now().isoformat(),
    )


def test_upsert_inserts_and_updates(db):
    conn = db
    upsert_fundamentals_history(conn, [_row("600519", 2024, gm=42.0)])
    rows = get_fundamentals_history_by_code(conn, "600519")
    assert len(rows) == 1
    assert rows[0].gross_margin == pytest.approx(42.0)

    # update same (code, year)
    upsert_fundamentals_history(conn, [_row("600519", 2024, gm=43.5)])
    rows = get_fundamentals_history_by_code(conn, "600519")
    assert len(rows) == 1
    assert rows[0].gross_margin == pytest.approx(43.5)


def test_get_returns_sorted_by_year_desc(db):
    upsert_fundamentals_history(db, [
        _row("600519", 2022),
        _row("600519", 2024),
        _row("600519", 2023),
    ])
    rows = get_fundamentals_history_by_code(db, "600519")
    assert [r.year for r in rows] == [2024, 2023, 2022]


def test_get_filters_by_code(db):
    upsert_fundamentals_history(db, [
        _row("600519", 2024),
        _row("000001", 2024),
    ])
    rows = get_fundamentals_history_by_code(db, "600519")
    assert len(rows) == 1
    assert rows[0].code == "600519"
```

- [ ] **Step 2: 跑测试 → 失败**

Run: `.venv/bin/python -m pytest tests/test_db_repository_fundamentals_history.py -v`
Expected: ImportError（FundamentalsHistoryRow 不存在）

- [ ] **Step 3: 实现 dataclass + repository 函数**

在 `db_repository.py` `FundamentalsRow` 类附近插入：

```python
@dataclass
class FundamentalsHistoryRow:
    code: str
    year: int
    gross_margin: float = 0.0
    roe_excl: float = 0.0
    revenue: Optional[float] = None
    net_profit_excl: Optional[float] = None
    report_date: str = ""
    synced_at: str = ""
```

（在文件顶部加 `from typing import Optional` 如果还没导入）

然后在 `upsert_fundamentals` 之后插入：

```python
def upsert_fundamentals_history(
    conn: sqlite3.Connection,
    rows: Iterable[FundamentalsHistoryRow],
) -> int:
    data = [
        (r.code, r.year, r.gross_margin, r.roe_excl,
         r.revenue, r.net_profit_excl, r.report_date, r.synced_at)
        for r in rows
    ]
    if not data:
        return 0
    conn.executemany(
        """
        INSERT INTO fundamentals_history
            (code, year, gross_margin, roe_excl, revenue, net_profit_excl, report_date, synced_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(code, year) DO UPDATE SET
            gross_margin=excluded.gross_margin,
            roe_excl=excluded.roe_excl,
            revenue=excluded.revenue,
            net_profit_excl=excluded.net_profit_excl,
            report_date=excluded.report_date,
            synced_at=excluded.synced_at
        """,
        data,
    )
    return len(data)


def get_fundamentals_history_by_code(
    conn: sqlite3.Connection, code: str
) -> List[FundamentalsHistoryRow]:
    cur = conn.execute(
        "SELECT code, year, gross_margin, roe_excl, revenue, net_profit_excl, "
        "report_date, synced_at FROM fundamentals_history WHERE code = ? ORDER BY year DESC",
        (code,),
    )
    return [FundamentalsHistoryRow(*r) for r in cur.fetchall()]
```

- [ ] **Step 4: 重跑测试**

Run: `.venv/bin/python -m pytest tests/test_db_repository_fundamentals_history.py -v`
Expected: 3 passed

- [ ] **Step 5: Commit**

```bash
git add db_repository.py tests/test_db_repository_fundamentals_history.py
git commit -m "feat(db): FundamentalsHistoryRow + upsert/get helpers"
```

---

## Task 5: `fetch_fundamentals_history_akshare`

**Files:**
- Modify: `akshare_data_provider.py`

**Interfaces:**
- Produces: `def fetch_fundamentals_history_akshare(code: str) -> List[FundamentalsHistoryRow]`

- [ ] **Step 1: 写失败测试**

在 `tests/test_akshare_helpers.py` 末尾：

```python
from datetime import datetime
from unittest.mock import patch
import pandas as pd
from akshare_data_provider import fetch_fundamentals_history_akshare


def _yearly_df():
    return pd.DataFrame({
        "指标": ["毛利率", "净资产收益率(扣非)"],
        "单位": ["%", "%"],
        "20241231": [42.5, 18.0],
        "20231231": [41.0, 17.5],
        "20221231": [40.5, 16.0],
        "20240930": [22.0, 9.0],
    })


def test_fetch_returns_one_row_per_annual_report():
    with patch("akshare_data_provider.ak") as mock_ak:
        mock_ak.stock_financial_abstract.return_value = _yearly_df()
        rows = fetch_fundamentals_history_akshare("600519")
    assert len(rows) == 3
    years = sorted(r.year for r in rows)
    assert years == [2022, 2023, 2024]


def test_fetch_returns_empty_on_exception():
    with patch("akshare_data_provider.ak") as mock_ak:
        mock_ak.stock_financial_abstract.side_effect = RuntimeError("net")
        rows = fetch_fundamentals_history_akshare("600519")
    assert rows == []


def test_fetch_returns_empty_on_empty_dataframe():
    with patch("akshare_data_provider.ak") as mock_ak:
        mock_ak.stock_financial_abstract.return_value = pd.DataFrame()
        rows = fetch_fundamentals_history_akshare("600519")
    assert rows == []
```

- [ ] **Step 2: 跑测试 → 失败**

Run: `.venv/bin/python -m pytest tests/test_akshare_helpers.py::test_fetch_returns_one_row_per_annual_report -v`
Expected: ImportError

- [ ] **Step 3: 实现**

```python
# akshare_data_provider.py
def fetch_fundamentals_history_akshare(code: str) -> List[FundamentalsHistoryRow]:
    """单只股票拉历年财报关键指标（年报列 1231）。失败返回 []。"""
    try:
        df = ak.stock_financial_abstract(symbol=code)
    except Exception:
        return []
    metrics_by_year = _annual_metrics(df)
    now = datetime.now().isoformat(timespec="seconds")
    return [
        FundamentalsHistoryRow(
            code=code, year=y,
            gross_margin=m["gross_margin"], roe_excl=m["roe_excl"],
            revenue=None, net_profit_excl=None,
            report_date=f"{y}-12-31", synced_at=now,
        )
        for y, m in sorted(metrics_by_year.items(), reverse=True)[:7]
    ]
```

需要 `from datetime import datetime` 和 `from db_repository import FundamentalsHistoryRow`。

注意：循环顶部 import 会引发循环依赖（`db_repository` import `akshare_data_provider` 不应该）。改为函数内 lazy import：

```python
def fetch_fundamentals_history_akshare(code: str) -> List[FundamentalsHistoryRow]:
    from db_repository import FundamentalsHistoryRow
    try:
        df = ak.stock_financial_abstract(symbol=code)
    except Exception:
        return []
    ...
```

如果 db_repository 顶部 import akshare_data_provider，会出现循环。检查：若 `db_repository.py` 没 import `akshare_data_provider.py`，则文件顶部 import 安全。先确认；如果有，改为函数内 lazy import。

- [ ] **Step 4: 重跑测试**

Run: `.venv/bin/python -m pytest tests/test_akshare_helpers.py -v`
Expected: 9 passed（含 Task 2/3 + 本 Task 3 个）

- [ ] **Step 5: 跑全量回归**

Run: `.venv/bin/python -m pytest`
Expected: 全量 ≥ 当前 passed

- [ ] **Step 6: Commit**

```bash
git add akshare_data_provider.py tests/test_akshare_helpers.py
git commit -m "feat(akshare): fetch_fundamentals_history_akshare"
```

---

## Task 6: `fetch_industry_akshare`

**Files:**
- Modify: `akshare_data_provider.py`

**Interfaces:**
- Produces: `def fetch_industry_akshare(code: str) -> str` — 返回 `ak.stock_individual_info_em` 中的「行业」字段；失败或字段缺失返回 ""

- [ ] **Step 1: 写失败测试**

在 `tests/test_akshare_helpers.py` 末尾：

```python
from akshare_data_provider import fetch_industry_akshare


def _info_df_with(industry="医药生物"):
    return pd.DataFrame({
        "item": ["股票简称", "行业", "区域"],
        "value": ["贵州茅台", industry, "贵州"],
    })


def test_fetch_industry_returns_industry_field():
    with patch("akshare_data_provider.ak") as mock_ak:
        mock_ak.stock_individual_info_em.return_value = _info_df_with("食品饮料")
    assert fetch_industry_akshare("600519") == "食品饮料"


def test_fetch_industry_returns_empty_on_missing_field():
    with patch("akshare_data_provider.ak") as mock_ak:
        mock_ak.stock_individual_info_em.return_value = pd.DataFrame({
            "item": ["股票简称"], "value": ["X"]
        })
    assert fetch_industry_akshare("600519") == ""


def test_fetch_industry_returns_empty_on_exception():
    with patch("akshare_data_provider.ak") as mock_ak:
        mock_ak.stock_individual_info_em.side_effect = RuntimeError("net")
    assert fetch_industry_akshare("600519") == ""
```

- [ ] **Step 2: 跑测试 → 失败**

Run: `.venv/bin/python -m pytest tests/test_akshare_helpers.py::test_fetch_industry_returns_industry_field -v`
Expected: ImportError

- [ ] **Step 3: 实现**

```python
# akshare_data_provider.py
def fetch_industry_akshare(code: str) -> str:
    """单只股票从 stock_individual_info_em 抽「行业」字段。失败返回 ''。"""
    try:
        df = ak.stock_individual_info_em(symbol=code)
    except Exception:
        return ""
    if df is None or df.empty or "item" not in df.columns or "value" not in df.columns:
        return ""
    matched = df[df["item"] == "行业"]
    if matched.empty:
        return ""
    val = matched.iloc[0]["value"]
    return str(val) if val is not None else ""
```

- [ ] **Step 4: 重跑测试**

Run: `.venv/bin/python -m pytest tests/test_akshare_helpers.py -v`
Expected: 12 passed

- [ ] **Step 5: Commit**

```bash
git add akshare_data_provider.py tests/test_akshare_helpers.py
git commit -m "feat(akshare): fetch_industry_akshare"
```

---

## Task 7: `sync_fundamentals_history` 服务

**Files:**
- Modify: `sync_service.py`（紧接 `sync_fundamentals` 之后）

**Interfaces:**
- Produces: `def sync_fundamentals_history(db_path, *, concurrency=4, rate_limit=3.0, retries=2, backoff=1.0, progress=None) -> Dict[str, int]`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_fundamentals_history_sync.py`：

```python
import sqlite3
from unittest.mock import patch
from db_repository import open_db, get_fundamentals_history_by_code
from sync_service import sync_fundamentals_history


def test_sync_writes_rows_to_db(tmp_path):
    db = str(tmp_path / "s.db")
    conn = open_db(db)
    conn.execute("INSERT INTO hs300_constituents (code, name) VALUES ('600519', '贵州茅台')")
    conn.commit()
    conn.close()

    fake_rows = []  # populated by mock
    from db_repository import FundamentalsHistoryRow
    from datetime import datetime
    for y in [2020, 2021, 2022, 2023, 2024]:
        fake_rows.append(FundamentalsHistoryRow(
            code="600519", year=y, gross_margin=45.0, roe_excl=18.0,
            report_date=f"{y}-12-31", synced_at=datetime.now().isoformat(),
        ))

    with patch("sync_service.fetch_fundamentals_history_akshare", return_value=fake_rows):
        result = sync_fundamentals_history(db, concurrency=1, rate_limit=1000.0)

    assert result["symbols"] == 1
    assert result["rows"] == 5
    rows = get_fundamentals_history_by_code(sqlite3.connect(db), "600519")
    assert len(rows) == 5


def test_sync_skips_when_no_codes(tmp_path):
    db = str(tmp_path / "empty.db")
    open_db(db).close()
    from sync_service import FetchError
    import pytest
    with pytest.raises(FetchError):
        sync_fundamentals_history(db)
```

- [ ] **Step 2: 跑测试 → 失败**

Run: `.venv/bin/python -m pytest tests/test_fundamentals_history_sync.py -v`
Expected: ImportError（sync_fundamentals_history 不存在）

- [ ] **Step 3: 实现**

```python
# sync_service.py
def sync_fundamentals_history(
    db_path: str,
    *,
    concurrency: int = 4,
    rate_limit: float = 3.0,
    retries: int = 2,
    backoff: float = 1.0,
    progress: Optional[Callable[[int, str], None]] = None,
) -> Dict[str, int]:
    """同步历年财务指标到 fundamentals_history。失败单只股票降级日志。"""
    from akshare_data_provider import fetch_fundamentals_history_akshare
    from db_repository import upsert_fundamentals_history

    logger = get_logger()
    conn = open_db(db_path)
    with conn:
        codes = get_all_codes(conn)
    if not codes:
        raise FetchError("无本地股票代码，无法同步财务历史")

    limiter = RateLimiter(rate_limit)
    all_rows = []
    done = 0
    total = len(codes)
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {
            executor.submit(_wrap_retry(fetch_fundamentals_history_akshare, retries, backoff), code): code
            for code in sorted(codes)
        }
        for future in as_completed(futures):
            code = futures[future]
            try:
                rows = future.result() or []
                all_rows.extend(rows)
            except Exception as exc:  # noqa: BLE001
                logger.warning("财务历史同步失败: code=%s error=%s", code, exc)
            done += 1
            if progress:
                progress(int(done * 100 / total), f"财务历史 {code}（{done}/{total}）")

    with conn:
        inserted = upsert_fundamentals_history(conn, all_rows)
    logger.info("财务历史同步完成: symbols=%s rows=%s", total, inserted)
    return {"symbols": total, "rows": inserted}
```

`get_all_codes` / `_wrap_retry` / `RateLimiter` / `FetchError` / `ThreadPoolExecutor` / `as_completed` 已经在文件顶部 import，直接复用。

- [ ] **Step 4: 重跑测试**

Run: `.venv/bin/python -m pytest tests/test_fundamentals_history_sync.py -v`
Expected: 2 passed

- [ ] **Step 5: 跑全量回归**

Run: `.venv/bin/python -m pytest`
Expected: 全量 ≥ 当前 passed

- [ ] **Step 6: Commit**

```bash
git add sync_service.py tests/test_fundamentals_history_sync.py
git commit -m "feat(sync): sync_fundamentals_history service"
```

---

## Task 8: `sync_industry` 服务

**Files:**
- Modify: `sync_service.py`（紧接 `sync_fundamentals_history` 之后）

**Interfaces:**
- Produces: `def sync_industry(db_path, *, concurrency=4, rate_limit=3.0, retries=2, backoff=1.0, progress=None) -> Dict[str, int]`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_industry_sync.py`：

```python
import sqlite3
from unittest.mock import patch
from db_repository import open_db, get_metadata_by_code
from sync_service import sync_industry


def test_sync_writes_industry_to_metadata(tmp_path):
    db = str(tmp_path / "i.db")
    conn = open_db(db)
    conn.execute(
        "INSERT INTO hs300_metadata (code, name, industry, region) VALUES ('600519', '贵州茅台', '', '')"
    )
    conn.commit()
    conn.close()

    with patch("sync_service.fetch_industry_akshare", return_value="食品饮料"):
        result = sync_industry(db, concurrency=1, rate_limit=1000.0)

    assert result["symbols"] == 1
    assert result["rows"] == 1
    meta = get_metadata_by_code(sqlite3.connect(db), "600519")
    assert meta.industry == "食品饮料"


def test_sync_skips_empty_industry(tmp_path):
    db = str(tmp_path / "i2.db")
    conn = open_db(db)
    conn.execute(
        "INSERT INTO hs300_metadata (code, name, industry, region) VALUES ('600519', 'X', 'OLD', '')"
    )
    conn.commit()
    conn.close()

    with patch("sync_service.fetch_industry_akshare", return_value=""):
        result = sync_industry(db, concurrency=1, rate_limit=1000.0)

    # 空行业不覆盖原值
    assert result["rows"] == 0
    meta = get_metadata_by_code(sqlite3.connect(db), "600519")
    assert meta.industry == "OLD"
```

- [ ] **Step 2: 跑测试 → 失败**

Run: `.venv/bin/python -m pytest tests/test_industry_sync.py -v`
Expected: ImportError

- [ ] **Step 3: 实现**

```python
# sync_service.py
def sync_industry(
    db_path: str,
    *,
    concurrency: int = 4,
    rate_limit: float = 3.0,
    retries: int = 2,
    backoff: float = 1.0,
    progress: Optional[Callable[[int, str], None]] = None,
) -> Dict[str, int]:
    """从 akshare 拉单只股票行业回填 hs300_metadata.industry。空字符串不覆盖。"""
    from akshare_data_provider import fetch_industry_akshare
    from db_repository import upsert_metadata, StockMeta

    logger = get_logger()
    conn = open_db(db_path)
    with conn:
        codes = get_all_codes(conn)
        existing = {
            row[0]: row[1]
            for row in conn.execute(
                "SELECT code, COALESCE(name, code) FROM hs300_metadata"
            ).fetchall()
        }
    if not codes:
        raise FetchError("无本地股票代码，无法同步行业")

    limiter = RateLimiter(rate_limit)
    industry_map: Dict[str, str] = {}
    done = 0
    total = len(codes)
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        futures = {
            executor.submit(_wrap_retry(fetch_industry_akshare, retries, backoff), code): code
            for code in sorted(codes)
        }
        for future in as_completed(futures):
            code = futures[future]
            try:
                industry = future.result() or ""
                if industry:
                    industry_map[code] = industry
            except Exception as exc:  # noqa: BLE001
                logger.warning("行业同步失败: code=%s error=%s", code, exc)
            done += 1
            if progress:
                progress(int(done * 100 / total), f"行业 {code}（{done}/{total}）")

    rows = [
        StockMeta(code=c, name=existing.get(c, c), industry=ind, region="")
        for c, ind in industry_map.items()
    ]
    with conn:
        inserted = upsert_metadata(conn, rows)
    logger.info("行业同步完成: symbols=%s rows=%s", total, inserted)
    return {"symbols": total, "rows": inserted}
```

需要 `from db_repository import StockMeta`（在 lazy import 里）。`upsert_metadata` 已经存在。

- [ ] **Step 4: 重跑测试**

Run: `.venv/bin/python -m pytest tests/test_industry_sync.py -v`
Expected: 2 passed

- [ ] **Step 5: 跑全量回归**

Run: `.venv/bin/python -m pytest`
Expected: 全量 ≥ 当前 passed

- [ ] **Step 6: Commit**

```bash
git add sync_service.py tests/test_industry_sync.py
git commit -m "feat(sync): sync_industry service"
```

---

## Task 9: 林园策略纯过滤函数 `_passes_linyuan_filter`

**Files:**
- Create: `strategies/linyuan_multi_factor.py`（仅放配置 dataclass + 纯过滤函数；MultiFactor 子类留 Task 10）

**Interfaces:**
- Produces:
  - `@dataclass class LinYuanConfig(industries, gross_margin_min, roe_excl_min, continuity_years)`
  - `def passes_linyuan_filter(sector: str, history: List[FundamentalsHistoryRow], config: LinYuanConfig) -> bool`

- [ ] **Step 1: 写失败测试**

新建 `tests/test_linyuan_strategy.py`：

```python
import pytest
from db_repository import FundamentalsHistoryRow
from strategies.linyuan_multi_factor import (
    LinYuanConfig, passes_linyuan_filter,
)

CFG = LinYuanConfig()


def _h(gm, roe, years):
    return [FundamentalsHistoryRow(
        code="X", year=y, gross_margin=gm, roe_excl=roe,
        report_date=f"{y}-12-31", synced_at="",
    ) for y in years]


def test_passes_with_5_consecutive_years():
    history = _h(0.50, 0.20, [2020, 2021, 2022, 2023, 2024])
    assert passes_linyuan_filter("医药生物", history, CFG) is True


def test_fails_when_only_4_years():
    history = _h(0.50, 0.20, [2020, 2021, 2022, 2023])
    assert passes_linyuan_filter("医药生物", history, CFG) is False


def test_fails_with_year_gap():
    # 缺 2022
    history = _h(0.50, 0.20, [2020, 2021, 2023, 2024])
    history.append(FundamentalsHistoryRow(
        code="X", year=2022, gross_margin=0, roe_excl=0,
        report_date="2022-12-31", synced_at="",
    ))
    assert passes_linyuan_filter("医药生物", history, CFG) is False


def test_fails_when_margin_below_threshold():
    history = _h(0.39, 0.20, [2020, 2021, 2022, 2023, 2024])
    assert passes_linyuan_filter("医药生物", history, CFG) is False


def test_fails_when_roe_below_threshold():
    history = _h(0.50, 0.14, [2020, 2021, 2022, 2023, 2024])
    assert passes_linyuan_filter("医药生物", history, CFG) is False


def test_fails_when_sector_not_in_whitelist():
    history = _h(0.50, 0.20, [2020, 2021, 2022, 2023, 2024])
    assert passes_linyuan_filter("房地产", history, CFG) is False


@pytest.mark.parametrize("sector", [
    "医药生物", "中药", "食品饮料", "机械设备", "电力设备", "汽车整车",
])
def test_all_whitelisted_sectors_pass(sector):
    history = _h(0.50, 0.20, [2020, 2021, 2022, 2023, 2024])
    assert passes_linyuan_filter(sector, history, CFG) is True
```

- [ ] **Step 2: 跑测试 → 失败**

Run: `.venv/bin/python -m pytest tests/test_linyuan_strategy.py -v`
Expected: ImportError

- [ ] **Step 3: 实现**

```python
# strategies/linyuan_multi_factor.py
from dataclasses import dataclass, field
from typing import List, Tuple

from db_repository import FundamentalsHistoryRow


DEFAULT_INDUSTRIES: Tuple[str, ...] = (
    "医药生物", "中药", "食品饮料",
    "机械设备", "电力设备", "汽车整车",
)


@dataclass
class LinYuanConfig:
    industries: Tuple[str, ...] = DEFAULT_INDUSTRIES
    gross_margin_min: float = 0.40
    roe_excl_min: float = 0.15
    continuity_years: int = 5


def passes_linyuan_filter(
    sector: str,
    history: List[FundamentalsHistoryRow],
    config: LinYuanConfig,
) -> bool:
    """行业白名单 + 连续 N 年 (gross_margin > min ∧ roe_excl > min)。"""
    if sector not in config.industries:
        return False
    if len(history) < config.continuity_years:
        return False
    years_sorted = sorted(history, key=lambda r: r.year, reverse=True)
    # 必须是最近 N 年连续（按 year 排，相邻差 1）
    selected = years_sorted[: config.continuity_years]
    if len(selected) < config.continuity_years:
        return False
    for i in range(len(selected) - 1):
        if selected[i].year - selected[i + 1].year != 1:
            return False
    return all(
        (r.gross_margin or 0) > config.gross_margin_min
        and (r.roe_excl or 0) > config.roe_excl_min
        for r in selected
    )
```

- [ ] **Step 4: 重跑测试**

Run: `.venv/bin/python -m pytest tests/test_linyuan_strategy.py -v`
Expected: 11 passed（1 + 1 + 1 + 1 + 1 + 1 + 6 参数化）

- [ ] **Step 5: Commit**

```bash
git add strategies/linyuan_multi_factor.py tests/test_linyuan_strategy.py
git commit -m "feat(strategies): LinYuanConfig + passes_linyuan_filter"
```

---

## Task 10: `LinYuanMultiFactor` 策略类 + 选股流程

**Files:**
- Modify: `strategies/linyuan_multi_factor.py`
- Modify: `strategies/__init__.py`

**Interfaces:**
- Produces:
  - `class LinYuanMultiFactor(MultiFactorBase)` — 重写 `select(date, candidates)`，从 DB 读 `fundamentals_history` + `hs300_metadata.industry` 后调 `passes_linyuan_filter`
  - `class LinYuanRunner` 简化版：直接接受候选 + DB 连接 → 输出 positions（不依赖 MultiFactorBase 的 z-score 流水线，避免引入 120 天价格数据要求）

权衡：`MultiFactorBase` 要求 `Stock.prices` 至少 120 天数据，林园策略仅需财务，过重。**最终决策**：本任务不引入 MultiFactorBase，新建 `LinYuanRunner` 直接消费候选 + DB，返回 `SelectionResult`。

- [ ] **Step 1: 写失败测试**

在 `tests/test_linyuan_strategy.py` 末尾：

```python
import sqlite3
from db_repository import open_db, upsert_fundamentals_history, upsert_metadata, StockMeta
from strategies.linyuan_multi_factor import LinYuanRunner, LinYuanConfig
from strategies.multi_factor_base import TargetPosition


@pytest.fixture()
def lin_db(tmp_path):
    db = str(tmp_path / "lin.db")
    conn = open_db(db)
    # 元数据
    conn.executemany(
        "INSERT INTO hs300_metadata (code, name, industry, region) VALUES (?, ?, ?, ?)",
        [
            ("600519", "贵州茅台", "食品饮料", "贵州"),
            ("000538", "云南白药", "中药", "云南"),
            ("600276", "恒瑞医药", "医药生物", "江苏"),
            ("000002", "万科A", "房地产", "深圳"),
        ],
    )
    # 5 年合格
    rows = []
    for code in ["600519", "000538", "600276"]:
        for y in [2020, 2021, 2022, 2023, 2024]:
            rows.append((code, y, 50.0, 20.0, f"{y}-12-31", ""))
    conn.executemany(
        "INSERT INTO fundamentals_history (code, year, gross_margin, roe_excl, report_date, synced_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows,
    )
    # 000002 只有 3 年
    rows2 = [(("000002", y, 50.0, 20.0, f"{y}-12-31", "")) for y in [2022, 2023, 2024]]
    conn.executemany(
        "INSERT INTO fundamentals_history (code, year, gross_margin, roe_excl, report_date, synced_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        rows2,
    )
    conn.commit()
    conn.close()
    return db


def test_runner_returns_only_passing_stocks(lin_db):
    runner = LinYuanRunner()
    result = runner.run(db_path=lin_db)
    codes = sorted(p.code for p in result.positions)
    assert codes == ["000538", "600276", "600519"]


def test_runner_assigns_equal_weights(lin_db):
    runner = LinYuanRunner()
    result = runner.run(db_path=lin_db)
    weights = {p.weight for p in result.positions}
    assert len(weights) == 1
    w = next(iter(weights))
    assert abs(w - 1 / 3) < 1e-6


def test_runner_top_n_caps_output(lin_db):
    runner = LinYuanRunner(top_n=2)
    result = runner.run(db_path=lin_db)
    assert len(result.positions) == 2


def test_runner_empty_when_no_history(tmp_path):
    db = str(tmp_path / "empty.db")
    open_db(db).close()
    runner = LinYuanRunner()
    result = runner.run(db_path=db)
    assert result.positions == []
```

- [ ] **Step 2: 跑测试 → 失败**

Run: `.venv/bin/python -m pytest tests/test_linyuan_strategy.py -v -k runner`
Expected: ImportError (LinYuanRunner)

- [ ] **Step 3: 实现 LinYuanRunner**

```python
# strategies/linyuan_multi_factor.py 末尾追加
import sqlite3
from datetime import date as _date
from typing import List, Optional

from db_repository import (
    open_db,
    get_fundamentals_history_by_code,
    get_metadata_by_code,
)
from strategies.multi_factor_base import SelectionResult, TargetPosition


class LinYuanRunner:
    """林园策略执行器：DB 读取 + 过滤 + 等权 top_n。"""

    def __init__(
        self,
        config: Optional[LinYuanConfig] = None,
        top_n: int = 20,
    ):
        self.config = config or LinYuanConfig()
        self.top_n = top_n

    def run(
        self,
        db_path: str,
        today: Optional[_date] = None,
    ) -> SelectionResult:
        today = today or _date.today()
        conn = open_db(db_path)
        try:
            codes = [
                r[0] for r in conn.execute("SELECT code FROM hs300_constituents").fetchall()
            ]
            if not codes:
                codes = [
                    r[0] for r in conn.execute(
                        "SELECT code FROM hs300_metadata"
                    ).fetchall()
                ]
            positions: List[TargetPosition] = []
            for code in codes:
                meta = get_metadata_by_code(conn, code)
                sector = (meta.industry if meta else "") or ""
                history = get_fundamentals_history_by_code(conn, code)
                if not passes_linyuan_filter(sector, history, self.config):
                    continue
                positions.append(TargetPosition(
                    code=code,
                    name=(meta.name if meta else code) or code,
                    weight=0.0,
                    score=0.0,
                    sector=sector,
                    sub_sector="",
                ))
        finally:
            conn.close()

        positions.sort(key=lambda p: p.code)
        n = min(len(positions), self.top_n)
        positions = positions[:n]
        if positions:
            w = 1.0 / len(positions)
            for p in positions:
                p.weight = w
        return SelectionResult(
            date=today,
            positions=positions,
            excluded=[],
            rebalance_reason=f"林园: 候选{len(codes)}只 通过{len(positions)}只",
        )
```

- [ ] **Step 4: 重跑测试**

Run: `.venv/bin/python -m pytest tests/test_linyuan_strategy.py -v`
Expected: 全部通过

- [ ] **Step 5: 在 strategies/__init__.py 注册**

```python
# strategies/__init__.py
from strategies.linyuan_multi_factor import LinYuanRunner

MULTI_FACTOR_STRATEGIES = {
    "linyuan": LinYuanRunner,
}
```

- [ ] **Step 6: 跑全量回归**

Run: `.venv/bin/python -m pytest`
Expected: 全量 ≥ 当前 passed

- [ ] **Step 7: Commit**

```bash
git add strategies/linyuan_multi_factor.py strategies/__init__.py tests/test_linyuan_strategy.py
git commit -m "feat(strategies): LinYuanRunner + register in MULTI_FACTOR_STRATEGIES"
```

---

## Task 11: CLI 暴露 `linyuan-picks` / `sync-fundamentals-history` / `sync-industry`

**Files:**
- Modify: `cli_layer.py`（parser + run_cli 分发）

**Interfaces:**
- Produces:
  - argparse 子命令 `linyuan-picks`（`--top` / `--db` / `--dry-run`）
  - argparse 子命令 `sync-fundamentals-history`（`--db` / `--concurrency` / `--rate` / `--retries` / `--backoff`）
  - argparse 子命令 `sync-industry`（同上参数组）
  - `_run_linyuan(args)` / `_run_sync_fundamentals_history(args)` / `_run_sync_industry(args)` 处理器

- [ ] **Step 1: 写失败测试**

在 `tests/test_cli_plan.py` 模式新建 `tests/test_cli_linyuan.py`（参考已有 cli 测试结构）：

```python
import argparse
from cli_layer import build_parser


def test_linyuan_picks_parser():
    parser = build_parser()
    sub_actions = next(
        a for a in parser._actions
        if hasattr(a, "choices") and isinstance(a.choices, dict) and "linyuan-picks" in a.choices
    )
    linyuan = sub_actions.choices["linyuan-picks"]
    flags = {a.dest for a in linyuan._actions}
    assert "top" in flags
    assert "db" in flags
    assert "dry_run" in flags


def test_sync_fundamentals_history_parser():
    parser = build_parser()
    sub_actions = next(
        a for a in parser._actions
        if hasattr(a, "choices") and isinstance(a.choices, dict)
        and "sync-fundamentals-history" in a.choices
    )
    cmd = sub_actions.choices["sync-fundamentals-history"]
    flags = {a.dest for a in cmd._actions}
    assert {"db", "concurrency", "rate", "retries", "backoff"} <= flags


def test_sync_industry_parser():
    parser = build_parser()
    sub_actions = next(
        a for a in parser._actions
        if hasattr(a, "choices") and isinstance(a.choices, dict)
        and "sync-industry" in a.choices
    )
    cmd = sub_actions.choices["sync-industry"]
    flags = {a.dest for a in cmd._actions}
    assert {"db", "concurrency", "rate", "retries", "backoff"} <= flags
```

- [ ] **Step 2: 跑测试 → 失败**

Run: `.venv/bin/python -m pytest tests/test_cli_linyuan.py -v`
Expected: 3 failed（子命令不存在）

- [ ] **Step 3: 加 parser 子命令**

在 `cli_layer.py` `build_parser` 末尾追加：

```python
ly = subparsers.add_parser(
    "linyuan-picks",
    help="林园策略选股：连续5年毛利率>40% ∧ 扣非ROE>15% ∧ 行业白名单",
)
ly.add_argument("--top", type=int, default=20)
ly.add_argument("--db", type=str, default="hs300.db")
ly.add_argument("--dry-run", action="store_true")

sfh = subparsers.add_parser("sync-fundamentals-history", help="同步历年财务到 fundamentals_history")
sfh.add_argument("--db", type=str, default="hs300.db")
sfh.add_argument("--concurrency", type=int, default=4)
sfh.add_argument("--rate", type=float, default=3.0)
sfh.add_argument("--retries", type=int, default=2)
sfh.add_argument("--backoff", type=float, default=1.0)

si = subparsers.add_parser("sync-industry", help="回填 hs300_metadata.industry")
si.add_argument("--db", type=str, default="hs300.db")
si.add_argument("--concurrency", type=int, default=4)
si.add_argument("--rate", type=float, default=3.0)
si.add_argument("--retries", type=int, default=2)
si.add_argument("--backoff", type=float, default=1.0)
```

- [ ] **Step 4: 加 run_cli 分发**

在 `run_cli` `elif args.command == "evolve":` 前加：

```python
elif args.command == "linyuan-picks":
    _run_linyuan(args)
elif args.command == "sync-fundamentals-history":
    _run_sync_fundamentals_history(args)
elif args.command == "sync-industry":
    _run_sync_industry(args)
```

并在文件底部加处理器：

```python
def _run_linyuan(args):
    from strategies import MULTI_FACTOR_STRATEGIES
    from formatter import format_table

    runner_cls = MULTI_FACTOR_STRATEGIES["linyuan"]
    runner = runner_cls(top_n=args.top)
    result = runner.run(args.db)
    if args.dry_run:
        print(f"[dry-run] 林园候选 {len(result.positions)} 只")
        for p in result.positions:
            print(f"  {p.code} {p.name} sector={p.sector} weight={p.weight:.4f}")
        return
    headers = ["代码", "名称", "行业", "权重"]
    rows = [[p.code, p.name, p.sector, f"{p.weight:.4f}"] for p in result.positions]
    print(format_table(headers, rows))


def _run_sync_fundamentals_history(args):
    from sync_service import sync_fundamentals_history
    def _progress(pct, msg):
        print(f"[{pct:3d}%] {msg}")
    print(sync_fundamentals_history(
        args.db,
        concurrency=args.concurrency,
        rate_limit=args.rate,
        retries=args.retries,
        backoff=args.backoff,
        progress=_progress,
    ))


def _run_sync_industry(args):
    from sync_service import sync_industry
    def _progress(pct, msg):
        print(f"[{pct:3d}%] {msg}")
    print(sync_industry(
        args.db,
        concurrency=args.concurrency,
        rate_limit=args.rate,
        retries=args.retries,
        backoff=args.backoff,
        progress=_progress,
    ))
```

- [ ] **Step 5: 重跑测试**

Run: `.venv/bin/python -m pytest tests/test_cli_linyuan.py -v`
Expected: 3 passed

- [ ] **Step 6: 跑全量回归**

Run: `.venv/bin/python -m pytest`
Expected: 全量 ≥ 当前 passed

- [ ] **Step 7: Commit**

```bash
git add cli_layer.py tests/test_cli_linyuan.py
git commit -m "feat(cli): linyuan-picks + sync-fundamentals-history + sync-industry"
```

---

## Task 12: README 文档

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 常用命令加新子命令**

在 README 「中文使用说明」-「常用命令说明」列表中追加：

```markdown
- linyuan-picks：林园策略选股，财务质量主导（连续5年毛利率>40% ∧ 扣非ROE>15%），过滤医药/中药/食品饮料/高端制造
- sync-fundamentals-history：同步历年财务到 fundamentals_history
- sync-industry：从 akshare 拉行业回填 hs300_metadata.industry
```

在 `bash` 示例块中追加：

```bash
uv run a-finder linyuan-picks --top 20
uv run a-finder linyuan-picks --top 20 --dry-run
uv run a-finder sync-industry --db hs300.db
uv run a-finder sync-fundamentals-history --db hs300.db --concurrency 6 --rate 5
```

- [ ] **Step 2: 新增「林园策略 / LinYuan」章节**

在 `## 交易计划 / Trade Plan` 之前插入：

```markdown
## 林园策略 / LinYuan

财务质量主导的选股策略：行业白名单 + 连续 5 年毛利率/扣非 ROE 双门槛。

| 维度 | 阈值 |
|---|---|
| 行业 | 医药生物 / 中药 / 食品饮料 / 机械设备 / 电力设备 / 汽车整车 |
| 毛利率（连续 5 年） | > 40% |
| 扣非 ROE（连续 5 年） | > 15% |

**数据底座**：先用 `sync-industry` 回填 `hs300_metadata.industry`，再 `sync-fundamentals-history` 拉历年财务指标到 `fundamentals_history`。两表缺一不可。

```bash
uv run a-finder sync-industry --db hs300.db                     # 一次性回填行业
uv run a-finder sync-fundamentals-history --db hs300.db         # 拉历年财务
uv run a-finder linyuan-picks --top 20                          # 跑林园策略
```

输出落在终端表格；`--dry-run` 仅打印候选不写库。
```

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: README linyuan strategy + new sync commands"
```

---

## Task 13: 全量回归 + 提交收尾

- [ ] **Step 1: 跑全量测试**

Run: `.venv/bin/python -m pytest`
Expected: 全量 ≥ 200 passed

- [ ] **Step 2: 状态确认**

Run: `git status`
Expected: working tree clean

- [ ] **Step 3: 推送**

Run: `git push origin main`
Expected: 推送成功

---

## Self-Review Checklist

- [x] **Spec coverage**: 11 节内容已映射到 13 个 Task：
  - 一/二/三 → Task 1
  - 四 → Task 2, 3, 5, 6
  - 五 → Task 7, 8
  - 六 → Task 9, 10
  - 七 → Task 11
  - 八 → Task 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11 测试
  - 九 → Task 12
  - 十/十一 → 标注为风险，README 在 Task 12 提醒

- [x] **Placeholder scan**: 没有 TBD/TODO/"implement later"

- [x] **Type consistency**:
  - `FundamentalsHistoryRow` 在 Task 4 定义，Task 5/7/9/10 使用，签名一致
  - `LinYuanConfig` 在 Task 9 定义，Task 10 使用
  - `passes_linyuan_filter` 在 Task 9 定义，Task 10 使用

- [x] **Interface boundaries**: Task 之间接口（参数、返回）一致；不依赖未定义的方法

- [x] **TDD 顺序**: 每 Task 都是先测试、后实现、再回归、再 commit