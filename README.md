# Optionslab-Pro

A personal options income tracking and management system built around systematic, ruleset-driven strategies. Designed to replace a patchwork of spreadsheets with a single source of truth — enforced at the data layer, not by convention.

---

## What This Is

optionslab-pro is a structured options income system tracking cash-secured puts (CSPs), covered calls (CCs), vertical credit spreads, and the wheel strategy across two accounts (MGN margin and ROTH IRA). Capital is deployed via a **staggered three-bucket system** to smooth weekly revenue and avoid over-concentration at any single entry point.

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
A normalized relational schema replacing the spreadsheet's double-tracking, status-flag accounting, and manually maintained breakevens. See [`optionslab-pro-schema.md`](./optionslab-pro-schema.md) for the full spec.

**Core design principles:**
1. **Single source of truth** — every premium captured once, one authoritative status at all times
2. **Enforced at the data layer** — accounting rules are schema constraints and triggers, not application conventions
3. **Chain as the unit of a trade** — `chain_id` groups every leg, share block, and exit event across the full lifecycle
4. **Reporting as a view** — weekly income, breakeven, utilization, and P/L are derived, never stored redundantly
5. **Extensible by configuration** — new strategy types added as rows in `strategy_definitions`, not schema changes
6. **Money as integer cents** — all currency stored as INTEGER cents; display layer converts to dollars

### Planned Integration: Schwab API
- `schwab-py` library available, developer account active
- Phase 2 data source replacing WiseSheets dependency
- Options chain data → Python pipeline → screener and position monitoring

---

## Repository Structure

```
optionslab-pro/
├── README.md                        # this file
├── optionslab-pro-schema.md         # full database schema spec (v0.7)
├── pyproject.toml                   # project config, pytest settings
├── requirements.txt                 # pytest, pytest-cov
├── db/
│   ├── __init__.py
│   ├── init_db.py                   # init_db(), get_connection(), generate_chain_id/alias()
│   ├── schema/
│   │   ├── layer1_reference.sql     # accounts, buckets, bucket_config, strategy_definitions
│   │   ├── layer2_chains.sql        # chains, equity_positions, option_legs, share_blocks, share_exits
│   │   ├── layer3_equity.sql        # equity_exits
│   │   ├── triggers.sql             # 6 triggers enforcing cross-table business rules
│   │   └── views.sql                # 5 computed views (breakeven, utilization, income, P/L)
│   └── seed/
│       └── seed_data.sql            # reference data: 3 accounts, 5 buckets, 12 strategies
├── tests/
│   ├── conftest.py                  # in-memory DB fixtures
│   ├── test_schema.py               # table structure, CHECK constraints, FK enforcement
│   ├── test_triggers.py             # business rule trigger enforcement
│   └── test_views.py                # computed view correctness with realistic data
├── scripts/
│   └── migrate_from_sheets.py       # (planned) one-time historical data migration
└── data/
    └── optionslab.db                # SQLite database (gitignored)
```

> **Note:** `data/optionslab.db` is gitignored. Schema files, seed data, and Python source are tracked; the database file itself is not.

---

## Schema Overview

### Layer 1 — Reference Tables
| Table | Purpose |
|---|---|
| `accounts` | MGN, ROTH, CASH account definitions |
| `buckets` | A, B, C, Legacy, ODTE bucket identifiers |
| `bucket_config` | Capital allocation targets per account + bucket; historical rows preserved on change |
| `strategy_definitions` | Strategy type registry — new strategies added as rows, never as schema changes |

### Layer 2 — Chain & Position Tables
| Table | Purpose |
|---|---|
| `chains` | One row per trade cycle; parent record for everything. System `id` (`chn_xxxxxxxx`) + human `alias` |
| `option_legs` | Every option contract opened, closed, rolled, or assigned — linked to a chain |
| `share_blocks` | Share positions acquired via assignment or direct purchase |
| `share_exits` | Share exit events — called away or sold |
| `equity_positions` | Discretionary equity purchases outside the wheel context |
| `equity_exits` | Equity exit events — manual sale or called away via CC |

