"""Refresh the stored data: python -m scrapers.run [--all | --faculty | --calendar | --deadlines].

Data is refreshed by hand, then the database is committed. Nothing runs on a schedule.
"""
import argparse
import logging
import time
from datetime import datetime, timezone

import config
import db as database
from agent.arabic import search_form
from agent.names import normalize_name
from scrapers import catalog, ccis, faculty, profiles, programs
from scrapers import calendar as ksu_calendar
from scrapers import deadlines as academic_table
from scrapers.base import CACHE_DIR, ScrapeError, fetch
from scrapers.sources import (
    CALENDAR,
    COURSE_CATALOG,
    DEADLINES,
    FACULTY_LISTS,
    FACULTY_LISTS_BY_ID,
    SOURCES,
    SOURCES_BY_ID,
)

logger = logging.getLogger("scrapers.run")
MAX_CONSECUTIVE_FAILURES = 3
MAX_PROFILE_SECONDS = 30.0
THROTTLE_MARKERS = ("HTTP 403", "HTTP 429", "timed out", "timeout")
TABLES = (
    "pages",
    "chunks",
    "faculty",
    "office_hours",
    "calendar_events",
    "academic_deadlines",
    "courses",
    "programs",
)

PROGRAM_SOURCE_IDS = tuple(
    source.id for source in SOURCES if source.id.startswith("ccis_programs_")
)

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


def scrape_pages(conn, only: set[str] | None = None, force: bool = False) -> int:
    """Scrape the college pages, returning the number of failures."""
    failures = 0
    for source in SOURCES:
        if only and source.id not in only:
            continue
        try:
            fetched = fetch(source.id, source.url, force=force)
            parsed = ccis.parse(fetched.html)
            count = store_page(conn, source.id, source.url, parsed)
            if source.id in PROGRAM_SOURCE_IDS:
                rows = programs.parse(fetched.html, programs.DEGREES[source.id], source.url)
                store_programs(conn, rows, source.url)
        except (ScrapeError, ccis.ParseError) as exc:
            logger.error("%s: %s (existing data kept)", source.id, exc)
            failures += 1
            continue
        note = " (not modified)" if fetched.not_modified else ""
        print(f"{source.id:22} {count:3} chunks  {parsed.title}{note}")
    return failures


def run(only: set[str] | None = None, force: bool = False) -> int:
    """Scrape the selected college pages and return the number of failures."""
    conn = database.init_db(config.DB_PATH)
    try:
        return scrape_pages(conn, only, force)
    finally:
        conn.close()


def store_calendar(conn, parsed: ksu_calendar.Calendar, source_url: str) -> int:
    """Replace the stored calendar with the freshly parsed one; return the row count.

    Only called after a successful parse, so a broken page never wipes the table.
    """
    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with conn:
        conn.execute("DELETE FROM calendar_events WHERE source_url = ?", (source_url,))
        conn.executemany(
            "INSERT INTO calendar_events (academic_year, term, title, weekday, date_gregorian,"
            " date_hijri, source_url, scraped_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    event.academic_year,
                    event.term,
                    event.title,
                    event.weekday,
                    event.date_gregorian,
                    event.date_hijri,
                    source_url,
                    scraped_at,
                )
                for event in parsed.events
            ],
        )
    return len(parsed.events)


def scrape_calendar(conn, only: set[str] | None = None, force: bool = False) -> int:
    """Scrape the academic calendar page, returning the number of failures."""
    if only and CALENDAR.id not in only:
        return 0
    try:
        fetched = fetch(CALENDAR.id, CALENDAR.url, force=force)
        parsed = ksu_calendar.parse_calendar(fetched.html)
    except (ScrapeError, ccis.ParseError) as exc:
        logger.error("%s: %s (existing data kept)", CALENDAR.id, exc)
        return 1
    count = store_calendar(conn, parsed, CALENDAR.url)
    mismatched = [event for event in parsed.events if not event.weekday_matches]
    if mismatched:
        logger.warning(
            "%s: %d rows whose weekday does not match the Gregorian date: %s",
            CALENDAR.id,
            len(mismatched),
            ", ".join(f"{event.title} {event.date_gregorian}" for event in mismatched),
        )
    note = " (not modified)" if fetched.not_modified else ""
    print(f"{CALENDAR.id:22} {count:3} events  {parsed.academic_year}{note}")
    return 0


def run_calendar(force: bool = False) -> int:
    """Refresh only the calendar table."""
    conn = database.init_db(config.DB_PATH)
    try:
        return scrape_calendar(conn, None, force)
    finally:
        conn.close()


