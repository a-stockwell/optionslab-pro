# Yield Engine — Database Schema
*Status: Draft v0.7 — In Progress*
*Last Updated: 2026-05-06*

---

## Design Principles

1. **Single source of truth** — every premium is captured once and has one authoritative status at all times. No double-counting by design.
2. **Constraints at the data layer, triggers for cross-table rules** — simple accounting constraints (valid status values, field ranges) are enforced via CHECK constraints. Cross-table rules (e.g. COMPRESSED invalid for spread chains) are enforced via INSERT/UPDATE triggers. Application logic is a secondary safety net, not the primary guard.
3. **Chain as the unit of a trade** — the chain_id groups every leg, share block, and exit event across the full lifecycle of a position.
4. **Reporting as a view** — weekly income, breakeven, utilization, and P/L are derived from the transaction tables. They are not stored redundantly. Breakeven is always computed live from `option_legs` — it is never stored on `share_blocks` and therefore can never be stale.
5. **Extensible by configuration** — new strategy types are added as rows in `strategy_definitions`, not as schema changes.
6. **Money as integer cents** — all currency values are stored as INTEGER (cents) to avoid floating point drift in reconciliation. Display layer converts to dollars. e.g. $10,500.00 → 1050000.

---

## Layer 1 — Configuration & Reference Tables

These tables define the structure of the system. They change rarely.

---

### `accounts`
One row per trading account.

```sql
CREATE TABLE accounts (
    id              TEXT PRIMARY KEY,       -- e.g. 'MGN', 'ROTH', 'CASH'
    name            TEXT NOT NULL,          -- e.g. 'Margin Account'
    broker          TEXT,                   -- e.g. 'Schwab'
    account_number  TEXT,                   -- masked, e.g. '****1234'
    is_active       INTEGER DEFAULT 1,      -- boolean
    notes           TEXT
);
```

**Seed data:**
- MGN — Margin Account
- ROTH — Roth IRA
- CASH — Cash Account

---

### `buckets`
Reference table for bucket identifiers. Intentionally minimal — config lives in `bucket_config`.

```sql
CREATE TABLE buckets (
    id          TEXT PRIMARY KEY,   -- 'A', 'B', 'C', 'Legacy', 'ODTE'
    sort_order  INTEGER             -- for display ordering
);
```

**Seed data:** A, B, C, Legacy, ODTE

---

### `bucket_config`
One row per account + bucket + effective_date combination. This is where capital allocation targets live.

```sql
CREATE TABLE bucket_config (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    account_id      TEXT NOT NULL REFERENCES accounts(id),
    bucket_id       TEXT NOT NULL REFERENCES buckets(id),
    bucket_size     INTEGER NOT NULL,       -- in cents, e.g. 1050000 = $10,500.00
    is_active       INTEGER DEFAULT 1,
    effective_date  TEXT NOT NULL,          -- ISO date, required — drives historical reporting
    notes           TEXT,
    UNIQUE (account_id, bucket_id, effective_date)
);
```

**Notes:**
- `bucket_size` is stored in integer cents. Display layer divides by 100.
- `effective_date` is required (NOT NULL). Every bucket size change creates a new row — the old row is not deleted, preserving history.
- Only one row per account+bucket should have `is_active = 1` at any time. A trigger enforces this on insert/update.
- `v_bucket_utilization` selects the row where `effective_date <= report_date` and `is_active = 1`. For current utilization, report_date = today.

---

### `strategy_definitions`
One row per strategy type. Adding a new strategy never requires a schema change.

```sql
CREATE TABLE strategy_definitions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    name                TEXT NOT NULL UNIQUE,   -- 'WHEEL', 'BEAR_CALL_SPREAD', 'STRANGLE', etc.
    category            TEXT NOT NULL,          -- 'INCOME', 'DIRECTIONAL', 'NEUTRAL', 'HEDGE'
    leg_count           INTEGER NOT NULL,        -- expected number of legs
    allows_assignment   INTEGER NOT NULL,        -- boolean: can legs result in share acquisition?
    allows_compression  INTEGER NOT NULL,        -- boolean: can premiums compress cost basis?
    max_loss_defined    INTEGER NOT NULL,        -- boolean: is max loss capped (spreads, iron fly)?
    notes               TEXT
);
```

