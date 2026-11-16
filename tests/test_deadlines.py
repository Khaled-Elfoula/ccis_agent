"""Tests for the academic-movements table, its storage and the merged get_calendar."""

from datetime import date
from pathlib import Path

import pytest

import db as database
from agent.tools import get_calendar
from scrapers.calendar import parse_calendar
from scrapers.ccis import ParseError
from scrapers.deadlines import parse_deadlines
from scrapers.run import store_calendar, store_deadlines
from scrapers.sources import CALENDAR, DEADLINES

FIXTURE = Path(__file__).parent / "fixtures" / "deadlines" / "academic_table.html"
CALENDAR_FIXTURE = Path(__file__).parent / "fixtures" / "calendar" / "current.html"
TODAY = date(2026, 11, 15)
FIRST = "الفصل الدراسي الأول للعام الدراسي 2026 /2027 م"
SECOND = "الفصل الدراسي الثاني للعام الدراسي 2026 /2027 م"


def fixture_html() -> str:
    """The saved copy of the academic-movements page."""
    return FIXTURE.read_text(encoding="utf-8")


def find(parsed, term: str, title: str):
    """Return the single row with this term and title, or None."""
    matches = [row for row in parsed.rows if row.term == term and row.title == title]
    assert len(matches) <= 1
    return matches[0] if matches else None


def test_academic_year_and_last_update_come_from_the_page():
    parsed = parse_deadlines(fixture_html())
    assert parsed.academic_year == "2026-2027"
    assert parsed.page_updated == "أغسطس 6, 2026 12:22م"


def test_every_movement_of_the_saved_page_is_parsed():
    parsed = parse_deadlines(fixture_html())
    assert len(parsed.rows) == 49
    terms = {row.term for row in parsed.rows}
    assert terms == {FIRST, SECOND, "الفصل الصيفي للعام الدراسي 2026 /2027 م"}


def test_the_expected_windows_from_the_live_page():
    """The five rows the milestone names, with their exact dates."""
    parsed = parse_deadlines(fixture_html())
    expected = {
        (FIRST, "الاعتذار عن مقرر دراسي / فصل دراسي"): ("2026-09-13", "2026-11-28"),
        (FIRST, "الانسحاب من الجامعة"): ("2026-07-26", "2026-12-10"),
        (
            FIRST,
            "الحذف والإضافة للمقررات الدراسية من خلال البوابة الإلكترونية"
            " (وفق الجدول الزمني للتسجيل)",
        ): ("2026-08-18", "2026-08-22"),
        (FIRST, "الاختبارات النهائية للفصل الدراسي الأول"): ("2026-12-13", "2026-12-26"),
        (SECOND, "الاعتذار عن مقرر دراسي / فصل دراسي"): ("2027-01-31", "2027-05-22"),
    }
    for key, dates in expected.items():
        row = find(parsed, *key)
        assert row is not None, key
        assert (row.start_date, row.end_date) == dates


def test_every_weekday_matches_its_gregorian_date():
    """A mismatch is flagged in the report, never silently corrected."""
    parsed = parse_deadlines(fixture_html())
    assert all(row.weekday_matches for row in parsed.rows)


def test_a_semester_start_has_one_date_and_no_note():
    parsed = parse_deadlines(fixture_html())
    starts = [row for row in parsed.rows if row.kind == "semester_start"]
    assert [row.title for row in starts] == [
        "بداية الفصل الدراسي الأول",
        "بداية الفصل الدراسي الثاني",
        "بداية الفصل الصيفي",
    ]
    assert [row.start_date for row in starts] == ["2026-08-23", "2027-01-10", "2027-06-22"]
    assert all(row.end_date is None for row in starts)
    assert all(row.note is None for row in starts)


def test_bold_rows_are_holidays_with_a_range():
    parsed = parse_deadlines(fixture_html())
    holidays = {row.title: (row.start_date, row.end_date) for row in parsed.rows if row.kind == "holiday"}
    assert holidays["إجازة اليوم الوطني"] == ("2026-09-23", "2026-09-26")
    assert holidays["إجازة الخريف"] == ("2026-11-22", "2026-11-28")
    assert holidays["إجازة منتصف العام الدراسي"] == ("2026-12-27", "2027-01-09")


def test_final_exams_are_exam_rows_with_a_range():
    parsed = parse_deadlines(fixture_html())
    exams = [row for row in parsed.rows if row.kind == "exam" and row.start_date]
    assert (FIRST, "الاختبارات النهائية للفصل الدراسي الأول") in [
        (row.term, row.title) for row in exams
    ]
    assert find(parsed, SECOND, "الاختبارات النهائية للفصل الدراسي الثاني").start_date == "2027-06-06"


