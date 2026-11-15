"""Evidence check: do the scraped emails follow username@ksu.edu.sa?

Read-only. Looks only at emails we actually scraped (email_source = 'scraped'),
prints how many there are, how many match the username pattern exactly, and lists
every mismatch. Nothing is written to the database.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import db

sys.stdout.reconfigure(encoding="utf-8")

PATTERN = "{username}@ksu.edu.sa"


def main() -> None:
    """Compare every scraped email with the username pattern and print the result."""
    conn = db.connect(config.DB_PATH)
    try:
        rows = conn.execute(
            "SELECT username, name_ar, name_en, email FROM faculty"
            " WHERE email IS NOT NULL AND TRIM(email) <> ''"
            " AND (email_source = 'scraped' OR email_source IS NULL) ORDER BY username"
        ).fetchall()
        total_people = conn.execute("SELECT COUNT(*) AS n FROM faculty").fetchone()["n"]
        by_source = {
            row["email_source"] or "unlabelled": row["n"]
            for row in conn.execute(
                "SELECT email_source, COUNT(*) AS n FROM faculty GROUP BY email_source"
            )
        }
    finally:
        conn.close()

    scraped = len(rows)
    exact = 0
    case_insensitive = 0
    mismatches = []
    upper_usernames = []
    for row in rows:
        username = row["username"]
        expected = PATTERN.format(username=username)
        email = row["email"].strip()
        if username != username.lower():
            upper_usernames.append((username, email))
        if email == expected:
            exact += 1
        if email.lower() == expected.lower():
            case_insensitive += 1
        else:
            mismatches.append((row["name_ar"] or row["name_en"], username, email, expected))

    print(f"faculty rows: {total_people}  by email_source: {by_source}")
    print(f"scraped emails: {scraped} ({scraped / total_people * 100:.1f}% of rows)")
    print(f"match username@ksu.edu.sa exactly: {exact}/{scraped}")
    if scraped:
        print(
            f"match ignoring case: {case_insensitive}/{scraped} "
            f"({case_insensitive / scraped * 100:.2f}%)"
        )
    print(f"usernames that are not already lowercase: {len(upper_usernames)}")
    for username, email in upper_usernames:
        print(f"  case-only: username={username} email={email}")
    print(f"mismatches: {len(mismatches)}")
    for name, username, email, expected in mismatches:
        print(f"  {name} | {username} | scraped={email} | expected={expected}")

    if not scraped:
        print("RESULT: no scraped emails to check")
        return
    rate = case_insensitive / scraped * 100
    print(f"RESULT: match rate {rate:.2f}% -> {'PASS (>=95%)' if rate >= 95 else 'FAIL (<95%)'}")


if __name__ == "__main__":
    main()
