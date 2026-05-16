"""
test_views.py — Computed view correctness with realistic data.

Scenario
--------
One WHEEL chain on SPY, FULL_CHAIN reporting, MGN account, Bucket A.

Timeline:
  Week 1  — CSP opened and assigned at $500 strike. $5.00/share premium COMPRESSED.
  Week 3  — Share block acquired at $500.
  Week 4  — First CC expires worthless. $2.00/share premium REALIZED.
  Week 6  — Second CC open. $1.50/share premium OPEN_CREDIT.
  Week 8  — Shares called away at $505. Share exit recorded.

All monetary values in cents throughout.
"""

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def wheel_chain(db):
    """
    Insert a complete WHEEL chain with CSP → assignment → two CCs → share exit.
    Returns (conn, share_block_id).
    """
    sid = db.execute(
        "SELECT id FROM strategy_definitions WHERE name = 'WHEEL'"
    ).fetchone()[0]

    db.execute(
        f"INSERT INTO chains VALUES ('chn_spy1', 'm-spy-500-0101', 'SPY', 'MGN', 'A', {sid},"
        " 'FULL_CHAIN', 'OPEN', '2026-01-02', NULL, NULL)"
    )

    # CSP — assigned, COMPRESSED, $5.00/share = 500 cents/share, net $500 = 50000 cents
    db.execute(
        "INSERT INTO option_legs"
        " (chain_id, account_id, bucket_id, leg_role, option_type, strike, expiration_date,"
        "  status, date_in, date_out, contracts, premium_raw, premium_net, credit_debit,"
        "  premium_status, realized_amount, collateral_per_contract, collateral_total, year, week)"
        " VALUES ('chn_spy1','MGN','A','SHORT','PUT',50000,'2026-01-17',"
        "  'ASSIGNED','2026-01-02','2026-01-17',1,500,50000,'CREDIT',"
        "  'COMPRESSED',50000,5000000,5000000,2026,1)"
    )

    # Share block — acquired at $500 strike
    db.execute(
        "INSERT INTO share_blocks"
        " (chain_id, account_id, bucket_id, block_number, shares, acquisition_type,"
        "  assigned_strike, acquisition_date, status, capital_deployed, year, week)"
        " VALUES ('chn_spy1','MGN','A',1,100,'ASSIGNED',50000,'2026-01-17','OPEN',5000000,2026,3)"
    )
    sb_id = db.execute("SELECT id FROM share_blocks").fetchone()[0]

    # CC 1 — expired worthless, REALIZED, $2.00/share = 200 cents/share, net $200 = 20000 cents
    db.execute(
        f"INSERT INTO option_legs"
        " (chain_id, account_id, bucket_id, leg_role, option_type, strike, expiration_date,"
        "  status, date_in, date_out, contracts, premium_raw, premium_net, credit_debit,"
        "  premium_status, realized_amount, share_block_id, year, week)"
        f" VALUES ('chn_spy1','MGN','A','SHORT','CALL',51000,'2026-01-31',"
        "  'EXPIRED','2026-01-21','2026-01-31',1,200,20000,'CREDIT',"
        f"  'REALIZED',20000,{sb_id},2026,4)"
    )

    # CC 2 — open, $1.50/share = 150 cents/share, net $150 = 15000 cents
    db.execute(
        f"INSERT INTO option_legs"
        " (chain_id, account_id, bucket_id, leg_role, option_type, strike, expiration_date,"
        "  status, date_in, contracts, premium_raw, premium_net, credit_debit,"
        "  premium_status, share_block_id, year, week)"
        f" VALUES ('chn_spy1','MGN','A','SHORT','CALL',51500,'2026-02-14',"
        "  'OPEN','2026-02-03',1,150,15000,'CREDIT',"
        f"  'OPEN_CREDIT',{sb_id},2026,6)"
    )

    # Share exit — called away at $505 = 50500 cents
    # gross P/L: (50500 - 50000) * 100 = 50000 cents ($500)
    db.execute(
        f"INSERT INTO share_exits"
        " (share_block_id, chain_id, exit_type, exit_price, exit_date, shares_exited,"
        "  realized_pl_gross, year, week)"
        f" VALUES ({sb_id},'chn_spy1','CALLED_AWAY',50500,'2026-02-28',100,50000,2026,8)"
    )

    db.commit()
    return db, sb_id


# ---------------------------------------------------------------------------
# v_share_breakeven
# ---------------------------------------------------------------------------

