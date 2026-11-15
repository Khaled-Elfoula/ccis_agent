"""SQLite connection helpers."""

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "ccis.sqlite"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

# Where a faculty email came from. Anything else in the column is a bug.
EMAIL_SOURCES = ("scraped", "derived", "derived_unverified")


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open the database with named rows and foreign keys enabled."""
    path = Path(db_path or DEFAULT_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Return the column names of a table."""
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def migrate(conn: sqlite3.Connection) -> None:
    """Bring an existing database up to the current schema without losing data.

    Idempotent: every step checks the current state first, so it is safe to run on
    a database that is already up to date.
    """
    faculty_columns = columns(conn, "faculty")
    if "email_source" not in faculty_columns:
        conn.execute("ALTER TABLE faculty ADD COLUMN email_source TEXT")
    # An email that predates the column was read off a faculty profile page.
    # A derived email always carries its own source, so this cannot mislabel one.
    conn.execute(
        "UPDATE faculty SET email_source = 'scraped'"
        " WHERE email_source IS NULL AND email IS NOT NULL AND TRIM(email) <> ''"
    )
    conn.commit()


def init_db(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Create the schema when missing, migrate it, and return a connection."""
    conn = connect(db_path)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    migrate(conn)
    return conn
