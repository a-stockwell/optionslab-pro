"""
test_schema.py — Table structure, CHECK constraints, FK enforcement.
"""

import sqlite3
import pytest


# ---------------------------------------------------------------------------
# Tables and views exist
# ---------------------------------------------------------------------------

EXPECTED_TABLES = {
    "accounts", "buckets", "bucket_config", "strategy_definitions",
    "chains", "option_legs", "share_blocks", "share_exits",
    "equity_positions", "equity_exits",
}

EXPECTED_VIEWS = {
    "v_share_breakeven", "v_equity_breakeven", "v_bucket_utilization",
    "v_chain_summary", "v_weekly_income",
}


def test_all_tables_exist(db):
    tables = {
        r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    assert tables == EXPECTED_TABLES


def test_all_views_exist(db):
    views = {
        r[0] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='view'"
        )
    }
    assert views == EXPECTED_VIEWS


# ---------------------------------------------------------------------------
# Seed data integrity
# ---------------------------------------------------------------------------

def test_seed_accounts(db):
    rows = {r[0] for r in db.execute("SELECT id FROM accounts")}
    assert rows == {"MGN", "ROTH", "CASH"}


def test_seed_buckets(db):
    rows = db.execute("SELECT id, sort_order FROM buckets ORDER BY sort_order").fetchall()
    assert [(r[0], r[1]) for r in rows] == [
        ("A", 1), ("B", 2), ("C", 3), ("Legacy", 4), ("ODTE", 5)
    ]


def test_seed_strategy_definitions_count(db):
    count = db.execute("SELECT COUNT(*) FROM strategy_definitions").fetchone()[0]
    assert count == 12


def test_seed_strategy_flags(db):
    row = db.execute(
        "SELECT allows_compression, max_loss_defined FROM strategy_definitions WHERE name = 'WHEEL'"
    ).fetchone()
    assert row["allows_compression"] == 1
    assert row["max_loss_defined"] == 0

    row = db.execute(
        "SELECT allows_compression, max_loss_defined FROM strategy_definitions WHERE name = 'BEAR_CALL_SPREAD'"
    ).fetchone()
    assert row["allows_compression"] == 0
    assert row["max_loss_defined"] == 1


# ---------------------------------------------------------------------------
# accounts CHECK constraints
# ---------------------------------------------------------------------------

