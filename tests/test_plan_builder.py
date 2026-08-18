"""Tests for plan_builder.build_plan.

Cumulative TDD: tasks 8-12 build on each other, single file, all tests added
in order. Spec coverage: read inputs, buy rows, hold/exit rows, sanity gate,
paper trader.

Real-schema note: `daily_picks` uses columns (date, rank, kind, code, name,
strategy, buy, stop, target, score). `daily_prices` is the kline table with
(code, trade_date, open, close, ...). All tests seed via plain INSERTs.
"""
from __future__ import annotations

import os
import tempfile

import pytest

from db_repository import (
    close_open_position,
    get_open_positions,
    open_db,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fresh_db():
    """Open a temp DB with all migrations applied; return (path, conn)."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = open_db(path)
    return path, conn


_GLOBAL_RANK = {"n": 0}


def _seed_pick(conn, *, date, code, score=2.0, buy=100.0, stop=92.0, target=120.0,
               kind="test"):
    """Insert a daily_picks row. Real PK is (date, rank, kind) — vary rank."""
    _GLOBAL_RANK["n"] += 1
    conn.execute(
        """INSERT INTO daily_picks (date, rank, kind, code, name, strategy,
             buy, stop, target, score)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (date, _GLOBAL_RANK["n"], kind, code, code, "test", buy, stop, target, score),
    )
    conn.commit()


