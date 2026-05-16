-- =============================================================================
-- Layer 3 — Equity Exit Table
-- equity_positions is defined in layer2_chains.sql (required before option_legs).
-- This file adds equity_exits, which depends on both equity_positions and
-- option_legs — so it must load after layer2.
--
-- Depends on: layer1_reference.sql, layer2_chains.sql
-- =============================================================================

PRAGMA foreign_keys = ON;

-- -----------------------------------------------------------------------------
-- equity_exits
-- One row per equity position exit — manual sale or called away via CC.
--
-- realized_pl_gross: (exit_price - purchase_price) * shares_exited  [always populated]
-- realized_pl_net:   gross + total CC premiums compressed            [FULL_CHAIN only]
-- option_leg_id:     populated only when exit is triggered by CC assignment
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS equity_exits (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    equity_position_id      INTEGER NOT NULL REFERENCES equity_positions(id),
    exit_price              INTEGER NOT NULL                    -- in cents
                            CHECK (exit_price > 0),
    exit_date               TEXT NOT NULL,                      -- ISO date
    shares_exited           INTEGER NOT NULL
                            CHECK (shares_exited > 0),
    realized_pl_gross       INTEGER NOT NULL,                   -- (exit_price - purchase_price) * shares_exited
    realized_pl_net         INTEGER,                            -- gross + total CC premiums compressed (FULL_CHAIN only)
    option_leg_id           INTEGER REFERENCES option_legs(id), -- populated if exit via CC assignment
    year                    INTEGER,                            -- ISO year
    week                    INTEGER,                            -- ISO week
    notes                   TEXT
);
