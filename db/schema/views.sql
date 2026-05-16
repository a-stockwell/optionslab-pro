-- =============================================================================
-- Layer 4 — Computed Views
-- All reporting is derived here — nothing is stored redundantly.
-- All views use CTE-based aggregation to prevent join multiplication.
-- All monetary values returned in cents; display layer divides by 100.
-- Depends on: layer1_reference.sql, layer2_chains.sql, layer3_equity.sql
-- =============================================================================

-- -----------------------------------------------------------------------------
-- v_share_breakeven
-- Computes original, current, and next breakeven for every share block.
-- Joins CC legs via share_block_id (v0.7 explicit lot linkage) — eliminates
-- ambiguity when multiple blocks exist for the same ticker in the same account.
--
-- Breakeven logic:
--   original → assigned_strike minus per-share CSP premium at assignment
--   current  → original minus cumulative closed CC premiums on this block
--   next     → current minus any open CC premium on this block
-- -----------------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS v_share_breakeven AS
WITH

-- CSP premium that caused assignment — joins by chain, PUT ASSIGNED COMPRESSED
csp_compression AS (
    SELECT
        sb.id                                               AS share_block_id,
        COALESCE(SUM(ol.premium_net) / sb.shares, 0)       AS csp_premium_per_share
    FROM share_blocks sb
    JOIN option_legs ol
        ON  ol.chain_id      = sb.chain_id
        AND ol.option_type   = 'PUT'
        AND ol.leg_role      = 'SHORT'
        AND ol.status        = 'ASSIGNED'
        AND ol.premium_status = 'COMPRESSED'
    GROUP BY sb.id
),

-- Closed CC legs explicitly linked to this block via share_block_id
cc_realized AS (
    SELECT
        ol.share_block_id,
        SUM(ol.realized_amount)                             AS total_realized
    FROM option_legs ol
    WHERE ol.option_type    = 'CALL'
      AND ol.leg_role       = 'SHORT'
      AND ol.status         IN ('EXPIRED', 'ASSIGNED')
      AND ol.premium_status = 'REALIZED'
      AND ol.share_block_id IS NOT NULL
    GROUP BY ol.share_block_id
),

-- Open CC leg linked to this block
cc_open AS (
    SELECT
        ol.share_block_id,
        SUM(ol.premium_net)                                 AS total_open
    FROM option_legs ol
    WHERE ol.option_type    = 'CALL'
      AND ol.leg_role       = 'SHORT'
      AND ol.status         = 'OPEN'
      AND ol.premium_status = 'OPEN_CREDIT'
      AND ol.share_block_id IS NOT NULL
    GROUP BY ol.share_block_id
)

SELECT
    sb.id                                                   AS share_block_id,
    sb.chain_id,
    sb.account_id,
    sb.bucket_id,
    sb.block_number,
    sb.assigned_strike,
    sb.shares,
    sb.status,

    sb.assigned_strike
        - COALESCE(c.csp_premium_per_share, 0)             AS breakeven_original,

    sb.assigned_strike
        - COALESCE(c.csp_premium_per_share, 0)
        - COALESCE(r.total_realized / sb.shares, 0)        AS breakeven_current,

    sb.assigned_strike
        - COALESCE(c.csp_premium_per_share, 0)
        - COALESCE(r.total_realized / sb.shares, 0)
        - COALESCE(o.total_open / sb.shares, 0)            AS breakeven_next,

    COALESCE(c.csp_premium_per_share, 0)                   AS csp_compression_per_share,
    COALESCE(r.total_realized / sb.shares, 0)              AS cc_compression_per_share,
    COALESCE(o.total_open / sb.shares, 0)                  AS cc_open_per_share

FROM share_blocks sb
LEFT JOIN csp_compression c  ON c.share_block_id = sb.id
LEFT JOIN cc_realized r      ON r.share_block_id = sb.id
LEFT JOIN cc_open o          ON o.share_block_id = sb.id;


-- -----------------------------------------------------------------------------
-- v_equity_breakeven
-- Parallel to v_share_breakeven but sourced from equity_positions.
-- Joins CC legs via equity_position_id — prevents cross-contamination between
-- two blocks of the same ticker with different purposes (e.g. two SOFI lots).
-- Only meaningful under FULL_CHAIN reporting.
-- -----------------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS v_equity_breakeven AS
WITH

