"""Tests for search_kb against a temporary database."""

import pytest

import db as database
from agent.arabic import search_form
from agent.tools import execute_tool, search_kb

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
