# 固定 200 股纸面跟踪 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有百分比纸面交易之上叠加「固定 200 股、累积持仓、按金额算收益」的纸面跟踪。

**Architecture:** 保留 `size_pct` 作参考仓位，新增 `shares`（股数）贯穿 `open_positions`/`trade_plan`/`trade_events` 三表；buy 固定 200 股、同 code 异日累积为加权均价；收益按 `(价格 − 加权均价) × shares` 以元计。

**Tech Stack:** Python 3.11 + SQLite + Flask + 原生 JS/jQuery（沿用现有技术栈，不引入新依赖）。

**Spec:** `docs/superpowers/specs/2026-08-20-share-lots-design.md`

## Global Constraints

- 组合收益率分母 = 总成本 = Σ `entry_price × shares`（open + closed 全量）。
- 已实现收益 = Σ `(close_price − entry_price) × shares`（closed）。
- 止损/止盈百分比复用 `risk_manager.REGIME_CONFIGS`（不新增配置）。
- 存量 13 个 open 持仓回填 `shares = 200`。
- 不接入真实券商/手续费/印花税；滑点沿用 `SLIPPAGE=0.001`。
- 测试命令统一 `uv run pytest -q`；提交信息用中文、结尾带 Co-Authored-By。
- 项目使用「本地 Ollama API」——本计划不涉及 LLM 调用。

---

### Task 1: 迁移三表新增 shares/pnl_amt 列

**Files:**
- Create: `db/migrations/2026_08_20_share_lots.sql`
- Test: `tests/test_share_lots.py`（本任务只放迁移用例）

**Interfaces:**
- Produces: `open_positions.shares`(INTEGER NOT NULL DEFAULT 0)、`trade_plan.shares`(INTEGER NULL)、`trade_events.shares`(INTEGER NULL)、`trade_events.pnl_amt`(REAL NULL)；存量 open 持仓 `shares=200`。

- [ ] **Step 1: 写迁移文件**

```sql
ALTER TABLE open_positions ADD COLUMN shares INTEGER NOT NULL DEFAULT 0;
ALTER TABLE trade_plan     ADD COLUMN shares INTEGER;
ALTER TABLE trade_events   ADD COLUMN shares INTEGER;
ALTER TABLE trade_events   ADD COLUMN pnl_amt REAL;
UPDATE open_positions SET shares = 200 WHERE status = 'open' AND shares = 0;
```

- [ ] **Step 2: 写迁移测试（含存量回填）**

```python
# tests/test_share_lots.py
import os
import tempfile

from db_repository import open_db


def test_migration_adds_shares_and_backfills_open():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = open_db(path)  # applies schema + all migrations
    try:
        # 先造一个存量 open 持仓（模拟迁移前已有数据，shares 默认为 0）
        conn.execute(
            """INSERT INTO open_positions
            (code, entry_date, entry_price, size_pct, stop_price, tp_price, status)
            VALUES ('600519','2026-08-10',100.0,0.1,92.0,120.0,'open')"""
        )
        conn.commit()
        # 迁移对已存在表不会二次 ALTER；这里直接断言列已存在
        cols = [r[1] for r in conn.execute("PRAGMA table_info(open_positions)").fetchall()]
        assert "shares" in cols
        tp_cols = [r[1] for r in conn.execute("PRAGMA table_info(trade_plan)").fetchall()]
        assert "shares" in tp_cols
        te_cols = [r[1] for r in conn.execute("PRAGMA table_info(trade_events)").fetchall()]
        assert "shares" in te_cols and "pnl_amt" in te_cols
    finally:
        conn.close()
```

> 注：迁移文件里的 `UPDATE ... shares = 200` 只在**未应用的库**上执行一次；已存在的 `hs300.db` 会在下次 `open_db` 时应用。存量回填的正确性用 Task 7 的真实 DB 校验，不在此单测强断言（tempfile 库没有存量 open 行触发回填）。

- [ ] **Step 3: 跑迁移测试**

Run: `uv run pytest tests/test_share_lots.py -v`
Expected: PASS（列存在）

- [ ] **Step 4: 手动验证真实库已应用迁移**

Run: `uv run python -c "import sqlite3; c=sqlite3.connect('hs300.db'); print([r[1] for r in c.execute('PRAGMA table_info(open_positions)')]); print(c.execute(\"SELECT shares FROM open_positions LIMIT 3\").fetchall())"`
Expected: 列表含 `shares`，`SELECT shares` 返回 `[(200,), (200,), (200,)]`

- [ ] **Step 5: Commit**

```bash
git add db/migrations/2026_08_20_share_lots.sql tests/test_share_lots.py
git commit -m "feat: 迁移三表新增 shares/pnl_amt 列并回填存量 200 股

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: PlanRow.shares 字段 + trade_plan 写入/读回 shares

**Files:**
- Modify: `shared_lib/strategy.py`（PlanRow dataclass）
- Modify: `db_repository.py`（`insert_trade_plan`、`get_trade_plan_by_date_and_hash` 已 SELECT *，无需改）
- Modify: `plan_builder.py`（`_row_from_db`）
- Test: `tests/test_shared_lib.py`（追加）

**Interfaces:**
- Produces: `PlanRow.shares: int = 200`；`insert_trade_plan` 落库 `shares`；`_row_from_db` 读回 `shares`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_shared_lib.py 追加
def test_plan_row_default_shares():
    from shared_lib.strategy import PlanRow
    r = PlanRow(code="600519", action="buy", plan_price=100.0, size_pct=0.1,
                stop_price=92.0, tp_price=120.0, rr_ratio=2.0)
    assert r.shares == 200
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_shared_lib.py::test_plan_row_default_shares -v`
Expected: FAIL（`TypeError: __init__() got an unexpected keyword argument...` 或 `AttributeError: shares`）

- [ ] **Step 3: 给 PlanRow 加 shares**

在 `shared_lib/strategy.py` 的 `PlanRow` dataclass 中，`reason: str = ""` 之后加一行：

```python
    shares: int = 200
```

- [ ] **Step 4: insert_trade_plan 写入 shares**

