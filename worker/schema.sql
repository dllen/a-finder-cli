-- D1 schema：决策台持仓的唯一真相源。与 db/migrations/2026_10_25_real_positions.sql 一致。
--
-- 注意：D1 只存这几百行元数据。**不要把行情写进 D1** —— 免费版行写入仅 10 万/天，
-- 一次 650 天历史回填就是 19.5 万行，会把配额打爆（2026-10-26 踩过）。
-- 行情的数据路径见 CLAUDE.md「行情数据路径」一节。
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
