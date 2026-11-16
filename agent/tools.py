"""Gemini function declarations and their SQLite-backed implementations."""

import json
import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from google.genai import types

import config
import db as database
from agent.arabic import fts_query, normalize_arabic, search_form
from agent.names import MAX_CANDIDATES, load_overrides, match_faculty, normalize_name

MAX_LIMIT = 20
DEFAULT_LIMIT = 5
MAX_PASSAGE_CHARS = 1500


def search_kb(
    query: str,
    lang: str | None = None,
    limit: int = DEFAULT_LIMIT,
    db_path: str | Path | None = None,
) -> list[dict] | dict:
    """Search indexed CCIS pages with FTS5 and return passages with their sources."""
    if not query or not query.strip():
        return {"error": "empty query"}
    match = fts_query(query)
    if not match:
        return {"error": "query has no searchable words"}
    try:
        size = max(1, min(int(limit), MAX_LIMIT))
    except (TypeError, ValueError):
        size = DEFAULT_LIMIT

    sql = [
        "SELECT c.text, p.title, p.url AS source_url, p.scraped_at, bm25(chunks_fts) AS rank",
        "FROM chunks_fts",
        "JOIN chunks c ON c.id = chunks_fts.rowid",
        "JOIN pages p ON p.id = c.page_id",
        "WHERE chunks_fts MATCH ?",
    ]
    params: list[object] = [match]
    if lang in ("ar", "en"):
        sql.append("AND p.lang = ?")
        params.append(lang)
    sql.append("ORDER BY rank LIMIT ?")
    params.append(size)

    try:
        conn = database.connect(db_path or config.DB_PATH)
    except sqlite3.Error as exc:
        return {"error": f"knowledge base unavailable: {exc}"}
    try:
        rows = conn.execute(" ".join(sql), params).fetchall()
    except sqlite3.Error as exc:
        return {"error": f"search failed: {exc}"}
    finally:
        conn.close()

    if not rows:
        return {"error": "no results found"}
    return [
        {
            "text": row["text"][:MAX_PASSAGE_CHARS],
            "title": row["title"],
            "source_url": row["source_url"],
            "scraped_at": row["scraped_at"],
        }
        for row in rows
    ]


SEARCH_KB_DECLARATION = types.FunctionDeclaration(
    name="search_kb",
    description=(
        "Search the College of Computer and Information Sciences knowledge base: college overview and "
        "departments, admission steps, transfer conditions, forms and regulations, and the college's "
        "contact details and academic affairs agency. Use it for general factual questions about the "
        "college before answering; do not answer such questions from memory. For a list of degree "
        "programmes, or for one programme's admission conditions, use list_programs instead. "
        "Queries may be Arabic or English. "
        "Returns a ranked list of passages, each with the passage text, the page title, the source_url to "
        "cite and the scraped_at date to check for staleness. "
        "Returns {\"error\": ...} when nothing matches, which means you must not invent an answer."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query": types.Schema(
                type=types.Type.STRING,
                description="What to look for, as a few keywords or a short phrase in the user's language.",
            ),
            "lang": types.Schema(
                type=types.Type.STRING,
                enum=["ar", "en"],
                description="Restrict results to Arabic or English pages. Omit to search all pages.",
            ),
            "limit": types.Schema(
                type=types.Type.INTEGER,
                description=f"How many passages to return, 1 to {MAX_LIMIT}. Default {DEFAULT_LIMIT}.",
            ),
        },
        required=["query"],
    ),
)


def _contact(row: dict) -> dict:
    """Shape one faculty row for the model: contact fields only."""
    record = {
        "name": row.get("name_ar") or row.get("name_en"),
        "rank": row.get("rank"),
        "department": row.get("department"),
        "building": row.get("building"),
        "room": row.get("room"),
        "email": row.get("email"),
        "profile_url": row.get("profile_url"),
    }
    stamp = row.get("scraped_at")
    if stamp and _stale(stamp):
        record["stale"] = True
        record["stale_note"] = (
            f"last updated {str(stamp)[:10]}, more than {config.STALE_AFTER_DAYS} days ago"
        )
    if row.get("name_ar") and row.get("name_en"):
        record["name_en"] = row["name_en"]
    return record


def _candidates_result(rows: list[dict]) -> dict:
    """Shape an unconfirmed lookup so it can never be read as an answer."""
    contacts = [_contact(row) for row in rows]
    names = [contact["name"] or contact.get("name_en") or "unknown" for contact in contacts]
    return {
        "status": "candidates, not confirmed",
        "message": (
            f"Candidates, not confirmed — did you mean {' or '.join(names)}? "
            "Ask the user which one they mean; never pick one yourself."
        ),
        "candidates": contacts,
    }


def _lookup(person_or_role: str, db_path=None) -> tuple[dict | None, list[dict], dict | None]:
    """Resolve a name to one faculty row, or to candidates, or to an error."""
    if not person_or_role or not person_or_role.strip():
        return None, [], {"error": "empty name"}
    try:
        conn = database.connect(db_path or config.DB_PATH)
        try:
            rows = conn.execute("SELECT * FROM faculty").fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return None, [], {"error": f"faculty directory unavailable: {exc}"}

    records = [dict(row) for row in rows]
    if not records:
        return None, [], {"error": "faculty directory is empty"}

    override = load_overrides().get(normalize_name(person_or_role))
    if override:
        record = next((row for row in records if row["username"] == override), None)
        if record is not None:
            return record, [], None

    match = match_faculty(person_or_role, records, limit=MAX_CANDIDATES)
    if match.accepted is not None:
        return match.accepted, [], None
    if match.candidates:
        return None, match.candidates, None
    return None, [], {"error": "no match found"}


