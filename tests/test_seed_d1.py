"""seed_d1.py 把本地 hs300.db 转成 D1 可执行的 SQL。"""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from db_repository import open_db

_SPEC = importlib.util.spec_from_file_location(
    "seed_d1",
    Path(__file__).resolve().parent.parent / "scripts" / "seed_d1.py",
)
seed = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(seed)


@pytest.fixture
def db(tmp_path):
    path = str(tmp_path / "m.db")
    conn = open_db(path)
    for code, close in (("600519", 1500.0), ("000001", 12.0)):
        conn.execute(
            "INSERT INTO daily_prices (code, trade_date, open, close, high, low,"
            " volume, amount, amplitude, pct_change, change, turnover)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (code, "2026-10-23", close, close, close, close, 1000, 1e6, 1.0, 0.5, 1.0, 2.0),
        )
        conn.execute(
            "INSERT INTO hs300_metadata (code, name) VALUES (?,?)",
            (code, f"票{code}"),
        )
    conn.commit()
    yield path, conn
    conn.close()


def test_symbols_sql(db):
    path, _ = db
    sql = seed.build_symbols_sql(path)
    assert "INSERT OR REPLACE INTO market_symbols" in sql
    assert "'600519'" in sql and "'票600519'" in sql
    # 301 只以内用单条多值 INSERT，避免每行一条语句
    assert sql.count("INSERT") == 1


def test_prices_sql_batched(db):
    path, _ = db
    sql, rows = seed.build_prices_sql(path, batch=1)
    assert rows == 2
    assert "INSERT OR REPLACE INTO market_prices" in sql
    assert "'2026-10-23'" in sql
    assert "2.0" in sql          # turnover 必须写进去
    # batch=1 时每行一条语句
    assert sql.count("INSERT") == 2


def test_prices_sql_respects_since(db):
    path, conn = db
    conn.execute(
        "INSERT INTO daily_prices (code, trade_date, open, close, high, low,"
        " volume, amount) VALUES ('600519','2024-01-02',1,1,1,1,1,1)"
    )
    conn.commit()
    _, rows = seed.build_prices_sql(path, since="2026-01-01")
    assert rows == 2            # 2024 的那行被 since 过滤掉
