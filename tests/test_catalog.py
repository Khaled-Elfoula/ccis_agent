"""Tests for the SE course catalog parser, its storage and the course tools."""

from pathlib import Path

import pytest

import db as database
from agent.tools import find_course, list_courses
from scrapers.catalog import normalize_code, parse_catalog
from scrapers.ccis import ParseError
from scrapers.run import store_courses
from scrapers.sources import COURSE_CATALOG

FIXTURE = Path(__file__).parent / "fixtures" / "catalog" / "se_course_catalog.html"


def fixture_html() -> str:
    """The saved copy of the catalog page."""
    return FIXTURE.read_text(encoding="utf-8")


def parsed():
    """The parsed catalog page."""
    return parse_catalog(fixture_html())


def course(catalog, code: str):
    """Return the course with this normalised code, or None."""
    matches = [item for item in catalog.courses if item.code_norm == code]
    assert len(matches) <= 1
    return matches[0] if matches else None


def catalog_db(tmp_path):
    """A temporary database holding the saved catalog page."""
    path = tmp_path / "courses.sqlite"
    conn = database.init_db(path)
    store_courses(conn, parsed(), COURSE_CATALOG.url)
    conn.close()
    return path


def test_the_whole_page_is_parsed():
    catalog = parsed()
    assert len(catalog.courses) == 82
    assert course(catalog, "هاب211").name_ar == "دخل إلى هندسة البرمجيات"


def test_arabic_indic_digits_become_ascii():
    assert normalize_code("هاب٣١٢") == "هاب312"
    assert normalize_code("هاب ٣١٢") == "هاب312"
    catalog = parsed()
    assert course(catalog, "هاب312").number == 312


def test_hours_are_broken_down():
    catalog = parsed()
    lab_course = course(catalog, "هاب444")
    assert (lab_course.credit_hours, lab_course.lecture_hours) == (2, 0)
    assert (lab_course.lab_hours, lab_course.tutorial_hours) == (4, 1)
    training = course(catalog, "هاب479")
    assert (training.credit_hours, training.lecture_hours) == (1, 0)
    assert (training.lab_hours, training.tutorial_hours) == (0, 0)


def test_the_course_with_no_name_stays_empty():
    empty = course(parsed(), "هاب314")
    assert empty.name_ar is None
    assert empty.description_ar
    assert empty.code_display == "هاب٣١٤"


def test_a_credit_hour_prerequisite_is_not_read_as_a_code():
    training = course(parsed(), "هاب479")
    assert training.prerequisites_text == "95 ساعة"
    assert training.prerequisite_codes == []
    assert training.prerequisite_min_credit_hours == 95


def test_codes_are_read_from_a_plus_joined_prerequisite():
    first = course(parsed(), "هاب211")
    assert first.prerequisites_text == "ريض١٥١ + عال١١١"
    assert first.prerequisite_codes == ["ريض151", "عال111"]
    assert first.prerequisite_min_credit_hours is None


def test_a_co_requisite_is_kept():
    modelling = course(parsed(), "هاب313")
    assert modelling.corequisites_text == "نال٢٣٠"
    assert modelling.corequisite_codes == ["نال230"]
    assert modelling.prerequisite_codes == ["هاب211"]


def test_empty_prerequisites_and_co_requisites_stay_empty():
    graduate = course(parsed(), "هاب501")
    assert graduate.prerequisites_text is None
    assert graduate.prerequisite_codes == []
    assert graduate.corequisites_text is None
    assert graduate.corequisite_codes == []


def test_a_graduate_course_keeps_its_persian_letters_and_level():
    graduate = course(parsed(), "هاب501")
    assert graduate.name_ar == "ھندسة البرمجیات الشیئیة المتقدمة"
    assert graduate.level == "graduate"
    assert graduate.number == 501
    assert course(parsed(), "هاب312").level == "bachelor"


def test_anomalies_are_reported_not_fixed():
    catalog = parsed()
    report = "\n".join(catalog.anomalies)
    assert "هاب314: the name is not listed" in report
    assert "هاب699: the hours text 'وحدة دراسية واحدة' was not parsed" in report
    assert "table 64: the code row has no label" in report
    # The unlabelled code is still read, and nothing is invented for the unparsed hours.
    # The unlabelled code is still read, and nothing is invented for the unparsed hours.
    assert course(catalog, "هاب603").name_ar == "مواضيع متقدمة في عمارة البرمجيات"
    assert course(catalog, "هاب699").credit_hours is None


def test_the_first_digit_of_the_number_decides_the_level():
    catalog = parsed()
    # 2-4 are bachelor, 5-7 are graduate.
    assert course(catalog, "هاب211").level == "bachelor"
    assert course(catalog, "هاب444").level == "bachelor"
    assert course(catalog, "هاب603").level == "graduate"
    assert course(catalog, "هاب699").level == "graduate"
    assert course(catalog, "شامل700").level == "graduate"
    # Every course now has a level, so the page reports no level anomaly.
    assert not [note for note in catalog.anomalies if "level" in note]


