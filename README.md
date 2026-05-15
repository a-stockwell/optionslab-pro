# Yield Engine

A personal options income tracking and management system built around systematic, ruleset-driven strategies. Designed to replace a patchwork of spreadsheets with a single source of truth — enforced at the data layer, not by convention.

---

## What This Is

The Yield Engine is a structured options income system tracking cash-secured puts (CSPs), covered calls (CCs), vertical credit spreads, and the wheel strategy across two accounts (MGN margin and ROTH IRA). Capital is deployed via a **staggered three-bucket system** to smooth weekly revenue and avoid over-concentration at any single entry point.

The project began as a Google Sheets workbook (still the operational source during migration) and is now being rebuilt as a SQLite-backed Python system with the spreadsheet serving as the validation dataset.

---

## Strategy Ruleset

### The Wheel (CSP → CC)
- Core cornerstones: SOXL (primary), energy-sector ETF (TBD, secondary)
- Ticker quality: higher-quality names you'd be comfortable holding at assignment
- Assignment is the intended outcome, not something to avoid

### Vertical Credit Spreads
- Vehicle: XSP (mini S&P 500) — cash-settled, European-style, 60/40 Section 1256 tax treatment
- Entry: ~45 DTE, short leg near .30 delta
- Hard close: 21 DTE regardless of position status
- Profit targets: 80% on bull put spreads, 50% on bear call spreads
- Max risk/reward: 3:1
- Direction: informed by overall market conditions; spreads increase as wheel opportunities decrease

### Capital Allocation
- Three buckets (A, B, C) deployed on a staggered weekly schedule — one bucket per week
- ~10% of each bucket allocated to spread positions
- Tactical deployment of excess capital from prior week is permitted
- Weekly revenue target derived from corrected accounting baseline (premiums captured once; no double-counting against cost basis)

---

## Architecture

### Current State (Google Sheets + WiseSheets)
- `options_log` — transaction ledger for every leg, roll, and exit event
- `positions` — state tracker for share blocks, breakevens, and realized P/L
- WiseSheets (`WISEOPTIONS()`, `WISEPRICE()`) for live options data
- CSP screener built on WiseSheets as a stopgap pending broker API integration

### Target State (SQLite + Python)
A normalized relational schema replacing the spreadsheet's double-tracking, status-flag accounting, and manually maintained breakevens. See [`yield-engine-schema.md`](./yield-engine-schema.md) for the full spec.

**Core design principles:**
1. **Single source of truth** — every premium captured once, one authoritative status at all times
2. **Enforced at the data layer** — accounting rules are schema constraints, not application conventions
3. **Chain as the unit of a trade** — `chain_id` groups every leg, share block, and exit event across the full lifecycle
4. **Reporting as a view** — weekly income, breakeven, utilization, and P/L are derived, never stored redundantly
5. **Extensible by configuration** — new strategy types added as rows in `strategy_definitions`, not schema changes

### Planned Integration: Schwab API
- `schwab-py` library available, developer account active
- Phase 2 data source replacing WiseSheets dependency
- Options chain data → Python pipeline → screener and position monitoring

---

## Repository Structure

```
yield-engine/
├── README.md                   # this file
├── yield-engine-schema.md      # full database schema spec (v0.x)
├── schema/
│   └── create_tables.sql       # DDL for SQLite database
├── src/
│   ├── db/
│   │   └── connection.py       # database connection helpers
│   ├── models/                 # table-level CRUD helpers
│   ├── reports/                # views and derived calculations
│   └── ingestion/              # import scripts for historical Sheets data
├── data/
│   └── yield_engine.db         # SQLite database (gitignored)
└── scripts/
    └── migrate_from_sheets.py  # one-time historical data migration
```

> **Note:** `data/yield_engine.db` should be added to `.gitignore`. The schema files and Python source are tracked; the database itself is not.

---

## Schema Overview

### Layer 1 — Reference Tables
| Table | Purpose |
|---|---|
| `accounts` | MGN, ROTH, CASH account definitions |
| `buckets` | A, B, C, Legacy, ODTE bucket identifiers |
| `bucket_config` | Capital allocation targets per account + bucket combination |
| `strategy_definitions` | Strategy type registry — new strategies added here, not in schema |

### Layer 2 — Trade Tables
| Table | Purpose |
|---|---|
| `chains` | One row per trade cycle; parent record for everything. Has system-generated `id` and human-readable `alias` (e.g. `m-te-7-0311`) |
| `option_legs` | Every option contract opened or closed, linked to a chain |
| `share_blocks` | Share positions acquired via assignment or direct purchase |
| `exit_events` | All closing transactions — option buybacks, share sales, spread closes |

### Layer 3 — Derived (Views)
Breakeven, weekly revenue, bucket utilization, and P/L are computed from the transaction tables. Nothing is stored redundantly.

Full schema: [`yield-engine-schema.md`](./yield-engine-schema.md)

---

## Key Design Decisions

**Chain ID system:** System-generated `id` (e.g. `chn_a1b2c3d4`) is the stable key used for all foreign key relationships. Human-readable `alias` (e.g. `m-te-7-0311`) is auto-constructed at insert and can be updated freely without breaking anything. This decouples readability from data integrity.

**No double-counting:** The original spreadsheet counted CSP premiums as both weekly income and a reduction to cost basis. The new schema enforces single-source capture — a `reporting_method` flag on each chain determines whether premium flows to `YIELD_ENGINE` weekly income or `FULL_CHAIN` cost basis accounting, not both.

**SQLite first:** No server to run, no infrastructure overhead. The database is a single file sitting alongside the Python scripts. Migration to PostgreSQL is straightforward from a clean schema if concurrent access or optionslab.io integration requires it later.

---

## Development Roadmap

- [x] Schema design (v0.x complete, see schema doc)
- [ ] `create_tables.sql` DDL finalized
- [ ] Database connection helper (`src/db/connection.py`)
- [ ] Historical data migration from Google Sheets (`scripts/migrate_from_sheets.py`)
- [ ] Validation: P/L and weekly revenue match spreadsheet figures
- [ ] Schwab API integration (`schwab-py`) for live options data
- [ ] Morning market summary automation (Mac Mini cron job)
- [ ] Screener pipeline (Schwab API → pandas → ranked candidates)
- [ ] Obsidian integration for trade notes and pre-market prep

---

## Related Projects

- **optionslab.io** — public-facing options calculator built with Claude
- **Morning Market Summary** — planned Python agent running on Mac Mini M4 (headless), delivering pre-market IWM levels to Obsidian via webhook
- **Dotfiles** — [`github.com/a-stockwell/dotfiles`](https://github.com/a-stockwell/dotfiles) — terminal and tooling config shared across MBP M4 Pro and Mac Mini M4

---

## Notes on the Spreadsheet Migration

The Google Sheets workbook is the operational source of truth until migration is complete. It should be treated as the **validation dataset** — every P/L figure, breakeven, and weekly revenue number produced by the new system must match it before the sheet is retired.

Known issues in the spreadsheet that the schema corrects:
- Columns pulling double duty (Strike / Exit overloaded)
- Status flags doing relational work that foreign keys should do
- Premium double-counting (now enforced out by schema design)
- Breakeven calculations maintained manually (now derived from transaction tables)
- New strategy types required column additions (now require only a new row in `strategy_definitions`)
