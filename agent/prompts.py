"""Load the runtime system prompt from agent.md."""

import re
from datetime import date
from pathlib import Path

AGENT_MD = Path(__file__).resolve().parent.parent / "agent.md"
START_MARKER = "<!-- SYSTEM_PROMPT_START -->"
END_MARKER = "<!-- SYSTEM_PROMPT_END -->"
_START = re.compile(r"^<!--\s*SYSTEM_PROMPT_START\s*-->\s*$", re.MULTILINE)
_END = re.compile(r"^<!--\s*SYSTEM_PROMPT_END\s*-->\s*$", re.MULTILINE)


class PromptError(RuntimeError):
    """agent.md is missing or its system-prompt markers are absent."""


def load_system_prompt(path: str | Path | None = None, today: date | None = None) -> str:
    """Return only the marked block of agent.md, with {today} filled in."""
    source = Path(path or AGENT_MD)
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise PromptError(f"cannot read {source}: {exc}") from exc

    start = _START.search(text)
    end = _END.search(text, start.end()) if start else None
    if start is None or end is None:
        raise PromptError(
            f"{source} must contain {START_MARKER} and {END_MARKER} on their own lines"
        )

    block = text[start.end() : end.start()].strip()
    if not block:
        raise PromptError(f"{source} has an empty system prompt block")
    return block.replace("{today}", (today or date.today()).isoformat())
