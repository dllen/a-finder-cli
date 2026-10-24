"""可转债迁移测试：4 张 cb_ 表的 schema / 约束 / 索引校验。"""
import sqlite3

import pytest

from db_repository import open_db


def _cols(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def _info(conn, table):
    """返回 {列名: (类型, notnull, pk序号)}。"""
    return {
        r[1]: (r[2], r[3], r[5])
        for r in conn.execute(f"PRAGMA table_info({table})").fetchall()
    }


def _pk(conn, table):
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall() if r[5] > 0]


def _index_names(conn, table):
    return [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?", (table,)
        ).fetchall()
    ]


def test_cb_universe_table_exists(tmp_path):
    conn = open_db(str(tmp_path / "m.db"))
    cols = _cols(conn, "cb_universe")
    conn.close()
    for c in ["trade_date", "code", "name", "stock_code", "price", "convert_price",
              "convert_value", "premium_rate", "stock_price", "rating",
              "issue_size", "listed"]:
        assert c in cols


def test_cb_universe_column_types(tmp_path):
    conn = open_db(str(tmp_path / "m.db"))
    info = _info(conn, "cb_universe")
    conn.close()
    assert info["trade_date"][0] == "TEXT"
    assert info["code"][0] == "TEXT"
    for c in ["price", "convert_price", "convert_value", "premium_rate",
              "stock_price", "issue_size"]:
        assert info[c][0] == "REAL", c
    assert info["listed"][0] == "INTEGER"
    assert info["listed"][1] == 1, "listed 必须 NOT NULL"
    assert info["trade_date"][1] == 1
    assert info["code"][1] == 1


def test_cb_factors_table_exists(tmp_path):
    conn = open_db(str(tmp_path / "m.db"))
    cols = _cols(conn, "cb_factors")
    conn.close()
    for c in ["trade_date", "code", "bond_floor", "ytm", "year_left",
              "redeem_dist", "put_dist", "adj_count", "redeem_status"]:
        assert c in cols


def test_cb_factors_column_types_and_pk(tmp_path):
    conn = open_db(str(tmp_path / "m.db"))
    info = _info(conn, "cb_factors")
    pk = _pk(conn, "cb_factors")
    conn.close()
    for c in ["bond_floor", "ytm", "year_left", "redeem_dist", "put_dist"]:
        assert info[c][0] == "REAL", c
    assert info["adj_count"][0] == "INTEGER"
    assert info["trade_date"][0] == "TEXT"
    assert info["code"][0] == "TEXT"
    assert pk == ["trade_date", "code"]


def test_cb_picks_table_exists(tmp_path):
    conn = open_db(str(tmp_path / "m.db"))
    cols = _cols(conn, "cb_picks")
    conn.close()
    for c in ["trade_date", "rank", "code", "price", "total_score",
              "a_score", "b_score", "c_score", "size_pct", "shares",
              "stop_price", "tp_price", "reason", "risk_note", "factor_counts"]:
        assert c in cols


def test_cb_picks_column_types(tmp_path):
    conn = open_db(str(tmp_path / "m.db"))
    info = _info(conn, "cb_picks")
    conn.close()
    assert info["trade_date"][0] == "TEXT"
    assert info["code"][0] == "TEXT"
    assert info["rank"][0] == "INTEGER"
    assert info["shares"][0] == "INTEGER"
    for c in ["price", "total_score", "a_score", "b_score", "c_score",
              "size_pct", "stop_price", "tp_price"]:
        assert info[c][0] == "REAL", c
    assert info["rank"][1] == 1
    assert info["trade_date"][1] == 1


def test_cb_positions_table_exists(tmp_path):
    conn = open_db(str(tmp_path / "m.db"))
    cols = _cols(conn, "cb_positions")
    conn.close()
    for c in ["code", "name", "stock_code", "entry_date", "entry_price",
              "shares", "stop_price", "tp_price", "note", "updated_at"]:
        assert c in cols


def test_cb_positions_column_types_and_notnull(tmp_path):
    conn = open_db(str(tmp_path / "m.db"))
    info = _info(conn, "cb_positions")
    conn.close()
    assert info["code"][0] == "TEXT"
    assert info["entry_date"][0] == "TEXT"
    assert info["entry_price"][0] == "REAL"
    assert info["shares"][0] == "INTEGER"
    assert info["stop_price"][0] == "REAL"
    assert info["tp_price"][0] == "REAL"
    # 关键持仓字段必须 NOT NULL（告警扫描依赖 entry_price / shares / entry_date）
    for c in ["entry_date", "entry_price", "shares"]:
        assert info[c][1] == 1, c
    # 可后置推导的字段保持可空
    for c in ["name", "stock_code", "stop_price", "tp_price", "note", "updated_at"]:
        assert info[c][1] == 0, c


def test_cb_positions_rejects_missing_key_fields(tmp_path):
    conn = open_db(str(tmp_path / "m.db"))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO cb_positions (code) VALUES ('113059')")
    conn.rollback()
    conn.close()


def test_cb_picks_pk_on_date_rank(tmp_path):
    conn = open_db(str(tmp_path / "m.db"))
    pk = _pk(conn, "cb_picks")
    conn.close()
    assert pk == ["trade_date", "rank"]


def test_cb_positions_pk_on_code(tmp_path):
    conn = open_db(str(tmp_path / "m.db"))
    pk = _pk(conn, "cb_positions")
    conn.close()
    assert pk == ["code"]


def test_cb_picks_rejects_same_code_twice_on_one_date(tmp_path):
    conn = open_db(str(tmp_path / "m.db"))
    conn.execute(
        "INSERT INTO cb_picks (trade_date, rank, code, price) VALUES (?, ?, ?, ?)",
        ("2026-10-24", 1, "113059", 100.0),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO cb_picks (trade_date, rank, code, price) VALUES (?, ?, ?, ?)",
            ("2026-10-24", 2, "113059", 100.0),
        )
    conn.rollback()
    conn.close()


def test_cb_indexes_exist(tmp_path):
    conn = open_db(str(tmp_path / "m.db"))
    names = _index_names(conn, "cb_picks")
    conn.close()
    assert "idx_cb_picks_date" in names


def test_cb_all_indexes_exist(tmp_path):
    conn = open_db(str(tmp_path / "m.db"))
    picks_idx = _index_names(conn, "cb_picks")
    universe_idx = _index_names(conn, "cb_universe")
    factors_idx = _index_names(conn, "cb_factors")
    conn.close()
    assert "idx_cb_picks_date" in picks_idx
    assert "idx_cb_picks_date_code" in picks_idx
    assert "idx_cb_universe_code" in universe_idx
    assert "idx_cb_factors_code" in factors_idx


def test_cb_picks_date_code_index_is_unique(tmp_path):
    conn = open_db(str(tmp_path / "m.db"))
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name='idx_cb_picks_date_code'"
    ).fetchone()
    conn.close()
    assert row is not None
    assert "UNIQUE" in row[0].upper()
