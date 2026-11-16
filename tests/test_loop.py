"""Tests for the agent loop: tool calls, budget, repeat guard and errors."""

from types import SimpleNamespace

import pytest
from google.genai import errors, types

import agent.loop as loop_module
from agent.loop import FRIENDLY_ERROR, Agent


def text_response(text: str) -> SimpleNamespace:
    """A model response that answers without calling a tool."""
    content = types.Content(role="model", parts=[types.Part(text=text)])
    return SimpleNamespace(text=text, function_calls=[], candidates=[SimpleNamespace(content=content)])


def tool_response(query: str = "شروط القبول") -> SimpleNamespace:
    """A model response that asks for one search_kb call."""
    call = types.FunctionCall(name="search_kb", args={"query": query})
    content = types.Content(role="model", parts=[types.Part(function_call=call)])
    return SimpleNamespace(text=None, function_calls=[call], candidates=[SimpleNamespace(content=content)])


class FakeClient:
    """Replays scripted responses and records the requests it received."""

    def __init__(self, responses: list[SimpleNamespace]) -> None:
        self.models = self
        self.responses = list(responses)
        self.requests: list[dict] = []

    def generate_content(self, **kwargs):
        self.requests.append(kwargs)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def make_agent(
    responses, max_tool_calls: int = 4, fallback_model: str = ""
) -> tuple[Agent, FakeClient]:
    """Build an Agent wired to a fake client."""
    client = FakeClient(responses)
    agent = Agent(
        client=client,
        model="test-model",
        system_prompt="test prompt",
        max_tool_calls=max_tool_calls,
        history_messages=10,
        fallback_model=fallback_model,
    )
    return agent, client


@pytest.fixture()
def recorded_tools(monkeypatch):
    """Replace tool execution with a recorder."""
    calls: list[tuple[str, dict]] = []

    def fake_execute(name: str, arguments: dict):
        calls.append((name, arguments))
        return [{"text": "شروط القبول", "source_url": "https://ccis.ksu.edu.sa/ar/masters"}]

    monkeypatch.setattr(loop_module, "execute_tool", fake_execute)
    return calls


def test_answers_without_tools():
    agent, client = make_agent([text_response("مرحباً")])
    assert agent.reply(1, "hi") == "مرحباً"
    assert len(client.requests) == 1


def test_runs_a_tool_then_answers(recorded_tools):
    agent, client = make_agent([tool_response("القبول"), text_response("الجواب")])
    assert agent.reply(1, "ما شروط القبول؟") == "الجواب"
    assert recorded_tools == [("search_kb", {"query": "القبول"})]
    assert len(client.requests) == 2
    sent_back = client.requests[1]["contents"][-1]
    assert sent_back.parts[0].function_response.response["result"][0]["source_url"]


def test_stops_after_the_tool_budget(recorded_tools):
    agent, client = make_agent(
        [tool_response(f"سؤال {index}") for index in range(4)] + [text_response("توقف")]
    )
    assert agent.reply(1, "سؤال") == "توقف"
    assert len(recorded_tools) == 4
    assert len(client.requests) == 5
    assert client.requests[-1]["config"].tools is None


def test_ignores_a_repeated_call(recorded_tools):
    agent, client = make_agent([tool_response(), tool_response(), text_response("جواب")])
    assert agent.reply(1, "سؤال") == "جواب"
    assert len(recorded_tools) == 1
    assert len(client.requests) == 3


def test_retries_once_on_rate_limit(monkeypatch):
    monkeypatch.setattr(loop_module.time, "sleep", lambda _: None)
    rate_limited = errors.APIError(
        code=429, response_json={"error": {"code": 429, "message": "rate limit"}}
    )
    agent, client = make_agent([rate_limited, text_response("تم")])
    assert agent.reply(1, "hi") == "تم"
    assert len(client.requests) == 2


def test_friendly_message_when_the_model_keeps_failing(monkeypatch):
    monkeypatch.setattr(loop_module.time, "sleep", lambda _: None)
    rate_limited = errors.APIError(
        code=429, response_json={"error": {"code": 429, "message": "rate limit"}}
    )
    agent, _ = make_agent([rate_limited, rate_limited])
    assert agent.reply(1, "hi") == FRIENDLY_ERROR


def test_reset_clears_the_history(recorded_tools):
    agent, client = make_agent([text_response("أولاً"), text_response("ثانياً")])
    agent.reply(7, "hi")
    agent.reset(7)
    agent.reply(7, "hi")
    assert len(client.requests[1]["contents"]) == 1


def test_uses_the_fallback_model_after_the_main_one_fails(monkeypatch):
    monkeypatch.setattr(loop_module.time, "sleep", lambda _: None)
    rate_limited = errors.APIError(
        code=429, response_json={"error": {"code": 429, "message": "rate limit"}}
    )
    agent, client = make_agent(
        [rate_limited, rate_limited, text_response("تم")], fallback_model="backup-model"
    )
    assert agent.reply(1, "hi") == "تم"
    assert [request["model"] for request in client.requests] == [
        "test-model",
        "test-model",
        "backup-model",
    ]


def test_friendly_message_when_the_fallback_also_fails(monkeypatch):
    monkeypatch.setattr(loop_module.time, "sleep", lambda _: None)
    rate_limited = errors.APIError(
        code=429, response_json={"error": {"code": 429, "message": "rate limit"}}
    )
    agent, client = make_agent([rate_limited] * 3, fallback_model="backup-model")
    assert agent.reply(1, "hi") == FRIENDLY_ERROR
    assert len(client.requests) == 3


def test_no_fallback_call_when_both_models_are_the_same(monkeypatch):
    monkeypatch.setattr(loop_module.time, "sleep", lambda _: None)
    rate_limited = errors.APIError(
        code=429, response_json={"error": {"code": 429, "message": "rate limit"}}
    )
    agent, client = make_agent([rate_limited, rate_limited], fallback_model="test-model")
    assert agent.reply(1, "hi") == FRIENDLY_ERROR
    assert len(client.requests) == 2


def test_english_message_asks_for_an_english_reply():
    agent, client = make_agent([text_response("The college offers several programs.")])
    agent.reply(1, "What programs does the college offer?")
    instruction = client.requests[0]["config"].system_instruction
    assert "written in English" in instruction
    assert "test prompt" in instruction


def test_arabic_message_asks_for_an_arabic_reply():
    agent, client = make_agent([text_response("تقدم الكلية عدة برامج.")])
    agent.reply(1, "ما هي برامج الكلية؟")
    instruction = client.requests[0]["config"].system_instruction
    assert "written in Arabic" in instruction


def test_the_language_rule_reaches_the_closing_answer(recorded_tools):
    agent, client = make_agent(
        [tool_response(f"سؤال {index}") for index in range(4)] + [text_response("توقف")]
    )
    agent.reply(1, "What are the transfer conditions?")
    instruction = client.requests[-1]["config"].system_instruction
    assert "written in English" in instruction