def test_the_make_up_exam_row_keeps_its_text_and_no_dates():
    parsed = parse_deadlines(fixture_html())
    rows = [row for row in parsed.rows if row.title == "تقديم طلب اختبار نهائي بديل"]
    assert len(rows) == 2
    for row in rows:
        assert row.kind == "exam"
        assert row.start_date is None and row.end_date is None
        assert row.note == "تاريخ عقد الاختبار — اسبوع من تاريخ عقد الاختبار"


def test_the_starred_row_takes_the_footnote_as_its_note():
    parsed = parse_deadlines(fixture_html())
    rows = [row for row in parsed.rows if row.title == "أسبوع التعزيز الأكاديمي"]
    assert len(rows) == 2
    for row in rows:
        assert row.note.startswith("* أسبوع التعزيز الأكاديمي:")
        assert "تعزيز تحصيل الطلبة" in row.note


def test_arabic_indic_digits_are_converted():
    html = """
    <html><body><h2>الجدول الزمني 2026 /2027م</h2>
    <details><summary>الفصل الدراسي الأول للعام الدراسي 2026 /2027 م</summary>
    <table>
      <tr><td>الحركة الأكاديمية</td><td>البداية</td><td>النهاية</td></tr>
      <tr><td>الانسحاب من الجامعة</td><td>الأحد<br>٢٦ /٠٧ /٢٠٢٦ م</td>
          <td>الخميس<br>١٠ /١٢ /٢٠٢٦م</td></tr>
    </table></details></body></html>
    """
    row = parse_deadlines(html).rows[0]
    assert (row.start_date, row.end_date) == ("2026-07-26", "2026-12-10")
    assert row.start_weekday == "الأحد"
    assert row.end_weekday == "الخميس"
    assert row.weekday_matches is True


def test_a_wrong_weekday_is_flagged_not_corrected():
    html = """
    <html><body><h2>الجدول الزمني 2026 /2027م</h2>
    <details><summary>الفصل الدراسي الأول للعام الدراسي 2026 /2027 م</summary>
    <table>
      <tr><td>الحركة الأكاديمية</td><td>البداية</td><td>النهاية</td></tr>
      <tr><td>الانسحاب من الجامعة</td><td>الثلاثاء<br>26 /07 /2026 م</td>
          <td>الخميس<br>10 /12 /2026م</td></tr>
    </table></details></body></html>
    """
    row = parse_deadlines(html).rows[0]
    assert row.start_weekday == "الثلاثاء"
    assert row.start_date == "2026-07-26"
    assert row.weekday_matches is False


def test_a_repeated_table_does_not_duplicate_rows():
    html = """
    <html><body><h2>الجدول الزمني 2026 /2027م</h2>
    <details><summary>الفصل الدراسي الأول للعام الدراسي 2026 /2027 م</summary>
    <table>
      <tr><td>الحركة الأكاديمية</td><td>البداية</td><td>النهاية</td></tr>
      <tr><td>الانسحاب من الجامعة</td><td>الأحد<br>26 /07 /2026 م</td>
          <td>الخميس<br>10 /12 /2026م</td></tr>
    </table>
    </details>
    <details><summary>الفصل الدراسي الأول للعام الدراسي 2026 /2027 م</summary>
    <table>
      <tr><td>الحركة الأكاديمية</td><td>البداية</td><td>النهاية</td></tr>
      <tr><td>الانسحاب من الجامعة</td><td>الأحد<br>26 /07 /2026 م</td>
          <td>الخميس<br>10 /12 /2026م</td></tr>
    </table>
    </details></body></html>
    """
    assert len(parse_deadlines(html).rows) == 1


def test_an_empty_page_raises():
    with pytest.raises(ParseError):
        parse_deadlines("<html><body><h2>لا شيء</h2></body></html>")


def both_sources_db(tmp_path):
    """A temporary database holding the calendar page and the academic table."""
    path = tmp_path / "both.sqlite"
    conn = database.init_db(path)
    store_calendar(
        conn, parse_calendar(CALENDAR_FIXTURE.read_text(encoding="utf-8")), CALENDAR.url
    )
    store_deadlines(conn, parse_deadlines(fixture_html()), DEADLINES.url)
    conn.close()
    return path


def test_store_deadlines_replaces_the_rows(tmp_path):
    path = both_sources_db(tmp_path)
    conn = database.init_db(path)
    stored = conn.execute("SELECT COUNT(*) AS n FROM academic_deadlines").fetchone()["n"]
    store_deadlines(conn, parse_deadlines(fixture_html()), DEADLINES.url)
    again = conn.execute("SELECT COUNT(*) AS n FROM academic_deadlines").fetchone()["n"]
    conn.close()
    assert stored == again == 49


def test_get_calendar_finds_the_withdrawal_window_and_its_status(tmp_path):
    path = both_sources_db(tmp_path)
    rows = get_calendar("الاعتذار عن مقرر", db_path=path, today=TODAY)
    current = [row for row in rows if row["title"] == "الاعتذار عن مقرر دراسي / فصل دراسي"]
    first = [row for row in current if row["term"] == FIRST][0]
    assert first["status"] == "open_now"
    assert first["start_date"] == "2026-09-13"
    assert first["end_date"] == "2026-11-28"
    assert first["days_left_until_end"] == 13
    assert first["weekdays"] == "الأحد – السبت"
    assert first["source_url"] == DEADLINES.url
    assert first["kind"] == "action"


