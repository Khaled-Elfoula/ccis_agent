"""Parse the KSU academic-movements table into one row per movement (spec.md section 7).

The page (dar.ksu.edu.sa/ar/academictable) holds one table per term, each inside a
<details> element whose <summary> names the term, for example
"الفصل الدراسي الأول للعام الدراسي 2026 /2027 م". Every row is
الحركة الأكاديمية | البداية | النهاية, and each date cell holds the weekday and the
date separated by a line break, for example "الأحد<br>26 /07 /2026 م".

Three rows are not ordinary movements:

- a single-cell bold row such as "بداية الفصل الدراسي الأول - الأحد 23 /08 /2026م"
  marks the start of a term and carries one date;
- "تقديم طلب اختبار نهائي بديل" has text where the dates go ("تاريخ عقد الاختبار",
  "اسبوع من تاريخ عقد الاختبار"), so no date is stored and the text becomes the note;
- a row marked with * (أسبوع التعزيز الأكاديمي) takes the footnote under its table
  as its note.

Every weekday is checked against its Gregorian date and a mismatch is flagged, never
corrected.
"""

import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from scrapers.calendar import WEEKDAYS, iso_date, weekday_matches, year_from_text
from scrapers.ccis import ParseError

WHITESPACE = re.compile(r"\s+")
UPDATED = re.compile(r"تاريخ آخر تحديث\s*[:：]\s*(.+?)(?:شارك هذه الصفحة|$)")
HEADER_LABEL = "الحركة الأكاديمية"
SEMESTER_MARK = "بداية الفصل"
HOLIDAY_PREFIX = "إجازة"
EXAM_MARK = "اختبار"
TERM_MARK = "الفصل"
FOOTNOTE_MARK = "*"
MAX_NOTE = 1200
KINDS = ("action", "holiday", "semester_start", "exam")


@dataclass
class Deadline:
    """One movement from the academic table."""

    academic_year: str
    term: str | None
    title: str
    kind: str
    start_date: str | None
    end_date: str | None
    start_weekday: str | None
    end_weekday: str | None
    note: str | None
    page_updated: str | None
    weekday_matches: bool = True


@dataclass
class Deadlines:
    """The parsed page: its academic year, its last-update line and every row."""

    academic_year: str
    page_updated: str | None
    rows: list[Deadline] = field(default_factory=list)


def _clean(text: str) -> str:
    """Collapse every run of whitespace, including the page's non-breaking spaces."""
    return WHITESPACE.sub(" ", (text or "").replace("\xa0", " ")).strip()


def weekday_in(text: str) -> str | None:
    """Return the weekday the cell names, as the page writes it, or None."""
    for token in _clean(text).split():
        name = token.strip("،.:-–—")
        if name in WEEKDAYS:
            return name
    return None


def _title_bold(cell) -> bool:
    """True when the cell writes its text in bold."""
    return cell.find(["b", "strong"]) is not None


def _academic_year(soup: BeautifulSoup) -> str:
    """Read the academic year from the page heading, falling back to any heading."""
    candidates = [node.get_text(" ", strip=True) for node in soup.find_all(["h1", "h2"])]
    if soup.title is not None:
        candidates.append(soup.title.get_text(" ", strip=True))
    for text in candidates:
        year = year_from_text(text)
        if year:
            return year
    raise ParseError("no academic year found on the academic table page")


def _page_updated(soup: BeautifulSoup) -> str | None:
    """Read the page's "آخر تحديث" line, or None when it is missing."""
    block = soup.select_one(".block-updated")
    if block is not None:
        match = UPDATED.search(_clean(block.get_text(" ", strip=True)))
        if match:
            return _clean(match.group(1))
    match = UPDATED.search(_clean(soup.get_text(" ", strip=True)))
    return _clean(match.group(1)) if match else None


def _term_for(table) -> str | None:
    """Read the term heading that sits above a table."""
    details = table.find_parent("details")
    if details is not None:
        summary = details.find("summary")
        if summary is not None:
            text = _clean(summary.get_text(" ", strip=True))
            if TERM_MARK in text:
                return text
    node = table.find_previous(["summary", "h2", "h3", "h4"])
    if node is not None:
        text = _clean(node.get_text(" ", strip=True))
        if TERM_MARK in text:
            return text
    return None


