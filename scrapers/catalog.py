"""Parse the SE course catalog into one row per course (spec.md section 7).

The page (ccis.ksu.edu.sa/ar/se/se-course-catalog) holds one two-column table per
course. In every row one cell is a bold label and the other is its value:

    رمز المقرر | هاب٣١٢        عدد الساعات | 3(3+0+1)
    اسم المقرر | هندسة متطلبات البرمجيات
    متطلب سابق | عال١١٣ + هاب٢١١        متطلب مصاحب | نال٢٣٠
    وصف المقرر | ...

The label is found by its text, never by the column order: the page sometimes puts the
value first and sometimes leaves a label out. Codes and hours use Arabic-Indic digits, and the
graduate rows use the Persian letter forms ھ ی ک, so everything is folded before parsing.

Two shapes are not a plain number: "95 ساعة" is a credit-hour requirement rather than a course
code, and "وحدة دراسية واحدة" is a credit-hour count written in words. The first is stored as
prerequisite_min_credit_hours, the second is left unparsed and reported, never guessed.
"""

import json
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

from agent.arabic import normalize_arabic
from scrapers.calendar import digits
from scrapers.ccis import ParseError

WHITESPACE = re.compile(r"\s+")
PERSIAN = str.maketrans({"ھ": "ه", "ی": "ي", "ک": "ك"})
CODE_RE = re.compile(r"([\u0621-\u064A]{2,})\s*(\d{2,4})")
HOURS_RE = re.compile(r"(\d+)\s*\(\s*(\d+)\s*\+\s*(\d+)(?:\s*\+\s*(\d+))?\s*\)")
CREDIT_ONLY_RE = re.compile(r"\(\s*(\d+)\s*\)")
CREDIT_HOURS_RE = re.compile(r"(\d+)\s*ساع")
BACHELOR_DIGITS = (2, 3, 4)
GRADUATE_DIGITS = (5, 6, 7)

LABELS = {
    "رمز المقرر": "code",
    "اسم المقرر": "name",
    "عدد الساعات": "hours",
    "متطلب سابق": "prerequisites",
    "متطلب مصاحب": "corequisites",
    "وصف المقرر": "description",
}


@dataclass
class Course:
    """One course from the catalog page."""

    code_display: str
    code_norm: str
    number: int | None
    level: str | None
    name_ar: str | None
    hours_text: str | None
    credit_hours: int | None
    lecture_hours: int | None
    lab_hours: int | None
    tutorial_hours: int | None
    prerequisites_text: str | None
    prerequisite_codes: list[str]
    prerequisite_min_credit_hours: int | None
    corequisites_text: str | None
    corequisite_codes: list[str]
    description_ar: str | None
    name_en: str | None = None
    description_en: str | None = None


@dataclass
class Catalog:
    """The parsed page and everything that looked wrong, left unfixed."""

    courses: list[Course] = field(default_factory=list)
    anomalies: list[str] = field(default_factory=list)


def _clean(text: str) -> str:
    """Collapse every run of whitespace, including the page's non-breaking spaces."""
    return WHITESPACE.sub(" ", (text or "").replace("\xa0", " ")).strip()


def normalize_code(text: str) -> str:
    """Use Arabic letters and ASCII digits with no space, e.g. "هاب312"."""
    return re.sub(r"\s+", "", digits(_clean(text)).translate(PERSIAN))


def _label(text: str) -> str | None:
    """Return the field a label cell names, or None when it is a value cell."""
    return LABELS.get(normalize_arabic(_clean(text)))


def _level_anomaly(code_norm: str, number: int | None, level: str | None) -> str | None:
    """Report a code whose first digit names no level, instead of guessing one."""
    if level is not None:
        return None
    return f"{code_norm}: the level could not be derived from {number}"


def parse_hours(text: str | None) -> tuple[int | None, int | None, int | None, int | None]:
    """Read "3(3+0+1)" as credit, lecture, lab and tutorial hours."""
    clean = digits(_clean(text or ""))
    match = HOURS_RE.search(clean)
    if match:
        credit, lecture, lab, tutorial = match.groups()
        return (
            int(credit),
            int(lecture),
            int(lab),
            int(tutorial) if tutorial is not None else None,
        )
    match = CREDIT_ONLY_RE.fullmatch(clean)
    if match:
        return int(match.group(1)), None, None, None
    return None, None, None, None