def test_a_course_withdrawal_also_reaches_add_and_drop(tmp_path):
    path = both_sources_db(tmp_path)
    titles = {row["title"] for row in get_calendar("انسحاب من مقرر", db_path=path, today=TODAY)}
    assert "الاعتذار عن مقرر دراسي / فصل دراسي" in titles
    assert any(title.startswith("الحذف والإضافة") for title in titles)
    assert "الانسحاب من الجامعة" not in titles


def test_withdrawing_from_the_university_is_a_different_row(tmp_path):
    path = both_sources_db(tmp_path)
    rows = get_calendar("الانسحاب من الجامعة", db_path=path, today=TODAY)
    assert [row["title"] for row in rows] == ["الانسحاب من الجامعة", "الانسحاب من الجامعة"]
    assert rows[0]["status"] == "open_now"
    assert rows[0]["end_date"] == "2026-12-10"


def test_a_bare_withdraw_returns_both_kinds(tmp_path):
    path = both_sources_db(tmp_path)
    titles = {row["title"] for row in get_calendar("withdraw", db_path=path, today=TODAY)}
    assert "الانسحاب من الجامعة" in titles
    assert "الاعتذار عن مقرر دراسي / فصل دراسي" in titles
    assert any(title.startswith("الحذف والإضافة") for title in titles)


def test_the_english_and_arabic_phrases_resolve_to_the_same_rows(tmp_path):
    path = both_sources_db(tmp_path)
    arabic = get_calendar("انسحاب من الجامعة", db_path=path, today=TODAY)
    english = get_calendar("withdraw from the university", db_path=path, today=TODAY)
    assert [row["title"] for row in arabic] == [row["title"] for row in english]


def test_final_exams_come_from_both_sources(tmp_path):
    path = both_sources_db(tmp_path)
    rows = get_calendar("الاختبارات النهائية", db_path=path, today=TODAY)
    sources = {row["source_url"] for row in rows}
    assert sources == {CALENDAR.url, DEADLINES.url}
    assert any(row["title"] == "الاختبارات النهائية للفصل الدراسي الأول" for row in rows)
    assert all(row["kind"] == "exam" for row in rows)


def test_a_make_up_exam_row_has_no_status_and_keeps_its_text(tmp_path):
    path = both_sources_db(tmp_path)
    rows = get_calendar("اختبار بديل", db_path=path, today=TODAY)
    assert rows
    for row in rows:
        assert row["status"] is None
        assert row["start_date"] is None
        assert row["note"] == "تاريخ عقد الاختبار — اسبوع من تاريخ عقد الاختبار"


def test_the_other_synonyms_reach_their_rows(tmp_path):
    path = both_sources_db(tmp_path)
    assert get_calendar("تأجيل", db_path=path, today=TODAY)
    assert get_calendar("عذر غياب", db_path=path, today=TODAY)
    assert get_calendar("تحويل داخلي", db_path=path, today=TODAY)
    assert get_calendar("إعفاء", db_path=path, today=TODAY)
    holidays = get_calendar("holidays", db_path=path, today=TODAY)
    assert any(row["kind"] == "holiday" for row in holidays)


def test_upcoming_only_drops_rows_that_have_ended(tmp_path):
    path = both_sources_db(tmp_path)
    every = get_calendar("الحذف والإضافة", db_path=path, today=TODAY)
    upcoming = get_calendar("الحذف والإضافة", upcoming_only=True, db_path=path, today=TODAY)
    assert len(upcoming) < len(every)
    assert all(row["status"] != "past" for row in upcoming)
    assert any(row["status"] == "past" for row in every)


def test_the_term_filter_reaches_the_academic_table(tmp_path):
    path = both_sources_db(tmp_path)
    rows = get_calendar(
        "الاعتذار عن مقرر", term="الفصل الدراسي الأول", db_path=path, today=TODAY
    )
    assert all(row["term"] == FIRST for row in rows)


def test_results_are_capped_and_the_result_shape_is_the_same_for_both_sources(tmp_path):
    path = both_sources_db(tmp_path)
    rows = get_calendar(db_path=path, today=TODAY)
    assert len(rows) == 8
    keys = {
        "title", "term", "academic_year", "kind", "start_date", "end_date",
        "weekdays", "date_hijri", "note", "status", "source_url", "scraped_at",
    }
    assert all(keys <= set(row) for row in rows)


def test_an_unknown_topic_is_not_guessed(tmp_path):
    path = both_sources_db(tmp_path)
    assert get_calendar("سؤال لا يوجد له صف", db_path=path, today=TODAY) == {
        "error": "no matching calendar events"
    }
