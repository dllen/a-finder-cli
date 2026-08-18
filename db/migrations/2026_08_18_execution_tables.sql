CREATE TABLE IF NOT EXISTS trade_plan (
    plan_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_date        TEXT NOT NULL,
    code             TEXT NOT NULL,
    action           TEXT NOT NULL CHECK(action IN ('buy','hold','exit')),
    plan_price       REAL NOT NULL,
    size_pct         REAL NOT NULL,
    stop_price       REAL NOT NULL,
    tp_price         REAL NOT NULL,
    rr_ratio         REAL NOT NULL,
    status           TEXT NOT NULL CHECK(status IN ('ok','failed')),
    reason           TEXT DEFAULT '',
    rationale_json   TEXT NOT NULL,
    params_hash      TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    UNIQUE(plan_date, code, action)
);
CREATE INDEX IF NOT EXISTS idx_trade_plan_date ON trade_plan(plan_date);

CREATE TABLE IF NOT EXISTS open_positions (
    pos_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    code             TEXT NOT NULL,
    entry_date       TEXT NOT NULL,
    entry_price      REAL NOT NULL,
    size_pct         REAL NOT NULL,
    stop_price       REAL NOT NULL,
    tp_price         REAL NOT NULL,
    status           TEXT NOT NULL CHECK(status IN ('open','closed')),
    close_date       TEXT,
    close_price      REAL,
    close_reason     TEXT
);
CREATE INDEX IF NOT EXISTS idx_open_positions_status ON open_positions(status);
CREATE INDEX IF NOT EXISTS idx_open_positions_code ON open_positions(code);

CREATE TABLE IF NOT EXISTS trade_events (
    event_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_date        TEXT NOT NULL,
    code             TEXT NOT NULL,
    event_type       TEXT NOT NULL CHECK(event_type IN ('open','close')),
    price            REAL NOT NULL,
    size_pct         REAL,
    pnl_pct          REAL,
    note             TEXT,
    created_at       TEXT NOT NULL
);