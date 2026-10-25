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
