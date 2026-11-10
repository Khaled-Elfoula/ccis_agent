"""SQLite connection helpers."""

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "ccis.sqlite"
SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"


def connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Open the database with named rows and foreign keys enabled."""
    path = Path(db_path or DEFAULT_DB_PATH)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: str | Path | None = None) -> sqlite3.Connection:
    """Create the schema when missing and return a connection."""
    conn = connect(db_path)
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()
    return conn
