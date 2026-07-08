-- Market Intelligence Engine — SQLite schema (reference)
-- The application creates these tables automatically via SQLAlchemy (init_db()).
-- This file documents the schema for reviewers.

CREATE TABLE IF NOT EXISTS candles (
    id         INTEGER PRIMARY KEY,
    asset      TEXT    NOT NULL,          -- e.g. BTC/USDT
    timeframe  TEXT    NOT NULL,          -- 1h | 4h
    open_time  INTEGER NOT NULL,          -- ms epoch of bar open
    open       REAL    NOT NULL,
    high       REAL    NOT NULL,
    low        REAL    NOT NULL,
    close      REAL    NOT NULL,
    volume     REAL    NOT NULL,
    UNIQUE (asset, timeframe, open_time)
);
CREATE INDEX IF NOT EXISTS ix_candles_asset      ON candles(asset);
CREATE INDEX IF NOT EXISTS ix_candles_timeframe  ON candles(timeframe);
CREATE INDEX IF NOT EXISTS ix_candles_open_time  ON candles(open_time);

CREATE TABLE IF NOT EXISTS hypotheses (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    logic       TEXT NOT NULL,           -- human-readable description
    conditions  JSON NOT NULL,           -- machine-readable rule spec
    direction   TEXT NOT NULL,           -- long | short
    timeframe   TEXT NOT NULL,
    asset       TEXT NOT NULL,
    created_at  DATETIME
);

CREATE TABLE IF NOT EXISTS backtests (
    id             INTEGER PRIMARY KEY,
    hypothesis_id  INTEGER REFERENCES hypotheses(id),
    total_return   REAL,
    win_rate       REAL,
    profit_factor  REAL,
    max_drawdown   REAL,
    sharpe         REAL,
    num_trades     INTEGER,
    avg_win        REAL,
    avg_loss       REAL,
    rejected       BOOLEAN,
    reject_reasons JSON,
    equity_curve   JSON,
    created_at     DATETIME
);
CREATE INDEX IF NOT EXISTS ix_backtests_hypothesis ON backtests(hypothesis_id);

CREATE TABLE IF NOT EXISTS reports (
    id             INTEGER PRIMARY KEY,
    backtest_id    INTEGER REFERENCES backtests(id),
    hypothesis_id  INTEGER REFERENCES hypotheses(id),
    title          TEXT NOT NULL,
    markdown       TEXT NOT NULL,
    created_at     DATETIME
);
CREATE INDEX IF NOT EXISTS ix_reports_backtest   ON reports(backtest_id);
CREATE INDEX IF NOT EXISTS ix_reports_hypothesis ON reports(hypothesis_id);