def store_deadlines(
    conn,
    parsed: academic_table.Deadlines,
    source_url: str,
    scraped_at: str | None = None,
) -> int:
    """Replace the stored academic movements with the freshly parsed ones.

    Only called after a successful parse, so a broken page never wipes the table.
    """
    scraped_at = scraped_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    with conn:
        conn.execute("DELETE FROM academic_deadlines WHERE source_url = ?", (source_url,))
        conn.executemany(
            "INSERT INTO academic_deadlines (academic_year, term, title, kind, start_date,"
            " end_date, start_weekday, end_weekday, note, page_updated, source_url, scraped_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    row.academic_year,
                    row.term,
                    row.title,
                    row.kind,
                    row.start_date,
                    row.end_date,
                    row.start_weekday,
                    row.end_weekday,
                    row.note,
                    row.page_updated,
                    source_url,
                    scraped_at,
                )
                for row in parsed.rows
            ],
        )
    return len(parsed.rows)


def scrape_deadlines(conn, only: set[str] | None = None, force: bool = False) -> int:
    """Scrape the academic-movements page, returning the number of failures."""
    if only and DEADLINES.id not in only:
        return 0
    try:
        fetched = fetch(DEADLINES.id, DEADLINES.url, force=force)
        parsed = academic_table.parse_deadlines(fetched.html)
    except (ScrapeError, ccis.ParseError) as exc:
        logger.error("%s: %s (existing data kept)", DEADLINES.id, exc)
        return 1
    count = store_deadlines(conn, parsed, DEADLINES.url)
    mismatched = [row for row in parsed.rows if not row.weekday_matches]
    if mismatched:
        logger.warning(
            "%s: %d rows whose weekday does not match the Gregorian date: %s",
            DEADLINES.id,
            len(mismatched),
            ", ".join(f"{row.title} {row.start_date}" for row in mismatched),
        )
    note = " (not modified)" if fetched.not_modified else ""
    print(f"{DEADLINES.id:22} {count:3} movements  {parsed.academic_year}{note}")
    return 0


def run_deadlines(force: bool = False) -> int:
    """Refresh only the academic_deadlines table."""
    conn = database.init_db(config.DB_PATH)
    try:
        return scrape_deadlines(conn, None, force)
    finally:
        conn.close()


def store_courses(
    conn,
    parsed: catalog.Catalog,
    source_url: str,
    scraped_at: str | None = None,
) -> int:
    """Replace the stored courses with the freshly parsed ones.

    Only called after a successful parse, so a broken page never wipes the table.
    """
    scraped_at = scraped_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    with conn:
        conn.execute("DELETE FROM courses WHERE source_url = ?", (source_url,))
        conn.executemany(
            "INSERT INTO courses (code_display, code_norm, number, level, name_ar, hours_text,"
            " credit_hours, lecture_hours, lab_hours, tutorial_hours, prerequisites_text,"
            " prerequisite_codes, prerequisite_min_credit_hours, corequisites_text,"
            " corequisite_codes, description_ar, name_en, description_en, source_url, scraped_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    course.code_display,
                    course.code_norm,
                    course.number,
                    course.level,
                    course.name_ar,
                    course.hours_text,
                    course.credit_hours,
                    course.lecture_hours,
                    course.lab_hours,
                    course.tutorial_hours,
                    course.prerequisites_text,
                    catalog.codes_json(course.prerequisite_codes),
                    course.prerequisite_min_credit_hours,
                    course.corequisites_text,
                    catalog.codes_json(course.corequisite_codes),
                    course.description_ar,
                    course.name_en,
                    course.description_en,
                    source_url,
                    scraped_at,
                )
                for course in parsed.courses
            ],
        )
    return len(parsed.courses)


def scrape_catalog(conn, only: set[str] | None = None, force: bool = False) -> int:
    """Scrape the SE course catalog, returning the number of failures."""
    if only and COURSE_CATALOG.id not in only:
        return 0
    try:
        fetched = fetch(COURSE_CATALOG.id, COURSE_CATALOG.url, force=force)
        parsed = catalog.parse_catalog(fetched.html)
    except (ScrapeError, ccis.ParseError) as exc:
        logger.error("%s: %s (existing data kept)", COURSE_CATALOG.id, exc)
        return 1
    count = store_courses(conn, parsed, COURSE_CATALOG.url)
    for anomaly in parsed.anomalies:
        print(f"ANOMALY {anomaly}")
    note = " (not modified)" if fetched.not_modified else ""
    print(f"{COURSE_CATALOG.id:22} {count:3} courses{note}")
    return 0


def run_catalog(force: bool = False) -> int:
    """Refresh only the courses table."""
    conn = database.init_db(config.DB_PATH)
    try:
        return scrape_catalog(conn, None, force)
    finally:
        conn.close()

