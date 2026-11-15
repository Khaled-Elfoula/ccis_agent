"""The agent loop: one user message in, one answer out, tool calls in between."""

import json
import logging
import time

from google import genai
from google.genai import errors as genai_errors
from google.genai import types

import config
from agent.arabic import detect_language
from agent.prompts import load_system_prompt
from agent.tools import TOOLS, execute_tool


logger = logging.getLogger(__name__)

FRIENDLY_ERROR = (
    "Sorry, something went wrong on my side just now. Please try again in a moment.\n"
    "عذراً، حدث خطأ مؤقت. حاول مرة أخرى بعد قليل."
)
RETRY_DELAY = 2.0
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
FINAL_NUDGE = (
    "You have reached the tool-call limit for this message. Answer now with what you "
    "already have, or hand off if you still cannot answer."
)
LANGUAGE_NAMES = {"ar": "Arabic", "en": "English"}
LANGUAGE_RULE = (
    "The user's latest message is written in {name}. Write your whole reply in {name} — "
    "the greeting, the body, any list and the closing. Do not switch to another language "
    "because the tool results or the sources are in one."
)


class Agent:
    """Runs one turn per chat and keeps short in-memory history."""

    def __init__(
        self,
        client: genai.Client | None = None,
        model: str | None = None,
        system_prompt: str | None = None,
        max_tool_calls: int | None = None,
        history_messages: int | None = None,
        fallback_model: str | None = None,
    ) -> None:
        self.client = client or genai.Client(api_key=config.GEMINI_API_KEY)
        self.model = model or config.MODEL
        self.fallback_model = config.MODEL_FALLBACK if fallback_model is None else fallback_model
        self.system_prompt = system_prompt or load_system_prompt()
        self.max_tool_calls = config.MAX_TOOL_CALLS if max_tool_calls is None else max_tool_calls
        self.history_messages = (
            config.HISTORY_MESSAGES if history_messages is None else history_messages
        )
        self._history: dict[int, list[types.Content]] = {}
        self.last_tools: dict[int, list[str]] = {}
    def reset(self, chat_id: int) -> None:
        """Forget the conversation with one chat."""
        self._history.pop(chat_id, None)
        self.last_tools.pop(chat_id, None)

    def reply(self, chat_id: int, text: str) -> str:
        """Answer one user message, running tool calls until the model stops."""
        history = self._history.setdefault(chat_id, [])
        turn = [*history, types.Content(role="user", parts=[types.Part(text=text)])]
        tools: list[str] = []
        answer = self._run(turn, detect_language(text), tools)
        self.last_tools[chat_id] = tools
        history.extend(
            [
                types.Content(role="user", parts=[types.Part(text=text)]),
                types.Content(role="model", parts=[types.Part(text=answer)]),
            ]
        )
        del history[: max(0, len(history) - self.history_messages)]
        return answer

    def _run(
        self, contents: list[types.Content], language: str = "en", tools: list[str] | None = None
    ) -> str:
        """Drive the tool-call loop for one turn, within the call budget."""
        seen: set[str] = set()
        calls = 0
        while True:
            response = self._generate(contents, with_tools=True, language=language)
            if response is None:
                return FRIENDLY_ERROR
            function_calls = response.function_calls or []
            if not function_calls:
                return _text(response) or FRIENDLY_ERROR

            model_content = response.candidates[0].content
            if model_content is not None:
                contents.append(model_content)

            executed = 0
            for call in function_calls:
                arguments = dict(call.args or {})
                key = f"{call.name}:{json.dumps(arguments, sort_keys=True, default=str)}"
                if key in seen or calls >= self.max_tool_calls:
                    result: object = {
                        "error": "repeated call ignored"
                        if key in seen
                        else "tool-call limit reached"
                    }
                else:
                    seen.add(key)
                    calls += 1
                    executed += 1
                    if tools is not None:
                        tools.append(call.name)
                    result = execute_tool(call.name, arguments)
                contents.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part.from_function_response(
                                name=call.name, response={"result": result}
                            )
                        ],
                    )
                )

            if calls >= self.max_tool_calls or executed == 0:
                return self._final_answer(contents, language)

    def _final_answer(self, contents: list[types.Content], language: str = "en") -> str:
        """Ask for a closing answer without tools once the budget is spent."""
        contents.append(types.Content(role="user", parts=[types.Part(text=FINAL_NUDGE)]))
        response = self._generate(contents, with_tools=False, language=language)
        return (_text(response) if response is not None else "") or FRIENDLY_ERROR

    def _generate(self, contents: list[types.Content], with_tools: bool, language: str = "en"):
        """Call the model, retrying once, then try the fallback model once."""
        generation_config = types.GenerateContentConfig(
            system_instruction=_system_instruction(self.system_prompt, language),
            temperature=0.3,
            tools=TOOLS if with_tools else None,
        )
        response = self._call(self.model, contents, generation_config, attempts=2)
        if response is None and self.fallback_model and self.fallback_model != self.model:
            logger.warning(
                "main model %s failed, trying fallback %s", self.model, self.fallback_model
            )
            response = self._call(self.fallback_model, contents, generation_config, attempts=1)
        return response

    def _call(self, model: str, contents: list[types.Content], generation_config, attempts: int):
        """Call one model, retrying while the error is transient."""
        request = {"model": model, "contents": contents, "config": generation_config}
        for attempt in range(1, attempts + 1):
            try:
                return self.client.models.generate_content(**request)
            except genai_errors.APIError as exc:
                if attempt < attempts and _is_retryable(exc):
                    logger.warning("model call failed (%s), retrying once", exc)
                    time.sleep(RETRY_DELAY)
                    continue
                logger.error("model call failed: %s", exc)
                return None
            except Exception as exc:  # network errors and SDK quirks
                logger.error("model call failed: %s", exc)
                return None
        return None


def _text(response) -> str:
    """Read the text of a model response, tolerating responses without text."""
    try:
        return (response.text or "").strip()
    except (ValueError, AttributeError):
        return ""


def _is_retryable(exc: genai_errors.APIError) -> bool:
    """True for rate limits and transient server errors."""
    code = getattr(exc, "code", None)
    return code in RETRYABLE_STATUS or "429" in str(exc)

def _system_instruction(base: str, language: str) -> str:
    """Add the per-message language rule to the system prompt."""
    name = LANGUAGE_NAMES.get(language, LANGUAGE_NAMES["en"])
    return f"{base}\n\n{LANGUAGE_RULE.format(name=name)}"