class TestShareBreakeven:
    def test_breakeven_original(self, wheel_chain):
        db, sb_id = wheel_chain
        row = db.execute(
            "SELECT breakeven_original FROM v_share_breakeven WHERE share_block_id = ?", (sb_id,)
        ).fetchone()
        # assigned_strike 50000 − csp_compression 500 = 49500 ($495.00)
        assert row["breakeven_original"] == 49500

    def test_breakeven_current(self, wheel_chain):
        db, sb_id = wheel_chain
        row = db.execute(
            "SELECT breakeven_current FROM v_share_breakeven WHERE share_block_id = ?", (sb_id,)
        ).fetchone()
        # 49500 − (20000 realized / 100 shares) = 49500 − 200 = 49300 ($493.00)
        assert row["breakeven_current"] == 49300

    def test_breakeven_next(self, wheel_chain):
        db, sb_id = wheel_chain
        row = db.execute(
            "SELECT breakeven_next FROM v_share_breakeven WHERE share_block_id = ?", (sb_id,)
        ).fetchone()
        # 49300 − (15000 open / 100 shares) = 49300 − 150 = 49150 ($491.50)
        assert row["breakeven_next"] == 49150

    def test_csp_compression_per_share(self, wheel_chain):
        db, sb_id = wheel_chain
        row = db.execute(
            "SELECT csp_compression_per_share FROM v_share_breakeven WHERE share_block_id = ?",
            (sb_id,)
        ).fetchone()
        # 50000 net / 100 shares = 500 cents ($5.00)
        assert row["csp_compression_per_share"] == 500

    def test_no_compression_on_yield_engine_chain(self, db):
        """A YIELD_ENGINE chain: CSP premium is REALIZED not COMPRESSED — breakeven unchanged."""
        sid = db.execute(
            "SELECT id FROM strategy_definitions WHERE name = 'WHEEL'"
        ).fetchone()[0]
        db.execute(
            f"INSERT INTO chains VALUES ('chn_ye', NULL, 'SPY', 'MGN', 'A', {sid},"
            " 'YIELD_ENGINE', 'OPEN', '2026-01-02', NULL, NULL)"
        )
        db.execute(
            "INSERT INTO option_legs"
            " (chain_id, account_id, bucket_id, leg_role, option_type, strike, expiration_date,"
            "  status, date_in, contracts, premium_raw, premium_net, credit_debit,"
            "  premium_status, realized_amount, year, week)"
            " VALUES ('chn_ye','MGN','A','SHORT','PUT',50000,'2026-01-17',"
            "  'ASSIGNED','2026-01-02',1,500,50000,'CREDIT',"
            "  'REALIZED',50000,2026,1)"
        )
        db.execute(
            "INSERT INTO share_blocks"
            " (chain_id, account_id, bucket_id, block_number, shares, acquisition_type,"
            "  assigned_strike, acquisition_date, status, capital_deployed)"
            " VALUES ('chn_ye','MGN','A',1,100,'ASSIGNED',50000,'2026-01-17','OPEN',5000000)"
        )
        db.commit()
        sb_id = db.execute("SELECT id FROM share_blocks").fetchone()[0]
        row = db.execute(
            "SELECT breakeven_original, csp_compression_per_share"
            " FROM v_share_breakeven WHERE share_block_id = ?", (sb_id,)
        ).fetchone()
        # No COMPRESSED CSP → breakeven_original equals assigned_strike
        assert row["breakeven_original"] == 50000
        assert row["csp_compression_per_share"] == 0


# ---------------------------------------------------------------------------
# v_equity_breakeven
# ---------------------------------------------------------------------------

