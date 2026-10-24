"""可转债仓储层测试：cb_universe / cb_factors / cb_picks / cb_positions 的读写。"""
from db_repository import open_db

from cb_repository import (
    upsert_cb_universe_rows,
    get_cb_universe,
    upsert_cb_factors_rows,
    get_cb_factors,
    replace_cb_picks,
    get_cb_picks,
    upsert_cb_position,
    get_cb_positions,
    delete_cb_position,
)


def open_db_for_test(tmp_path):
    return open_db(str(tmp_path / "cb.db"))


def test_upsert_and_get_universe(tmp_path):
    conn = open_db_for_test(tmp_path)
    upsert_cb_universe_rows(conn, "2026-10-24", [
        {"code": "113059", "name": "福莱转债", "price": 115.3},
    ])
    rows = get_cb_universe(conn, "2026-10-24")
    conn.close()
    assert len(rows) == 1
    assert rows[0]["code"] == "113059"
    assert rows[0]["name"] == "福莱转债"


def test_universe_upsert_is_idempotent(tmp_path):
    conn = open_db_for_test(tmp_path)
    upsert_cb_universe_rows(conn, "2026-10-24", [{"code": "113059", "price": 115.3}])
    upsert_cb_universe_rows(conn, "2026-10-24", [{"code": "113059", "price": 118.0}])
    rows = get_cb_universe(conn, "2026-10-24")
    conn.close()
    assert len(rows) == 1
    assert rows[0]["price"] == 118.0


def test_upsert_and_get_factors(tmp_path):
    conn = open_db_for_test(tmp_path)
    upsert_cb_factors_rows(conn, "2026-10-24", [
        {"code": "113059", "bond_floor": 1.01, "year_left": 1.6, "redeem_dist": 12.3},
    ])
    rows = get_cb_factors(conn, "2026-10-24")
    conn.close()
    assert len(rows) == 1
    assert rows[0]["bond_floor"] == 1.01
    assert rows[0]["redeem_status"] is None


def test_replace_picks_overwrites_same_date(tmp_path):
    conn = open_db_for_test(tmp_path)
    replace_cb_picks(conn, "2026-10-24", [
        {"rank": 1, "code": "A", "total_score": 90.0},
    ])
    replace_cb_picks(conn, "2026-10-24", [
        {"rank": 1, "code": "B", "total_score": 80.0},
    ])
    rows = get_cb_picks(conn, "2026-10-24")
    conn.close()
    assert len(rows) == 1
    assert rows[0]["code"] == "B"


def test_position_crud(tmp_path):
    conn = open_db_for_test(tmp_path)
    upsert_cb_position(conn, {
        "code": "113059", "name": "福莱转债", "entry_date": "2026-10-20",
        "entry_price": 115.0, "shares": 50, "stop_price": 105.8, "tp_price": 138.0,
    })
    rows = get_cb_positions(conn)
    assert len(rows) == 1
    assert rows[0]["shares"] == 50
    upsert_cb_position(conn, {
        "code": "113059", "name": "福莱转债", "entry_date": "2026-10-20",
        "entry_price": 115.0, "shares": 80, "stop_price": 105.8, "tp_price": 138.0,
    })
    rows = get_cb_positions(conn)
    assert len(rows) == 1
    assert rows[0]["shares"] == 80
    delete_cb_position(conn, "113059")
    assert get_cb_positions(conn) == []
    conn.close()