def find_contact(
    person_or_role: str,
    db_path: str | Path | None = None,
) -> list[dict] | dict:
    """Look a person up by name and return their contact details (spec.md section 8)."""
    record, candidates, error = _lookup(person_or_role, db_path)
    if error is not None:
        return error
    if record is not None:
        return [_contact(record)]
    return _candidates_result(candidates)


FIND_CONTACT_DECLARATION = types.FunctionDeclaration(
    name="find_contact",
    description=(
        "Look up a college member by name and return their contact details: rank, department, office "
        "(building and room), email and the profile link. Arabic spelling variants, missing "
        "middle names and titles such as د. or أ.د. are handled. "
        "Returns a list with exactly one person when the match is confirmed. "
        "When the name is not confirmed it returns {status: 'candidates, not confirmed', message, "
        "candidates: [...]}: ask the user 'did you mean X?' and never present a candidate as the answer. "
        "Returns {\"error\": ...} when nobody matches, which means you must say you "
        "could not find that person instead of inventing contact details."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "person_or_role": types.Schema(
                type=types.Type.STRING,
                description="The name of the person, in Arabic or English, as the user wrote it.",
            ),
        },
        required=["person_or_role"],
    ),
)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

DAY_ALIASES = {
    "sunday": "Sunday", "sun": "Sunday", "الأحد": "Sunday", "الاحد": "Sunday", "أحد": "Sunday",
    "monday": "Monday", "mon": "Monday", "الاثنين": "Monday", "الإثنين": "Monday", "اثنين": "Monday",
    "tuesday": "Tuesday", "tue": "Tuesday", "الثلاثاء": "Tuesday", "ثلاثاء": "Tuesday",
    "wednesday": "Wednesday", "wed": "Wednesday", "الأربعاء": "Wednesday", "الاربعاء": "Wednesday",
    "thursday": "Thursday", "thu": "Thursday", "الخميس": "Thursday", "خميس": "Thursday",
    "friday": "Friday", "fri": "Friday", "الجمعة": "Friday",
    "saturday": "Saturday", "sat": "Saturday", "السبت": "Saturday",
}

CALENDAR_LIMIT = 8
TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def normalize_day(value: str | None) -> str | None:
    """Map an English or Arabic day name to its canonical English name."""
    if not value:
        return None
    return DAY_ALIASES.get(str(value).strip().lower())


def _office_hours(faculty_id: int, db_path=None) -> list[dict]:
    """Return a person's stored office-hours rows in page order, or []."""
    try:
        conn = database.connect(db_path or config.DB_PATH)
        try:
            rows = conn.execute(
                "SELECT day, start, end, source_url, scraped_at FROM office_hours"
                " WHERE faculty_id = ? ORDER BY id",
                (faculty_id,),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return []
    return [dict(row) for row in rows]


def _readable_hour(row: dict) -> bool:
    """True for a stored row with a real HH:MM range that ends after it starts."""
    start = TIME_RE.match(str(row.get("start") or ""))
    end = TIME_RE.match(str(row.get("end") or ""))
    if start is None or end is None:
        return False
    return (end.group(1), end.group(2)) > (start.group(1), start.group(2))


def get_office_hours(
    faculty_name: str,
    day: str | None = None,
    db_path: str | Path | None = None,
) -> dict:
    """Return a person's published office hours, or their contact details when they have none."""
    record, candidates, error = _lookup(faculty_name, db_path)
    if error is not None:
        return error
    if record is None:
        return _candidates_result(candidates)

    wanted = None
    if day and str(day).strip():
        wanted = normalize_day(day)
        if wanted is None:
            return {"error": f"unknown day: {day}"}

    person = _contact(record)
    rows = _office_hours(record["id"], db_path)
    if not rows:
        return {
            "no_office_hours": True,
            "faculty": person,
            "email": person.get("email"),
            "office": {"building": person.get("building"), "room": person.get("room")},
            "profile_url": person.get("profile_url"),
        }

    readable = [row for row in rows if _readable_hour(row)]
    selected = [
        row for row in readable if wanted is None or normalize_day(row["day"]) == wanted
    ]
    result = {
        "faculty": person,
        "office_hours": [
            {"day": row["day"], "start": row["start"], "end": row["end"]} for row in selected
        ],
    }
    stamp = rows[0].get("scraped_at")
    if stamp and _stale(stamp):
        result["stale"] = True
        result["stale_note"] = (
            f"last updated {str(stamp)[:10]}, more than {config.STALE_AFTER_DAYS} days ago"
        )
    if wanted is not None:
        result["day"] = wanted
    if len(readable) < len(rows):
        result["unreadable_rows"] = len(rows) - len(readable)
    return result


GET_OFFICE_HOURS_DECLARATION = types.FunctionDeclaration(
    name="get_office_hours",
    description=(
        "Return a faculty member's published office hours, read from their profile page. "
        "Read-only: it never books, reserves or sends anything, and it does not estimate or compute "
        "free times. "
        "Returns {faculty, office_hours: [{day, start, end}]}, plus day when the user asked for one "
        "day; unreadable_rows counts published rows whose times could not be read. "
        "When the doctor has no published office hours at all it returns {no_office_hours: true, "
        "faculty, email, office, profile_url} instead, so you can give their email and office. "
        "When the name is not confirmed it returns {status: 'candidates, not confirmed', message, "
        "candidates: [...]}: ask the user 'did you mean X?' and never present a candidate as the "
        "answer. Returns {\"error\": ...} when nobody matches."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "faculty_name": types.Schema(
                type=types.Type.STRING,
                description="The faculty member's name in Arabic or English, as the user wrote it.",
            ),
            "day": types.Schema(
                type=types.Type.STRING,
                description="Optional day name, English or Arabic, e.g. Sunday or الأحد. Omit for the whole week.",
            ),
        },
        required=["faculty_name"],
    ),
)