**Seed data:**

| name | category | legs | assignment | compression | max_loss_defined |
|---|---|---|---|---|---|
| WHEEL | INCOME | 1+ | 1 | 1 | 0 |
| COVERED_CALL | INCOME | 1 | 0 | 0 | 0 |
| CASH_SECURED_PUT | INCOME | 1 | 1 | 1 | 0 |
| BEAR_CALL_SPREAD | DIRECTIONAL | 2 | 0 | 0 | 1 |
| BULL_PUT_SPREAD | DIRECTIONAL | 2 | 0 | 0 | 1 |
| IRON_CONDOR | NEUTRAL | 4 | 0 | 0 | 1 |
| IRON_BUTTERFLY | NEUTRAL | 4 | 0 | 0 | 1 |
| STRANGLE | NEUTRAL | 2 | 1 | 0 | 0 |
| STRADDLE | NEUTRAL | 2 | 1 | 0 | 0 |
| DIAGONAL_PUT | INCOME | 2 | 1 | 1 | 0 |
| DIAGONAL_CALL | INCOME | 2 | 0 | 0 | 0 |
| COVERED_CALL_EQUITY | INCOME | 1 | 1 | 1 | 0 |

---

## Layer 2 — Chain & Position Tables

These tables track the lifecycle of every trade from open to close.

---

### `chains`
One row per trade cycle. The parent record for everything.

```sql
CREATE TABLE chains (
    id                      TEXT PRIMARY KEY,       -- system generated, e.g. 'chn_a1b2c3d4'
                                                    -- never constructed from trade data
                                                    -- stable forever regardless of convention changes
    alias                   TEXT UNIQUE,            -- human-readable label, e.g. 'm-te-7-0311'
                                                    -- auto-constructed at insert, can be updated freely
                                                    -- no foreign keys reference this field
    ticker                  TEXT NOT NULL,
    account_id              TEXT NOT NULL REFERENCES accounts(id),
    bucket_id               TEXT NOT NULL REFERENCES buckets(id),
    strategy_definition_id  INTEGER NOT NULL REFERENCES strategy_definitions(id),
    reporting_method        TEXT NOT NULL
                            CHECK (reporting_method IN ('YIELD_ENGINE', 'FULL_CHAIN')),
    status                  TEXT NOT NULL DEFAULT 'OPEN'
                            CHECK (status IN ('OPEN', 'CLOSED', 'PARTIALLY_CLOSED')),
    opened_date             TEXT NOT NULL,          -- ISO date
    closed_date             TEXT,                   -- ISO date, null if still open
    notes                   TEXT
);
```

**ID generation:**
System `id` is generated at insert time using a short prefixed random token:
```python
import secrets

def generate_chain_id():
    return f"chn_{secrets.token_hex(4)}"   # e.g. 'chn_a1b2c3d4'
```

**Alias construction:**
`alias` is auto-constructed from trade data at insert time but is purely cosmetic — no part of the system depends on it for lookups or relationships:
```python
def generate_chain_alias(account_id, ticker, strike, open_date, db_conn, is_equity=False):
    account_code = account_id.lower()[0]            # 'm', 'r', 'c'
    ticker_code  = ticker.lower()                   # 'te', 'soxl', 'sofi'
    date_code    = open_date.strftime('%m%d')       # '0311'

    if is_equity:
        base = f"{account_code}-{ticker_code}-eq-{date_code}"
    else:
        strike_code = str(int(strike))              # '7', '56'
        base = f"{account_code}-{ticker_code}-{strike_code}-{date_code}"

    # Collision guard on alias only
    existing = db_conn.execute(
        "SELECT alias FROM chains WHERE alias LIKE ?", (f"{base}%",)
    ).fetchall()
    if not existing:
        return base
    return f"{base}-{len(existing) + 1}"
```

