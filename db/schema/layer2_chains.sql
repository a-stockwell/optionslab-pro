-- =============================================================================
-- Layer 2 — Chain & Position Tables
-- Tracks the lifecycle of every trade from open to close.
-- Depends on: layer1_reference.sql
-- =============================================================================

PRAGMA foreign_keys = ON;

-- -----------------------------------------------------------------------------
-- chains
-- One row per trade cycle. The parent record for everything.
-- id:    system-generated prefixed token ('chn_a1b2c3d4') — stable forever
-- alias: human-readable label ('m-te-7-0311') — cosmetic only, no FKs reference it
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS chains (
    id                      TEXT PRIMARY KEY,       -- e.g. 'chn_a1b2c3d4'
    alias                   TEXT UNIQUE,            -- e.g. 'm-te-7-0311', display only
    ticker                  TEXT NOT NULL,
    account_id              TEXT NOT NULL REFERENCES accounts(id),
    bucket_id               TEXT NOT NULL REFERENCES buckets(id),
    strategy_definition_id  INTEGER NOT NULL REFERENCES strategy_definitions(id),
    reporting_method        TEXT NOT NULL
                            CHECK (reporting_method IN ('YIELD_ENGINE', 'FULL_CHAIN')),
    status                  TEXT NOT NULL DEFAULT 'OPEN'
                            CHECK (status IN ('OPEN', 'CLOSED', 'PARTIALLY_CLOSED')),
    opened_date             TEXT NOT NULL,          -- ISO date
    closed_date             TEXT,                   -- ISO date, NULL if still open
    notes                   TEXT
);

-- -----------------------------------------------------------------------------
-- share_blocks
-- One row per 100-share block acquired through assignment or purchase.
-- Defined before option_legs because option_legs holds a FK to share_blocks.
-- breakeven values are NOT stored here — always computed via v_share_breakeven.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS share_blocks (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    chain_id            TEXT NOT NULL REFERENCES chains(id),
    account_id          TEXT NOT NULL REFERENCES accounts(id),
    bucket_id           TEXT NOT NULL REFERENCES buckets(id),
    block_number        INTEGER NOT NULL            -- 1, 2, 3... per chain
                        CHECK (block_number > 0),
    shares              INTEGER NOT NULL DEFAULT 100
                        CHECK (shares > 0),
    acquisition_type    TEXT NOT NULL
                        CHECK (acquisition_type IN ('ASSIGNED', 'PURCHASED')),
    assigned_strike     INTEGER NOT NULL            -- in cents, cost basis
                        CHECK (assigned_strike > 0),
    acquisition_date    TEXT NOT NULL,              -- ISO date
    status              TEXT NOT NULL DEFAULT 'OPEN'
                        CHECK (status IN ('OPEN', 'CALLED_AWAY', 'SOLD')),

    -- Reporting
    capital_deployed    INTEGER NOT NULL            -- in cents: assigned_strike * shares
                        CHECK (capital_deployed > 0),
    year                INTEGER,                    -- ISO year
    week                INTEGER,                    -- ISO week (1–53)
    notes               TEXT,

    UNIQUE (chain_id, block_number)
);

-- -----------------------------------------------------------------------------
-- equity_positions
-- Defined here (before option_legs) so option_legs can hold a valid FK to it.
-- Full business logic documented in layer3_equity.sql alongside equity_exits.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS equity_positions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker              TEXT NOT NULL,
    account_id          TEXT NOT NULL REFERENCES accounts(id),
    bucket_id           TEXT REFERENCES buckets(id),            -- NULL = outside bucket system
    shares              INTEGER NOT NULL
                        CHECK (shares > 0),
    purchase_price      INTEGER NOT NULL                        -- in cents, e.g. 1200 = $12.00
                        CHECK (purchase_price > 0),
    purchase_date       TEXT NOT NULL,                          -- ISO date
    capital_deployed    INTEGER NOT NULL                        -- in cents: purchase_price * shares
                        CHECK (capital_deployed > 0),
    purpose             TEXT NOT NULL DEFAULT 'INCOME'
                        CHECK (purpose IN ('INCOME', 'COMPRESSION', 'HOLD')),
    status              TEXT NOT NULL DEFAULT 'OPEN'
                        CHECK (status IN ('OPEN', 'CLOSED', 'PARTIALLY_CLOSED')),
    notes               TEXT
);

