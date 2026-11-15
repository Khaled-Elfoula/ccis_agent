"""Tests for derived emails, the email_source column and its migration."""

import sqlite3

import pytest

import agent.tools as tools_module
import db as database
from agent.tools import draft_email, find_contact, handoff
from scripts.fill_emails import derive_email, fill

SCRAPED_AT = "2026-11-10T10:00:00+00:00"
COLUMNS = (
    "username, name_ar, name_en, rank, department, building, room, email, email_source,"
    " profile_url, list_url, scraped_at"
)


def insert(conn, username, name_ar=None, email=None, email_source=None, department="CS"):
    """Insert one faculty row with the fields these tests care about."""
    conn.execute(
        f"INSERT INTO faculty ({COLUMNS}) VALUES (?, ?, ?, 'أستاذ', ?, '31', '2100', ?, ?, ?, ?, ?)",
        (
            username,
            name_ar,
            None,
            department,
            email,
            email_source,
            f"https://faculty.ksu.edu.sa/ar/{username}",
            "https://ccis.ksu.edu.sa/ar/cs/faculty-and-staff",
            SCRAPED_AT,
        ),
    )


@pytest.fixture()
def emails_db(tmp_path, monkeypatch):
    """A database whose scraped emails all follow the username pattern."""
    monkeypatch.setattr(tools_module, "load_overrides", lambda: {})
    path = tmp_path / "emails.sqlite"
    conn = database.init_db(path)
    insert(conn, "matching", "سعد الأحمدي", "matching@ksu.edu.sa", "scraped")
    insert(conn, "alsalem", "محمد السالم", "alsalem@ksu.edu.sa", "scraped")
    insert(conn, "noemail", "خالد الغامدي")
    insert(conn, "Mixed.Case_1-2", "نورة العتيبي")
    insert(conn, "bad name", "عبدالله العنزي")
    insert(conn, "bad@name", "مها الشهري")
    conn.commit()
    conn.close()
    return path


def test_derive_email_uses_the_exact_pattern():
    assert derive_email("alsalem") == "alsalem@ksu.edu.sa"
    assert derive_email("Abeer") == "abeer@ksu.edu.sa"
    assert derive_email("A.B_c-1") == "a.b_c-1@ksu.edu.sa"


def test_invalid_usernames_get_no_derived_email():
    for username in (None, "", "bad name", "bad@name", "user/slash", "أحمد"):
        assert derive_email(username) is None


def test_fill_derives_only_missing_emails_and_keeps_the_pattern(emails_db):
    summary = fill(db_path=emails_db)
    assert summary["aborted"] is False
    assert summary["derived"] == 2

    conn = database.connect(emails_db)
    rows = {row["username"]: row for row in conn.execute("SELECT * FROM faculty")}
    conn.close()
    assert rows["noemail"]["email"] == "noemail@ksu.edu.sa"
    assert rows["noemail"]["email_source"] == "derived"
    assert rows["Mixed.Case_1-2"]["email"] == "mixed.case_1-2@ksu.edu.sa"


def test_fill_never_overwrites_a_scraped_email(emails_db):
    conn = database.connect(emails_db)
    insert(conn, "odd", "سامي الوكيل", "somebody.else@ksu.edu.sa", "scraped")
    conn.commit()
    conn.close()

    fill(db_path=emails_db)

    conn = database.connect(emails_db)
    row = conn.execute("SELECT email, email_source FROM faculty WHERE username = 'odd'").fetchone()
    conn.close()
    assert row["email"] == "somebody.else@ksu.edu.sa"
    assert row["email_source"] == "scraped"


def test_invalid_username_rows_stay_without_an_email(emails_db):
    fill(db_path=emails_db)
    conn = database.connect(emails_db)
    rows = conn.execute(
        "SELECT username, email FROM faculty WHERE username IN ('bad name', 'bad@name')"
    ).fetchall()
    conn.close()
    assert len(rows) == 2
    assert all(row["email"] is None for row in rows)