def _calendar_rows(db_path=None) -> tuple[list[dict], dict | None]:
    """Read every stored calendar row, or return the error to give the model."""
    try:
        conn = database.connect(db_path or config.DB_PATH)
        try:
            rows = conn.execute(
                "SELECT title, term, academic_year, weekday, date_gregorian, date_hijri,"
                " source_url, scraped_at FROM calendar_events ORDER BY date_gregorian, id"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return [], {"error": f"calendar unavailable: {exc}"}
    return [dict(row) for row in rows], None


def _deadline_rows(db_path=None) -> tuple[list[dict], dict | None]:
    """Read every stored academic movement, or return the error to give the model."""
    try:
        conn = database.connect(db_path or config.DB_PATH)
        try:
            rows = conn.execute(
                "SELECT title, term, academic_year, kind, start_date, end_date, start_weekday,"
                " end_weekday, note, page_updated, source_url, scraped_at"
                " FROM academic_deadlines ORDER BY start_date, id"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return [], {"error": f"academic deadlines unavailable: {exc}"}
    return [dict(row) for row in rows], None


def _token_matches(wanted: str, tokens: list[str]) -> bool:
    """True when a query token is a title token, or a longer form of one."""
    return any(
        wanted == token
        or (len(wanted) >= 3 and (token.startswith(wanted) or wanted.startswith(token)))
        for token in tokens
    )


def _title_matches(topic: str, title: str) -> bool:
    """True when every word of the topic appears in the event title."""
    wanted = [token for token in search_form(topic).split() if len(token) > 1]
    have = search_form(title).split()
    return bool(wanted) and all(_token_matches(token, have) for token in wanted)


# A topic a student may use, mapped to the title phrases it stands for. The specific
# phrases come first, so "انسحاب من الجامعة" never falls through to the general
# withdrawal entry. The general one covers a bare "withdraw", which is ambiguous: the
# agent must then give both a course withdrawal and a university withdrawal.
TOPIC_SYNONYMS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (
        ("انسحاب من الجامعة", "withdraw from the university", "withdraw from university"),
        ("الانسحاب من الجامعة",),
    ),
    (
        (
            "انسحاب من المقرر",
            "انسحاب من مقرر",
            "withdraw from a course",
            "withdraw a course",
            "drop a course",
            "drop the course",
            "حذف مقرر",
        ),
        ("الاعتذار عن مقرر دراسي", "الحذف والإضافة"),
    ),
    (
        ("حذف و اضافه", "add drop", "add/drop", "add and drop"),
        ("الحذف والإضافة",),
    ),
    (
        ("اعتذار", "انسحاب", "withdraw", "withdrawal", "drop"),
        ("الاعتذار عن مقرر دراسي", "الحذف والإضافة", "الانسحاب من الجامعة"),
    ),
    (("تاجيل", "postpone", "defer"), ("تأجيل الفصل الدراسي",)),
    (
        ("عذر غياب", "حرمان", "absence excuse", "deprivation"),
        ("تقديم أعذار الغياب",),
    ),
    (("تحويل داخلي", "internal transfer"), ("التحويل الداخلي",)),
    (
        ("الاختبارات النهائيه", "final exam", "final exams", "finals"),
        ("الاختبارات النهائية",),
    ),
    (
        ("اختبار بديل", "make up exam", "makeup exam", "make-up exam"),
        ("تقديم طلب اختبار نهائي بديل",),
    ),
    (("اعفاء", "exemption", "exempt"), ("الاعفاء عن مقرر دراسي",)),
    (("اجازه", "holiday", "holidays", "break", "vacation"), ("إجازة",)),
)


def _topic_phrases(topic: str) -> list[str]:
    """Expand a topic through the synonym table, or return it unchanged."""
    tokens = search_form(topic).split()
    for triggers, titles in TOPIC_SYNONYMS:
        for trigger in triggers:
            wanted = search_form(trigger).split()
            if wanted and all(_token_matches(token, tokens) for token in wanted):
                return list(titles)
    return [topic]


def _matches_topic(topic: str | None, title: str) -> bool:
    """True when the topic, once expanded, matches this title."""
    if not topic or not str(topic).strip():
        return True
    return any(_title_matches(phrase, title) for phrase in _topic_phrases(topic))


def _term_matches(term: str, stored: str | None) -> bool:
    """True when the asked term is the stored term, allowing a shorter form."""
    wanted, have = search_form(term), search_form(stored or "")
    return bool(wanted) and bool(have) and (wanted in have or have in wanted)


def _flag(value) -> bool:
    """Read a model boolean that may arrive as a string."""
    return value is True or str(value).strip().lower() in ("true", "1", "yes")


def _calendar_kind(title: str) -> str:
    """Classify a single-date calendar row by its title."""
    if title.startswith("إجازة"):
        return "holiday"
    if "اختبار" in title:
        return "exam"
    if title.startswith("بداية"):
        return "semester_start"
    return "action"


def _weekdays(start: str | None, end: str | None) -> str | None:
    """Name the weekday of each date, e.g. "الأحد – السبت"."""
    if start and end and start != end:
        return f"{start} – {end}"
    return start or end


def _calendar_record(row: dict) -> dict:
    """One calendar page date in the shape get_calendar returns."""
    return {
        "title": row["title"],
        "term": row["term"],
        "academic_year": row["academic_year"],
        "kind": _calendar_kind(row["title"]),
        "start_date": row["date_gregorian"],
        "end_date": row["date_gregorian"],
        "weekdays": row["weekday"],
        "date_hijri": row["date_hijri"],
        "note": None,
        "page_updated": None,
        "source_url": row["source_url"],
        "scraped_at": row["scraped_at"],
    }


def _deadline_record(row: dict) -> dict:
    """One academic movement in the shape get_calendar returns."""
    return {
        "title": row["title"],
        "term": row["term"],
        "academic_year": row["academic_year"],
        "kind": row["kind"],
        "start_date": row["start_date"],
        "end_date": row["end_date"],
        "weekdays": _weekdays(row["start_weekday"], row["end_weekday"]),
        "date_hijri": None,
        "page_updated": row["page_updated"],
        "note": row["note"],
        "source_url": row["source_url"],
        "scraped_at": row["scraped_at"],
    }


def _add_status(record: dict, today: str) -> dict:
    """Compute past / open_now / upcoming and the days left, never left to the model."""
    start, end = record["start_date"], record["end_date"]
    if not start:
        record["status"] = None
        return record
    finish = end or start
    if finish < today:
        record["status"] = "past"
    elif start <= today:
        record["status"] = "open_now"
        record["days_left_until_end"] = (
            date.fromisoformat(finish) - date.fromisoformat(today)
        ).days
    else:
        record["status"] = "upcoming"
        record["days_until_start"] = (
            date.fromisoformat(start) - date.fromisoformat(today)
        ).days
    return record


STATUS_ORDER = {"open_now": 0, "upcoming": 1, "past": 2, None: 3}


def _sort_key(record: dict) -> tuple:
    """Open rows first, and among them the one that closes soonest."""
    if record["status"] == "open_now":
        when = record["end_date"] or record["start_date"] or "9999-99-99"
    else:
        when = record["start_date"] or "9999-99-99"
    return (STATUS_ORDER.get(record["status"], 3), when, record["title"])


def get_calendar(
    topic: str | None = None,
    term: str | None = None,
    upcoming_only: bool = False,
    db_path: str | Path | None = None,
    today: date | None = None,
) -> list[dict] | dict:
    """Return calendar dates and academic movements matching a topic and term.

    One tool for both sources: the calendar page's single dates and the
    academic-movements table's windows. Each row carries its status and, when it is
    open or upcoming, the days left or the days until it starts, all computed here.
    """
    events, events_error = _calendar_rows(db_path)
    movements, movements_error = _deadline_rows(db_path)
    if events_error is not None and movements_error is not None:
        return events_error

    records = [_calendar_record(row) for row in events]
    records += [_deadline_record(row) for row in movements]
    if not records:
        return {"error": "calendar is empty"}

    now = (today or date.today()).isoformat()
    selected: list[dict] = []
    for record in records:
        if not _matches_topic(topic, record["title"]):
            continue
        if term and str(term).strip() and not _term_matches(term, record["term"]):
            continue
        _add_status(record, now)
        if _flag(upcoming_only) and record["status"] == "past":
            continue
        selected.append(record)
    if not selected:
        return {"error": "no matching calendar events"}
    selected.sort(key=_sort_key)
    return selected[:CALENDAR_LIMIT]


GET_CALENDAR_DECLARATION = types.FunctionDeclaration(
    name="get_calendar",
    description=(
        "Look up university dates and academic deadlines: the start and end of study, holidays, the "
        "final exam periods and the summer term from the academic calendar page, and the student "
        "academic movements (add/drop, course withdrawal, withdrawal from the university, postponing a "
        "term, absence excuses, internal transfer, exemption, final and make-up exams, holidays) from the "
        "academic-movements table. Use it for every date or deadline question; never answer one from "
        "memory. "
        "Returns rows {title, term, academic_year, kind, start_date, end_date, weekdays, date_hijri, "
        "note, page_updated, status, source_url, scraped_at}, where kind is action, holiday, semester_start "
        "or exam and status is past, open_now or upcoming. A row that is open now carries "
        "days_left_until_end and an upcoming row carries days_until_start; a row with no date has status "
        "null and its text in note. page_updated is the date the source page itself gives. Give the status "
        "first, with the weekday and the date, and never compute a date or a count of days yourself. "
        "Mention page_updated only when the result carries stale=true. "
        "Set upcoming_only=true to leave out rows that have already ended. "
        "Returns {\"error\": \"no matching calendar events\"} when nothing matches: say the date is not on "
        "the university calendar and do not guess. "
        "Returns {\"error\": \"calendar is empty\"} when neither page has been downloaded yet."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "topic": types.Schema(
                type=types.Type.STRING,
                description=(
                    "What to look for, e.g. بداية الدراسة, الاختبارات النهائية, إجازة, "
                    "الاعتذار عن مقرر, الانسحاب من الجامعة, الحذف والإضافة, تأجيل الفصل, "
                    "عذر غياب, تحويل داخلي, اختبار بديل, إعفاء. Arabic or English; omit for every row."
                ),
            ),
            "term": types.Schema(
                type=types.Type.STRING,
                description="Optional term, e.g. الفصل الدراسي الأول or الفصل الصيفي. Omit for all terms.",
            ),
            "upcoming_only": types.Schema(
                type=types.Type.BOOLEAN,
                description="When true, leave out rows that have already ended.",
            ),
        },
        required=[],
    ),
)


