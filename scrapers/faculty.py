"""Parse the CCIS faculty list pages into one row per person (spec.md sections 7, 8, 11)."""

import logging
import re
from dataclasses import dataclass

from bs4 import BeautifulSoup

from agent.names import normalize_name
from scrapers.ccis import ParseError
from scrapers.sources import username_from_url

logger = logging.getLogger(__name__)

RANKS = ("أستاذ مشارك", "أستاذ مساعد", "أستاذ", "محاضر", "معيد", "مدرس")
RANK_HEADINGS = {
    "الأساتذة": "أستاذ",
    "الأساتذة المشاركون": "أستاذ مشارك",
    "الأساتذة المساعدون": "أستاذ مساعد",
    "المحاضرون": "محاضر",
    "المعيدون": "معيد",
    "المحاضرون والمعيدون": "محاضر",
}
NO_VALUE = re.compile(r"لا\s*يوجد|غير\s*محدد|^_+$")
OFFICE_LABELLED = re.compile(r"مكتب\s*:?\s*([0-9A-Za-z\u0600-\u06FF]+)\s*\|\s*([0-9A-Za-z\u0600-\u06FF]+)")
OFFICE_BARE = re.compile(r"(?<![\w])([0-9A-Za-z]{1,8})\|([0-9A-Za-z]{1,8})(?![\w])")
OFFICE_LABELS = frozenset({"هاتف", "جوال", "تحويلة", "مكتب", "الهاتف", "لا", "يوجد"})
PHONE_LABELLED = re.compile(r"(?:هاتف|جوال|تحويلة)\s*:?\s*([+\d][\d\s-]{4,})")
PHONE_BARE = re.compile(r"^\+?\d{6,10}$")
ROOM_LABELLED = re.compile(r"مكتب\s*(\d+[A-Za-z]?)")
ARABIC_CHAR = re.compile(r"[\u0600-\u06FF]")
NON_ARABIC = re.compile(r"[^\u0600-\u06FF\s]+")


@dataclass
class FacultyRow:
    """One person as shown on a department list page."""

    username: str
    name: str
    department: str
    profile_url: str
    list_url: str
    rank: str | None = None
    building: str | None = None
    room: str | None = None
    phone: str | None = None

    @property
    def name_ar(self) -> str | None:
        """The name when it is written in Arabic."""
        return self.name if ARABIC_CHAR.search(self.name) else None

    @property
    def name_en(self) -> str | None:
        """The name when it is written in Latin script."""
        return None if ARABIC_CHAR.search(self.name) else self.name

    @property
    def name_norm(self) -> str:
        """Normalized name for matching."""
        return normalize_name(self.name)


def parse_office(text: str) -> tuple[str | None, str | None]:
    """Read a building and room out of '31|2115' style text, in either order."""
    match = OFFICE_LABELLED.search(text or "") or OFFICE_BARE.search(text or "")
    if match is None:
        return None, None
    first, second = match.group(1), match.group(2)
    if second in OFFICE_LABELS:
        return (first, None) if first not in OFFICE_LABELS else (None, None)
    if first in OFFICE_LABELS:
        return (second, None) if second not in OFFICE_LABELS else (None, None)
    if first.isdigit() and not second.isdigit():
        return first, second
    if second.isdigit() and not first.isdigit():
        return second, first
    return first, second


def parse_phone(text: str) -> str | None:
    """Read a phone or extension, ignoring 'لا يوجد' and empty markers."""
    text = (text or "").strip()
    if not text or NO_VALUE.search(text):
        return None
    match = PHONE_LABELLED.search(text)
    if match:
        digits = re.sub(r"\D", "", match.group(1))
        return digits or None
    return re.sub(r"\D", "", text) if PHONE_BARE.match(text) else None


def parse_rank(text: str) -> str | None:
    """Find an academic rank in a cell, preferring the longer titles."""
    for rank in RANKS:
        if rank in (text or ""):
            return rank
    return None


