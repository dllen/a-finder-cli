"""可转债策略的 SQLite 读写层。

只操作 `cb_` 前缀的表，与股票体系（daily_picks / trade_plan / open_positions）
完全隔离。所有函数接受一个已打开的 sqlite3.Connection。
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from typing import Any, Dict, Iterable, List


def _now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _as_json(value: Any) -> Any:
    """dict / list 序列化成 JSON 文本；其余原样返回。"""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return value


# ---------------------------------------------------------------------------
# cb_universe
# ---------------------------------------------------------------------------

def upsert_cb_universe_rows(
    conn: sqlite3.Connection,
    trade_date: str,
    rows: Iterable[Dict[str, Any]],
) -> int:
    """写入全市场快照。同一 (trade_date, code) 覆盖旧值。返回写入行数。"""
    now = _now()
    data = []
    for r in rows:
        code = str(r.get("code") or "").strip()
        if not code:
            continue
        data.append(
            (
                trade_date, code,
                r.get("name"), r.get("stock_code"), r.get("stock_name"),
                r.get("price"), r.get("convert_price"), r.get("convert_value"),
                r.get("premium_rate"), r.get("stock_price"), r.get("rating"),
                r.get("issue_size"), 1 if r.get("listed") else 0,
                now,
            )
        )
    if not data:
        return 0
    conn.executemany(
        """INSERT INTO cb_universe
           (trade_date, code, name, stock_code, stock_name, price, convert_price,
            convert_value, premium_rate, stock_price, rating, issue_size, listed,
            updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(trade_date, code) DO UPDATE SET
             name=excluded.name, stock_code=excluded.stock_code,
             stock_name=excluded.stock_name, price=excluded.price,
             convert_price=excluded.convert_price,
             convert_value=excluded.convert_value,
             premium_rate=excluded.premium_rate, stock_price=excluded.stock_price,
             rating=excluded.rating, issue_size=excluded.issue_size,
             listed=excluded.listed, updated_at=excluded.updated_at""",
        data,
    )
    conn.commit()
    return len(data)


def get_cb_universe(
    conn: sqlite3.Connection,
    trade_date: str,
) -> List[Dict[str, Any]]:
    cur = conn.execute(
        "SELECT * FROM cb_universe WHERE trade_date=? ORDER BY code", (trade_date,)
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# cb_factors
# ---------------------------------------------------------------------------

def upsert_cb_factors_rows(
    conn: sqlite3.Connection,
    trade_date: str,
    rows: Iterable[Dict[str, Any]],
) -> int:
    now = _now()
    data = []
    for r in rows:
        code = str(r.get("code") or "").strip()
        if not code:
            continue
        data.append(
            (
                trade_date, code,
                r.get("bond_floor"), r.get("ytm"), r.get("year_left"),
                r.get("redeem_dist"), r.get("put_dist"), r.get("adj_count"),
                r.get("redeem_status"), _as_json(r.get("factor_json")), now,
            )
        )
    if not data:
        return 0
    conn.executemany(
        """INSERT INTO cb_factors
           (trade_date, code, bond_floor, ytm, year_left, redeem_dist, put_dist,
            adj_count, redeem_status, factor_json, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(trade_date, code) DO UPDATE SET
             bond_floor=excluded.bond_floor, ytm=excluded.ytm,
             year_left=excluded.year_left, redeem_dist=excluded.redeem_dist,
             put_dist=excluded.put_dist, adj_count=excluded.adj_count,
             redeem_status=excluded.redeem_status,
             factor_json=excluded.factor_json, updated_at=excluded.updated_at""",
        data,
    )
    conn.commit()
    return len(data)


def get_cb_factors(
    conn: sqlite3.Connection,
    trade_date: str,
) -> List[Dict[str, Any]]:
    cur = conn.execute(
        "SELECT * FROM cb_factors WHERE trade_date=? ORDER BY code", (trade_date,)
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# cb_picks
# ---------------------------------------------------------------------------

def replace_cb_picks(
    conn: sqlite3.Connection,
    trade_date: str,
    rows: Iterable[Dict[str, Any]],
) -> int:
    """覆盖写入某日榜单。先删该日旧行，再插入新行。"""
    now = _now()
    data = []
    for r in rows:
        code = str(r.get("code") or "").strip()
        if not code:
            continue
        data.append(
            (
                trade_date, int(r.get("rank") or 0), code, r.get("name"),
                r.get("price"), r.get("total_score"), r.get("a_score"),
                r.get("b_score"), r.get("c_score"), r.get("size_pct"),
                r.get("shares"), r.get("stop_price"), r.get("tp_price"),
                r.get("reason"), r.get("risk_note"),
                _as_json(r.get("factor_counts")), now,
            )
        )
    conn.execute("DELETE FROM cb_picks WHERE trade_date=?", (trade_date,))
    if data:
        conn.executemany(
            """INSERT INTO cb_picks
               (trade_date, rank, code, name, price, total_score, a_score, b_score,
                c_score, size_pct, shares, stop_price, tp_price, reason, risk_note,
                factor_counts, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            data,
        )
    conn.commit()
    return len(data)


def get_cb_picks(
    conn: sqlite3.Connection,
    trade_date: str,
) -> List[Dict[str, Any]]:
    cur = conn.execute(
        "SELECT * FROM cb_picks WHERE trade_date=? ORDER BY rank", (trade_date,)
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# cb_positions
# ---------------------------------------------------------------------------

def upsert_cb_position(
    conn: sqlite3.Connection,
    row: Dict[str, Any],
) -> None:
    code = str(row.get("code") or "").strip()
    if not code:
        raise ValueError("cb_position requires code")
    conn.execute(
        """INSERT INTO cb_positions
           (code, name, stock_code, entry_date, entry_price, shares,
            stop_price, tp_price, note, updated_at)
           VALUES (?,?,?,?,?,?,?,?,?,?)
           ON CONFLICT(code) DO UPDATE SET
             name=excluded.name, stock_code=excluded.stock_code,
             entry_date=excluded.entry_date, entry_price=excluded.entry_price,
             shares=excluded.shares, stop_price=excluded.stop_price,
             tp_price=excluded.tp_price, note=excluded.note,
             updated_at=excluded.updated_at""",
        (
            code, row.get("name"), row.get("stock_code"), row.get("entry_date"),
            row.get("entry_price"), int(row.get("shares") or 0),
            row.get("stop_price"), row.get("tp_price"), row.get("note"), _now(),
        ),
    )
    conn.commit()


def get_cb_positions(
    conn: sqlite3.Connection,
) -> List[Dict[str, Any]]:
    cur = conn.execute("SELECT * FROM cb_positions ORDER BY entry_date, code")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def delete_cb_position(conn: sqlite3.Connection, code: str) -> None:
    conn.execute("DELETE FROM cb_positions WHERE code=?", (code,))
    conn.commit()
