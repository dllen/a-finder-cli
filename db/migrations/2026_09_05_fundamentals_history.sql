-- db/migrations/2026_09_05_fundamentals_history.sql
CREATE TABLE IF NOT EXISTS fundamentals_history (
    code TEXT NOT NULL,
    year INTEGER NOT NULL,
    gross_margin REAL,
    roe_excl REAL,
    revenue REAL,
    net_profit_excl REAL,
    report_date TEXT,
    synced_at TEXT,
    PRIMARY KEY (code, year)
);
CREATE INDEX IF NOT EXISTS idx_fundamentals_history_code
    ON fundamentals_history(code);
