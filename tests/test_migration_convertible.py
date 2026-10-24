from db_repository import open_db


def _cols(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def test_cb_universe_table_exists(tmp_path):
    conn = open_db(str(tmp_path / "m.db"))
    cols = _cols(conn, "cb_universe")
    conn.close()
    for c in ["trade_date", "code", "name", "stock_code", "price", "convert_price",
              "convert_value", "premium_rate", "stock_price", "rating",
              "issue_size", "listed"]:
        assert c in cols


def test_cb_factors_table_exists(tmp_path):
    conn = open_db(str(tmp_path / "m.db"))
    cols = _cols(conn, "cb_factors")
    conn.close()
    for c in ["trade_date", "code", "bond_floor", "ytm", "year_left",
              "redeem_dist", "put_dist", "adj_count", "redeem_status"]:
        assert c in cols


def test_cb_picks_table_exists(tmp_path):
    conn = open_db(str(tmp_path / "m.db"))
    cols = _cols(conn, "cb_picks")
    conn.close()
    for c in ["trade_date", "rank", "code", "price", "total_score",
              "a_score", "b_score", "c_score", "size_pct", "shares",
              "stop_price", "tp_price", "reason", "risk_note", "factor_counts"]:
        assert c in cols


def test_cb_positions_table_exists(tmp_path):
    conn = open_db(str(tmp_path / "m.db"))
    cols = _cols(conn, "cb_positions")
    conn.close()
    for c in ["code", "name", "stock_code", "entry_date", "entry_price",
              "shares", "stop_price", "tp_price"]:
        assert c in cols


def test_cb_picks_pk_on_date_rank(tmp_path):
    conn = open_db(str(tmp_path / "m.db"))
    pk = [r[1] for r in conn.execute("PRAGMA table_info(cb_picks)").fetchall() if r[5] > 0]
    conn.close()
    assert pk == ["trade_date", "rank"]


def test_cb_positions_pk_on_code(tmp_path):
    conn = open_db(str(tmp_path / "m.db"))
    pk = [r[1] for r in conn.execute("PRAGMA table_info(cb_positions)").fetchall() if r[5] > 0]
    conn.close()
    assert pk == ["code"]


def test_cb_indexes_exist(tmp_path):
    conn = open_db(str(tmp_path / "m.db"))
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='cb_picks'"
    ).fetchall()]
    conn.close()
    assert "idx_cb_picks_date" in names
