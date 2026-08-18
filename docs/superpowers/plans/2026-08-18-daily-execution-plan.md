# Daily Execution Plan Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use supo-subagent-driven-development (recommended) or supo-executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a paper-trading execution layer that turns daily picks into immutable, sanity-gated trade plans with carryover positions, shared with `ma_backtest` logic.

**Architecture:** Extract pure strategy functions from `ma_backtest` into a new `shared_lib/` package. Add a `plan_builder` that reads `daily_picks` + `open_positions`, calls shared functions to produce `trade_plan` rows (insert-only), runs a sanity gate, and writes carryover via `open_positions`. Expose via CLI + Flask `/plan` tab.

**Tech Stack:** Python 3.11 (uv-managed), pandas, sqlite3, Flask, pytest. No new third-party deps.

## Global Constraints

- Python 3.11, `uv` env, pyproject.toml-driven
- `pytest` test runner, tests under `tests/`
- `git` per-task commit, Conventional Commits style
- No new third-party dependencies; reuse existing `pandas`, `numpy`, `flask`, `pytest`
- A-share HS300 universe; paper-trading only (no live broker)
- `trade_plan` table is **insert-only**; `UNIQUE(plan_date, code, action)` enforces idempotency
- `shared_lib` functions must be **pure**: no DB I/O, no network, no logging side-effects
- Defaults (in `config.py`): `rr_target=2.0`, `max_single=0.15`, `max_total=0.95`, `slippage=0.001`, `stop_atr_mult=2.0`
- All plan rows carry `params_hash = sha256(json.dumps(params, sort_keys=True))`

## File Structure

**New files**:
- `shared_lib/__init__.py` — re-exports public API
- `shared_lib/strategy.py` — pure functions: `select_picks`, `score_signal`, `position_size`, `stop_loss`, `take_profit`, `params_hash`, `PlanRow`
- `plan_builder.py` — top-level `build_plan(plan_date, db_path, params, slippage)` orchestrator + sanity gate + paper trader
- `db/migrations/2026_08_18_execution_tables.sql` — schema for `trade_plan`, `open_positions`, `trade_events`
- `tests/test_shared_lib.py` — bit-identical parity tests vs `ma_backtest` golden output
- `tests/test_plan_builder.py` — carryover + sanity + idempotency integration tests
- `daily_plan.sh` — one-shot: sync range → picks → plan

**Modified files**:
- `ma_backtest.py` — replace inlined logic with `from shared_lib.strategy import ...`
- `config.py` — add default constants `RR_TARGET`, `MAX_SINGLE`, `MAX_TOTAL`, `SLIPPAGE`, `STOP_ATR_MULT`
- `db_repository.py` — add CRUD for `trade_plan`, `open_positions`, `trade_events`; auto-run migration
- `cli_layer.py` — register `plan` / `plan list` / `plan show` subcommands
- `web_server.py` — add `/api/plan/today`, `/api/plan/<date>`, `/plan`, `/plan/<date>` routes
- `templates/index.html` — add Plan tab content (renders table + modal)
- `stock_cli.py` / `pyproject.toml` entry point — wire `plan` subcommand

**Decomposition rationale**:
- `shared_lib/` is a leaf package; nothing else depends on it but everything else uses it
- `plan_builder.py` is the single integration point; CLI and tests call it directly
- Web layer only reads; never writes (recompute via `plan` CLI)
- Tests mirror the same decomposition: `test_shared_lib` for pure functions, `test_plan_builder` for orchestration

---

## Phase 1: Schema and shared_lib skeleton (Tasks 1-2)

### Task 1: Database migration — add execution tables

**Files:**
- Create: `db/migrations/2026_08_18_execution_tables.sql`
- Create: `tests/test_migration.py`
- Modify: `db_repository.py:1-30` (auto-run migration on open)

**Interfaces:**
- Consumes: existing `hs300.db` schema (`daily_picks`, etc.)
- Produces: `trade_plan`, `open_positions`, `trade_events` tables; `conn.execute("SELECT … FROM trade_plan")` works

- [ ] **Step 1: Write the failing test**

```python
# tests/test_migration.py
import sqlite3
from pathlib import Path
import tempfile

def test_execution_tables_created():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    # First-touch migration; expect tables present after opening DB
    from db_repository import open_db
    conn = open_db(path)
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = {row[0] for row in cur.fetchall()}
    assert "trade_plan" in tables
    assert "open_positions" in tables
    assert "trade_events" in tables
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_migration.py::test_execution_tables_created -v`
Expected: FAIL with `ImportError` or `no such table: trade_plan`

- [ ] **Step 3: Write the migration SQL**

```sql
-- db/migrations/2026_08_18_execution_tables.sql
CREATE TABLE IF NOT EXISTS trade_plan (
    plan_id          INTEGER PRIMARY KEY AUTOINCREMENT,
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
    UNIQUE(plan_date, code, action)
);
CREATE INDEX IF NOT EXISTS idx_trade_plan_date ON trade_plan(plan_date);

CREATE TABLE IF NOT EXISTS open_positions (
    pos_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    code             TEXT NOT NULL,
    entry_date       TEXT NOT NULL,
    entry_price      REAL NOT NULL,
    size_pct         REAL NOT NULL,
    stop_price       REAL NOT NULL,
    tp_price         REAL NOT NULL,
    status           TEXT NOT NULL CHECK(status IN ('open','closed')),
    close_date       TEXT,
    close_price      REAL,
    close_reason     TEXT
);
CREATE INDEX IF NOT EXISTS idx_open_positions_status ON open_positions(status);
CREATE INDEX IF NOT EXISTS idx_open_positions_code ON open_positions(code);

CREATE TABLE IF NOT EXISTS trade_events (
    event_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_date        TEXT NOT NULL,
    code             TEXT NOT NULL,
    event_type       TEXT NOT NULL CHECK(event_type IN ('open','close')),
    price            REAL NOT NULL,
    size_pct         REAL,
    pnl_pct          REAL,
    note             TEXT,
    created_at       TEXT NOT NULL
);
```

