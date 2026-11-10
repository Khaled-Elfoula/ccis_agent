"""Parse CCIS Drupal pages into a title, a language and clean text chunks."""

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

CONTENT_SELECTORS = ("[role=main]", "main", "article", "body")
NOISE_SELECTORS = (
    "script",
    "style",
    "noscript",
    "nav",
    "header",
    "footer",
    "aside",
    "form",
    "iframe",
    "svg",
    "button",
    ".breadcrumb",
    ".more-link",
    ".social-media-sharing",
    ".block-social-media",
    ".newsstyle",
    ".announcements",
    ".announcments",
    ".milestone-block",
    ".owl-carousel",
    ".hidden",
    "[id^=block-lastupdateddateblock]",
)
NOISE_LINE = re.compile(r"^(>>|\.|[-–—])$|^\(\s*(?:PDF|pdf|word|Word|docx)\s*\)$")
NOISE_PREFIX = re.compile(r"^(تاريخ آخر تحديث|شارك هذه الصفحة|اضغط هنا للمزيد)")
LIST_MARKER = re.compile(r"^\d+\s*[-.)]$")
HOME_LABELS = {"home", "home page", "الصفحة الرئيسية", "الرئيسية"}
CHUNK_CHARS = 900
CHUNK_OVERLAP = 150
MIN_TEXT_CHARS = 100


class ParseError(RuntimeError):
    """The page did not contain the expected content."""


@dataclass
class ParsedPage:
    """A page ready for storage."""

    title: str
    lang: str
    text: str
    chunks: list[str]


def parse(html: str) -> ParsedPage:
    """Turn raw HTML into a parsed page, raising ParseError when the layout changed."""
    soup = BeautifulSoup(html, "lxml")
    node = None
    for selector in CONTENT_SELECTORS:
        candidate = soup.select_one(selector)
        if candidate is not None and len(candidate.get_text(strip=True)) >= MIN_TEXT_CHARS:
            node = candidate
            break
    if node is None:
        raise ParseError("no content container matched: " + ", ".join(CONTENT_SELECTORS))

    title = _title(soup, node)
    for selector in NOISE_SELECTORS:
        for element in node.select(selector):
            element.decompose()

    text = "\n".join(_tidy(node.get_text("\n", strip=True).splitlines()))
    if len(text) < MIN_TEXT_CHARS:
        raise ParseError("main text too short after cleaning")
    return ParsedPage(title=title, lang=_lang(soup), text=text, chunks=chunk_text(text))


def _title(soup: BeautifulSoup, node) -> str:
    """Use the page heading, falling back to the <title> tag."""
    for holder in (node, soup):
        heading = holder.find("h1")
        if heading is not None and heading.get_text(strip=True):
            return heading.get_text(" ", strip=True)
    if soup.title is not None and soup.title.get_text(strip=True):
        for part in (piece.strip() for piece in soup.title.get_text(strip=True).split("|")):
            if part and part.lower() not in HOME_LABELS:
                return part
    return ""


def _lang(soup: BeautifulSoup) -> str:
    """Read the document language, defaulting to Arabic."""
    value = (soup.html.get("lang") if soup.html else None) or ""
    return "en" if value.lower().startswith("en") else "ar"


def _tidy(raw_lines: list[str]) -> list[str]:
    """Drop boilerplate, rejoin broken sentences and keep list markers with their item."""
    lines: list[str] = []
    prefix = ""
    for raw in raw_lines:
        line = re.sub(r"\s+", " ", raw).strip()
        if not line or NOISE_LINE.match(line) or NOISE_PREFIX.match(line):
            continue
        if LIST_MARKER.match(line):
            prefix = f"{line} "
            continue
        if prefix:
            line = prefix + line
            prefix = ""
        if line in "()" or (lines and lines[-1].endswith("(")):
            if lines:
                lines[-1] += line
            continue
        if lines and line[0] in "\"'.,،؛":
            lines[-1] = f"{lines[-1]} {line}"
            continue
        lines.append(line)
    if prefix:
        lines.append(prefix.strip())
    return lines


def _split_long(line: str, size: int) -> list[str]:
    """Break a single over-long line into word-sized pieces."""
    if len(line) <= size:
        return [line]
    pieces: list[str] = []
    current = ""
    for word in line.split():
        if current and len(current) + len(word) + 1 > size:
            pieces.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        pieces.append(current)
    return pieces


def chunk_text(text: str, size: int = CHUNK_CHARS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Split text into overlapping chunks on line boundaries."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    lines = [piece for line in lines for piece in _split_long(line, size)]
    chunks: list[str] = []
    start = 0
    while start < len(lines):
        end = start
        length = 0
        while end < len(lines) and (end == start or length + len(lines[end]) + 1 <= size):
            length += len(lines[end]) + 1
            end += 1
        chunks.append("\n".join(lines[start:end]))
        if end >= len(lines):
            break
        tail = end
        tail_length = 0
        while tail > start and tail_length + len(lines[tail - 1]) + 1 <= overlap:
            tail -= 1
            tail_length += len(lines[tail]) + 1
        start = max(tail, start + 1)
    return chunks
