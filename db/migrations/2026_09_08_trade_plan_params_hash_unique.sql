-- trade_plan UNIQUE 加入 params_hash，使得同一 (plan_date, portfolio, code, action)
-- 下可以并存多个策略的 plan 行（每个策略因 params_hash 中含 strategy 而相异）。
-- UNIQUE 重建需要先临时表换名。
CREATE TABLE trade_plan_new (
    plan_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_date   TEXT    NOT NULL,
    portfolio   TEXT    NOT NULL DEFAULT 'default',
    code        TEXT    NOT NULL,
    action      TEXT    NOT NULL,
    plan_price  REAL    NOT NULL,
    size_pct    REAL    NOT NULL,
    stop_price  REAL    NOT NULL,
    tp_price    REAL    NOT NULL,
    rr_ratio    REAL    NOT NULL,
    status      TEXT    NOT NULL DEFAULT 'ok',
    reason      TEXT    NOT NULL DEFAULT '',
    rationale_json TEXT NOT NULL DEFAULT '{}',
    params_hash TEXT    NOT NULL DEFAULT '',
    created_at  TEXT    NOT NULL DEFAULT (datetime('now')),
    shares      INTEGER NOT NULL DEFAULT 0,
    UNIQUE(plan_date, portfolio, code, action, params_hash)
);

INSERT INTO trade_plan_new
  (plan_id, plan_date, portfolio, code, action, plan_price, size_pct,
   stop_price, tp_price, rr_ratio, status, reason, rationale_json,
   params_hash, created_at, shares)
SELECT plan_id, plan_date, portfolio, code, action, plan_price, size_pct,
       stop_price, tp_price, rr_ratio, status, reason, rationale_json,
       params_hash, created_at, shares
  FROM trade_plan;

DROP TABLE trade_plan;
ALTER TABLE trade_plan_new RENAME TO trade_plan;

CREATE INDEX IF NOT EXISTS idx_trade_plan_date ON trade_plan(plan_date);
CREATE INDEX IF NOT EXISTS idx_trade_plan_portfolio_date
  ON trade_plan(portfolio, plan_date);
