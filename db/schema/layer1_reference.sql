-- =============================================================================
-- Layer 1 — Configuration & Reference Tables
-- These tables define the structure of the system. They change rarely.
-- =============================================================================

PRAGMA foreign_keys = ON;

-- -----------------------------------------------------------------------------
-- accounts
-- One row per trading account.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS accounts (
    id              TEXT PRIMARY KEY,       -- e.g. 'MGN', 'ROTH', 'CASH'
    name            TEXT NOT NULL,          -- e.g. 'Margin Account'
    broker          TEXT,                   -- e.g. 'Schwab'
    account_number  TEXT,                   -- masked, e.g. '****1234'
    is_active       INTEGER DEFAULT 1       -- boolean
                    CHECK (is_active IN (0, 1)),
    notes           TEXT
);

-- -----------------------------------------------------------------------------
-- buckets
-- Reference table for bucket identifiers.
-- Intentionally minimal — config lives in bucket_config.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS buckets (
    id          TEXT PRIMARY KEY,           -- 'A', 'B', 'C', 'Legacy', 'ODTE'
    sort_order  INTEGER NOT NULL
);

-- -----------------------------------------------------------------------------
-- bucket_config
-- One row per account + bucket + effective_date combination.
-- Capital allocation targets live here.
-- Every bucket size change creates a new row — old rows are never deleted.
-- Only one row per account+bucket may have is_active = 1 at any time
-- (enforced by trigger trg_bucket_config_one_active in triggers.sql).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bucket_config (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id      TEXT NOT NULL REFERENCES accounts(id),
    bucket_id       TEXT NOT NULL REFERENCES buckets(id),
    bucket_size     INTEGER NOT NULL            -- in cents, e.g. 1050000 = $10,500.00
                    CHECK (bucket_size > 0),
    is_active       INTEGER DEFAULT 1
                    CHECK (is_active IN (0, 1)),
    effective_date  TEXT NOT NULL,              -- ISO date — required, drives historical reporting
    notes           TEXT,
    UNIQUE (account_id, bucket_id, effective_date)
);

-- -----------------------------------------------------------------------------
-- strategy_definitions
-- One row per strategy type.
-- Adding a new strategy never requires a schema change — just a new row.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS strategy_definitions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL UNIQUE,   -- 'WHEEL', 'BEAR_CALL_SPREAD', etc.
    category            TEXT NOT NULL
                        CHECK (category IN ('INCOME', 'DIRECTIONAL', 'NEUTRAL', 'HEDGE')),
    leg_count           INTEGER NOT NULL
                        CHECK (leg_count > 0),
    allows_assignment   INTEGER NOT NULL        -- boolean: can legs result in share acquisition?
                        CHECK (allows_assignment IN (0, 1)),
    allows_compression  INTEGER NOT NULL        -- boolean: can premiums compress cost basis?
                        CHECK (allows_compression IN (0, 1)),
    max_loss_defined    INTEGER NOT NULL        -- boolean: is max loss capped (spreads, iron fly)?
                        CHECK (max_loss_defined IN (0, 1)),
    notes               TEXT
);