`db_repository.py` `insert_trade_plan` 的 INSERT 列清单与 VALUES 各加 `shares`：

```python
        """INSERT OR IGNORE INTO trade_plan
        (plan_date, code, action, plan_price, size_pct, stop_price, tp_price,
         rr_ratio, status, reason, rationale_json, params_hash, created_at, shares)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            plan_date, row.code, row.action, row.plan_price, row.size_pct,
            row.stop_price, row.tp_price, row.rr_ratio, row.status, row.reason,
            json.dumps(row.rationale), params_hash,
            dt.datetime.utcnow().isoformat(timespec="seconds"),
            row.shares,
        ),
```

- [ ] **Step 5: _row_from_db 读回 shares**

`plan_builder.py` `_row_from_db` 返回的 `PlanRow(...)` 末尾加：

```python
        shares=int(r.get("shares") or 200),
```

- [ ] **Step 6: 跑测试确认通过**

Run: `uv run pytest tests/test_shared_lib.py tests/test_plan_builder.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add shared_lib/strategy.py db_repository.py plan_builder.py tests/test_shared_lib.py
git commit -m "feat: PlanRow 新增 shares 字段并贯穿 trade_plan 写入/读回

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: open_positions 落股数 + 累积 helper

**Files:**
- Modify: `db_repository.py`（`insert_open_position`、新增 `accumulate_open_position`）
- Test: `tests/test_share_lots.py`（追加）

**Interfaces:**
- Produces:
  - `insert_open_position(conn, code, entry_date, entry_price, size_pct, stop_price, tp_price, shares=200) -> int`
  - `accumulate_open_position(conn, code, fill_price, size_pct, stop_price, tp_price, shares_to_add=200) -> int`（存在则加权均价累积并重算 stop/tp，否则新建）

- [ ] **Step 1: 写失败测试**

```python
# tests/test_share_lots.py 追加
from db_repository import insert_open_position, accumulate_open_position, get_open_positions


def test_insert_open_position_default_200_shares():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = open_db(path)
    try:
        insert_open_position(conn, "600519", "2026-08-18", 100.0, 0.1, 92.0, 120.0)
        opens = get_open_positions(conn)
        assert opens[0]["shares"] == 200
    finally:
        conn.close()


def test_accumulate_open_position_weights_avg():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = open_db(path)
    try:
        insert_open_position(conn, "600519", "2026-08-18", 100.0, 0.1, 92.0, 120.0)
        # 第二天再买 200 股 @ 110 → 加权均价 = (200*100 + 200*110)/400 = 105
        accumulate_open_position(conn, "600519", 110.0, 0.1, 97.0, 126.0, 200)
        opens = get_open_positions(conn)
        assert len(opens) == 1
        assert opens[0]["shares"] == 400
        assert abs(opens[0]["entry_price"] - 105.0) < 1e-6
        assert abs(opens[0]["stop_price"] - 97.0) < 1e-6
    finally:
        conn.close()


def test_accumulate_open_position_creates_when_missing():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = open_db(path)
    try:
        accumulate_open_position(conn, "000001", 10.0, 0.1, 9.2, 12.0, 200)
        opens = get_open_positions(conn)
        assert len(opens) == 1 and opens[0]["shares"] == 200
    finally:
        conn.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_share_lots.py -v`
Expected: FAIL（`accumulate_open_position` 不存在 / `shares` 不在返回）

- [ ] **Step 3: 改 insert_open_position 加 shares**

```python
def insert_open_position(
    conn: sqlite3.Connection,
    code: str,
    entry_date: str,
    entry_price: float,
    size_pct: float,
    stop_price: float,
    tp_price: float,
    shares: int = 200,
) -> int:
    """Open a new paper position with a fixed share count. Returns pos_id."""
    cur = conn.execute(
        """INSERT INTO open_positions
        (code, entry_date, entry_price, size_pct, stop_price, tp_price, status, shares)
        VALUES (?,?,?,?,?,?,'open',?)""",
        (code, entry_date, entry_price, size_pct, stop_price, tp_price, shares),
    )
    conn.commit()
    return cur.lastrowid
