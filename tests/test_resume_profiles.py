"""Tests for scripts/resume_profiles.py: dead marking, progress and stop rules."""

import db as database
from scripts import resume_profiles as rp
from scrapers.base import ScrapeError


class FakeProfile:
    """A parsed profile with the fields the store step reads."""

    def __init__(self, hours: int = 0) -> None:
        self.source_url = "https://faculty.ksu.edu.sa/ar/x"
        self.office_hours = [object()] * hours


def setup(tmp_path, monkeypatch, usernames: list[str]):
    """Point the script at a temp database and an in-memory dead list."""
    dead: dict[str, dict] = {}
    stored: set[str] = set()
    pauses: list[float] = []

    monkeypatch.setattr(rp, "all_usernames", lambda: list(usernames))
    monkeypatch.setattr(rp, "is_cached", lambda name: name in stored)
    monkeypatch.setattr(rp, "load_dead", lambda: dead)
    monkeypatch.setattr(rp, "save_dead", lambda value: None)
    real_init = database.init_db
    monkeypatch.setattr(rp.database, "init_db", lambda path: real_init(tmp_path / "t.sqlite"))
    monkeypatch.setattr(rp.config, "DB_PATH", tmp_path / "t.sqlite")

    def fake_store(conn, username, profile):
        stored.add(username)
        return len(profile.office_hours)

    monkeypatch.setattr(rp, "store_profile", fake_store)
    monkeypatch.setattr(rp.time, "monotonic", lambda: 0.0)
    return dead, stored, pauses


def test_dead_profiles_are_marked_and_stored_ones_are_counted(tmp_path, monkeypatch):
    usernames = ["a", "b", "c", "d"]
    dead, stored, pauses = setup(tmp_path, monkeypatch, usernames)

    def fetch(name):
        if name in {"b", "d"}:
            raise ScrapeError(f"https://faculty.ksu.edu.sa/ar/{name}: HTTP 404")
        return FakeProfile(hours=2), None

    summary = rp.run_batches(batch_size=2, pause_seconds=0, fetch=fetch, sleep=pauses.append)

    assert stored == {"a", "c"}
    assert set(dead) == {"b", "d"}
    assert dead["b"]["status"] == 404
    assert summary["stored"] == 2
    assert summary["dead"] == 2
    assert summary["stopped"] == "everything is done or marked dead"


def test_dead_profiles_are_not_retried_in_a_later_batch(tmp_path, monkeypatch):
    dead, stored, pauses = setup(tmp_path, monkeypatch, ["a", "b"])
    calls: list[str] = []

    def fetch(name):
        calls.append(name)
        raise ScrapeError(f"https://faculty.ksu.edu.sa/ar/{name}: HTTP 403")

    rp.run_batches(batch_size=1, pause_seconds=0, fetch=fetch, sleep=pauses.append)

    assert calls == ["a", "b"]


def test_three_empty_batches_stop_the_loop(tmp_path, monkeypatch):
    dead, stored, pauses = setup(tmp_path, monkeypatch, ["a", "b", "c", "d", "e"])
    calls: list[str] = []

    def fetch(name):
        calls.append(name)
        raise ScrapeError(f"https://faculty.ksu.edu.sa/ar/{name}: timed out")

    summary = rp.run_batches(batch_size=1, pause_seconds=0, fetch=fetch, sleep=pauses.append)

    assert len(calls) == 3
    assert summary["stopped"] == "3 batches in a row made no progress"
    assert summary["failed"] == 3


def test_two_slow_requests_stop_the_loop(tmp_path, monkeypatch):
    dead, stored, pauses = setup(tmp_path, monkeypatch, ["a", "b", "c"])
    clock = {"now": 0.0}
    monkeypatch.setattr(rp.time, "monotonic", lambda: clock["now"])

    def fetch(name):
        clock["now"] += 40.0
        raise ScrapeError(f"https://faculty.ksu.edu.sa/ar/{name}: timed out")

    summary = rp.run_batches(batch_size=3, pause_seconds=0, fetch=fetch, sleep=pauses.append)

    assert summary["stopped"] == "a request hung past 30 seconds twice in a row"
    assert summary["failed"] == 2
