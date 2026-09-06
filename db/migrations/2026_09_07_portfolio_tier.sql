-- db/migrations/2026_09_07_portfolio_tier.sql
-- 重建 trade_plan：portfolio 列 + UNIQUE 改 (plan_date, portfolio, code, action)
CREATE TABLE trade_plan_new (
    plan_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    portfolio        TEXT NOT NULL DEFAULT 'default',
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
    shares           INTEGER,
    UNIQUE(plan_date, portfolio, code, action)
);
INSERT INTO trade_plan_new
  SELECT plan_id, 'default', plan_date, code, action, plan_price, size_pct,
         stop_price, tp_price, rr_ratio, status, reason, rationale_json,
         params_hash, created_at, shares
  FROM trade_plan;
DROP TABLE trade_plan;
ALTER TABLE trade_plan_new RENAME TO trade_plan;

CREATE INDEX IF NOT EXISTS idx_trade_plan_portfolio_date
  ON trade_plan(portfolio, plan_date);

-- open_positions: 加 portfolio 列 + partial unique index
ALTER TABLE open_positions ADD COLUMN portfolio TEXT NOT NULL DEFAULT 'default';

CREATE UNIQUE INDEX IF NOT EXISTS uq_open_positions_portfolio_code_open
  ON open_positions(portfolio, code)
  WHERE status = 'open';

CREATE INDEX IF NOT EXISTS idx_open_positions_portfolio
  ON open_positions(portfolio);

-- trade_events: 加 portfolio 列 + 索引
ALTER TABLE trade_events ADD COLUMN portfolio TEXT NOT NULL DEFAULT 'default';

CREATE INDEX IF NOT EXISTS idx_trade_events_portfolio
  ON trade_events(portfolio);