**Notes:**
- All foreign keys in `option_legs`, `share_blocks`, `share_exits`, and `equity_exits` reference `chains.id` — never `chains.alias`.
- `alias` can be changed at any time without cascading updates. It is a display label only.
- `reporting_method` at the chain level means wheels and spreads can have different accounting treatment simultaneously.
- **Migration:** Historical chain_ids (e.g. `m-te-7-0311`) become the `alias` on import. New system `id` values are generated for each. One-time mapping, no data loss.

---

### `option_legs`
One row per discrete leg event. Replaces `options_log`.

```sql
CREATE TABLE option_legs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    chain_id            TEXT NOT NULL REFERENCES chains(id),
    account_id          TEXT NOT NULL REFERENCES accounts(id),
    bucket_id           TEXT NOT NULL REFERENCES buckets(id),

    -- Leg identity
    leg_role            TEXT NOT NULL
                        CHECK (leg_role IN ('SHORT', 'LONG')),
    option_type         TEXT NOT NULL
                        CHECK (option_type IN ('PUT', 'CALL')),
    strike              INTEGER NOT NULL,              -- in cents, e.g. 5600 = $56.00
    expiration_date     TEXT NOT NULL,                 -- ISO date

    -- Lifecycle
    status              TEXT NOT NULL
                        CHECK (status IN (
                            'OPEN', 'CLOSED', 'EXPIRED',
                            'ASSIGNED', 'ROLLED', 'CANCELLED'
                        )),
    date_in             TEXT NOT NULL,                 -- ISO date
    date_out            TEXT,                          -- ISO date, null if open
    dte_at_open         INTEGER,
    dit                 INTEGER,                       -- days in trade at close

    -- Premium (all in cents)
    contracts           INTEGER NOT NULL,
    premium_raw         INTEGER NOT NULL,              -- per share in cents, negative = debit
    premium_net         INTEGER,                       -- total: premium_raw * contracts * 100
    credit_debit        TEXT NOT NULL
                        CHECK (credit_debit IN ('CREDIT', 'DEBIT')),

    -- Premium accounting
    premium_status      TEXT NOT NULL DEFAULT 'OPEN_CREDIT'
                        CHECK (premium_status IN (
                            'OPEN_CREDIT', 'REALIZED',
                            'PARTIALLY_REALIZED', 'COMPRESSED', 'LOSS'
                        )),
    realized_date       TEXT,                          -- ISO date
    realized_amount     INTEGER,                       -- in cents, actual amount locked in at close

    -- Collateral / risk (in cents)
    collateral_per_contract INTEGER,                   -- strike * 100 for CSP, width * 100 for spreads
    collateral_total    INTEGER,                       -- collateral_per_contract * contracts

    -- Performance (kept as REAL — ratios, not currency)
    roc_pct             REAL,                          -- return on collateral %
    roc_annual_pct      REAL,                          -- annualized

    -- Lot linkage (for covered calls — links leg to the specific lot it covers)
    share_block_id      INTEGER REFERENCES share_blocks(id),
                                                       -- populated for CC legs against assigned shares
                                                       -- null for all other leg types
    equity_position_id  INTEGER REFERENCES equity_positions(id),
                                                       -- populated for CC legs against equity positions
                                                       -- null for all other leg types

    -- Roll lineage
    rolled_from_leg_id  INTEGER REFERENCES option_legs(id),
                                                       -- null if original leg
                                                       -- points to prior closing leg if opened as part of a roll

    -- Meta
    year                INTEGER,                       -- ISO year (from date_in isocalendar)
    week                INTEGER,                       -- ISO week (from date_in isocalendar)
    notes               TEXT
);
```

**Business rules:**
- `account_id` and `bucket_id` must match the parent chain's values. Enforced by INSERT trigger — prevents drift between leg and chain records.
- If the parent chain's `strategy_definitions.allows_compression = 0`, then `premium_status = 'COMPRESSED'` is invalid. Enforced by INSERT/UPDATE trigger on `premium_status`.
- Only one of `share_block_id` or `equity_position_id` may be populated on a given leg. Enforced by CHECK constraint:
  ```sql
  CHECK (NOT (share_block_id IS NOT NULL AND equity_position_id IS NOT NULL))
  ```
