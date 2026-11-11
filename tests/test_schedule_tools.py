"""Tests for get_free_slots, draft_email and handoff against a temporary database."""

import agent.tools as tools_module
from agent.tools import (
    HANDLERS,
    draft_email,
    free_slots_for_day,
    get_free_slots,
    handoff,
    normalize_day,
)

SUNDAY_WITH_TEN_TO_ELEVEN = [
    {"day": "Sunday", "start": "08:00", "end": "09:00"},
    {"day": "Sunday", "start": "11:00", "end": "16:00"},
]


def test_free_slots_for_day_subtracts_busy_blocks():
    busy = [{"day": "Sunday", "start": "09:00", "end": "11:00"}]
    assert free_slots_for_day("Sunday", busy) == SUNDAY_WITH_TEN_TO_ELEVEN


def test_free_slots_for_day_drops_gaps_shorter_than_thirty_minutes():
    busy = [
        {"day": "Sunday", "start": "09:00", "end": "09:50"},
        {"day": "Sunday", "start": "10:10", "end": "11:00"},
    ]
    assert free_slots_for_day("Sunday", busy) == SUNDAY_WITH_TEN_TO_ELEVEN


def test_free_slots_for_day_merges_overlapping_blocks_and_clips_the_window():
    busy = [
        {"day": "Sunday", "start": "07:00", "end": "09:00"},
        {"day": "Sunday", "start": "10:00", "end": "12:00"},
        {"day": "Sunday", "start": "11:00", "end": "13:00"},
        {"day": "Monday", "start": "08:00", "end": "16:00"},
    ]
    assert free_slots_for_day("Sunday", busy) == [
        {"day": "Sunday", "start": "09:00", "end": "10:00"},
        {"day": "Sunday", "start": "13:00", "end": "16:00"},
    ]


def test_free_slots_for_day_ignores_broken_blocks_and_a_free_day_is_the_window():
    assert free_slots_for_day("Tuesday", [{"day": "Sunday", "start": "09:00", "end": "11:00"}]) == [
        {"day": "Tuesday", "start": "08:00", "end": "16:00"}
    ]
    assert free_slots_for_day("Sunday", [{"day": "Sunday", "start": "11:00", "end": "oops"}]) == [
        {"day": "Sunday", "start": "08:00", "end": "16:00"}
    ]


def test_normalize_day_accepts_english_and_arabic():
    assert normalize_day("sunday") == "Sunday"
    assert normalize_day("الأحد") == "Sunday"
    assert normalize_day("الخميس") == "Thursday"
    assert normalize_day("yesterday") is None


def test_office_hours_basis(faculty_db, sample_file):
    result = get_free_slots(
        "د. سعد الاحمدي", db_path=faculty_db, sample_path=sample_file
    )
    assert result["basis"] == "office_hours"
    assert result["slots"] == [
        {"day": "Sunday", "start": "10:00", "end": "11:00"},
        {"day": "Tuesday", "start": "12:00", "end": "13:00"},
    ]
    assert result["faculty"]["name"] == "سعد عبدالله الأحمدي"
    assert result["source_url"] == "https://faculty.ksu.edu.sa/ar/withhours"
    assert result["scraped_at"] == "2026-11-10T10:00:00+00:00"


def test_office_hours_win_over_sample_data(faculty_db, sample_file):
    result = get_free_slots("سعد الاحمدي", "Sunday", db_path=faculty_db, sample_path=sample_file)
    assert result["basis"] == "office_hours"
    assert result["slots"] == [{"day": "Sunday", "start": "10:00", "end": "11:00"}]


def test_sample_data_basis_when_there_are_no_office_hours(faculty_db, sample_file):
    result = get_free_slots(
        "سامي بن صالح الوكيل", "Sunday", db_path=faculty_db, sample_path=sample_file
    )
    assert result["basis"] == "sample_data"
    assert result["slots"] == SUNDAY_WITH_TEN_TO_ELEVEN
    assert result["source_url"] == "data/sample_schedules.json"
    assert result["faculty"]["email"] == "swakeel@ksu.edu.sa"