-- Closed CC legs explicitly linked to this equity position
cc_realized AS (
    SELECT
        ol.equity_position_id,
        SUM(ol.realized_amount)                             AS total_realized
    FROM option_legs ol
    WHERE ol.option_type    = 'CALL'
      AND ol.leg_role       = 'SHORT'
      AND ol.status         IN ('EXPIRED', 'ASSIGNED')
      AND ol.premium_status = 'REALIZED'
      AND ol.equity_position_id IS NOT NULL
    GROUP BY ol.equity_position_id
),

-- Open CC leg linked to this equity position
cc_open AS (
    SELECT
        ol.equity_position_id,
        SUM(ol.premium_net)                                 AS total_open
    FROM option_legs ol
    WHERE ol.option_type    = 'CALL'
      AND ol.leg_role       = 'SHORT'
      AND ol.status         = 'OPEN'
      AND ol.premium_status = 'OPEN_CREDIT'
      AND ol.equity_position_id IS NOT NULL
    GROUP BY ol.equity_position_id
)

SELECT
    ep.id                                                   AS equity_position_id,
    ep.ticker,
    ep.account_id,
    ep.shares,
    ep.purchase_price,
    ep.purpose,
    ep.status,

    ep.purchase_price                                       AS breakeven_original,

    ep.purchase_price
        - COALESCE(r.total_realized / ep.shares, 0)        AS breakeven_current,

    ep.purchase_price
        - COALESCE(r.total_realized / ep.shares, 0)
        - COALESCE(o.total_open / ep.shares, 0)            AS breakeven_next,

    COALESCE(r.total_realized / ep.shares, 0)              AS cc_compression_per_share,
    COALESCE(o.total_open / ep.shares, 0)                  AS cc_open_per_share

FROM equity_positions ep
LEFT JOIN cc_realized r  ON r.equity_position_id = ep.id
LEFT JOIN cc_open o      ON o.equity_position_id = ep.id;


-- -----------------------------------------------------------------------------
-- v_bucket_utilization
-- Current capital deployment vs. allocation target per account + bucket.
-- Options and shares aggregated in separate CTEs before joining — prevents
-- row multiplication from a flat join across both tables.
-- active_config pins to the most recent effective_date where is_active = 1.
-- -----------------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS v_bucket_utilization AS
WITH

-- Current active bucket config: one row per account+bucket
active_config AS (
    SELECT
        account_id,
        bucket_id,
        bucket_size,
        effective_date
    FROM bucket_config bc1
    WHERE is_active = 1
      AND effective_date = (
          SELECT MAX(bc2.effective_date)
          FROM   bucket_config bc2
          WHERE  bc2.account_id = bc1.account_id
            AND  bc2.bucket_id  = bc1.bucket_id
            AND  bc2.is_active  = 1
      )
),

-- Options collateral deployed: aggregated by account + bucket independently
options_deployed AS (
    SELECT
        account_id,
        bucket_id,
        COALESCE(SUM(collateral_total), 0)                  AS deployed_options
    FROM option_legs
    WHERE status = 'OPEN'
    GROUP BY account_id, bucket_id
),

-- Share capital deployed: aggregated by account + bucket independently
shares_deployed AS (
    SELECT
        account_id,
        bucket_id,
        COALESCE(SUM(capital_deployed), 0)                  AS deployed_shares
    FROM share_blocks
    WHERE status = 'OPEN'
    GROUP BY account_id, bucket_id
)

SELECT
    ac.account_id,
    ac.bucket_id,
    ac.bucket_size,
    ac.effective_date,

    COALESCE(od.deployed_options, 0)                        AS deployed_options,
    COALESCE(sd.deployed_shares, 0)                         AS deployed_shares,
    COALESCE(od.deployed_options, 0)
        + COALESCE(sd.deployed_shares, 0)                   AS deployed_total,
    ac.bucket_size
        - COALESCE(od.deployed_options, 0)
        - COALESCE(sd.deployed_shares, 0)                   AS available,

    ROUND(
        (COALESCE(od.deployed_options, 0)
            + COALESCE(sd.deployed_shares, 0)) * 100.0
        / ac.bucket_size, 2
    )                                                       AS utilization_pct

FROM active_config ac
LEFT JOIN options_deployed od
    ON  od.account_id = ac.account_id
    AND od.bucket_id  = ac.bucket_id
LEFT JOIN shares_deployed sd
    ON  sd.account_id = ac.account_id
    AND sd.bucket_id  = ac.bucket_id;


-- -----------------------------------------------------------------------------
-- v_chain_summary
-- Full lifecycle view of any chain: premium totals and share exit P/L.
-- Premium and share P/L aggregated in separate CTEs before joining chains —
-- prevents row multiplication from a flat join across option_legs + share_exits.
-- -----------------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS v_chain_summary AS
WITH

