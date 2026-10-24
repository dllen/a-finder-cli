-- db/migrations/2026_10_24_convertible_bond.sql
-- 可转债策略：独立于股票体系，cb_ 前缀表
CREATE TABLE IF NOT EXISTS cb_universe (
    trade_date     TEXT NOT NULL,
    code           TEXT NOT NULL,
    name           TEXT,
    stock_code     TEXT,
    stock_name     TEXT,
    price          REAL,
    convert_price  REAL,
    convert_value  REAL,
    premium_rate   REAL,
    stock_price    REAL,
    rating         TEXT,
    issue_size     REAL,
    listed         INTEGER DEFAULT 0,
    updated_at     TEXT,
    PRIMARY KEY (trade_date, code)
);

CREATE TABLE IF NOT EXISTS cb_factors (
    trade_date    TEXT NOT NULL,
    code          TEXT NOT NULL,
    bond_floor    REAL,
    ytm           REAL,
    year_left     REAL,
    redeem_dist   REAL,
    put_dist      REAL,
    adj_count     INTEGER,
    redeem_status TEXT,
    factor_json   TEXT,
    updated_at    TEXT,
    PRIMARY KEY (trade_date, code)
);

CREATE TABLE IF NOT EXISTS cb_picks (
    trade_date    TEXT NOT NULL,
    rank          INTEGER NOT NULL,
    code          TEXT NOT NULL,
    name          TEXT,
    price         REAL,
    total_score   REAL,
    a_score       REAL,
    b_score       REAL,
    c_score       REAL,
    size_pct      REAL,
    shares        INTEGER,
    stop_price    REAL,
    tp_price      REAL,
    reason        TEXT,
    risk_note     TEXT,
    factor_counts TEXT,
    updated_at    TEXT,
    PRIMARY KEY (trade_date, rank)
);

CREATE TABLE IF NOT EXISTS cb_positions (
    code        TEXT PRIMARY KEY,
    name        TEXT,
    stock_code  TEXT,
    entry_date  TEXT,
    entry_price REAL,
    shares      INTEGER,
    stop_price  REAL,
    tp_price    REAL,
    note        TEXT,
    updated_at  TEXT
);

CREATE INDEX IF NOT EXISTS idx_cb_picks_date ON cb_picks(trade_date);
CREATE INDEX IF NOT EXISTS idx_cb_universe_code ON cb_universe(code);
