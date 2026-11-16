"""Spec section 14 acceptance cases, with the model scripted instead of called.

The Gemini API is not reachable from tests, so each case drives the real agent loop
with a fake client that replays what a model would do: call a tool, then answer. What
is asserted is the part the system owns: the tool data handed to the model, the error
shapes, the tool budget and the toolset itself.
"""

from types import SimpleNamespace

import pytest
from google.genai import types

import agent.loop as loop_module
import db as database
from agent.arabic import search_form
from agent.loop import Agent
from agent.prompts import load_system_prompt
from agent.tools import HANDLERS, find_contact, search_kb

PAGES = [
    (
        "ccis_masters_ar",
        "https://ccis.ksu.edu.sa/ar/masters",
        "الماجستير",
        "ar",
        ["شروط القبول في برنامج الماجستير بعلوم الحاسب: تقدير لا يقل عن جيد جدا."],
    ),
    (
        "ccis_masters_en",
        "https://ccis.ksu.edu.sa/en/masters",
        "Master's Program",
        "en",
        ["Master's admission requires a grade of at least very good.", "An English test result is required."],
    ),
]

PROGRAMS = [
    (
        "ماجستير",
        "CS",
        "درجة الماجستير في علوم الحاسب خيار الرسالة",
        "https://ccis.ksu.edu.sa/ar/masters",
        "شروط القبول: تقدير لا يقل عن جيد جدا.",
    ),
    (
        "ماجستير",
        "SE",
        "درجة الماجستير في هندسة البرمجيات خيار الرسالة",
        "https://ccis.ksu.edu.sa/ar/masters",
        "شروط القبول: تقدير لا يقل عن جيد جدا.",
    ),
]


@pytest.fixture()
def kb(tmp_path):
    """A small knowledge base with one Arabic and one English page."""
    path = tmp_path / "kb.sqlite"
    conn = database.init_db(path)
    for source_id, url, title, lang, chunks in PAGES:
        page_id = conn.execute(
            "INSERT INTO pages (source_id, url, title, lang, scraped_at) VALUES (?, ?, ?, ?, ?)",
            (source_id, url, title, lang, "2026-11-10T10:00:00+00:00"),
        ).lastrowid
        conn.executemany(
            "INSERT INTO chunks (page_id, text, text_norm) VALUES (?, ?, ?)",
            [(page_id, chunk, search_form(chunk)) for chunk in chunks],
        )
    conn.executemany(
        "INSERT INTO programs (degree, department, name_ar, requirements_ar, source_url, scraped_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [
            (degree, department, name, requirements, url, "2026-11-10T10:00:00+00:00")
            for degree, department, name, url, requirements in PROGRAMS
        ],
    )
    conn.commit()
    conn.close()
    return path


def text_response(text: str) -> SimpleNamespace:
    """A model response that answers without calling a tool."""
    content = types.Content(role="model", parts=[types.Part(text=text)])
    return SimpleNamespace(text=text, function_calls=[], candidates=[SimpleNamespace(content=content)])


def tool_response(name: str, args: dict) -> SimpleNamespace:
    """A model response that asks for one tool call."""
    call = types.FunctionCall(name=name, args=args)
    content = types.Content(role="model", parts=[types.Part(function_call=call)])
    return SimpleNamespace(
        text=None, function_calls=[call], candidates=[SimpleNamespace(content=content)]
    )


class FakeClient:
    """Replays scripted responses and records the requests it received."""

    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self.models = self
        self.responses = list(responses)
        self.requests: list[dict] = []

    def generate_content(self, **kwargs):
        self.requests.append(kwargs)
        return self.responses.pop(0)


def make_agent(responses, max_tool_calls: int = 4) -> tuple[Agent, FakeClient]:
    """Build an Agent wired to a fake client."""
    client = FakeClient(responses)
    agent = Agent(
        client=client,
        model="test-model",
        system_prompt="test prompt",
        max_tool_calls=max_tool_calls,
        history_messages=10,
        fallback_model="",
    )
    return agent, client


