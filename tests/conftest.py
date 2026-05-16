"""
Shared pytest fixtures.
Each test gets a fresh in-memory SQLite database, fully initialised and seeded.
"""

import pytest
from db.init_db import init_db


@pytest.fixture
def db():
    """
    In-memory SQLite connection, initialised with full schema + seed data.
    Torn down automatically after each test.
    """
    conn = init_db(":memory:")
    yield conn
    conn.close()


@pytest.fixture
def db_seeded(db):
    """
    Extends db with the minimum reference rows needed by most tests:
    - strategy_definitions are already seeded by init_db
    - Adds accounts MGN + ROTH, buckets A + B, one bucket_config row each
    Returns the connection.
    """
    # accounts and buckets already seeded by seed_data.sql
    db.execute(
        "INSERT INTO bucket_config (account_id, bucket_id, bucket_size, is_active, effective_date)"
        " VALUES ('MGN', 'A', 1050000, 1, '2026-01-01')"
    )
    db.execute(
        "INSERT INTO bucket_config (account_id, bucket_id, bucket_size, is_active, effective_date)"
        " VALUES ('MGN', 'B', 1050000, 1, '2026-01-01')"
    )
    db.commit()
    return db
