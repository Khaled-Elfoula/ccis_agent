"""Tests for CCIS page cleaning, faculty list parsing and faculty profiles."""

from pathlib import Path

import pytest
from bs4 import BeautifulSoup

import db as database
from scrapers import programs
from scrapers.ccis import ParseError, chunk_text, parse
from scrapers.faculty import FacultyRow, parse_office, parse_phone, parse_list
from scrapers.profiles import (
    Profile,
    parse_email,
    parse_office_address,
    parse_office_hours,
    parse_profile,
    to_24h,
)
from scrapers.run import store_faculty
from scrapers.sources import username_from_url

FIXTURES = Path(__file__).parent / "fixtures" / "faculty"


def fixture(name: str) -> str:
    """Read a trimmed copy of a real CCIS page."""
    return (FIXTURES / name).read_text(encoding="utf-8")


PAGE = """
<html lang="ar"><head><title>اتصل بنا | كلية علوم الحاسب والمعلومات</title></head>
<body>
<nav>القائمة الرئيسية</nav>
<div role="main">
  <h1>اتصل بنا</h1>
  <div class="breadcrumb">الرئيسية >> اتصل بنا</div>
  <p>للتواصل مع الكلية عبر القنوات التالية:</p>
  <p>وحدة العلاقات العامة والإعلام، هاتف 0114697568.</p>
  <p>نموذج طلب معادلة مقرر</p>
  <p>(PDF)</p>
  <div class="newsstyle"><a>خبر قديم</a></div>
  <p>تاريخ آخر تحديث : يناير 2026</p>
  <p>شارك هذه الصفحة</p>
  <script>alert(1)</script>
</div>
<footer>جميع الحقوق محفوظة</footer>
</body></html>
"""


def test_parse_keeps_content_and_drops_boilerplate():
    page = parse(PAGE)
    assert page.title == "اتصل بنا"
    assert page.lang == "ar"
    assert "0114697568" in page.text
    assert "نموذج طلب معادلة مقرر" in page.text
    for noise in ("القائمة الرئيسية", "الرئيسية >>", "خبر قديم", "تاريخ آخر تحديث",
                  "شارك هذه الصفحة", "جميع الحقوق محفوظة", "alert", "(PDF)"):
        assert noise not in page.text
    assert page.chunks


def test_parse_raises_when_the_layout_changes():
    with pytest.raises(ParseError):
        parse("<html><body><div role='main'>short</div></body></html>")


def test_chunk_text_respects_size_and_overlaps():
    text = "\n".join(f"سطر رقم {index} في صفحة الكلية" for index in range(60))
    chunks = chunk_text(text, size=200, overlap=50)
    assert len(chunks) > 1
    assert all(len(chunk) <= 200 for chunk in chunks)
    assert chunks[0].splitlines()[-1] in chunks[1]


def test_chunk_text_splits_a_very_long_line():
    chunks = chunk_text("كلمة " * 400, size=200, overlap=20)
    assert all(len(chunk) <= 200 for chunk in chunks)


def test_se_list_reads_office_and_phone_from_one_cell():
    rows = parse_list(
        fixture("se_list.html"),
        "SE",
        "https://ccis.ksu.edu.sa/ar/se/se-faculty-and-staff",
    )
    first = rows[0]
    assert first.username == "mshossain"
    assert first.name_ar == "أ.د. محمد شميم حسين"
    assert (first.building, first.room) == ("31", "2115")
    assert first.phone == "4676189"
    assert first.department == "SE"


def test_cs_list_takes_the_rank_from_the_section_heading():
    rows = parse_list(
        fixture("cs_list.html"), "CS", "https://cs.ksu.edu.sa/ar/cs/faculty-and-staff"
    )
    by_username = {row.username: row for row in rows}
    assert by_username["salahmadi"].rank == "أستاذ"
    assert by_username["salahmadi"].department == "CS"
    assert (by_username["salahmadi"].building, by_username["salahmadi"].room) == ("31", "2179")
    assert by_username["salahmadi"].phone == "4695212"


def test_it_list_reads_phone_and_office_columns():
    rows = parse_list(
        fixture("it_list.html"),
        "IT",
        "https://ccis.ksu.edu.sa/ar/it/it-faculty-and-staff",
    )
    first = rows[0]
    assert first.username == "dalsaeed"
    assert (first.building, first.room) == ("06", "T035")
    assert first.phone == "8058028"


def test_is_list_reads_the_rank_column():
    rows = parse_list(
        fixture("is_list.html"), "IS", "https://ccis.ksu.edu.sa/is/is-faculty-and-staff"
    )
    first = rows[0]
    assert first.username == "aasanad"
    assert first.rank == "أستاذ"
    assert (first.building, first.room) == ("31", "2043")
    assert first.phone == "4676606"


