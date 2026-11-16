"""Parse the KSU academic calendar page into one row per date (spec.md section 7).

The page (dar.ksu.edu.sa/ar/current) holds one table per term. The first header cell
names the term, and every row below it is a single date:

    بداية الدراسة للفصل الدراسي الأول | الأحد | 23 / 08 / 2026 م | 10 / 03 / 1448 هـ

"بداية ..." and "نهاية ..." are separate rows, so no range is ever built here.
"""

import re
from dataclasses import dataclass, field
from datetime import date

from bs4 import BeautifulSoup

from scrapers.ccis import ParseError

ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
GREGORIAN_RE = re.compile(r"(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{4})")
HIJRI_RE = re.compile(r"(\d{1,2})\s*/\s*(\d{1,2})\s*/\s*(\d{3,4})")
YEAR_RE = re.compile(r"(\d{4})\s*[-–—/]\s*(\d{4})")
WHITESPACE = re.compile(r"\s+")
HEADER_DAY = "اليوم"

WEEKDAYS = {
    "الأحد": "Sunday",
    "الاحد": "Sunday",
    "الإثنين": "Monday",
    "الاثنين": "Monday",
    "الإثنين": "Monday",
    "الثلاثاء": "Tuesday",
    "الأربعاء": "Wednesday",
    "الاربعاء": "Wednesday",
    "الخميس": "Thursday",
    "الجمعة": "Friday",
    "السبت": "Saturday",
}


@dataclass
class CalendarEvent:
    """One dated row of the calendar."""

    academic_year: str
    term: str
    title: str
    weekday: str
    date_gregorian: str
    date_hijri: str | None
    weekday_matches: bool = True


@dataclass
class Calendar:
    """The parsed page: its academic year and every event on it."""

    academic_year: str
    events: list[CalendarEvent] = field(default_factory=list)


def _text(cell) -> str:
    """Read a cell as one line, joining split spans without inventing spaces."""
    return WHITESPACE.sub(" ", cell.get_text("", strip=True)).strip()


def digits(text: str) -> str:
    """Use ASCII digits, so Arabic-Indic ones parse too."""
    return (text or "").translate(ARABIC_DIGITS)


def _date(cell_text: str, pattern: re.Pattern) -> tuple[int, int, int] | None:
    """Read DD / MM / YYYY into (day, month, year)."""
    match = pattern.search(digits(cell_text))
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def iso_date(cell_text: str) -> str | None:
    """Read the Gregorian cell as an ISO date, or None when it is not a real date."""
    parts = _date(cell_text, GREGORIAN_RE)
    if parts is None:
        return None
    day, month, year = parts
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _iso_hijri(cell_text: str) -> str | None:
    """Read the Hijri cell as YYYY-MM-DD text, keeping it as the page wrote it."""
    parts = _date(cell_text, HIJRI_RE)
    if parts is None:
        return None
    day, month, year = parts
    if not 1 <= month <= 12 or not 1 <= day <= 30:
        return None
    return f"{year:04d}-{month:02d}-{day:02d}"


def weekday_matches(weekday: str, iso_date: str) -> bool:
    """True when the written weekday is the one the Gregorian date actually falls on."""
    english = WEEKDAYS.get(weekday.strip())
    if english is None:
        return False
    try:
        return date.fromisoformat(iso_date).strftime("%A") == english
    except ValueError:
        return False


def year_from_text(text: str) -> str | None:
    """Read "2026 /2027" out of a heading as "2026-2027"."""
    match = YEAR_RE.search(digits(text))
    if match is None:
        return None
    return f"{match.group(1)}-{match.group(2)}"


def _academic_year(soup: BeautifulSoup) -> str:
    """Read the academic year from the page heading, falling back to the title tag."""
    candidates = [node.get_text(" ", strip=True) for node in soup.find_all(["h1", "h2"])]
    if soup.title is not None:
        candidates.append(soup.title.get_text(" ", strip=True))
    for text in candidates:
        year = year_from_text(text)
        if year:
            return year
    raise ParseError("no academic year found on the calendar page")


def _header(cells: list) -> bool:
    """True for a header row: the weekday column label sits in the second cell."""
    return len(cells) >= 4 and _text(cells[1]) == HEADER_DAY


def _term(cells: list) -> str | None:
    """Read the term name from a header row's first cell."""
    term = _text(cells[0])
    return term or None


def parse_calendar(html: str) -> Calendar:
    """Parse the calendar page, raising ParseError when no dated row is found."""
    soup = BeautifulSoup(html, "lxml")
    academic_year = _academic_year(soup)
    events: list[CalendarEvent] = []
    seen: set[tuple[str, str, str]] = set()

    for table in soup.find_all("table"):
        term: str | None = None
        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if len(cells) < 4:
                continue
            if _header(cells):
                term = _term(cells)
                continue
            if term is None:
                continue
            title = _text(cells[0])
            weekday = _text(cells[1])
            date_iso = iso_date(_text(cells[2]))
            if not title or not weekday or date_iso is None:
                continue
            key = (term, title, date_iso)
            if key in seen:
                continue
            seen.add(key)
            events.append(
                CalendarEvent(
                    academic_year=academic_year,
                    term=term,
                    title=title,
                    weekday=weekday,
                    date_gregorian=date_iso,
                    date_hijri=_iso_hijri(_text(cells[3])),
                    weekday_matches=weekday_matches(weekday, date_iso),
                )
            )

    if not events:
        raise ParseError("no calendar rows found")
    events.sort(key=lambda event: (event.date_gregorian, event.term, event.title))
    return Calendar(academic_year=academic_year, events=events)