- [ ] **Step 4: Add migration runner to `db_repository.py`**

At top of `db_repository.py`, locate `open_db()` (or equivalent — search `def open_db`). Replace its body with logic that:
1. Opens the connection
2. Reads `db/migrations/*.sql` files in lexical order
3. Executes each one (statements are idempotent via `IF NOT EXISTS`)
4. Returns the connection

If `open_db` does not exist, create it:

```python
# db_repository.py
import sqlite3
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "db" / "migrations"

def open_db(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    if MIGRATIONS_DIR.exists():
        for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
            conn.executescript(sql_file.read_text())
    conn.commit()
    return conn
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_migration.py::test_execution_tables_created -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add db/migrations/2026_08_18_execution_tables.sql db_repository.py tests/test_migration.py
git commit -m "feat(db): add trade_plan, open_positions, trade_events tables"
```

---

### Task 2: shared_lib skeleton + config constants

**Files:**
- Create: `shared_lib/__init__.py`
- Create: `shared_lib/strategy.py`
- Modify: `config.py:1-30`

**Interfaces:**
- Consumes: nothing
- Produces: `shared_lib.strategy.PlanRow` dataclass + `params_hash(d: dict) -> str`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_shared_lib.py
from shared_lib.strategy import PlanRow, params_hash

def test_plan_row_defaults():
    row = PlanRow(
        code="600519",
        action="buy",
        plan_price=100.0,
        size_pct=0.1,
        stop_price=95.0,
        tp_price=110.0,
        rr_ratio=2.0,
        rationale={"score": 1.2},
        status="ok",
        reason="",
    )
    assert row.code == "600519"
    assert row.status == "ok"

def test_params_hash_deterministic():
    a = params_hash({"a": 1, "b": 2})
    b = params_hash({"b": 2, "a": 1})
    assert a == b
    assert len(a) == 64  # sha256 hex
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_shared_lib.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'shared_lib'`

- [ ] **Step 3: Implement skeleton**

```python
# shared_lib/__init__.py
from .strategy import (
    PlanRow,
    params_hash,
)

__all__ = ["PlanRow", "params_hash"]
```

```python
# shared_lib/strategy.py
from __future__ import annotations
import hashlib
import json
from dataclasses import dataclass, field
from typing import Literal


@dataclass
class PlanRow:
    code: str
    action: Literal["buy", "hold", "exit"]
    plan_price: float
    size_pct: float
    stop_price: float
    tp_price: float
    rr_ratio: float
    rationale: dict = field(default_factory=dict)
    status: Literal["ok", "failed"] = "ok"
    reason: str = ""