def _heading_rank(tag) -> str | None:
    """Read the rank from the closest section heading above a table."""
    for previous in tag.find_all_previous(["h2", "h3", "h4"], limit=6):
        text = re.sub(r"\s+", " ", NON_ARABIC.sub(" ", previous.get_text(" ", strip=True))).strip()
        if text in RANK_HEADINGS:
            return RANK_HEADINGS[text]
    return None


def _cell_phone(cells) -> str | None:
    """Take the first cell that holds a phone number or extension."""
    for cell in cells:
        phone = parse_phone(cell.get_text(" ", strip=True))
        if phone:
            return phone
    return None


def _table_rows(table, department: str, list_url: str) -> list[FacultyRow]:
    """Read every faculty row of one table, skipping rows without a profile link."""
    rows: list[FacultyRow] = []
    heading_rank = _heading_rank(table)
    for tr in table.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        link = next(
            (a for a in tr.find_all("a", href=True) if username_from_url(a["href"])), None
        )
        if link is None:
            continue
        username = username_from_url(link["href"])
        name = re.sub(r"\s+", " ", link.get_text(" ", strip=True)).strip()
        if not username or not name:
            continue
        text = " ".join(cell.get_text(" ", strip=True) for cell in cells)
        rank = heading_rank
        for cell in cells:
            cell_rank = parse_rank(cell.get_text(" ", strip=True))
            if cell_rank:
                rank = cell_rank
                break
        building, room = parse_office(text)
        rows.append(
            FacultyRow(
                username=username,
                name=name,
                department=department,
                profile_url=link["href"],
                list_url=list_url,
                rank=rank,
                building=building,
                room=room,
                phone=_cell_phone(cells),
            )
        )
    return rows


def _card_container(link):
    """Walk up from a profile link to the card that holds its office line."""
    node = link
    for _ in range(5):
        node = node.parent
        if node is None:
            return None
        text = node.get_text(" ", strip=True)
        if "مكتب" in text or "تحويلة" in text:
            return node
    return None


def _bare_room(text: str) -> str | None:
    """Read a room number written as 'مكتب 2206' without a building."""
    match = ROOM_LABELLED.search(text or "")
    return match.group(1) if match else None


def _card_rows(soup: BeautifulSoup, department: str, list_url: str) -> list[FacultyRow]:
    """Read the card layout used by the CE page (no tables, one card per person)."""
    rows: list[FacultyRow] = []
    for link in soup.find_all("a", href=True):
        username = username_from_url(link["href"])
        if not username:
            continue
        card = _card_container(link)
        if card is None:
            continue
        name = re.sub(r"\s+", " ", link.get_text(" ", strip=True)).strip()
        if not name:
            continue
        text = card.get_text(" ", strip=True)
        building, room = parse_office(text)
        rows.append(
            FacultyRow(
                username=username,
                name=name,
                department=department,
                profile_url=link["href"],
                list_url=list_url,
                rank=parse_rank(text),
                building=building,
                room=room or _bare_room(text),
                phone=parse_phone(text),
            )
        )
    return rows


def _dedupe(rows: list[FacultyRow]) -> list[FacultyRow]:
    """Keep one row per username, filling a missing rank from a later duplicate."""
    by_username: dict[str, FacultyRow] = {}
    for row in rows:
        existing = by_username.get(row.username)
        if existing is None:
            by_username[row.username] = row
            continue
        if not existing.rank and row.rank:
            existing.rank = row.rank
    return list(by_username.values())


def parse_list(html: str, department: str, list_url: str) -> list[FacultyRow]:
    """Parse one faculty list page, raising ParseError when nothing was found."""
    soup = BeautifulSoup(html, "lxml")
    rows: list[FacultyRow] = []
    for table in soup.find_all("table"):
        rows.extend(_table_rows(table, department, list_url))
    if not rows:
        rows = _card_rows(soup, department, list_url)
    rows = _dedupe(rows)
    if not rows:
        raise ParseError(f"no faculty rows found on {list_url}")
    return rows
