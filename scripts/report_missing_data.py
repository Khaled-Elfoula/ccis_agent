"""Report the people the faculty table cannot fully serve.

Writes data/people_missing_data.csv (one row per person with a gap) and prints
counts per department for five groups:

  a_no_username     no username or profile link, so no email is possible
  b_dead_profile    profile page returned 403/404 (email is derived_unverified)
  c_no_email        still no email for any reason
  d_no_office       no building and no room
  e_no_office_hours no office hours stored (normal, not a fault)

Usage: python scripts/report_missing_data.py
"""

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import db

sys.stdout.reconfigure(encoding="utf-8")

DEAD_PROFILES_PATH = config.ROOT / "db" / "cache" / "profiles_dead.json"
CSV_PATH = config.ROOT / "data" / "people_missing_data.csv"
GROUPS = (
    "a_no_username",
    "b_dead_profile",
    "c_no_email",
    "d_no_office",
    "e_no_office_hours",
)
COLUMNS = [
    "username",
    "name_ar",
    "name_en",
    "department",
    "rank",
    "email",
    "email_source",
    "profile_url",
]


def load_dead_profiles(path: str | Path | None = None) -> dict:
    """Read the usernames whose profile page failed, returning {} when the file is absent."""
    try:
        data = json.loads(Path(path or DEAD_PROFILES_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def gaps(row: dict, dead: dict) -> list[str]:
    """Return the group codes this person belongs to."""
    found = []
    if not (row["username"] or "").strip() or not (row["profile_url"] or "").strip():
        found.append("a_no_username")
    if row["email_source"] == "derived_unverified" or (row["username"] or "") in dead:
        found.append("b_dead_profile")
    if not (row["email"] or "").strip():
        found.append("c_no_email")
    if not (row["building"] or "").strip() and not (row["room"] or "").strip():
        found.append("d_no_office")
    if not row["has_hours"]:
        found.append("e_no_office_hours")
    return found


def load_people(db_path: str | Path | None = None) -> list[dict]:
    """Read every faculty row with the gaps it has."""
    conn = db.connect(db_path or config.DB_PATH)
    try:
        rows = conn.execute(
            "SELECT f.*, (SELECT COUNT(*) FROM office_hours h WHERE h.faculty_id = f.id) AS hours"
            " FROM faculty f ORDER BY f.department, f.username"
        ).fetchall()
    finally:
        conn.close()

    dead = load_dead_profiles()
    people = []
    for row in rows:
        record = dict(row)
        record["has_hours"] = bool(record.pop("hours"))
        record["gaps"] = gaps(record, dead)
        people.append(record)
    return people


def main() -> None:
    """Write the CSV and print the per-department summary."""
    people = load_people()

    by_department: dict[str, dict[str, int]] = {}
    for person in people:
        bucket = by_department.setdefault(
            person["department"] or "(none)", {"people": 0, **{g: 0 for g in GROUPS}}
        )
        bucket["people"] += 1
        for gap in person["gaps"]:
            bucket[gap] += 1

    with CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS + ["missing"])
        writer.writeheader()
        for person in people:
            if not person["gaps"]:
                continue
            writer.writerow(
                {
                    **{column: person[column] for column in COLUMNS},
                    "missing": ";".join(person["gaps"]),
                }
            )

    print(f"people: {len(people)}  csv: {CSV_PATH.relative_to(config.ROOT)}")
    print(f"{'Department':<10} {'People':>6}" + "".join(f"{g[0]:>6}" for g in GROUPS))
    for department in sorted(by_department):
        bucket = by_department[department]
        print(
            f"{department:<10} {bucket['people']:>6}"
            + "".join(f"{bucket[g]:>6}" for g in GROUPS)
        )
    totals = {g: sum(bucket[g] for bucket in by_department.values()) for g in GROUPS}
    print(f"{'TOTAL':<10} {len(people):>6}" + "".join(f"{totals[g]:>6}" for g in GROUPS))
    print("\nlegend: a=no username/profile link  b=dead profile  c=no email  d=no office  e=no office hours")

    for code in ("a_no_username", "c_no_email"):
        named = [p for p in people if code in p["gaps"]]
        print(f"\n{code}: {len(named)}")
        for person in named[:40]:
            print(f"  {person['name_ar'] or person['name_en']} | {person['department']} | {code}")


if __name__ == "__main__":
    main()
