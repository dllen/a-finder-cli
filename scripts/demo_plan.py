"""End-to-end demo of plan_builder.build_plan.

Seeds a fresh temp DB with 2 picks + 2 open_positions, runs build_plan,
prints the resulting rows. Run directly:
    uv run python scripts/demo_plan.py
"""
from __future__ import annotations

import os
import tempfile

from db_repository import open_db
from plan_builder import build_plan


def _seed_pick(conn, *, date, code, score, buy, stop, target, rank):
    conn.execute(
        """INSERT INTO daily_picks (date, rank, kind, code, name, strategy,
             buy, stop, target, score)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (date, rank, "demo", code, code, "demo", buy, stop, target, score),
    )
    conn.execute(
        """INSERT INTO daily_prices (code, trade_date, open, close, high, low)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (code, date, buy, buy, buy, buy),
    )
    conn.commit()


def main() -> None:
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    conn = open_db(path)
    try:
        # Two fresh picks (stops >= 10% below buy so BULL -8% recompute is ok)
        _seed_pick(conn, date="2026-08-18", code="600519", score=2.5,
                   buy=100.0, stop=80.0, target=140.0, rank=1)
        _seed_pick(conn, date="2026-08-18", code="000001", score=1.8,
                   buy=50.0, stop=40.0, target=70.0, rank=2)
        # Open position: current price above stop → hold
        conn.execute(
            """INSERT INTO open_positions
               (code, entry_date, entry_price, size_pct, stop_price, tp_price, status)
               VALUES (?, ?, ?, ?, ?, ?, 'open')""",
            ("300750", "2026-08-15", 200.0, 0.10, 180.0, 240.0),
        )
        conn.execute(
            """INSERT INTO daily_prices (code, trade_date, open, close, high, low)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("300750", "2026-08-18", 210.0, 210.0, 210.0, 210.0),
        )
        # Open position: stop just hit → exit
        conn.execute(
            """INSERT INTO open_positions
               (code, entry_date, entry_price, size_pct, stop_price, tp_price, status)
               VALUES (?, ?, ?, ?, ?, ?, 'open')""",
            ("688981", "2026-08-10", 100.0, 0.10, 105.0, 130.0),
        )
        conn.execute(
            """INSERT INTO daily_prices (code, trade_date, open, close, high, low)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("688981", "2026-08-18", 102.0, 102.0, 102.0, 102.0),
        )
        conn.commit()
    finally:
        conn.close()

    result = build_plan("2026-08-18", path, params={
        "regime": "BULL",
        "max_single": 0.15,
        "max_total": 0.95,
    })

    print(f"plan_date: {result.plan_date}")
    print(f"picks:     {result.num_picks}")
    print(f"open_pos:  {result.num_open_positions}")
    print(f"sanity:    passed={result.sanity_passed} reasons={result.sanity_reasons}")
    print(f"rows ({len(result.rows)}):")
    for r in result.rows:
        print(
            f"  {r.action:4s} {r.code} px={r.plan_price:7.2f} "
            f"size={r.size_pct*100:5.2f}% stop={r.stop_price:7.2f} "
            f"tp={r.tp_price:7.2f} rr={r.rr_ratio:.2f} "
            f"status={r.status} reason={r.reason!r}"
        )

    conn = open_db(path)
    try:
        opens = conn.execute(
            "SELECT code, entry_price, size_pct, status FROM open_positions"
        ).fetchall()
        evts = conn.execute(
            "SELECT code, event_type, price, shares, pnl_amt, note FROM trade_events"
        ).fetchall()
    finally:
        conn.close()
    print(f"\nopen_positions ({len(opens)}):")
    for o in opens:
        print(f"  {o}")
    print(f"trade_events ({len(evts)}):")
    for e in evts:
        print(f"  {e}")


if __name__ == "__main__":
    main()
