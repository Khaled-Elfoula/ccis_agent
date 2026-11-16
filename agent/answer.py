"""Clean the model's final answer before it is sent to the user."""

import re
from urllib.parse import urlsplit

URL_RE = re.compile(r"https?://[^\s<>\"']+")
MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\(\s*(https?://[^)\s]+)\s*\)")
HEADING = re.compile(r"(?m)^#{1,6}\s*")
BULLET = re.compile(r"(?m)^\s*[*+]\s+")
BOLD = re.compile(r"\*\*|__")
ITALIC = re.compile(r"(?<!\w)\*(?=\S)(.+?)(?<=\S)\*(?!\w)")
CODE = re.compile(r"`+")
SOURCE_LABEL = re.compile(r"(?im)\s*(?:source|المصدر)\s*:\s*$")
SOURCE_ONLY = re.compile(r"(?im)^\s*(?:source|المصدر)\s*:\s*$")
CONJUNCTION_END = re.compile(r"(?im)\s+(?:أو|or)\s*$")
SPACE_BEFORE_PUNCT = re.compile(r" +([.,;:!?،؛])")
EXTRA_BLANKS = re.compile(r"\n{3,}")
TRAILING_URL_PUNCT = ".,;:!?)]}>'\""


def _url_key(url: str) -> str:
    """Normalize a URL to host plus path, ignoring scheme, query and trailing slash."""
    parsed = urlsplit(str(url).strip().strip(TRAILING_URL_PUNCT))
    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    path = parsed.path.rstrip("/")
    return f"{host}{path}" if host else ""


def collect_urls(value: object) -> set[str]:
    """Collect every URL found in a tool result or in the user's message."""
    found: set[str] = set()
    if isinstance(value, str):
        found.update(match.group(0) for match in URL_RE.finditer(value))
    elif isinstance(value, dict):
        for item in value.values():
            found.update(collect_urls(item))
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            found.update(collect_urls(item))
    return found


def _allowed(url: str, allowed: set[str]) -> bool:
    """Accept a URL only when a tool result or the user supplied it."""
    key = _url_key(url)
    if not key:
        return False
    if key in allowed:
        return True
    return any(
        key.startswith(candidate + "/") or candidate.startswith(key + "/")
        for candidate in allowed
    )


def _links(text: str, allowed: set[str]) -> str:
    """Turn markdown links into plain text, keeping only a known URL."""

    def replace(match: re.Match) -> str:
        label, url = match.group(1), match.group(2)
        return f"{label} ({url})" if _allowed(url, allowed) else label

    return MARKDOWN_LINK.sub(replace, text)


def _strip_urls(text: str, allowed: set[str]) -> str:
    """Remove bare URLs that did not come from a tool result or the user."""

    def replace(match: re.Match) -> str:
        url = match.group(0)
        return url if _allowed(url, allowed) else ""

    return URL_RE.sub(replace, text)


def clean_answer(text: str, allowed_urls: set[str] | None = None) -> str:
    """Remove markdown markers and any URL the model invented."""
    if not text:
        return text
    allowed = {_url_key(url) for url in (allowed_urls or set()) if _url_key(url)}
    cleaned = _links(text, allowed)
    cleaned = _strip_urls(cleaned, allowed)
    cleaned = HEADING.sub("", cleaned)
    cleaned = BULLET.sub("- ", cleaned)
    cleaned = BOLD.sub("", cleaned)
    cleaned = ITALIC.sub(r"\1", cleaned)
    cleaned = CODE.sub("", cleaned)
    cleaned = SOURCE_ONLY.sub("", cleaned)
    cleaned = SOURCE_LABEL.sub("", cleaned)
    cleaned = CONJUNCTION_END.sub("", cleaned)
    cleaned = SPACE_BEFORE_PUNCT.sub(r"\1", cleaned)
    cleaned = re.sub(r"[ \t]{2,}", " ", cleaned)
    cleaned = re.sub(r" *\n *", "\n", cleaned)
    cleaned = EXTRA_BLANKS.sub("\n\n", cleaned)
    return cleaned.strip()
