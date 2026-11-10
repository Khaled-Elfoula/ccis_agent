"""Registry of the pages the scrapers are allowed to fetch (spec.md section 11)."""

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
