"""Tests for the academic calendar parser, its storage and get_calendar."""

from datetime import date
from pathlib import Path

import pytest

import agent.tools as tools_module
import db as database
from agent.tools import get_calendar
from scrapers.calendar import parse_calendar
from scrapers.ccis import ParseError
from scrapers.run import store_calendar
from scrapers.sources import CALENDAR

FIXTURE = Path(__file__).parent / "fixtures" / "calendar" / "current.html"
SCRAPED_AT = "2026-11-10T10:00:00+00:00"


def fixture_html() -> str:
    """The saved copy of the university calendar page."""
    return FIXTURE.read_text(encoding="utf-8")


def calendar_db(tmp_path):
    """A temporary database holding the saved calendar page."""
    path = tmp_path / "calendar.sqlite"
    conn = database.init_db(path)
    store_calendar(conn, parse_calendar(fixture_html()), CALENDAR.url)
    conn.close()
    return path


def test_academic_year_and_terms_come_from_the_page():
    parsed = parse_calendar(fixture_html())
    assert parsed.academic_year == "2026-2027"
    terms = {event.term for event in parsed.events}
    assert terms == {
        "الفصل الدراسي الأول",
        "الفصل الدراسي الثاني",
        "االفصل الدراسي الصيفي",
        "العام الدراسي 2027 / 2028م",
    }


def test_every_row_of_the_saved_page_is_parsed():
    parsed = parse_calendar(fixture_html())
    assert len(parsed.events) == 22
    first = parsed.events[0]
    assert first.title == "بداية الدراسة للفصل الدراسي الأول"
    assert first.weekday == "الأحد"
    assert first.date_gregorian == "2026-08-23"
    assert first.date_hijri == "1448-03-10"
    assert first.academic_year == "2026-2027"


def test_every_weekday_matches_its_gregorian_date():
    """The check the parser makes: a mismatch is flagged, never silently fixed."""
    parsed = parse_calendar(fixture_html())
    assert all(event.weekday_matches for event in parsed.events)


def test_final_exam_periods_are_separate_rows():
    parsed = parse_calendar(fixture_html())
    exams = [event for event in parsed.events if "الاختبارات النهائية" in event.title]
    assert [(event.title, event.date_gregorian) for event in exams] == [
        ("بداية الاختبارات النهائية", "2026-12-13"),
        ("نهاية الاختبارات النهائية", "2026-12-26"),
        ("بداية الاختبارات النهائية", "2027-06-06"),
        ("نهاية الاختبارات النهائية", "2027-06-19"),
        ("بداية الاختبارات النهائية", "2027-08-10"),
        ("نهاية الاختبارات النهائية", "2027-08-12"),
    ]


def test_the_page_has_no_drop_add_or_withdrawal_rows():
    """What the calendar really contains: no الحذف والإضافة and no الانسحاب rows."""
    titles = " ".join(event.title for event in parse_calendar(fixture_html()).events)
    assert "الحذف" not in titles
    assert "الانسحاب" not in titles


def test_a_wrong_weekday_is_flagged_not_corrected():
    html = """
    <html><body><h1>التقويم الجامعي 2026-2027م</h1>
    <table>
      <tr><th>الفصل الدراسي الأول</th><th>اليوم</th><th>التاريخ / ميلادي</th><th>التاريخ / هجري</th></tr>
      <tr><td>بداية الدراسة</td><td>الأربعاء</td><td>23 / 08 / 2026 م</td><td>10 / 03 / 1448 هـ</td></tr>
    </table></body></html>
    """
    event = parse_calendar(html).events[0]
    assert event.weekday == "الأربعاء"
    assert event.date_gregorian == "2026-08-23"
    assert event.weekday_matches is False


def test_arabic_indic_digits_and_a_repeated_header_are_handled():
    html = """
    <html><body><h1>التقويم الجامعي 2026-2027م</h1>
    <table>
      <tr><th>الفصل الدراسي الأول</th><th>اليوم</th><th>التاريخ / ميلادي</th><th>التاريخ / هجري</th></tr>
      <tr><td>بداية الدراسة</td><td>الأحد</td><td>٢٣ / ٠٨ / ٢٠٢٦ م</td><td>١٠ / ٠٣ / ١٤٤٨ هـ</td></tr>
      <tr><th>الفصل الدراسي الأول</th><th>اليوم</th><th>التاريخ / ميلادي</th><th>التاريخ / هجري</th></tr>
      <tr><td>بداية الاختبارات النهائية</td><td>الأحد</td><td>13 / 12 / 2026 م</td><td>04 / 07 / 1448 هـ</td></tr>
    </table></body></html>
    """
    events = parse_calendar(html).events
    assert [(event.title, event.date_gregorian) for event in events] == [
        ("بداية الدراسة", "2026-08-23"),
        ("بداية الاختبارات النهائية", "2026-12-13"),
    ]
    assert events[0].date_hijri == "1448-03-10"
    assert all(event.weekday_matches for event in events)