PROGRAM_LEVELS = {
    "bachelor": "بكالوريوس",
    "bsc": "بكالوريوس",
    "بكالوريوس": "بكالوريوس",
    "master": "ماجستير",
    "masters": "ماجستير",
    "ماجستير": "ماجستير",
    "phd": "دكتوراه",
    "doctorate": "دكتوراه",
    "دكتوراه": "دكتوراه",
}
PROGRAM_DEPARTMENTS = {
    "CS": "CS",
    "علوم الحاسب": "CS",
    "CE": "CE",
    "هندسة الحاسب": "CE",
    "IT": "IT",
    "تقنية المعلومات": "IT",
    "SE": "SE",
    "هندسة البرمجيات": "SE",
    "IS": "IS",
    "نظم المعلومات": "IS",
    "shared": "مشترك",
    "مشترك": "مشترك",
}
PROGRAM_LIST_LIMIT = 100


def list_programs(
    level: str | None = None,
    department: str | None = None,
    include_requirements: bool = False,
    db_path: str | Path | None = None,
) -> list[dict] | dict:
    """List every stored degree programme, optionally for one level or department."""
    sql = [
        "SELECT degree, department, name_ar, requirements_ar, source_url, scraped_at",
        "FROM programs",
    ]
    params: list[object] = []
    conditions: list[str] = []
    if level and str(level).strip():
        wanted = PROGRAM_LEVELS.get(str(level).strip().lower())
        if wanted is None:
            return {"error": "unknown level"}
        conditions.append("degree = ?")
        params.append(wanted)
    if department and str(department).strip():
        raw = str(department).strip()
        wanted = PROGRAM_DEPARTMENTS.get(raw) or PROGRAM_DEPARTMENTS.get(raw.upper())
        if wanted is None:
            return {"error": "unknown department"}
        conditions.append("department = ?")
        params.append(wanted)
    if conditions:
        sql.append("WHERE " + " AND ".join(conditions))
    sql.append(
        "ORDER BY CASE degree WHEN 'بكالوريوس' THEN 1 WHEN 'ماجستير' THEN 2"
        " WHEN 'دكتوراه' THEN 3 ELSE 4 END, department, id"
    )
    sql.append("LIMIT ?")
    params.append(PROGRAM_LIST_LIMIT)

    try:
        conn = database.connect(db_path or config.DB_PATH)
        try:
            rows = conn.execute(" ".join(sql), params).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return {"error": f"program list unavailable: {exc}"}
    if not rows:
        return {"error": "no matching program"}

    result = []
    for row in rows:
        record = {
            "degree": row["degree"],
            "department": row["department"],
            "name": row["name_ar"],
            "source_url": row["source_url"],
            "scraped_at": row["scraped_at"],
        }
        if include_requirements:
            record["requirements"] = row["requirements_ar"]
        result.append(record)
    return result