- A roll produces exactly **two rows** under the same `chain_id`: the closing leg (`status = 'ROLLED'`) and the new opening leg (`status = 'OPEN'`, `rolled_from_leg_id` pointing to the closing leg).
- The closing leg of a roll captures the debit paid to close as a negative `realized_amount`, reducing total chain premium. The new leg opens fresh `OPEN_CREDIT`. Net roll cost flows through `v_chain_summary` automatically.

---

### `share_blocks`
One row per 100-share block acquired through assignment or purchase to wheel.

```sql
CREATE TABLE share_blocks (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    chain_id            TEXT NOT NULL REFERENCES chains(id),
    account_id          TEXT NOT NULL REFERENCES accounts(id),
    bucket_id           TEXT NOT NULL REFERENCES buckets(id),
    block_number        INTEGER NOT NULL,               -- 1, 2, 3... per chain
    shares              INTEGER NOT NULL DEFAULT 100,
    acquisition_type    TEXT NOT NULL
                        CHECK (acquisition_type IN ('ASSIGNED', 'PURCHASED')),
    assigned_strike     INTEGER NOT NULL,               -- in cents, cost basis under Framework 2
    acquisition_date    TEXT NOT NULL,                  -- ISO date
    status              TEXT NOT NULL DEFAULT 'OPEN'
                        CHECK (status IN ('OPEN', 'CALLED_AWAY', 'SOLD')),

    -- Reporting
    capital_deployed    INTEGER NOT NULL,               -- in cents: assigned_strike * shares
    year                INTEGER,                        -- ISO year
    week                INTEGER,                        -- ISO week
    notes               TEXT,

    UNIQUE (chain_id, block_number)
);
```

**Notes:**
- `breakeven_original`, `breakeven_current`, and `breakeven_next` are **not stored here**. They are always computed via `v_share_breakeven` from `option_legs`. See Layer 4.
- `capital_deployed` is stored because it represents actual cash committed at acquisition time and should not change retroactively.
- `account_id` and `bucket_id` must match the parent chain. Enforced by INSERT trigger.

---

### `share_exits`
One row per share block exit event. Handles both called-away and manual sale scenarios.

```sql
CREATE TABLE share_exits (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    share_block_id      INTEGER NOT NULL REFERENCES share_blocks(id),
    chain_id            TEXT NOT NULL REFERENCES chains(id),
    exit_type           TEXT NOT NULL
                        CHECK (exit_type IN ('CALLED_AWAY', 'SOLD')),
    exit_price          INTEGER NOT NULL,               -- in cents
    exit_date           TEXT NOT NULL,                  -- ISO date
    shares_exited       INTEGER NOT NULL,

    -- P/L (in cents)
    -- gross: (exit_price - assigned_strike) * shares
    -- net: gross + total_cc_premium_harvested (FULL_CHAIN reporting only)
    realized_pl_gross   INTEGER NOT NULL,
    realized_pl_net     INTEGER,                        -- populated when chain closes under FULL_CHAIN

    -- Linked option leg if called away via CC assignment
    option_leg_id       INTEGER REFERENCES option_legs(id),

    year                INTEGER,                        -- ISO year
    week                INTEGER,                        -- ISO week
    notes               TEXT
);
```

---

## Layer 3 — Equity Tables

For discretionary share purchases outside the wheel context.

---

### `equity_positions`
One row per discretionary share purchase. Lives outside the bucket system by default — these are long-term capital allocations, not yield engine deployments.

```sql
CREATE TABLE equity_positions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker              TEXT NOT NULL,
    account_id          TEXT NOT NULL REFERENCES accounts(id),
    bucket_id           TEXT REFERENCES buckets(id),       -- NULL for long-term holds outside bucket system
    shares              INTEGER NOT NULL,
    purchase_price      INTEGER NOT NULL,                  -- in cents, e.g. 1200 = $12.00
    purchase_date       TEXT NOT NULL,                     -- ISO date
    capital_deployed    INTEGER NOT NULL,                  -- in cents: purchase_price * shares
    purpose             TEXT NOT NULL DEFAULT 'INCOME'
                        CHECK (purpose IN (
                            'INCOME',       -- active weekly revenue generator, CC premiums hit weekly_income
                            'COMPRESSION',  -- long-term hold, CC premiums compress cost basis toward zero
                            'HOLD'          -- owned but no CC activity yet
                        )),
    status              TEXT NOT NULL DEFAULT 'OPEN'
                        CHECK (status IN ('OPEN', 'CLOSED', 'PARTIALLY_CLOSED')),
    notes               TEXT
);
```

