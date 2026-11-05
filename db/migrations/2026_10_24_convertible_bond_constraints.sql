-- db/migrations/2026_10_24_convertible_bond_constraints.sql
-- requires-sqlite: 3.53
--
-- ⚠️ 上面那行是**硬性运行时要求**，由 db_repository._run_migrations 解析：
-- 运行环境的 SQLite 低于 3.53 时**跳过本文件**并记 warning，而不是抛异常。
--
-- 为什么必须有（2026-11-05 实测事故）：本文件用 ALTER TABLE ... ALTER COLUMN
-- ... SET NOT NULL（SQLite 3.53+）。本机 sqlite 3.53.1 正常，但 GitHub runner 的
-- python3.11 链接的 SQLite 更老 ⇒ **只在 CI 冷启动（全新库）时**抛
--     sqlite3.OperationalError: near "ALTER": syntax error
-- ⇒ open_db() 崩 ⇒ 同步 3 秒退出 ⇒ 谱系永远建不起来。
-- 本机**永远看不到**：本地库的 _applied_migrations 早记过本文件，直接跳过。
-- 跳过是安全的：本文件只为「存量库」补约束，全新库在
-- 2026_10_24_convertible_bond.sql 里就带着 NOT NULL 建表。
--
-- 可转债 schema 约束加固：修复已应用 2026_10_24_convertible_bond.sql 的存量库。
--
-- 目标：让「已应用旧版迁移的存量库」与「全新库」收敛到完全一致的 schema
--（列名 / 类型 / notnull 标志 / pk 位置 / 索引集合均一致）。
--
-- 旧版迁移的 cb_universe / cb_factors / cb_picks 键列缺少 NOT NULL，
-- 新版原始迁移里才有。SQLite 3.54 支持 ALTER COLUMN ... SET NOT NULL，
-- 且该语句会保留既有索引与数据，因此无需重建表；
-- 仅 cb_positions 因需把已有行的 NULL 补成默认值而采用重建方式。

-- 1) cb_positions：entry_date / entry_price / shares 补 NOT NULL
--    （重建：存量脏数据需用 COALESCE 兜底才能装进 NOT NULL 列）
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

-- 2) cb_universe：键列补 NOT NULL + listed 补 NOT NULL
--    先清 NULL，否则 ALTER 会因存量 NULL 报错。
UPDATE cb_universe SET trade_date = '' WHERE trade_date IS NULL;
UPDATE cb_universe SET code = '' WHERE code IS NULL;
ALTER TABLE cb_universe ALTER COLUMN trade_date SET NOT NULL;
ALTER TABLE cb_universe ALTER COLUMN code SET NOT NULL;
UPDATE cb_universe SET listed = 0 WHERE listed IS NULL;
ALTER TABLE cb_universe ALTER COLUMN listed SET NOT NULL;

-- 3) cb_factors：键列补 NOT NULL
UPDATE cb_factors SET trade_date = '' WHERE trade_date IS NULL;
UPDATE cb_factors SET code = '' WHERE code IS NULL;
ALTER TABLE cb_factors ALTER COLUMN trade_date SET NOT NULL;
ALTER TABLE cb_factors ALTER COLUMN code SET NOT NULL;

-- 4) cb_picks：trade_date / rank / code 补 NOT NULL
--    索引在 ALTER 后自动保留，无需重建。
UPDATE cb_picks SET trade_date = '' WHERE trade_date IS NULL;
UPDATE cb_picks SET rank = 0 WHERE rank IS NULL;
UPDATE cb_picks SET code = '' WHERE code IS NULL;
ALTER TABLE cb_picks ALTER COLUMN trade_date SET NOT NULL;
ALTER TABLE cb_picks ALTER COLUMN rank SET NOT NULL;
ALTER TABLE cb_picks ALTER COLUMN code SET NOT NULL;

-- 5) cb_picks：同日同 code 唯一（防止同一天重复推荐同一只债）
CREATE UNIQUE INDEX IF NOT EXISTS idx_cb_picks_date_code ON cb_picks(trade_date, code);

-- 6) cb_factors：补 code 索引
CREATE INDEX IF NOT EXISTS idx_cb_factors_code ON cb_factors(code);
