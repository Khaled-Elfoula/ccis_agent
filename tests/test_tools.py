"""Tests for the tools against a temporary database."""

import pytest

import agent.tools as tools_module
import db as database
from agent.arabic import search_form
from agent.tools import execute_tool, find_contact, search_kb

PAGES = [
    (
        "ccis_programs_masters",
        "https://ccis.ksu.edu.sa/ar/masters",
        "الماجستير",
        [
            "شروط القبول في برنامج الماجستير بعلوم الحاسب: تقدير لا يقل عن جيد جدا.",
            "يجب تقديم نتيجة اختبار اللغة الإنجليزية عند التقديم.",
        ],
    ),
    (
        "ccis_contact",
        "https://ccis.ksu.edu.sa/ar/contact-us",
        "اتصل بنا",
        ["للتواصل مع الكلية: وحدة العلاقات العامة والإعلام، هاتف 0114697568."],
    ),
]


@pytest.fixture()
def kb(tmp_path):
    """Create a small database with two pages and return its path."""
    path = tmp_path / "test.sqlite"
    conn = database.init_db(path)
    for source_id, url, title, chunks in PAGES:
        page_id = conn.execute(
            "INSERT INTO pages (source_id, url, title, lang, scraped_at) VALUES (?, ?, ?, 'ar', ?)",
            (source_id, url, title, "2026-11-10T10:00:00+00:00"),
        ).lastrowid
        conn.executemany(
            "INSERT INTO chunks (page_id, text, text_norm) VALUES (?, ?, ?)",
            [(page_id, chunk, search_form(chunk)) for chunk in chunks],
        )
    conn.commit()
    conn.close()
    return path


def search(path, query: str, **kwargs):
    """Run search_kb against the given database."""
    return search_kb(query, db_path=path, **kwargs)


def test_returns_passages_with_source_and_date(kb):
    results = search(kb, "شروط القبول في الماجستير")
    assert isinstance(results, list)
    first = results[0]
    assert first["source_url"] == "https://ccis.ksu.edu.sa/ar/masters"
    assert first["scraped_at"] == "2026-11-10T10:00:00+00:00"
    assert first["title"] == "الماجستير"
    assert "شروط القبول" in first["text"]


def test_ranks_the_relevant_page_first(kb):
    assert search(kb, "هاتف التواصل")[0]["source_url"].endswith("/contact-us")


def test_lang_filter_and_limit(kb):
    assert search(kb, "الكلية", lang="en") == {"error": "no results found"}
    assert len(search(kb, "الكلية", limit=1)) == 1


def test_empty_and_unsearchable_queries(kb):
    assert search(kb, "   ") == {"error": "empty query"}
    assert search(kb, "؟!") == {"error": "query has no searchable words"}


def test_missing_database_is_an_error(tmp_path):
    assert "error" in search(tmp_path / "missing.sqlite", "القبول")


def test_execute_tool_never_raises(tmp_path):
    assert "error" in execute_tool("no_such_tool", {})
    assert "error" in execute_tool("search_kb", {"unexpected": 1})


FACULTY = [
    (
        "salahmadi",
        "سعد عبدالله الأحمدي",
        "Saad Abdullah AlAhmadi",
        "أستاذ",
        "CS",
        "31",
        "2179",
        "salahmadi@ksu.edu.sa",
    ),
    (
        "msalem",
        "محمد عبدالله السالم",
        None,
        "أستاذ مساعد",
        "CS",
        "31",
        "2200",
        "msalem@ksu.edu.sa",
    ),
    (
        "msalem2",
        "محمد سعد السالم",
        None,
        "محاضر",
        "CS",
        "31",
        "2201",
        "msalem2@ksu.edu.sa",
    ),
    (
        "mshossain",
        "محمد شميم حسين",
        None,
        "أستاذ",
        "SE",
        "31",
        "2115",
        None,
    ),
]