LIST_PROGRAMS_DECLARATION = types.FunctionDeclaration(
    name="list_programs",
    description=(
        "List the college's degree programmes from its three program pages: every bachelor, "
        "master and PhD programme, with its department. Use it whenever the user asks what "
        "programmes the college offers, or asks about the programmes of one department or one "
        "level. Return every programme the tool gives; do not answer with only one when several "
        "match. Set include_requirements only when the user asks about admission conditions."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "level": types.Schema(
                type=types.Type.STRING,
                enum=["bachelor", "master", "phd"],
                description="Optional level: bachelor, master or phd. Omit for all levels.",
            ),
            "department": types.Schema(
                type=types.Type.STRING,
                enum=["CS", "CE", "IT", "SE", "IS", "shared"],
                description=(
                    "Optional department: CS, CE, IT, SE, IS, or shared for a joint programme. "
                    "Omit for every department."
                ),
            ),
            "include_requirements": types.Schema(
                type=types.Type.BOOLEAN,
                description=(
                    "Include each programme's admission conditions. Use it only when the user asks "
                    "about admission requirements."
                ),
            ),
        },
        required=[],
    ),
)


EMAIL_SUBJECTS = {"ar": "استفسار: {summary}", "en": "Enquiry: {summary}"}


COURSE_LIMIT = 5
COURSE_LIST_LIMIT = 200
DESCRIPTION_PREVIEW = 200

