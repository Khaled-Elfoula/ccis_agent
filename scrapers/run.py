"""Fill the knowledge base and faculty tables: python -m scrapers.run [--only ID] [--faculty]."""

import argparse
import logging
from datetime import datetime, timezone

import config
import db as database
from agent.arabic import search_form
from agent.names import normalize_name
from scrapers import ccis, faculty, profiles
from scrapers.base import CACHE_DIR, ScrapeError, fetch
from scrapers.sources import FACULTY_LISTS, FACULTY_LISTS_BY_ID, SOURCES, SOURCES_BY_ID

logger = logging.getLogger("scrapers.run")


def store_page(conn, source_id: str, url: str, parsed: ccis.ParsedPage) -> int:
    """Replace a page and its chunks in one transaction; return the chunk count."""
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with conn:
        row = conn.execute("SELECT id FROM pages WHERE url = ?", (url,)).fetchone()
        if row is None:
            page_id = conn.execute(
                "INSERT INTO pages (source_id, url, title, lang, scraped_at) VALUES (?, ?, ?, ?, ?)",
                (source_id, url, parsed.title, parsed.lang, scraped_at),
            ).lastrowid
        else:
            page_id = row["id"]
            conn.execute(
                "UPDATE pages SET source_id = ?, title = ?, lang = ?, scraped_at = ? WHERE id = ?",
                (source_id, parsed.title, parsed.lang, scraped_at, page_id),
            )
            conn.execute("DELETE FROM chunks WHERE page_id = ?", (page_id,))
        conn.executemany(
            "INSERT INTO chunks (page_id, text, text_norm) VALUES (?, ?, ?)",
            [(page_id, chunk, search_form(chunk)) for chunk in parsed.chunks],
        )
    return len(parsed.chunks)


def run(only: set[str] | None = None, force: bool = False) -> int:
    """Scrape the selected sources and return the number of failures."""
    conn = database.init_db(config.DB_PATH)
    failures = 0
    try:
        for source in SOURCES:
            if only and source.id not in only:
                continue
            try:
                fetched = fetch(source.id, source.url, force=force)
                parsed = ccis.parse(fetched.html)
                count = store_page(conn, source.id, source.url, parsed)
            except (ScrapeError, ccis.ParseError) as exc:
                logger.error("%s: %s (existing data kept)", source.id, exc)
                failures += 1
                continue
            note = " (not modified)" if fetched.not_modified else ""
            print(f"{source.id:22} {count:3} chunks  {parsed.title}{note}")
    finally:
        conn.close()
    return failures


def main() -> None:
    """Parse arguments, run the scrape and exit non-zero on any failure."""
    parser = argparse.ArgumentParser(description="Scrape CCIS pages into SQLite.")
    parser.add_argument("--only", help="Comma-separated source ids to scrape")
    parser.add_argument("--force", action="store_true", help="Ignore ETag/Last-Modified")
    parser.add_argument("--faculty", action="store_true", help="Scrape faculty lists and profiles")
    parser.add_argument("--max-profiles", type=int, help="Fetch at most this many profiles")
    parser.add_argument(
        "--resume", action="store_true", help="Skip profiles already fetched today"
    )

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    only = {part.strip() for part in args.only.split(",") if part.strip()} if args.only else None
    known = FACULTY_LISTS_BY_ID if args.faculty else SOURCES_BY_ID
    unknown = (only or set()) - set(known)
    if unknown:
        raise SystemExit(f"unknown source id(s): {', '.join(sorted(unknown))}")
    if args.faculty:
        raise SystemExit(1 if run_faculty(only, args.force, args.max_profiles, args.resume) else 0)
    raise SystemExit(1 if run(only, args.force) else 0)


FACULTY_UPSERT = """
INSERT INTO faculty (
  username, name_ar, name_en, name_norm, rank, department,
  building, room, email, phone, profile_url, list_url, scraped_at
) VALUES (
  :username, :name_ar, :name_en, :name_norm, :rank, :department,
  :building, :room, :email, :phone, :profile_url, :list_url, :scraped_at
)
ON CONFLICT(username) DO UPDATE SET
  name_ar = excluded.name_ar,
  name_en = excluded.name_en,
  name_norm = excluded.name_norm,
  rank = excluded.rank,
  department = excluded.department,
  building = excluded.building,
  room = excluded.room,
  email = excluded.email,
  phone = excluded.phone,
  profile_url = excluded.profile_url,
  list_url = excluded.list_url,
  scraped_at = excluded.scraped_at
"""