**Notes:**
- `bucket_id = NULL` means the position is outside the yield engine bucket system entirely. Its capital does not count against bucket utilization.
- CC chains written against equity positions use `strategy_definition = COVERED_CALL_EQUITY` and carry their own `bucket_id` for income tracking. The shares and the options are tracked independently.
- `reporting_method` on the CC chain should align with `purpose` — `INCOME` pairs with `YIELD_ENGINE`, `COMPRESSION` pairs with `FULL_CHAIN`. `HOLD` has no active CC chain.
- `purpose` is the user-declared intent for the block. It drives how the position surfaces in dashboard views and reports — two blocks of the same ticker can be held simultaneously with different purposes and different accounting treatment.

---

### `equity_exits`
One row per equity position exit — whether sold manually or called away via CC assignment.

```sql
CREATE TABLE equity_exits (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    equity_position_id      INTEGER NOT NULL REFERENCES equity_positions(id),
    exit_price              INTEGER NOT NULL,              -- in cents
    exit_date               TEXT NOT NULL,                 -- ISO date
    shares_exited           INTEGER NOT NULL,
    realized_pl_gross       INTEGER NOT NULL,              -- in cents: (exit_price - purchase_price) * shares_exited
    realized_pl_net         INTEGER,                       -- in cents: gross + total CC premiums compressed (FULL_CHAIN only)
    option_leg_id           INTEGER REFERENCES option_legs(id),
                                                           -- populated if exit triggered by CC assignment
    year                    INTEGER,                       -- ISO year
    week                    INTEGER,                       -- ISO week
    notes                   TEXT
);
```

---

## Layer 4 — Computed Views (not stored tables)

These replace manually maintained columns in the spreadsheet. Always current, never stale. All views use CTE-based aggregation to prevent join multiplication — options and shares are always aggregated independently before being joined together.

---

### `v_share_breakeven`
Computes original, current, and next breakeven for every share block from `option_legs`. Joins via `share_block_id` on the CC leg — not chain + ticker — eliminating ambiguity when multiple blocks exist for the same ticker.

**Breakeven logic:**
- **Original** — `assigned_strike` minus the per-share CSP premium that triggered assignment
- **Current** — original breakeven minus cumulative per-share premium from all closed CC legs linked to this specific block
- **Next** — current minus per-share premium from any currently open CC leg linked to this block

```sql
CREATE VIEW v_share_breakeven AS
WITH

-- CSP that caused assignment — joins by chain, filtered to PUT ASSIGNED COMPRESSED
csp_compression AS (
    SELECT
        sb.id                                               AS share_block_id,
        COALESCE(SUM(ol.premium_net) / sb.shares, 0)       AS csp_premium_per_share
    FROM share_blocks sb
    JOIN option_legs ol
        ON ol.chain_id = sb.chain_id
        AND ol.option_type = 'PUT'
        AND ol.leg_role = 'SHORT'
        AND ol.status = 'ASSIGNED'
        AND ol.premium_status = 'COMPRESSED'
    GROUP BY sb.id
),

-- Closed CC legs explicitly linked to this block via share_block_id
cc_realized AS (
    SELECT
        ol.share_block_id,
        SUM(ol.realized_amount)                             AS total_realized
    FROM option_legs ol
    WHERE ol.option_type = 'CALL'
        AND ol.leg_role = 'SHORT'
        AND ol.status IN ('EXPIRED', 'ASSIGNED')
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
    WHERE ol.option_type = 'CALL'
        AND ol.leg_role = 'SHORT'
        AND ol.status = 'OPEN'
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

    -- All values in cents
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
```