# The catalog writes codes in Arabic letters, and students sometimes type the Latin
# spelling of the department prefix. Only the prefixes that appear in the catalog are
# mapped; anything else is left alone rather than guessed.
LATIN_PREFIXES = {
    "hab": "هاب",
    "aal": "عال",
    "nal": "نال",
    "hal": "هال",
    "riyad": "ريض",
    "shamel": "شامل",
}


def _course_code(query: str) -> str | None:
    """Read a course code written in any order or script, e.g. "312 هاب" or "hab 312"."""
    text = normalize_arabic(query)
    digits_part = "".join(re.findall(r"\d+", text))
    if not digits_part:
        return None
    arabic = "".join(re.findall(r"[\u0621-\u064A]+", text))
    if arabic:
        return normalize_arabic(arabic) + digits_part
    latin = "".join(re.findall(r"[A-Za-z]+", query)).lower()
    prefix = LATIN_PREFIXES.get(latin)
    return f"{prefix}{digits_part}" if prefix else None


def _course_number(query: str) -> int | None:
    """Read the number of a course code on its own, e.g. "312"."""
    digits_part = "".join(re.findall(r"\d+", normalize_arabic(query)))
    return int(digits_part) if digits_part else None


def _course_rows(db_path=None) -> tuple[list[dict], dict | None]:
    """Read every stored course, or return the error to give the model."""
    try:
        conn = database.connect(db_path or config.DB_PATH)
        try:
            rows = conn.execute(
                "SELECT code_display, code_norm, number, level, name_ar, hours_text,"
                " credit_hours, lecture_hours, lab_hours, tutorial_hours, prerequisites_text,"
                " prerequisite_codes, prerequisite_min_credit_hours, corequisites_text,"
                " corequisite_codes, description_ar, source_url, scraped_at"
                " FROM courses ORDER BY number, code_norm"
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return [], {"error": f"course catalog unavailable: {exc}"}
    return [dict(row) for row in rows], None


def _code_list(value) -> list[str]:
    """Read a stored JSON code list, tolerating an empty or broken value."""
    try:
        codes = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return [str(code) for code in codes] if isinstance(codes, list) else []


def _word_matches(text: str, row: dict) -> bool:
    """True when every word of the query appears in the course name or description."""
    wanted = [token for token in search_form(text).split() if len(token) > 1]
    if not wanted:
        return False
    have = search_form(" ".join(filter(None, [row.get("name_ar"), row.get("description_ar")]))).split()
    return all(_token_matches(token, have) for token in wanted)


def _course_hours(row: dict) -> dict:
    """Break the hours down, leaving a part null when the page did not give it."""
    return {
        "credit": row["credit_hours"],
        "lecture": row["lecture_hours"],
        "lab": row["lab_hours"],
        "tutorial": row["tutorial_hours"],
        "text": row["hours_text"],
    }


def _course_detail(row: dict, rows: list[dict]) -> dict:
    """Everything the catalog holds about one course, plus the courses it unlocks."""
    code = row["code_norm"]
    unlocks = [
        other["code_norm"]
        for other in rows
        if code in _code_list(other["prerequisite_codes"])
    ]
    return {
        "code": code,
        "code_display": row["code_display"],
        "name": row["name_ar"],
        "level": row["level"],
        "hours": _course_hours(row),
        "prerequisites": {
            "text": row["prerequisites_text"],
            "codes": _code_list(row["prerequisite_codes"]),
            "min_credit_hours": row["prerequisite_min_credit_hours"],
        },
        "corequisites": {
            "text": row["corequisites_text"],
            "codes": _code_list(row["corequisite_codes"]),
        },
        "description": row["description_ar"],
        "unlocks": unlocks,
        "source_url": row["source_url"],
        "scraped_at": row["scraped_at"],
    }


def _course_summary(row: dict) -> dict:
    """Code, name, credit hours and a short description, for a list of matches."""
    description = row["description_ar"] or ""
    if len(description) > DESCRIPTION_PREVIEW:
        description = description[: DESCRIPTION_PREVIEW - 1].rstrip() + "…"
    return {
        "code": row["code_norm"],
        "name": row["name_ar"],
        "credit_hours": row["credit_hours"],
        "description": description or None,
        "source_url": row["source_url"],
        "scraped_at": row["scraped_at"],
    }


def find_course(
    query: str,
    level: str | None = None,
    db_path: str | Path | None = None,
) -> list[dict] | dict:
    """Find a course by its code, in any form, or by words from its name or description."""
    text = (query or "").strip()
    if not text:
        return {"error": "empty query"}
    rows, error = _course_rows(db_path)
    if error is not None:
        return error
    if not rows:
        return {"error": "course catalog is empty"}

    code = _course_code(text)
    number = _course_number(text)
    if code:
        matches = [row for row in rows if row["code_norm"] == code]
        if matches:
            return [_course_detail(row, rows) for row in matches]
        matches = [row for row in rows if number is not None and row["number"] == number]
    elif number is not None:
        matches = [row for row in rows if row["number"] == number]
    else:
        matches = [row for row in rows if _word_matches(text, row)]

    if level and str(level).strip():
        wanted = str(level).strip().lower()
        matches = [row for row in matches if (row["level"] or "").lower() == wanted]
    if not matches:
        return {"error": "no matching course"}
    return [_course_summary(row) for row in matches[:COURSE_LIMIT]]


FIND_COURSE_DECLARATION = types.FunctionDeclaration(
    name="find_course",
    description=(
        "Look up a course in the Software Engineering department catalog. The query may be a code in any "
        "form ('هاب312', '312 هاب', 'هاب ٣١٢', 'hab 312') or words from the course name or description. "
        "One exact code returns everything: the code as written, the name, the level, the hours broken "
        "down (credit, lecture, lab and tutorial), the prerequisites (codes, or a minimum credit-hour "
        "requirement), the co-requisites, the description, and 'unlocks', the codes of the courses that "
        "list this code as a prerequisite. "
        "A null name means the catalog does not list a name for that course: say the name is not listed "
        "and give the description, never guess a name. "
        "Several matches return code, name, credit hours, a short description and the source. "
        "Every result includes source_url and scraped_at. "
        "Returns {\"error\": \"no matching course\"} when nothing matches."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query": types.Schema(
                type=types.Type.STRING,
                description="A course code or words from the course name or description.",
            ),
            "level": types.Schema(
                type=types.Type.STRING,
                enum=["bachelor", "graduate"],
                description="Optional level filter.",
            ),
        },
        required=["query"],
    ),
)