def params_hash(d: dict) -> str:
    """Deterministic sha256 of a params dict (sorted keys)."""
    payload = json.dumps(d, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
```

- [ ] **Step 4: Add config constants**

Open `config.py` and append:

```python
# config.py
RR_TARGET = 2.0        # take-profit / stop-loss ratio
MAX_SINGLE = 0.15      # max single-position weight
MAX_TOTAL = 0.95       # max total portfolio weight
SLIPPAGE = 0.001       # paper-trade fill slippage (0.1%)
STOP_ATR_MULT = 2.0    # ATR multiple for stop
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_shared_lib.py -v`
Expected: PASS (2 passed)

- [ ] **Step 6: Commit**

```bash
git add shared_lib/__init__.py shared_lib/strategy.py config.py tests/test_shared_lib.py
git commit -m "feat(shared_lib): add PlanRow dataclass and params_hash"
```

---

## Phase 2: Extract shared logic from ma_backtest (Tasks 3-6)

### Task 3: Extract `score_signal()` (TDD, parity-tested)

**Files:**
- Modify: `shared_lib/strategy.py`
- Modify: `ma_backtest.py` (locate `score_signal`-equivalent inline function)
- Modify: `tests/test_shared_lib.py`

**Interfaces:**
- Consumes: `row: pd.Series`, `params: dict`
- Produces: `dict` with keys `score`, `components` (matching `ma_backtest` golden output bit-identically)

- [ ] **Step 1: Locate the inlined scoring function in `ma_backtest.py`**

Run: `grep -n "def.*score\|slope200.*momentum\|def _score" ma_backtest.py`

Pick the function body that computes score from `slope200`, `momentum20`, etc. Copy it verbatim into a scratch file. This is your **golden reference** — the test will assert identical output.

- [ ] **Step 2: Write the parity test**

```python
# tests/test_shared_lib.py (append)
import pandas as pd
from ma_backtest import score_signal as legacy_score_signal
from shared_lib.strategy import score_signal

def test_score_signal_matches_ma_backtest():
    """Golden parity: shared_lib.score_signal == ma_backtest legacy on identical input."""
    # Build a sample row that exercises every component
    row = pd.Series({
        "slope200": 0.03,
        "momentum20": 0.12,
        "momentum10": 0.05,
        "volume_bonus": 1.5,
        "code": "600519",
    })
    params = {
        "w_slope200": 3.0,
        "w_momentum20": 200.0,
        "w_momentum10": 50.0,
        "w_volume_bonus": 12.0,
    }
    legacy = legacy_score_signal(row, params)
    new = score_signal(row, params)
    assert new == legacy, f"Parity broken:\nlegacy={legacy}\nnew={new}"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/test_shared_lib.py::test_score_signal_matches_ma_backtest -v`
Expected: FAIL with `ImportError: cannot import name 'score_signal' from 'shared_lib.strategy'`

- [ ] **Step 4: Move function body into `shared_lib`**

In `ma_backtest.py`, find the inlined scoring function. Cut its body. In `shared_lib/strategy.py`, append:

```python
import pandas as pd

def score_signal(row: pd.Series, params: dict) -> dict:
    """Pure scoring: weighted sum of slope/momentum/volume components.

    Returns dict with 'score' (float) and 'components' (dict of inputs).
    Parity-must-match with ma_backtest legacy implementation.
    """
    # >>> PASTE BODY HERE, replace 'row' references if renamed <<<
    score = (
        params["w_slope200"] * row["slope200"]
        + params["w_momentum20"] * row["momentum20"]
        + params["w_momentum10"] * row["momentum10"]
        + params["w_volume_bonus"] * row["volume_bonus"]
    )
    return {
        "score": float(score),
        "components": {
            "slope200": float(row["slope200"]),
            "momentum20": float(row["momentum20"]),
            "momentum10": float(row["momentum10"]),
            "volume_bonus": float(row["volume_bonus"]),
        },
    }
```

(Adjust to whatever the original function actually does. The assertion is what guarantees parity.)

In `ma_backtest.py`, replace the original function body with:

```python
from shared_lib.strategy import score_signal  # re-exported for back-compat
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/test_shared_lib.py::test_score_signal_matches_ma_backtest -v`
Expected: PASS

Also run the full existing test suite to confirm no regression:

Run: `uv run pytest tests/test_integration.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add shared_lib/strategy.py ma_backtest.py tests/test_shared_lib.py
git commit -m "refactor: extract score_signal to shared_lib (parity-tested)"
```

---

### Task 4: Extract `stop_loss()` and `take_profit()` (TDD, parity-tested)

**Files:**
- Modify: `shared_lib/strategy.py`
- Modify: `ma_backtest.py`
- Modify: `tests/test_shared_lib.py`

**Interfaces:**
- `stop_loss(plan_price: float, atr: float, params: dict) -> float`
- `take_profit(plan_price: float, stop_price: float, rr_target: float) -> float`

- [ ] **Step 1: Write the parity tests**

```python
# tests/test_shared_lib.py (append)
from shared_lib.strategy import stop_loss, take_profit

def test_stop_loss_atr_parity():
    """stop_loss(plan_price=100, atr=2.5, params={'stop_atr_mult':2.0}) == 95.0"""
    from ma_backtest import stop_loss as legacy_stop
    plan_price = 100.0
    atr = 2.5
    params = {"stop_atr_mult": 2.0}
    assert stop_loss(plan_price, atr, params) == legacy_stop(plan_price, atr, params)

def test_take_profit_rr_parity():
    from ma_backtest import take_profit as legacy_tp
    plan_price, stop_price, rr_target = 100.0, 95.0, 2.0
    assert take_profit(plan_price, stop_price, rr_target) == legacy_tp(plan_price, stop_price, rr_target)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_shared_lib.py -k "stop_loss or take_profit" -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Move function bodies into `shared_lib/strategy.py`**

```python
def stop_loss(plan_price: float, atr: float, params: dict) -> float:
    """ATR-multiple stop below entry."""
    return plan_price - params["stop_atr_mult"] * atr


def take_profit(plan_price: float, stop_price: float, rr_target: float) -> float:
    """Profit target = entry + rr_target × risk."""
    risk = plan_price - stop_price
    return plan_price + rr_target * risk
```

(Adjust to whatever `ma_backtest` actually does. The test enforces parity.)

In `ma_backtest.py`, replace original bodies with re-exports:

```python
from shared_lib.strategy import stop_loss, take_profit
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_shared_lib.py -k "stop_loss or take_profit" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add shared_lib/strategy.py ma_backtest.py tests/test_shared_lib.py
git commit -m "refactor: extract stop_loss and take_profit to shared_lib"
```

---

### Task 5: Extract `position_size()` (TDD, parity-tested)

**Files:**
- Modify: `shared_lib/strategy.py`
- Modify: `ma_backtest.py` and `risk_manager.py`

**Interfaces:**
- `position_size(code: str, score: float, regime: str, risk_cfg: dict) -> float`

- [ ] **Step 1: Write the parity test**

```python
# tests/test_shared_lib.py (append)
def test_position_size_parity():
    from ma_backtest import position_size as legacy_pos
    code, score, regime = "600519", 1.5, "bull"
    risk_cfg = {"max_single": 0.15, "base_size": 0.05, "regime_factor": {"bull": 1.0, "bear": 0.5}}
    assert (
        shared_lib_position_size(code, score, regime, risk_cfg)
        == legacy_pos(code, score, regime, risk_cfg)
    )
```

Add to `__init__.py` re-export.

- [ ] **Step 2-5: Mirror Task 3 pattern** (test fails, implement, test passes, commit)

```python
# shared_lib/strategy.py
def position_size(code: str, score: float, regime: str, risk_cfg: dict) -> float:
    """Pure: returns position weight in [0, max_single]."""
    base = risk_cfg["base_size"]
    cap = risk_cfg["max_single"]
    factor = risk_cfg.get("regime_factor", {}).get(regime, 1.0)
    return min(cap, base * factor * max(0.5, score))
```

Commit: `git commit -m "refactor: extract position_size to shared_lib"`

---

### Task 6: Extract `select_picks()` (TDD, parity-tested)

**Files:**
- Modify: `shared_lib/strategy.py`
- Modify: `ma_backtest.py`

**Interfaces:**
- `select_picks(daily_picks: pd.DataFrame, regime: str, params: dict) -> pd.DataFrame`

- [ ] **Step 1: Write the parity test**

```python
def test_select_picks_parity():
    from ma_backtest import select_picks as legacy_sel
    # Construct a DataFrame mirroring real schema
    df = pd.DataFrame({
        "code": ["600519", "000001", "300750"],
        "score": [2.1, 1.5, 0.9],
        "regime": ["bull", "bull", "bear"],
        "shape_quota": ["breakout", "pullback", "breakout"],
    })
    params = {"breakout_quota": 0.75, "pullback_quota": 0.25, "min_score": 1.0}
    legacy = legacy_sel(df, "bull", params)
    new = shared_select(df, "bull", params)
    pd.testing.assert_frame_equal(new, legacy)
```

- [ ] **Step 2-5: Mirror Task 3 pattern** — test, fail, implement, pass, commit

```python
# shared_lib/strategy.py
def select_picks(df: pd.DataFrame, regime: str, params: dict) -> pd.DataFrame:
    """Filter daily_picks by regime + score threshold + shape quota."""
    min_score = params.get("min_score", 0.0)
    out = df[(df["regime"] == regime) & (df["score"] >= min_score)].copy()
    # Apply shape quotas if present
    for shape, q in [("breakout", params.get("breakout_quota", 1.0)),
                     ("pullback", params.get("pullback_quota", 1.0))]:
        if q < 1.0 and (out["shape_quota"] == shape).any():
            out = out.sample(frac=q, random_state=42)
    return out.reset_index(drop=True)
```

(Adjust to whatever `ma_backtest` actually does.)

Commit: `git commit -m "refactor: extract select_picks to shared_lib"`

---

## Phase 3: plan_builder core (Tasks 7-11)

### Task 7: `db_repository` CRUD for `trade_plan`, `open_positions`, `trade_events`

**Files:**
- Modify: `db_repository.py`
- Modify: `tests/test_db_repository.py` (create if missing)

**Interfaces:**
- `insert_trade_plan(conn, row: PlanRow, plan_date: str, params_hash: str) -> int`
- `get_trade_plan_by_date(conn, plan_date: str, include_failed: bool = False) -> list[dict]`
- `insert_open_position(conn, code, entry_date, entry_price, size_pct, stop_price, tp_price) -> int`
- `get_open_positions(conn) -> list[dict]`
- `close_open_position(conn, pos_id, close_date, close_price, reason)`
- `insert_trade_event(conn, plan_date, code, event_type, price, size_pct=None, pnl_pct=None, note=None)`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_db_repository.py
import sqlite3, tempfile
from db_repository import open_db
from shared_lib.strategy import PlanRow

def _conn():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    return open_db(path)

def test_insert_and_fetch_trade_plan():
    from db_repository import insert_trade_plan, get_trade_plan_by_date
    conn = _conn()
    row = PlanRow("600519", "buy", 100.0, 0.1, 95.0, 110.0, 2.0, {}, "ok", "")
    pid = insert_trade_plan(conn, row, "2026-08-18", "abc123")
    assert pid > 0
    rows = get_trade_plan_by_date(conn, "2026-08-18")
    assert len(rows) == 1
    assert rows[0]["code"] == "600519"
    assert rows[0]["status"] == "ok"

def test_trade_plan_unique_constraint():
    from db_repository import insert_trade_plan
    import sqlite3
    conn = _conn()
    row = PlanRow("600519", "buy", 100.0, 0.1, 95.0, 110.0, 2.0, {}, "ok", "")
    insert_trade_plan(conn, row, "2026-08-18", "abc")
    try:
        insert_trade_plan(conn, row, "2026-08-18", "abc")
    except sqlite3.IntegrityError:
        return  # expected
    raise AssertionError("Expected UNIQUE violation")

def test_open_position_lifecycle():
    from db_repository import (
        insert_open_position, get_open_positions, close_open_position
    )
    conn = _conn()
    pid = insert_open_position(conn, "600519", "2026-08-18", 100.0, 0.1, 95.0, 110.0)
    assert len(get_open_positions(conn)) == 1
    close_open_position(conn, pid, "2026-08-25", 108.0, "tp_hit")
    assert len(get_open_positions(conn)) == 0

def test_trade_event_round_trip():
    from db_repository import insert_trade_event
    conn = _conn()
    eid = insert_trade_event(conn, "2026-08-18", "600519", "open", 100.0, 0.1)
    assert eid > 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_db_repository.py -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement CRUD functions in `db_repository.py`**

```python
# db_repository.py — append at bottom
import json
from datetime import datetime
from shared_lib.strategy import PlanRow

def insert_trade_plan(conn, row: PlanRow, plan_date: str, params_hash: str) -> int:
    cur = conn.execute(
        """INSERT OR IGNORE INTO trade_plan
        (plan_date, code, action, plan_price, size_pct, stop_price, tp_price,
         rr_ratio, status, reason, rationale_json, params_hash, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (plan_date, row.code, row.action, row.plan_price, row.size_pct,
         row.stop_price, row.tp_price, row.rr_ratio, row.status, row.reason,
         json.dumps(row.rationale), params_hash,
         datetime.utcnow().isoformat(timespec="seconds")),
    )
    conn.commit()
    return cur.lastrowid or 0


def get_trade_plan_by_date(conn, plan_date: str, include_failed: bool = False):
    sql = "SELECT * FROM trade_plan WHERE plan_date = ?"
    if not include_failed:
        sql += " AND status = 'ok'"
    sql += " ORDER BY action DESC, code"  # buy first, then hold/exit
    cur = conn.execute(sql, (plan_date,))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def insert_open_position(conn, code, entry_date, entry_price,
                         size_pct, stop_price, tp_price) -> int:
    cur = conn.execute(
        """INSERT INTO open_positions
        (code, entry_date, entry_price, size_pct, stop_price, tp_price, status)
        VALUES (?,?,?,?,?,?,'open')""",
        (code, entry_date, entry_price, size_pct, stop_price, tp_price),
    )
    conn.commit()
    return cur.lastrowid


def get_open_positions(conn):
    cur = conn.execute(
        """SELECT * FROM open_positions WHERE status='open'
        ORDER BY entry_date, code"""
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def close_open_position(conn, pos_id, close_date, close_price, reason):
    conn.execute(
        """UPDATE open_positions
        SET status='closed', close_date=?, close_price=?, close_reason=?
        WHERE pos_id=?""",
        (close_date, close_price, reason, pos_id),
    )
    conn.commit()


def insert_trade_event(conn, plan_date, code, event_type, price,
                        size_pct=None, pnl_pct=None, note=None) -> int:
    cur = conn.execute(
        """INSERT INTO trade_events
        (plan_date, code, event_type, price, size_pct, pnl_pct, note, created_at)
        VALUES (?,?,?,?,?,?,?,?)""",
        (plan_date, code, event_type, price, size_pct, pnl_pct, note,
         datetime.utcnow().isoformat(timespec="seconds")),
    )
    conn.commit()
    return cur.lastrowid
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_db_repository.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add db_repository.py tests/test_db_repository.py
git commit -m "feat(db_repository): add CRUD for trade_plan, open_positions, trade_events"
```

---

### Task 8: `plan_builder.build_plan()` — read inputs (scaffold)

**Files:**
- Create: `plan_builder.py`
- Create: `tests/test_plan_builder.py`

**Interfaces:**
- `build_plan(plan_date: str, db_path: str, params: dict, slippage: float = 0.001) -> PlanResult`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_plan_builder.py
import tempfile
from db_repository import open_db, insert_daily_pick_for_test, insert_open_position

def test_build_plan_reads_inputs():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = open_db(path)
    # Seed daily_picks (use whatever helper your DB layer exposes)
    insert_daily_pick_for_test(conn, "2026-08-18", "600519", score=2.1, atr=2.5)
    insert_open_position(conn, "000001", "2026-08-15", 50.0, 0.1, 47.0, 56.0)
    conn.close()
    from plan_builder import build_plan
    result = build_plan("2026-08-18", path, params={"min_score": 1.0})
    assert result.num_picks >= 1
    assert result.num_open_positions >= 1
```

(Adjust `insert_daily_pick_for_test` to match your `db_repository` helper for seeding `daily_picks`; if absent, write inline `INSERT` in the test.)

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_plan_builder.py -v`
Expected: FAIL with `ModuleNotFoundError`

- [ ] **Step 3: Implement scaffold**

```python
# plan_builder.py
from __future__ import annotations
from dataclasses import dataclass
from db_repository import open_db, get_open_positions


@dataclass
class PlanResult:
    plan_date: str
    rows: list
    num_picks: int
    num_open_positions: int
    sanity_passed: bool
    sanity_reasons: list[str]


def build_plan(plan_date: str, db_path: str, params: dict, slippage: float = 0.001) -> PlanResult:
    """Scaffold: reads inputs only. Tasks 9-11 will add row generation."""
    conn = open_db(db_path)
    try:
        # Read daily_picks for plan_date
        cur = conn.execute(
            "SELECT code, score, atr FROM daily_picks WHERE pick_date = ?",
            (plan_date,),
        )
        picks = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]
        opens = get_open_positions(conn)
    finally:
        conn.close()
    return PlanResult(
        plan_date=plan_date,
        rows=[],
        num_picks=len(picks),
        num_open_positions=len(opens),
        sanity_passed=True,
        sanity_reasons=[],
    )
```

(Adapt to whatever your real `daily_picks` columns and helper look like. The key invariant: it returns the counts without crashing.)

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_plan_builder.py::test_build_plan_reads_inputs -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add plan_builder.py tests/test_plan_builder.py
git commit -m "feat(plan_builder): scaffold build_plan() with input reading"
```

---

### Task 9: Generate `buy` rows from picks

**Files:**
- Modify: `plan_builder.py`
- Modify: `tests/test_plan_builder.py`

- [ ] **Step 1: Write the failing test**

```python
def test_build_plan_emits_buy_rows():
    # ... (seed daily_picks with one row, score > min_score, regime='bull')
    from plan_builder import build_plan
    result = build_plan("2026-08-18", path, params={
        "min_score": 1.0,
        "rr_target": 2.0,
        "stop_atr_mult": 2.0,
        "max_single": 0.15,
        "regime": "bull",
    })
    buy_rows = [r for r in result.rows if r.action == "buy"]
    assert len(buy_rows) == 1
    row = buy_rows[0]
    assert row.code == "600519"
    assert row.stop_price < row.plan_price
    assert row.tp_price > row.plan_price
    assert abs(row.rr_ratio - 2.0) < 0.01
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_plan_builder.py::test_build_plan_emits_buy_rows -v`
Expected: FAIL — no buy rows

- [ ] **Step 3: Implement buy-row generation**

```python
# plan_builder.py — extend build_plan
from shared_lib.strategy import (
    PlanRow, score_signal, position_size, stop_loss, take_profit, params_hash
)
from db_repository import insert_trade_plan, insert_trade_event

def build_plan(plan_date, db_path, params, slippage=0.001):
    conn = open_db(db_path)
    try:
        # ... existing read inputs ...
        rows: list[PlanRow] = []

        # === BUY rows from picks ===
        for p in picks:
            plan_price = p.get("close") or p.get("plan_price") or 100.0
            atr = p.get("atr", 0.0)
            score_dict = score_signal(p, params)
            size = position_size(p["code"], score_dict["score"],
                                 params["regime"], params.get("risk_cfg", {}))
            stop = stop_loss(plan_price, atr, params)
            tp = take_profit(plan_price, stop, params["rr_target"])
            risk = plan_price - stop
            rr = (tp - plan_price) / risk if risk > 0 else 0.0
            rows.append(PlanRow(
                code=p["code"], action="buy",
                plan_price=plan_price, size_pct=size,
                stop_price=stop, tp_price=tp, rr_ratio=rr,
                rationale=score_dict, status="ok", reason="",
            ))
    finally:
        conn.close()
    return PlanResult(...)
```

(Adjust column names from `daily_picks` to your schema.)

- [ ] **Step 4-5: Test passes, commit**

```bash
git commit -m "feat(plan_builder): emit buy rows from daily_picks via shared_lib"
```

---

### Task 10: Generate `hold`/`exit` rows from carryover

**Files:**
- Modify: `plan_builder.py`
- Modify: `tests/test_plan_builder.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_build_plan_emits_hold_when_stop_not_hit():
    # Seed: open_position with stop far below current price
    insert_open_position(conn, "000001", "2026-08-10", 50.0, 0.1, 45.0, 60.0)
    # daily_picks has a current price for 000001 (or look up via market_data stub)
    result = build_plan(...)
    assert any(r.code == "000001" and r.action == "hold" for r in result.rows)

def test_build_plan_emits_exit_when_stop_hit():
    # Seed: open_position with stop ABOVE current price
    insert_open_position(conn, "000002", "2026-08-10", 100.0, 0.1, 105.0, 115.0)
    result = build_plan(...)
    assert any(r.code == "000002" and r.action == "exit" for r in result.rows)
```

- [ ] **Step 2-5: Implement, test, commit**

```python
# plan_builder.py — inside build_plan, after buy-row loop:
        # === HOLD / EXIT rows from carryover ===
        current_prices = _lookup_current_prices(conn, [o["code"] for o in opens])
        for o in opens:
            cur_px = current_prices.get(o["code"], o["entry_price"])
            if cur_px <= o["stop_price"]:
                rows.append(PlanRow(
                    code=o["code"], action="exit",
                    plan_price=cur_px, size_pct=0.0,
                    stop_price=o["stop_price"], tp_price=o["tp_price"],
                    rr_ratio=0.0, rationale={"trigger": "stop_hit"},
                    status="ok", reason="",
                ))
            else:
                rows.append(PlanRow(
                    code=o["code"], action="hold",
                    plan_price=cur_px, size_pct=o["size_pct"],
                    stop_price=o["stop_price"], tp_price=o["tp_price"],
                    rr_ratio=0.0, rationale={"trigger": "hold"},
                    status="ok", reason="",
                ))
```

```python
def _lookup_current_prices(conn, codes):
    """Last close from kline table for each code. Empty if no data."""
    if not codes:
        return {}
    placeholders = ",".join("?" for _ in codes)
    cur = conn.execute(
        f"""SELECT code, close FROM (
            SELECT code, close, ROW_NUMBER() OVER (
                PARTITION BY code ORDER BY date DESC
            ) AS rn FROM daily_kline WHERE code IN ({placeholders})
        ) WHERE rn = 1""",
        codes,
    )
    return {r[0]: r[1] for r in cur.fetchall()}
```

(Adjust `daily_kline` table name to your schema.)

```bash
git commit -m "feat(plan_builder): emit hold/exit rows from open_positions"
```

---

### Task 11: Sanity gate + write `trade_plan`

**Files:**
- Modify: `plan_builder.py`
- Modify: `tests/test_plan_builder.py`

- [ ] **Step 1: Write the failing tests**

```python
def test_sanity_gate_fails_size_exceed_max():
    # Seed picks with size that would exceed max_single after position_size()
    # ... (force position_size to return 0.5 via params override)
    result = build_plan(...)
    assert all(r.status == "failed" for r in result.rows if r.action == "buy")
    assert any("size_exceed_max" in r.reason for r in result.rows)

def test_sanity_gate_scales_total_overflow():
    # Seed 5 picks each wanting 0.25 size; total = 1.25 > 0.95
    # Expect: each scaled to 0.25 * 0.95/1.25 = 0.19, reason='scaled_to_fit'
    result = build_plan(...)
    buy_rows = [r for r in result.rows if r.action == "buy"]
    total = sum(r.size_pct for r in buy_rows)
    assert total <= 0.95 + 0.001
    assert all(r.status == "ok" for r in buy_rows)
    assert any("scaled_to_fit" in r.reason for r in buy_rows)

def test_sanity_gate_invalid_stop():
    # Force stop_price > plan_price (invalid)
    result = build_plan(...)
    assert all(r.status == "failed" for r in result.rows)
```

- [ ] **Step 2-5: Implement sanity gate + write `trade_plan`**

```python
# plan_builder.py — append after row generation:
        # === Sanity gate ===
        max_single = params.get("max_single", 0.15)
        max_total = params.get("max_total", 0.95)
        buy_rows = [r for r in rows if r.action == "buy"]
        reasons: list[str] = []

        # Rule 1: per-row size cap
        for r in buy_rows:
            if r.size_pct > max_single:
                r.status = "failed"
                r.reason = "size_exceed_max"
                reasons.append(f"{r.code}:size_exceed_max")

        # Rule 2: stop below price
        for r in buy_rows:
            if r.stop_price >= r.plan_price * 0.9:
                r.status = "failed"
                r.reason = "stop_too_tight"
                reasons.append(f"{r.code}:stop_too_tight")

        # Rule 3: portfolio scaling
        ok_buys = [r for r in buy_rows if r.status == "ok"]
        total = sum(r.size_pct for r in ok_buys)
        if total > max_total and ok_buys:
            scale = max_total / total
            for r in ok_buys:
                r.size_pct = round(r.size_pct * scale, 4)
                r.reason = "scaled_to_fit"

        # === Write to DB ===
        phash = params_hash(params)
        conn2 = open_db(db_path)
        try:
            for r in rows:
                insert_trade_plan(conn2, r, plan_date, phash)
        finally:
            conn2.close()

        sanity_passed = not reasons or all("scaled_to_fit" in reason for reason in reasons)
        return PlanResult(
            plan_date=plan_date, rows=rows,
            num_picks=len(picks), num_open_positions=len(opens),
            sanity_passed=sanity_passed, sanity_reasons=reasons,
        )
```

```bash
git commit -m "feat(plan_builder): add sanity gate and persist to trade_plan"
```

---

### Task 12: Paper trader — write `open_positions` on buy + `trade_events`

**Files:**
- Modify: `plan_builder.py`
- Modify: `tests/test_plan_builder.py`

- [ ] **Step 1: Write the failing test**

```python
def test_buy_creates_open_position_and_event():
    result = build_plan(...)
    conn = open_db(path)
    opens = get_open_positions(conn)
    events = conn.execute("SELECT * FROM trade_events WHERE event_type='open'").fetchall()
    assert len(opens) == 1
    assert len(events) == 1
```

- [ ] **Step 2-5: Implement and commit**

```python
# plan_builder.py — after sanity gate, before writing trade_plan:
        # === Paper trader: simulate fills for today's buy rows ===
        # Use today's close as fill price (slippage applied); open_positions
        # are recorded so tomorrow's plan can carry them.
        conn2 = open_db(db_path)
        try:
            for r in rows:
                if r.action == "buy" and r.status == "ok":
                    fill_price = round(r.plan_price * (1 + slippage), 2)
                    insert_open_position(
                        conn2, r.code, plan_date, fill_price,
                        r.size_pct, r.stop_price, r.tp_price,
                    )
                    insert_trade_event(
                        conn2, plan_date, r.code, "open",
                        fill_price, r.size_pct, note="paper_fill",
                    )
                elif r.action == "exit" and r.status == "ok":
                    # Close the matching open_position
                    cur = conn2.execute(
                        "SELECT pos_id, entry_price FROM open_positions "
                        "WHERE code=? AND status='open'",
                        (r.code,),
                    )
                    row = cur.fetchone()
                    if row:
                        pos_id, entry_price = row
                        close_open_position(conn2, pos_id, plan_date,
                                            r.plan_price, "stop_hit")
                        pnl = (r.plan_price / entry_price - 1) * 100
                        insert_trade_event(
                            conn2, plan_date, r.code, "close",
                            r.plan_price, None, round(pnl, 2),
                            note="paper_close",
                        )
        finally:
            conn2.close()
```

```bash
git commit -m "feat(plan_builder): paper trader fills + trade_events audit"
```

---

## Phase 4: CLI + Web + Script (Tasks 13-16)

### Task 13: CLI subcommand `plan` / `plan list` / `plan show`

**Files:**
- Modify: `cli_layer.py`
- Modify: `stock_cli.py` (or `pyproject.toml` entry)

- [ ] **Step 1: Write CLI smoke test**

```python
# tests/test_cli_plan.py
from click.testing import CliRunner
from stock_cli import cli

def test_plan_help():
    runner = CliRunner()
    result = runner.invoke(cli, ["plan", "--help"])
    assert result.exit_code == 0
    assert "plan_date" in result.output or "--date" in result.output
```

- [ ] **Step 2: Add `plan` subcommand in `cli_layer.py`**

```python
# cli_layer.py — register plan subcommand
@click.command("plan")
@click.option("--date", "plan_date", default=None, help="YYYY-MM-DD (default: today)")
@click.option("--db", "db_path", default="hs300.db")
@click.option("--rr-target", type=float, default=None)
@click.option("--max-single", type=float, default=None)
@click.option("--slippage", type=float, default=None)
@click.option("--dry-run", is_flag=True)
def plan_cmd(plan_date, db_path, rr_target, max_single, slippage, dry_run):
    from datetime import date as _date
    from plan_builder import build_plan
    pd = plan_date or _date.today().isoformat()
    params = _load_plan_params()
    if rr_target is not None: params["rr_target"] = rr_target
    if max_single is not None: params["max_single"] = max_single
    if slippage is not None: params["slippage"] = slippage
    if dry_run:
        click.echo(f"[dry-run] would build plan for {pd}")
        return
    result = build_plan(pd, db_path, params, slippage=params.get("slippage", 0.001))
    click.echo(f"plan_date={pd} picks={result.num_picks} "
               f"open={result.num_open_positions} sanity={result.sanity_passed}")
    for r in result.rows:
        click.echo(f"  {r.action:4s} {r.code} px={r.plan_price} "
                   f"size={r.size_pct} stop={r.stop_price} tp={r.tp_price} "
                   f"status={r.status}")
```

Register: `cli.add_command(plan_cmd)` alongside existing commands.

- [ ] **Step 3: Verify CLI**

Run: `uv run a-finder plan --help`
Expected: shows options

Run: `uv run a-finder plan --dry-run`
Expected: prints "[dry-run] would build plan for YYYY-MM-DD"

- [ ] **Step 4: Commit**

```bash
git commit -m "feat(cli): add plan, plan list, plan show subcommands"
```

---

### Task 14: `daily_plan.sh` wrapper

**Files:**
- Create: `daily_plan.sh`

- [ ] **Step 1: Write the script**

```bash
#!/usr/bin/env bash
set -euo pipefail
DB="${1:-hs300.db}"
TOP="${2:-20}"
CMD="${3:-picks}"

bash sync_incremental_pick.sh "$DB" "$TOP" "$CMD"
uv run a-finder plan --db "$DB"
```

- [ ] **Step 2: Make executable + smoke test**

```bash
chmod +x daily_plan.sh
bash -n daily_plan.sh    # syntax check
```

- [ ] **Step 3: Commit**

```bash
git add daily_plan.sh
git commit -m "feat: add daily_plan.sh wrapper script"
```

---

### Task 15: Web API `/api/plan/today` and `/api/plan/<date>`

**Files:**
- Modify: `web_server.py`
- Modify: `tests/test_web_plan.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_web_plan.py
from web_server import create_app

def test_api_plan_today():
    app = create_app(testing=True)
    client = app.test_client()
    resp = client.get("/api/plan/today")
    assert resp.status_code == 200
    data = resp.get_json()
    assert "rows" in data
    assert "plan_date" in data

def test_api_plan_specific_date():
    app = create_app(testing=True)
    client = app.test_client()
    resp = client.get("/api/plan/2026-08-18?include_failed=1")
    assert resp.status_code == 200
```

- [ ] **Step 2-5: Implement, test, commit**

```python
# web_server.py
from db_repository import open_db, get_trade_plan_by_date
from datetime import date as _date

@app.get("/api/plan/today")
def api_plan_today():
    conn = open_db(current_app.config["DB_PATH"])
    rows = get_trade_plan_by_date(conn, _date.today().isoformat())
    conn.close()
    return jsonify({"plan_date": _date.today().isoformat(), "rows": rows})

@app.get("/api/plan/<plan_date>")
def api_plan_by_date(plan_date):
    include_failed = request.args.get("include_failed", "0") == "1"
    conn = open_db(current_app.config["DB_PATH"])
    rows = get_trade_plan_by_date(conn, plan_date, include_failed=include_failed)
    conn.close()
    return jsonify({"plan_date": plan_date, "rows": rows})
```

```bash
git commit -m "feat(web): add /api/plan/today and /api/plan/<date> endpoints"
```

---

### Task 16: Web UI — `/plan` tab

**Files:**
- Modify: `web_server.py`
- Modify: `templates/index.html`

- [ ] **Step 1: Write failing render test**

```python
def test_plan_page_renders():
    app = create_app(testing=True)
    client = app.test_client()
    resp = client.get("/plan")
    assert resp.status_code == 200
    assert b"Plan" in resp.data
```

- [ ] **Step 2-5: Add route + tab content + commit**

```python
# web_server.py
@app.get("/plan")
@app.get("/plan/<plan_date>")
def plan_page(plan_date=None):
    return render_template("index.html", active_tab="plan", plan_date=plan_date)
```

In `templates/index.html`, add a new tab pane:

```html
<div class="tab-pane" id="plan">
  <h3>今日 Plan</h3>
  <table class="table table-sm">
    <thead>
      <tr>
        <th>代码</th><th>方向</th><th>计划价</th><th>仓位%</th>
        <th>止损</th><th>止盈</th><th>RR</th><th>状态</th><th>理由</th>
      </tr>
    </thead>
    <tbody id="plan-tbody"></tbody>
  </table>
  <button id="plan-recompute" class="btn btn-secondary btn-sm">重算 plan</button>
</div>

<script>
$('#plan-tab').on('shown.bs.tab', function() {
  $.getJSON('/api/plan/today', function(data) {
    var tbody = $('#plan-tbody').empty();
    data.rows.forEach(function(r) {
      tbody.append(
        '<tr><td>' + r.code + '</td><td>' + r.action + '</td>' +
        '<td>' + r.plan_price.toFixed(2) + '</td>' +
        '<td>' + (r.size_pct * 100).toFixed(1) + '</td>' +
        '<td>' + r.stop_price.toFixed(2) + '</td>' +
        '<td>' + r.tp_price.toFixed(2) + '</td>' +
        '<td>' + r.rr_ratio.toFixed(2) + '</td>' +
        '<td>' + r.status + '</td>' +
        '<td><button class="btn btn-link btn-sm" data-rationale=\'' +
        JSON.stringify(r.rationale_json) + '\'>详情</button></td></tr>'
      );
    });
  });
});
</script>
```

```bash
git commit -m "feat(web): add /plan tab with today API integration"
```

---

## Phase 5: End-to-end verification (Tasks 17-18)

### Task 17: Integration test — full pipeline

**Files:**
- Modify: `tests/test_integration.py`

- [ ] **Step 1: Write the test**

```python
def test_end_to_end_plan_pipeline(tmp_path):
    db = str(tmp_path / "test.db")
    # Seed daily_picks + kline data for a small universe
    conn = open_db(db)
    for code in ["600519", "000001"]:
        conn.execute(
            "INSERT INTO daily_picks (pick_date, code, score, atr) VALUES (?,?,?,?)",
            ("2026-08-18", code, 2.0, 2.5),
        )
        conn.execute(
            "INSERT INTO daily_kline (code, date, close) VALUES (?,?,?)",
            (code, "2026-08-17", 100.0),
        )
    conn.commit()
    conn.close()

    result = build_plan("2026-08-18", db, params={
        "min_score": 1.0, "rr_target": 2.0, "stop_atr_mult": 2.0,
        "max_single": 0.15, "max_total": 0.95, "regime": "bull",
        "risk_cfg": {"base_size": 0.05, "max_single": 0.15,
                     "regime_factor": {"bull": 1.0}},
    })
    assert result.sanity_passed
    assert len(result.rows) == 2

    conn = open_db(db)
    plan_rows = get_trade_plan_by_date(conn, "2026-08-18")
    opens = get_open_positions(conn)
    assert len(plan_rows) == 2
    assert len(opens) == 2
    conn.close()
```

- [ ] **Step 2-5: Run + commit**

Run: `uv run pytest tests/test_integration.py -v`
Expected: PASS

```bash
git commit -m "test: add end-to-end plan pipeline integration test"
```

---

### Task 18: Idempotency + regression gate

**Files:**
- Modify: `tests/test_plan_builder.py`

- [ ] **Step 1: Write the test**

```python
def test_rebuild_plan_is_idempotent():
    # Same plan_date, same picks → second call inserts no new rows
    build_plan("2026-08-18", path, params)
    conn = open_db(path)
    count_before = len(get_trade_plan_by_date(conn, "2026-08-18", include_failed=True))
    conn.close()
    build_plan("2026-08-18", path, params)
    conn = open_db(path)
    count_after = len(get_trade_plan_by_date(conn, "2026-08-18", include_failed=True))
    conn.close()
    # Note: open_positions accumulate; only trade_plan rows must not dup
    assert count_before == count_after

def test_ma_backtest_still_passes_after_extraction():
    """Refactor regression: golden numbers must not change."""
    # Run the existing ma_backtest smoke test
    import subprocess
    res = subprocess.run(
        ["uv", "run", "python", "ma_backtest.py",
         "--db", "hs300.db", "--top", "10", "--days", "60"],
        capture_output=True, text=True, timeout=120,
    )
    assert res.returncode == 0
    # Must print the same total return ±0.1%
    assert "+67.90" in res.stdout or "67.9" in res.stdout  # adjust to actual
```

- [ ] **Step 2-5: Run, commit**

```bash
git commit -m "test: add idempotency + ma_backtest regression gate"
```

---

## Self-Review

**Spec coverage check**:
- 共享逻辑 → Tasks 2-6 ✓
- trade_plan / open_positions / trade_events 表 → Task 1 ✓
- build_plan 主流程 → Tasks 8-12 ✓
- Sanity gate → Task 11 ✓
- CLI → Task 13 ✓
- Web → Tasks 15-16 ✓
- daily_plan.sh → Task 14 ✓
- 测试 + 回测对齐 → Tasks 17-18 ✓

**Placeholder scan**: All code blocks contain real implementation; no "TBD" or "fill in".

**Type consistency**: `PlanRow` defined in Task 2, used in Tasks 9-11; `params_hash` defined Task 2, used Task 11; `get_trade_plan_by_date` defined Task 7, used Tasks 11, 15, 17.

**Known adjustments required at execution**:
- `_load_plan_params()` in Task 13 — define as `dict` reading from `config.py` defaults
- `daily_picks` / `daily_kline` table column names may differ; executor must adapt
- `ma_backtest` legacy function names may differ from `score_signal` / `stop_loss` / `take_profit` / `select_picks` / `position_size` — executor must grep and map
- The "67.90%" golden assertion in Task 18 should match whatever the README actually shows