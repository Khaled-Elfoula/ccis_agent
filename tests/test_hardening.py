"""Tests for the hardening rules: stale data, safe logging and scrapers that keep old data."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from unittest.mock import AsyncMock
import agent.tools as tools_module
import agent.tools as tools_module
import bot.telegram as telegram_bot
import config
import db as database
import scrapers.run as run_module
from agent.loop import FRIENDLY_ERROR
from scrapers.base import ScrapeError
from scrapers.calendar import parse_calendar
from scrapers.ccis import parse
from scrapers.profiles import OfficeHour, Profile
from scrapers.sources import CALENDAR, FACULTY_LISTS, SOURCES, SOURCES_BY_ID
from agent.loop import FRIENDLY_ERROR
from scrapers.base import ScrapeError
from scrapers.calendar import parse_calendar
from scrapers.ccis import ParsedPage, parse
from scrapers.profiles import OfficeHour, Profile
from scrapers.sources import CALENDAR, FACULTY_LISTS, SOURCES, SOURCES_BY_ID

FIXTURE = Path(__file__).parent / "fixtures" / "calendar" / "current.html"
SCRAPED_AT = "2026-11-10T10:00:00+00:00"
PAGE_HTML = """
<html lang="ar"><body><div role="main"><h1>اتصل بنا</h1>
<p>للتواصل مع الكلية عبر القنوات التالية: وحدة العلاقات العامة والإعلام، هاتف 0114697568.</p>
<p>نموذج طلب معادلة مقرر متاح في مكتب الشؤون التعليمية والأكاديمية خلال ساعات العمل الرسمية.</p>
</div></body></html>
"""


def iso(days_ago: int) -> str:
    """An ISO timestamp that many days before now."""
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(timespec="seconds")


def test_a_recent_result_is_not_flagged_stale():
    fresh = tools_module._with_staleness({"scraped_at": iso(1), "title": "x"})
    assert fresh == {"scraped_at": iso(1), "title": "x"}


def test_an_old_result_is_flagged_with_its_date(monkeypatch):
    monkeypatch.setattr(config, "STALE_AFTER_DAYS", 30)
    old = iso(31)
    result = tools_module._with_staleness({"scraped_at": old, "title": "x"})
    assert result["stale"] is True
    assert old[:10] in result["stale_note"]
    assert "30" in result["stale_note"]


def test_staleness_reaches_lists_and_nested_contacts(monkeypatch):
    monkeypatch.setattr(config, "STALE_AFTER_DAYS", 30)
    old = iso(31)
    rows = tools_module._with_staleness([{"scraped_at": old}, {"scraped_at": iso(1)}])
    assert rows[0]["stale"] is True
    assert "stale" not in rows[1]
    nested = tools_module._with_staleness({"faculty": {"scraped_at": old}})
    assert nested["faculty"]["stale"] is True


def test_errors_and_missing_dates_are_left_alone():
    assert tools_module._with_staleness({"error": "no match found"}) == {"error": "no match found"}
    assert tools_module._with_staleness("plain") == "plain"
    assert "stale" not in tools_module._with_staleness({"scraped_at": "not a date"})


def test_execute_tool_flags_a_stale_office_hours_row(tmp_path, monkeypatch):
    path = tmp_path / "stale.sqlite"
    conn = database.init_db(path)
    conn.execute(
        "INSERT INTO faculty (username, name_ar, email, email_source, profile_url, scraped_at)"
        " VALUES ('old', 'سعد الأحمدي', 'old@ksu.edu.sa', 'scraped',"
        " 'https://faculty.ksu.edu.sa/ar/old', ?)",
        (iso(90),),
    )
    faculty_id = conn.execute("SELECT id FROM faculty WHERE username = 'old'").fetchone()["id"]
    conn.execute(
        "INSERT INTO office_hours (faculty_id, day, start, end, source_url, scraped_at)"
        " VALUES (?, 'Sunday', '09:00', '10:00', 'https://faculty.ksu.edu.sa/ar/old', ?)",
        (faculty_id, iso(90)),
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(tools_module, "load_overrides", lambda: {})
    monkeypatch.setattr(config, "STALE_AFTER_DAYS", 30)

    result = tools_module.execute_tool(
        "get_office_hours", {"faculty_name": "سعد الاحمدي", "db_path": str(path)}
    )
    assert result["stale"] is True
    assert result["faculty"]["stale"] is True


def test_turn_intent_labels_the_tools():
    assert telegram_bot.turn_intent([]) == "answer"
    assert telegram_bot.turn_intent(["get_calendar"]) == "calendar"
    assert telegram_bot.turn_intent(["find_contact", "get_office_hours"]) == "contact,office_hours"
    assert telegram_bot.turn_intent(["search_kb", "search_kb"]) == "kb"


def test_a_turn_logs_chat_time_intent_and_tools_but_never_the_text(caplog):
    class FakeMessage:
        text = "my password is hunter2"

        def __init__(self) -> None:
            self.replies: list[str] = []

        async def reply_text(self, text: str) -> None:
            self.replies.append(text)

    message = FakeMessage()
    agent = SimpleNamespace(last_tools={7: ["search_kb"]}, reply=lambda chat_id, text: "answer")
    update = SimpleNamespace(
        effective_chat=SimpleNamespace(id=7), effective_message=message
    )
    context = SimpleNamespace(
        bot=AsyncMock(),
        application=SimpleNamespace(
            bot_data={"agent": agent, "limiter": telegram_bot.ChatLimiter()}
        ),
    )
    with caplog.at_level(logging.INFO, logger="bot.telegram"):
        asyncio.run(telegram_bot.on_text(update, context))

    assert "chat=7" in caplog.text
    assert "intent=kb" in caplog.text
    assert "tools=search_kb" in caplog.text
    assert "hunter2" not in caplog.text
    assert message.replies == ["answer"]


def test_the_bot_answers_friendly_when_the_agent_raises(caplog):
    class ExplodingAgent:
        def reply(self, chat_id: int, text: str) -> str:
            raise RuntimeError("model down")

    update = SimpleNamespace(effective_chat=SimpleNamespace(id=5))
    context = SimpleNamespace(
        bot=AsyncMock(), application=SimpleNamespace(bot_data={"agent": ExplodingAgent()})
    )
    assert asyncio.run(telegram_bot._answer(update, context, "hi")) == FRIENDLY_ERROR
    assert "chat=5" in caplog.text


def test_a_failed_page_parse_keeps_the_stored_page(tmp_path, monkeypatch):
    path = tmp_path / "pages.sqlite"
    monkeypatch.setattr(config, "DB_PATH", path)
    conn = database.init_db(path)
    source = SOURCES_BY_ID["ccis_home"]
    stored = run_module.store_page(conn, source.id, source.url, parse(PAGE_HTML))
    assert stored

    def broken(*args, **kwargs):
        raise ScrapeError("HTTP 503")

    monkeypatch.setattr(run_module, "fetch", broken)
    assert run_module.scrape_pages(conn, {source.id}) == 1

    assert conn.execute("SELECT COUNT(*) AS n FROM pages").fetchone()["n"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM chunks").fetchone()["n"] == stored
    conn.close()


def test_a_failed_calendar_parse_keeps_the_stored_rows(tmp_path, monkeypatch):
    path = tmp_path / "calendar.sqlite"
    monkeypatch.setattr(config, "DB_PATH", path)
    conn = database.init_db(path)
    run_module.store_calendar(conn, parse_calendar(FIXTURE.read_text(encoding="utf-8")), CALENDAR.url)

    def broken(*args, **kwargs):
        raise ScrapeError("HTTP 500")

    monkeypatch.setattr(run_module, "fetch", broken)
    assert run_module.scrape_calendar(conn) == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM calendar_events").fetchone()["n"] == 22
    conn.close()


def test_run_all_survives_every_failing_source_and_keeps_the_data(tmp_path, monkeypatch, capsys):
    path = tmp_path / "all.sqlite"
    monkeypatch.setattr(config, "DB_PATH", path)
    conn = database.init_db(path)
    source = SOURCES_BY_ID["ccis_home"]
    run_module.store_page(conn, source.id, source.url, parse(PAGE_HTML))
    run_module.store_calendar(
        conn, parse_calendar(FIXTURE.read_text(encoding="utf-8")), CALENDAR.url
    )
    conn.close()

    def broken(*args, **kwargs):
        raise ScrapeError("HTTP 403")

    monkeypatch.setattr(run_module, "fetch", broken)
    failures = run_module.run_all()

    assert failures == len(SOURCES) + len(FACULTY_LISTS) + 1
    conn = database.connect(path)
    assert conn.execute("SELECT COUNT(*) AS n FROM pages").fetchone()["n"] == 1
    assert conn.execute("SELECT COUNT(*) AS n FROM calendar_events").fetchone()["n"] == 22
    counts = run_module.table_counts(conn)
    conn.close()
    assert counts["pages"] == 1 and counts["calendar_events"] == 22
    output = capsys.readouterr().out
    assert "rows per table:" in output
    assert "calendar_events" in output


def test_store_faculty_keeps_a_derived_email_and_prefers_a_scraped_one(tmp_path):
    path = tmp_path / "faculty.sqlite"
    conn = database.init_db(path)
    conn.execute(
        "INSERT INTO faculty (username, name_ar, email, email_source, scraped_at)"
        " VALUES ('u', 'سعد الأحمدي', 'u@ksu.edu.sa', 'derived', ?)",
        (SCRAPED_AT,),
    )
    conn.commit()

    run_module.store_faculty(
        conn, "u", iso(1), profile=Profile(source_url="https://faculty.ksu.edu.sa/ar/u")
    )
    row = conn.execute("SELECT email, email_source FROM faculty WHERE username = 'u'").fetchone()
    assert (row["email"], row["email_source"]) == ("u@ksu.edu.sa", "derived")

    run_module.store_faculty(
        conn, "u", iso(1), profile=Profile(source_url="https://x", email="real@ksu.edu.sa")
    )
    row = conn.execute("SELECT email, email_source FROM faculty WHERE username = 'u'").fetchone()
    assert (row["email"], row["email_source"]) == ("real@ksu.edu.sa", "scraped")
    conn.close()


def test_store_office_hours_keeps_rows_when_the_parse_is_empty(tmp_path):
    path = tmp_path / "hours.sqlite"
    conn = database.init_db(path)
    conn.execute(
        "INSERT INTO faculty (username, name_ar, scraped_at) VALUES ('u', 'سعد الأحمدي', ?)",
        (SCRAPED_AT,),
    )
    faculty_id = conn.execute("SELECT id FROM faculty WHERE username = 'u'").fetchone()["id"]
    run_module.store_office_hours(
        conn, faculty_id, [OfficeHour("Sunday", "09:00", "10:00")], "https://x", SCRAPED_AT
    )
    assert run_module.store_office_hours(conn, faculty_id, [], "https://x", iso(1)) == 1
    rows = conn.execute("SELECT day, start, end FROM office_hours").fetchall()
    assert [tuple(row) for row in rows] == [("Sunday", "09:00", "10:00")]

    replaced = run_module.store_office_hours(
        conn, faculty_id, [OfficeHour("Monday", "11:00", "12:00")], "https://x", iso(1)
    )
    rows = conn.execute("SELECT day, start, end FROM office_hours").fetchall()
    conn.close()
    assert replaced == 1
    assert [tuple(row) for row in rows] == [("Monday", "11:00", "12:00")]
