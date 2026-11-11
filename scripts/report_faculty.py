"""Report on the faculty table: counts by department and rank, contact coverage."""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import db

sys.stdout.reconfigure(encoding="utf-8")


def main() -> None:
    """Print a markdown report of the scraped faculty data."""
    conn = db.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    total = conn.execute("SELECT COUNT(*) AS n FROM faculty").fetchone()["n"]
    unique = conn.execute("SELECT COUNT(DISTINCT username) AS n FROM faculty").fetchone()["n"]
    repeated = conn.execute(
        "SELECT COUNT(*) AS n FROM"
        " (SELECT name_norm FROM faculty GROUP BY name_norm HAVING COUNT(*) > 1)"
    ).fetchone()["n"]

    print("# Faculty data report\n")
    print(f"- rows: {total}")
    print(f"- unique usernames: {unique} (duplicates: {total - unique})")
    print(f"- repeated normalised names: {repeated}")
    print()

    print("## By department\n")
    print("| Department | People | With email | With office | With office hours |")
    print("|---|---|---|---|---|")
    for row in conn.execute(
        "SELECT department, COUNT(*) AS people,"
        " SUM(email IS NOT NULL) AS emails,"
        " SUM(COALESCE(building, room) IS NOT NULL) AS offices,"
        " SUM(id IN (SELECT faculty_id FROM office_hours)) AS hours"
        " FROM faculty GROUP BY department ORDER BY department"
    ):
        print(
            f"| {row['department']} | {row['people']} | {row['emails']} "
            f"| {row['offices']} | {row['hours']} |"
        )
    print()

    print("## By rank\n")
    print("| Rank | People |")
    print("|---|---|")
    for row in conn.execute(
        "SELECT COALESCE(rank, '(unknown)') AS rank, COUNT(*) AS n"
        " FROM faculty GROUP BY rank ORDER BY n DESC"
    ):
        print(f"| {row['rank']} | {row['n']} |")
    print()

    print("## People with both an email and an office\n")
    print("| Name | Department | Office | Email | Office hours rows |")
    print("|---|---|---|---|---|")
    rows = conn.execute(
        "SELECT f.name_ar, f.name_en, f.department, f.building, f.room, f.email,"
        " (SELECT COUNT(*) FROM office_hours h WHERE h.faculty_id = f.id) AS hours"
        " FROM faculty f"
        " WHERE f.email IS NOT NULL AND COALESCE(f.building, f.room) IS NOT NULL"
        " ORDER BY f.department, f.username LIMIT 10"
    )
    for row in rows:
        office = "|".join(part for part in (row["building"], row["room"]) if part)
        name = row["name_ar"] or row["name_en"]
        print(
            f"| {name} | {row['department']} | {office} | {row['email']} | {row['hours']} |"
        )
    conn.close()


if __name__ == "__main__":
    main()