def parse_codes(text: str | None) -> list[str]:
    """Read the course codes out of a prerequisite or co-requisite cell."""
    clean = digits(_clean(text or ""))
    return [normalize_code(match.group(0)) for match in CODE_RE.finditer(clean)]


def min_credit_hours(text: str | None) -> int | None:
    """Read "95 ساعة" as 95, or None when the cell holds no such requirement."""
    match = CREDIT_HOURS_RE.search(digits(_clean(text or "")))
    return int(match.group(1)) if match else None


def level_for(number: int | None) -> str | None:
    """Derive the level from the first digit: 2-4 bachelor, 5-7 graduate, else None."""
    if number is None:
        return None
    first = number // 100
    if first in BACHELOR_DIGITS:
        return "bachelor"
    if first in GRADUATE_DIGITS:
        return "graduate"
    return None


def _fields(table, index: int, anomalies: list[str]) -> dict[str, str]:
    """Read one course table as {field: value}, reporting rows it cannot place."""
    values: dict[str, str] = {}
    for row in table.find_all("tr"):
        cells = row.find_all(["td", "th"])
        if len(cells) != 2:
            anomalies.append(f"table {index}: a row with {len(cells)} cells was skipped")
            continue
        first, second = (_clean(cell.get_text(" ", strip=True)) for cell in cells)
        field_name = _label(first) or _label(second)
        if field_name is not None:
            values[field_name] = second if _label(first) else first
            continue
        if (first == "") != (second == ""):
            # The page leaves the code's label cell empty; the code is still the value.
            if "code" not in values:
                values["code"] = first or second
                anomalies.append(f"table {index}: the code row has no label")
                continue
        anomalies.append(f"table {index}: a row with no label was skipped")
    return values


def parse_catalog(html: str) -> Catalog:
    """Parse the catalog page, raising ParseError when no course is found."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all(["script", "style", "noscript"]):
        tag.decompose()

    catalog = Catalog()
    seen: dict[str, int] = {}
    for index, table in enumerate(soup.find_all("table"), start=1):
        values = _fields(table, index, catalog.anomalies)
        code_display = values.get("code", "").strip()
        if not code_display:
            catalog.anomalies.append(f"table {index}: no course code")
            continue
        code_norm = normalize_code(code_display)
        number_match = re.search(r"\d+", code_norm)
        number = int(number_match.group(0)) if number_match else None
        level = level_for(number)
        level_note = _level_anomaly(code_norm, number, level)
        if level_note:
            catalog.anomalies.append(level_note)
        hours_text = values.get("hours") or None
        credit, lecture, lab, tutorial = parse_hours(hours_text)
        if hours_text and credit is None:
            catalog.anomalies.append(f"{code_norm}: the hours text {hours_text!r} was not parsed")
        prerequisites_text = values.get("prerequisites") or None
        corequisites_text = values.get("corequisites") or None
        name_ar = values.get("name") or None
        if name_ar is None:
            catalog.anomalies.append(f"{code_norm}: the name is not listed")
        if code_norm in seen:
            catalog.anomalies.append(
                f"{code_norm}: the code appears in tables {seen[code_norm]} and {index}"
            )
        seen[code_norm] = index
        catalog.courses.append(
            Course(
                code_display=code_display,
                code_norm=code_norm,
                number=number,
                level=level,
                name_ar=name_ar,
                hours_text=hours_text,
                credit_hours=credit,
                lecture_hours=lecture,
                lab_hours=lab,
                tutorial_hours=tutorial,
                prerequisites_text=prerequisites_text,
                prerequisite_codes=parse_codes(prerequisites_text),
                prerequisite_min_credit_hours=min_credit_hours(prerequisites_text),
                corequisites_text=corequisites_text,
                corequisite_codes=parse_codes(corequisites_text),
                description_ar=values.get("description") or None,
            )
        )

    if not catalog.courses:
        raise ParseError("no courses found on the catalog page")
    return catalog


def codes_json(codes: list[str]) -> str:
    """Store a code list as JSON text."""
    return json.dumps(codes, ensure_ascii=False)