def store_faculty(
    conn,
    username: str,
    scraped_at: str,
    row: faculty.FacultyRow | None = None,
    profile: profiles.Profile | None = None,
) -> int:
    """Insert or update one person, keeping stored values the new parse lacks."""
    existing = conn.execute(
        "SELECT * FROM faculty WHERE username = ?", (username,)
    ).fetchone()

    def pick(*values):
        return next((value for value in values if value), None)

    name_ar = pick(
        row.name_ar if row else None,
        profile.name_ar if profile else None,
        existing["name_ar"] if existing else None,
    )
    name_en = pick(
        row.name_en if row else None,
        profile.name_en if profile else None,
        existing["name_en"] if existing else None,
    )
    values = {
        "username": username,
        "name_ar": name_ar,
        "name_en": name_en,
        "name_norm": normalize_name(name_ar or name_en or ""),
        "rank": pick(
            profile.rank if profile else None,
            row.rank if row else None,
            existing["rank"] if existing else None,
        ),
        "department": pick(
            row.department if row else None,
            existing["department"] if existing else None,
        ),
        "building": pick(
            row.building if row else None,
            profile.building if profile else None,
            existing["building"] if existing else None,
        ),
        "room": pick(
            row.room if row else None,
            profile.room if profile else None,
            existing["room"] if existing else None,
        ),
        "email": pick(
            profile.email if profile else None,
            existing["email"] if existing else None,
        ),
        "phone": pick(
            row.phone if row else None,
            existing["phone"] if existing else None,
        ),
        "profile_url": pick(
            row.profile_url if row else None,
            profile.source_url if profile else None,
            existing["profile_url"] if existing else None,
        ),
        "list_url": pick(
            row.list_url if row else None,
            existing["list_url"] if existing else None,
        ),
        "scraped_at": scraped_at,
    }
    with conn:
        conn.execute(FACULTY_UPSERT, values)
    return conn.execute(
        "SELECT id FROM faculty WHERE username = ?", (username,)
    ).fetchone()["id"]


def store_office_hours(conn, faculty_id: int, hours, source_url: str, scraped_at: str) -> int:
    """Replace a person's office hours with the freshly parsed ones."""
    with conn:
        conn.execute("DELETE FROM office_hours WHERE faculty_id = ?", (faculty_id,))
        conn.executemany(
            "INSERT INTO office_hours (faculty_id, day, start, end, source_url, scraped_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [
                (faculty_id, hour.day, hour.start, hour.end, source_url, scraped_at)
                for hour in hours
            ],
        )
    return len(hours)


def _profile_fetched_today(username: str) -> bool:
    """True when this profile already has a cached copy from today."""
    cache_file = CACHE_DIR / f"fac_profile_{username}.html"
    if not cache_file.exists():
        return False
    fetched = datetime.fromtimestamp(cache_file.stat().st_mtime, timezone.utc)
    return fetched.date() == datetime.now(timezone.utc).date()


def run_faculty(
    only: set[str] | None = None,
    force: bool = False,
    max_profiles: int | None = None,
    resume: bool = False,
) -> int:
    """Scrape the faculty lists and profiles, returning the number of failures."""
    conn = database.init_db(config.DB_PATH)
    failures = 0
    skipped: list[str] = []
    usernames: list[str] = []
    try:
        for faculty_list in FACULTY_LISTS:
            if only and faculty_list.id not in only:
                continue
            scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            try:
                fetched = fetch(faculty_list.id, faculty_list.url, force=force)
                rows = faculty.parse_list(
                    fetched.html, faculty_list.department, faculty_list.url
                )
            except (ScrapeError, ccis.ParseError) as exc:
                logger.error("%s: %s (existing data kept)", faculty_list.id, exc)
                skipped.append(f"{faculty_list.id}: {exc}")
                failures += 1
                continue
            for row in rows:
                store_faculty(conn, row.username, scraped_at, row=row)
            print(f"{faculty_list.id:22} {len(rows):3} people  {faculty_list.department}")
            usernames.extend(row.username for row in rows)

        usernames = list(dict.fromkeys(usernames))
        if resume:
            usernames = [name for name in usernames if not _profile_fetched_today(name)]
        if max_profiles is not None:
            usernames = usernames[:max_profiles]
        for index, username in enumerate(usernames, start=1):
            scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            try:
                profile, fetched = profiles.fetch_profile(username, force=force)
            except (ScrapeError, ccis.ParseError) as exc:
                logger.error("profile %s: %s (existing data kept)", username, exc)
                skipped.append(f"profile {username}: {exc}")
                failures += 1
                continue
            faculty_id = store_faculty(conn, username, scraped_at, profile=profile)
            count = store_office_hours(
                conn, faculty_id, profile.office_hours, profile.source_url, scraped_at
            )
            note = " (not modified)" if fetched.not_modified else ""
            print(f"  profile {index:3}/{len(usernames)} {username:20} {count} hours{note}")
    finally:
        conn.close()
    for line in skipped:
        print(f"SKIPPED {line}")
    print(f"done: {failures} failures, {len(skipped)} skipped")
    return failures


if __name__ == "__main__":
    main()
