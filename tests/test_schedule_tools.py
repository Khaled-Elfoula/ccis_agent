"""Tests for get_office_hours, draft_email and handoff against a temporary database."""

import db as database
import agent.tools as tools_module
from agent.tools import HANDLERS, draft_email, get_office_hours, handoff, normalize_day

WITHHOURS_SUNDAY = [{"day": "Sunday", "start": "10:00", "end": "11:00"}]


def test_normalize_day_accepts_english_and_arabic():
    assert normalize_day("sunday") == "Sunday"
    assert normalize_day("الأحد") == "Sunday"
    assert normalize_day("الخميس") == "Thursday"
    assert normalize_day("yesterday") is None


def test_office_hours_are_returned_without_source_metadata(faculty_db):
    result = get_office_hours("د. سعد الاحمدي", db_path=faculty_db)
    assert result["office_hours"] == [
        {"day": "Sunday", "start": "10:00", "end": "11:00"},
        {"day": "Tuesday", "start": "12:00", "end": "13:00"},
    ]
    assert result["faculty"]["name"] == "سعد عبدالله الأحمدي"
    assert "source_url" not in result
    assert "scraped_at" not in result
    assert "no_office_hours" not in result


def test_a_day_filter_accepts_english_and_arabic(faculty_db):
    for day in ("Sunday", "الأحد"):
        result = get_office_hours("سعد الاحمدي", day, db_path=faculty_db)
        assert result["office_hours"] == WITHHOURS_SUNDAY
        assert result["day"] == "Sunday"


def test_a_day_without_published_hours_is_an_empty_list(faculty_db):
    result = get_office_hours("سعد الاحمدي", "Monday", db_path=faculty_db)
    assert result["office_hours"] == []
    assert result["day"] == "Monday"
    assert "no_office_hours" not in result


def test_no_office_hours_returns_the_contact_details(faculty_db):
    result = get_office_hours("سامي الوكيل", db_path=faculty_db)
    assert result["no_office_hours"] is True
    assert "office_hours" not in result
    assert result["email"] == "swakeel@ksu.edu.sa"
    assert "email_source" not in result
    assert result["office"] == {"building": None, "room": "2206"}
    assert result["profile_url"] == "https://faculty.ksu.edu.sa/ar/sampleonly"


def test_the_short_english_name_finds_the_right_doctor(faculty_db):
    """The demo case: Dr. Saad Al-Ahmadi is the CS doctor."""
    result = get_office_hours("Dr. Saad Al-Ahmadi", db_path=faculty_db)
    assert result["faculty"]["name"] == "سعد عبدالله الأحمدي"
    assert result["faculty"]["email"] == "salahmadi@ksu.edu.sa"
    assert result["office_hours"] == [
        {"day": "Sunday", "start": "10:00", "end": "11:00"},
        {"day": "Tuesday", "start": "12:00", "end": "13:00"},
    ]


def test_an_unconfirmed_name_is_labelled_not_answered(faculty_db):
    """A shared family name only must come back as a question, with no hours attached."""
    result = get_office_hours("Saud AlAhmadi", db_path=faculty_db)
    assert result["status"] == "candidates, not confirmed"
    assert "office_hours" not in result
    assert [person["name"] for person in result["candidates"]] == ["سعد عبدالله الأحمدي"]


def test_ambiguous_name_returns_candidates(faculty_db):
    result = get_office_hours("محمد السالم", db_path=faculty_db)
    assert result["status"] == "candidates, not confirmed"
    assert {person["name"] for person in result["candidates"]} == {
        "محمد عبدالله السالم",
        "محمد سعد السالم",
    }
    assert "did you mean" in result["message"]


def test_unknown_name_unknown_day_and_empty_name(faculty_db):
    assert get_office_hours("خالد الغامدي", db_path=faculty_db) == {"error": "no match found"}
    assert get_office_hours("سعد الاحمدي", "yesterday", db_path=faculty_db) == {
        "error": "unknown day: yesterday"
    }
    assert get_office_hours("   ", db_path=faculty_db) == {"error": "empty name"}


