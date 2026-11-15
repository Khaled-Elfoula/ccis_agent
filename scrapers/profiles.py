"""Parse a faculty profile page: email, office and office hours (spec.md sections 7, 11)."""

import logging
import random
import re
from dataclasses import dataclass, field
from datetime import datetime

from bs4 import BeautifulSoup

from agent.arabic import normalize_arabic
from agent.names import strip_titles
from scrapers.base import Fetched, fetch
from scrapers.ccis import ParseError
from scrapers.sources import PROFILE_URL

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")
BUILDING_EN = re.compile(r"Building\s*(?:No\.?\s*)?(\d+)", re.IGNORECASE)
ROOM_EN = re.compile(r"(?:Room|Office)\s*(?:No\.?\s*)?([0-9A-Za-z]+)", re.IGNORECASE)
BUILDING_AR = re.compile(r"مبنى\s*(?:رقم\s*)?(\d+)")
ROOM_AR = re.compile(r"(?:مكتب|غرفة)\s*(?:رقم\s*)?([0-9A-Za-z\u0600-\u06FF]+)")
ARABIC_CHAR = re.compile(r"[\u0600-\u06FF]")
SCRIPT_SPLIT = re.compile(
    r"(?<=[\u0600-\u06FF])\s+(?=[A-Za-z])|(?<=[A-Za-z])\s+(?=[\u0600-\u06FF])"
)
EMPTY_CELL = {"", "_", "-", "—"}
DAY_NAMES = {
    "الأحد": "Sunday",
    "الاثنين": "Monday",
    "الإثنين": "Monday",
    "الثلاثاء": "Tuesday",
    "الأربعاء": "Wednesday",
    "الخميس": "Thursday",
    "الجمعة": "Friday",
    "السبت": "Saturday",
    "Sunday": "Sunday",
    "Monday": "Monday",
    "Tuesday": "Tuesday",
    "Wednesday": "Wednesday",
    "Thursday": "Thursday",
    "Friday": "Friday",
    "Saturday": "Saturday",
}


@dataclass
class OfficeHour:
    """One row of the profile office-hours table."""

    day: str
    start: str
    end: str


@dataclass
class Profile:
    """What a faculty profile page adds to the list data."""

    source_url: str
    email: str | None = None
    building: str | None = None
    room: str | None = None
    rank: str | None = None
    name_ar: str | None = None
    name_en: str | None = None
    office_hours: list[OfficeHour] = field(default_factory=list)


def parse_email(text: str) -> str | None:
    """Return a valid email address, never a guessed one."""
    value = (text or "").strip().lower()
    return value if EMAIL_RE.match(value) else None


def parse_office_address(text: str) -> tuple[str | None, str | None]:
    """Read a building and room from the free-text office line."""
    text = text or ""
    building_match = BUILDING_EN.search(text) or BUILDING_AR.search(text)
    room_match = ROOM_EN.search(text) or ROOM_AR.search(text)
    building = building_match.group(1) if building_match else None
    room = room_match.group(1) if room_match else None
    return building, room


def to_24h(value: str) -> str:
    """Turn '04:00 PM' into '16:00', returning an empty string when unreadable."""
    value = re.sub(r"\s+", " ", normalize_arabic(value)).strip().upper()
    if not value or value in EMPTY_CELL:
        return ""
    for pattern in ("%I:%M %p", "%I %p", "%H:%M"):
        try:
            return datetime.strptime(value, pattern).strftime("%H:%M")
        except ValueError:
            continue
    return ""


def _cell_text(row, index: int) -> str:
    """Read one cell of a table row, treating '_' as empty."""
    if row is None:
        return ""
    cells = row.find_all(["td", "th"])
    if index >= len(cells):
        return ""
    value = re.sub(r"\s+", " ", cells[index].get_text(" ", strip=True)).strip()
    return "" if value in EMPTY_CELL else value


