-- db/migrations/2026_10_24_convertible_bond_constraints.sql
-- 可转债 schema 约束加固：修复已应用 2026_10_24_convertible_bond.sql 的存量库。
-- SQLite 无法用 ALTER 添加 NOT NULL，故 cb_positions 采用重建表方式；
-- cb_picks 的同日同 code 唯一约束用 UNIQUE INDEX 实现，无需重建。

-- 1) cb_positions：entry_date / entry_price / shares 补 NOT NULL
CREATE TABLE IF NOT EXISTS cb_positions_new (
    code        TEXT PRIMARY KEY,
    name        TEXT,
    stock_code  TEXT,
    entry_date  TEXT NOT NULL,
    entry_price REAL NOT NULL,
    shares      INTEGER NOT NULL,
    stop_price  REAL,
    tp_price    REAL,
    note        TEXT,
    updated_at  TEXT
);

-- 仅用 COALESCE 兜住 NULL 关键字段；其余约束冲突应报错而非静默丢弃持仓。
-- 注意：不用 OR IGNORE，避免脏数据被静默跳过导致持仓丢失。
INSERT INTO cb_positions_new (
    code, name, stock_code, entry_date, entry_price, shares,
    stop_price, tp_price, note, updated_at
)
SELECT
    code, name, stock_code,
    -- 存量脏数据（NULL 关键字段）补默认值，避免 NOT NULL 重建失败
    COALESCE(entry_date, ''),
    COALESCE(entry_price, 0),
    COALESCE(shares, 0),
    stop_price, tp_price, note, updated_at
FROM cb_positions;

DROP TABLE cb_positions;
ALTER TABLE cb_positions_new RENAME TO cb_positions;

-- cb_positions 上无索引；如后续新增索引，在此一并重建。

-- 2) cb_universe：listed 补 NOT NULL（SQLite 支持 ALTER COLUMN SET NOT NULL）
UPDATE cb_universe SET listed = 0 WHERE listed IS NULL;
ALTER TABLE cb_universe ALTER COLUMN listed SET NOT NULL;

-- 3) cb_picks：同日同 code 唯一（防止同一天重复推荐同一只债）
CREATE UNIQUE INDEX IF NOT EXISTS idx_cb_picks_date_code ON cb_picks(trade_date, code);

-- 4) cb_factors：补 code 索引
CREATE INDEX IF NOT EXISTS idx_cb_factors_code ON cb_factors(code);
