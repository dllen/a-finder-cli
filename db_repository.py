import datetime as dt
import sqlite3
from dataclasses import dataclass
from typing import Dict, Iterable, List, Optional, Tuple

from db_schema import ensure_schema


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