def parse_office_hours(section) -> list[OfficeHour]:
    """Read the office-hours table: one row per day that has both a start and an end."""
    table = section.find("table") if section is not None else None
    if table is None:
        return []
    header = table.find("thead")
    days = [th.get_text(" ", strip=True) for th in header.find_all("th")] if header else []
    from_row = table.find("tr", class_="from")
    to_row = table.find("tr", class_="to")
    hours: list[OfficeHour] = []
    for index, label in enumerate(days):
        if index == 0:
            continue
        start = to_24h(_cell_text(from_row, index))
        end = to_24h(_cell_text(to_row, index))
        if not start or not end:
            continue
        day = DAY_NAMES.get(label.strip(), label.strip())
        if day not in DAY_NAMES.values():
            continue
        hours.append(OfficeHour(day=day, start=start, end=end))
    return hours


def split_name(text: str) -> tuple[str | None, str | None]:
    """Split a profile heading into its Arabic and English parts."""
    text = (text or "").strip()
    if not text:
        return None, None
    if "|" in text:
        parts = text.split("|")
    elif re.search(r"\s{2,}", text):
        parts = re.split(r"\s{2,}", text)
    else:
        parts = SCRIPT_SPLIT.split(text)
    parts = [re.sub(r"\s+", " ", part).strip(" ,|") for part in parts]
    parts = [part for part in parts if part]
    arabic = next((part for part in parts if ARABIC_CHAR.search(part)), None)
    english = next((part for part in parts if not ARABIC_CHAR.search(part)), None)
    if arabic or english:
        return arabic, _trim_descriptors(english)
    return (text, None) if ARABIC_CHAR.search(text) else (None, text)


def _trim_descriptors(name: str | None) -> str | None:
    """Drop a trailing description such as ', Highly Cited Researcher'."""
    if not name:
        return name
    head = name.split(",", 1)[0].strip()
    return head if len(head.split()) >= 2 else name


def parse_profile(html: str, url: str) -> Profile:
    """Parse a faculty profile page, raising ParseError when the banner is missing."""
    soup = BeautifulSoup(html, "lxml")
    banner = soup.select_one(".professor-Banner")
    if banner is None:
        raise ParseError(f"no profile banner on {url}")

    email_node = banner.select_one(".professor-Banner__mail")
    email = parse_email(email_node.get_text(" ", strip=True) if email_node else "")
    if email is None:
        mailto = next(
            (a["href"] for a in banner.find_all("a", href=True) if a["href"].startswith("mailto:")),
            "",
        )
        email = parse_email(mailto.split(":", 1)[-1])

    office_node = banner.select_one(".professor-Banner__office_address")
    building, room = parse_office_address(
        office_node.get_text(" ", strip=True) if office_node else ""
    )

    rank_node = banner.select_one(".professor-Banner__degree")
    name_node = banner.select_one(".professor-Banner__name")
    name_ar, name_en = split_name(name_node.get_text(" ", strip=True) if name_node else "")
    name_ar, name_en = strip_titles(name_ar or "") or None, strip_titles(name_en or "") or None

    return Profile(
        source_url=url,
        email=email,
        building=building,
        room=room,
        rank=rank_node.get_text(" ", strip=True) if rank_node else None,
        name_ar=name_ar,
        name_en=name_en,
        office_hours=parse_office_hours(soup.select_one("section.working_hours")),
    )
PROFILE_TIMEOUT = 10.0
PROFILE_ATTEMPTS = 2
PROFILE_INTERVAL = (5.0, 10.0)


def fetch_profile(
    username: str,
    force: bool = False,
    timeout: float | None = None,
    attempts: int | None = None,
    interval: float | None = None,
) -> tuple[Profile, Fetched]:
    """Download and parse one profile, politely: 10-15s apart, 10s timeout, 2 attempts."""
    url = PROFILE_URL.format(username=username)
    fetched = fetch(
        f"fac_profile_{username}",
        url,
        force=force,
        timeout=PROFILE_TIMEOUT if timeout is None else timeout,
        attempts=PROFILE_ATTEMPTS if attempts is None else attempts,
        min_interval=(
            random.uniform(*PROFILE_INTERVAL) if interval is None else interval
        ),
    )
    return parse_profile(fetched.html, url), fetched