class TestEquityBreakeven:
    @pytest.fixture
    def equity_scenario(self, db):
        db.execute(
            "INSERT INTO equity_positions"
            " (ticker, account_id, shares, purchase_price, purchase_date, capital_deployed, purpose)"
            " VALUES ('SOFI', 'MGN', 100, 1200, '2026-01-01', 120000, 'COMPRESSION')"
        )
        db.commit()
        ep_id = db.execute("SELECT id FROM equity_positions").fetchone()[0]

        sid = db.execute(
            "SELECT id FROM strategy_definitions WHERE name = 'COVERED_CALL_EQUITY'"
        ).fetchone()[0]
        db.execute(
            f"INSERT INTO chains VALUES ('chn_sofi', NULL, 'SOFI', 'MGN', 'A', {sid},"
            " 'FULL_CHAIN', 'OPEN', '2026-01-05', NULL, NULL)"
        )
        # Closed CC: $0.30/share REALIZED, net $30 = 3000 cents
        db.execute(
            f"INSERT INTO option_legs"
            " (chain_id, account_id, bucket_id, leg_role, option_type, strike, expiration_date,"
            "  status, date_in, date_out, contracts, premium_raw, premium_net, credit_debit,"
            "  premium_status, realized_amount, equity_position_id, year, week)"
            f" VALUES ('chn_sofi','MGN','A','SHORT','CALL',1300,'2026-01-17',"
            "  'EXPIRED','2026-01-07','2026-01-17',1,30,3000,'CREDIT',"
            f"  'REALIZED',3000,{ep_id},2026,3)"
        )
        # Open CC: $0.25/share, net $25 = 2500 cents
        db.execute(
            f"INSERT INTO option_legs"
            " (chain_id, account_id, bucket_id, leg_role, option_type, strike, expiration_date,"
            "  status, date_in, contracts, premium_raw, premium_net, credit_debit,"
            "  premium_status, equity_position_id, year, week)"
            f" VALUES ('chn_sofi','MGN','A','SHORT','CALL',1350,'2026-01-31',"
            "  'OPEN','2026-01-21',1,25,2500,'CREDIT',"
            f"  'OPEN_CREDIT',{ep_id},2026,4)"
        )
        db.commit()
        return db, ep_id

    def test_breakeven_original_equals_purchase_price(self, equity_scenario):
        db, ep_id = equity_scenario
        row = db.execute(
            "SELECT breakeven_original FROM v_equity_breakeven WHERE equity_position_id = ?",
            (ep_id,)
        ).fetchone()
        assert row["breakeven_original"] == 1200

    def test_breakeven_current(self, equity_scenario):
        db, ep_id = equity_scenario
        row = db.execute(
            "SELECT breakeven_current FROM v_equity_breakeven WHERE equity_position_id = ?",
            (ep_id,)
        ).fetchone()
        # 1200 − (3000 / 100) = 1200 − 30 = 1170 ($11.70)
        assert row["breakeven_current"] == 1170

    def test_breakeven_next(self, equity_scenario):
        db, ep_id = equity_scenario
        row = db.execute(
            "SELECT breakeven_next FROM v_equity_breakeven WHERE equity_position_id = ?",
            (ep_id,)
        ).fetchone()
        # 1170 − (2500 / 100) = 1170 − 25 = 1145 ($11.45)
        assert row["breakeven_next"] == 1145


# ---------------------------------------------------------------------------
# v_bucket_utilization
# ---------------------------------------------------------------------------

class TestBucketUtilization:
    def test_deployed_shares_counts_open_blocks(self, wheel_chain, db_seeded):
        db, sb_id = wheel_chain
        row = db.execute(
            "SELECT deployed_shares, deployed_options, deployed_total, available, utilization_pct"
            " FROM v_bucket_utilization WHERE account_id = 'MGN' AND bucket_id = 'A'"
        ).fetchone()
        assert row["deployed_shares"] == 5000000    # $50,000 in cents
        # open CC has no collateral_total set → 0
        assert row["deployed_options"] == 0
        assert row["deployed_total"] == 5000000

    def test_utilization_pct(self, wheel_chain, db_seeded):
        db, sb_id = wheel_chain
        row = db.execute(
            "SELECT utilization_pct FROM v_bucket_utilization WHERE account_id='MGN' AND bucket_id='A'"
        ).fetchone()
        # 5000000 / 1050000 * 100 ≈ 476.19
        assert abs(row["utilization_pct"] - 476.19) < 0.01

    def test_empty_bucket_shows_zero_deployment(self, db_seeded):
        row = db_seeded.execute(
            "SELECT deployed_total FROM v_bucket_utilization WHERE account_id='MGN' AND bucket_id='B'"
        ).fetchone()
        assert row["deployed_total"] == 0

    def test_only_most_recent_active_config_used(self, db):
        """Two config rows for same account+bucket — only the most recent active one counts."""
        db.execute(
            "INSERT INTO bucket_config (account_id, bucket_id, bucket_size, is_active, effective_date)"
            " VALUES ('MGN', 'A', 500000, 0, '2025-01-01')"
        )
        db.execute(
            "INSERT INTO bucket_config (account_id, bucket_id, bucket_size, is_active, effective_date)"
            " VALUES ('MGN', 'A', 1050000, 1, '2026-01-01')"
        )
        db.commit()
        row = db.execute(
            "SELECT bucket_size FROM v_bucket_utilization WHERE account_id='MGN' AND bucket_id='A'"
        ).fetchone()
        assert row["bucket_size"] == 1050000


