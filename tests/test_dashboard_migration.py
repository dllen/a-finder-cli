import sqlite3
import tempfile

from db_repository import open_db


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