PROGRAMS_INSERT = """
INSERT INTO programs (
  degree, department, name_ar, name_en, requirements_ar, source_url, scraped_at
) VALUES (?, ?, ?, ?, ?, ?, ?)
"""


def store_programs(conn, rows, source_url: str, scraped_at: str | None = None) -> int:
    """Replace one program page's rows after a successful parse."""
    scraped_at = scraped_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    with conn:
        conn.execute("DELETE FROM programs WHERE source_url = ?", (source_url,))
        conn.executemany(
            PROGRAMS_INSERT,
            [
                (
                    row.degree,
                    row.department,
                    row.name_ar,
                    row.name_en,
                    row.requirements_ar,
                    source_url,
                    scraped_at,
                )
                for row in rows
            ],
        )
    return len(rows)


def scrape_programs(only: set[str] | None = None, force: bool = False) -> int:
    """Scrape the three degree-program pages, returning the number of failures."""
    conn = database.init_db(config.DB_PATH)
    failures = 0
    try:
        for source_id in PROGRAM_SOURCE_IDS:
            if only and source_id not in only:
                continue
            source = SOURCES_BY_ID[source_id]
            try:
                fetched = fetch(source.id, source.url, force=force)
                rows = programs.parse(fetched.html, programs.DEGREES[source.id], source.url)
            except (ScrapeError, ccis.ParseError) as exc:
                logger.error("%s: %s (existing data kept)", source.id, exc)
                failures += 1
                continue
            count = store_programs(conn, rows, source.url)
            print(f"{source.id:24} {count:3} programs  {programs.DEGREES[source.id]}")
    finally:
        conn.close()
    return failures

def table_counts(conn) -> dict[str, int]:
    """Row count per table, in a stable order."""
    return {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in TABLES}


def run_all(only: set[str] | None = None, force: bool = False) -> int:
    """Refresh the college pages, faculty lists, calendar, academic table and catalog."""
    conn = database.init_db(config.DB_PATH)
    try:
        failures = scrape_pages(conn, only, force)
        failures += scrape_lists(conn, only, force)[0]
        failures += scrape_calendar(conn, only, force)
        failures += scrape_deadlines(conn, only, force)
        failures += scrape_catalog(conn, only, force)
        counts = table_counts(conn)
    finally:
        conn.close()
    print("rows per table:")
    for table, count in counts.items():
        print(f"  {table:16} {count:6}")
    return failures


def main() -> None:
    """Parse arguments, run the scrape and exit non-zero on any failure."""
    parser = argparse.ArgumentParser(description="Refresh the stored CCIS data.")
    parser.add_argument("--only", help="Comma-separated source ids to scrape")
    parser.add_argument("--force", action="store_true", help="Ignore ETag/Last-Modified")
    parser.add_argument("--faculty", action="store_true", help="Scrape faculty lists and profiles")
    parser.add_argument(
        "--all",
        action="store_true",
        help="College pages, faculty lists, calendar, academic table, catalog and programs"
        " (not profiles), then row counts",
    )
    parser.add_argument("--calendar", action="store_true", help="Scrape only the academic calendar")
    parser.add_argument(
        "--deadlines", action="store_true", help="Scrape only the academic-movements table"
    )
    parser.add_argument("--catalog", action="store_true", help="Scrape only the SE course catalog")
    parser.add_argument("--programs", action="store_true", help="Scrape only degree programs")
    parser.add_argument("--max-profiles", type=int, help="Fetch at most this many profiles")
    parser.add_argument(
        "--resume", action="store_true", help="Skip profiles already downloaded"
    )

    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    only = {part.strip() for part in args.only.split(",") if part.strip()} if args.only else None
    if args.all:
        known = set(SOURCES_BY_ID) | set(FACULTY_LISTS_BY_ID) | {
            CALENDAR.id,
            DEADLINES.id,
            COURSE_CATALOG.id,
            *PROGRAM_SOURCE_IDS,
        }
    elif args.calendar:
        known = {CALENDAR.id}
    elif args.deadlines:
        known = {DEADLINES.id}
    elif args.catalog:
        known = {COURSE_CATALOG.id}
    elif args.faculty:
        known = set(FACULTY_LISTS_BY_ID)
    elif args.programs:
        known = set(PROGRAM_SOURCE_IDS)
    else:
        known = set(SOURCES_BY_ID)
    unknown = (only or set()) - known
    if unknown:
        raise SystemExit(f"unknown source id(s): {', '.join(sorted(unknown))}")
    if args.all:
        raise SystemExit(1 if run_all(only, args.force) else 0)
    if args.calendar:
        raise SystemExit(1 if run_calendar(args.force) else 0)
    if args.deadlines:
        raise SystemExit(1 if run_deadlines(args.force) else 0)
    if args.catalog:
        raise SystemExit(1 if run_catalog(args.force) else 0)
    if args.programs:
        raise SystemExit(1 if scrape_programs(only, args.force) else 0)
    if args.faculty:
        raise SystemExit(1 if run_faculty(only, args.force, args.max_profiles, args.resume) else 0)
    raise SystemExit(1 if run(only, args.force) else 0)


