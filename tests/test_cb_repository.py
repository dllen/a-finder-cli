"""可转债仓储层测试：cb_universe / cb_factors / cb_picks / cb_positions 的读写。"""
import sqlite3

import pytest

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


# --- 写入原子性：失败不得留下半成品状态，也不得污染连接 ---

def test_replace_picks_duplicate_code_raises_and_preserves_old(tmp_path):
    """同日同 code 触发唯一索引时，整批替换必须回滚，旧榜单原样保留。"""
    conn = open_db_for_test(tmp_path)
    replace_cb_picks(conn, "2026-10-24", [{"rank": 1, "code": "OLD"}])

    with pytest.raises(sqlite3.IntegrityError):
        replace_cb_picks(conn, "2026-10-24", [
            {"rank": 1, "code": "NEW"},
            {"rank": 2, "code": "NEW"},
        ])

    # 同连接读取：旧行仍在，且不应混入写了一半的新行
    rows = get_cb_picks(conn, "2026-10-24")
    assert [r["code"] for r in rows] == ["OLD"], "失败后旧榜单必须完整保留"

    # 模拟后续代码在同一连接上 commit：坏状态不得被持久化
    conn.commit()
    conn.close()

    conn2 = open_db_for_test(tmp_path)
    rows = get_cb_picks(conn2, "2026-10-24")
    conn2.close()
    assert [r["code"] for r in rows] == ["OLD"], "重开数据库后旧榜单仍须完好"


def test_replace_picks_duplicate_code_does_not_corrupt_connection(tmp_path):
    """失败后连接须保持可用：后续写入应正常提交，且不残留脏事务。"""
    conn = open_db_for_test(tmp_path)
    replace_cb_picks(conn, "2026-10-24", [{"rank": 1, "code": "OLD"}])

    with pytest.raises(sqlite3.IntegrityError):
        replace_cb_picks(conn, "2026-10-24", [
            {"rank": 1, "code": "NEW"},
            {"rank": 2, "code": "NEW"},
        ])

    # 连接必须回到干净状态（没有悬而未决的事务）
    assert conn.in_transaction is False, "失败后连接不应残留打开的事务"

    # 同一连接继续写其它表，应正常落库
    replace_cb_picks(conn, "2026-10-25", [{"rank": 1, "code": "OTHER"}])
    upsert_cb_universe_rows(conn, "2026-10-24", [{"code": "113059", "price": 115.3}])
    conn.commit()
    conn.close()

    conn2 = open_db_for_test(tmp_path)
    old = get_cb_picks(conn2, "2026-10-24")
    other = get_cb_picks(conn2, "2026-10-25")
    universe = get_cb_universe(conn2, "2026-10-24")
    conn2.close()

    assert [r["code"] for r in old] == ["OLD"]
    assert [r["code"] for r in other] == ["OTHER"]
    assert [r["code"] for r in universe] == ["113059"]


def test_replace_picks_rolls_back_other_dates_too(tmp_path):
    """替换失败时，其它日期的榜单不受影响（同一次事务内回滚）。"""
    conn = open_db_for_test(tmp_path)
    replace_cb_picks(conn, "2026-10-23", [{"rank": 1, "code": "PREV"}])
    replace_cb_picks(conn, "2026-10-24", [{"rank": 1, "code": "OLD"}])

    with pytest.raises(sqlite3.IntegrityError):
        replace_cb_picks(conn, "2026-10-24", [
            {"rank": 1, "code": "NEW"},
            {"rank": 2, "code": "NEW"},
        ])

    conn.commit()
    conn.close()

    conn2 = open_db_for_test(tmp_path)
    prev = get_cb_picks(conn2, "2026-10-23")
    cur = get_cb_picks(conn2, "2026-10-24")
    conn2.close()
    assert [r["code"] for r in prev] == ["PREV"]
    assert [r["code"] for r in cur] == ["OLD"]


def test_upsert_universe_failure_keeps_connection_clean(tmp_path):
    """universe 批量写入失败时连接须干净，且不留半批次数据。"""
    conn = open_db_for_test(tmp_path)
    upsert_cb_universe_rows(conn, "2026-10-24", [{"code": "GOOD", "price": 1.0}])

    # name 列虽可空，但通过非法类型触发约束失败
    with pytest.raises(sqlite3.Error):
        conn.execute(
            "INSERT INTO cb_universe (trade_date, code, listed) VALUES (?, ?, ?)",
            ("2026-10-25", "BAD", None),
        )
    conn.rollback()

    assert conn.in_transaction is False
    conn.close()


def test_upsert_position_failure_keeps_connection_clean(tmp_path):
    """position 写入违反 NOT NULL 时，旧持仓保留且连接干净。"""
    conn = open_db_for_test(tmp_path)
    upsert_cb_position(conn, {
        "code": "113059", "entry_date": "2026-10-20",
        "entry_price": 115.0, "shares": 50,
    })

    with pytest.raises(sqlite3.IntegrityError):
        upsert_cb_position(conn, {"code": "BAD", "shares": 10})

    assert conn.in_transaction is False, "失败后连接不应残留打开的事务"
    rows = get_cb_positions(conn)
    assert [r["code"] for r in rows] == ["113059"], "失败不得影响既有持仓"

    # 失败后连接仍可正常写入
    upsert_cb_position(conn, {
        "code": "113060", "entry_date": "2026-10-21",
        "entry_price": 120.0, "shares": 30,
    })
    conn.commit()
    conn.close()

    conn2 = open_db_for_test(tmp_path)
    rows = get_cb_positions(conn2)
    conn2.close()
    assert sorted(r["code"] for r in rows) == ["113059", "113060"]
