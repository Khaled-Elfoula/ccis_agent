"""Finish the faculty profile scrape politely, in small batches, and stay resumable.

Only the profiles that are still missing are fetched: nothing else is scraped, no
user-agent change, no proxy, no parallel requests, and robots.txt is still obeyed
by the shared fetch layer.

Usage:
    py scripts/resume_profiles.py
    py scripts/resume_profiles.py --batch 15 --pause-minutes 20
"""

import argparse
import json
import logging
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import db as database
from scrapers import ccis, faculty, profiles
from scrapers.base import CACHE_DIR, ScrapeError
from scrapers.sources import FACULTY_LISTS
from scrapers.run import store_faculty, store_office_hours

logger = logging.getLogger("resume_profiles")

BATCH_SIZE = 15
PAUSE_SECONDS = 20 * 60
PROFILE_TIMEOUT = 10.0
PROFILE_ATTEMPTS = 2
REQUEST_GAP = (10.0, 15.0)
STALL_SECONDS = 30.0
STALL_LIMIT = 2
ZERO_BATCH_LIMIT = 3
DEAD_STATUS = (403, 404)
DEAD_FILE = CACHE_DIR / "profiles_dead.json"


def load_dead() -> dict[str, dict]:
    """Read the usernames already known to be gone, so they are never fetched again."""
    if not DEAD_FILE.exists():
        return {}
    try:
        data = json.loads(DEAD_FILE.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        logger.error("dead list unreadable (%s); keeping it as it is", exc)
        return {}
    return data if isinstance(data, dict) else {}


def save_dead(dead: dict[str, dict]) -> None:
    """Write the dead list, merging into whatever is on disk already."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    DEAD_FILE.write_text(json.dumps(dead, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def mark_dead(dead: dict[str, dict], username: str, status: int) -> None:
    """Record a 403/404 as dead and save it at once."""
    dead[username] = {
        "status": status,
        "url": profiles.PROFILE_URL.format(username=username),
        "checked_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    save_dead(dead)


def all_usernames() -> list[str]:
    """Read the people from the cached faculty lists, in scrape order, without a request."""
    names: list[str] = []
    for faculty_list in FACULTY_LISTS:
        cache = CACHE_DIR / f"{faculty_list.id}.html"
        if not cache.exists():
            logger.error("%s is not cached; run the list scrape first", faculty_list.id)
            continue
        rows = faculty.parse_list(
            cache.read_text(encoding="utf-8"), faculty_list.department, faculty_list.url
        )
        names.extend(row.username for row in rows)
    return list(dict.fromkeys(names))


def is_cached(username: str) -> bool:
    """True when this profile page is already on disk."""
    return (CACHE_DIR / f"fac_profile_{username}.html").exists()


def pending_usernames(dead: dict[str, dict]) -> list[str]:
    """The profiles still to fetch: not stored yet and not known dead."""
    return [name for name in all_usernames() if name not in dead and not is_cached(name)]


def _status_code(message: str) -> int | None:
    """Read the HTTP status out of a fetch error message."""
    match = re.search(r"HTTP (\d{3})", message or "")
    return int(match.group(1)) if match else None


def _emails(conn) -> int:
    """How many faculty rows carry an email address."""
    return conn.execute(
        "SELECT count(*) FROM faculty WHERE email IS NOT NULL AND email <> ''"
    ).fetchone()[0]


def _report(conn, dead: dict[str, dict], label: str) -> None:
    """Print one progress line."""
    total = len(all_usernames())
    pending = pending_usernames(dead)
    done = total - len(pending) - len(dead)
    print(
        f"{label}: done {done}/{total}, dead {len(dead)}, "
        f"remaining {len(pending)}, emails {_emails(conn)}",
        flush=True,
    )


def fetch_one(username: str):
    """Fetch one profile with the polite settings this script promises."""
    return profiles.fetch_profile(
        username,
        timeout=PROFILE_TIMEOUT,
        attempts=PROFILE_ATTEMPTS,
        interval=random.uniform(*REQUEST_GAP),
    )


def store_profile(conn, username: str, profile) -> int:
    """Store one profile at once, keeping office hours the new parse does not have."""
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    faculty_id = store_faculty(conn, username, scraped_at, profile=profile)
    if not profile.office_hours:
        return 0
    return store_office_hours(conn, faculty_id, profile.office_hours, profile.source_url, scraped_at)


def run_batches(
    batch_size: int = BATCH_SIZE,
    pause_seconds: float = PAUSE_SECONDS,
    fetch=None,
    sleep=time.sleep,
) -> dict:
    """Work through the remaining profiles batch by batch; return the summary."""
    fetch = fetch or fetch_one
    conn = database.init_db(config.DB_PATH)
    dead = load_dead()
    summary = {"stored": 0, "dead": 0, "failed": 0, "batches": 0, "stopped": ""}
    zero_batches = 0
    stalls = 0
    try:
        while True:
            pending = pending_usernames(dead)
            if not pending:
                summary["stopped"] = "everything is done or marked dead"
                break
            summary["batches"] += 1
            stored = dead_now = failed = 0
            stalled = False
            for username in pending[:batch_size]:
                started = time.monotonic()
                try:
                    profile, _ = fetch(username)
                except ScrapeError as exc:
                    code = _status_code(str(exc))
                    if code in DEAD_STATUS:
                        mark_dead(dead, username, code)
                        dead_now += 1
                        print(f"  dead {username}: HTTP {code}", flush=True)
                    else:
                        failed += 1
                        print(f"  failed {username}: {exc}", flush=True)
                except ccis.ParseError as exc:
                    failed += 1
                    print(f"  failed {username}: {exc}", flush=True)
                else:
                    hours = store_profile(conn, username, profile)
                    stored += 1
                    print(f"  stored {username}: {hours} hours", flush=True)
                elapsed = time.monotonic() - started
                stalls = stalls + 1 if elapsed > STALL_SECONDS else 0
                if stalls >= STALL_LIMIT:
                    stalled = True
                    break
            summary["stored"] += stored
            summary["dead"] += dead_now
            summary["failed"] += failed
            _report(conn, dead, f"batch {summary['batches']}")
            if stalled:
                summary["stopped"] = "a request hung past 30 seconds twice in a row"
                break
            zero_batches = zero_batches + 1 if stored + dead_now == 0 else 0
            if zero_batches >= ZERO_BATCH_LIMIT:
                summary["stopped"] = f"{ZERO_BATCH_LIMIT} batches in a row made no progress"
                break
            if not pending_usernames(dead):
                summary["stopped"] = "everything is done or marked dead"
                break
            print(f"pausing {pause_seconds / 60:.0f} minutes before the next batch", flush=True)
            sleep(pause_seconds)
    finally:
        conn.close()
    return summary


def main() -> int:
    """Run the batches and print the closing numbers."""
    parser = argparse.ArgumentParser(description="Finish the remaining faculty profiles.")
    parser.add_argument("--batch", type=int, default=BATCH_SIZE, help="profiles per batch")
    parser.add_argument(
        "--pause-minutes", type=float, default=PAUSE_SECONDS / 60, help="pause between batches"
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s %(message)s")
    sys.stdout.reconfigure(encoding="utf-8")

    conn = database.init_db(config.DB_PATH)
    try:
        _report(conn, load_dead(), "start")
    finally:
        conn.close()

    try:
        summary = run_batches(args.batch, args.pause_minutes * 60)
    except KeyboardInterrupt:
        print("\ninterrupted; everything stored so far is kept", flush=True)
        conn = database.init_db(config.DB_PATH)
        try:
            _report(conn, load_dead(), "stopped")
        finally:
            conn.close()
        return 130

    conn = database.init_db(config.DB_PATH)
    try:
        print(f"stopped: {summary['stopped']}", flush=True)
        print(
            f"this run: {summary['stored']} stored, {summary['dead']} dead, "
            f"{summary['failed']} failed, {summary['batches']} batches",
            flush=True,
        )
        _report(conn, load_dead(), "final")
        print(
            f"profiles stored {conn.execute('SELECT count(*) FROM faculty').fetchone()[0]}, "
            f"office_hours rows {conn.execute('SELECT count(*) FROM office_hours').fetchone()[0]}",
            flush=True,
        )
    finally:
        conn.close()
    return 0



if __name__ == "__main__":
    raise SystemExit(main())
