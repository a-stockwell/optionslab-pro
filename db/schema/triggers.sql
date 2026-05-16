-- =============================================================================
-- Triggers — Cross-table business rule enforcement
-- Design principle: constraints at the data layer, triggers for cross-table rules.
-- Application logic is a secondary safety net — these are the primary guard.
-- Depends on: layer1_reference.sql, layer2_chains.sql, layer3_equity.sql
-- =============================================================================

PRAGMA foreign_keys = ON;

-- -----------------------------------------------------------------------------
-- trg_bucket_config_one_active_insert
-- trg_bucket_config_one_active_update
-- When a bucket_config row is inserted or updated with is_active = 1,
-- deactivate all other rows for the same account + bucket combination.
-- Enforces: only one is_active = 1 per account+bucket at any time.
-- -----------------------------------------------------------------------------
CREATE TRIGGER IF NOT EXISTS trg_bucket_config_one_active_insert
AFTER INSERT ON bucket_config
WHEN NEW.is_active = 1
BEGIN
    UPDATE bucket_config
    SET    is_active = 0
    WHERE  account_id = NEW.account_id
      AND  bucket_id  = NEW.bucket_id
      AND  id         != NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS trg_bucket_config_one_active_update
AFTER UPDATE OF is_active ON bucket_config
WHEN NEW.is_active = 1
BEGIN
    UPDATE bucket_config
    SET    is_active = 0
    WHERE  account_id = NEW.account_id
      AND  bucket_id  = NEW.bucket_id
      AND  id         != NEW.id;
END;

-- -----------------------------------------------------------------------------
-- trg_option_legs_chain_match_insert
-- On INSERT to option_legs, verify that account_id and bucket_id match the
-- parent chain. Prevents drift between leg and chain records.
-- -----------------------------------------------------------------------------
CREATE TRIGGER IF NOT EXISTS trg_option_legs_chain_match_insert
BEFORE INSERT ON option_legs
BEGIN
    SELECT RAISE(ABORT, 'option_legs.account_id must match parent chain.account_id')
    WHERE (
        SELECT account_id FROM chains WHERE id = NEW.chain_id
    ) != NEW.account_id;

    SELECT RAISE(ABORT, 'option_legs.bucket_id must match parent chain.bucket_id')
    WHERE (
        SELECT bucket_id FROM chains WHERE id = NEW.chain_id
    ) != NEW.bucket_id;
END;

-- -----------------------------------------------------------------------------
-- trg_share_blocks_chain_match_insert
-- On INSERT to share_blocks, verify that account_id and bucket_id match the
-- parent chain. Prevents drift between share block and chain records.
-- -----------------------------------------------------------------------------
CREATE TRIGGER IF NOT EXISTS trg_share_blocks_chain_match_insert
BEFORE INSERT ON share_blocks
BEGIN
    SELECT RAISE(ABORT, 'share_blocks.account_id must match parent chain.account_id')
    WHERE (
        SELECT account_id FROM chains WHERE id = NEW.chain_id
    ) != NEW.account_id;

    SELECT RAISE(ABORT, 'share_blocks.bucket_id must match parent chain.bucket_id')
    WHERE (
        SELECT bucket_id FROM chains WHERE id = NEW.chain_id
    ) != NEW.bucket_id;
END;

-- -----------------------------------------------------------------------------
-- trg_option_legs_compression_insert
-- trg_option_legs_compression_update
-- Block premium_status = 'COMPRESSED' on a leg whose parent chain's strategy
-- has allows_compression = 0 (e.g. BEAR_CALL_SPREAD, IRON_CONDOR).
-- Applies on both INSERT and UPDATE of premium_status.
-- -----------------------------------------------------------------------------
CREATE TRIGGER IF NOT EXISTS trg_option_legs_compression_insert
BEFORE INSERT ON option_legs
WHEN NEW.premium_status = 'COMPRESSED'
BEGIN
    SELECT RAISE(ABORT, 'premium_status COMPRESSED is invalid for this strategy (allows_compression = 0)')
    WHERE (
        SELECT sd.allows_compression
        FROM   chains c
        JOIN   strategy_definitions sd ON sd.id = c.strategy_definition_id
        WHERE  c.id = NEW.chain_id
    ) = 0;
END;

CREATE TRIGGER IF NOT EXISTS trg_option_legs_compression_update
BEFORE UPDATE OF premium_status ON option_legs
WHEN NEW.premium_status = 'COMPRESSED'
BEGIN
    SELECT RAISE(ABORT, 'premium_status COMPRESSED is invalid for this strategy (allows_compression = 0)')
    WHERE (
        SELECT sd.allows_compression
        FROM   chains c
        JOIN   strategy_definitions sd ON sd.id = c.strategy_definition_id
        WHERE  c.id = NEW.chain_id
    ) = 0;
END;
