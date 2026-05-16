"""
db/init_db.py — Database initialisation and connection management.

Public API
----------
init_db(path)               → sqlite3.Connection
    Create schema, apply triggers and views, seed reference data.
    Safe to call on an existing database — all DDL uses IF NOT EXISTS
    and seed uses INSERT OR IGNORE, so re-running never destroys data.

get_connection(path)        → sqlite3.Connection
    Open a connection to an already-initialised database.
    Enables foreign keys and sets row_factory for dict-style access.

generate_chain_id()         → str
    Return a unique system id, e.g. 'chn_a1b2c3d4'.

generate_chain_alias(...)   → str
    Return a human-readable alias, e.g. 'm-spy-500-0101'.
    Includes collision guard — safe to call concurrently.
"""

import secrets
import sqlite3
from datetime import date
from pathlib import Path

# Ordered load sequence — each file depends on the ones before it.
_SCHEMA_DIR = Path(__file__).parent / "schema"
_SEED_DIR   = Path(__file__).parent / "seed"

_SQL_FILES = [
    _SCHEMA_DIR / "layer1_reference.sql",
    _SCHEMA_DIR / "layer2_chains.sql",
    _SCHEMA_DIR / "layer3_equity.sql",
    _SCHEMA_DIR / "triggers.sql",
    _SCHEMA_DIR / "views.sql",
    _SEED_DIR   / "seed_data.sql",
]


# ---------------------------------------------------------------------------
# Connection helper
# ---------------------------------------------------------------------------

def _configure(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Apply connection-level settings common to all connections."""
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")   # safe for concurrent readers
    conn.row_factory = sqlite3.Row              # col access by name: row["field"]
    return conn


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def init_db(path: str) -> sqlite3.Connection:
    """
    Initialise the database at *path* and return an open connection.

    Parameters
    ----------
    path : str
        Filesystem path for the SQLite file, or ':memory:' for an
        in-memory database (used by tests).

    Returns
    -------
    sqlite3.Connection
        Open, configured connection. Caller is responsible for closing it.
    """
    conn = sqlite3.connect(path)
    _configure(conn)

    for sql_file in _SQL_FILES:
        sql = sql_file.read_text(encoding="utf-8")
        conn.executescript(sql)

    return conn


def get_connection(path: str) -> sqlite3.Connection:
    """
    Open a connection to an already-initialised database.

    Does not run schema or seed scripts — use init_db() for first-time setup.

    Parameters
    ----------
    path : str
        Filesystem path to an existing SQLite database.

    Returns
    -------
    sqlite3.Connection
        Open, configured connection. Caller is responsible for closing it.
    """
    conn = sqlite3.connect(path)
    _configure(conn)
    return conn


# ---------------------------------------------------------------------------
# Chain ID and alias generation
# ---------------------------------------------------------------------------

def generate_chain_id() -> str:
    """
    Generate a unique, stable system id for a chain row.

    Format: 'chn_' + 8 hex chars, e.g. 'chn_a1b2c3d4'.
    Uses secrets.token_hex — cryptographically random, collision probability
    negligible at any realistic trade volume.

    Returns
    -------
    str
    """
    return f"chn_{secrets.token_hex(4)}"


def generate_chain_alias(
    account_id: str,
    ticker: str,
    open_date: date,
    conn: sqlite3.Connection,
    strike: int | None = None,
    is_equity: bool = False,
) -> str:
    """
    Construct a human-readable alias for a new chain.

    The alias is purely cosmetic — no foreign keys reference it.
    It can be changed at any time without cascading updates.

    Format (standard):  '{account_code}-{ticker}-{strike}-{mmdd}'
    Format (equity):    '{account_code}-{ticker}-eq-{mmdd}'

    Parameters
    ----------
    account_id : str
        e.g. 'MGN' → account_code 'm'
    ticker : str
        e.g. 'SPY' → ticker_code 'spy'
    open_date : date
        Trade open date — used for the mmdd suffix.
    conn : sqlite3.Connection
        Active db connection for the collision guard query.
    strike : int | None
        Strike price in cents (e.g. 50000 = $500). Required unless is_equity.
    is_equity : bool
        If True, omits the strike segment and inserts 'eq' instead.

    Returns
    -------
    str
        Unique alias, e.g. 'm-spy-500-0101' or 'm-spy-eq-0101'.

    Raises
    ------
    ValueError
        If strike is None and is_equity is False.
    """
    if not is_equity and strike is None:
        raise ValueError("strike is required for non-equity chains")

    account_code = account_id.lower()[0]            # 'm', 'r', 'c'
    ticker_code  = ticker.lower()                   # 'spy', 'soxl'
    date_code    = open_date.strftime("%m%d")       # '0101'

    if is_equity:
        base = f"{account_code}-{ticker_code}-eq-{date_code}"
    else:
        strike_dollars = str(int(strike // 100))    # cents → whole dollars: 50000 → '500'
        base = f"{account_code}-{ticker_code}-{strike_dollars}-{date_code}"

    # Collision guard — append count suffix if alias (or variant) already exists
    existing = conn.execute(
        "SELECT alias FROM chains WHERE alias LIKE ?",
        (f"{base}%",),
    ).fetchall()

    if not existing:
        return base
    return f"{base}-{len(existing) + 1}"
