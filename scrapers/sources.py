"""Registry of the pages the scrapers are allowed to fetch (spec.md section 11)."""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    """One scrapable page: stable id, URL, language and refresh hint."""

    id: str
    url: str
    lang: str
    refresh: str


SOURCES: tuple[Source, ...] = (
    Source("ccis_home", "https://ccis.ksu.edu.sa/ar", "ar", "weekly"),
    Source("ccis_about", "https://ccis.ksu.edu.sa/ar/aboutcollege", "ar", "monthly"),
    Source("ccis_contact", "https://ccis.ksu.edu.sa/ar/contact-us", "ar", "monthly"),
    Source("ccis_programs_bsc", "https://ccis.ksu.edu.sa/ar/bsc", "ar", "weekly"),
    Source("ccis_programs_masters", "https://ccis.ksu.edu.sa/ar/masters", "ar", "weekly"),
    Source("ccis_programs_phd", "https://ccis.ksu.edu.sa/ar/phd", "ar", "weekly"),
    Source("ccis_transfer", "https://ccis.ksu.edu.sa/ar/acceptance-and-transfer", "ar", "weekly"),
    Source("ccis_regulations", "https://ccis.ksu.edu.sa/ar/ccis-regulations", "ar", "monthly"),
    Source(
        "ccis_affairs_agency",
        "https://ccis.ksu.edu.sa/ar/student-affairs-agency-mail",
        "ar",
        "monthly",
    ),
)

SOURCES_BY_ID = {source.id: source for source in SOURCES}


@dataclass(frozen=True)
class FacultyList:
    """One department's faculty list page (spec.md section 11)."""

    id: str
    url: str
    department: str
    verified: bool = False


FACULTY_LISTS: tuple[FacultyList, ...] = (
    FacultyList("fac_list_se", "https://ccis.ksu.edu.sa/ar/se/se-faculty-and-staff", "SE", True),
    FacultyList("fac_list_cs", "https://cs.ksu.edu.sa/ar/cs/faculty-and-staff", "CS"),
    FacultyList("fac_list_it", "https://ccis.ksu.edu.sa/ar/it/it-faculty-and-staff", "IT"),
    FacultyList("fac_list_ce", "https://ccis.ksu.edu.sa/ar/ce/faculty-and-staff", "CE"),
    FacultyList("fac_list_is", "https://ccis.ksu.edu.sa/is/is-faculty-and-staff", "IS"),
)

FACULTY_LISTS_BY_ID = {faculty_list.id: faculty_list for faculty_list in FACULTY_LISTS}

CALENDAR = Source("ksu_calendar", "https://dar.ksu.edu.sa/ar/current", "ar", "daily")

PROFILE_URL = "https://faculty.ksu.edu.sa/ar/{username}"
PROFILE_URL = "https://faculty.ksu.edu.sa/ar/{username}"

USERNAME_RE = re.compile(
    r"(?:fac|faculty)\.ksu\.edu\.sa/(?:(?:ar|en)/)?([A-Za-z0-9_.-]+)"
)


def username_from_url(href: str) -> str | None:
    """Extract the faculty username from any shape of profile link (spec.md section 8)."""
    match = USERNAME_RE.search(href or "")
    if match is None:
        return None
    return match.group(1).strip(".-_") or None
