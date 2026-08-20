ALTER TABLE open_positions ADD COLUMN shares INTEGER NOT NULL DEFAULT 0;
ALTER TABLE trade_plan     ADD COLUMN shares INTEGER;
ALTER TABLE trade_events   ADD COLUMN shares INTEGER;
ALTER TABLE trade_events   ADD COLUMN pnl_amt REAL;
UPDATE open_positions SET shares = 200 WHERE status = 'open' AND shares = 0;
