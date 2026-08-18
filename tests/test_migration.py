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