def test_sample_data_returns_the_whole_week_and_accepts_an_arabic_day(faculty_db, sample_file):
    week = get_free_slots("سامي الوكيل", db_path=faculty_db, sample_path=sample_file)
    arabic = get_free_slots("سامي الوكيل", "الأحد", db_path=faculty_db, sample_path=sample_file)
    assert [slot["day"] for slot in week["slots"]] == [
        "Sunday",
        "Sunday",
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
    ]
    assert arabic["slots"] == SUNDAY_WITH_TEN_TO_ELEVEN


def test_the_short_english_name_finds_the_right_doctor(faculty_db, sample_file):
    """The demo case, through get_free_slots: Dr. Saad Al-Ahmadi is the CS doctor."""
    result = get_free_slots("Dr. Saad Al-Ahmadi", db_path=faculty_db, sample_path=sample_file)
    assert result["basis"] == "office_hours"
    assert result["faculty"]["name"] == "سعد عبدالله الأحمدي"
    assert result["faculty"]["email"] == "salahmadi@ksu.edu.sa"


def test_both_wakeel_spellings_reach_the_sample_schedule(faculty_db, sample_file):
    for name in ("Sami Alwakeel", "Sami Al-Wakeel"):
        result = get_free_slots(name, "Sunday", db_path=faculty_db, sample_path=sample_file)
        assert result["basis"] == "sample_data"
        assert result["slots"] == SUNDAY_WITH_TEN_TO_ELEVEN


def test_an_unconfirmed_name_is_labelled_not_answered(faculty_db, sample_file):
    """A shared family name only must come back as a question, with no slots attached."""
    result = get_free_slots("Saud AlAhmadi", db_path=faculty_db, sample_path=sample_file)
    assert result["status"] == "candidates, not confirmed"
    assert "slots" not in result
    assert [person["name"] for person in result["candidates"]] == ["سعد عبدالله الأحمدي"]


def test_no_schedule_data(faculty_db, sample_file):
    assert get_free_slots("محمد شميم حسين", db_path=faculty_db, sample_path=sample_file) == {
        "error": "no schedule data"
    }


def test_ambiguous_name_returns_candidates(faculty_db, sample_file):
    result = get_free_slots("محمد السالم", db_path=faculty_db, sample_path=sample_file)
    assert result["status"] == "candidates, not confirmed"
    assert {person["name"] for person in result["candidates"]} == {
        "محمد عبدالله السالم",
        "محمد سعد السالم",
    }
    assert "did you mean" in result["message"]


def test_unknown_name_unknown_day_and_missing_sample_file(faculty_db, tmp_path):
    assert get_free_slots("خالد الغامدي", db_path=faculty_db) == {"error": "no match found"}
    assert get_free_slots("سامي الوكيل", "yesterday", db_path=faculty_db) == {
        "error": "unknown day: yesterday"
    }
    missing = tmp_path / "missing.json"
    assert get_free_slots("سامي الوكيل", db_path=faculty_db, sample_path=missing) == {
        "error": "no schedule data"
    }


def test_free_slots_never_books_anything(faculty_db, sample_file):
    result = get_free_slots("سامي الوكيل", db_path=faculty_db, sample_path=sample_file)
    assert "booked" not in result
    assert set(HANDLERS) == {"search_kb", "find_contact", "get_free_slots", "draft_email", "handoff"}


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


def test_execute_tool_runs_the_new_tools(faculty_db, sample_file):
    result = tools_module.execute_tool(
        "get_free_slots",
        {
            "faculty_name": "سامي الوكيل",
            "day": "Sunday",
            "db_path": str(faculty_db),
            "sample_path": str(sample_file),
        },
    )
    assert result["basis"] == "sample_data"
    assert "error" in tools_module.execute_tool("get_free_slots", {"day": "Sunday"})
