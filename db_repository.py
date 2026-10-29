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


@dataclass
class FundamentalsRow:
    code: str
    pe: float = 0.0
    pb: float = 0.0
    roe: float = 0.0
    debt_ratio: float = 0.0
    gross_margin: float = 0.0
    gross_margin_std: float = 0.0
    revenue_growth: float = 0.0
    profit_growth: float = 0.0
    revenue_cagr_3y: float = 0.0
    profit_cagr_3y: float = 0.0
    cashflow: float = 0.0
    dividend_yield: float = 0.0
    dividend_stability: float = 0.0
    sector: str = ""


@dataclass
class FundamentalsHistoryRow:
    code: str
    year: int
    gross_margin: float = 0.0
    roe_excl: float = 0.0
    revenue: Optional[float] = None
    net_profit_excl: Optional[float] = None
    report_date: str = ""
    synced_at: str = ""


def upsert_fundamentals(conn: sqlite3.Connection, rows: Iterable[FundamentalsRow]) -> int:
    now = dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    data = [
        (
            r.code, r.pe, r.pb, r.roe, r.debt_ratio, r.gross_margin, r.gross_margin_std,
            r.revenue_growth, r.profit_growth, r.revenue_cagr_3y, r.profit_cagr_3y,
            r.cashflow, r.dividend_yield, r.dividend_stability, r.sector, now,
        )
        for r in rows
    ]
    if not data:
        return 0
    conn.executemany(
        """
        INSERT INTO fundamentals (code, pe, pb, roe, debt_ratio, gross_margin, gross_margin_std,
            revenue_growth, profit_growth, revenue_cagr_3y, profit_cagr_3y,
            cashflow, dividend_yield, dividend_stability, sector, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(code) DO UPDATE SET
            pe=excluded.pe, pb=excluded.pb, roe=excluded.roe, debt_ratio=excluded.debt_ratio,
            gross_margin=excluded.gross_margin, gross_margin_std=excluded.gross_margin_std,
            revenue_growth=excluded.revenue_growth, profit_growth=excluded.profit_growth,
            revenue_cagr_3y=excluded.revenue_cagr_3y, profit_cagr_3y=excluded.profit_cagr_3y,
            cashflow=excluded.cashflow, dividend_yield=excluded.dividend_yield,
            dividend_stability=excluded.dividend_stability, sector=excluded.sector,
            updated_at=excluded.updated_at
        """,
        data,
    )
    return len(data)


def upsert_fundamentals_history(
    conn: sqlite3.Connection,
    rows: Iterable[FundamentalsHistoryRow],
) -> int:
    data = [
        (r.code, r.year, r.gross_margin, r.roe_excl,
         r.revenue, r.net_profit_excl, r.report_date, r.synced_at)
        for r in rows
    ]
    if not data:
        return 0
    conn.executemany(
        """
        INSERT INTO fundamentals_history
            (code, year, gross_margin, roe_excl, revenue, net_profit_excl, report_date, synced_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(code, year) DO UPDATE SET
            gross_margin=excluded.gross_margin,
            roe_excl=excluded.roe_excl,
            revenue=excluded.revenue,
            net_profit_excl=excluded.net_profit_excl,
            report_date=excluded.report_date,
            synced_at=excluded.synced_at
        """,
        data,
    )
    return len(data)


def get_fundamentals_history_by_code(
    conn: sqlite3.Connection, code: str
) -> List[FundamentalsHistoryRow]:
    cur = conn.execute(
        "SELECT code, year, gross_margin, roe_excl, revenue, net_profit_excl, "
        "report_date, synced_at FROM fundamentals_history WHERE code = ? ORDER BY year DESC",
        (code,),
    )
    return [FundamentalsHistoryRow(*r) for r in cur.fetchall()]


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


_FUNDAMENTALS_COLS = (
    "code, pe, pb, roe, debt_ratio, gross_margin, gross_margin_std, revenue_growth,"
    " profit_growth, revenue_cagr_3y, profit_cagr_3y, cashflow, dividend_yield,"
    " dividend_stability, sector"
)


def get_fundamentals(conn: sqlite3.Connection) -> Dict[str, FundamentalsRow]:
    result: Dict[str, FundamentalsRow] = {}
    for row in conn.execute(f"SELECT {_FUNDAMENTALS_COLS} FROM fundamentals").fetchall():
        result[row[0]] = FundamentalsRow(*row)
    return result