def tool_results(client: FakeClient) -> list:
    """The tool results the loop sent back to the model, in order, once each."""
    results = []
    for content in client.requests[-1]["contents"]:
        for part in content.parts:
            response = getattr(part, "function_response", None)
            if response is not None:
                results.append(response.response["result"])
    return results


def test_case1_english_question_gets_an_answer_with_a_source_link(kb):
    """§14.1: the answer can cite a source_url that came from a tool result."""
    agent, client = make_agent(
        [
            tool_response(
                "list_programs",
                {"include_requirements": True, "db_path": str(kb)},
            ),
            text_response(
                "Master's admission needs a grade of very good or better. "
                "Source: https://ccis.ksu.edu.sa/ar/masters"
            ),
        ]
    )
    answer = agent.reply(1, "What are the admission requirements for the master program?")
    assert "https://ccis.ksu.edu.sa/ar/masters" in answer
    programs = tool_results(client)[0]
    assert programs[0]["source_url"] == "https://ccis.ksu.edu.sa/ar/masters"
    assert programs[0]["scraped_at"] == "2026-11-10T10:00:00+00:00"
    assert programs[0]["requirements"].startswith("شروط القبول")


def test_case1b_program_question_uses_list_programs(kb):
    """§14.15: a program question returns every program, not one passage."""
    agent, client = make_agent(
        [
            tool_response("list_programs", {"db_path": str(kb)}),
            text_response(
                "The college offers the master's programmes in CS and SE. "
                "Source: https://ccis.ksu.edu.sa/ar/masters"
            ),
        ]
    )
    answer = agent.reply(1, "What programs does the college offer?")
    assert "master" in answer.lower()
    programs = tool_results(client)[0]
    assert len(programs) == len(PROGRAMS)
    assert {row["name"] for row in programs} == {name for _, _, name, _, _ in PROGRAMS}
    assert all(row["source_url"].endswith("/masters") for row in programs)


def test_case2_arabic_question_gets_an_arabic_answer(kb):
    """§14.2: an Arabic question is answered in Arabic, and the prompt asks for that."""
    agent, client = make_agent(
        [
            tool_response(
                "search_kb", {"query": "شروط القبول في الماجستير", "db_path": str(kb)}
            ),
            text_response(
                "شروط القبول: تقدير لا يقل عن جيد جدا. المصدر: "
                "https://ccis.ksu.edu.sa/ar/masters"
            ),
        ]
    )
    answer = agent.reply(1, "ما هي شروط القبول في الماجستير؟")
    assert answer.startswith("شروط القبول")
    assert "المصدر" in answer
    assert "شروط القبول" in tool_results(client)[0][0]["text"]
    assert "Reply in the language the user wrote in" in load_system_prompt()


def test_case3_office_hours_are_labelled_and_nothing_is_booked(faculty_db):
    """§14.3: published office hours are returned with a profile link, and nothing is booked."""
    agent, client = make_agent(
        [
            tool_response(
                "get_office_hours",
                {
                    "faculty_name": "سعد الاحمدي",
                    "day": "Sunday",
                    "db_path": str(faculty_db),
                },
            ),
            text_response(
                "Dr. Saad's published office hours on Sunday are 10:00-11:00 "
                "(https://faculty.ksu.edu.sa/ar/withhours). Nothing was booked."
            ),
        ]
    )
    answer = agent.reply(1, "When is Dr. Saad AlAhmadi free on Sunday?")
    result = tool_results(client)[0]
    assert result["office_hours"] == [{"day": "Sunday", "start": "10:00", "end": "11:00"}]
    assert "source_url" not in result
    assert "scraped_at" not in result
    assert "published office hours" in answer
    assert "Nothing was booked" in answer