# ---------------------------------------------------------------------------
# v_chain_summary
# ---------------------------------------------------------------------------

class TestChainSummary:
    def test_open_credit(self, wheel_chain):
        db, _ = wheel_chain
        row = db.execute(
            "SELECT open_credit FROM v_chain_summary WHERE chain_id = 'chn_spy1'"
        ).fetchone()
        assert row["open_credit"] == 15000   # open CC net

    def test_total_realized_premium(self, wheel_chain):
        db, _ = wheel_chain
        row = db.execute(
            "SELECT total_realized_premium FROM v_chain_summary WHERE chain_id = 'chn_spy1'"
        ).fetchone()
        assert row["total_realized_premium"] == 20000   # CC 1 realized_amount

    def test_total_compressed_premium(self, wheel_chain):
        db, _ = wheel_chain
        row = db.execute(
            "SELECT total_compressed_premium FROM v_chain_summary WHERE chain_id = 'chn_spy1'"
        ).fetchone()
        assert row["total_compressed_premium"] == 50000   # CSP premium_net

    def test_share_pl_gross(self, wheel_chain):
        db, _ = wheel_chain
        row = db.execute(
            "SELECT share_pl_gross FROM v_chain_summary WHERE chain_id = 'chn_spy1'"
        ).fetchone()
        assert row["share_pl_gross"] == 50000   # (50500 - 50000) * 100

    def test_total_cycle_pl(self, wheel_chain):
        db, _ = wheel_chain
        row = db.execute(
            "SELECT total_cycle_pl FROM v_chain_summary WHERE chain_id = 'chn_spy1'"
        ).fetchone()
        # total_closed_premium (20000 + 50000) + share_pl_gross (50000) = 120000
        assert row["total_cycle_pl"] == 120000

    def test_chain_with_no_legs_shows_zeros(self, db):
        sid = db.execute(
            "SELECT id FROM strategy_definitions WHERE name = 'WHEEL'"
        ).fetchone()[0]
        db.execute(
            f"INSERT INTO chains VALUES ('chn_empty', NULL, 'SPY', 'MGN', 'A', {sid},"
            " 'YIELD_ENGINE', 'OPEN', '2026-01-01', NULL, NULL)"
        )
        db.commit()
        row = db.execute(
            "SELECT open_credit, total_realized_premium, share_pl_gross, total_cycle_pl"
            " FROM v_chain_summary WHERE chain_id = 'chn_empty'"
        ).fetchone()
        assert row["open_credit"] == 0
        assert row["total_realized_premium"] == 0
        assert row["share_pl_gross"] == 0
        assert row["total_cycle_pl"] == 0


# ---------------------------------------------------------------------------
# v_weekly_income
# ---------------------------------------------------------------------------

class TestWeeklyIncome:
    def test_compressed_premium_appears_in_correct_week(self, wheel_chain):
        db, _ = wheel_chain
        row = db.execute(
            "SELECT premium_realized FROM v_weekly_income WHERE year=2026 AND week=1"
        ).fetchone()
        assert row["premium_realized"] == 50000   # CSP COMPRESSED realized_amount

    def test_realized_premium_appears_in_correct_week(self, wheel_chain):
        db, _ = wheel_chain
        row = db.execute(
            "SELECT premium_realized FROM v_weekly_income WHERE year=2026 AND week=4"
        ).fetchone()
        assert row["premium_realized"] == 20000   # CC 1 REALIZED

    def test_open_credit_not_in_weekly_income(self, wheel_chain):
        db, _ = wheel_chain
        row = db.execute(
            "SELECT premium_realized FROM v_weekly_income WHERE year=2026 AND week=6"
        ).fetchone()
        # open CC is OPEN_CREDIT — not yet realized, must not appear
        assert row is None or row["premium_realized"] == 0

    def test_share_exit_pl_in_correct_week(self, wheel_chain):
        db, _ = wheel_chain
        row = db.execute(
            "SELECT share_pl, total_weekly_income FROM v_weekly_income WHERE year=2026 AND week=8"
        ).fetchone()
        assert row["share_pl"] == 50000
        assert row["total_weekly_income"] == 50000

    def test_total_weekly_income_sums_premium_and_share_pl(self, wheel_chain):
        db, _ = wheel_chain
        # week 1: 50000 premium + 0 share = 50000
        row = db.execute(
            "SELECT total_weekly_income FROM v_weekly_income WHERE year=2026 AND week=1"
        ).fetchone()
        assert row["total_weekly_income"] == 50000
