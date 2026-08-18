ALTER TABLE daily_picks ADD COLUMN updated_at TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_daily_picks_updated_at ON daily_picks(updated_at);