import sqlite3
from db_repository import open_db


def test_fundamentals_history_table_exists(tmp_path):
    db = str(tmp_path / "m.db")
    conn = open_db(db)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(fundamentals_history)").fetchall()]
    conn.close()
    assert "code" in cols
    assert "year" in cols
    assert "gross_margin" in cols
    assert "roe_excl" in cols


def test_fundamentals_history_pk_on_code_year(tmp_path):
    db = str(tmp_path / "m.db")
    conn = open_db(db)
    pk_cols = [
        r[1] for r in conn.execute("PRAGMA table_info(fundamentals_history)").fetchall()
        if r[5] > 0
    ]
    conn.close()
    assert pk_cols == ["code", "year"]


def test_fundamentals_history_index_exists(tmp_path):
    db = str(tmp_path / "m.db")
    conn = open_db(db)
    idx_names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='fundamentals_history'"
    ).fetchall()]
    conn.close()
    assert "idx_fundamentals_history_code" in idx_names
