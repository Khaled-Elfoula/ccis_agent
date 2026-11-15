"""Fill missing faculty emails from the username pattern, labelled by evidence.

A scraped email is never touched. Only a person with no email and a valid username
gets a derived address, and the row records where it came from:

  scraped            read off the doctor's profile page
  derived            built as username@ksu.edu.sa, not confirmed on the page
  derived_unverified the same, but the profile page returned 403/404

The username pattern is only trusted when the emails we did scrape follow it
almost always, so this script stops before writing anything below MIN_MATCH_RATE.

Usage: python scripts/fill_emails.py [--dry-run]
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import db

sys.stdout.reconfigure(encoding="utf-8")

PATTERN = "{username}@ksu.edu.sa"
VALID_USERNAME = re.compile(r"^[A-Za-z0-9._-]+$")
MIN_MATCH_RATE = 95.0
DEAD_PROFILES_PATH = config.ROOT / "db" / "cache" / "profiles_dead.json"


def derive_email(username: str | None) -> str | None:
    """Return username@ksu.edu.sa, or None when the username cannot make an address."""
    if not username or not VALID_USERNAME.match(username):
        return None
    return PATTERN.format(username=username.lower())


def load_dead_profiles(path: str | Path | None = None) -> dict:
    """Read the usernames whose profile page failed, returning {} when the file is absent."""
    try:
        data = json.loads(Path(path or DEAD_PROFILES_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def scraped_match_rate(conn) -> tuple[int, int, float]:
    """Compare the emails we scraped with the username pattern: (total, matched, rate)."""
    rows = conn.execute(
        "SELECT username, email FROM faculty"
        " WHERE email IS NOT NULL AND TRIM(email) <> ''"
        " AND (email_source = 'scraped' OR email_source IS NULL)"
    ).fetchall()
    total = len(rows)
    matched = sum(
        1
        for row in rows
        if row["email"].strip().lower() == PATTERN.format(username=row["username"]).lower()
    )
    return total, matched, (matched / total * 100) if total else 0.0


def fill(
    db_path=None,
    dry_run: bool = False,
    dead_path: str | Path | None = None,
    dead: dict | None = None,
) -> dict:
    """Derive the missing emails and return a summary of what changed."""
    conn = db.init_db(db_path or config.DB_PATH)
    try:
        total, matched, rate = scraped_match_rate(conn)
        summary = {
            "scraped": total,
            "matched": matched,
            "rate": rate,
            "derived": 0,
            "derived_unverified": 0,
            "skipped_invalid": [],
            "still_missing": 0,
            "aborted": False,
            "unverified": [],
        }
        print(f"scraped emails: {total}, matching the pattern: {matched} ({rate:.2f}%)")
        if rate < MIN_MATCH_RATE:
            summary["aborted"] = True
            print(f"ABORTED: {rate:.2f}% is below {MIN_MATCH_RATE:.0f}%; nothing was filled in.")
            return summary

        dead = load_dead_profiles(dead_path) if dead is None else dead
        rows = conn.execute(
            "SELECT id, username, name_ar, name_en, department FROM faculty"
            " WHERE email IS NULL OR TRIM(email) = '' ORDER BY department, username"
        ).fetchall()

        changes = []
        unverified = []
        for row in rows:
            email = derive_email(row["username"])
            if email is None:
                summary["skipped_invalid"].append((row["name_ar"] or row["name_en"], row["username"]))
                continue
            source = "derived_unverified" if row["username"] in dead else "derived"
            changes.append((email, source, row["id"]))
            summary[source] += 1
            if source == "derived_unverified":
                unverified.append(
                    (
                        row["name_ar"] or row["name_en"],
                        row["department"],
                        row["username"],
                        email,
                        dead.get(row["username"], {}).get("status", "?"),
                    )
                )
        summary["unverified"] = unverified

        if not dry_run and changes:
            with conn:
                conn.executemany(
                    "UPDATE faculty SET email = ?, email_source = ?"
                    " WHERE id = ? AND (email IS NULL OR TRIM(email) = '')",
                    changes,
                )
        summary["still_missing"] = conn.execute(
            "SELECT COUNT(*) AS n FROM faculty WHERE email IS NULL OR TRIM(email) = ''"
        ).fetchone()["n"]
        summary["dry_run"] = dry_run
        return summary
    finally:
        conn.close()


def main() -> None:
    """Run the fill and print the summary."""
    parser = argparse.ArgumentParser(description="Derive missing faculty emails from usernames.")
    parser.add_argument("--dry-run", action="store_true", help="Report without writing anything")
    args = parser.parse_args()

    summary = fill(dry_run=args.dry_run)
    if summary.get("aborted"):
        raise SystemExit(1)
    verb = "would derive" if summary["dry_run"] else "derived"
    print(f"{verb}: {summary['derived']} (derived), {summary['derived_unverified']} (derived_unverified)")
    for name, username in summary["skipped_invalid"]:
        print(f"  no address possible: {name} | username={username!r}")
    print(f"people still without an email: {summary['still_missing']}")

    if summary["unverified"]:
        print("\nderived_unverified (profile page returned 403/404):")
        for name, department, username, email, status in summary["unverified"]:
            print(f"  {name} | {department} | {username} | {email} | status {status}")


if __name__ == "__main__":
    main()
