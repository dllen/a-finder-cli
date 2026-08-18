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


def test_get_today_plan_summary_failed_hold_exit_excluded_from_action_count():
    """hold/exit + failed rows count as failed, NOT as hold/exit (regression)."""
    import tempfile
    from db_repository import open_db, get_today_plan_summary
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = open_db(path)
    try:
        today = "2026-08-18"
        rows = [
            (today, "600001", "hold", 5, 0.05, 4.5, 6, 1.0, "ok", "", "{}", "h", today + "T00:00:00"),
            (today, "600002", "hold", 5, 0.05, 4.5, 6, 1.0, "failed", "trigger_gone", "{}", "h", today + "T00:00:00"),
            (today, "600003", "exit", 8, 0.0, 7, 10, 2.0, "ok", "", "{}", "h", today + "T00:00:00"),
            (today, "600004", "exit", 8, 0.0, 7, 10, 2.0, "failed", "stale_price", "{}", "h", today + "T00:00:00"),
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
            "buy": 0, "hold": 1, "exit": 1,
            "size_total": 0.0,
            "failed": 2,  # hold+failed + exit+failed
        }
    finally:
        conn.close()


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
            "INSERT INTO daily_prices (code, trade_date, open, close, high, low, volume, amount, amplitude, pct_change, turnover) "
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