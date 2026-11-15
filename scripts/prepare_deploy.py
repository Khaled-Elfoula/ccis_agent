"""Prepare the SQLite database for deployment.

Checkpoints the write-ahead log into the database file, prints its size and the exact git
commands that publish it. Nothing is committed automatically.

Usage: py scripts/prepare_deploy.py
"""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402


def checkpoint(path: str | Path) -> tuple[int, int, int]:
    """Fold the write-ahead log into the database file; return (busy, log frames, checkpointed)."""
    conn = sqlite3.connect(path)
    try:
        busy, log_frames, checkpointed = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
    finally:
        conn.close()
    return busy, log_frames, checkpointed


def size_mb(path: str | Path) -> float:
    """The size of the database file in megabytes."""
    return Path(path).stat().st_size / (1024 * 1024)


def git_commands(path: str | Path) -> list[str]:
    """The commands that publish the refreshed database, with a root-relative path when possible."""
    database = Path(path)
    try:
        shown = database.resolve().relative_to(config.ROOT)
    except ValueError:
        shown = database
    return [f"git add -f {shown.as_posix()}", 'git commit -m "refresh data"', "git push"]


def main() -> int:
    """Checkpoint the database, report its size and print the git commands."""
    path = config.DB_PATH
    if not path.exists():
        print(f"database not found: {path}")
        print("run: py -m scrapers.run --all")
        return 1
    busy, log_frames, checkpointed = checkpoint(path)
    print(f"checkpoint: busy={busy} log_frames={log_frames} checkpointed={checkpointed}")
    print(f"database:   {path} ({size_mb(path):.2f} MB)")
    print("publish it:")
    for command in git_commands(path):
        print(f"  {command}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
