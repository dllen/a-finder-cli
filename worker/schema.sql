-- D1 schema：持仓唯一真相源。与 db/migrations/2026_10_25_real_positions.sql 一致。
CREATE TABLE IF NOT EXISTS real_positions (
    code          TEXT PRIMARY KEY,
    name          TEXT,
    shares        INTEGER NOT NULL,
    entry_price   REAL NOT NULL,
    stop_price    REAL,
    entry_date    TEXT,
    note          TEXT,
    updated_at    TEXT
);

CREATE TABLE IF NOT EXISTS real_cb_positions (
    code          TEXT PRIMARY KEY,
    name          TEXT,
    shares        INTEGER NOT NULL,
    entry_price   REAL NOT NULL,
    stop_price    REAL,
    entry_date    TEXT,
    note          TEXT,
    updated_at    TEXT
);

-- 行情：字段与 SQLite daily_prices 对齐（turnover 必须保留：
-- strategies/backtest.py 与 market_data.py 会读）
CREATE TABLE IF NOT EXISTS market_prices (
    code       TEXT NOT NULL,
    trade_date TEXT NOT NULL,
    open REAL, close REAL, high REAL, low REAL,
    volume REAL, amount REAL, turnover REAL, pct_change REAL,
    PRIMARY KEY (code, trade_date)
);
CREATE INDEX IF NOT EXISTS idx_mp_date ON market_prices(trade_date);

-- 成分股：由 GitHub Actions 每日全量 upsert（Worker 不自己维护名单）
CREATE TABLE IF NOT EXISTS market_symbols (
    code TEXT PRIMARY KEY,
    name TEXT,
    updated_at TEXT
);

-- 抓取任务：状态机的全部状态
CREATE TABLE IF NOT EXISTS sync_jobs (
    id         TEXT PRIMARY KEY,
    status     TEXT NOT NULL,
    cursor     INTEGER NOT NULL DEFAULT 0,
    total      INTEGER NOT NULL,
    attempts   INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    note       TEXT
);

-- 心跳：每次 cron 进入抓取逻辑时写一条，用于判断 cron 是否在触发
-- （排查「任务卡 pending」时最直接的证据）
CREATE TABLE IF NOT EXISTS worker_heartbeat (
    name TEXT PRIMARY KEY,
    at   TEXT NOT NULL,
    note TEXT
);
