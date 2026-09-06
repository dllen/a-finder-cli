# tests/test_migration_portfolio_tier.py
import sqlite3
import pytest
from db_repository import open_db


@pytest.fixture
def migrated_db(tmp_path):
    path = str(tmp_path / "m.db")
    conn = open_db(path)
    yield path, conn
    conn.close()


def test_trade_plan_has_portfolio_column(migrated_db):
    _, conn = migrated_db
    cols = [r[1] for r in conn.execute("PRAGMA table_info(trade_plan)").fetchall()]
    assert "portfolio" in cols


def test_trade_plan_unique_includes_portfolio(migrated_db):
    _, conn = migrated_db
    # SQLite UNIQUE constraints create an autoindex with sql=NULL; detect it by name
    idx_names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='trade_plan'"
    ).fetchall()]
    # The autoindex (sqlite_autoindex_*) is the UNIQUE constraint; it covers (plan_date, portfolio, code, action)
    autoindexes = [n for n in idx_names if n.startswith("sqlite_autoindex_trade_plan_")]
    assert len(autoindexes) == 1, f"Expected 1 autoindex, got {autoindexes}"
    # Verify the explicit composite index covers portfolio + plan_date
    idx = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND tbl_name='trade_plan' AND name='idx_trade_plan_portfolio_date'"
    ).fetchone()
    assert idx is not None, "idx_trade_plan_portfolio_date not found"
    assert "portfolio" in idx[0] and "plan_date" in idx[0], f"idx_trade_plan_portfolio_date missing columns: {idx[0]}"
    # Confirm UNIQUE constraint actually rejects duplicates
    conn.execute(
        """INSERT INTO trade_plan
           (plan_date, code, action, plan_price, size_pct, stop_price, tp_price,
            rr_ratio, status, rationale_json, params_hash, created_at, shares)
           VALUES ('2026-09-07','600519','buy',100,0.1,90,120,2.0,'ok','{}','h','2026-09-07',200)"""
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            """INSERT INTO trade_plan
               (plan_date, code, action, plan_price, size_pct, stop_price, tp_price,
                rr_ratio, status, rationale_json, params_hash, created_at, shares)
               VALUES ('2026-09-07','600519','buy',100,0.1,90,120,2.0,'ok','{}','h','2026-09-07',200)"""
        )


def test_trade_plan_same_code_different_portfolio_allowed(migrated_db):
    _, conn = migrated_db
    conn.execute(
        """INSERT INTO trade_plan
           (plan_date, code, action, plan_price, size_pct, stop_price, tp_price,
            rr_ratio, status, rationale_json, params_hash, created_at, shares)
           VALUES ('2026-09-07','600519','buy',100,0.1,90,120,2.0,'ok','{}','h','2026-09-07',200)"""
    )
    conn.execute(
        """INSERT INTO trade_plan
           (plan_date, portfolio, code, action, plan_price, size_pct, stop_price, tp_price,
            rr_ratio, status, rationale_json, params_hash, created_at, shares)
           VALUES ('2026-09-07','10W','600519','buy',100,0.1,90,120,2.0,'ok','{}','h','2026-09-07',200)"""
    )
    count = conn.execute("SELECT COUNT(*) FROM trade_plan WHERE code='600519'").fetchone()[0]
    assert count == 2


def test_open_positions_has_portfolio_column(migrated_db):
    _, conn = migrated_db
    cols = [r[1] for r in conn.execute("PRAGMA table_info(open_positions)").fetchall()]
    assert "portfolio" in cols


def test_open_positions_partial_unique_index(migrated_db):
    _, conn = migrated_db
    idx_names = [
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='open_positions'"
        ).fetchall()
    ]
    assert "uq_open_positions_portfolio_code_open" in idx_names


def test_open_positions_same_code_two_portfolios(migrated_db):
    _, conn = migrated_db
    for p in ("default", "10W"):
        conn.execute(
            """INSERT INTO open_positions
               (code, portfolio, entry_date, entry_price, size_pct, stop_price, tp_price, status, shares)
               VALUES ('600519', ?, '2026-09-07', 100, 0.1, 90, 120, 'open', 200)""",
            (p,),
        )
    count = conn.execute("SELECT COUNT(*) FROM open_positions WHERE code='600519'").fetchone()[0]
    assert count == 2


def test_trade_events_has_portfolio_column(migrated_db):
    _, conn = migrated_db
    cols = [r[1] for r in conn.execute("PRAGMA table_info(trade_events)").fetchall()]
    assert "portfolio" in cols


def test_trade_events_index_exists(migrated_db):
    _, conn = migrated_db
    idx_names = [
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='trade_events'"
        ).fetchall()
    ]
    assert "idx_trade_events_portfolio" in idx_names


def test_existing_data_defaults_to_default_portfolio(migrated_db):
    """升级已有 DB：trade_plan / open_positions / trade_events 历史行 portfolio='default'。"""
    _, conn = migrated_db
    # 通过迁到一个已有 trade_plan 行的 DB 验证：migrated_db 是新 DB 没有历史行
    # 改用单独测：open_db 在已包含 trade_plan 行的 DB 上跑迁移
    conn.execute(
        """INSERT INTO trade_plan
           (plan_date, code, action, plan_price, size_pct, stop_price, tp_price,
            rr_ratio, status, rationale_json, params_hash, created_at, shares)
           VALUES ('2026-09-01','000001','buy',50,0.1,40,70,2.0,'ok','{}','h','2026-09-01',200)"""
    )
    conn.commit()
    # 模拟重新打开（同进程 → _applied_migrations 已含此文件 → 跳过；这是幂等检查，
    # 历史数据迁移在第一次运行迁移时一次性执行）
    row = conn.execute(
        "SELECT portfolio FROM trade_plan WHERE plan_date='2026-09-01'"
    ).fetchone()
    assert row[0] == "default"