def test_ce_cards_are_parsed_without_a_table():
    rows = parse_list(
        fixture("ce_cards.html"),
        "CE",
        "https://ccis.ksu.edu.sa/ar/ce/faculty-and-staff",
    )
    assert [row.username for row in rows] == ["swakeel", "msuliman"]
    assert rows[0].room == "2206"
    assert rows[0].building is None
    assert rows[0].phone == "77101"
    assert rows[0].rank == "أستاذ"


def test_parse_list_raises_when_the_layout_changes():
    with pytest.raises(ParseError):
        parse_list("<html><body><p>لا يوجد شيء</p></body></html>", "CS", "https://example")


def test_parse_office_handles_either_order_and_missing_rooms():
    assert parse_office("مكتب: 31|2179") == ("31", "2179")
    assert parse_office("06|T035") == ("06", "T035")
    assert parse_office("T124|06") == ("06", "T124")
    assert parse_office("مكتب: 31| هاتف: 4676189") == ("31", None)
    assert parse_office("لا يوجد") == (None, None)


def test_parse_phone_rejects_placeholders():
    assert parse_phone("هاتف: 4695212") == "4695212"
    assert parse_phone("8058028") == "8058028"
    assert parse_phone("هاتف: لا يوجد") is None
    assert parse_phone("31|2179") is None


def test_username_from_url_handles_every_link_shape():
    assert username_from_url("https://faculty.ksu.edu.sa/ar/salahmadi") == "salahmadi"
    assert username_from_url("https://faculty.ksu.edu.sa/en/eabdulqader") == "eabdulqader"
    assert username_from_url("http://fac.ksu.edu.sa/mshossain/") == "mshossain"
    assert username_from_url("https://faculty.ksu.edu.sa/ar/aalwadain/cv/83147") == "aalwadain"
    assert username_from_url("mailto:salahmadi@ksu.edu.sa") is None


def test_profile_reads_email_office_and_office_hours():
    profile = parse_profile(fixture("profile_hours.html"), "https://faculty.ksu.edu.sa/ar/x")
    assert profile.email == "salahmadi@ksu.edu.sa"
    assert (profile.building, profile.room) == ("31", "2179")
    assert profile.rank == "أستاذ"
    assert profile.name_ar == "سعد عبدالله الأحمدي"
    assert profile.name_en == "Saad Abdullah AlAhmadi"
    hours = {hour.day: (hour.start, hour.end) for hour in profile.office_hours}
    assert hours["Sunday"] == ("16:00", "18:00")
    assert hours["Wednesday"] == ("15:00", "17:00")
    assert "Friday" not in hours


def test_profile_without_hours_or_a_real_email():
    profile = parse_profile(fixture("profile_no_hours.html"), "https://faculty.ksu.edu.sa/ar/y")
    assert profile.email is None
    assert profile.office_hours == []
    assert (profile.building, profile.room) == ("31", "2115")
    assert profile.name_en == "M. Shamim Hossain"


def test_profile_raises_when_the_banner_is_missing():
    with pytest.raises(ParseError):
        parse_profile("<html><body>لا شيء</body></html>", "https://example")


def test_parse_email_never_guesses():
    assert parse_email("M.Shah@KSU.EDU.SA") == "m.shah@ksu.edu.sa"
    assert parse_email("mshossain") is None
    assert parse_email("") is None
    assert parse_email("not an address") is None


def test_parse_office_address_shapes():
    assert parse_office_address("Building 31 (CCIS Building) - 2nd Floor - Room 2179") == (
        "31",
        "2179",
    )
    assert parse_office_address("Room No. 2115, Building No. 31") == ("31", "2115")
    assert parse_office_address("Building 6 - 3rd floor- Office 35") == ("6", "35")
    assert parse_office_address("P.O.Box 51178 . Riyadh 11543") == (None, None)


def test_to_24h_reads_the_profile_time_format():
    assert to_24h("04:00 PM") == "16:00"
    assert to_24h("12:00 PM") == "12:00"
    assert to_24h("08:30") == "08:30"
    assert to_24h("_") == ""
    assert to_24h("غير محدد") == ""


