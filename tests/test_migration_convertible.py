"""可转债迁移测试：4 张 cb_ 表的 schema / 约束 / 索引校验。"""
import re
import sqlite3
from pathlib import Path

import pytest

from db_repository import open_db

MIGRATIONS_DIR = Path(__file__).parent.parent / "db" / "migrations"
ORIG_MIGRATION = MIGRATIONS_DIR / "2026_10_24_convertible_bond.sql"
CONSTRAINTS_MIGRATION = MIGRATIONS_DIR / "2026_10_24_convertible_bond_constraints.sql"


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


# --- 键列 NOT NULL 回归（防止存量库缺约束） ---

KEY_NOTNULL = {
    "cb_universe": ["trade_date", "code"],
    "cb_factors": ["trade_date", "code"],
    "cb_picks": ["trade_date", "rank", "code"],
    "cb_positions": ["entry_date", "entry_price", "shares"],
}


@pytest.mark.parametrize("table,cols", sorted(KEY_NOTNULL.items()))
def test_cb_key_columns_notnull(tmp_path, table, cols):
    conn = open_db(str(tmp_path / "m.db"))
    info = _info(conn, table)
    conn.close()
    for c in cols:
        assert info[c][1] == 1, f"{table}.{c} 必须是 NOT NULL"


def _schema_snapshot(conn):
    """抓取全部 cb_ 表的 (列名, 类型, notnull, 默认值, pk位置) + 索引集合。"""
    snap = {}
    tables = [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'cb_%' ORDER BY name"
        )
    ]
    for t in tables:
        info = [
            (r[1], r[2], r[3], r[4], r[5]) for r in conn.execute(f"PRAGMA table_info({t})")
        ]
        idx = sorted(
            (r[0], (r[1] or "").split()) for r in conn.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='index' AND tbl_name=?", (t,)
            )
        )
        snap[t] = (info, idx)
    return snap


def _strip_not_null(sql):
    """模拟「已部署的旧版迁移」：去掉列上的 NOT NULL。"""
    return re.sub(r"\s+NOT NULL(?=\s*,|\s*\n)", "", sql)


def test_cb_constraints_migration_converges_existing_db(tmp_path):
    """存量库（只应用过旧版迁移）跑完约束迁移后，schema 必须与全新库一致。

    回归点：旧版迁移的 cb_universe/cb_factors/cb_picks 键列没有 NOT NULL，
    若约束迁移不补齐，两条路径的 PRAGMA table_info 会不一致。
    """
    orig_sql = ORIG_MIGRATION.read_text(encoding="utf-8")
    cons_sql = CONSTRAINTS_MIGRATION.read_text(encoding="utf-8")

    # 路径 A：全新库 —— 原始迁移 + 约束迁移
    fresh = sqlite3.connect(str(tmp_path / "fresh.db"))
    fresh.executescript(orig_sql)
    fresh.executescript(cons_sql)
    fresh.commit()
    snap_fresh = _schema_snapshot(fresh)

    # 路径 B：存量库 —— 旧版原始迁移（去掉 NOT NULL）+ 约束迁移
    existing = sqlite3.connect(str(tmp_path / "existing.db"))
    existing.executescript(_strip_not_null(orig_sql))
    existing.commit()
    # 确认旧版确实缺 NOT NULL（保证本测试真的覆盖了该场景）
    old_info = _info(existing, "cb_universe")
    assert old_info["trade_date"][1] == 0, "旧版模拟失效：trade_date 本就不缺 NOT NULL"

    # 存量数据
    existing.execute("INSERT INTO cb_universe (trade_date, code) VALUES ('2026-10-24','113059')")
    existing.execute("INSERT INTO cb_factors (trade_date, code) VALUES ('2026-10-24','113059')")
    existing.execute("INSERT INTO cb_picks (trade_date, rank, code) VALUES ('2026-10-24',1,'113059')")
    existing.execute(
        "INSERT INTO cb_positions (code, entry_date, entry_price, shares) "
        "VALUES ('113059','2026-10-24',100.0,10)"
    )
    existing.commit()

    existing.executescript(cons_sql)
    existing.commit()
    snap_existing = _schema_snapshot(existing)

    assert set(snap_fresh) == set(snap_existing)
    for table in sorted(snap_fresh):
        info_f, idx_f = snap_fresh[table]
        info_e, idx_e = snap_existing[table]
        assert info_f == info_e, f"{table} 列定义未收敛：\n新={info_f}\n旧={info_e}"
        assert idx_f == idx_e, f"{table} 索引未收敛：\n新={idx_f}\n旧={idx_e}"

    # 存量数据必须保留
    for table in ["cb_universe", "cb_factors", "cb_picks", "cb_positions"]:
        rows = existing.execute(f"SELECT * FROM {table}").fetchall()
        assert len(rows) == 1, f"{table} 存量数据丢失：{rows}"

    fresh.close()
    existing.close()


def test_cb_constraints_migration_is_idempotent(tmp_path):
    """约束迁移重复执行不得改变 schema（已是目标态时不得报错）。"""
    cons_sql = CONSTRAINTS_MIGRATION.read_text(encoding="utf-8")
    orig_sql = ORIG_MIGRATION.read_text(encoding="utf-8")

    conn = sqlite3.connect(str(tmp_path / "idem.db"))
    conn.executescript(orig_sql)
    conn.executescript(cons_sql)
    conn.commit()
    first = _schema_snapshot(conn)

    conn.executescript(cons_sql)
    conn.commit()
    second = _schema_snapshot(conn)

    assert first == second
    conn.close()
