-- =============================================================================
-- Seed Data — Reference tables
-- Run once after schema creation. Uses INSERT OR IGNORE so re-running is safe.
-- =============================================================================

PRAGMA foreign_keys = ON;

-- -----------------------------------------------------------------------------
-- accounts
-- -----------------------------------------------------------------------------
INSERT OR IGNORE INTO accounts (id, name, broker) VALUES
    ('MGN',  'Margin Account', 'Schwab'),
    ('ROTH', 'Roth IRA',       'Schwab'),
    ('CASH', 'Cash Account',   'Schwab');

-- -----------------------------------------------------------------------------
-- buckets
-- sort_order controls display ordering on the dashboard.
-- -----------------------------------------------------------------------------
INSERT OR IGNORE INTO buckets (id, sort_order) VALUES
    ('A',      1),
    ('B',      2),
    ('C',      3),
    ('Legacy', 4),
    ('ODTE',   5);

-- -----------------------------------------------------------------------------
-- strategy_definitions
-- leg_count for WHEEL is set to 1 (minimum); actual chains may have more legs
-- as rolls and CCs accumulate under the same chain_id.
-- -----------------------------------------------------------------------------
INSERT OR IGNORE INTO strategy_definitions
    (name, category, leg_count, allows_assignment, allows_compression, max_loss_defined)
VALUES
    ('WHEEL',               'INCOME',      1, 1, 1, 0),
    ('COVERED_CALL',        'INCOME',      1, 0, 0, 0),
    ('CASH_SECURED_PUT',    'INCOME',      1, 1, 1, 0),
    ('BEAR_CALL_SPREAD',    'DIRECTIONAL', 2, 0, 0, 1),
    ('BULL_PUT_SPREAD',     'DIRECTIONAL', 2, 0, 0, 1),
    ('IRON_CONDOR',         'NEUTRAL',     4, 0, 0, 1),
    ('IRON_BUTTERFLY',      'NEUTRAL',     4, 0, 0, 1),
    ('STRANGLE',            'NEUTRAL',     2, 1, 0, 0),
    ('STRADDLE',            'NEUTRAL',     2, 1, 0, 0),
    ('DIAGONAL_PUT',        'INCOME',      2, 1, 1, 0),
    ('DIAGONAL_CALL',       'INCOME',      2, 0, 0, 0),
    ('COVERED_CALL_EQUITY', 'INCOME',      1, 1, 1, 0);