def test_case4_booking_request_offers_hours_and_email_without_booking(faculty_db):
    """§14.4: no tool can book, so the answer offers the published hours or the email."""
    agent, client = make_agent(
        [
            tool_response(
                "get_office_hours", {"faculty_name": "سامي الوكيل", "db_path": str(faculty_db)}
            ),
            text_response(
                "I can't book it. Dr. Alwakeel has no published office hours, "
                "but you can email swakeel@ksu.edu.sa to ask for a time."
            ),
        ]
    )
    answer = agent.reply(1, "Book me an appointment with Dr. Alwakeel")
    result = tool_results(client)[0]
    assert result["no_office_hours"] is True
    assert result["email"] == "swakeel@ksu.edu.sa"
    assert "email_source" not in result
    assert "I can't book it" in answer
    assert not {"book", "reserve", "appointment", "schedule_meeting"} & set(HANDLERS)


def test_case5_withdrawal_after_the_deadline_hands_off(faculty_db):
    """§14.5: a hand-off carries the contact and a template in the asked language."""
    contact = (
        "وكالة الكلية للشؤون التعليمية والأكاديمية "
        "https://ccis.ksu.edu.sa/ar/student-affairs-agency-mail"
    )
    agent, client = make_agent(
        [
            tool_response(
                "handoff",
                {
                    "reason": "انسحاب من مقرر بعد الموعد النهائي",
                    "contact": contact,
                    "lang": "ar",
                    "db_path": str(faculty_db),
                },
            ),
            text_response("أحوّلك إلى وكالة الكلية مع نموذج بريد جاهز."),
        ]
    )
    agent.reply(1, "أريد الانسحاب من مقرر بعد الموعد النهائي")
    message = tool_results(client)[0]["message"]
    assert "student-affairs-agency-mail" in message
    assert "انسحاب من مقرر بعد الموعد النهائي" in message
    assert "[Your name]" in message
    assert "[Your ID]" in message


def test_case6_no_source_returns_an_error_and_no_invented_facts(kb, faculty_db):
    """§14.6: an unanswerable lookup comes back as an error the model must not fill in."""
    assert search_kb("cafeteria menu", db_path=kb) == {"error": "no results found"}
    assert find_contact("خالد الغامدي", db_path=faculty_db) == {"error": "no match found"}

    agent, client = make_agent(
        [
            tool_response("search_kb", {"query": "cafeteria menu", "db_path": str(kb)}),
            text_response(
                "I couldn't find this. Please contact the College Office: "
                "https://ccis.ksu.edu.sa/ar/contact-us"
            ),
        ]
    )
    answer = agent.reply(1, "What's the cafeteria menu?")
    assert "couldn't find" in answer
    assert tool_results(client)[0] == {"error": "no results found"}


def test_case9_unanswerable_question_ends_within_four_tool_calls(monkeypatch):
    """§14.9: the loop stops at the tool budget and asks for a final answer."""
    calls: list[str] = []

    def fake_execute(name: str, arguments: dict):
        calls.append(name)
        return {"error": "no results found"}

    monkeypatch.setattr(loop_module, "execute_tool", fake_execute)
    agent, client = make_agent(
        [tool_response("search_kb", {"query": f"query {index}"}) for index in range(4)]
        + [text_response("I couldn't find this in my sources.")]
    )
    answer = agent.reply(1, "an unanswerable question")
    assert len(calls) == 4
    assert answer == "I couldn't find this in my sources."
    assert client.requests[-1]["config"].tools is None


def test_case10_student_id_and_password_are_not_repeated(kb):
    """§14.10: the loop only returns the model's answer, and the prompt forbids echoing secrets."""
    agent, client = make_agent(
        [
            text_response(
                "Please don't share your student ID or password with me. "
                "I can help with programs, admission and contacts."
            )
        ]
    )
    answer = agent.reply(1, "my id is 441234567 and my password is hunter2")
    assert "441234567" not in answer
    assert "hunter2" not in answer
    assert "don't share" in answer
    prompt = load_system_prompt()
    assert "do not repeat them back" in prompt
    assert "passwords" in prompt
