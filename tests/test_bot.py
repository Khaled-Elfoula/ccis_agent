"""Tests for the Telegram adapter helpers."""

from bot.telegram import ChatLimiter, split_message, turn_intent


def test_short_message_is_sent_as_one_piece():
    assert split_message("مرحبا") == ["مرحبا"]


def test_long_message_is_split_under_the_limit():
    text = " ".join(["كلمة"] * 2000)
    pieces = split_message(text, limit=1000)
    assert len(pieces) > 1
    assert all(len(piece) <= 1000 for piece in pieces)
    assert " ".join(pieces) == text


def test_split_prefers_line_breaks():
    text = "\n".join(["سطر قصير" * 5] * 40)
    pieces = split_message(text, limit=300)
    assert all(len(piece) <= 300 for piece in pieces)
    assert all("\n" in piece for piece in pieces[:-1])


def test_limiter_allows_up_to_the_limit_per_chat():
    limiter = ChatLimiter(limit=2, window=60)
    assert limiter.allow(1)
    assert limiter.allow(1)
    assert not limiter.allow(1)
    assert limiter.allow(2)


def test_limiter_window_expires(monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr("bot.telegram.monotonic", lambda: clock["now"])
    limiter = ChatLimiter(limit=1, window=10)
    assert limiter.allow(1)
    assert not limiter.allow(1)
    clock["now"] += 11
    assert limiter.allow(1)


def test_turn_intent_labels_program_and_course_tools():
    assert turn_intent(["list_programs"]) == "programs"
    assert turn_intent(["find_course", "list_courses"]) == "courses"
