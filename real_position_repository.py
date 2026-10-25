"""10w 决策台：用户真实持仓的读写。

与 cb_repository 同风格——SAVEPOINT 原子化写入，不用裸 rollback()。
表名带 real_ 前缀，与平台模拟的 trade_plan 明确区分。
"""
from __future__ import annotations

import datetime as dt
import sqlite3
from typing import Any, Dict, List

_SAVEPOINT = "sp_real_position"


def _now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def _begin(conn: sqlite3.Connection) -> None:
    conn.execute(f"SAVEPOINT {_SAVEPOINT}")


def _rollback_to(conn: sqlite3.Connection) -> None:
    conn.execute(f"ROLLBACK TO SAVEPOINT {_SAVEPOINT}")
    conn.execute(f"RELEASE SAVEPOINT {_SAVEPOINT}")


def _release(conn: sqlite3.Connection) -> None:
    conn.execute(f"RELEASE SAVEPOINT {_SAVEPOINT}")


def _validated(row: Dict[str, Any]) -> tuple:
    code = str(row.get("code") or "").strip()
    if not code:
        raise ValueError("position requires code")
    if row.get("shares") is None:
        raise ValueError("position requires shares")
    shares = int(row["shares"])
    if shares <= 0:
        raise ValueError("position requires shares > 0")
    if row.get("entry_price") is None:
        raise ValueError("position requires entry_price")
    entry_price = float(row["entry_price"])
    if entry_price <= 0:
        raise ValueError("position requires entry_price > 0")
    stop = row.get("stop_price")
    stop_price = float(stop) if stop is not None else None
    return code, shares, entry_price, stop_price


# ---------------------------------------------------------------------------
# 股票
# ---------------------------------------------------------------------------

def upsert_real_position(conn: sqlite3.Connection, row: Dict[str, Any]) -> None:
    code, shares, entry_price, stop_price = _validated(row)
    _begin(conn)
    try:
        conn.execute(
            """INSERT INTO real_positions
               (code, name, shares, entry_price, stop_price, entry_date, note, updated_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(code) DO UPDATE SET
                 name=excluded.name, shares=excluded.shares,
                 entry_price=excluded.entry_price, stop_price=excluded.stop_price,
                 entry_date=excluded.entry_date, note=excluded.note,
                 updated_at=excluded.updated_at""",
            (code, row.get("name"), shares, entry_price, stop_price,
             row.get("entry_date"), row.get("note"), _now()),
        )
        _release(conn)
        conn.commit()
    except Exception:
        _rollback_to(conn)
        raise


def get_real_positions(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    cur = conn.execute("SELECT * FROM real_positions ORDER BY code")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def delete_real_position(conn: sqlite3.Connection, code: str) -> None:
    _begin(conn)
    try:
        conn.execute("DELETE FROM real_positions WHERE code=?", (str(code),))
        _release(conn)
        conn.commit()
    except Exception:
        _rollback_to(conn)
        raise


# ---------------------------------------------------------------------------
# 可转债
# ---------------------------------------------------------------------------

def upsert_real_cb_position(conn: sqlite3.Connection, row: Dict[str, Any]) -> None:
    code, shares, entry_price, stop_price = _validated(row)
    _begin(conn)
    try:
        conn.execute(
            """INSERT INTO real_cb_positions
               (code, name, shares, entry_price, stop_price, entry_date, note, updated_at)
               VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(code) DO UPDATE SET
                 name=excluded.name, shares=excluded.shares,
                 entry_price=excluded.entry_price, stop_price=excluded.stop_price,
                 entry_date=excluded.entry_date, note=excluded.note,
                 updated_at=excluded.updated_at""",
            (code, row.get("name"), shares, entry_price, stop_price,
             row.get("entry_date"), row.get("note"), _now()),
        )
        _release(conn)
        conn.commit()
    except Exception:
        _rollback_to(conn)
        raise


def get_real_cb_positions(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    cur = conn.execute("SELECT * FROM real_cb_positions ORDER BY code")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def delete_real_cb_position(conn: sqlite3.Connection, code: str) -> None:
    _begin(conn)
    try:
        conn.execute("DELETE FROM real_cb_positions WHERE code=?", (str(code),))
        _release(conn)
        conn.commit()
    except Exception:
        _rollback_to(conn)
        raise