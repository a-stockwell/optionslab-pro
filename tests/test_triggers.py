"""
test_triggers.py — Business rule trigger enforcement.
"""

import sqlite3
import pytest

TRIGGER_ERR = (sqlite3.OperationalError, sqlite3.IntegrityError)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _wheel_id(db):
    return db.execute(
        "SELECT id FROM strategy_definitions WHERE name = 'WHEEL'"
    ).fetchone()[0]


def _spread_id(db):
    return db.execute(
        "SELECT id FROM strategy_definitions WHERE name = 'BEAR_CALL_SPREAD'"
    ).fetchone()[0]


def _insert_chain(db, chain_id, strategy="WHEEL", reporting="YIELD_ENGINE", account="MGN", bucket="A"):
    sid = db.execute(
        "SELECT id FROM strategy_definitions WHERE name = ?", (strategy,)
    ).fetchone()[0]
    db.execute(
        f"INSERT INTO chains VALUES ('{chain_id}', NULL, 'SPY', '{account}', '{bucket}', {sid},"
        f" '{reporting}', 'OPEN', '2026-01-01', NULL, NULL)"
    )
    db.commit()


def _insert_leg(db, chain_id, account="MGN", bucket="A", premium_status="OPEN_CREDIT", **kwargs):
    defaults = dict(
        leg_role="SHORT", option_type="PUT", strike=50000,
        expiration_date="2026-02-01", status="OPEN", date_in="2026-01-15",
        contracts=1, premium_raw=100, credit_debit="CREDIT",
    )
    defaults.update(kwargs)
    defaults["premium_status"] = premium_status
    cols = ", ".join(defaults.keys())
    placeholders = ", ".join("?" * len(defaults))
    db.execute(
        f"INSERT INTO option_legs (chain_id, account_id, bucket_id, {cols})"
        f" VALUES ('{chain_id}', '{account}', '{bucket}', {placeholders})",
        list(defaults.values()),
    )
    db.commit()


# ---------------------------------------------------------------------------
# trg_bucket_config_one_active_insert
# ---------------------------------------------------------------------------

def test_bucket_config_insert_deactivates_prior_active_row(db):
    db.execute(
        "INSERT INTO bucket_config (account_id, bucket_id, bucket_size, is_active, effective_date)"
        " VALUES ('MGN', 'A', 1000000, 1, '2026-01-01')"
    )
    db.execute(
        "INSERT INTO bucket_config (account_id, bucket_id, bucket_size, is_active, effective_date)"
        " VALUES ('MGN', 'A', 1200000, 1, '2026-03-01')"
    )
    db.commit()
    rows = db.execute(
        "SELECT is_active FROM bucket_config WHERE account_id='MGN' AND bucket_id='A'"
        " ORDER BY effective_date"
    ).fetchall()
    assert rows[0]["is_active"] == 0, "earlier row should be deactivated"
    assert rows[1]["is_active"] == 1, "newer row should be active"


def test_bucket_config_insert_does_not_affect_other_bucket(db):
    db.execute(
        "INSERT INTO bucket_config (account_id, bucket_id, bucket_size, is_active, effective_date)"
        " VALUES ('MGN', 'A', 1000000, 1, '2026-01-01')"
    )
    db.execute(
        "INSERT INTO bucket_config (account_id, bucket_id, bucket_size, is_active, effective_date)"
        " VALUES ('MGN', 'B', 1000000, 1, '2026-01-01')"
    )
    # inserting a new active row for A should not touch B
    db.execute(
        "INSERT INTO bucket_config (account_id, bucket_id, bucket_size, is_active, effective_date)"
        " VALUES ('MGN', 'A', 1200000, 1, '2026-03-01')"
    )
    db.commit()
    b_active = db.execute(
        "SELECT is_active FROM bucket_config WHERE account_id='MGN' AND bucket_id='B'"
    ).fetchone()["is_active"]
    assert b_active == 1, "bucket B should remain active"


# ---------------------------------------------------------------------------
# trg_bucket_config_one_active_update
# ---------------------------------------------------------------------------

def test_bucket_config_update_deactivates_other_rows(db):
    db.execute(
        "INSERT INTO bucket_config (account_id, bucket_id, bucket_size, is_active, effective_date)"
        " VALUES ('MGN', 'A', 1000000, 0, '2026-01-01')"
    )
    db.execute(
        "INSERT INTO bucket_config (account_id, bucket_id, bucket_size, is_active, effective_date)"
        " VALUES ('MGN', 'A', 1200000, 0, '2026-03-01')"
    )
    db.commit()
    db.execute(
        "UPDATE bucket_config SET is_active = 1 WHERE effective_date = '2026-03-01'"
    )
    db.commit()
    rows = db.execute(
        "SELECT is_active FROM bucket_config WHERE account_id='MGN' AND bucket_id='A'"
        " ORDER BY effective_date"
    ).fetchall()
    assert rows[0]["is_active"] == 0
    assert rows[1]["is_active"] == 1


