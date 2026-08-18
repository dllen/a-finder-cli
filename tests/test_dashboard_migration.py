import sqlite3
import tempfile

from db_repository import open_db
from pick_history import upsert_picks


def test_daily_picks_has_updated_at():
    """After open_db runs migrations, daily_picks.updated_at exists and defaults populate."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = open_db(path)
    try:
        # column exists
        cols = [r[1] for r in conn.execute("PRAGMA table_info(daily_picks)").fetchall()]
        assert "updated_at" in cols
        # default populates on insert
        conn.execute(
            "INSERT INTO daily_picks (date, rank, kind, code) VALUES ('2026-08-18', 1, '均线', '600519')"
        )
        conn.commit()
        row = conn.execute(
            "SELECT updated_at FROM daily_picks WHERE date='2026-08-18' AND code='600519'"
        ).fetchone()
        assert row[0] is not None and len(row[0]) >= 10  # ISO-ish string
    finally:
        conn.close()


def test_pick_history_writes_updated_at():
    """pick_history upserts daily_picks rows with updated_at populated."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        path = f.name
    conn = open_db(path)
    try:
        picks = [{
            "date": "2026-08-18", "rank": 1, "kind": "均线",
            "code": "600519", "name": "贵州茅台", "strategy": "突破",
            "buy": 1500.0, "stop": 1450.0, "target": 1600.0, "score": 9.5,
        }]
        upsert_picks(conn, "2026-08-18", "均线", picks)
        conn.commit()
        row = conn.execute(
            "SELECT updated_at FROM daily_picks WHERE date='2026-08-18' AND code='600519'"
        ).fetchone()
        assert row[0] is not None
    finally:
        conn.close()