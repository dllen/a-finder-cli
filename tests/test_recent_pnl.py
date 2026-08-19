"""Unit tests for get_recent_pnl (最近 5 日收益) mark-to-market semantics.

New contract: pnl_5d combines realized (positions closed that day) and
unrealized (positions still open, marked to that day's close), both
size-weighted, expressed as portfolio return %. Newest-first order.
"""
from db_repository import get_recent_pnl, open_db


def _seed_prices(conn, rows):
    conn.executemany(
        "INSERT INTO daily_prices (code, trade_date, close) VALUES (?,?,?)",
        rows,
    )
    conn.commit()


def _seed_position(conn, code, entry_date, entry_price, size_pct,
                   status="open", close_date=None, close_price=None):
    conn.execute(
        """INSERT INTO open_positions
        (code, entry_date, entry_price, size_pct, stop_price, tp_price,
         status, close_date, close_price, close_reason)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (code, entry_date, entry_price, size_pct, 0.0, 0.0,
         status, close_date, close_price, None),
    )
    conn.commit()


def test_recent_pnl_empty_no_positions(tmp_path):
    db = str(tmp_path / "pnl.db")
    conn = open_db(db)
    try:
        assert get_recent_pnl(conn, days=5) == []
    finally:
        conn.close()


def test_recent_pnl_no_price_data(tmp_path):
    db = str(tmp_path / "pnl.db")
    conn = open_db(db)
    _seed_position(conn, "600000", "2026-08-18", 100.0, 0.5)
    try:
        assert get_recent_pnl(conn, days=5) == []
    finally:
        conn.close()


def test_recent_pnl_single_open_position_multiday(tmp_path):
    db = str(tmp_path / "pnl.db")
    conn = open_db(db)
    _seed_prices(conn, [
        ("600000", "2026-08-15", 100.0),
        ("600000", "2026-08-18", 110.0),
        ("600000", "2026-08-19", 105.0),
    ])
    _seed_position(conn, "600000", "2026-08-15", 100.0, 0.5)
    try:
        # size-weighted mark-to-market: ret = (close/entry - 1) * 100 * size
        # 08-15: 0.0 ; 08-18: 5.0 ; 08-19: 2.5  → newest first
        assert get_recent_pnl(conn, days=3) == [
            {"date": "2026-08-19", "pnl_pct": 2.5},
            {"date": "2026-08-18", "pnl_pct": 5.0},
            {"date": "2026-08-15", "pnl_pct": 0.0},
        ]
    finally:
        conn.close()


def test_recent_pnl_mixed_realized_and_unrealized(tmp_path):
    db = str(tmp_path / "pnl.db")
    conn = open_db(db)
    _seed_prices(conn, [
        ("600000", "2026-08-15", 100.0),
        ("600000", "2026-08-18", 110.0),
        ("600001", "2026-08-15", 100.0),
        ("600001", "2026-08-18", 120.0),
        ("600001", "2026-08-19", 90.0),
    ])
    # A: closed 08-18, realized = (110/100-1)*100*0.5 = 5.0
    _seed_position(conn, "600000", "2026-08-15", 100.0, 0.5,
                   status="closed", close_date="2026-08-18", close_price=110.0)
    # B: still open
    _seed_position(conn, "600001", "2026-08-15", 100.0, 0.5)
    try:
        # 08-19: A skip(closed), B = (90/100-1)*100*0.5 = -5.0
        # 08-18: A realized 5.0 + B (120/100-1)*100*0.5 = 10.0 → 15.0
        # 08-15: A mtm 0.0 + B 0.0 → 0.0
        assert get_recent_pnl(conn, days=3) == [
            {"date": "2026-08-19", "pnl_pct": -5.0},
            {"date": "2026-08-18", "pnl_pct": 15.0},
            {"date": "2026-08-15", "pnl_pct": 0.0},
        ]
    finally:
        conn.close()


def test_recent_pnl_skips_days_before_entry(tmp_path):
    db = str(tmp_path / "pnl.db")
    conn = open_db(db)
    _seed_prices(conn, [
        ("600000", "2026-08-13", 100.0),
        ("600000", "2026-08-18", 110.0),
    ])
    _seed_position(conn, "600000", "2026-08-18", 100.0, 0.5)
    try:
        # days=3 → dates [08-18, 08-13]; 08-13 precedes entry → skipped
        assert get_recent_pnl(conn, days=3) == [
            {"date": "2026-08-18", "pnl_pct": 5.0},
        ]
    finally:
        conn.close()