### Layer 3 — Computed Views
| View | Purpose |
|---|---|
| `v_share_breakeven` | Original / current / next breakeven per share block, computed live from option_legs |
| `v_equity_breakeven` | Parallel breakeven view for discretionary equity positions |
| `v_bucket_utilization` | Capital deployed vs. allocation target per account + bucket |
| `v_chain_summary` | Full lifecycle P/L for any chain: premium totals + share exit P/L |
| `v_weekly_income` | Weekly income by year / week / account / bucket |

Full schema: [`optionslab-pro-schema.md`](./optionslab-pro-schema.md)

---

## Key Design Decisions

**Chain ID system:** System-generated `id` (e.g. `chn_a1b2c3d4`) is the stable key used for all foreign key relationships. Human-readable `alias` (e.g. `m-te-7-0311`) is auto-constructed at insert and can be updated freely without breaking anything. This decouples readability from data integrity.

**No double-counting:** The original spreadsheet counted CSP premiums as both weekly income and a reduction to cost basis. The new schema enforces single-source capture — `reporting_method` on each chain (`YIELD_ENGINE` or `FULL_CHAIN`) determines how premium flows; never both simultaneously.

**Independent lot linkage:** CC legs link to share blocks and equity positions via explicit foreign keys (`share_block_id`, `equity_position_id`) rather than chain + ticker joins. This correctly isolates two positions in the same ticker with different accounting treatment (e.g. two SPY lots — one compressing cost basis, one generating income).

**SQLite first:** No server to run, no infrastructure overhead. The database is a single file alongside the Python scripts. Migration to PostgreSQL is straightforward from a clean schema if concurrent access or integration requires it later.

---

## Development Roadmap

- [x] Schema design — v0.7 complete
- [x] Layer 1 DDL — `accounts`, `buckets`, `bucket_config`, `strategy_definitions`
- [x] Layer 2 DDL — `chains`, `option_legs`, `share_blocks`, `share_exits`, `equity_positions`, `equity_exits`
- [x] Layer 3 DDL — `equity_exits`
- [x] Triggers — 6 triggers enforcing all cross-table business rules
- [x] Computed views — `v_share_breakeven`, `v_equity_breakeven`, `v_bucket_utilization`, `v_chain_summary`, `v_weekly_income`
- [x] Seed data — accounts, buckets, 12 strategy definitions
- [x] Python init module — `init_db()`, `get_connection()`, `generate_chain_id()`, `generate_chain_alias()`
- [x] Test suite — 59 tests, all passing
- [ ] Google Sheets export review — confirm column names and data shape before migration
- [ ] Historical data migration from Google Sheets (`scripts/migrate_from_sheets.py`)
- [ ] Validation — P/L and weekly revenue match spreadsheet figures before sheet is retired
- [ ] Schwab API integration (`schwab-py`) for live options data
- [ ] Morning market summary automation (Mac Mini cron job)
- [ ] Screener pipeline (Schwab API → pandas → ranked candidates)
- [ ] Obsidian integration for trade notes and pre-market prep

---

## Running Tests

```bash
# Activate venv first, or prefix with .venv/bin/
source .venv/bin/activate
pytest -v
```

---

## Related Projects

- **optionslab.io** — public-facing options calculator
- **Morning Market Summary** — planned Python agent running on Mac Mini M4 (headless), delivering pre-market IWM levels to Obsidian via webhook
- **Dotfiles** — [`github.com/a-stockwell/dotfiles`](https://github.com/a-stockwell/dotfiles) — terminal and tooling config shared across MBP M4 Pro and Mac Mini M4

---

## Notes on the Spreadsheet Migration

The Google Sheets workbook is the operational source of truth until migration is complete. It should be treated as the **validation dataset** — every P/L figure, breakeven, and weekly revenue number produced by the new system must match it before the sheet is retired.

Known issues in the spreadsheet that the schema corrects:
- Columns pulling double duty (Strike / Exit overloaded)
- Status flags doing relational work that foreign keys should do
- Premium double-counting (now enforced out by schema design)
- Breakeven calculations maintained manually (now derived live from transaction tables)
- New strategy types required column additions (now require only a new row in `strategy_definitions`)
