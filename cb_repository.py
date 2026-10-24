"""可转债策略的 SQLite 读写层。

只操作 `cb_` 前缀的表，与股票体系（daily_picks / trade_plan / open_positions）
完全隔离。所有函数接受一个已打开的 sqlite3.Connection。

写入约定：每个写函数都是原子的。sqlite3 的 executemany 逐行插入，批次
中途违反约束时前面的行已落库、后面的行没有；若不显式回滚，连接会停留在
「半成品」状态，调用方后续任意一次 commit() 都会把坏状态持久化。

回滚范围：失败时只回滚本函数自己的写入（SAVEPOINT 作用域），
调用方在本连接上更早的未提交写入不受影响。

提交范围：写函数末尾的 conn.commit() 提交的是整个连接上的事务，
因此调用方更早的未提交写入也会一并被提交——这是本项目
「每次写入自行提交」约定（与 db_repository.py 一致）的固有行为。
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


def _as_listed(value: Any) -> int:
    """把 listed 归一化为 0/1。

    注意不能用 `1 if value else 0`：字符串 "0" 是真值，会被误判为已上市。
    """
    if value in (True, 1, "1", "true", "True"):
        return 1
    return 0


# ---------------------------------------------------------------------------
# 写入原语
# ---------------------------------------------------------------------------

_SAVEPOINT = "cb_write"


def _begin(conn: sqlite3.Connection) -> None:
    """开启一个保存点，作为本函数写入的回滚边界。"""
    conn.execute(f"SAVEPOINT {_SAVEPOINT}")


def _rollback_to(conn: sqlite3.Connection) -> None:
    """回滚到保存点：只撤销本函数自己的写入。

    不使用裸 rollback()——那会撤销整个事务，连带把调用方更早的未提交
    写入一并丢掉。ROLLBACK TO 之后必须再 RELEASE，否则保存点仍然定义着。
    """
    conn.execute(f"ROLLBACK TO SAVEPOINT {_SAVEPOINT}")
    conn.execute(f"RELEASE SAVEPOINT {_SAVEPOINT}")


def _release(conn: sqlite3.Connection) -> None:
    """释放保存点。"""
    conn.execute(f"RELEASE SAVEPOINT {_SAVEPOINT}")


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
                r.get("issue_size"), _as_listed(r.get("listed")),
                now,
            )
        )
    if not data:
        return 0
    _begin(conn)
    try:
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
        _release(conn)
        conn.commit()
    except Exception:
        _rollback_to(conn)
        raise
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
    _begin(conn)
    try:
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
        _release(conn)
        conn.commit()
    except Exception:
        _rollback_to(conn)
        raise
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
    """覆盖写入某日榜单。先删该日旧行，再插入新行。

    整个替换是原子的：任一行插入失败（含同日同 code 触发唯一索引）
    则整体回滚，旧榜单完整保留，连接回到干净状态。
    回滚只影响本函数的写入，调用方更早的未提交写入不受影响。
    """
    now = _now()
    data = []
    for r in rows:
        code = str(r.get("code") or "").strip()
        if not code:
            continue
        rank = r.get("rank")
        if rank is None:
            raise ValueError(f"cb_picks row requires rank: code={code!r}")
        data.append(
            (
                trade_date, int(rank), code, r.get("name"),
                r.get("price"), r.get("total_score"), r.get("a_score"),
                r.get("b_score"), r.get("c_score"), r.get("size_pct"),
                r.get("shares"), r.get("stop_price"), r.get("tp_price"),
                r.get("reason"), r.get("risk_note"),
                _as_json(r.get("factor_counts")), now,
            )
        )
    _begin(conn)
    try:
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
        _release(conn)
        conn.commit()
    except Exception:
        _rollback_to(conn)
        raise
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
    # entry_date / entry_price / shares 是 NOT NULL 列。其中 shares=0 能通过
    # NOT NULL 检查，但 0 股持仓在语义上无效（会破坏退出/风险预警），
    # 故与 code 一并显式校验，缺失时抛 ValueError。
    for field in ("entry_date", "entry_price", "shares"):
        if row.get(field) is None:
            raise ValueError(f"cb_position requires {field}")
    shares = int(row["shares"])
    if shares <= 0:
        raise ValueError("cb_position requires shares > 0")
    _begin(conn)
    try:
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
                row.get("entry_price"), shares,
                row.get("stop_price"), row.get("tp_price"), row.get("note"), _now(),
            ),
        )
        _release(conn)
        conn.commit()
    except Exception:
        _rollback_to(conn)
        raise


def get_cb_positions(
    conn: sqlite3.Connection,
) -> List[Dict[str, Any]]:
    cur = conn.execute("SELECT * FROM cb_positions ORDER BY entry_date, code")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def delete_cb_position(conn: sqlite3.Connection, code: str) -> None:
    _begin(conn)
    try:
        conn.execute("DELETE FROM cb_positions WHERE code=?", (code,))
        _release(conn)
        conn.commit()
    except Exception:
        _rollback_to(conn)
        raise
