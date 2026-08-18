import sqlite3
import tempfile

from db_repository import open_db
from shared_lib.strategy import PlanRow


def _conn():
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    return open_db(path)


def test_insert_and_fetch_trade_plan():
    from db_repository import insert_trade_plan, get_trade_plan_by_date
    conn = _conn()
    try:
        row = PlanRow("600519", "buy", 100.0, 0.1, 95.0, 110.0, 2.0, {}, "ok", "")
        pid = insert_trade_plan(conn, row, "2026-08-18", "abc123")
        assert pid > 0
        rows = get_trade_plan_by_date(conn, "2026-08-18")
        assert len(rows) == 1
        assert rows[0]["code"] == "600519"
        assert rows[0]["status"] == "ok"
        assert rows[0]["action"] == "buy"
        assert rows[0]["plan_price"] == 100.0
    finally:
        conn.close()


def test_trade_plan_idempotent_via_insert_ignore():
    """UNIQUE(plan_date, code, action) + INSERT OR IGNORE = silent no-op on dup."""
    from db_repository import insert_trade_plan, get_trade_plan_by_date
    conn = _conn()
    try:
        row = PlanRow("600519", "buy", 100.0, 0.1, 95.0, 110.0, 2.0, {}, "ok", "")
        first_id = insert_trade_plan(conn, row, "2026-08-18", "abc")
        second_id = insert_trade_plan(conn, row, "2026-08-18", "abc")
        assert first_id > 0
        assert second_id == 0  # ignored
        rows = get_trade_plan_by_date(conn, "2026-08-18")
        assert len(rows) == 1
    finally:
        conn.close()


def test_trade_plan_excludes_failed_by_default():
    from db_repository import insert_trade_plan, get_trade_plan_by_date
    conn = _conn()
    try:
        ok = PlanRow("600519", "buy", 100.0, 0.1, 95.0, 110.0, 2.0, {}, "ok", "")
        bad = PlanRow("000001", "buy", 50.0, 0.5, 47.0, 56.0, 2.0, {}, "failed", "size_exceed_max")
        insert_trade_plan(conn, ok, "2026-08-18", "h1")
        insert_trade_plan(conn, bad, "2026-08-18", "h1")
        visible = get_trade_plan_by_date(conn, "2026-08-18")
        assert len(visible) == 1
        assert visible[0]["code"] == "600519"
        all_rows = get_trade_plan_by_date(conn, "2026-08-18", include_failed=True)
        assert len(all_rows) == 2
    finally:
        conn.close()


def test_open_position_lifecycle():
    from db_repository import (
        insert_open_position, get_open_positions, close_open_position
    )
    conn = _conn()
    try:
        pid = insert_open_position(conn, "600519", "2026-08-18", 100.0, 0.1, 95.0, 110.0)
        assert pid > 0
        opens = get_open_positions(conn)
        assert len(opens) == 1
        assert opens[0]["code"] == "600519"
        assert opens[0]["status"] == "open"
        close_open_position(conn, pid, "2026-08-25", 108.0, "tp_hit")
        assert len(get_open_positions(conn)) == 0
    finally:
        conn.close()


def test_trade_event_round_trip():
    from db_repository import insert_trade_event
    conn = _conn()
    try:
        eid = insert_trade_event(conn, "2026-08-18", "600519", "open", 100.0, 0.1)
        assert eid > 0
        cur = conn.execute(
            "SELECT event_type, price, size_pct, pnl_pct, note "
            "FROM trade_events WHERE event_id = ?",
            (eid,),
        )
        row = cur.fetchone()
        assert row[0] == "open"
        assert row[1] == 100.0
        assert row[2] == 0.1
        assert row[3] is None
    finally:
        conn.close()


def test_trade_event_with_pnl():
    from db_repository import insert_trade_event
    conn = _conn()
    try:
        eid = insert_trade_event(
            conn, "2026-08-25", "600519", "close", 108.0, pnl_pct=8.0, note="tp_hit"
        )
        assert eid > 0
        cur = conn.execute(
            "SELECT pnl_pct, note FROM trade_events WHERE event_id = ?", (eid,)
        )
        row = cur.fetchone()
        assert row[0] == 8.0
        assert row[1] == "tp_hit"
    finally:
        conn.close()