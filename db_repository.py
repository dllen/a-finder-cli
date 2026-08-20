import datetime as dt
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from db_schema import ensure_schema

MIGRATIONS_DIR = Path(__file__).parent / "db" / "migrations"


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Apply unapplied SQL files in db/migrations/ in lexical order.

    Each file is recorded in `_applied_migrations` after successful execution,
    so non-idempotent statements (e.g. ALTER TABLE ADD COLUMN) only run once
    across the lifetime of a database, even though _run_migrations is called
    on every open_db().
    """
    if not MIGRATIONS_DIR.exists():
        return
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS _applied_migrations (
            filename TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL
        )
        """
    )
    conn.commit()
    applied = {
        row[0]
        for row in conn.execute("SELECT filename FROM _applied_migrations").fetchall()
    }
    for sql_file in sorted(MIGRATIONS_DIR.glob("*.sql")):
        name = sql_file.name
        if name in applied:
            continue
        try:
            conn.executescript(sql_file.read_text())
            conn.execute(
                "INSERT INTO _applied_migrations (filename, applied_at) VALUES (?, ?)",
                (name, dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            )
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
            raise


@dataclass
class PriceRow:
    code: str
    trade_date: str
    open_price: float
    close_price: float
    high_price: float
    low_price: float
    volume: float
    amount: float
    amplitude: float
    pct_change: float
    change: float
    turnover: float


@dataclass
class StockMeta:
    code: str
    name: str
    industry: str
    region: str


def open_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    ensure_schema(conn)
    _run_migrations(conn)
    conn.commit()
    return conn


def upsert_constituents(conn: sqlite3.Connection, mapping: Dict[str, str]) -> None:
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = []
    for code, name in mapping.items():
        exchange = "SH" if code.startswith(("60", "688")) else "SZ"
        rows.append((code, name, exchange, now))
    conn.executemany(
        """
        INSERT INTO hs300_constituents (code, name, exchange, updated_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(code) DO UPDATE SET
            name=excluded.name,
            exchange=excluded.exchange,
            updated_at=excluded.updated_at
        """,
        rows,
    )


def upsert_metadata(conn: sqlite3.Connection, rows: Iterable[StockMeta]) -> int:
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data = [(r.code, r.name, r.industry, r.region, now) for r in rows]
    if not data:
        return 0
    conn.executemany(
        """
        INSERT INTO hs300_metadata (code, name, industry, region, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(code) DO UPDATE SET
            name=excluded.name,
            industry=excluded.industry,
            region=excluded.region,
            updated_at=excluded.updated_at
        """,
        data,
    )
    return len(data)


def get_metadata_by_code(conn: sqlite3.Connection, code: str) -> Optional[StockMeta]:
    cur = conn.execute(
        "SELECT code, COALESCE(name, ''), COALESCE(industry, ''), COALESCE(region, '') FROM hs300_metadata WHERE code = ?",
        (code,),
    )
    row = cur.fetchone()
    if not row:
        return None
    return StockMeta(code=row[0], name=row[1], industry=row[2], region=row[3])


def get_all_codes(conn: sqlite3.Connection) -> List[str]:
    cur = conn.execute("SELECT code FROM hs300_constituents ORDER BY code")
    rows = [row[0] for row in cur.fetchall()]
    if rows:
        return rows
    cur = conn.execute("SELECT DISTINCT code FROM daily_prices ORDER BY code")
    return [row[0] for row in cur.fetchall()]


def get_last_trade_date(conn: sqlite3.Connection, code: str) -> Optional[str]:
    cur = conn.execute(
        "SELECT MAX(trade_date) FROM daily_prices WHERE code = ?",
        (code,),
    )
    row = cur.fetchone()
    return row[0] if row and row[0] else None


def get_fetch_offsets(conn: sqlite3.Connection, codes: List[str]) -> Dict[str, str]:
    if not codes:
        return {}
    placeholders = ",".join("?" for _ in codes)
    cur = conn.execute(
        f"SELECT code, last_trade_date FROM fetch_offsets WHERE code IN ({placeholders})",
        codes,
    )
    return {row[0]: row[1] for row in cur.fetchall()}


def upsert_fetch_offsets(conn: sqlite3.Connection, offsets: Dict[str, str]) -> None:
    if not offsets:
        return
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    rows = [(code, date_str, now) for code, date_str in offsets.items()]
    conn.executemany(
        """
        INSERT INTO fetch_offsets (code, last_trade_date, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(code) DO UPDATE SET
            last_trade_date=excluded.last_trade_date,
            updated_at=excluded.updated_at
        """,
        rows,
    )


def list_fetch_offsets(
    conn: sqlite3.Connection,
    code: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[Tuple[str, str, str]]:
    sql = "SELECT code, last_trade_date, updated_at FROM fetch_offsets"
    params: List = []
    if code:
        sql += " WHERE code = ?"
        params.append(code)
    sql += " ORDER BY code"
    if limit is not None and limit > 0:
        sql += " LIMIT ?"
        params.append(limit)
    cur = conn.execute(sql, params)
    return [(row[0], row[1], row[2]) for row in cur.fetchall()]


def get_trade_date_range(conn: sqlite3.Connection, code: str) -> Tuple[Optional[str], Optional[str]]:
    cur = conn.execute(
        "SELECT MIN(trade_date), MAX(trade_date) FROM daily_prices WHERE code = ?",
        (code,),
    )
    row = cur.fetchone()
    if not row:
        return None, None
    return row[0], row[1]


def get_trade_dates(conn: sqlite3.Connection, code: str, start_date: str, end_date: str) -> List[str]:
    cur = conn.execute(
        """
        SELECT trade_date FROM daily_prices
        WHERE code = ? AND trade_date BETWEEN ? AND ?
        ORDER BY trade_date
        """,
        (code, start_date, end_date),
    )
    return [row[0] for row in cur.fetchall()]


def get_completed_codes(conn: sqlite3.Connection, job_id: str) -> Dict[str, str]:
    cur = conn.execute(
        "SELECT code, status FROM sync_checkpoints WHERE job_id = ? AND status = 'success'",
        (job_id,),
    )
    return {row[0]: row[1] for row in cur.fetchall()}


def get_failed_codes(conn: sqlite3.Connection, job_id: str) -> Dict[str, str]:
    cur = conn.execute(
        "SELECT code, status FROM sync_checkpoints WHERE job_id = ? AND status = 'failed'",
        (job_id,),
    )
    return {row[0]: row[1] for row in cur.fetchall()}


def upsert_checkpoint(
    conn: sqlite3.Connection,
    job_id: str,
    code: str,
    start_date: str,
    end_date: str,
    status: str,
    error: Optional[str],
) -> None:
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute(
        """
        INSERT INTO sync_checkpoints (job_id, code, start_date, end_date, status, updated_at, error)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(job_id, code) DO UPDATE SET
            start_date=excluded.start_date,
            end_date=excluded.end_date,
            status=excluded.status,
            updated_at=excluded.updated_at,
            error=excluded.error
        """,
        (job_id, code, start_date, end_date, status, now, error),
    )


def insert_prices(conn: sqlite3.Connection, rows: Iterable[PriceRow]) -> int:
    data = [
        (
            r.code,
            r.trade_date,
            r.open_price,
            r.close_price,
            r.high_price,
            r.low_price,
            r.volume,
            r.amount,
            r.amplitude,
            r.pct_change,
            r.change,
            r.turnover,
        )
        for r in rows
    ]
    if not data:
        return 0
    conn.executemany(
        """
        INSERT OR REPLACE INTO daily_prices (
            code, trade_date, open, close, high, low, volume, amount, amplitude, pct_change, change, turnover
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        data,
    )
    return len(data)


def insert_trade_plan(
    conn: sqlite3.Connection,
    row: "PlanRow",  # forward ref; imported lazily to avoid import cycle
    plan_date: str,
    params_hash: str,
) -> int:
    """Insert a trade_plan row. Idempotent via UNIQUE(plan_date, code, action).

    Returns plan_id (>0) on insert, 0 on duplicate ignored.
    """
    cur = conn.execute(
        """INSERT OR IGNORE INTO trade_plan
        (plan_date, code, action, plan_price, size_pct, stop_price, tp_price,
         rr_ratio, status, reason, rationale_json, params_hash, created_at, shares)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            plan_date, row.code, row.action, row.plan_price, row.size_pct,
            row.stop_price, row.tp_price, row.rr_ratio, row.status, row.reason,
            json.dumps(row.rationale), params_hash,
            dt.datetime.utcnow().isoformat(timespec="seconds"),
            row.shares,
        ),
    )
    conn.commit()
    # cur.rowcount=1 on insert, 0 on ignored dup; lastrowid is unreliable on ignore.
    return cur.lastrowid if cur.rowcount > 0 else 0


def get_trade_plan_by_date(
    conn: sqlite3.Connection,
    plan_date: str,
    include_failed: bool = False,
) -> List[Dict]:
    """Return all trade_plan rows for a date with stock name. Excludes status='failed' unless asked.

    Stock name comes from `hs300_metadata` (the table that actually stores names).
    `hs300_constituents.name` is intentionally empty in this codebase — see
    data_providers.fetch_hs300_constituents_from_api for why.
    """
    sql = ("SELECT tp.*, m.name AS name "
           "FROM trade_plan tp LEFT JOIN hs300_metadata m ON m.code = tp.code "
           "WHERE tp.plan_date = ?")
    if not include_failed:
        sql += " AND tp.status = 'ok'"
    sql += " ORDER BY tp.action DESC, tp.code"  # buy first, then hold/exit
    cur = conn.execute(sql, (plan_date,))
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def get_trade_plan_by_date_and_hash(
    conn: sqlite3.Connection,
    plan_date: str,
    params_hash: str,
) -> List[Dict]:
    """Return trade_plan rows for a (plan_date, params_hash). Empty if cache miss.
    Used by build_plan to short-circuit when the plan for this date+params is already persisted.
    """
    cur = conn.execute(
        "SELECT * FROM trade_plan WHERE plan_date = ? AND params_hash = ?",
        (plan_date, params_hash),
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def insert_open_position(
    conn: sqlite3.Connection,
    code: str,
    entry_date: str,
    entry_price: float,
    size_pct: float,
    stop_price: float,
    tp_price: float,
    shares: int = 200,
) -> int:
    """Open a new paper position with a fixed share count. Returns pos_id."""
    cur = conn.execute(
        """INSERT INTO open_positions
        (code, entry_date, entry_price, size_pct, stop_price, tp_price, status, shares)
        VALUES (?,?,?,?,?,?,'open',?)""",
        (code, entry_date, entry_price, size_pct, stop_price, tp_price, shares),
    )
    conn.commit()
    return cur.lastrowid


def accumulate_open_position(
    conn: sqlite3.Connection,
    code: str,
    fill_price: float,
    size_pct: float,
    stop_price: float,
    tp_price: float,
    shares_to_add: int = 200,
    entry_date: str = "",
) -> int:
    """Add shares to an existing open position (weighted-average entry).

    If no open position exists for `code`, opens a fresh one. Returns pos_id.
    """
    row = conn.execute(
        "SELECT pos_id, shares, entry_price FROM open_positions "
        "WHERE code=? AND status='open' LIMIT 1",
        (code,),
    ).fetchone()
    if row is None:
        return insert_open_position(
            conn, code, entry_date, fill_price, size_pct, stop_price, tp_price, shares_to_add
        )
    pos_id, old_shares, old_entry = row
    old_shares = old_shares or 0
    old_entry = old_entry or 0.0
    new_shares = old_shares + shares_to_add
    new_entry = round((old_shares * old_entry + shares_to_add * fill_price) / new_shares, 4)
    conn.execute(
        """UPDATE open_positions
           SET shares=?, entry_price=?, stop_price=?, tp_price=?
           WHERE pos_id=?""",
        (new_shares, new_entry, stop_price, tp_price, pos_id),
    )
    conn.commit()
    return pos_id


def get_open_positions(conn: sqlite3.Connection) -> List[Dict]:
    """Return all open positions."""
    cur = conn.execute(
        """SELECT * FROM open_positions WHERE status='open'
        ORDER BY entry_date, code"""
    )
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def close_open_position(
    conn: sqlite3.Connection,
    pos_id: int,
    close_date: str,
    close_price: float,
    reason: str,
) -> None:
    """Mark an open position as closed."""
    conn.execute(
        """UPDATE open_positions
        SET status='closed', close_date=?, close_price=?, close_reason=?
        WHERE pos_id=?""",
        (close_date, close_price, reason, pos_id),
    )
    conn.commit()


def insert_trade_event(
    conn: sqlite3.Connection,
    plan_date: str,
    code: str,
    event_type: str,
    price: float,
    size_pct: Optional[float] = None,
    pnl_pct: Optional[float] = None,
    note: Optional[str] = None,
    shares: Optional[int] = None,
    pnl_amt: Optional[float] = None,
) -> int:
    """Record a trade event (open/close). Returns event_id."""
    cur = conn.execute(
        """INSERT INTO trade_events
        (plan_date, code, event_type, price, size_pct, pnl_pct, note, shares, pnl_amt, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (plan_date, code, event_type, price, size_pct, pnl_pct, note, shares, pnl_amt,
         dt.datetime.utcnow().isoformat(timespec="seconds")),
    )
    conn.commit()
    return cur.lastrowid


def get_last_refresh(conn: sqlite3.Connection) -> Optional[Dict]:
    """Return the most recently updated daily_picks row: {date, updated_at}. None if empty.

    Rows with empty updated_at (legacy or not-yet-populated) are ignored so the
    dashboard's freshness logic only sees real timestamps.
    """
    cur = conn.execute(
        "SELECT date, updated_at FROM daily_picks "
        "WHERE updated_at != '' "
        "ORDER BY updated_at DESC, date DESC LIMIT 1"
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {"date": row[0], "updated_at": row[1]}


def get_today_plan_summary(conn: sqlite3.Connection, today: str) -> Dict:
    """Counts by action + total buy size + failed count for a given plan_date."""
    cur = conn.execute(
        """SELECT action, status, COUNT(*), COALESCE(SUM(size_pct), 0.0)
           FROM trade_plan WHERE plan_date = ? GROUP BY action, status""",
        (today,),
    )
    buy = hold = exit_ = failed = 0
    size_total = 0.0
    for action, status, count, sum_size in cur.fetchall():
        if action == "buy" and status == "ok":
            buy += count
            size_total += sum_size
        elif action == "buy" and status == "failed":
            failed += count  # buy + failed
        elif action == "hold" and status == "ok":
            hold += count
        elif action == "exit" and status == "ok":
            exit_ += count
        elif status == "failed":  # hold/exit + failed
            failed += count
    return {
        "date": today,
        "buy": buy, "hold": hold, "exit": exit_,
        "size_total": round(size_total, 4),
        "failed": failed,
    }


def get_open_positions_with_unrealized(conn: sqlite3.Connection) -> Dict:
    cur = conn.execute(
        """SELECT op.code, op.entry_date, op.entry_price, op.size_pct,
                  op.stop_price, op.tp_price, op.shares,
                  dp.close AS close_price
           FROM open_positions op
           LEFT JOIN (
               SELECT code, close FROM daily_prices dp1
               WHERE trade_date = (SELECT MAX(trade_date) FROM daily_prices dp2
                                   WHERE dp2.code = dp1.code)
           ) dp ON dp.code = op.code
           WHERE op.status = 'open'
           ORDER BY op.entry_date, op.code""",
    )
    items = []
    shares_total = 0
    floating_total = 0.0
    pct_sum = 0.0
    pct_count = 0
    for code, ed, ep, sz, sp, tp, shares, close in cur.fetchall():
        shares = shares or 0
        floating = None
        if close is not None and ep:
            floating = round((close - ep) * shares, 2)
            floating_total += floating
            unrealized_pct = (close - ep) / ep * 100
            pct_sum += unrealized_pct
            pct_count += 1
        shares_total += shares
        items.append({
            "code": code, "entry_date": ed, "entry_price": ep,
            "size_pct": sz, "stop_price": sp, "tp_price": tp,
            "shares": shares, "current_price": close,
            "floating_pnl": floating,
            "stop_pnl": round((sp - ep) * shares, 2) if (sp is not None and ep) else None,
            "tp_pnl": round((tp - ep) * shares, 2) if (tp is not None and ep) else None,
        })
    avg = round(pct_sum / pct_count, 2) if pct_count else None
    return {
        "count": len(items), "size_total": round(sum(i["size_pct"] for i in items), 4),
        "shares_total": shares_total, "floating_pnl": round(floating_total, 2),
        "avg_unrealized_pct": avg, "items": items[:3],
    }


def get_recent_pnl(conn: sqlite3.Connection, days: int = 5) -> List[Dict]:
    """Last `days` distinct trade dates' portfolio return in yuan, newest first.

    Per day: realized (closed that day) + unrealized (open positions marked
    to that day's close, skipping dates before entry). Return [] with no prices.
    """
    dates = [
        r[0] for r in conn.execute(
            "SELECT DISTINCT trade_date FROM daily_prices "
            "ORDER BY trade_date DESC LIMIT ?", (days,),
        ).fetchall()
    ]
    if not dates:
        return []
    closed = conn.execute(
        "SELECT close_date, entry_price, close_price, shares "
        "FROM open_positions WHERE status='closed' AND close_date IS NOT NULL",
    ).fetchall()
    opens = conn.execute(
        "SELECT code, entry_date, entry_price, shares "
        "FROM open_positions WHERE status='open'",
    ).fetchall()
    open_codes = [r[0] for r in opens]
    prices: Dict[Tuple[str, str], float] = {}
    if open_codes:
        ph = ",".join("?" for _ in open_codes)
        prices = {
            (code, tdate): close
            for code, tdate, close in conn.execute(
                f"SELECT code, trade_date, close FROM daily_prices "
                f"WHERE code IN ({ph}) AND trade_date BETWEEN ? AND ?",
                (*open_codes, dates[-1], dates[0]),
            )
        }
    out: List[Dict] = []
    for d in dates:
        amt = 0.0
        contributed = False
        for close_date, entry, close, shares in closed:
            if close_date == d and entry:
                amt += (close - entry) * (shares or 0)
                contributed = True
        for code, entry_date, entry, shares in opens:
            if entry_date > d or not entry:
                continue
            close = prices.get((code, d))
            if close is not None:
                amt += (close - entry) * (shares or 0)
                contributed = True
        if contributed:
            out.append({"date": d, "pnl_amt": round(amt, 2)})
    return out


def get_holdings_detail(conn: sqlite3.Connection) -> Dict:
    """Full per-position tracking + portfolio summary (yuan)."""
    opens = conn.execute(
        """SELECT op.code, m.name, op.entry_price, op.shares, op.stop_price, op.tp_price,
                  dp.close AS current_price
           FROM open_positions op
           LEFT JOIN hs300_metadata m ON m.code = op.code
           LEFT JOIN (
               SELECT code, close FROM daily_prices dp1
               WHERE trade_date = (SELECT MAX(trade_date) FROM daily_prices dp2
                                   WHERE dp2.code = dp1.code)
           ) dp ON dp.code = op.code
           WHERE op.status = 'open'
           ORDER BY op.entry_date, op.code""",
    ).fetchall()
    holdings = []
    floating_total = 0.0
    shares_total = 0
    cost_open = 0.0
    for code, name, entry, shares, stop, tp, cur in opens:
        entry = entry or 0.0
        shares = shares or 0
        cost_open += entry * shares
        shares_total += shares
        floating = round((cur - entry) * shares, 2) if cur is not None else None
        if floating is not None:
            floating_total += floating
        holdings.append({
            "code": code, "name": name, "shares": shares,
            "entry_price": entry, "current_price": cur,
            "stop_price": stop, "tp_price": tp,
            "floating_pnl": floating,
            "stop_pnl": round((stop - entry) * shares, 2) if stop is not None else None,
            "tp_pnl": round((tp - entry) * shares, 2) if tp is not None else None,
        })
    realized = conn.execute(
        "SELECT COALESCE(SUM((close_price - entry_price) * shares), 0) "
        "FROM open_positions WHERE status='closed'",
    ).fetchone()[0]
    closed_cost = conn.execute(
        "SELECT COALESCE(SUM(entry_price * shares), 0) "
        "FROM open_positions WHERE status='closed'",
    ).fetchone()[0]
    total_cost = cost_open + closed_cost
    total_pnl = round(floating_total + realized, 2)
    return_pct = round(total_pnl / total_cost * 100, 2) if total_cost else 0.0
    return {
        "holdings": holdings,
        "summary": {
            "open_count": len(holdings),
            "shares_total": shares_total,
            "floating_pnl": round(floating_total, 2),
            "realized_pnl": round(realized, 2),
            "total_cost": round(total_cost, 2),
            "total_pnl": total_pnl,
            "return_pct": return_pct,
        },
    }
