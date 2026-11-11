"""Gemini function declarations and their SQLite-backed implementations."""

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from google.genai import types

import config
import db as database
from agent.arabic import fts_query
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
        "departments, bachelor/master/PhD programs, admission requirements, transfer conditions, forms "
        "and regulations, and the college's contact details and academic affairs agency. "
        "Use it for any factual question about the college before answering; do not answer such questions "
        "from memory. Queries may be Arabic or English. "
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
        "source_url": row.get("list_url"),
        "scraped_at": row.get("scraped_at"),
    }
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
        "(building and room), email, and the profile link. Arabic spelling variants, missing middle "
        "names and titles such as د. or أ.د. are handled. "
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

WORK_DAYS = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday")
MIN_SLOT_MINUTES = 30
SAMPLE_SCHEDULES_PATH = config.ROOT / "data" / "sample_schedules.json"
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


def _minutes(value: str) -> int | None:
    """Parse "HH:MM" into minutes since midnight; None when it is not a time."""
    try:
        hour, minute = str(value).strip().split(":")[:2]
        return int(hour) * 60 + int(minute)
    except (TypeError, ValueError):
        return None


def _clock(minutes: int) -> str:
    """Format minutes since midnight as "HH:MM"."""
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def normalize_day(value: str | None) -> str | None:
    """Map an English or Arabic day name to its canonical English name."""
    if not value:
        return None
    return DAY_ALIASES.get(str(value).strip().lower())


def free_slots_for_day(
    day: str,
    busy: list[dict] | None,
    start: str | None = None,
    end: str | None = None,
    minimum: int = MIN_SLOT_MINUTES,
) -> list[dict]:
    """Return the free gaps of at least `minimum` minutes in one working window."""
    window_start = _minutes(start or config.WORK_DAY_START)
    window_end = _minutes(end or config.WORK_DAY_END)
    if window_start is None or window_end is None or window_end <= window_start:
        return []

    blocks: list[tuple[int, int]] = []
    for block in busy or []:
        if normalize_day(block.get("day")) != day:
            continue
        block_start = _minutes(block.get("start"))
        block_end = _minutes(block.get("end"))
        if block_start is None or block_end is None or block_end <= block_start:
            continue
        blocks.append((max(block_start, window_start), min(block_end, window_end)))

    slots: list[dict] = []
    cursor = window_start
    for block_start, block_end in sorted(blocks):
        if block_end <= cursor:
            continue
        if block_start - cursor >= minimum:
            slots.append({"day": day, "start": _clock(cursor), "end": _clock(block_start)})
        cursor = max(cursor, block_end)
    if window_end - cursor >= minimum:
        slots.append({"day": day, "start": _clock(cursor), "end": _clock(window_end)})
    return slots


def load_sample_schedules(path: str | Path | None = None) -> dict:
    """Read the demo busy blocks, returning {} when the file is missing or broken."""
    try:
        data = json.loads(Path(path or SAMPLE_SCHEDULES_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    doctors = data.get("doctors") if isinstance(data, dict) else None
    return doctors if isinstance(doctors, dict) else {}


def _office_hours(faculty_id: int, db_path=None) -> list[dict]:
    """Return a person's stored office-hours rows, newest first, or []."""
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


def _file_date(path: str | Path | None = None) -> str | None:
    """Return the last-modified date of the sample file as an ISO date."""
    try:
        stamp = Path(path or SAMPLE_SCHEDULES_PATH).stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(stamp, timezone.utc).date().isoformat()


def get_free_slots(
    faculty_name: str,
    day: str | None = None,
    db_path: str | Path | None = None,
    sample_path: str | Path | None = None,
) -> dict:
    """Return a person's published office hours, or gaps from the demo sample data."""
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
    hours = _office_hours(record["id"], db_path)
    if hours:
        return {
            "faculty": person,
            "basis": "office_hours",
            "slots": [
                {"day": row["day"], "start": row["start"], "end": row["end"]}
                for row in hours
                if wanted is None or normalize_day(row["day"]) == wanted
            ],
            "source_url": hours[0]["source_url"],
            "scraped_at": hours[0]["scraped_at"],
        }

    sample = load_sample_schedules(sample_path).get(record["username"])
    if not sample:
        return {"error": "no schedule data"}
    slots: list[dict] = []
    for name in [wanted] if wanted else WORK_DAYS:
        slots.extend(free_slots_for_day(name, sample.get("busy")))
    return {
        "faculty": person,
        "basis": "sample_data",
        "slots": slots,
        "source_url": "data/sample_schedules.json",
        "scraped_at": _file_date(sample_path),
    }


GET_FREE_SLOTS_DECLARATION = types.FunctionDeclaration(
    name="get_free_slots",
    description=(
        "Return when a faculty member is free: their published office hours, or, when they have none, "
        "gaps estimated from a demo sample schedule. Read-only: it never books, reserves or sends anything. "
        "The result carries a basis field: 'office_hours' means these are the doctor's published office hours; "
        "'sample_data' means the times come from DEMO sample data and are not confirmed availability, so say "
        "that clearly and suggest confirming by email. "
        "Returns {faculty, basis, slots: [{day, start, end}], source_url, scraped_at}. "
        "When the name is not confirmed it returns {status: 'candidates, not confirmed', message, "
        "candidates: [...]}: ask the user 'did you mean X?' and never present a candidate as the answer. "
        "Returns {\"error\": \"no schedule data\"} when the person has neither office hours nor sample data."
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


EMAIL_SUBJECTS = {"ar": "استفسار: {summary}", "en": "Enquiry: {summary}"}

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
        "Returns {message} with the reason, the contact and a ready email template that keeps the "
        "placeholders [Your name] and [Your ID]."
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
            FIND_CONTACT_DECLARATION,
            GET_FREE_SLOTS_DECLARATION,
            DRAFT_EMAIL_DECLARATION,
            HANDOFF_DECLARATION,
        ]
    )
]
HANDLERS = {
    "search_kb": search_kb,
    "find_contact": find_contact,
    "get_free_slots": get_free_slots,
    "draft_email": draft_email,
    "handoff": handoff,
}


def execute_tool(name: str, arguments: dict | None) -> list | dict:
    """Run a tool by name, returning JSON-ready data and never raising."""
    handler = HANDLERS.get(name)
    if handler is None:
        return {"error": f"unknown tool: {name}"}
    try:
        return handler(**(arguments or {}))
    except TypeError as exc:
        return {"error": f"bad arguments for {name}: {exc}"}
    except Exception as exc:  # a failing tool must never break the agent loop
        return {"error": f"{name} failed: {exc}"}
