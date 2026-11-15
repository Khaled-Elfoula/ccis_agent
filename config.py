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


TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
MODEL = os.getenv("MODEL", "")
MODEL_FALLBACK = os.getenv("MODEL_FALLBACK", "")

MAX_TOOL_CALLS = _int("MAX_TOOL_CALLS", 4)
HISTORY_MESSAGES = _int("HISTORY_MESSAGES", 10)
STALE_AFTER_DAYS = _int("STALE_AFTER_DAYS", 30)

DB_PATH = Path(os.getenv("DB_PATH") or ROOT / "db" / "ccis.sqlite")
DB_PATH = Path(os.getenv("DB_PATH") or ROOT / "db" / "ccis.sqlite")