```

- [ ] **Step 4: 新增 accumulate_open_position**

放在 `insert_open_position` 之后：

```python
def accumulate_open_position(
    conn: sqlite3.Connection,
    code: str,
    fill_price: float,
    size_pct: float,
    stop_price: float,
    tp_price: float,
    shares_to_add: int = 200,
) -> int:
    """Add shares to an existing open position (weighted-average entry).

    If no open position exists for `code`, opens a fresh one. Returns pos_id.
    """
    row = conn.execute(
        "SELECT pos_id, shares, entry_price FROM open_positions "
        "WHERE code=? AND status='open' LIMIT 1",
        (code,),
    ).fetchone()
    if row is None:
        return insert_open_position(
            conn, code, "", fill_price, size_pct, stop_price, tp_price, shares_to_add
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
```

- [ ] **Step 5: 跑测试确认通过**

Run: `uv run pytest tests/test_share_lots.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add db_repository.py tests/test_share_lots.py
git commit -m "feat: open_positions 支持固定股数与加权均价累积

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: plan_builder buy 路径 — 固定 200 股 + 累积 + 幂等改造

**Files:**
- Modify: `plan_builder.py`（`_build_buy_rows`、`_build_carryover_rows`、`_paper_trade` buy 分支）
- Modify: `db_repository.py`（`insert_trade_event` 增 shares/pnl_amt 参数）
- Test: `tests/test_share_lots.py`（追加）

**Interfaces:**
- Consumes: `accumulate_open_position`（Task 3）
- Produces: buy 行 `shares=200`；同 code 异日累积；同日幂等（不重复买）；`insert_trade_event(..., shares=None, pnl_amt=None)`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_share_lots.py 追加
from plan_builder import build_plan


def _seed_pick(conn, date, code, buy=100.0, score=2.0):
    conn.execute(
        """INSERT INTO daily_picks (date, rank, kind, code, name, strategy, buy, stop, target, score)
        VALUES (?, 1, '均线', ?, ?, 'test', ?, ?, ?, ?)""",
        (date, code, code, buy, buy * 0.9, buy * 1.2, score),
    )
    conn.commit()


def test_build_plan_buys_200_shares_and_accumulates_next_day():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = open_db(path)
    _seed_pick(conn, "2026-08-18", "600519", buy=100.0)
    conn.close()
    build_plan("2026-08-18", path, params={"regime": "BULL"})

    conn = open_db(path)
    _seed_pick(conn, "2026-08-19", "600519", buy=110.0)
    conn.close()
    build_plan("2026-08-19", path, params={"regime": "BULL"})

    conn = open_db(path)
    try:
        opens = conn.execute(
            "SELECT shares, entry_price FROM open_positions WHERE code='600519' AND status='open'"
        ).fetchall()
        assert len(opens) == 1
        assert opens[0][0] == 400  # 累积 400 股
        # 加权均价 = (200*100.1 + 200*110.11)/400 ≈ 105.1（滑点 0.1%）
        assert 104.0 < opens[0][1] < 106.0
        evts = conn.execute(
            "SELECT plan_date, shares FROM trade_events WHERE event_type='open' ORDER BY plan_date"
        ).fetchall()
        assert evts == [("2026-08-18", 200), ("2026-08-19", 200)]
    finally:
        conn.close()


def test_build_plan_same_day_rebuild_does_not_double_buy():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = open_db(path)
    _seed_pick(conn, "2026-08-18", "600519", buy=100.0)
    conn.close()
    build_plan("2026-08-18", path, params={"regime": "BULL"})
    build_plan("2026-08-18", path, params={"regime": "BULL"})  # 同日重跑
    conn = open_db(path)
    try:
        shares = conn.execute(
            "SELECT shares FROM open_positions WHERE code='600519' AND status='open'"
        ).fetchone()[0]
        assert shares == 200  # 未重复买
    finally:
        conn.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_share_lots.py::test_build_plan_buys_200_shares_and_accumulates_next_day -v`
Expected: FAIL（shares 不是 400 / 没有累积）

- [ ] **Step 3: insert_trade_event 增 shares/pnl_amt**

`db_repository.py` `insert_trade_event` 签名与 INSERT 改为：

```python
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
) -> int:
    cur = conn.execute(
        """INSERT INTO trade_events
        (plan_date, code, event_type, price, size_pct, pnl_pct, note, shares, pnl_amt, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (plan_date, code, event_type, price, size_pct, pnl_pct, note, shares, pnl_amt,
         dt.datetime.utcnow().isoformat(timespec="seconds")),
    )
    conn.commit()
    return cur.lastrowid
```

- [ ] **Step 4: _build_buy_rows 设 shares=200**

在 `_build_buy_rows` 返回的 `PlanRow(...)` 里加 `shares=200`：

```python
        rows.append(PlanRow(
            code=str(p["code"]),
            action="buy",
            plan_price=plan_price,
            size_pct=cfg.position_size,
            stop_price=stop,
            tp_price=tp,
            rr_ratio=round(rr, 4),
            shares=200,
            rationale={...},
            status="ok",
            reason="",
        ))
```

- [ ] **Step 5: _build_carryover_rows 带 shares**

`_build_carryover_rows` 的 hold/exit 两个 `PlanRow(...)` 各加 `shares=int(o.get("shares") or 0)`（`get_open_positions` 已 SELECT * 含 shares）。

- [ ] **Step 6: C2 去掉「已持有 code 不买」**

`build_plan` 中删除：

```python
    held_codes = {o["code"] for o in opens}
    buy_picks = [p for p in picks if str(p.get("code", "")) not in held_codes]
    rows.extend(_build_buy_rows(buy_picks, regime, risk_manager))
```

改为直接：

```python
    rows.extend(_build_buy_rows(picks, regime, risk_manager))
```

- [ ] **Step 7: _paper_trade buy 分支 — 幂等 + 累积**

把 buy 分支整体替换为：

```python
            if r.action == "buy" and r.status == "ok":
                # C1 幂等（改）：以 trade_events 的 (code, plan_date, 'open') 为准，
                # 允许同 code 跨日累积。
                existing = conn.execute(
                    "SELECT 1 FROM trade_events "
                    "WHERE code=? AND plan_date=? AND event_type='open' LIMIT 1",
                    (r.code, plan_date),
                ).fetchone()
                if existing:
                    continue
                fill_price = round(r.plan_price * (1 + slippage), 4)
                accumulate_open_position(
                    conn, r.code, fill_price, r.size_pct,
                    r.stop_price, r.tp_price, r.shares,
                )
                insert_trade_event(
                    conn, plan_date, r.code, "open",
                    fill_price, r.size_pct, shares=r.shares, note="paper_fill",
                )
```

> 原 `existing` 查询基于 open_positions，改为基于 trade_events。`insert_open_position` 的 import 需保留（`accumulate_open_position` 内部会用到它，但 plan_builder 直接调 accumulate 即可）。

- [ ] **Step 8: 更新 import**

`plan_builder.py` 顶部 `from db_repository import (...)` 中把 `insert_open_position` 换成 `accumulate_open_position`（若已无其他调用点）。确认 `insert_trade_event`、`close_open_position` 仍在。

- [ ] **Step 9: 跑测试确认通过**

Run: `uv run pytest tests/test_share_lots.py -v`
Expected: PASS（两条 build_plan 测试通过）

- [ ] **Step 10: Commit**

```bash
git add plan_builder.py db_repository.py tests/test_share_lots.py
git commit -m "feat: buy 固定 200 股、同 code 异日累积、幂等改以 trade_events 为准

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: plan_builder close 路径 — pnl_amt 金额盈亏

**Files:**
- Modify: `plan_builder.py`（`_paper_trade` exit 分支）
- Test: `tests/test_share_lots.py`（追加）

**Interfaces:**
- Consumes: `open_positions.shares`；`insert_trade_event(..., shares=, pnl_amt=)`（Task 4）
- Produces: close 事件写 `shares` 与 `pnl_amt = (close_price − entry_price) × shares`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_share_lots.py 追加
def test_close_records_amount_pnl():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = open_db(path)
    # 200 股 @ 100，止损价 105（现价 102 会触发 exit? 需现价 <= stop? 见下）
    conn.execute(
        """INSERT INTO open_positions
        (code, entry_date, entry_price, size_pct, stop_price, tp_price, status, shares)
        VALUES ('000002','2026-08-10',100.0,0.1,105.0,115.0,'open',200)"""
    )
    conn.execute(
        "INSERT INTO daily_prices (code, trade_date, close) VALUES ('000002','2026-08-18',102.0)"
    )
    conn.commit()
    conn.close()
    build_plan("2026-08-18", path, params={"regime": "BULL"})

    conn = open_db(path)
    try:
        close = conn.execute(
            "SELECT shares, pnl_amt FROM trade_events WHERE event_type='close'"
        ).fetchone()
        assert close[0] == 200
        assert abs(close[1] - 400.0) < 0.01  # (102 - 100) * 200 = 400
        still_open = conn.execute(
            "SELECT COUNT(*) FROM open_positions WHERE code='000002' AND status='open'"
        ).fetchone()[0]
        assert still_open == 0
    finally:
        conn.close()
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_share_lots.py::test_close_records_amount_pnl -v`
Expected: FAIL（`pnl_amt` 为 NULL / `shares` 为 NULL）

- [ ] **Step 3: 改 _paper_trade exit 分支**

把 exit 分支整体替换为：

```python
            elif r.action == "exit" and r.status == "ok":
                cur = conn.execute(
                    "SELECT pos_id, entry_price, shares FROM open_positions "
                    "WHERE code=? AND status='open' ORDER BY entry_date LIMIT 1",
                    (r.code,),
                )
                row = cur.fetchone()
                if row:
                    pos_id, entry_price, shares = row
                    close_reason = (r.rationale or {}).get("trigger", "manual")
                    close_open_position(conn, pos_id, plan_date,
                                        r.plan_price, close_reason)
                    pnl_amt = round((r.plan_price - entry_price) * (shares or 0), 2)
                    insert_trade_event(
                        conn, plan_date, r.code, "close",
                        r.plan_price, None, shares=shares, pnl_amt=pnl_amt,
                        note="paper_close",
                    )
```

（删除原 `pnl = round((r.plan_price / entry_price - 1) * 100, 4)` 与 `pnl_pct` 传参。）

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_share_lots.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add plan_builder.py tests/test_share_lots.py
git commit -m "feat: close 事件记录 shares 与金额盈亏 pnl_amt

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: sanity gate — 停用组合缩放、max_single 降级为参考

**Files:**
- Modify: `plan_builder.py`（`_apply_sanity_gate`）
- Test: `tests/test_share_lots.py`（追加）

**Interfaces:**
- Produces: 固定股数下不再按 `max_total` 缩放；`max_single` 不再标 failed，仅往 `sanity_reasons` 记 `size_ref_warn`；「止损高于入场」仍标 failed。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_share_lots.py 追加
def test_sanity_gate_no_scaling_under_fixed_shares():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = open_db(path)
    for code in ["600000", "600001", "600002", "600003", "600004"]:
        _seed_pick(conn, "2026-08-18", code, buy=100.0)
    conn.close()
    result = build_plan("2026-08-18", path, params={
        "regime": "BULL", "max_single": 0.15, "max_total": 0.3,
    })
    buys = [r for r in result.rows if r.action == "buy"]
    assert len(buys) == 5
    # 固定股数下不得缩放、不得标 failed
    assert all(r.status == "ok" for r in buys)
    assert not any("scaled_to_fit" in r.reason for r in buys)
    assert not any("size_exceed_max" in r.reason for r in buys)
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_share_lots.py::test_sanity_gate_no_scaling_under_fixed_shares -v`
Expected: FAIL（现有逻辑会 `scaled_to_fit` 或标 failed）

- [ ] **Step 3: 改 _apply_sanity_gate**

把函数体替换为（保留规则 2，停用规则 1 的 failed 与规则 3 的缩放）：

```python
def _apply_sanity_gate(
    rows: List[PlanRow],
    max_single: float,
    max_total: float,
) -> List[str]:
    """Fixed-share sanity gate.

    Fixed 200-share lots disable portfolio-weight scaling (you cannot scale
    200 shares down to 190). `max_single` becomes a reference warning only;
    the only hard failure left is a stop placed at/above entry.
    """
    reasons: List[str] = []
    buy_rows = [r for r in rows if r.action == "buy"]

    # Rule 1 (downgraded): reference weight above max_single → warning only.
    for r in buy_rows:
        if r.size_pct > max_single:
            reasons.append(f"{r.code}:size_ref_warn")

    # Rule 2 (hard): stop must be below entry.
    for r in buy_rows:
        if r.plan_price > 0 and r.stop_price > r.plan_price:
            r.status = "failed"
            r.reason = "stop_above_entry"
            reasons.append(f"{r.code}:stop_above_entry")

    return reasons
```

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run pytest tests/test_share_lots.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add plan_builder.py tests/test_share_lots.py
git commit -m "feat: 固定股数下停用组合缩放、max_single 降级为参考告警

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: 收益口径改金额 — get_recent_pnl / get_open_positions_with_unrealized / get_holdings_detail

**Files:**
- Modify: `db_repository.py`（重写 `get_recent_pnl`、扩展 `get_open_positions_with_unrealized`、新增 `get_holdings_detail`）
- Test: `tests/test_recent_pnl.py`（改写）、`tests/test_share_lots.py`（追加）

**Interfaces:**
- Produces:
  - `get_recent_pnl(conn, days=5) -> list[{"date": str, "pnl_amt": float}]`
  - `get_open_positions_with_unrealized(conn) -> dict`（items 增 `shares/current_price/floating_pnl/stop_pnl/tp_pnl`；顶层增 `shares_total/floating_pnl`）
  - `get_holdings_detail(conn) -> {"holdings": [...], "summary": {...}}`

- [ ] **Step 1: 重写 get_recent_pnl 为金额口径**

用以下实现整体替换 `get_recent_pnl`：

```python
def get_recent_pnl(conn: sqlite3.Connection, days: int = 5) -> List[Dict]:
    """Last `days` distinct trade dates' portfolio return in yuan, newest first.

    Per day: realized (closed that day) + unrealized (open positions marked
    to that day's close, skipping dates before entry). Return [] with no prices.
    """
    dates = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT trade_date FROM daily_prices "
            "ORDER BY trade_date DESC LIMIT ?", (days,),
        ).fetchall()
    ]
    if not dates:
        return []
    closed = conn.execute(
        "SELECT close_date, entry_price, close_price, shares "
        "FROM open_positions WHERE status='closed' AND close_date IS NOT NULL",
    ).fetchall()
    opens = conn.execute(
        "SELECT code, entry_date, entry_price, shares "
        "FROM open_positions WHERE status='open'",
    ).fetchall()
    open_codes = [r[0] for r in opens]
    prices: Dict[Tuple[str, str], float] = {}
    if open_codes:
        ph = ",".join("?" for _ in open_codes)
        prices = {
            (code, tdate): close
            for code, tdate, close in conn.execute(
                f"SELECT code, trade_date, close FROM daily_prices "
                f"WHERE code IN ({ph}) AND trade_date BETWEEN ? AND ?",
                (*open_codes, dates[-1], dates[0]),
            )
        }
    out: List[Dict] = []
    for d in dates:
        amt = 0.0
        contributed = False
        for close_date, entry, close, shares in closed:
            if close_date == d and entry:
                amt += (close - entry) * (shares or 0)
                contributed = True
        for code, entry_date, entry, shares in opens:
            if entry_date > d or not entry:
                continue
            close = prices.get((code, d))
            if close is not None:
                amt += (close - entry) * (shares or 0)
                contributed = True
        if contributed:
            out.append({"date": d, "pnl_amt": round(amt, 2)})
    return out
```

- [ ] **Step 2: 扩展 get_open_positions_with_unrealized**

把返回的 items 字典构造改为（查询已 `dp.close AS close_price`，补 shares 列与 name）：

```python
def get_open_positions_with_unrealized(conn: sqlite3.Connection) -> Dict:
    cur = conn.execute(
        """SELECT op.code, op.entry_date, op.entry_price, op.size_pct,
                  op.stop_price, op.tp_price, op.shares,
                  dp.close AS close_price
           FROM open_positions op
           LEFT JOIN (
               SELECT code, close FROM daily_prices dp1
               WHERE trade_date = (SELECT MAX(trade_date) FROM daily_prices dp2
                                   WHERE dp2.code = dp1.code)
           ) dp ON dp.code = op.code
           WHERE op.status = 'open'
           ORDER BY op.entry_date, op.code""",
    )
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
```

- [ ] **Step 3: 新增 get_holdings_detail**

```python
def get_holdings_detail(conn: sqlite3.Connection) -> Dict:
    """Full per-position tracking + portfolio summary (yuan)."""
    opens = conn.execute(
        """SELECT op.code, m.name, op.entry_price, op.shares, op.stop_price, op.tp_price,
                  dp.close AS current_price
           FROM open_positions op
           LEFT JOIN hs300_metadata m ON m.code = op.code
           LEFT JOIN (
               SELECT code, close FROM daily_prices dp1
               WHERE trade_date = (SELECT MAX(trade_date) FROM daily_prices dp2
                                   WHERE dp2.code = dp1.code)
           ) dp ON dp.code = op.code
           WHERE op.status = 'open'
           ORDER BY op.entry_date, op.code""",
    ).fetchall()
    holdings = []
    floating_total = 0.0
    shares_total = 0
    cost_open = 0.0
    for code, name, entry, shares, stop, tp, cur in opens:
        entry = entry or 0.0
        shares = shares or 0
        cost_open += entry * shares
        shares_total += shares
        floating = round((cur - entry) * shares, 2) if cur is not None else None
        if floating is not None:
            floating_total += floating
        holdings.append({
            "code": code, "name": name, "shares": shares,
            "entry_price": entry, "current_price": cur,
            "stop_price": stop, "tp_price": tp,
            "floating_pnl": floating,
            "stop_pnl": round((stop - entry) * shares, 2) if stop is not None else None,
            "tp_pnl": round((tp - entry) * shares, 2) if tp is not None else None,
        })
    realized = conn.execute(
        "SELECT COALESCE(SUM((close_price - entry_price) * shares), 0) "
        "FROM open_positions WHERE status='closed'",
    ).fetchone()[0]
    closed_cost = conn.execute(
        "SELECT COALESCE(SUM(entry_price * shares), 0) "
        "FROM open_positions WHERE status='closed'",
    ).fetchone()[0]
    total_cost = cost_open + closed_cost
    total_pnl = round(floating_total + realized, 2)
    return_pct = round(total_pnl / total_cost * 100, 2) if total_cost else 0.0
    return {
        "holdings": holdings,
        "summary": {
            "open_count": len(holdings),
            "shares_total": shares_total,
            "floating_pnl": round(floating_total, 2),
            "realized_pnl": round(realized, 2),
            "total_cost": round(total_cost, 2),
            "total_pnl": total_pnl,
            "return_pct": return_pct,
        },
    }
```

- [ ] **Step 4: 改写 test_recent_pnl.py 为金额口径**

把 `tests/test_recent_pnl.py` 里所有 `pnl_pct` 断言改为 `pnl_amt`，并把 size 权重改为 shares 权重。示例（单持仓多日，200 股 @ 100 入）：

```python
def test_recent_pnl_single_open_position_multiday(tmp_path):
    db = str(tmp_path / "pnl.db")
    conn = open_db(db)
    conn.executemany(
        "INSERT INTO daily_prices (code, trade_date, close) VALUES (?,?,?)",
        [("600000", "2026-08-15", 100.0),
         ("600000", "2026-08-18", 110.0),
         ("600000", "2026-08-19", 105.0)],
    )
    conn.execute(
        """INSERT INTO open_positions
        (code, entry_date, entry_price, size_pct, stop_price, tp_price, status, shares)
        VALUES ('600000','2026-08-15',100.0,0.1,92.0,120.0,'open',200)""",
    )
    conn.commit()
    try:
        assert get_recent_pnl(conn, days=3) == [
            {"date": "2026-08-19", "pnl_amt": 1000.0},   # (105-100)*200
            {"date": "2026-08-18", "pnl_amt": 2000.0},   # (110-100)*200
            {"date": "2026-08-15", "pnl_amt": 0.0},
        ]
    finally:
        conn.close()
```

按同法改写其余 4 个用例（空/无行情/混合/跳过 entry 前），注意 `open_positions` 插入需带 `shares`，断言用 `pnl_amt`。

- [ ] **Step 5: 写 get_holdings_detail 测试**

```python
# tests/test_share_lots.py 追加
from db_repository import get_holdings_detail


def test_holdings_detail_summary():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = open_db(path)
    conn.execute(
        "INSERT INTO hs300_metadata (code, name) VALUES ('600519','贵州茅台')"
    )
    conn.execute(
        """INSERT INTO open_positions
        (code, entry_date, entry_price, size_pct, stop_price, tp_price, status, shares)
        VALUES ('600519','2026-08-18',100.0,0.1,92.0,120.0,'open',200)"""
    )
    conn.execute(
        "INSERT INTO daily_prices (code, trade_date, close) VALUES ('600519','2026-08-19',110.0)"
    )
    conn.commit()
    d = get_holdings_detail(conn)
    assert d["summary"]["open_count"] == 1
    assert d["summary"]["shares_total"] == 200
    assert d["summary"]["floating_pnl"] == 2000.0
    assert d["summary"]["realized_pnl"] == 0.0
    assert d["holdings"][0]["name"] == "贵州茅台"
    assert d["holdings"][0]["floating_pnl"] == 2000.0
    assert d["holdings"][0]["stop_pnl"] == (92.0 - 100.0) * 200
    conn.close()
```

- [ ] **Step 6: 跑测试确认通过**

Run: `uv run pytest tests/test_recent_pnl.py tests/test_share_lots.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add db_repository.py tests/test_recent_pnl.py tests/test_share_lots.py
git commit -m "feat: 收益改金额口径，新增 get_holdings_detail 四层收益

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: /api/holdings 端点 + dashboard 字段升级

**Files:**
- Modify: `app.py`（新增 `/api/holdings`；dashboard 用 `get_holdings_detail` 摘要）
- Test: `tests/test_web_plan.py`（追加）

**Interfaces:**
- Consumes: `get_holdings_detail`、`get_recent_pnl`（Task 7）
- Produces: `GET /api/holdings`；`GET /api/dashboard` 的 `pnl_5d`（金额）、`open_positions`（含 shares/floating_pnl）、新增 `holdings_summary`。

- [ ] **Step 1: 写失败测试**

```python
# tests/test_web_plan.py 追加
def test_api_holdings_returns_summary(plan_db):
    app = create_app(db_path=plan_db)
    app.config["TESTING"] = True
    resp = app.test_client().get("/api/holdings")
    assert resp.status_code == 200
    data = resp.get_json()
    assert set(data.keys()) == {"holdings", "summary"}
    assert "total_pnl" in data["summary"]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run pytest tests/test_web_plan.py::test_api_holdings_returns_summary -v`
Expected: FAIL（404）

- [ ] **Step 3: 新增 /api/holdings 端点**

在 `app.py` 的 `/api/dashboard` 之后加：

```python
    @app.get("/api/holdings")
    def holdings():
        from db_repository import get_holdings_detail
        conn = open_conn(db_path)
        try:
            return jsonify(get_holdings_detail(conn))
        finally:
            conn.close()
```

- [ ] **Step 4: dashboard 升级**

`app.py` `dashboard()` 内：

```python
        from db_repository import (
            get_last_refresh,
            get_today_plan_summary,
            get_open_positions_with_unrealized,
            get_recent_pnl,
            get_holdings_detail,
        )
        ...
            opens = get_open_positions_with_unrealized(conn)
            pnl = get_recent_pnl(conn, days=5)
            hd = get_holdings_detail(conn)
        ...
        return jsonify({
            "last_refresh": last,
            "today_plan": today,
            "open_positions": opens,
            "pnl_5d": pnl,
            "holdings_summary": hd["summary"],
        })
```

- [ ] **Step 5: 同步 export_json 的 dashboard payload**

`export_json.py` `_dashboard_payload` 同样加 `get_holdings_detail`：

```python
from db_repository import (
    ...,
    get_recent_pnl,
    get_holdings_detail,
)
def _dashboard_payload(conn) -> dict:
    ...
    return {
        "last_refresh": last,
        "today_plan": today,
        "open_positions": opens,
        "pnl_5d": pnl,
        "holdings_summary": get_holdings_detail(conn)["summary"],
    }
```

- [ ] **Step 6: 跑测试确认通过**

Run: `uv run pytest tests/test_web_plan.py tests/test_export_static.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add app.py export_json.py tests/test_web_plan.py
git commit -m "feat: 新增 /api/holdings 端点并升级 dashboard 为金额口径

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: 前端 — 计划页股数列、dashboard 汇总、持仓跟踪表、静态导出对齐

**Files:**
- Modify: `app.py`（`PLAN_BODY`/`PLAN_SCRIPT`）
- Modify: `static/dashboard.js`、`static/data-source.js`
- Modify: `export_json.py`（导出 `data/holdings.json`）
- Test: `tests/test_web_plan.py`（追加）、`tests/test_export_static.py`（追加）

**Interfaces:**
- Consumes: `/api/holdings`、`/api/dashboard`（金额口径）
- Produces: `dsFetchHoldings()`；计划页渲染「股数」列与持仓跟踪表；dashboard 持仓卡改汇总。

- [ ] **Step 1: data-source.js 增加 dsFetchHoldings**

在 `dsFetchDashboard` 之后加：

```javascript
function dsFetchHoldings() {
  return isStatic()
    ? $.getJSON(dsPath('data/holdings.json'))
    : $.getJSON('/api/holdings');
}
```

- [ ] **Step 2: dashboard.js 持仓卡改汇总 + 收益卡金额**

把 `renderDashboard` 里 `opHtml` 改为：

```javascript
  const opHtml = op.count
    ? `<div><strong>${op.count}</strong> <small class="text-muted">只 · ${op.shares_total} 股</small></div>
       <small class="${(op.floating_pnl||0)>=0?'text-success':'text-danger'}">
         浮动 ${(op.floating_pnl||0)>=0?'+':''}${fmt(op.floating_pnl)} 元
       </small>
       <small class="text-muted d-block">均价浮动 ${op.avg_unrealized_pct==null?'—':(op.avg_unrealized_pct>=0?'+':'')+op.avg_unrealized_pct+'%'}</small>`
    : '<span class="text-muted">无持仓</span>';
```

把 `sparkline` 函数与 `pnlHtml` 的 `p.pnl_pct` / 单位 `%` 改为 `p.pnl_amt` / `元`：

```javascript
function sparkline(pnl) {
  if (!pnl.length) return '<span class="text-muted small">无</span>';
  const xs = [...pnl].reverse();
  const w = 80, h = 24, max = Math.max(...xs.map(p => Math.abs(p.pnl_amt)), 1);
  const step = w / Math.max(xs.length - 1, 1);
  const mid = h / 2;
  const pts = xs.map((p, i) => `${i * step},${mid - (p.pnl_amt / max) * mid}`).join(' ');
  const sum = xs.reduce((s, p) => s + p.pnl_amt, 0);
  const color = sum >= 0 ? '#198754' : '#dc3545';
  return `<svg width="${w}" height="${h}" style="vertical-align:middle"><polyline fill="none" stroke="${color}" stroke-width="1.5" points="${pts}"/></svg> <small class="${sum>=0?'text-success':'text-danger'}">${sum>=0?'+':''}${sum.toFixed(2)}元</small>`;
}
```

- [ ] **Step 3: 计划页表格加股数列**

`PLAN_SCRIPT` 的 `row()` 里，在仓位列（`sizePct`）之后加一列 `shares`：

```javascript
  var shares = r.shares == null ? '—' : r.shares;
  ...
    '<td class="num">'+sizePct+'</td>' +
    '<td class="num">'+shares+'</td>' +
```

对应 `drawPlan` 的表头 `<th class="num">仓位</th>` 后加 `<th class="num">股数</th>`；汇总行「买入合计仓位」改为同时显示总股数：

```javascript
    var buyShares = 0;
    rows.forEach(function(r){
      ...
      if (r.action === 'buy' && r.status === 'ok' && r.shares != null) buyShares += r.shares;
    });
    ...
    '<span class="text-muted me-3">买入合计仓位 '+ (buySize*100).toFixed(1) +'% · '+ buyShares +' 股</span>' +
```

- [ ] **Step 4: 计划页下方渲染持仓跟踪表**

`PLAN_BODY` 在 `<div id="board"></div>` 后加容器：

```html
  <h2 class="section-title mt-4">持仓跟踪</h2>
  <div id="holdings"></div>
```

`PLAN_SCRIPT` 加渲染函数并在 `$(function(){...})` 中调用：

```javascript
function drawHoldings(d){
  var rows = d.holdings || [];
  var s = d.summary || {};
  var sumHtml = '<div class="card mb-3"><div class="card-body py-2">' +
    '<span class="me-3"><strong>持仓 '+ s.open_count +'</strong></span>' +
    '<span class="me-3">总股数 '+ (s.shares_total||0) +'</span>' +
    '<span class="me-3">浮动 ' + (s.floating_pnl>=0?'+':'') + fmt(s.floating_pnl) + ' 元</span>' +
    '<span class="me-3">已实现 ' + (s.realized_pnl>=0?'+':'') + fmt(s.realized_pnl) + ' 元</span>' +
    '<span class="me-3"><strong>总收益 ' + (s.total_pnl>=0?'+':'') + fmt(s.total_pnl) + ' 元</strong></span>' +
    '<span class="'+(s.return_pct>=0?'text-success':'text-danger')+'">'+(s.return_pct>=0?'+':'')+fmt(s.return_pct)+'%</span>' +
    '</div></div>';
  if (!rows.length) { $('#holdings').html(sumHtml + '<div class="empty-state">暂无持仓</div>'); return; }
  var h = sumHtml + '<div class="table-responsive app-card"><table class="app-table"><thead><tr>' +
    '<th>代码</th><th>名称</th><th class="num">股数</th><th class="num">加权均价</th><th class="num">现价</th>' +
    '<th class="num">止损</th><th class="num">止盈</th><th class="num">浮动盈亏</th><th class="num">止损预期</th><th class="num">止盈预期</th>' +
    '</tr></thead><tbody>' +
    rows.map(function(r){
      return '<tr><td class="code">'+r.code+'</td><td>'+(r.name||'—')+'</td>' +
        '<td class="num">'+r.shares+'</td><td class="num">'+fmt(r.entry_price)+'</td><td class="num">'+fmt(r.current_price)+'</td>' +
        '<td class="num">'+fmt(r.stop_price)+'</td><td class="num">'+fmt(r.tp_price)+'</td>' +
        '<td class="num '+(r.floating_pnl>=0?'text-success':'text-danger')+'">'+(r.floating_pnl==null?'—':(r.floating_pnl>=0?'+':'')+fmt(r.floating_pnl))+'</td>' +
        '<td class="num '+(r.stop_pnl>=0?'text-success':'text-danger')+'">'+(r.stop_pnl==null?'—':(r.stop_pnl>=0?'+':'')+fmt(r.stop_pnl))+'</td>' +
        '<td class="num '+(r.tp_pnl>=0?'text-success':'text-danger')+'">'+(r.tp_pnl==null?'—':(r.tp_pnl>=0?'+':'')+fmt(r.tp_pnl))+'</td></tr>';
    }).join('') + '</tbody></table></div>';
  $('#holdings').html(h);
}
function loadHoldings(){ dsFetchHoldings().done(drawHoldings).fail(function(){ $('#holdings').html('<div class="text-muted small">持仓加载失败</div>'); }); }
```

在 `PLAN_SCRIPT` 的 `$(function(){...})` 里加 `loadHoldings();`（并在 `pollBuild` 成功回调里也调用 `loadHoldings()` 以在生成 plan 后刷新）。

- [ ] **Step 5: export_json 导出 holdings.json**

`export_json.py` 的 `export()` 里，`data_dir` JSON 导出块加：

```python
        json.dump(get_holdings_detail(conn), (data_dir / "holdings.json").open("w"),
                  ensure_ascii=False, default=str)
```

（`get_holdings_detail` 需已 import；`export()` 里 `conn` 尚在 `try` 块内，放在 `dashboard.json` 导出同处即可。）

- [ ] **Step 6: 写前端/导出测试**

```python
# tests/test_web_plan.py 追加
def test_plan_page_has_holdings_container(plan_db):
    app = create_app(db_path=plan_db)
    resp = app.test_client().get("/plan")
    assert b'id="holdings"' in resp.data
    assert b'dsFetchHoldings' in resp.data

# tests/test_export_static.py 追加
def test_export_emits_holdings_json(tmp_path):
    import os, tempfile
    from db_repository import open_db
    from export_json import export
    fd, db = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    open_db(db).close()
    out = str(tmp_path / "site")
    export(db, out)
    assert os.path.exists(os.path.join(out, "data", "holdings.json"))
```

- [ ] **Step 7: 跑测试确认通过**

Run: `uv run pytest tests/test_web_plan.py tests/test_export_static.py -v`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add app.py static/dashboard.js static/data-source.js export_json.py tests/test_web_plan.py tests/test_export_static.py
git commit -m "feat: 前端持仓跟踪表 + 股数列 + dashboard 金额汇总 + 静态 holdings 导出

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 10: 适配存量测试 + 全量回归

**Files:**
- Modify: `tests/test_db_repository.py`、`tests/test_plan_builder.py`、`tests/test_integration_plan.py`、`tests/test_integration.py`（按新语义适配）
- Test: 全量

**Interfaces:**
- 无新产出；让受金额/股数/sanity 语义影响的存量用例通过。

- [ ] **Step 1: 定位受影响用例**

Run: `uv run pytest -q`
Expected: 若干 FAIL，集中在：
- `test_db_repository.py::test_get_recent_pnl_realized_from_closed_positions`（pnl_pct → pnl_amt、closed 行需 shares）
- `test_db_repository.py` 的 `get_open_positions_with_unrealized` 两用例（需 shares）
- `test_plan_builder.py::test_sanity_gate_fails_size_exceed_max`（改为断言 `size_ref_warn` 且 status=ok）
- `test_plan_builder.py::test_sanity_gate_scales_total_overflow`（改为断言不缩放、全 ok）
- `test_plan_builder.py::test_exit_closes_position_and_records_event`（pnl_pct → pnl_amt，open 行带 shares）
- `test_plan_builder.py::test_buy_creates_open_position_and_event`（断言 shares=200）
- `test_integration_plan.py` 的幂等/去重/持仓 size 相关用例

- [ ] **Step 2: 逐个适配断言**

按新语义改写（要点）：
- `get_recent_pnl` 断言键 `pnl_pct` → `pnl_amt`，数值按 `(close−entry)×shares`。
- 所有手工 `INSERT INTO open_positions` 补 `shares` 列与值（200 或测试所需）。
- `test_sanity_gate_fails_size_exceed_max`：改名为 `test_sanity_gate_warns_size_ref`，断言 `buys[0].status == "ok"` 且 `"size_ref_warn" in result.sanity_reasons`。
- `test_sanity_gate_scales_total_overflow`：断言 `all(r.status == "ok")` 且无 `scaled_to_fit`。
- `test_exit_closes_position_and_records_event`：断言 `pnl_amt` 而非 `pnl_pct`。
- `test_integration_plan.py`：`test_rebuild_plan_is_idempotent` 仍应通过（同日幂等已改 trade_events 基准）；「held size 计入 max_total」的断言改为不再缩放即可通过。

- [ ] **Step 3: 跑全量确认通过**

Run: `uv run pytest -q`
Expected: 全部 PASS

- [ ] **Step 4: 真库冒烟**

Run:
```bash
uv run python -c "from db_repository import open_db, get_holdings_detail, get_recent_pnl; c=open_db('hs300.db'); print(get_holdings_detail(c)['summary']); print(get_recent_pnl(c, 5))"
```
Expected: `summary` 含 13 只、`shares_total==2600`、`floating_pnl` 有值；`get_recent_pnl` 返回金额列表。

- [ ] **Step 5: Commit**

```bash
git add tests/
git commit -m "test: 适配金额/股数/sanity 新语义，全量回归通过

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## 自审记录

- **Spec 覆盖**：迁移→数据模型（Task1）；PlanRow/plan 表 shares（Task2）；open_positions 累积（Task3）；buy 固定 200 + 累积 + 幂等（Task4）；close pnl_amt（Task5）；sanity 停缩放/降级（Task6）；金额口径收益 + 四层（Task7）；端点（Task8）；前端 + 导出（Task9）；回归（Task10）。四层收益、组合收益率分母、止损止盈复用 regime、存量回填均覆盖。
- **占位符扫描**：无 TBD/TODO；所有步骤含具体代码或精确命令。
- **类型一致性**：`shares`（int）贯穿 PlanRow/open_positions/trade_plan/trade_events；`pnl_amt`（REAL）仅在 trade_events 与返回 dict；`accumulate_open_position` 签名在 Task3 定义、Task4 调用，一致；`get_holdings_detail` 返回 `{holdings, summary}` 在 Task7/8/9 一致。
- **已知遗留**：`get_recent_pnl` 返回键 `pnl_amt` 与 dashboard.js `sparkline` 的 `p.pnl_amt` 对齐；`test_web_plan.py` 现有 `test_api_dashboard_returns_four_sections` 断言 `set(data.keys()) == {...}` 含新键 `holdings_summary`，会在 Task8 后失败——已并入 Task10 Step2 适配（在 keys 集合加 `"holdings_summary"`）。
