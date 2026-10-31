import sqlite3


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS hs300_constituents (
            code TEXT PRIMARY KEY,
            name TEXT,
            exchange TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_prices (
            code TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            open REAL,
            close REAL,
            high REAL,
            low REAL,
            volume REAL,
            amount REAL,
            amplitude REAL,
            pct_change REAL,
            change REAL,
            turnover REAL,
            -- adj_basis: 复权口径（none 不复权 / qfq 前复权）。
            -- 只对**全新库**有意义；已部署库走 db/migrations/2026_10_30_daily_prices_adj_basis.sql。
            -- 为什么要有：备源链里腾讯是前复权、东财/新浪是不复权，混写会在除权日
            -- 留下假跳空（2026-10-29/30 事故，见 docs/reports/ci-cache-fix-plan.md F2/A5）。
            adj_basis TEXT,
            PRIMARY KEY (code, trade_date)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS sync_checkpoints (
            job_id TEXT NOT NULL,
            code TEXT NOT NULL,
            start_date TEXT NOT NULL,
            end_date TEXT NOT NULL,
            status TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            error TEXT,
            PRIMARY KEY (job_id, code)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS hs300_metadata (
            code TEXT PRIMARY KEY,
            name TEXT,
            industry TEXT,
            region TEXT,
            updated_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fetch_offsets (
            code TEXT PRIMARY KEY,
            last_trade_date TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_picks (
            date TEXT NOT NULL,
            rank INTEGER NOT NULL,
            kind TEXT NOT NULL,
            code TEXT NOT NULL,
            name TEXT,
            strategy TEXT,
            buy REAL,
            stop REAL,
            target REAL,
            score REAL,
            PRIMARY KEY (date, rank, kind)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS fundamentals (
            code TEXT PRIMARY KEY,
            pe REAL,
            pb REAL,
            roe REAL,
            debt_ratio REAL,
            gross_margin REAL,
            gross_margin_std REAL,
            revenue_growth REAL,
            profit_growth REAL,
            revenue_cagr_3y REAL,
            profit_cagr_3y REAL,
            cashflow REAL,
            dividend_yield REAL,
            dividend_stability REAL,
            sector TEXT DEFAULT '',
            updated_at TEXT
        )
        """
    )