# ---------------------------------------------------------------------------
# trg_option_legs_chain_match_insert
# ---------------------------------------------------------------------------

def test_option_leg_account_mismatch_blocked(db):
    _insert_chain(db, "chn_001", account="MGN")
    with pytest.raises(TRIGGER_ERR):
        _insert_leg(db, "chn_001", account="ROTH")   # chain is MGN, leg says ROTH


def test_option_leg_bucket_mismatch_blocked(db):
    _insert_chain(db, "chn_001", bucket="A")
    with pytest.raises(TRIGGER_ERR):
        _insert_leg(db, "chn_001", bucket="B")   # chain is A, leg says B


def test_option_leg_matching_account_and_bucket_allowed(db):
    _insert_chain(db, "chn_001", account="MGN", bucket="A")
    _insert_leg(db, "chn_001", account="MGN", bucket="A")   # must not raise


# ---------------------------------------------------------------------------
# trg_share_blocks_chain_match_insert
# ---------------------------------------------------------------------------

def test_share_block_account_mismatch_blocked(db):
    _insert_chain(db, "chn_001", account="MGN")
    with pytest.raises(TRIGGER_ERR):
        db.execute(
            "INSERT INTO share_blocks"
            " (chain_id, account_id, bucket_id, block_number, shares, acquisition_type,"
            "  assigned_strike, acquisition_date, status, capital_deployed)"
            " VALUES ('chn_001', 'ROTH', 'A', 1, 100, 'ASSIGNED', 50000, '2026-01-10', 'OPEN', 5000000)"
        )


def test_share_block_bucket_mismatch_blocked(db):
    _insert_chain(db, "chn_001", bucket="A")
    with pytest.raises(TRIGGER_ERR):
        db.execute(
            "INSERT INTO share_blocks"
            " (chain_id, account_id, bucket_id, block_number, shares, acquisition_type,"
            "  assigned_strike, acquisition_date, status, capital_deployed)"
            " VALUES ('chn_001', 'MGN', 'B', 1, 100, 'ASSIGNED', 50000, '2026-01-10', 'OPEN', 5000000)"
        )


def test_share_block_matching_chain_allowed(db):
    _insert_chain(db, "chn_001", account="MGN", bucket="A")
    db.execute(
        "INSERT INTO share_blocks"
        " (chain_id, account_id, bucket_id, block_number, shares, acquisition_type,"
        "  assigned_strike, acquisition_date, status, capital_deployed)"
        " VALUES ('chn_001', 'MGN', 'A', 1, 100, 'ASSIGNED', 50000, '2026-01-10', 'OPEN', 5000000)"
    )
    db.commit()


# ---------------------------------------------------------------------------
# trg_option_legs_compression_insert / _update
# ---------------------------------------------------------------------------

def test_compressed_blocked_on_non_compressible_strategy_insert(db):
    _insert_chain(db, "chn_002", strategy="BEAR_CALL_SPREAD")
    with pytest.raises(TRIGGER_ERR):
        _insert_leg(db, "chn_002", premium_status="COMPRESSED")


def test_compressed_blocked_on_non_compressible_strategy_update(db):
    _insert_chain(db, "chn_002", strategy="BEAR_CALL_SPREAD")
    _insert_leg(db, "chn_002")   # insert as OPEN_CREDIT
    leg_id = db.execute("SELECT id FROM option_legs").fetchone()[0]
    with pytest.raises(TRIGGER_ERR):
        db.execute(
            f"UPDATE option_legs SET premium_status = 'COMPRESSED' WHERE id = {leg_id}"
        )


def test_compressed_allowed_on_compressible_strategy(db):
    _insert_chain(db, "chn_003", strategy="WHEEL", reporting="FULL_CHAIN")
    _insert_leg(db, "chn_003", premium_status="COMPRESSED")   # must not raise


def test_compressed_allowed_on_cash_secured_put(db):
    _insert_chain(db, "chn_004", strategy="CASH_SECURED_PUT", reporting="FULL_CHAIN")
    _insert_leg(db, "chn_004", premium_status="COMPRESSED")   # must not raise


def test_non_compressed_status_always_allowed_on_spread(db):
    _insert_chain(db, "chn_005", strategy="BEAR_CALL_SPREAD")
    _insert_leg(db, "chn_005", premium_status="OPEN_CREDIT")   # must not raise