def test_unreadable_published_rows_are_counted_not_reported(faculty_db):
    conn = database.connect(faculty_db)
    faculty_id = conn.execute("SELECT id FROM faculty WHERE username = 'dup1'").fetchone()["id"]
    conn.executemany(
        "INSERT INTO office_hours (faculty_id, day, start, end, source_url, scraped_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [
            (faculty_id, "Sunday", "16:23", "15:23", "https://faculty.ksu.edu.sa/ar/dup1", "2026-11-10T10:00:00+00:00"),
            (faculty_id, "Monday", "09:00", "10:00", "https://faculty.ksu.edu.sa/ar/dup1", "2026-11-10T10:00:00+00:00"),
        ],
    )
    conn.commit()
    conn.close()

    result = get_office_hours("محمد عبدالله السالم", db_path=faculty_db)
    assert result["office_hours"] == [{"day": "Monday", "start": "09:00", "end": "10:00"}]
    assert result["unreadable_rows"] == 1


def test_office_hours_never_books_anything(faculty_db):
    result = get_office_hours("سعد الاحمدي", db_path=faculty_db)
    assert "booked" not in result
    assert set(HANDLERS) == {
        "search_kb",
        "get_calendar",
        "find_contact",
        "get_office_hours",
        "find_course",
        "list_courses",
        "list_programs",
        "draft_email",
        "handoff",
    }


def test_draft_email_arabic_and_english(faculty_db):
    arabic = draft_email("salahmadi@ksu.edu.sa", "طلب تأجيل اختبار", "ar", db_path=faculty_db)
    assert "طلب تأجيل اختبار" in arabic["subject"]
    assert "[Your name]" in arabic["body"]
    assert "[Your ID]" in arabic["body"]

    english = draft_email("salahmadi@ksu.edu.sa", "exam postponement", "en", db_path=faculty_db)
    assert "exam postponement" in english["subject"]
    assert "[Your name]" in english["body"]
    assert "[Your ID]" in english["body"]


def test_draft_email_refuses_an_invented_email(faculty_db):
    result = draft_email("someone@elsewhere.com", "x", "en", db_path=faculty_db)
    assert result == {"error": "email not found in sources: someone@elsewhere.com"}


def test_draft_email_needs_a_summary(faculty_db):
    assert draft_email("salahmadi@ksu.edu.sa", "  ", "en", db_path=faculty_db) == {
        "error": "empty case summary"
    }


def test_handoff_arabic_has_contact_and_template(faculty_db):
    contact = (
        "وكالة الكلية للشؤون التعليمية والأكاديمية "
        "https://ccis.ksu.edu.sa/ar/student-affairs-agency-mail"
    )
    result = handoff("انسحاب من مقرر بعد الموعد النهائي", contact, "ar", db_path=faculty_db)
    message = result["message"]
    assert "student-affairs-agency-mail" in message
    assert "انسحاب من مقرر بعد الموعد النهائي" in message
    assert "[Your name]" in message
    assert "[Your ID]" in message


def test_handoff_english_and_empty_fields(faculty_db):
    result = handoff("grade appeal", "salahmadi@ksu.edu.sa", "en", db_path=faculty_db)
    assert "salahmadi@ksu.edu.sa" in result["message"]
    assert "[Your name]" in result["message"]
    assert handoff("", "salahmadi@ksu.edu.sa", "en", db_path=faculty_db) == {
        "error": "empty reason"
    }
    assert handoff("grade appeal", "  ", "en", db_path=faculty_db) == {"error": "empty contact"}


def test_handoff_refuses_an_invented_contact(faculty_db):
    result = handoff("grade appeal", "advisor@nowhere.com", "en", db_path=faculty_db)
    assert result == {"error": "contact not found in sources: advisor@nowhere.com"}


def test_execute_tool_runs_the_office_hours_tool(faculty_db):
    result = tools_module.execute_tool(
        "get_office_hours",
        {"faculty_name": "سعد الاحمدي", "day": "Sunday", "db_path": str(faculty_db)},
    )
    assert result["office_hours"] == WITHHOURS_SUNDAY
    assert "error" in tools_module.execute_tool("get_office_hours", {"day": "Sunday"})