def _footnote(table) -> str | None:
    """Read the * footnote that follows a table, or None when the table has none."""
    scope = table.find_parent("details") or table.parent
    if scope is None:
        return None
    parts: list[str] = []
    started = False
    for node in scope.find_all("p"):
        text = _clean(node.get_text(" ", strip=True))
        if not text:
            continue
        if not started:
            if text.startswith(FOOTNOTE_MARK):
                started = True
                parts.append(text)
            continue
        parts.append(text)
    return _clean(" ".join(parts))[:MAX_NOTE] or None


def _kind(title: str, bold: bool, has_dates: bool) -> str:
    """Classify a row as an action, a holiday, a term start or an exam movement."""
    if not has_dates:
        return "exam" if EXAM_MARK in title else "action"
    if bold or title.startswith(HOLIDAY_PREFIX):
        return "holiday"
    if EXAM_MARK in title:
        return "exam"
    return "action"


def _all_weekdays_match(pairs: list[tuple[str | None, str | None]]) -> bool:
    """True when every weekday the page wrote is the weekday of its date."""
    checked = [(weekday, date_iso) for weekday, date_iso in pairs if weekday and date_iso]
    return all(weekday_matches(weekday, date_iso) for weekday, date_iso in checked)


def _row(
    cells: list,
    term: str | None,
    footnote: str | None,
    academic_year: str,
    page_updated: str | None,
) -> Deadline | None:
    """Build one deadline from a table row, or None when the row is not a movement."""
    texts = [_clean(cell.get_text(" ", strip=True)) for cell in cells]
    title = texts[0]
    if not title or title == HEADER_LABEL:
        return None
    bold = _title_bold(cells[0])

    if len(cells) == 1:
        start_date = iso_date(title)
        if start_date is None:
            return None
        name = re.split(r"\s+[-–—]\s+", title, maxsplit=1)[0].strip() or title
        start_weekday = weekday_in(title)
        # Only a row the page marks with * takes the footnote as its note.
        marked = FOOTNOTE_MARK in title
        note = footnote if marked else None
        clean_title = name.replace(FOOTNOTE_MARK, "").strip() or name
        return Deadline(
            academic_year=academic_year,
            term=term,
            title=clean_title,
            kind="semester_start",
            start_date=start_date,
            end_date=None,
            start_weekday=start_weekday,
            end_weekday=None,
            note=note,
            page_updated=page_updated,
            weekday_matches=_all_weekdays_match([(start_weekday, start_date)]),
        )

    if len(cells) < 3:
        return None

    start_text, end_text = texts[1], texts[2]
    start_date, end_date = iso_date(start_text), iso_date(end_text)
    start_weekday, end_weekday = weekday_in(start_text), weekday_in(end_text)
    # Only a row the page marks with * takes the footnote as its note.
    marked = FOOTNOTE_MARK in title
    note = footnote if marked else None
    if start_date is None and end_date is None:
        # "تاريخ عقد الاختبار" and "اسبوع من تاريخ عقد الاختبار" are not dates.
        note = " — ".join(part for part in (start_text, end_text) if part) or note
    clean_title = title.replace(FOOTNOTE_MARK, "").strip() or title
    return Deadline(
        academic_year=academic_year,
        term=term,
        title=clean_title,
        kind=_kind(clean_title, bold, start_date is not None or end_date is not None),
        start_date=start_date,
        end_date=end_date,
        start_weekday=start_weekday,
        end_weekday=end_weekday,
        note=note,
        page_updated=page_updated,
        weekday_matches=_all_weekdays_match(
            [(start_weekday, start_date), (end_weekday, end_date)]
        ),
    )


def parse_deadlines(html: str) -> Deadlines:
    """Parse the academic table page, raising ParseError when no movement is found."""
    soup = BeautifulSoup(html, "lxml")
    academic_year = _academic_year(soup)
    page_updated = _page_updated(soup)
    rows: list[Deadline] = []
    seen: set[tuple[str | None, str, str | None, str | None]] = set()

    for table in soup.find_all("table"):
        term = _term_for(table)
        footnote = _footnote(table)
        for row in table.find_all("tr"):
            cells = row.find_all(["td", "th"])
            if not cells:
                continue
            deadline = _row(cells, term, footnote, academic_year, page_updated)
            if deadline is None:
                continue
            key = (deadline.term, deadline.title, deadline.start_date, deadline.end_date)
            if key in seen:
                continue
            seen.add(key)
            rows.append(deadline)

    if not rows:
        raise ParseError("no academic deadline rows found")
    rows.sort(key=lambda item: (item.start_date or "9999-99-99", item.term or "", item.title))
    return Deadlines(academic_year=academic_year, page_updated=page_updated, rows=rows)
