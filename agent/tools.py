"""Gemini function declarations and their SQLite-backed implementations."""

import re
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from google.genai import types

import config
import db as database
from agent.arabic import fts_query, search_form
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
        # A row that has an email but no label predates the column, and every email
        # stored back then was read off a page.
        "email_source": (row.get("email_source") or "scraped") if row.get("email") else None,
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
        "(building and room), email, email_source, and the profile link. Arabic spelling variants, missing "
        "middle names and titles such as د. or أ.د. are handled. "
        "email_source says where the email came from: 'scraped' means it was read off the doctor's page and "
        "can be given normally; 'derived' means it was built from the username in the standard university "
        "format username@ksu.edu.sa and was not confirmed on the doctor's page; 'derived_unverified' means "
        "the same but the profile page could not be opened, so it needs double-checking; null means there is "
        "no email at all. Say which of these applies whenever you give an email. "
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

CALENDAR_LIMIT = 40
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
            "email_source": person.get("email_source"),
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
        "source_url": rows[0]["source_url"],
        "scraped_at": rows[0]["scraped_at"],
    }
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
        "Returns {faculty, office_hours: [{day, start, end}], source_url, scraped_at}, plus the "
        "requested day when one was given. If office_hours is empty the doctor published no hours for "
        "that day; unreadable_rows counts published rows whose times could not be read. "
        "When the doctor has no published office hours at all it returns {no_office_hours: true, "
        "faculty, email, email_source, office, profile_url} instead, so you can give their email and "
        "office and suggest writing to them. "
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
    if not rows:
        return [], {"error": "calendar is empty"}
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


def _term_matches(term: str, stored: str | None) -> bool:
    """True when the asked term is the stored term, allowing a shorter form."""
    wanted, have = search_form(term), search_form(stored or "")
    return bool(wanted) and bool(have) and (wanted in have or have in wanted)


def _flag(value) -> bool:
    """Read a model boolean that may arrive as a string."""
    return value is True or str(value).strip().lower() in ("true", "1", "yes")


def get_calendar(
    topic: str | None = None,
    term: str | None = None,
    upcoming_only: bool = False,
    db_path: str | Path | None = None,
    today: date | None = None,
) -> list[dict] | dict:
    """Return calendar events matching a topic and term, with their dates and sources."""
    rows, error = _calendar_rows(db_path)
    if error is not None:
        return error

    now = (today or date.today()).isoformat()
    selected: list[dict] = []
    for row in rows:
        if topic and str(topic).strip() and not _title_matches(topic, row["title"]):
            continue
        if term and str(term).strip() and not _term_matches(term, row["term"]):
            continue
        row["is_past"] = row["date_gregorian"] < now
        if _flag(upcoming_only) and row["is_past"]:
            continue
        selected.append(row)
    if not selected:
        return {"error": "no matching calendar events"}
    return selected[:CALENDAR_LIMIT]


GET_CALENDAR_DECLARATION = types.FunctionDeclaration(
    name="get_calendar",
    description=(
        "Look up key dates and deadlines on the university's academic calendar: the start and end of "
        "study, the national day and mid-year breaks, the academic reinforcement week, and the final "
        "exam periods, for the first and second semesters and the summer term. "
        "Use it for every date or deadline question; never answer one from memory. "
        "Returns one row per date: {title, term, academic_year, weekday, date_gregorian, date_hijri, "
        "source_url, scraped_at, is_past}. Each 'بداية ...' and 'نهاية ...' is its own row, and no date "
        "range is computed. Set upcoming_only=true to leave out dates that have already passed. "
        "Returns {\"error\": \"no matching calendar events\"} when the page has no such row: say the event "
        "is not on the university calendar page and do not guess a date. "
        "Returns {\"error\": \"calendar is empty\"} when the calendar has not been downloaded yet."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "topic": types.Schema(
                type=types.Type.STRING,
                description=(
                    "What to look for, e.g. بداية الدراسة, الاختبارات النهائية, إجازة, الأسبوع "
                    "التعزيزي. Arabic or English; omit for every row."
                ),
            ),
            "term": types.Schema(
                type=types.Type.STRING,
                description="Optional term, e.g. الفصل الدراسي الأول or الفصل الصيفي. Omit for all terms.",
            ),
            "upcoming_only": types.Schema(
                type=types.Type.BOOLEAN,
                description="When true, leave out dates before today.",
            ),
        },
        required=[],
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


def _email_source(email: str | None, db_path=None) -> str | None:
    """Return where a stored email came from, or None when we do not hold it."""
    if not email or "@" not in email:
        return None
    try:
        conn = database.connect(db_path or config.DB_PATH)
        try:
            row = conn.execute(
                "SELECT email_source FROM faculty WHERE LOWER(email) = LOWER(?) LIMIT 1",
                (email.strip(),),
            ).fetchone()
        finally:
            conn.close()
    except sqlite3.Error:
        return None
    return (row["email_source"] or "scraped") if row else None


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
        "email_source": _email_source(to, db_path),
    }


DRAFT_EMAIL_DECLARATION = types.FunctionDeclaration(
    name="draft_email",
    description=(
        "Draft a ready-to-send email to a contact about a student's case, in Arabic or English. "
        "Pass the recipient exactly as find_contact or a college page gave it; an email address that is not in "
        "the sources is refused. The body keeps the placeholders [Your name] and [Your ID] for the student to "
        "fill in, and the agent never sends the email itself. Returns {subject, body, email_source}, where "
        "email_source is the same label find_contact gave for that address (scraped, derived, "
        "derived_unverified) or null when the address is not a stored faculty email."
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
    source = template.get("email_source")
    note = ""
    if source == "derived":
        note = (
            "\n\nملاحظة: هذا العنوان يتبع صيغة الجامعة القياسية username@ksu.edu.sa ولم يتم التأكد منه من صفحة "
            "عضو هيئة التدريس."
            if _language(lang) == "ar"
            else "\n\nNote: this address follows the university's standard format "
            "(username@ksu.edu.sa) and was not confirmed on the doctor's page."
        )
    elif source == "derived_unverified":
        note = (
            "\n\nملاحظة: هذا العنوان يتبع صيغة الجامعة القياسية username@ksu.edu.sa ولم يتم التأكد منه من صفحة "
            "عضو هيئة التدريس، كما تعذّر فتح صفحته، لذا يُرجى التحقق منه."
            if _language(lang) == "ar"
            else "\n\nNote: this address follows the university's standard format "
            "(username@ksu.edu.sa) and was not confirmed on the doctor's page. The profile page could "
            "not be opened either, so please double-check it."
        )
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
    return {"message": message + note, "email_source": source}


HANDOFF_DECLARATION = types.FunctionDeclaration(
    name="handoff",
    description=(
        "Hand a case to a person or office: petitions, exceptions, grade appeals, withdrawals after the "
        "deadline, academic warnings, discipline, or anything the sources do not cover. "
        "Give the reason in one sentence and the contact from find_contact or a college page. "
        "When an office has no published email, pass the office name and its page link and say the email was "
        "not found. An email that is not in the sources is refused. "
        "Returns {message, email_source} with the reason, the contact and a ready email template that keeps "
        "the placeholders [Your name] and [Your ID]. When email_source is 'derived' or 'derived_unverified' "
        "the message already carries the note that the address follows the university's standard format and "
        "was not confirmed on the doctor's page."
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
            DRAFT_EMAIL_DECLARATION,
            HANDOFF_DECLARATION,
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
