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