def test_fill_stops_when_the_pattern_does_not_hold(tmp_path, monkeypatch):
    monkeypatch.setattr(tools_module, "load_overrides", lambda: {})
    path = tmp_path / "weak.sqlite"
    conn = database.init_db(path)
    insert(conn, "one", "سعد الأول", "one@ksu.edu.sa", "scraped")
    insert(conn, "two", "سعد الثاني", "different@ksu.edu.sa", "scraped")
    insert(conn, "three", "سعد الثالث", "another@ksu.edu.sa", "scraped")
    insert(conn, "target", "سعد الرابع")
    conn.commit()
    conn.close()

    summary = fill(db_path=path)

    assert summary["aborted"] is True
    assert summary["derived"] == 0
    conn = database.connect(path)
    row = conn.execute("SELECT email FROM faculty WHERE username = 'target'").fetchone()
    conn.close()
    assert row["email"] is None


def test_dead_profiles_are_labelled_derived_unverified(tmp_path, monkeypatch):
    monkeypatch.setattr(tools_module, "load_overrides", lambda: {})
    path = tmp_path / "dead.sqlite"
    conn = database.init_db(path)
    insert(conn, "one", "سعد الأول", "one@ksu.edu.sa", "scraped")
    insert(conn, "gone", "عبدالله الشعلان")
    conn.commit()
    conn.close()

    summary = fill(db_path=path, dead_path=tmp_path / "dead.json", dead={"gone": {"status": 404}})

    assert summary["derived_unverified"] == 1
    conn = database.connect(path)
    row = conn.execute("SELECT email, email_source FROM faculty WHERE username = 'gone'").fetchone()
    conn.close()
    assert row["email"] == "gone@ksu.edu.sa"
    assert row["email_source"] == "derived_unverified"


def test_find_contact_returns_email_source(emails_db):
    fill(db_path=emails_db)
    derived = find_contact("خالد الغامدي", db_path=emails_db)
    assert derived[0]["email"] == "noemail@ksu.edu.sa"
    assert derived[0]["email_source"] == "derived"

    scraped = find_contact("محمد السالم", db_path=emails_db)
    assert scraped[0]["email_source"] == "scraped"


def test_find_contact_reports_no_source_when_there_is_no_email(emails_db):
    person = find_contact("عبدالله العنزي", db_path=emails_db)[0]
    assert person["email"] is None
    assert person["email_source"] is None


def test_draft_email_and_handoff_carry_email_source(emails_db):
    fill(db_path=emails_db)
    draft = draft_email("noemail@ksu.edu.sa", "طلب تأجيل", "ar", db_path=emails_db)
    assert draft["email_source"] == "derived"

    result = handoff("انسحاب من مقرر", "noemail@ksu.edu.sa", "en", db_path=emails_db)
    assert result["email_source"] == "derived"
    assert "standard format" in result["message"]


def test_migration_adds_the_column_and_labels_existing_emails(tmp_path):
    path = tmp_path / "legacy.sqlite"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE faculty (id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL,"
        " name_ar TEXT, name_en TEXT, name_norm TEXT, rank TEXT, department TEXT,"
        " building TEXT, room TEXT, email TEXT, phone TEXT, profile_url TEXT, list_url TEXT,"
        " scraped_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO faculty (username, email, scraped_at) VALUES ('old', 'old@ksu.edu.sa', ?)",
        (SCRAPED_AT,),
    )
    conn.commit()
    conn.close()

    database.init_db(path).close()
    database.init_db(path).close()  # idempotent

    conn = database.connect(path)
    assert "email_source" in database.columns(conn, "faculty")
    row = conn.execute("SELECT email, email_source FROM faculty WHERE username = 'old'").fetchone()
    conn.close()
    assert row["email"] == "old@ksu.edu.sa"
    assert row["email_source"] == "scraped"