def test_parse_office_hours_skips_days_without_times():
    html = """
    <section class="website__section working_hours"><table>
      <thead><tr><th></th><th>Saturday</th><th>الأحد</th></tr></thead>
      <tbody>
        <tr class="from"><td>من</td><td>_</td><td>09:00 AM</td></tr>
        <tr class="to"><td>الى</td><td>_</td><td>11:00 AM</td></tr>
      </tbody>
    </table></section>
    """
    section = BeautifulSoup(html, "lxml").select_one("section.working_hours")
    hours = parse_office_hours(section)
    assert [(hour.day, hour.start, hour.end) for hour in hours] == [("Sunday", "09:00", "11:00")]
    assert parse_office_hours(None) == []


def test_store_faculty_keeps_a_list_email_when_the_profile_has_none(tmp_path):
    conn = database.init_db(tmp_path / "faculty.sqlite")
    row = FacultyRow(
        username="listmail",
        name="د. سعد الأحمدي",
        department="CS",
        profile_url="https://faculty.ksu.edu.sa/ar/listmail",
        list_url="https://ccis.ksu.edu.sa/ar/cs/faculty-and-staff",
        email="list@ksu.edu.sa",
    )
    store_faculty(conn, "listmail", "2026-11-10T10:00:00+00:00", row=row)
    profile = Profile(source_url=row.profile_url, email=None)
    store_faculty(conn, "listmail", "2026-11-11T10:00:00+00:00", profile=profile)
    stored = conn.execute("SELECT email, email_source FROM faculty").fetchone()
    assert (stored["email"], stored["email_source"]) == ("list@ksu.edu.sa", "scraped")
    conn.close()


def test_store_faculty_prefers_a_profile_email(tmp_path):
    conn = database.init_db(tmp_path / "faculty.sqlite")
    row = FacultyRow(
        username="profilemail",
        name="د. سعد الأحمدي",
        department="CS",
        profile_url="https://faculty.ksu.edu.sa/ar/profilemail",
        list_url="https://ccis.ksu.edu.sa/ar/cs/faculty-and-staff",
        email="list@ksu.edu.sa",
    )
    store_faculty(conn, "profilemail", "2026-11-10T10:00:00+00:00", row=row)
    profile = Profile(source_url=row.profile_url, email="profile@ksu.edu.sa")
    store_faculty(conn, "profilemail", "2026-11-11T10:00:00+00:00", profile=profile)
    stored = conn.execute("SELECT email FROM faculty").fetchone()
    assert stored["email"] == "profile@ksu.edu.sa"
    conn.close()


PROGRAMS_PAGE = """
<div class="node__content">
  <p><a href="/ar/cs"><b>قسم علوم الحاسب</b></a></p>
  <p>بكالوريوس العلوم في علوم الحاسب</p>
  <p><a href="/ar/ce"><b>قسم هندسة الحاسب</b></a></p>
  <p>بكالوريوس العلوم في هندسة الحاسب</p>
</div>
"""


def test_programs_parse_tracks_the_department():
    rows = programs.parse(PROGRAMS_PAGE, "بكالوريوس", "https://ccis.ksu.edu.sa/ar/programs")
    assert [(row.department, row.name_ar) for row in rows] == [
        ("CS", "بكالوريوس العلوم في علوم الحاسب"),
        ("CE", "بكالوريوس العلوم في هندسة الحاسب"),
    ]
    assert all(row.requirements_ar is None for row in rows)


def test_programs_parse_reads_a_plain_department_heading():
    html = """
    <div class="node__content">
      <h2>قسم علوم الحاسب</h2>
      <p>بكالوريوس العلوم في علوم الحاسب</p>
    </div>
    """
    rows = programs.parse(html, "بكالوريوس", "https://ccis.ksu.edu.sa/ar/bsc")
    assert [(row.department, row.name_ar) for row in rows] == [
        ("CS", "بكالوريوس العلوم في علوم الحاسب")
    ]


def test_programs_parse_attaches_admission_requirements():
    html = """
    <div class="node__content">
      <p><a href="/ar/cs">علوم الحاسب</a></p>
      <ul>
        <li>درجة الماجستير في علوم الحاسب خيار الرسالة</li>
        <li>درجة الماجستير في علوم الحاسب خيار المقررات</li>
      </ul>
      <p>شروط القبول</p>
      <p>أن يكون المتقدم حاصلاً على درجة البكالوريوس.</p>
    </div>
    """
    rows = programs.parse(html, "ماجستير", "https://ccis.ksu.edu.sa/ar/masters")
    assert len(rows) == 2
    assert all(row.department == "CS" for row in rows)
    assert all("البكالوريوس" in (row.requirements_ar or "") for row in rows)


def test_programs_parse_raises_when_the_page_has_no_programs():
    with pytest.raises(ParseError):
        programs.parse("<div class='node__content'><p>صفحة فارغة</p></div>", "بكالوريوس", "url")