-- All premium aggregates by chain — independent of share tables
chain_premiums AS (
    SELECT
        chain_id,
        COALESCE(SUM(CASE WHEN premium_status = 'OPEN_CREDIT'
            THEN premium_net ELSE 0 END), 0)                AS open_credit,
        COALESCE(SUM(CASE WHEN premium_status = 'REALIZED'
            THEN realized_amount ELSE 0 END), 0)            AS total_realized_premium,
        COALESCE(SUM(CASE WHEN premium_status = 'COMPRESSED'
            THEN premium_net ELSE 0 END), 0)                AS total_compressed_premium,
        COALESCE(SUM(CASE WHEN premium_status IN ('REALIZED', 'COMPRESSED')
            THEN realized_amount ELSE 0 END), 0)            AS total_closed_premium
    FROM option_legs
    GROUP BY chain_id
),

-- All share exit P/L by chain — independent of option tables
chain_share_pl AS (
    SELECT
        sb.chain_id,
        COALESCE(SUM(se.realized_pl_gross), 0)              AS share_pl_gross,
        COALESCE(SUM(se.realized_pl_net), 0)                AS share_pl_net
    FROM share_exits se
    JOIN share_blocks sb ON sb.id = se.share_block_id
    GROUP BY sb.chain_id
)

SELECT
    c.id                                                    AS chain_id,
    c.alias,
    c.ticker,
    c.account_id,
    c.bucket_id,
    sd.name                                                 AS strategy,
    c.reporting_method,
    c.status,
    c.opened_date,
    c.closed_date,

    COALESCE(cp.open_credit, 0)                            AS open_credit,
    COALESCE(cp.total_realized_premium, 0)                 AS total_realized_premium,
    COALESCE(cp.total_compressed_premium, 0)               AS total_compressed_premium,

    COALESCE(sp.share_pl_gross, 0)                         AS share_pl_gross,
    COALESCE(sp.share_pl_net, 0)                           AS share_pl_net,

    -- Total cycle P/L: all closed premium + share exit gross
    COALESCE(cp.total_closed_premium, 0)
        + COALESCE(sp.share_pl_gross, 0)                   AS total_cycle_pl

FROM chains c
JOIN strategy_definitions sd ON sd.id = c.strategy_definition_id
LEFT JOIN chain_premiums cp  ON cp.chain_id = c.id
LEFT JOIN chain_share_pl sp  ON sp.chain_id = c.id;


-- -----------------------------------------------------------------------------
-- v_weekly_income
-- Weekly income summary: closed premium + share exit P/L by year/week/account/bucket.
-- Aggregated in separate CTEs before joining — prevents row multiplication.
-- FULL OUTER JOIN ensures weeks with only share exits or only premium income
-- both appear. Requires SQLite >= 3.39.0 (2022-07-21).
-- -----------------------------------------------------------------------------
CREATE VIEW IF NOT EXISTS v_weekly_income AS
WITH

-- Weekly closed premium income: one row per year/week/account/bucket
weekly_premiums AS (
    SELECT
        year,
        week,
        account_id,
        bucket_id,
        COALESCE(SUM(CASE WHEN premium_status IN ('REALIZED', 'COMPRESSED')
            THEN realized_amount ELSE 0 END), 0)            AS premium_realized
    FROM option_legs
    GROUP BY year, week, account_id, bucket_id
),

-- Weekly share exit P/L: one row per year/week/account/bucket
weekly_share_pl AS (
    SELECT
        se.year,
        se.week,
        sb.account_id,
        sb.bucket_id,
        COALESCE(SUM(se.realized_pl_gross), 0)              AS share_pl
    FROM share_exits se
    JOIN share_blocks sb ON sb.id = se.share_block_id
    GROUP BY se.year, se.week, sb.account_id, sb.bucket_id
)

SELECT
    COALESCE(wp.year,       sp.year)                        AS year,
    COALESCE(wp.week,       sp.week)                        AS week,
    COALESCE(wp.account_id, sp.account_id)                  AS account_id,
    COALESCE(wp.bucket_id,  sp.bucket_id)                   AS bucket_id,

    COALESCE(wp.premium_realized, 0)                        AS premium_realized,
    COALESCE(sp.share_pl, 0)                                AS share_pl,
    COALESCE(wp.premium_realized, 0)
        + COALESCE(sp.share_pl, 0)                         AS total_weekly_income

FROM weekly_premiums wp
FULL OUTER JOIN weekly_share_pl sp
    ON  sp.year       = wp.year
    AND sp.week       = wp.week
    AND sp.account_id = wp.account_id
    AND sp.bucket_id  = wp.bucket_id;