**Notes:**
- CC legs join via `ol.share_block_id` — explicit lot linkage added in v0.7. Eliminates the ambiguity of multiple blocks for the same ticker in the same account.
- The AMDL diagonal long put debit has a negative `premium_net`, so it reduces rather than adds to compression — correct by construction.
- All values in cents. Display layer divides by 100.

---

### `v_equity_breakeven`
Parallel to `v_share_breakeven` but sourced from `equity_positions`. Joins CC legs via `ol.equity_position_id` — explicit lot linkage prevents cross-contamination between two blocks of the same ticker with different purposes.

```sql
CREATE VIEW v_equity_breakeven AS
WITH

-- Closed CC legs explicitly linked to this equity position
cc_realized AS (
    SELECT
        ol.equity_position_id,
        SUM(ol.realized_amount)                             AS total_realized
    FROM option_legs ol
    WHERE ol.option_type = 'CALL'
        AND ol.leg_role = 'SHORT'
        AND ol.status IN ('EXPIRED', 'ASSIGNED')
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
    WHERE ol.option_type = 'CALL'
        AND ol.leg_role = 'SHORT'
        AND ol.status = 'OPEN'
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

    -- All values in cents
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
```

**Notes:**
- Only meaningful under `FULL_CHAIN` reporting. Under `YIELD_ENGINE`, `breakeven_current` will equal `breakeven_original` since premiums are recognized as income and not compressed.
- The ticker + account_id join used in v0.6 is replaced by explicit `equity_position_id` linkage — the two-SOFI-lot scenario is now correctly isolated.

---

### `v_bucket_utilization`
Replaces bucket deployment tracking on the dashboard. Options and shares aggregated in separate CTEs before joining — eliminates row multiplication from the prior flat join approach.

```sql
CREATE VIEW v_bucket_utilization AS
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
            FROM bucket_config bc2
            WHERE bc2.account_id = bc1.account_id
                AND bc2.bucket_id = bc1.bucket_id
                AND bc2.is_active = 1
        )
),

-- Options collateral: aggregated by account + bucket independently
options_deployed AS (
    SELECT
        account_id,
        bucket_id,
        COALESCE(SUM(collateral_total), 0)                  AS deployed_options
    FROM option_legs
    WHERE status = 'OPEN'
    GROUP BY account_id, bucket_id
),

-- Share capital: aggregated by account + bucket independently
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
    ON od.account_id = ac.account_id
    AND od.bucket_id = ac.bucket_id
LEFT JOIN shares_deployed sd
    ON sd.account_id = ac.account_id
    AND sd.bucket_id = ac.bucket_id;
```

**Notes:**
- `active_config` CTE selects the most recent `effective_date` row per account+bucket where `is_active = 1`. Eliminates duplication if multiple active rows exist and correctly pins utilization to the current bucket size.
- All values in cents. Display layer divides by 100.
- To query historical utilization at a specific date, replace `effective_date = MAX(...)` with `effective_date <= :report_date`.

---

### `v_chain_summary`
Full lifecycle view of any chain. Premium and share P/L aggregated in separate CTEs before joining chains — eliminates row multiplication from the prior flat join approach.

```sql
CREATE VIEW v_chain_summary AS
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

    -- Premium totals (in cents)
    COALESCE(cp.open_credit, 0)                            AS open_credit,
    COALESCE(cp.total_realized_premium, 0)                 AS total_realized_premium,
    COALESCE(cp.total_compressed_premium, 0)               AS total_compressed_premium,

    -- Share exit P/L (in cents)
    COALESCE(sp.share_pl_gross, 0)                         AS share_pl_gross,
    COALESCE(sp.share_pl_net, 0)                           AS share_pl_net,

    -- Total cycle P/L: all closed premium + share exit gross (in cents)
    COALESCE(cp.total_closed_premium, 0)
        + COALESCE(sp.share_pl_gross, 0)                   AS total_cycle_pl

FROM chains c
JOIN strategy_definitions sd ON sd.id = c.strategy_definition_id
LEFT JOIN chain_premiums cp  ON cp.chain_id = c.id
LEFT JOIN chain_share_pl sp  ON sp.chain_id = c.id;
```