def test_accounts_is_active_rejects_invalid(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute("INSERT INTO accounts VALUES ('X', 'X', NULL, NULL, 5, NULL)")


# ---------------------------------------------------------------------------
# bucket_config CHECK constraints
# ---------------------------------------------------------------------------

def test_bucket_config_bucket_size_must_be_positive(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO bucket_config (account_id, bucket_id, bucket_size, effective_date)"
            " VALUES ('MGN', 'A', -100, '2026-01-01')"
        )


def test_bucket_config_unique_account_bucket_date(db):
    db.execute(
        "INSERT INTO bucket_config (account_id, bucket_id, bucket_size, effective_date)"
        " VALUES ('MGN', 'A', 1000000, '2026-01-01')"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO bucket_config (account_id, bucket_id, bucket_size, effective_date)"
            " VALUES ('MGN', 'A', 2000000, '2026-01-01')"
        )


# ---------------------------------------------------------------------------
# strategy_definitions CHECK constraints
# ---------------------------------------------------------------------------

def test_strategy_definitions_category_rejects_invalid(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO strategy_definitions"
            " (name, category, leg_count, allows_assignment, allows_compression, max_loss_defined)"
            " VALUES ('TEST', 'BOGUS', 1, 0, 0, 0)"
        )


# ---------------------------------------------------------------------------
# chains CHECK constraints
# ---------------------------------------------------------------------------

def _wheel_id(db):
    return db.execute(
        "SELECT id FROM strategy_definitions WHERE name = 'WHEEL'"
    ).fetchone()[0]


def test_chains_reporting_method_rejects_invalid(db):
    sid = _wheel_id(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            f"INSERT INTO chains VALUES ('chn_test', NULL, 'SPY', 'MGN', 'A', {sid},"
            " 'BOGUS', 'OPEN', '2026-01-01', NULL, NULL)"
        )


def test_chains_status_rejects_invalid(db):
    sid = _wheel_id(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            f"INSERT INTO chains VALUES ('chn_test', NULL, 'SPY', 'MGN', 'A', {sid},"
            " 'YIELD_ENGINE', 'INVALID', '2026-01-01', NULL, NULL)"
        )


def test_chains_alias_is_unique(db):
    sid = _wheel_id(db)
    db.execute(
        f"INSERT INTO chains VALUES ('chn_001', 'same-alias', 'SPY', 'MGN', 'A', {sid},"
        " 'YIELD_ENGINE', 'OPEN', '2026-01-01', NULL, NULL)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            f"INSERT INTO chains VALUES ('chn_002', 'same-alias', 'AAPL', 'MGN', 'A', {sid},"
            " 'YIELD_ENGINE', 'OPEN', '2026-01-01', NULL, NULL)"
        )


# ---------------------------------------------------------------------------
# option_legs CHECK constraints
# ---------------------------------------------------------------------------

def _open_chain(db, chain_id="chn_001", strategy="WHEEL"):
    sid = db.execute(
        "SELECT id FROM strategy_definitions WHERE name = ?", (strategy,)
    ).fetchone()[0]
    db.execute(
        f"INSERT INTO chains VALUES ('{chain_id}', NULL, 'SPY', 'MGN', 'A', {sid},"
        " 'YIELD_ENGINE', 'OPEN', '2026-01-01', NULL, NULL)"
    )
    db.commit()


def test_option_legs_leg_role_rejects_invalid(db):
    _open_chain(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO option_legs"
            " (chain_id, account_id, bucket_id, leg_role, option_type, strike,"
            "  expiration_date, status, date_in, contracts, premium_raw, credit_debit)"
            " VALUES ('chn_001', 'MGN', 'A', 'LONG_SHORT', 'PUT', 50000,"
            "  '2026-02-01', 'OPEN', '2026-01-15', 1, 100, 'CREDIT')"
        )


def test_option_legs_option_type_rejects_invalid(db):
    _open_chain(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO option_legs"
            " (chain_id, account_id, bucket_id, leg_role, option_type, strike,"
            "  expiration_date, status, date_in, contracts, premium_raw, credit_debit)"
            " VALUES ('chn_001', 'MGN', 'A', 'SHORT', 'FUTURE', 50000,"
            "  '2026-02-01', 'OPEN', '2026-01-15', 1, 100, 'CREDIT')"
        )


def test_option_legs_premium_status_rejects_invalid(db):
    _open_chain(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO option_legs"
            " (chain_id, account_id, bucket_id, leg_role, option_type, strike,"
            "  expiration_date, status, date_in, contracts, premium_raw, credit_debit, premium_status)"
            " VALUES ('chn_001', 'MGN', 'A', 'SHORT', 'PUT', 50000,"
            "  '2026-02-01', 'OPEN', '2026-01-15', 1, 100, 'CREDIT', 'BOGUS')"
        )


def test_option_legs_mutual_exclusion_both_lot_ids_rejected(db):
    _open_chain(db)
    db.execute(
        "INSERT INTO share_blocks"
        " (chain_id, account_id, bucket_id, block_number, shares, acquisition_type,"
        "  assigned_strike, acquisition_date, status, capital_deployed)"
        " VALUES ('chn_001', 'MGN', 'A', 1, 100, 'ASSIGNED', 50000, '2026-01-10', 'OPEN', 5000000)"
    )
    db.execute(
        "INSERT INTO equity_positions"
        " (ticker, account_id, shares, purchase_price, purchase_date, capital_deployed)"
        " VALUES ('SPY', 'MGN', 100, 50000, '2026-01-01', 5000000)"
    )
    db.commit()
    sb_id = db.execute("SELECT id FROM share_blocks").fetchone()[0]
    ep_id = db.execute("SELECT id FROM equity_positions").fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO option_legs"
            " (chain_id, account_id, bucket_id, leg_role, option_type, strike,"
            "  expiration_date, status, date_in, contracts, premium_raw, credit_debit,"
            "  share_block_id, equity_position_id)"
            f" VALUES ('chn_001', 'MGN', 'A', 'SHORT', 'CALL', 51000,"
            f"  '2026-02-01', 'OPEN', '2026-01-15', 1, 100, 'CREDIT', {sb_id}, {ep_id})"
        )


def test_option_legs_self_referential_fk(db):
    _open_chain(db)
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO option_legs"
            " (chain_id, account_id, bucket_id, leg_role, option_type, strike,"
            "  expiration_date, status, date_in, contracts, premium_raw, credit_debit,"
            "  rolled_from_leg_id)"
            " VALUES ('chn_001', 'MGN', 'A', 'SHORT', 'PUT', 50000,"
            "  '2026-02-01', 'OPEN', '2026-01-15', 1, 100, 'CREDIT', 99999)"
        )


# ---------------------------------------------------------------------------
# share_blocks CHECK constraints
# ---------------------------------------------------------------------------

def test_share_blocks_unique_chain_block_number(db):
    _open_chain(db)
    db.execute(
        "INSERT INTO share_blocks"
        " (chain_id, account_id, bucket_id, block_number, shares, acquisition_type,"
        "  assigned_strike, acquisition_date, status, capital_deployed)"
        " VALUES ('chn_001', 'MGN', 'A', 1, 100, 'ASSIGNED', 50000, '2026-01-10', 'OPEN', 5000000)"
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO share_blocks"
            " (chain_id, account_id, bucket_id, block_number, shares, acquisition_type,"
            "  assigned_strike, acquisition_date, status, capital_deployed)"
            " VALUES ('chn_001', 'MGN', 'A', 1, 100, 'ASSIGNED', 50000, '2026-01-20', 'OPEN', 5000000)"
        )


# ---------------------------------------------------------------------------
# equity_positions CHECK constraints
# ---------------------------------------------------------------------------

def test_equity_positions_purpose_rejects_invalid(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO equity_positions"
            " (ticker, account_id, shares, purchase_price, purchase_date, capital_deployed, purpose)"
            " VALUES ('SPY', 'MGN', 100, 50000, '2026-01-01', 5000000, 'BOGUS')"
        )


def test_equity_positions_shares_must_be_positive(db):
    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            "INSERT INTO equity_positions"
            " (ticker, account_id, shares, purchase_price, purchase_date, capital_deployed)"
            " VALUES ('SPY', 'MGN', -10, 50000, '2026-01-01', 5000000)"
        )


def test_equity_positions_bucket_id_nullable(db):
    db.execute(
        "INSERT INTO equity_positions"
        " (ticker, account_id, bucket_id, shares, purchase_price, purchase_date, capital_deployed)"
        " VALUES ('SPY', 'MGN', NULL, 100, 50000, '2026-01-01', 5000000)"
    )
    db.commit()
    row = db.execute("SELECT bucket_id FROM equity_positions").fetchone()
    assert row["bucket_id"] is None
