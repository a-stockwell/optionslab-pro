"""
Pytest fixtures for optionslab-pro db tests.
Each test gets a fresh in-memory SQLite database, fully initialized and seeded.
"""

import sqlite3
import pytest
from db.init_db import init_db


@pytest.fixture
def db():
    """
    In-memory SQLite connection, initialized with full schema + seed data.
    Torn down automatically after each test.
    """
    conn = init_db(":memory:")
    yield conn
    conn.close()