@pytest.fixture()
def directory(tmp_path, monkeypatch):
    """A temporary database with four faculty rows and no name overrides."""
    path = tmp_path / "faculty.sqlite"
    conn = database.init_db(path)
    conn.executemany(
        "INSERT INTO faculty (username, name_ar, name_en, rank, department, building, room, "
        "email, profile_url, list_url, scraped_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (
                username,
                name_ar,
                name_en,
                rank,
                department,
                building,
                room,
                email,
                f"https://faculty.ksu.edu.sa/ar/{username}",
                f"https://ccis.ksu.edu.sa/ar/{department.lower()}/faculty-and-staff",
                "2026-11-10T10:00:00+00:00",
            )
            for username, name_ar, name_en, rank, department, building, room, email in FACULTY
        ],
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(tools_module, "load_overrides", lambda: {})
    return path


def test_find_contact_returns_one_person_without_source_metadata(directory):
    results = find_contact("د. سعد الاحمدي", db_path=directory)
    assert len(results) == 1
    person = results[0]
    assert person["name"] == "سعد عبدالله الأحمدي"
    assert person["name_en"] == "Saad Abdullah AlAhmadi"
    assert person["rank"] == "أستاذ"
    assert person["department"] == "CS"
    assert (person["building"], person["room"]) == ("31", "2179")
    assert person["email"] == "salahmadi@ksu.edu.sa"
    assert person["profile_url"] == "https://faculty.ksu.edu.sa/ar/salahmadi"
    assert "source_url" not in person
    assert "scraped_at" not in person


def test_find_contact_returns_candidates_when_the_name_is_ambiguous(directory):
    result = find_contact("محمد السالم", db_path=directory)
    assert result["status"] == "candidates, not confirmed"
    assert {person["name"] for person in result["candidates"]} == {
        "محمد عبدالله السالم",
        "محمد سعد السالم",
    }
    assert "did you mean" in result["message"]


def test_find_contact_resolves_the_short_english_name(directory):
    """The demo case: the short English form is him, not a candidate for somebody else."""
    results = find_contact("Dr. Saad Al-Ahmadi", db_path=directory)
    assert isinstance(results, list)
    assert results[0]["name_en"] == "Saad Abdullah AlAhmadi"
    assert results[0]["email"] == "salahmadi@ksu.edu.sa"


def test_find_contact_labels_a_below_threshold_match_as_unconfirmed(directory):
    """A shared family name only is a question for the user, never an answer."""
    result = find_contact("Saud AlAhmadi", db_path=directory)
    assert result["status"] == "candidates, not confirmed"
    assert [person["name_en"] for person in result["candidates"]] == ["Saad Abdullah AlAhmadi"]
    assert "did you mean" in result["message"]


def test_find_contact_does_not_invent_an_email(directory):
    results = find_contact("محمد شميم حسين", db_path=directory)
    person = results[0]
    assert person["email"] is None
    assert (person["building"], person["room"]) == ("31", "2115")
    assert person["profile_url"] == "https://faculty.ksu.edu.sa/ar/mshossain"


def test_find_contact_unknown_and_empty_names(directory):
    assert find_contact("خالد الغامدي", db_path=directory) == {"error": "no match found"}
    assert find_contact("   ", db_path=directory) == {"error": "empty name"}


def test_find_contact_on_an_empty_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(tools_module, "load_overrides", lambda: {})
    path = tmp_path / "empty.sqlite"
    database.init_db(path).close()
    assert find_contact("سعد الاحمدي", db_path=path) == {"error": "faculty directory is empty"}


def test_find_contact_uses_an_override_first(directory, monkeypatch):
    monkeypatch.setattr(tools_module, "load_overrides", lambda: {"سعد الاحمدي": "msalem2"})
    results = find_contact("سعد الاحمدي", db_path=directory)
    assert results[0]["name"] == "محمد سعد السالم"


def test_find_contact_missing_database_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(tools_module, "load_overrides", lambda: {})
    assert "error" in find_contact("سعد", db_path=tmp_path / "missing.sqlite")


def test_execute_tool_runs_find_contact(directory):
    results = execute_tool(
        "find_contact", {"person_or_role": "محمد شميم حسين", "db_path": str(directory)}
    )
    assert results[0]["name"] == "محمد شميم حسين"
    assert "error" in execute_tool("find_contact", {})
