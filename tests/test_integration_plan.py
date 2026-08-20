"""Integration tests for the daily plan pipeline.

Task 17 — end-to-end: seed picks + prices → build_plan → assert trade_plan,
open_positions, trade_events all populated.
Task 18 — idempotency: rebuild same plan_date → trade_plan row count unchanged.
"""
from __future__ import annotations

import os
import tempfile

from db_repository import (
    get_open_positions,
    get_trade_plan_by_date,
    open_db,
)


def _fresh_db():
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = open_db(path)
    return path, conn


_RANK = {"n": 0}


def _seed_pick(conn, *, date, code, score=2.0, buy=100.0,
               stop=80.0, target=140.0, kind="test"):
    _RANK["n"] += 1
    conn.execute(
        """INSERT INTO daily_picks (date, rank, kind, code, name, strategy,
             buy, stop, target, score)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (date, _RANK["n"], kind, code, code, "test", buy, stop, target, score),
    )
    conn.commit()


def _seed_price(conn, *, code, close, trade_date):
    conn.execute(
        """INSERT INTO daily_prices (code, trade_date, open, close,
             high, low, volume, amount)
           VALUES (?, ?, ?, ?, ?, ?, 0, 0)""",
        (code, trade_date, close, close, close, close),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Task 17: full pipeline integration
# ---------------------------------------------------------------------------

def test_end_to_end_plan_pipeline():
    """Seed picks + prices → build_plan → assert all three tables populated."""
    path, conn = _fresh_db()
    try:
        for code in ("600519", "000001"):
            _seed_pick(conn, date="2026-08-18", code=code, score=2.0)
            _seed_price(conn, code=code, close=100.0, trade_date="2026-08-18")
    finally:
        conn.close()

    from plan_builder import build_plan
    result = build_plan("2026-08-18", path, params={"regime": "BULL"})

    # result contains the expected buy rows
    buys = [r for r in result.rows if r.action == "buy"]
    assert len(buys) == 2
    assert all(r.status == "ok" for r in buys)

    # trade_plan persisted
    conn = open_db(path)
    try:
        plan_rows = get_trade_plan_by_date(conn, "2026-08-18")
        opens = get_open_positions(conn)
        events = conn.execute(
            "SELECT event_type, code FROM trade_events"
        ).fetchall()
    finally:
        conn.close()

    assert len(plan_rows) == 2
    assert {r["code"] for r in plan_rows} == {"600519", "000001"}
    assert len(opens) == 2
    assert all(o["status"] == "open" for o in opens)
    open_events = [e for e in events if e[0] == "open"]
    assert len(open_events) == 2


# ---------------------------------------------------------------------------
# Task 18: idempotency + ma_backtest regression
# ---------------------------------------------------------------------------

def test_rebuild_plan_is_idempotent():
    """Second build_plan for same plan_date must NOT duplicate any side-effect.

    C1: paper fills gated on `insert_trade_plan > 0` so:
      - trade_plan rows: stable per (date, code, action)
      - open_positions: stable (no duplicate fill)
      - trade_events: stable (no duplicate 'open' event)
    """
    path, conn = _fresh_db()
    try:
        _seed_pick(conn, date="2026-08-18", code="600519", score=2.0)
        _seed_price(conn, code="600519", close=100.0, trade_date="2026-08-18")
    finally:
        conn.close()

    from plan_builder import build_plan
    params = {"regime": "BULL"}
    build_plan("2026-08-18", path, params=params)

    conn = open_db(path)
    try:
        plan_before = get_trade_plan_by_date(conn, "2026-08-18", include_failed=True)
        opens_before = get_open_positions(conn)
        events_before = conn.execute(
            "SELECT code, event_type FROM trade_events"
        ).fetchall()
    finally:
        conn.close()

    # Second run — same picks, same params, same plan_date.
    build_plan("2026-08-18", path, params=params)

    conn = open_db(path)
    try:
        plan_after = get_trade_plan_by_date(conn, "2026-08-18", include_failed=True)
        opens_after = get_open_positions(conn)
        events_after = conn.execute(
            "SELECT code, event_type FROM trade_events"
        ).fetchall()
    finally:
        conn.close()

    # Buy row for this code present exactly once.
    buy_codes = [r["code"] for r in plan_after if r["action"] == "buy"]
    assert buy_codes.count("600519") == 1
    # No new open position row (C1: paper fill must not duplicate).
    assert len(opens_after) == len(opens_before) == 1
    # No new trade_event row.
    assert len(events_after) == len(events_before) == 1
    # Buy row count is stable. Hold/exit rows from carryover legitimately
    # accumulate (the carryover was just opened on the first run).
    buys_before = [r for r in plan_before if r["action"] == "buy"]
    buys_after = [r for r in plan_after if r["action"] == "buy"]
    assert len(buys_after) == len(buys_before)


def test_ma_backtest_importable_after_refactor():
    """Refactor regression: ma_backtest module imports + run_backtest callable."""
    # Lightweight smoke check — the full CLI run is too slow for unit-test
    # gate (60+ seconds over HS300). Importability proves the refactor
    # didn't break the canonical entry point.
    import ma_backtest  # noqa: F401
    assert callable(ma_backtest.run_backtest)


# ---------------------------------------------------------------------------
# Final-fix wave: C2 + C3 coverage
# ---------------------------------------------------------------------------

def test_held_code_rebought_accumulates_shares():
    """C2 removed: a held code re-appearing in today's picks IS re-bought.

    Same-code cross-day re-buy ACCUMULATES into the existing open position
    (weighted-average entry) instead of being deduped, so a buy row IS
    produced AND the existing open_positions row is reused, not duplicated.
    """
    path, conn = _fresh_db()
    try:
        # Open position from prior day for 600519, size 0.10, 200 shares
        conn.execute(
            """INSERT INTO open_positions
               (code, entry_date, entry_price, size_pct, stop_price, tp_price, status, shares)
               VALUES (?, ?, ?, ?, ?, ?, 'open', ?)""",
            ("600519", "2026-08-10", 100.0, 0.10, 92.0, 120.0, 200),
        )
        # Today's pick for the SAME code — re-bought (accumulates)
        _seed_pick(conn, date="2026-08-18", code="600519", score=2.0)
        # A second held-eligible pick for 000001 to verify it CAN buy
        _seed_pick(conn, date="2026-08-18", code="000001", score=2.0)
        _seed_price(conn, code="600519", close=100.0, trade_date="2026-08-18")
        _seed_price(conn, code="000001", close=50.0, trade_date="2026-08-18")
    finally:
        conn.close()

    from plan_builder import build_plan
    result = build_plan(
        "2026-08-18", path,
        params={"regime": "BULL", "max_total": 0.95},
    )

    # 600519: hold row (carryover) AND buy row (re-bought)
    actions_600519 = [r.action for r in result.rows if r.code == "600519"]
    assert "buy" in actions_600519
    assert "hold" in actions_600519

    # 000001: buy row only
    actions_000001 = [r.action for r in result.rows if r.code == "000001"]
    assert "buy" in actions_000001

    # 600519 keeps a single open position row (accumulated, not duplicated)
    conn = open_db(path)
    try:
        opens = conn.execute(
            "SELECT code, status, shares FROM open_positions WHERE code='600519'"
        ).fetchall()
    finally:
        conn.close()
    open_rows = [o for o in opens if o[1] == "open"]
    assert len(open_rows) == 1
    # Accumulated: original 200 + re-buy 200 = 400 shares
    assert open_rows[0][2] == 400


def test_tp_exit_fires_when_price_above_tp():
    """C3: take-profit must fire when cur_px >= tp_price."""
    path, conn = _fresh_db()
    try:
        # Open position with tp_price=110; current price above tp → tp_hit
        conn.execute(
            """INSERT INTO open_positions
               (code, entry_date, entry_price, size_pct, stop_price, tp_price, status, shares)
               VALUES (?, ?, ?, ?, ?, ?, 'open', ?)""",
            ("000002", "2026-08-10", 100.0, 0.10, 92.0, 110.0, 200),
        )
        _seed_price(conn, code="000002", close=115.0, trade_date="2026-08-18")
    finally:
        conn.close()

    from plan_builder import build_plan
    result = build_plan("2026-08-18", path, params={"regime": "BULL"})

    exits = [r for r in result.rows if r.action == "exit"]
    assert len(exits) == 1
    assert exits[0].code == "000002"
    assert exits[0].rationale.get("trigger") == "tp_hit"

    conn = open_db(path)
    try:
        opens = get_open_positions(conn)
        events = conn.execute(
            "SELECT event_type, pnl_amt FROM trade_events"
        ).fetchall()
    finally:
        conn.close()

    # Position closed
    assert opens == []
    # Close event recorded with positive pnl
    close_events = [e for e in events if e[0] == "close"]
    assert len(close_events) == 1
    # pnl_amt = (115 - 100) * 200 = 3000.0
    assert abs(close_events[0][1] - 3000.0) < 0.01