---

### `v_weekly_income`
Replaces `weekly_revenue`. Premium income and share P/L aggregated in separate CTEs by year + week + account + bucket before joining — eliminates row multiplication from the prior flat join approach.

```sql
CREATE VIEW v_weekly_income AS
WITH

-- Weekly premium income: one row per year/week/account/bucket
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

-- Weekly share exit P/L: one row per year/week/account
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
    COALESCE(wp.year, sp.year)                             AS year,
    COALESCE(wp.week, sp.week)                             AS week,
    COALESCE(wp.account_id, sp.account_id)                 AS account_id,
    COALESCE(wp.bucket_id, sp.bucket_id)                   AS bucket_id,

    COALESCE(wp.premium_realized, 0)                       AS premium_realized,
    COALESCE(sp.share_pl, 0)                               AS share_pl,
    COALESCE(wp.premium_realized, 0)
        + COALESCE(sp.share_pl, 0)                         AS total_weekly_income

FROM weekly_premiums wp
FULL OUTER JOIN weekly_share_pl sp
    ON  sp.year       = wp.year
    AND sp.week       = wp.week
    AND sp.account_id = wp.account_id
    AND sp.bucket_id  = wp.bucket_id;
```

**Notes:**
- `FULL OUTER JOIN` ensures weeks with only share exits (no closed premiums) and weeks with only premium income (no share exits) both appear correctly. SQLite supports FULL OUTER JOIN from version 3.39.0 (2022-07-21).
- All values in cents. Display layer divides by 100.

---

## Open Questions

1. ~~**Breakeven on share_blocks**~~ — ✅ **Resolved:** Always computed via `v_share_breakeven` from `option_legs`. Never stored on `share_blocks`.
2. ~~**Week numbering**~~ — ✅ **Resolved:** ISO weeks throughout. Python ingestion uses `date.isocalendar().week` and `date.isocalendar().year`. Note the ISO year-boundary edge case — a trade on Dec 29 may belong to ISO week 1 of the following year. Ingestion logic must use ISO year, not calendar year.
3. ~~**Multi-leg rolls**~~ — ✅ **Resolved:** Rolls stay on the same `chain_id`. A roll produces two `option_legs` rows — the closing leg (`status = 'ROLLED'`) and the new opening leg (`rolled_from_leg_id` pointing back to the closer). Full roll lineage is queryable within the chain.
4. **Schwab API integration** — which fields in `option_legs` need to map to Schwab order/position identifiers for eventual automated ingestion? *(Holding — revisit when building ingestion layer.)*
5. ~~**Equity positions and buckets**~~ — ✅ **Resolved:** Discretionary equity purchases (`equity_positions`) live outside the bucket system by default (`bucket_id = NULL`). CC chains written against those shares (`COVERED_CALL_EQUITY`) are inside the bucket system for income tracking. `reporting_method` on the CC chain controls whether premiums are weekly income (`YIELD_ENGINE`) or cost basis compression (`FULL_CHAIN`). Breakeven for equity positions computed via `v_equity_breakeven`.

---

## Migration Notes

Historical data from `yield-engine` Google Sheet maps as follows:

| Sheet | → | Table |
|---|---|---|
| options_log | → | chains + option_legs |
| positions (options rows) | → | option_legs |
| positions (share rows) | → | share_blocks + share_exits |
| weekly_revenue | → | v_weekly_income (computed, not migrated) |
| dashboard bucket data | → | v_bucket_utilization (computed, not migrated) |

**Chain ID migration:**
Existing chain_ids (e.g. `m-te-7-0311`) migrate as `alias` values on the `chains` table. New system `id` values (`chn_xxxxxxxx`) are generated for each chain at import time. All `option_legs` and `share_blocks` rows are then re-linked to the new system `id`. The alias is preserved exactly as-is — no reformatting required.

---

*Next: Question 4 (Schwab API field mapping) held pending ingestion layer build. Schema reviewed by Codex (v0.7) — all eight findings addressed. Ready to begin migration script from Google Sheets export.*