def list_courses(
    level: str | None = None,
    db_path: str | Path | None = None,
) -> list[dict] | dict:
    """List the catalog's courses as code, name and credit hours, optionally for one level."""
    rows, error = _course_rows(db_path)
    if error is not None:
        return error
    if not rows:
        return {"error": "course catalog is empty"}
    if level and str(level).strip():
        wanted = str(level).strip().lower()
        rows = [row for row in rows if (row["level"] or "").lower() == wanted]
    if not rows:
        return {"error": "no matching course"}
    return [
        {
            "code": row["code_norm"],
            "name": row["name_ar"],
            "credit_hours": row["credit_hours"],
            "level": row["level"],
        }
        for row in rows[:COURSE_LIST_LIMIT]
    ]


LIST_COURSES_DECLARATION = types.FunctionDeclaration(
    name="list_courses",
    description=(
        "List the Software Engineering catalog's courses as code, name, credit hours and level, for one "
        "level or for both. Use it to show what a level offers, not to answer a question about one course "
        "(use find_course for that). A null name means the catalog does not list one."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "level": types.Schema(
                type=types.Type.STRING,
                enum=["bachelor", "graduate"],
                description="Optional level: bachelor or graduate. Omit for both.",
            ),
        },
        required=[],
    ),
)

EMAIL_BODIES = {
    "ar": (
        "السلام عليكم ورحمة الله وبركاته،\n\n"
        "أكتب إليكم بخصوص: {summary}\n\n"
        "[اذكر تفاصيل حالتك هنا]\n\n"
        "آمل التكرم بإفادتي بالإجراء المطلوب، ولكم جزيل الشكر.\n\n"
        "مع التقدير،\n"
        "[Your name]\n"
        "[Your ID]"
    ),
    "en": (
        "Dear Sir or Madam,\n\n"
        "I am writing regarding: {summary}\n\n"
        "[Add the details of your case here]\n\n"
        "I would be grateful for your guidance on the next step.\n\n"
        "Kind regards,\n"
        "[Your name]\n"
        "[Your ID]"
    ),
}


def _known_emails(db_path=None) -> set[str]:
    """Every email address we actually hold, from faculty rows and page text."""
    emails: set[str] = set()
    try:
        conn = database.connect(db_path or config.DB_PATH)
        try:
            for row in conn.execute("SELECT email FROM faculty WHERE email IS NOT NULL"):
                emails.add(row["email"].strip().lower())
            for row in conn.execute("SELECT text FROM chunks WHERE text LIKE '%@%'"):
                emails.update(match.group(0).lower() for match in EMAIL_RE.finditer(row["text"]))
        finally:
            conn.close()
    except sqlite3.Error:
        return emails
    return emails


def _invented_email(text: str, db_path=None) -> str | None:
    """Return the first email in `text` that exists nowhere in our sources."""
    known = _known_emails(db_path)
    for match in EMAIL_RE.finditer(text or ""):
        if match.group(0).lower() not in known:
            return match.group(0)
    return None


def _language(lang: str | None) -> str:
    """Return "ar" or "en", defaulting to English."""
    return "ar" if str(lang or "").strip().lower().startswith("ar") else "en"


def draft_email(
    to: str,
    case_summary: str,
    lang: str = "en",
    db_path: str | Path | None = None,
) -> dict:
    """Draft an email in Arabic or English with the user's placeholders filled in later."""
    summary = (case_summary or "").strip()
    if not summary:
        return {"error": "empty case summary"}
    invented = _invented_email(to, db_path)
    if invented:
        return {"error": f"email not found in sources: {invented}"}
    language = _language(lang)
    return {
        "subject": EMAIL_SUBJECTS[language].format(summary=summary),
        "body": EMAIL_BODIES[language].format(summary=summary),
    }


