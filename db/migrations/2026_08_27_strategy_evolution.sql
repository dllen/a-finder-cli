CREATE TABLE IF NOT EXISTS pick_outcomes (
    date        TEXT NOT NULL,
    source      TEXT NOT NULL CHECK(source IN ('replay','live')),
    code        TEXT NOT NULL,
    strategy    TEXT NOT NULL,
    name        TEXT DEFAULT '',
    kind        TEXT NOT NULL DEFAULT '',
    score       REAL,
    buy         REAL NOT NULL,
    stop        REAL,
    target      REAL,
    exit_date   TEXT,
    exit_price  REAL,
    outcome_pct REAL,
    win         INTEGER CHECK(win IN (0,1)),
    labeled_at  TEXT NOT NULL,
    PRIMARY KEY (date, source, code, strategy)
);
CREATE INDEX IF NOT EXISTS idx_pick_outcomes_strategy ON pick_outcomes(strategy);
CREATE INDEX IF NOT EXISTS idx_pick_outcomes_date ON pick_outcomes(date);

CREATE TABLE IF NOT EXISTS strategy_config (
    version      INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL,
    active_json  TEXT NOT NULL,
    ratios_json  TEXT NOT NULL,
    metrics_json TEXT,
    status       TEXT NOT NULL CHECK(status IN ('champion','rejected','rolled_back')),
    reason       TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_strategy_config_status ON strategy_config(status);