def test_rows_are_not_duplicated_when_a_table_repeats():
    html = """
    <html><body><h1>التقويم الجامعي 2026-2027م</h1>
    <table>
      <tr><th>الفصل الدراسي الأول</th><th>اليوم</th><th>التاريخ / ميلادي</th><th>التاريخ / هجري</th></tr>
      <tr><td>بداية الدراسة</td><td>الأحد</td><td>23 / 08 / 2026 م</td><td>10 / 03 / 1448 هـ</td></tr>
    </table>
    <table>
      <tr><th>الفصل الدراسي الأول</th><th>اليوم</th><th>التاريخ / ميلادي</th><th>التاريخ / هجري</th></tr>
      <tr><td>بداية الدراسة</td><td>الأحد</td><td>23 / 08 / 2026 م</td><td>10 / 03 / 1448 هـ</td></tr>
    </table></body></html>
    """
    assert len(parse_calendar(html).events) == 1


def test_broken_rows_are_skipped_and_an_empty_page_raises():
    html = """
    <html><body><h1>التقويم الجامعي 2026-2027م</h1>
    <table>
      <tr><th>الفصل الدراسي الأول</th><th>اليوم</th><th>التاريخ / ميلادي</th><th>التاريخ / هجري</th></tr>
      <tr><td>تاريخ غير صالح</td><td>الأحد</td><td>31 / 02 / 2026 م</td><td>10 / 03 / 1448 هـ</td></tr>
      <tr><td>بداية الدراسة</td><td>الأحد</td><td>23 / 08 / 2026 م</td><td>10 / 03 / 1448 هـ</td></tr>
    </table></body></html>
    """
    events = parse_calendar(html).events
    assert [event.title for event in events] == ["بداية الدراسة"]
    with pytest.raises(ParseError):
        parse_calendar("<html><body><h1>لا شيء</h1></body></html>")
    with pytest.raises(ParseError):
        parse_calendar(
            "<html><body><h1>التقويم 2026-2027</h1><table><tr><th>الفصل</th><th>اليوم</th>"
            "<th>ميلادي</th><th>هجري</th></tr></table></body></html>"
        )


def test_store_calendar_replaces_the_rows(tmp_path):
    path = calendar_db(tmp_path)
    conn = database.init_db(path)
    stored = conn.execute("SELECT COUNT(*) AS n FROM calendar_events").fetchone()["n"]
    store_calendar(conn, parse_calendar(fixture_html()), CALENDAR.url)
    again = conn.execute("SELECT COUNT(*) AS n FROM calendar_events").fetchone()["n"]
    conn.close()
    assert stored == again == 22


def test_get_calendar_finds_exam_rows(tmp_path):
    path = calendar_db(tmp_path)
    rows = get_calendar("الاختبارات النهائية", db_path=path)
    assert len(rows) == 6
    assert all("الاختبارات النهائية" in row["title"] for row in rows)
    assert rows[0]["source_url"] == CALENDAR.url
    assert rows[0]["scraped_at"]
    assert rows[0]["academic_year"] == "2026-2027"


def test_get_calendar_matches_spelling_variants(tmp_path):
    path = calendar_db(tmp_path)
    for topic in ("الاختبارات النهائية", "اختبارات نهائية", "اختبار نهائي"):
        assert len(get_calendar(topic, db_path=path)) == 6


def test_get_calendar_filters_by_term(tmp_path):
    path = calendar_db(tmp_path)
    first = get_calendar(term="الفصل الدراسي الأول", db_path=path)
    short = get_calendar(term="الأول", db_path=path)
    # The tool returns at most CALENDAR_LIMIT rows, so both calls are capped the same way.
    assert len(first) == len(short) == tools_module.CALENDAR_LIMIT
    assert all(row["term"] == "الفصل الدراسي الأول" for row in first)


def test_get_calendar_marks_past_rows_and_can_hide_them(tmp_path):
    path = calendar_db(tmp_path)
    every = get_calendar("بداية الدراسة", db_path=path, today=date(2026, 11, 15))
    upcoming = get_calendar(
        "بداية الدراسة", upcoming_only=True, db_path=path, today=date(2026, 11, 15)
    )
    assert len(upcoming) < len(every)
    assert all(row["status"] != "past" for row in upcoming)
    assert any(row["status"] == "past" for row in every)
    assert all(
        row["days_until_start"] >= 0 for row in upcoming if row["status"] == "upcoming"
    )


def test_get_calendar_reports_a_missing_row_and_an_empty_calendar(tmp_path):
    path = calendar_db(tmp_path)
    assert get_calendar("الحذف والإضافة", db_path=path) == {
        "error": "no matching calendar events"
    }
    assert get_calendar("سؤال لا يوجد له صف", term="الصيفي", db_path=path) == {
        "error": "no matching calendar events"
    }
    empty = tmp_path / "empty.sqlite"
    database.init_db(empty).close()
    assert get_calendar(db_path=empty) == {"error": "calendar is empty"}


def test_execute_tool_runs_get_calendar(tmp_path):
    path = calendar_db(tmp_path)
    rows = tools_module.execute_tool(
        "get_calendar", {"topic": "الاختبارات النهائية", "db_path": str(path)}
    )
    assert len(rows) == 6