DRAFT_EMAIL_DECLARATION = types.FunctionDeclaration(
    name="draft_email",
    description=(
        "Draft a ready-to-send email to a contact about a student's case, in Arabic or English. "
        "Pass the recipient exactly as find_contact or a college page gave it; an email address that is not in "
        "the sources is refused. The body keeps the placeholders [Your name] and [Your ID] for the student to "
        "fill in, and the agent never sends the email itself. Returns {subject, body}."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "to": types.Schema(
                type=types.Type.STRING,
                description="The recipient: an email from find_contact, or the office name and page link.",
            ),
            "case_summary": types.Schema(
                type=types.Type.STRING,
                description="One short line on what the student needs, in the language of the draft.",
            ),
            "lang": types.Schema(
                type=types.Type.STRING,
                enum=["ar", "en"],
                description="Language of the draft: ar for Arabic, en for English.",
            ),
        },
        required=["to", "case_summary", "lang"],
    ),
)


def handoff(
    reason: str,
    contact: str,
    lang: str = "en",
    db_path: str | Path | None = None,
) -> dict:
    """Build a hand-off message: the reason, the contact and a ready email template."""
    reason_text = (reason or "").strip()
    contact_text = (contact or "").strip()
    if not reason_text:
        return {"error": "empty reason"}
    if not contact_text:
        return {"error": "empty contact"}
    invented = _invented_email(contact_text, db_path)
    if invented:
        return {"error": f"contact not found in sources: {invented}"}

    template = draft_email(contact_text, reason_text, lang, db_path)
    if "error" in template:
        return template
    if _language(lang) == "ar":
        message = (
            f"أحوّل حالتك إلى {contact_text}.\n"
            f"السبب: {reason_text}\n\n"
            f"نموذج بريد جاهز للإرسال:\n"
            f"الموضوع: {template['subject']}\n\n{template['body']}"
        )
    else:
        message = (
            f"I'm handing your case to {contact_text}.\n"
            f"Reason: {reason_text}\n\n"
            f"Ready-to-send email:\n"
            f"Subject: {template['subject']}\n\n{template['body']}"
        )
    return {"message": message}


HANDOFF_DECLARATION = types.FunctionDeclaration(
    name="handoff",
    description=(
        "Hand a case to a person or office: petitions, exceptions, grade appeals, withdrawals after the "
        "deadline, academic warnings, discipline, or anything the sources do not cover. "
        "Give the reason in one sentence and the contact from find_contact or a college page. "
        "When an office has no published email, pass the office name and its page link and say the email was "
        "not found. An email that is not in the sources is refused. "
        "Returns {message} with the reason, the contact and a ready email template that keeps "
        "the placeholders [Your name] and [Your ID]."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "reason": types.Schema(
                type=types.Type.STRING,
                description="One sentence on why this case needs a person, in the user's language.",
            ),
            "contact": types.Schema(
                type=types.Type.STRING,
                description="The contact from find_contact, or the office name with its page link.",
            ),
            "lang": types.Schema(
                type=types.Type.STRING,
                enum=["ar", "en"],
                description="Language of the template: ar for Arabic, en for English.",
            ),
        },
        required=["reason", "contact", "lang"],
    ),
)

TOOLS = [
    types.Tool(
        function_declarations=[
            SEARCH_KB_DECLARATION,
            GET_CALENDAR_DECLARATION,
            FIND_CONTACT_DECLARATION,
            GET_OFFICE_HOURS_DECLARATION,
            FIND_COURSE_DECLARATION,
            LIST_COURSES_DECLARATION,
            DRAFT_EMAIL_DECLARATION,
            HANDOFF_DECLARATION,
            LIST_PROGRAMS_DECLARATION,
        ]
    )
]
HANDLERS = {
    "search_kb": search_kb,
    "get_calendar": get_calendar,
    "find_contact": find_contact,
    "get_office_hours": get_office_hours,
    "draft_email": draft_email,
    "handoff": handoff,
    "find_course": find_course,
    "list_courses": list_courses,
    "list_programs": list_programs,
}



def _instant(value) -> datetime | None:
    """Read a stored ISO timestamp as an aware datetime, or None when unreadable."""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _stale(value) -> bool:
    """True when a stored scraped_at is older than STALE_AFTER_DAYS."""
    instant = _instant(value)
    if instant is None:
        return False
    age = (datetime.now(timezone.utc) - instant).total_seconds() / 86400
    return age >= config.STALE_AFTER_DAYS


def _with_staleness(value):
    """Flag every result that carries an old scraped_at, without changing its shape."""
    if isinstance(value, list):
        return [_with_staleness(item) for item in value]
    if not isinstance(value, dict):
        return value
    record = {key: _with_staleness(item) for key, item in value.items()}
    stamp = record.get("scraped_at")
    if stamp and _stale(stamp):
        record["stale"] = True
        record["stale_note"] = (
            f"last updated {str(stamp)[:10]}, more than {config.STALE_AFTER_DAYS} days ago"
        )
    return record


def execute_tool(name: str, arguments: dict | None) -> list | dict:
    """Run a tool by name, returning JSON-ready data and never raising."""
    handler = HANDLERS.get(name)
    if handler is None:
        return {"error": f"unknown tool: {name}"}
    try:
        result = handler(**(arguments or {}))
    except TypeError as exc:
        return {"error": f"bad arguments for {name}: {exc}"}
    except Exception as exc:  # a failing tool must never break the agent loop
        return {"error": f"{name} failed: {exc}"}
    return _with_staleness(result)