def fundamentals_stale(conn: sqlite3.Connection, max_age_days: int = 7) -> bool:
    row = conn.execute("SELECT COUNT(*), MAX(updated_at) FROM fundamentals").fetchone()
    if not row or not row[0] or not row[1]:
        return True
    try:
        updated = dt.datetime.strptime(row[1], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return True
    return dt.datetime.now() - updated > dt.timedelta(days=max_age_days)


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


def get_market_trade_dates(
    conn: sqlite3.Connection, start_date: str, end_date: str
) -> List[str]:
    """区间内「全市场出现过行情」的交易日（市场日历）。

    用于增量同步的缺口判定：只有全市场真的有行情的日期才算「应有数据」，
    这样法定假日、未开盘日不会被当成缺口而反复回补。
    """
    cur = conn.execute(
        """
        SELECT DISTINCT trade_date FROM daily_prices
        WHERE trade_date BETWEEN ? AND ?
        ORDER BY trade_date
        """,
        (start_date, end_date),
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
    portfolio: str = "default",
) -> int:
    """Insert a trade_plan row. Upsert via UNIQUE(plan_date, portfolio, code, action).

    Returns plan_id (>0) on insert or update; rebuild at a different capital
    overwrites all fields (except created_at), including shares.
    """
    cur = conn.execute(
        """INSERT INTO trade_plan
        (plan_date, portfolio, code, action, plan_price, size_pct, stop_price, tp_price,
         rr_ratio, status, reason, rationale_json, params_hash, created_at, shares)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(plan_date, portfolio, code, action, params_hash) DO UPDATE SET
         plan_price=excluded.plan_price,
         size_pct=excluded.size_pct,
         stop_price=excluded.stop_price,
         tp_price=excluded.tp_price,
         rr_ratio=excluded.rr_ratio,
         status=excluded.status,
         reason=excluded.reason,
         rationale_json=excluded.rationale_json,
         params_hash=excluded.params_hash,
         shares=excluded.shares""",
        (
            plan_date, portfolio, row.code, row.action, row.plan_price, row.size_pct,
            row.stop_price, row.tp_price, row.rr_ratio, row.status, row.reason,
            json.dumps(row.rationale), params_hash,
            dt.datetime.utcnow().isoformat(timespec="seconds"),
            row.shares,
        ),
    )
    conn.commit()
    return cur.lastrowid if cur.rowcount > 0 else 0


def canonical_plan_portfolio(conn: sqlite3.Connection, plan_date: str) -> Optional[str]:
    """该日期用于**展示/汇总**的规范档位。无行时返回 None。

    为什么需要：`trade_plan` 是**多档位 × 多轮次**的宽表 ——
      - 10 个资金档位（5W…50W）+ 1 个 `default`
      - 每个档位下还有多个 `params_hash` 轮次（`plan build-all` 会跑「无策略 +
        每个策略各一遍」，回补时逐轮追加）
    所以一个交易日的行数是 ~5000，而**用户要看的是一套计划**（`MAX_POSITIONS=8`）。
    曾经 `get_trade_plan_by_date` 不过滤也不去重，把这 5000 行直接喂给前端：
    计划页把每只票重复渲染几十遍，dashboard 的 `size_total` 汇总成 46.79（即 4679%，
    而 `MAX_TOTAL` 是 0.75）。这是 2026-10-29 查出的用户可见数据错误。

    为什么优先 `default`：只有 `default` 是 `plan build` 的产出，遵守
    `MAX_POSITIONS`/`MAX_TOTAL`（实测 8 行、仓位合计 ≤75%）。而 10 个档位是
    `plan build-all` 的策略研究产出（`ignore_capacity=True`），一轮就有 16 行、
    仓位合计 178% —— 那是「各策略各自的候选」，**不是**一套可执行计划。
    没有 default 的早期日期退到行数最多的档位（只作展示，不宣称是完整计划）。
    """
    row = conn.execute(
        "SELECT portfolio FROM trade_plan WHERE plan_date = ? "
        "GROUP BY portfolio "
        "ORDER BY (portfolio = 'default') DESC, COUNT(*) DESC, portfolio "
        "LIMIT 1",
        (plan_date,),
    ).fetchone()
    return row[0] if row else None


def canonical_plan_round(
    conn: sqlite3.Connection,
    plan_date: str,
    portfolio: str,
) -> Optional[str]:
    """该 (日期, 档位) 下用于展示的**单一轮次**（`params_hash`）。

    一套计划 = 一次 `build_plan` 的产出 = 一个 `params_hash`。跨轮次按 code 取
    「最新一条」会拼出**不存在的计划**：A 轮选 {1,2,3}、B 轮选 {4,5,6}，并集是
    {1..6}，仓位合计变成两轮之和（实测 2026-08-20 因此显示 380%）。
    所以必须整轮取，不能按行挑。

    选轮规则：行数最多的那一轮（该轮覆盖的持仓最全）；并列时取最新写入的
    （`MAX(plan_id)` 最大），保证同一份数据每次渲染稳定、可复现。
    """
    row = conn.execute(
        "SELECT params_hash FROM trade_plan "
        "WHERE plan_date = ? AND portfolio = ? "
        "GROUP BY params_hash "
        "ORDER BY COUNT(*) DESC, MAX(plan_id) DESC "
        "LIMIT 1",
        (plan_date, portfolio),
    ).fetchone()
    return row[0] if row else None


def get_trade_plan_by_date(
    conn: sqlite3.Connection,
    plan_date: str,
    include_failed: bool = False,
    portfolio: Optional[str] = None,
) -> List[Dict]:
    """Return the canonical trade_plan rows for a date, with stock name.

    默认只返回**一个规范档位**的**单一轮次**（见 `canonical_plan_portfolio` /
    `canonical_plan_round`）。原因：表里同时存着 10 个档位与多轮策略，
    不去重的话一个交易日 ~5000 行全是重复，前端会重复渲染、汇总会算成 4679%。

    需要看别的档位时显式传 `portfolio=`。数据结构没变，只是「默认视图」
    从「全表」改成「一套计划」。

    Stock name comes from `hs300_metadata` (the table that actually stores names).
    `hs300_constituents.name` is intentionally empty in this codebase — see
    data_providers.fetch_hs300_constituents_from_api for why.
    """
    if portfolio is None:
        portfolio = canonical_plan_portfolio(conn, plan_date)

    sql = ("SELECT tp.*, m.name AS name "
           "FROM trade_plan tp LEFT JOIN hs300_metadata m ON m.code = tp.code "
           "WHERE tp.plan_date = ?")
    params: List[object] = [plan_date]
    if portfolio is not None:
        round_hash = canonical_plan_round(conn, plan_date, portfolio)
        sql += " AND tp.portfolio = ? AND tp.params_hash = ?"
        params.extend([portfolio, round_hash])
    if not include_failed:
        sql += " AND tp.status = 'ok'"
    sql += " ORDER BY tp.action DESC, tp.code"  # buy first, then hold/exit
    cur = conn.execute(sql, tuple(params))
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


def get_trade_plan_by_date_and_portfolio(
    conn: sqlite3.Connection,
    plan_date: str,
    portfolio: str,
    params_hash: str,
) -> List[Dict]:
    cur = conn.execute(
        "SELECT * FROM trade_plan WHERE plan_date=? AND portfolio=? AND params_hash=?",
        (plan_date, portfolio, params_hash),
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
    portfolio: str = "default",
) -> int:
    """Open a new paper position with a fixed share count. Returns pos_id."""
    cur = conn.execute(
        """INSERT INTO open_positions
        (code, portfolio, entry_date, entry_price, size_pct, stop_price, tp_price,
         status, shares)
        VALUES (?,?,?,?,?,?,?,'open',?)""",
        (code, portfolio, entry_date, entry_price, size_pct, stop_price, tp_price,
         shares),
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
    portfolio: str = "default",
) -> int:
    """Add shares to an existing open position (weighted-average entry).

    If no open position exists for `code`, opens a fresh one. Returns pos_id.
    """
    row = conn.execute(
        "SELECT pos_id, shares, entry_price FROM open_positions "
        "WHERE code=? AND status='open' AND portfolio=? LIMIT 1",
        (code, portfolio),
    ).fetchone()
    if row is None:
        return insert_open_position(
            conn, code, entry_date, fill_price, size_pct, stop_price, tp_price,
            shares_to_add, portfolio,
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
    portfolio: str = "default",
) -> int:
    """Record a trade event (open/close). Returns event_id."""
    cur = conn.execute(
        """INSERT INTO trade_events
        (plan_date, code, event_type, price, size_pct, pnl_pct, note, shares, pnl_amt,
         portfolio, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
        (plan_date, code, event_type, price, size_pct, pnl_pct, note, shares, pnl_amt,
         portfolio, dt.datetime.utcnow().isoformat(timespec="seconds")),
    )
    conn.commit()
    return cur.lastrowid


def get_last_refresh(conn: sqlite3.Connection) -> Optional[Dict]:
    """Return the latest picks date and when it was computed: {date, updated_at}.

    Picks the row for the NEWEST trade date, and reports that row's own
    updated_at. Ordering by updated_at instead would report whichever date
    happened to be written last — after a batch backfill (which writes dates
    in descending order) that is the OLDEST date, making the dashboard show a
    stale date next to a "fresh" badge.

    Rows with empty updated_at (legacy or not-yet-populated) are ignored so the
    dashboard's freshness logic only sees real timestamps.
    """
    cur = conn.execute(
        "SELECT date, updated_at FROM daily_picks "
        "WHERE updated_at != '' "
        "ORDER BY date DESC, updated_at DESC LIMIT 1"
    )
    row = cur.fetchone()
    if row is None:
        return None
    return {"date": row[0], "updated_at": row[1]}


def get_today_plan_summary(
    conn: sqlite3.Connection,
    today: str,
    portfolio: Optional[str] = None,
) -> Dict:
    """Counts by action + total buy size + failed count for a given plan_date.

    基于 `get_trade_plan_by_date` 的**规范行集**聚合，不是直接 `GROUP BY action`
    扫全表。为什么必须这样：`trade_plan` 同时存 10 个档位 × 多轮策略，
    旧实现对一个交易日 ~5000 行直接 SUM(size_pct)，于是 dashboard 显示
    `size_total = 46.79`（4679%，而 `MAX_TOTAL` 只有 0.75）、`buy = 394`
    （而 `MAX_POSITIONS` 是 8）。数字看着像数据，其实是重复行叠加，
    比缺失更难被发现（2026-10-29 查出）。

    复用同一个取数函数也保证了「计划页看到的行」与「dashboard 汇总的数字」
    永远来自同一集合 —— 两处各自拼 SQL 迟早会漂移。
    """
    rows = get_trade_plan_by_date(conn, today, include_failed=True, portfolio=portfolio)
    buy = hold = exit_ = failed = 0
    size_total = 0.0
    for r in rows:
        action = r.get("action")
        status = r.get("status")
        if status == "failed":
            failed += 1  # buy/hold/exit 失败都计入失败数
        elif action == "buy":
            buy += 1
            size_total += float(r.get("size_pct") or 0.0)
        elif action == "hold":
            hold += 1
        elif action == "exit":
            exit_ += 1
    return {
        "date": today,
        "buy": buy, "hold": hold, "exit": exit_,
        "size_total": round(size_total, 4),
        "failed": failed,
    }


def get_open_positions_with_unrealized(
    conn: sqlite3.Connection,
    *,
    portfolio: Optional[str] = None,
    as_of: Optional[str] = None,
) -> Dict:
    """持仓 + 浮动盈亏。

    as_of 为 None：当前状态（open_positions + 最新收盘）。
    as_of=YYYY-MM-DD：该日收盘的持仓（按 trade_events 重建 + 该日或更早最近收盘价）。
    """
    if as_of and portfolio is not None:
        return _positions_with_unrealized_as_of(conn, portfolio, as_of)
    sql = (
        """SELECT op.code, op.entry_date, op.entry_price, op.size_pct,
                  op.stop_price, op.tp_price, op.shares,
                  dp.close AS close_price,
                  m.name AS name
           FROM open_positions op
           LEFT JOIN (
               SELECT code, close FROM daily_prices dp1
               WHERE trade_date = (SELECT MAX(trade_date) FROM daily_prices dp2
                                   WHERE dp2.code = dp1.code)
           ) dp ON dp.code = op.code
           LEFT JOIN hs300_metadata m ON m.code = op.code
           WHERE op.status = 'open'"""
    )
    params: tuple = ()
    if portfolio is not None:
        sql += " AND op.portfolio = ?"
        params = (portfolio,)
    sql += " ORDER BY op.entry_date, op.code"
    cur = conn.execute(sql, params)
    items = []
    shares_total = 0
    floating_total = 0.0
    pct_sum = 0.0
    pct_count = 0
    for code, ed, ep, sz, sp, tp, shares, close, name in cur.fetchall():
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
            "code": code, "name": name, "entry_date": ed, "entry_price": ep,
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


def _last_close_on_or_before(
    conn: sqlite3.Connection, code: str, as_of: str
) -> Optional[float]:
    """as_of 当日或之前最近一个交易日的收盘价（停牌/未同步时回退到更早）。"""
    row = conn.execute(
        """SELECT close FROM daily_prices
           WHERE code=? AND trade_date<=?
           ORDER BY trade_date DESC LIMIT 1""",
        (code, as_of),
    ).fetchone()
    return float(row[0]) if row and row[0] is not None else None


def rebuild_positions_as_of(
    conn: sqlite3.Connection,
    portfolio: str,
    as_of: str,
) -> Dict[str, Dict]:
    """按 trade_events 重建 as_of（含当日）收盘后的持仓。

    用移动平均成本：open 累加 shares/cost；close 按当前均价扣减。
    同一 code 清仓后再建仓也能算对（成本不跨轮次混算）。
    返回 {code: {"shares", "cost", "avg_entry", "entry_date"}}，只含 shares>0。
    """
    rows = conn.execute(
        """SELECT plan_date, event_id, code, event_type, price, shares
           FROM trade_events
           WHERE portfolio=? AND plan_date<=? AND event_type IN ('open','close')
           ORDER BY plan_date, event_id""",
        (portfolio, as_of),
    ).fetchall()

    state: Dict[str, Dict] = {}
    for plan_date, _eid, code, etype, price, shares in rows:
        n = int(shares or 0)
        if n <= 0 or price is None:
            continue
        st = state.setdefault(
            code, {"shares": 0, "cost": 0.0, "entry_date": plan_date}
        )
        if etype == "open":
            if st["shares"] == 0:
                st["entry_date"] = plan_date
            st["shares"] += n
            st["cost"] += float(price) * n
        else:  # close
            if st["shares"] <= 0:
                continue
            take = min(n, st["shares"])
            avg = st["cost"] / st["shares"] if st["shares"] else 0.0
            st["cost"] -= avg * take
            st["shares"] -= take

    out: Dict[str, Dict] = {}
    for code, st in state.items():
        if st["shares"] <= 0:
            continue
        avg_entry = st["cost"] / st["shares"] if st["shares"] else 0.0
        out[code] = {
            "shares": st["shares"],
            "cost": round(st["cost"], 4),
            "avg_entry": round(avg_entry, 4),
            "entry_date": st["entry_date"],
        }
    return out


def _positions_meta(conn: sqlite3.Connection, portfolio: str, code: str) -> Dict:
    """取该 code 在 portfolio 里的 name / stop / tp / size_pct（用于展示）。"""
    row = conn.execute(
        """SELECT m.name, op.stop_price, op.tp_price, op.size_pct
           FROM (SELECT 1) dummy
           LEFT JOIN open_positions op
                  ON op.code=? AND op.portfolio=?
           LEFT JOIN hs300_metadata m ON m.code=?
           ORDER BY op.entry_date DESC, op.pos_id DESC
           LIMIT 1""",
        (code, portfolio, code),
    ).fetchone()
    if not row:
        return {"name": None, "stop_price": None, "tp_price": None, "size_pct": None}
    name, stop, tp, size = row
    return {"name": name, "stop_price": stop, "tp_price": tp, "size_pct": size}


def _compute_portfolio_pnl_as_of(
    conn: sqlite3.Connection,
    portfolio: str,
    initial_capital: int,
    as_of: str,
) -> Dict:
    """as_of 当日收盘的组合状态：现金、持仓市值、已实现/未实现盈亏。"""
    agg = conn.execute(
        """SELECT
             COALESCE(SUM(CASE WHEN event_type='open'  THEN price*shares ELSE 0 END), 0.0),
             COALESCE(SUM(CASE WHEN event_type='close' THEN price*shares ELSE 0 END), 0.0),
             COALESCE(SUM(CASE WHEN event_type='close' THEN pnl_amt     ELSE 0 END), 0.0)
           FROM trade_events
           WHERE portfolio=? AND plan_date<=? AND event_type IN ('open','close')""",
        (portfolio, as_of),
    ).fetchone()
    buy_cost, close_proceeds, realized_pnl = (float(x or 0.0) for x in agg)
    cash_remaining = round(initial_capital - buy_cost + close_proceeds, 2)

    positions = rebuild_positions_as_of(conn, portfolio, as_of)
    position_value = 0.0
    cost_basis = 0.0
    for code, st in positions.items():
        px = _last_close_on_or_before(conn, code, as_of)
        cost_basis += st["cost"]
        if px is not None:
            position_value += px * st["shares"]
    position_value = round(position_value, 2)
    unrealized_pnl = round(position_value - cost_basis, 2)
    total_value = round(cash_remaining + position_value, 2)
    return {
        "portfolio": portfolio,
        "initial_capital": initial_capital,
        "as_of": as_of,
        "cash_remaining": cash_remaining,
        "position_value": position_value,
        "total_value": total_value,
        "realized_pnl": round(realized_pnl, 2),
        "unrealized_pnl": unrealized_pnl,
        "return_rate": round(total_value / initial_capital - 1, 6) if initial_capital else 0.0,
        "positions_count": len(positions),
        "shares_total": sum(st["shares"] for st in positions.values()),
    }


def _positions_with_unrealized_as_of(
    conn: sqlite3.Connection,
    portfolio: str,
    as_of: str,
) -> Dict:
    """as_of 当日收盘的持仓明细（供 /api/portfolio/<label> 的「当前持仓」表）。"""
    positions = rebuild_positions_as_of(conn, portfolio, as_of)
    items = []
    shares_total = 0
    floating_total = 0.0
    pct_sum = 0.0
    pct_count = 0
    for code, st in sorted(positions.items(), key=lambda kv: (kv[1]["entry_date"], kv[0])):
        meta = _positions_meta(conn, portfolio, code)
        px = _last_close_on_or_before(conn, code, as_of)
        shares = st["shares"]
        entry = st["avg_entry"]
        floating = None
        if px is not None and entry:
            floating = round((px - entry) * shares, 2)
            floating_total += floating
            pct_sum += (px - entry) / entry * 100
            pct_count += 1
        shares_total += shares
        stop, tp = meta["stop_price"], meta["tp_price"]
        items.append({
            "code": code,
            "name": meta["name"],
            "entry_date": st["entry_date"],
            "entry_price": entry,
            "size_pct": meta["size_pct"],
            "stop_price": stop,
            "tp_price": tp,
            "shares": shares,
            "current_price": px,
            "floating_pnl": floating,
            "stop_pnl": round((stop - entry) * shares, 2) if (stop is not None and entry) else None,
            "tp_pnl": round((tp - entry) * shares, 2) if (tp is not None and entry) else None,
        })
    avg = round(pct_sum / pct_count, 2) if pct_count else None
    return {
        "count": len(items),
        "size_total": round(sum(i["size_pct"] for i in items if i["size_pct"]), 4),
        "shares_total": shares_total,
        "floating_pnl": round(floating_total, 2),
        "avg_unrealized_pct": avg,
        "items": items,
        "as_of": as_of,
    }


def compute_portfolio_pnl(
    conn: sqlite3.Connection,
    portfolio: str,
    *,
    initial_capital: int,
    as_of: Optional[str] = None,
) -> Dict:
    """Cash carry + position mark-to-market + realized/unrealized P&L + return rate.

    as_of 为 None 时按当前状态计算（open_positions + 最新收盘，历史行为）；
    传 YYYY-MM-DD 时按 as_of 当日收盘重建（trade_events + 该日收盘价）。
    """
    if as_of:
        return _compute_portfolio_pnl_as_of(conn, portfolio, initial_capital, as_of)
    # 1. realized_pnl: Σ trade_events.pnl_amt WHERE event_type='close' AND portfolio=?
    realized_pnl_row = conn.execute(
        "SELECT COALESCE(SUM(pnl_amt), 0.0) FROM trade_events "
        "WHERE event_type='close' AND portfolio=?",
        (portfolio,),
    ).fetchone()
    realized_pnl = float(realized_pnl_row[0] or 0.0)

    # 2. cash_remaining: initial − Σbuy_cost + Σclose_proceeds
    buy_row = conn.execute(
        "SELECT COALESCE(SUM(price * shares), 0.0) FROM trade_events "
        "WHERE event_type='open' AND portfolio=?",
        (portfolio,),
    ).fetchone()
    close_row = conn.execute(
        "SELECT COALESCE(SUM(price * shares), 0.0) FROM trade_events "
        "WHERE event_type='close' AND portfolio=?",
        (portfolio,),
    ).fetchone()
    buy_cost = float(buy_row[0] or 0.0)
    close_proceeds = float(close_row[0] or 0.0)
    cash_remaining = round(initial_capital - buy_cost + close_proceeds, 2)

    # 3. position_value + unrealized_pnl: open positions + latest close
    pos_rows = conn.execute(
        """SELECT op.shares, op.entry_price,
                  (SELECT close FROM daily_prices dp
                   WHERE dp.code = op.code
                   ORDER BY trade_date DESC LIMIT 1) AS cur
           FROM open_positions op WHERE op.status='open' AND op.portfolio=?""",
        (portfolio,),
    ).fetchall()
    position_value = 0.0
    cost_basis = 0.0
    for shares, entry, cur in pos_rows:
        if not shares or not entry:
            continue
        if cur is not None:
            position_value += cur * shares
        cost_basis += entry * shares
    position_value = round(position_value, 2)
    unrealized_pnl = round(position_value - cost_basis, 2)
    total_value = round(cash_remaining + position_value, 2)
    return_rate = round(total_value / initial_capital - 1, 6) if initial_capital else 0.0
    return {
        "portfolio": portfolio,
        "initial_capital": initial_capital,
        "cash_remaining": cash_remaining,
        "position_value": position_value,
        "total_value": total_value,
        "realized_pnl": round(realized_pnl, 2),
        "unrealized_pnl": unrealized_pnl,
        "return_rate": return_rate,
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


# ---- 策略自我进化：pick_outcomes / strategy_config ----

def upsert_pick_outcomes(conn: sqlite3.Connection, rows: List[Dict]) -> int:
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT OR REPLACE INTO pick_outcomes
        (date, source, code, strategy, name, kind, score, buy, stop, target,
         exit_date, exit_price, outcome_pct, win, labeled_at)
        VALUES (:date, :source, :code, :strategy, :name, :kind, :score, :buy, :stop, :target,
                :exit_date, :exit_price, :outcome_pct, :win, :labeled_at)
        """,
        rows,
    )
    return len(rows)


def fetch_pick_outcomes(conn: sqlite3.Connection, source: Optional[str] = None,
                        judged_only: bool = True) -> List[Dict]:
    sql = "SELECT * FROM pick_outcomes"
    conds, args = [], []
    if source:
        conds.append("source = ?")
        args.append(source)
    if judged_only:
        conds.append("win IS NOT NULL")
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY date, strategy, code"
    cur = conn.execute(sql, args)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def outcomes_watermark(conn: sqlite3.Connection, source: str) -> str:
    row = conn.execute(
        "SELECT MAX(date) FROM pick_outcomes WHERE source = ? AND win IS NOT NULL", (source,)
    ).fetchone()
    return row[0] or ""


def load_champion_config(conn: sqlite3.Connection) -> Optional[Dict]:
    row = conn.execute(
        "SELECT version, created_at, active_json, ratios_json, metrics_json "
        "FROM strategy_config WHERE status = 'champion' ORDER BY version DESC LIMIT 1"
    ).fetchone()
    if not row:
        return None
    return {
        "version": row[0],
        "created_at": row[1],
        "active": json.loads(row[2]),
        "ratios": json.loads(row[3]),
        "metrics": json.loads(row[4]) if row[4] else None,
    }


def insert_strategy_config(conn: sqlite3.Connection, active: List[str],
                           ratios: Dict[str, float], status: str,
                           metrics: Optional[Dict] = None, reason: str = "") -> int:
    now = dt.datetime.now().isoformat(timespec="seconds")
    cur = conn.execute(
        "INSERT INTO strategy_config (created_at, active_json, ratios_json, metrics_json, status, reason) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (now, json.dumps(active, ensure_ascii=False), json.dumps(ratios, ensure_ascii=False),
         json.dumps(metrics, ensure_ascii=False) if metrics else None, status, reason),
    )
    return int(cur.lastrowid)


def mark_config_status(conn: sqlite3.Connection, version: int, status: str, reason: str = "") -> None:
    conn.execute(
        "UPDATE strategy_config SET status = ?, reason = reason || ? WHERE version = ?",
        (status, " | " + reason if reason else "", version),
    )