-- -----------------------------------------------------------------------------
-- option_legs
-- One row per discrete leg event. Core transaction table.
-- FKs to share_blocks and equity_positions for explicit lot linkage (v0.7).
-- Self-referential FK on rolled_from_leg_id for roll lineage.
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS option_legs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    chain_id            TEXT NOT NULL REFERENCES chains(id),
    account_id          TEXT NOT NULL REFERENCES accounts(id),
    bucket_id           TEXT NOT NULL REFERENCES buckets(id),

    -- Leg identity
    leg_role            TEXT NOT NULL
                        CHECK (leg_role IN ('SHORT', 'LONG')),
    option_type         TEXT NOT NULL
                        CHECK (option_type IN ('PUT', 'CALL')),
    strike              INTEGER NOT NULL            -- in cents, e.g. 5600 = $56.00
                        CHECK (strike > 0),
    expiration_date     TEXT NOT NULL,              -- ISO date

    -- Lifecycle
    status              TEXT NOT NULL
                        CHECK (status IN (
                            'OPEN', 'CLOSED', 'EXPIRED',
                            'ASSIGNED', 'ROLLED', 'CANCELLED'
                        )),
    date_in             TEXT NOT NULL,              -- ISO date
    date_out            TEXT,                       -- ISO date, NULL if open
    dte_at_open         INTEGER,
    dit                 INTEGER,                    -- days in trade at close

    -- Premium (all in cents)
    contracts           INTEGER NOT NULL
                        CHECK (contracts > 0),
    premium_raw         INTEGER NOT NULL,           -- per share in cents, negative = debit
    premium_net         INTEGER,                    -- total: premium_raw * contracts * 100
    credit_debit        TEXT NOT NULL
                        CHECK (credit_debit IN ('CREDIT', 'DEBIT')),

    -- Premium accounting
    premium_status      TEXT NOT NULL DEFAULT 'OPEN_CREDIT'
                        CHECK (premium_status IN (
                            'OPEN_CREDIT', 'REALIZED',
                            'PARTIALLY_REALIZED', 'COMPRESSED', 'LOSS'
                        )),
    realized_date       TEXT,                       -- ISO date
    realized_amount     INTEGER,                    -- in cents, actual amount locked in at close

    -- Collateral / risk (in cents)
    collateral_per_contract INTEGER,               -- strike * 100 for CSP, width * 100 for spreads
    collateral_total    INTEGER,                   -- collateral_per_contract * contracts

    -- Performance (REAL — ratios, not currency)
    roc_pct             REAL,                       -- return on collateral %
    roc_annual_pct      REAL,                       -- annualized

    -- Lot linkage — explicit FK to the specific lot this leg covers (v0.7)
    -- Only one of share_block_id or equity_position_id may be populated.
    share_block_id      INTEGER REFERENCES share_blocks(id),
    equity_position_id  INTEGER REFERENCES equity_positions(id),

    -- Roll lineage — points to the closing leg this opening leg replaced
    rolled_from_leg_id  INTEGER REFERENCES option_legs(id),

    -- Meta
    year                INTEGER,                    -- ISO year (from date_in isocalendar)
    week                INTEGER,                    -- ISO week (from date_in isocalendar)
    notes               TEXT,

    -- Table-level constraint: only one lot linkage column may be populated per leg
    CHECK (NOT (share_block_id IS NOT NULL AND equity_position_id IS NOT NULL))
);

-- -----------------------------------------------------------------------------
-- share_exits
-- One row per share block exit event (called-away or manual sale).
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS share_exits (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    share_block_id      INTEGER NOT NULL REFERENCES share_blocks(id),
    chain_id            TEXT NOT NULL REFERENCES chains(id),
    exit_type           TEXT NOT NULL
                        CHECK (exit_type IN ('CALLED_AWAY', 'SOLD')),
    exit_price          INTEGER NOT NULL            -- in cents
                        CHECK (exit_price > 0),
    exit_date           TEXT NOT NULL,              -- ISO date
    shares_exited       INTEGER NOT NULL
                        CHECK (shares_exited > 0),

    -- P/L (in cents)
    realized_pl_gross   INTEGER NOT NULL,           -- (exit_price - assigned_strike) * shares
    realized_pl_net     INTEGER,                    -- gross + cc_premium (FULL_CHAIN only)

    -- Linked option leg if called away via CC assignment
    option_leg_id       INTEGER REFERENCES option_legs(id),

    year                INTEGER,                    -- ISO year
    week                INTEGER,                    -- ISO week
    notes               TEXT
);
