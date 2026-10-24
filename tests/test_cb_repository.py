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


def test_failed_write_does_not_discard_callers_pending_work(tmp_path):
    """调用方自己的未提交写入不能在 cb 写失败时被连带回滚。

    回归点：裸 rollback() 会撤销整个事务，把调用方更早的未提交写入一并
    丢掉（静默数据丢失）。保存点作用域的回滚只撤销本函数自己的写入。
    """
    conn = open_db_for_test(tmp_path)
    # 调用方的未提交写入
    conn.execute(
        "INSERT INTO cb_positions (code, name, entry_date, entry_price, shares) "
        "VALUES ('CALLER','x','2026-10-20',100.0,50)"
    )
    assert [r["code"] for r in get_cb_positions(conn)] == ["CALLER"]

    with pytest.raises(sqlite3.IntegrityError):
        replace_cb_picks(conn, "2026-10-24", [
            {"rank": 1, "code": "NEW"},
            {"rank": 2, "code": "NEW"},
        ])

    # 调用方的写入必须幸存
    rows = get_cb_positions(conn)
    assert [r["code"] for r in rows] == ["CALLER"], "调用方的未提交写入被连带回滚了"
    assert rows[0]["shares"] == 50

    conn.commit()
    conn.close()

    conn2 = open_db_for_test(tmp_path)
    rows = get_cb_positions(conn2)
    conn2.close()
    assert [r["code"] for r in rows] == ["CALLER"], "调用方的写入未能落库"


def test_failed_write_leaves_no_open_savepoint(tmp_path):
    """失败后保存点必须被释放，不得残留在连接上。"""
    conn = open_db_for_test(tmp_path)
    with pytest.raises(sqlite3.IntegrityError):
        replace_cb_picks(conn, "2026-10-24", [
            {"rank": 1, "code": "NEW"},
            {"rank": 2, "code": "NEW"},
        ])
    # 若保存点残留，后续同名 SAVEPOINT 会嵌套报错
    replace_cb_picks(conn, "2026-10-24", [{"rank": 1, "code": "OK"}])
    rows = get_cb_picks(conn, "2026-10-24")
    conn.close()
    assert [r["code"] for r in rows] == ["OK"]


def test_upsert_position_failure_keeps_connection_clean(tmp_path):
    """position 写入在 SQL 层失败时，旧持仓保留且连接干净。"""
    conn = open_db_for_test(tmp_path)
    upsert_cb_position(conn, {
        "code": "113059", "entry_date": "2026-10-20",
        "entry_price": 115.0, "shares": 50,
    })

    # shares 传非整数，int() 转换失败 -> 事务中途抛错
    with pytest.raises(ValueError):
        upsert_cb_position(conn, {
            "code": "BAD", "entry_date": "2026-10-20",
            "entry_price": 1.0, "shares": "not-an-int",
        })

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


# --- 入参校验：缺失必填字段必须报错，而不是静默写入默认值 ---

def test_replace_picks_requires_rank(tmp_path):
    """缺 rank 必须报错，不能静默写 rank=0（两行会撞唯一索引）。"""
    conn = open_db_for_test(tmp_path)
    with pytest.raises(ValueError):
        replace_cb_picks(conn, "2026-10-24", [{"code": "113059"}])
    assert get_cb_picks(conn, "2026-10-24") == []
    conn.close()


def test_replace_picks_requires_rank_on_every_row(tmp_path):
    """批次中间缺 rank 也必须整批失败，旧榜单保持不变。"""
    conn = open_db_for_test(tmp_path)
    replace_cb_picks(conn, "2026-10-24", [{"rank": 1, "code": "OLD"}])
    with pytest.raises(ValueError):
        replace_cb_picks(conn, "2026-10-24", [
            {"rank": 1, "code": "A"},
            {"code": "B"},
        ])
    rows = get_cb_picks(conn, "2026-10-24")
    conn.close()
    assert [r["code"] for r in rows] == ["OLD"], "校验失败不得改动旧榜单"


@pytest.mark.parametrize("missing", ["entry_date", "entry_price", "shares"])
def test_upsert_position_requires_key_fields(tmp_path, missing):
    """entry_date / entry_price / shares 缺失必须报错。

    shares 缺失时 0 能通过 NOT NULL 检查，但 0 股持仓语义上无效，
    会破坏退出/风险预警，因此必须显式拒绝。
    """
    conn = open_db_for_test(tmp_path)
    row = {
        "code": "113059", "entry_date": "2026-10-20",
        "entry_price": 115.0, "shares": 50,
    }
    row.pop(missing)
    with pytest.raises(ValueError):
        upsert_cb_position(conn, row)
    assert get_cb_positions(conn) == []
    conn.close()


def test_upsert_position_rejects_zero_shares(tmp_path):
    """0 股持仓必须被拒绝（shares=0 能过 NOT NULL，但语义无效）。"""
    conn = open_db_for_test(tmp_path)
    with pytest.raises(ValueError):
        upsert_cb_position(conn, {
            "code": "113059", "entry_date": "2026-10-20",
            "entry_price": 115.0, "shares": 0,
        })
    assert get_cb_positions(conn) == []
    conn.close()


def test_upsert_position_requires_code(tmp_path):
    conn = open_db_for_test(tmp_path)
    with pytest.raises(ValueError):
        upsert_cb_position(conn, {
            "code": "  ", "entry_date": "2026-10-20",
            "entry_price": 115.0, "shares": 50,
        })
    conn.close()


def test_listed_string_zero_is_not_listed(tmp_path):
    """listed="0" 是字符串真值，不能被当成已上市。"""
    conn = open_db_for_test(tmp_path)
    upsert_cb_universe_rows(conn, "2026-10-24", [
        {"code": "113059", "price": 115.3, "listed": "0"},
        {"code": "113060", "price": 116.0, "listed": "1"},
        {"code": "113061", "price": 117.0, "listed": True},
        {"code": "113062", "price": 118.0},
    ])
    rows = {r["code"]: r["listed"] for r in get_cb_universe(conn, "2026-10-24")}
    conn.close()
    assert rows == {"113059": 0, "113060": 1, "113061": 1, "113062": 0}