def test_a_duplicate_code_is_reported():
    html = """
    <html><body>
    <table><tr><td>هاب٣١٢</td><td>رمز المقرر</td></tr>
      <tr><td>هندسة متطلبات البرمجيات</td><td>اسم المقرر</td></tr>
      <tr><td>3(3+0+1)</td><td>عدد الساعات</td></tr>
      <tr><td></td><td>متطلب سابق</td></tr>
      <tr><td></td><td>متطلب مصاحب</td></tr>
      <tr><td>وصف</td><td>وصف المقرر</td></tr></table>
    <table><tr><td>رمز المقرر</td><td>هاب٣١٢</td></tr>
      <tr><td>اسم المقرر</td><td>نسخة ثانية</td></tr>
      <tr><td>عدد الساعات</td><td>3(3+0+1)</td></tr>
      <tr><td>متطلب سابق</td><td></td></tr>
      <tr><td>متطلب مصاحب</td><td></td></tr>
      <tr><td>وصف المقرر</td><td>وصف</td></tr></table>
    </body></html>
    """
    catalog = parse_catalog(html)
    assert len(catalog.courses) == 2
    assert "هاب312: the code appears in tables 1 and 2" in catalog.anomalies


def test_a_page_with_no_course_raises():
    with pytest.raises(ParseError):
        parse_catalog("<html><body><p>لا شيء</p></body></html>")


def test_store_courses_replaces_the_rows(tmp_path):
    path = catalog_db(tmp_path)
    conn = database.init_db(path)
    stored = conn.execute("SELECT COUNT(*) AS n FROM courses").fetchone()["n"]
    store_courses(conn, parsed(), COURSE_CATALOG.url)
    again = conn.execute("SELECT COUNT(*) AS n FROM courses").fetchone()["n"]
    row = conn.execute("SELECT * FROM courses WHERE code_norm = 'هاب313'").fetchone()
    conn.close()
    assert stored == again == 82
    assert row["corequisite_codes"] == '["نال230"]'
    assert row["source_url"] == COURSE_CATALOG.url


def test_find_course_accepts_a_code_in_any_form(tmp_path):
    path = catalog_db(tmp_path)
    for query in ("هاب312", "312 هاب", "هاب ٣١٢", "hab 312"):
        result = find_course(query, db_path=path)
        assert isinstance(result, list) and len(result) == 1, query
        assert result[0]["code"] == "هاب312"


def test_one_exact_code_returns_everything(tmp_path):
    path = catalog_db(tmp_path)
    result = find_course("هاب٣١٣", db_path=path)[0]
    assert result["name"] == "منهجيات و نمذجة البرمجيات"
    assert result["hours"] == {
        "credit": 3, "lecture": 3, "lab": 0, "tutorial": 1, "text": "3(3+0+1)"
    }
    assert result["prerequisites"]["codes"] == ["هاب211"]
    assert result["corequisites"]["codes"] == ["نال230"]
    assert result["description"]
    # No course in the catalog lists هاب313 as a prerequisite.
    assert result["unlocks"] == []


def test_unlocks_are_computed_at_query_time(tmp_path):
    path = catalog_db(tmp_path)
    result = find_course("هاب٣٣٣", db_path=path)[0]
    assert set(result["unlocks"]) == {"هاب434", "هاب444", "هاب466"}


def test_a_course_with_no_name_says_so_without_inventing_one(tmp_path):
    path = catalog_db(tmp_path)
    result = find_course("هاب314", db_path=path)[0]
    assert result["name"] is None
    assert result["description"]


def test_words_from_a_name_or_description_find_the_course(tmp_path):
    path = catalog_db(tmp_path)
    results = find_course("هندسة البرمجيات", db_path=path)
    assert 1 <= len(results) <= 5
    assert all("description" in row for row in results)
    assert all(len(row["description"] or "") <= 200 for row in results)


def test_a_graduate_name_with_persian_letters_is_found_by_its_arabic_spelling(tmp_path):
    path = catalog_db(tmp_path)
    assert find_course("هندسة البرمجيات الشيئية المتقدمة", db_path=path)[0]["code"] == "هاب501"


def test_the_level_filter_applies(tmp_path):
    path = catalog_db(tmp_path)
    assert find_course("312", level="graduate", db_path=path) == {"error": "no matching course"}
    assert find_course("501", level="graduate", db_path=path)[0]["code"] == "هاب501"


def test_an_unknown_course_is_an_error(tmp_path):
    path = catalog_db(tmp_path)
    assert find_course("هاب999", db_path=path) == {"error": "no matching course"}
    assert find_course("   ", db_path=path) == {"error": "empty query"}


def test_list_courses_returns_the_compact_list(tmp_path):
    path = catalog_db(tmp_path)
    everything = list_courses(db_path=path)
    bachelor = list_courses("bachelor", db_path=path)
    graduate = list_courses("graduate", db_path=path)
    assert len(everything) == 82
    # Every course falls in one of the two levels, so the lists add up to the whole catalog.
    assert len(bachelor) + len(graduate) == 82
    assert all(set(row) == {"code", "name", "credit_hours", "level"} for row in everything)
    assert [row["code"] for row in bachelor][:3] == ["هاب211", "هاب312", "هاب313"]