FACULTY_UPSERT = """
INSERT INTO faculty (
  username, name_ar, name_en, name_norm, rank, department,
  building, room, email, email_source, phone, profile_url, list_url, scraped_at
) VALUES (
  :username, :name_ar, :name_en, :name_norm, :rank, :department,
  :building, :room, :email, :email_source, :phone, :profile_url, :list_url, :scraped_at
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
  email_source = excluded.email_source,
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
    # A freshly scraped profile email always wins, then a list-page email, then the one
    # already stored. A profile page without an email must not drop a derived address.
    if profile is not None and profile.email:
        email, email_source = profile.email, "scraped"
    elif row is not None and row.email:
        email, email_source = row.email, "scraped"
    elif existing is not None and existing["email"]:
        email, email_source = existing["email"], existing["email_source"] or "scraped"
    else:
        email, email_source = None, None
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
        "email": email,
        "email_source": email_source,
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
    """Replace a person's office hours with the freshly parsed ones.

    An empty parse keeps the stored rows: a page that opened without its hours table is
    not evidence that the doctor published none, and the rule is that data is never wiped.
    """
    if not hours:
        kept = conn.execute(
            "SELECT COUNT(*) AS n FROM office_hours WHERE faculty_id = ?", (faculty_id,)
        ).fetchone()["n"]
        if kept:
            logger.warning(
                "no office hours parsed for faculty %s; keeping %d stored rows", faculty_id, kept
            )
        return kept
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


def _profile_cached(username: str) -> bool:
    """True when this profile already has a downloaded copy we can keep."""
    return (CACHE_DIR / f"fac_profile_{username}.html").exists()


def scrape_lists(
    conn,
    only: set[str] | None = None,
    force: bool = False,
    skipped: list[str] | None = None,
) -> tuple[int, list[str]]:
    """Scrape the faculty list pages, returning failures and the usernames found."""
    failures = 0
    usernames: list[str] = []
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
            if skipped is not None:
                skipped.append(f"{faculty_list.id}: {exc}")
            failures += 1
            continue
        for row in rows:
            store_faculty(conn, row.username, scraped_at, row=row)
        print(f"{faculty_list.id:22} {len(rows):3} people  {faculty_list.department}")
        usernames.extend(row.username for row in rows if row.profile_url)
    return failures, usernames


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
    try:
        list_failures, usernames = scrape_lists(conn, only, force, skipped)
        failures += list_failures
        usernames = list(dict.fromkeys(usernames))
        if resume:
            usernames = [name for name in usernames if not _profile_cached(name)]
        if max_profiles is not None:
            usernames = usernames[:max_profiles]
        consecutive = 0
        for index, username in enumerate(usernames, start=1):
            scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            started = time.monotonic()
            try:
                profile, fetched = profiles.fetch_profile(username, force=force)
            except (ScrapeError, ccis.ParseError) as exc:
                logger.error("profile %s: %s (existing data kept)", username, exc)
                skipped.append(f"profile {username}: {exc}")
                failures += 1
                if any(marker in str(exc) for marker in THROTTLE_MARKERS):
                    consecutive += 1
                    if consecutive >= MAX_CONSECUTIVE_FAILURES:
                        logger.error("host looks throttled; stopping after %d failures", consecutive)
                        print(f"STOPPED: {consecutive} consecutive failures, data kept")
                        break
                else:
                    consecutive = 0
                continue
            consecutive = 0
            faculty_id = store_faculty(conn, username, scraped_at, profile=profile)
            count = store_office_hours(
                conn, faculty_id, profile.office_hours, profile.source_url, scraped_at
            )
            note = " (not modified)" if fetched.not_modified else ""
            print(f"  profile {index:3}/{len(usernames)} {username:20} {count} hours{note}")
            if time.monotonic() - started > MAX_PROFILE_SECONDS:
                logger.error("profile %s took over %.0fs; stopping", username, time.monotonic() - started)
                print("STOPPED: host is slow, data kept")
                break
    finally:
        conn.close()
    for line in skipped:
        print(f"SKIPPED {line}")
    print(f"done: {failures} failures, {len(skipped)} skipped")
    return failures


if __name__ == "__main__":
    main()