def _seed_price(conn, *, code, close, trade_date="2026-08-18"):
    conn.execute(
        """INSERT INTO daily_prices (code, trade_date, open, close, high, low)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (code, trade_date, close, close, close, close),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Task 8: scaffold + read inputs
# ---------------------------------------------------------------------------

def test_build_plan_reads_inputs():
    path, conn = _fresh_db()
    try:
        _seed_pick(conn, date="2026-08-18", code="600519", score=2.1)
        # Insert open_position via raw SQL to avoid coupling to helper signature
        conn.execute(
            """INSERT INTO open_positions
               (code, entry_date, entry_price, size_pct, stop_price, tp_price, status)
               VALUES (?, ?, ?, ?, ?, ?, 'open')""",
            ("000001", "2026-08-15", 50.0, 0.1, 45.0, 56.0),
        )
        conn.commit()
    finally:
        conn.close()

    from plan_builder import build_plan
    result = build_plan("2026-08-18", path, params={"min_score": 1.0})
    assert result.num_picks >= 1
    assert result.num_open_positions >= 1
    # rows non-empty: combined tasks 8-12 always produce buy/hold/exit rows.


# ---------------------------------------------------------------------------
# Task 9: emit buy rows
# ---------------------------------------------------------------------------

def test_build_plan_emits_buy_rows():
    path, conn = _fresh_db()
    try:
        _seed_pick(conn, date="2026-08-18", code="600519", score=2.1,
                   buy=100.0, stop=92.0, target=120.0)
    finally:
        conn.close()

    from plan_builder import build_plan
    result = build_plan("2026-08-18", path, params={
        "min_score": 1.0,
        "regime": "BULL",
        "max_single": 0.15,
        "max_total": 0.95,
    })
    buy_rows = [r for r in result.rows if r.action == "buy"]
    assert len(buy_rows) == 1
    row = buy_rows[0]
    assert row.code == "600519"
    assert row.plan_price > 0
    assert row.stop_price < row.plan_price
    assert row.tp_price > row.plan_price
    assert row.rr_ratio > 0


# ---------------------------------------------------------------------------
# Task 10: hold/exit from carryover
# ---------------------------------------------------------------------------

def test_build_plan_emits_hold_when_stop_not_hit():
    path, conn = _fresh_db()
    try:
        # open_position with stop far below current price (= entry here)
        conn.execute(
            """INSERT INTO open_positions
               (code, entry_date, entry_price, size_pct, stop_price, tp_price, status)
               VALUES (?, ?, ?, ?, ?, ?, 'open')""",
            ("000001", "2026-08-10", 50.0, 0.1, 45.0, 60.0),
        )
        _seed_price(conn, code="000001", close=50.0, trade_date="2026-08-18")
    finally:
        conn.close()

    from plan_builder import build_plan
    result = build_plan("2026-08-18", path, params={"regime": "BULL"})
    holds = [r for r in result.rows if r.code == "000001" and r.action == "hold"]
    assert len(holds) == 1


def test_build_plan_emits_exit_when_stop_hit():
    path, conn = _fresh_db()
    try:
        # open_position with stop ABOVE current price
        conn.execute(
            """INSERT INTO open_positions
               (code, entry_date, entry_price, size_pct, stop_price, tp_price, status)
               VALUES (?, ?, ?, ?, ?, ?, 'open')""",
            ("000002", "2026-08-10", 100.0, 0.1, 105.0, 115.0),
        )
        _seed_price(conn, code="000002", close=102.0, trade_date="2026-08-18")
    finally:
        conn.close()

    from plan_builder import build_plan
    result = build_plan("2026-08-18", path, params={"regime": "BULL"})
    exits = [r for r in result.rows if r.code == "000002" and r.action == "exit"]
    assert len(exits) == 1


# ---------------------------------------------------------------------------
# Task 11: sanity gate + write trade_plan
# ---------------------------------------------------------------------------

def test_sanity_gate_fails_size_exceed_max():
    """If a buy row's size_pct exceeds max_single, it's marked failed."""
    path, conn = _fresh_db()
    try:
        # High score saturates position_size; stop=92/traget=120 are wide
        # enough that the BULL -8% recomputed stop won't trip rule 2.
        _seed_pick(conn, date="2026-08-18", code="600519", score=10.0,
                   buy=100.0, stop=80.0, target=130.0)
    finally:
        conn.close()

    from plan_builder import build_plan
    result = build_plan("2026-08-18", path, params={
        "regime": "BULL",
        "max_single": 0.05,  # tiny cap — guaranteed to fail
        "max_total": 0.95,
    })
    buys = [r for r in result.rows if r.action == "buy"]
    assert len(buys) == 1
    assert buys[0].status == "failed"
    assert "size_exceed_max" in buys[0].reason


def test_sanity_gate_scales_total_overflow():
    path, conn = _fresh_db()
    try:
        for code in ["600000", "600001", "600002", "600003", "600004"]:
            _seed_pick(conn, date="2026-08-18", code=code, score=2.0,
                       buy=100.0, stop=80.0, target=140.0)
    finally:
        conn.close()

    from plan_builder import build_plan
    result = build_plan("2026-08-18", path, params={
        "regime": "BULL",
        "max_single": 0.99,
        "max_total": 0.5,  # cap below default signal size
    })
    buys = [r for r in result.rows if r.action == "buy"]
    assert len(buys) == 5
    total = sum(r.size_pct for r in buys)
    assert total <= 0.5 + 0.001
    assert all(r.status == "ok" for r in buys)
    assert any("scaled_to_fit" in r.reason for r in buys)


def test_sanity_gate_invalid_stop():
    """Stop above entry → failed (would be a no-op stop)."""
    path, conn = _fresh_db()
    try:
        # Score=0 so we get the smallest position_size; but force stop above
        # entry by directly inserting a debauched plan via DB to simulate
        # a corrupted pick. Cleanest: insert a pick with buy=100, then call
        # build_plan with regime where logic would land stop above entry.
        # With BULL/SIDEWAYS/BEAR all they put stop below buy. So we
        # fabricate via directly poisoning the row after plan generation
        # isn't possible; instead assert the rule via a synthetic pick that
        # forces compute_plan_prices to be ignored — we test the rule
        # directly via inserting a malformed row is not needed; the rule
        # WILL fire if a regime config ever returns stop_loss_pct >= 0,
        # which is not the case. So instead: rebuild the stop with a fake
        # PriceRow-like path.
        # Simplest robust test: monkey-patch RiskManager.get_config to return
        # a stop_loss_pct=+0.05 (above entry).
        _seed_pick(conn, date="2026-08-18", code="600519", score=2.0,
                   buy=100.0, stop=80.0, target=140.0)
    finally:
        conn.close()

    from plan_builder import build_plan
    from risk_manager import PositionConfig

    # Monkey-patch the RiskManager instance inside build_plan to return a
    # stop above entry. We patch the type used.
    import plan_builder as pb
    class _BadRM:
        def get_config(self, regime, signal_strength=1.0):
            return PositionConfig(
                position_size=0.05,
                stop_loss_pct=+0.05,  # stop ABOVE entry
                trailing_stop_pct=0.03,
                time_exit_days=10,
                profit_target_pct=0.20,
            )
    orig = pb.RiskManager
    pb.RiskManager = _BadRM
    try:
        result = build_plan("2026-08-18", path, params={
            "regime": "BULL",
            "max_single": 0.15,
            "max_total": 0.95,
        })
    finally:
        pb.RiskManager = orig

    buys = [r for r in result.rows if r.action == "buy"]
    assert len(buys) == 1
    assert buys[0].status == "failed"
    assert "stop_above_entry" in buys[0].reason


def test_trade_plan_persisted_on_build():
    path, conn = _fresh_db()
    try:
        _seed_pick(conn, date="2026-08-18", code="600519", score=2.0,
                   buy=100.0, stop=80.0, target=140.0)
    finally:
        conn.close()

    from plan_builder import build_plan
    build_plan("2026-08-18", path, params={"regime": "BULL"})

    conn2 = open_db(path)
    try:
        cur = conn2.execute(
            "SELECT code, action, status FROM trade_plan WHERE plan_date = ?",
            ("2026-08-18",),
        )
        rows = cur.fetchall()
    finally:
        conn2.close()
    assert len(rows) == 1
    assert rows[0][0] == "600519"
    assert rows[0][1] == "buy"
    assert rows[0][2] == "ok"


# ---------------------------------------------------------------------------
# Task 12: paper trader
# ---------------------------------------------------------------------------

def test_buy_creates_open_position_and_event():
    path, conn = _fresh_db()
    try:
        _seed_pick(conn, date="2026-08-18", code="600519", score=2.0,
                   buy=100.0, stop=80.0, target=140.0)
    finally:
        conn.close()

    from plan_builder import build_plan
    result = build_plan("2026-08-18", path, params={"regime": "BULL"})
    assert any(r.action == "buy" and r.status == "ok" for r in result.rows)

    conn2 = open_db(path)
    try:
        opens = get_open_positions(conn2)
        evts = conn2.execute(
            "SELECT code, event_type FROM trade_events"
        ).fetchall()
    finally:
        conn2.close()

    assert len(opens) == 1
    assert opens[0]["code"] == "600519"
    assert abs(opens[0]["entry_price"] - 100.0) < 0.5  # slippage adjusted
    assert len([e for e in evts if e[1] == "open"]) == 1


def test_exit_closes_position_and_records_event():
    path, conn = _fresh_db()
    try:
        conn.execute(
            """INSERT INTO open_positions
               (code, entry_date, entry_price, size_pct, stop_price, tp_price, status)
               VALUES (?, ?, ?, ?, ?, ?, 'open')""",
            ("000002", "2026-08-10", 100.0, 0.1, 105.0, 115.0),
        )
        _seed_price(conn, code="000002", close=102.0, trade_date="2026-08-18")
    finally:
        conn.close()

    from plan_builder import build_plan
    result = build_plan("2026-08-18", path, params={"regime": "BULL"})
    assert any(r.code == "000002" and r.action == "exit" for r in result.rows)

    conn2 = open_db(path)
    try:
        opens = get_open_positions(conn2)
        closes = conn2.execute(
            "SELECT code, event_type, pnl_pct FROM trade_events WHERE event_type='close'"
        ).fetchall()
    finally:
        conn2.close()

    assert opens == []  # the only open position got closed
    assert len(closes) == 1
    assert closes[0][0] == "000002"
    # pnl_pct = (close/entry - 1) * 100 = (102/100 - 1) * 100 = 2.0
    assert abs(closes[0][2] - 2.0) < 0.01
