"""Settings read from .env, with defaults for everything except the secrets."""

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def _int(name: str, default: int) -> int:
    """Read an int setting, falling back to the default when unset or invalid."""
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


def _models(name: str, default: tuple[str, ...] = ()) -> list[str]:
    """Read a comma-separated model list, dropping blanks and repeats."""
    raw = os.getenv(name, "")
    values = raw.split(",") if raw.strip() else list(default)
    chain: list[str] = []
    for value in values:
        model = value.strip()
        if model and model not in chain:
            chain.append(model)
    return chain


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
MODEL = os.getenv("MODEL", "")
DEFAULT_MODEL_FALLBACKS = (
    "gemini-3.5-flash-lite",
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "Gemini 4 Hydrogen",
)


def _model_chain() -> list[str]:
    """The ordered fallbacks: MODEL_FALLBACKS, else the older MODEL_FALLBACK, else the default."""
    return _models("MODEL_FALLBACKS") or _models("MODEL_FALLBACK") or list(DEFAULT_MODEL_FALLBACKS)


MODEL_FALLBACKS = _model_chain()


MAX_TOOL_CALLS = _int("MAX_TOOL_CALLS", 4)
HISTORY_MESSAGES = _int("HISTORY_MESSAGES", 10)
STALE_AFTER_DAYS = _int("STALE_AFTER_DAYS", 30)

DB_PATH = Path(os.getenv("DB_PATH") or ROOT / "db" / "ccis.sqlite")
