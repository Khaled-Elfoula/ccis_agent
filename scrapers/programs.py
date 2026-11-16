"""Parse the three CCIS program pages into one row per degree programme."""

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from scrapers.ccis import ParseError
from scrapers.sources import SOURCES_BY_ID

DEGREES = {
    "ccis_programs_bsc": "بكالوريوس",
    "ccis_programs_masters": "ماجستير",
    "ccis_programs_phd": "دكتوراه",
}
DEPARTMENT_NAMES = {
    "علوم الحاسب": "CS",
    "هندسة الحاسب": "CE",
    "تقنية المعلومات": "IT",
    "هندسة البرمجيات": "SE",
    "نظم المعلومات": "IS",
}
PROGRAM_PREFIXES = {
    "بكالوريوس": ("بكالوريوس", "برنامج التخصص"),
    "ماجستير": ("درجة الماجستير", "برنامج الماجستير"),
    "دكتوراه": ("درجة الدكتوراه", "برنامج الدكتوراه"),
}
REQUIREMENTS_HEADING = "شروط القبول"
SHARED_HEADING = "البرامج المشتركة"
DEPARTMENT_HEADING = "برامج الأقسام"
MAX_REQUIREMENTS = 6000


@dataclass
class Program:
    """One degree programme listed on a program page."""

    degree: str
    department: str | None
    name_ar: str
    requirements_ar: str | None = None
    name_en: str | None = None


def _clean(text: str) -> str:
    """Collapse whitespace in one block of page text."""
    return re.sub(r"\s+", " ", text or "").strip()


def _department(text: str) -> str | None:
    """Map a department heading or link label to its short code."""
    for name, code in DEPARTMENT_NAMES.items():
        if name in text:
            return code
    return None


def _is_department_heading(text: str) -> bool:
    """Tell a department heading from a program line that mentions a department."""
    return text.startswith("قسم ") or text in DEPARTMENT_NAMES


def _is_program(text: str, degree: str) -> bool:
    """Tell a program title from a requirements sentence."""
    prefixes = PROGRAM_PREFIXES.get(degree, ())
    if not any(text.startswith(prefix) for prefix in prefixes):
        return False
    return not any(
        marker in text
        for marker in (
            "شروط القبول",
            "أن يكون",
            "أن تكون",
            "يجب",
            "لا يقبل",
            "الحصول",
            "تقديم",
            "تحميل",
        )
    )


def parse(html: str, degree: str, source_url: str) -> list[Program]:
    """Parse one program page, raising ParseError when no programme was found."""
    soup = BeautifulSoup(html, "lxml")
    node = soup.select_one(".node__content") or soup.select_one("[role=main]") or soup
    programs: list[Program] = []
    seen: set[tuple[str, str | None, str]] = set()
    department: str | None = None
    current: list[Program] = []
    requirements: list[str] = []
    collecting = False

    def flush_requirements() -> None:
        nonlocal requirements, collecting
        text = "\n".join(requirements).strip()[:MAX_REQUIREMENTS]
        if text:
            for program in current:
                if not program.requirements_ar:
                    program.requirements_ar = text
        requirements = []
        collecting = False

    for tag in node.find_all(["p", "li", "h2", "h3", "h4", "h5"]):
        if tag.name == "p" and tag.find_parent("li") is not None:
            continue
        text = _clean(tag.get_text(" ", strip=True))
        if not text:
            continue
        if SHARED_HEADING in text:
            flush_requirements()
            department = "مشترك"
            current = []
            continue
        if DEPARTMENT_HEADING in text:
            flush_requirements()
            current = []
            continue
        if degree != "بكالوريوس" and (
            text == REQUIREMENTS_HEADING or text.startswith("شروط القبول")
        ):
            flush_requirements()
            collecting = True
            continue
        if _is_department_heading(text) and tag.find("a") is not None:
            flush_requirements()
            department = _department(text)
            current = []
            continue
        if _is_program(text, degree):
            flush_requirements()
            key = (degree, department, text)
            if key not in seen:
                seen.add(key)
                program = Program(
                    degree=degree,
                    department=department,
                    name_ar=text,
                    requirements_ar=None,
                )
                programs.append(program)
                current.append(program)
            continue

        if collecting and current:
            requirements.append(text)

    flush_requirements()
    if not programs:
        raise ParseError(f"no programs found on {source_url}")
    return programs


def parse_source(source_id: str, html: str) -> list[Program]:
    """Parse one known program source by its registry id."""
    source = SOURCES_BY_ID[source_id]
    return parse(html, DEGREES[source_id], source.url)
