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
OFFICE_COMPACT = re.compile(r"(?<![\w])([0-9]{1,2})([A-Za-z][0-9]{1,3})(?![\w])")
OFFICE_LETTER = re.compile(r"مكتب\s*([\u0600-\u06FF])\s*(\d{2,4})")
OFFICE_BUILDING_ONLY = re.compile(r"مكتب\s*:?\s*(\d{1,2})\s*\|")
OFFICE_LABELS = frozenset({"هاتف", "جوال", "تحويلة", "تحويله", "مكتب", "الهاتف", "لا", "يوجد"})
SECTION_MARKERS = ("هيئة التدريس", "هيئة تدريس", "قسم ", "الأقسام", "الدرجة العلمية")
PHONE_LABELLED = re.compile(r"(?:هاتف|جوال|تحويلة|تحويله)\s*:?\s*([+\d][\d\s-]{4,})")
PHONE_BARE = re.compile(r"^\+?\d{6,10}$")
ROOM_LABELLED = re.compile(r"مكتب\s*(\d+[A-Za-z]?)")
ARABIC_CHAR = re.compile(r"[\u0600-\u06FF]")
NON_ARABIC = re.compile(r"[^\u0600-\u06FF\s]+")
HEADER_NAMES = frozenset(
    {
        "الاسم",
        "الإسم",
        "اسم",
        "name",
        "القسم",
        "للتواصل",
        "هاتف",
        "الهاتف",
        "المكتب",
        "البريد",
        "الدرجة العلمية",
        "الدرجة العلميه",
    }
)
FORMER_HEADING = re.compile(r"سابق")


@dataclass
class FacultyRow:
    """One person as shown on a department list page."""

    username: str
    name: str
    department: str
    profile_url: str | None
    list_url: str
    rank: str | None = None
    building: str | None = None
    room: str | None = None
    phone: str | None = None
    email: str | None = None

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
    text = text or ""
    match = OFFICE_LABELLED.search(text) or OFFICE_BARE.search(text)
    if match is not None:
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

    letter = OFFICE_LETTER.search(text)
    if letter is not None:
        return letter.group(1), letter.group(2)

    compact = OFFICE_COMPACT.search(text)
    if compact is not None:
        return compact.group(1), compact.group(2)

    building_only = OFFICE_BUILDING_ONLY.search(text)
    if building_only is not None:
        return building_only.group(1), None

    room = ROOM_LABELLED.search(text)
    if room is not None:
        return None, room.group(1)
    return None, None


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


def _mailto_email(href: str | None) -> str | None:
    """Read a real mailto address, ignoring the page's share link."""
    if not href or not str(href).lower().startswith("mailto:"):
        return None
    address = str(href)[7:].split("?", 1)[0].strip()
    return address.lower() if "@" in address else None


def _email_username(email: str | None) -> str | None:
    """Derive a stable username from an email local part."""
    if not email or "@" not in email:
        return None
    local = re.sub(r"[^A-Za-z0-9_.-]", "", email.split("@", 1)[0])
    return local.lower() or None


def _synthetic_username(department: str, name: str) -> str:
    """A stable key for a person the page lists without a profile link or email."""
    slug = re.sub(r"\s+", "-", normalize_name(name)).strip("-") or "unknown"
    return f"list:{department.lower()}:{slug}"


def _clean_text(value: str) -> str:
    """Collapse whitespace in one cell or card label."""
    return re.sub(r"\s+", " ", value or "").strip()


def _row_username(link, mail_link) -> str | None:
    """Prefer the profile username, then the email local part."""
    if link is not None:
        username = username_from_url(link["href"])
        if username:
            return username
    return _email_username(_mailto_email(mail_link["href"] if mail_link is not None else None))


def _table_rows(table, department: str, list_url: str) -> list[FacultyRow]:
    """Read every faculty row of one table, including rows with no profile link."""
    rows: list[FacultyRow] = []
    heading_rank = _heading_rank(table)
    former = False
    for tr in table.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if not cells:
            continue
        text = " ".join(cell.get_text(" ", strip=True) for cell in cells)
        if FORMER_HEADING.search(text):
            former = True
            continue
        if former:
            continue

        link = next(
            (a for a in tr.find_all("a", href=True) if username_from_url(a["href"])), None
        )
        mail_link = next(
            (
                a
                for a in tr.find_all("a", href=True)
                if _mailto_email(a["href"]) is not None
            ),
            None,
        )
        name = _clean_text(link.get_text(" ", strip=True)) if link is not None else ""
        if not name:
            name = next(
                (
                    _clean_text(cell.get_text(" ", strip=True))
                    for cell in cells
                    if _clean_text(cell.get_text(" ", strip=True))
                ),
                "",
            )
        if not name or name in HEADER_NAMES:
            continue

        email = _mailto_email(mail_link["href"] if mail_link is not None else None)
        username = _row_username(link, mail_link) or _synthetic_username(department, name)
        rank = heading_rank
        for cell in cells:
            cell_rank = parse_rank(cell.get_text(" ", strip=True))
            if cell_rank:
                rank = cell_rank
                break
        building, room = parse_office(text)
        if (
            link is None
            and mail_link is None
            and (
                name in RANK_HEADINGS
                or any(marker in name for marker in SECTION_MARKERS)
            )
        ):
            continue
        rows.append(
            FacultyRow(
                username=username,
                name=name,
                department=department,
                profile_url=link["href"] if link is not None else None,
                list_url=list_url,
                rank=rank,
                building=building,
                room=room,
                phone=_cell_phone(cells),
                email=email,
            )
        )
    return rows


def _card_container(link):
    """Walk up from a link to the card that holds its office line."""
    node = link
    for _ in range(6):
        node = node.parent
        if node is None:
            return None
        text = node.get_text(" ", strip=True)
        if "مكتب" in text or "تحويلة" in text or "تحويله" in text:
            return node
    return None


def _card_name(card, profile_link) -> str:
    """Read a card's name from its profile link or its name span."""
    if profile_link is not None:
        name = _clean_text(profile_link.get_text(" ", strip=True))
        if name:
            return name
    tag = card.select_one("span[style*='color:#1a1f2b']")
    if tag is not None:
        name = _clean_text(tag.get_text(" ", strip=True))
        if name:
            return name
    for tag in card.find_all(["b", "strong", "span"]):
        text = _clean_text(tag.get_text(" ", strip=True))
        if (
            text
            and text not in HEADER_NAMES
            and "@" not in text
            and "مكتب" not in text
            and "تحويلة" not in text
            and "تحويله" not in text
            and parse_rank(text) is None
        ):
            return text
    return ""


def _find_card(link):

    """Find the full card around a link, even when it has no office line."""
    card = link.find_parent(
        "div", attrs={"style": re.compile(r"background-color:\s*#ffffff")}
    )
    return card if card is not None else _card_container(link)

def _card_rows(soup: BeautifulSoup, department: str, list_url: str) -> list[FacultyRow]:
    """Read the card layout used by the CE page (no tables, one card per person)."""
    rows: list[FacultyRow] = []
    cards = []
    for link in soup.find_all("a", href=True):
        if not (username_from_url(link["href"]) or _mailto_email(link["href"])):
            continue
        card = _find_card(link)
        if card is not None and card not in cards:
            cards.append(card)


    for card in cards:
        profile_link = next(
            (a for a in card.find_all("a", href=True) if username_from_url(a["href"])), None
        )
        mail_link = next(
            (
                a
                for a in card.find_all("a", href=True)
                if _mailto_email(a["href"]) is not None
            ),
            None,
        )
        name = _card_name(card, profile_link)
        if not name or name in HEADER_NAMES:
            continue
        email = _mailto_email(mail_link["href"] if mail_link is not None else None)
        username = _row_username(profile_link, mail_link) or _synthetic_username(department, name)
        text = card.get_text(" ", strip=True)
        building, room = parse_office(text)
        rows.append(
            FacultyRow(
                username=username,
                name=name,
                department=department,
                profile_url=profile_link["href"] if profile_link is not None else None,
                list_url=list_url,
                rank=parse_rank(text),
                building=building,
                room=room,
                phone=parse_phone(text),
                email=email,
            )
        )
    return rows


def _fill_missing(target: FacultyRow, source: FacultyRow) -> None:
    """Fill a duplicate's missing fields without overwriting a real value."""
    for field in ("rank", "email", "building", "room", "phone", "profile_url"):
        if not getattr(target, field) and getattr(source, field):
            setattr(target, field, getattr(source, field))


def _dedupe(rows: list[FacultyRow]) -> list[FacultyRow]:
    """Keep one row per username or name, filling a missing value from a duplicate."""
    by_username: dict[str, FacultyRow] = {}
    by_name: dict[str, FacultyRow] = {}
    synthetic_counts: dict[str, int] = {}
    for row in rows:
        name_key = normalize_name(row.name)
        same_name = by_name.get(name_key)
        if same_name is not None:
            if same_name.profile_url and not row.profile_url:
                _fill_missing(same_name, row)
                continue
            if row.profile_url and not same_name.profile_url:
                _fill_missing(row, same_name)
                by_username.pop(same_name.username.lower(), None)
                by_name[name_key] = row
                by_username[row.username.lower()] = row
                continue
            _fill_missing(same_name, row)
            continue

        key = row.username.lower()
        existing = by_username.get(key)
        if existing is None:
            by_username[key] = row
            by_name[name_key] = row
            continue
        if existing.username.startswith("list:") and not row.username.startswith("list:"):
            by_username[key] = row
            by_name[name_key] = row
            _fill_missing(row, existing)
            continue
        if row.username.startswith("list:") and not existing.username.startswith("list:"):
            _fill_missing(existing, row)
            continue
        if row.username.startswith("list:") and existing.username.startswith("list:"):
            count = synthetic_counts.get(key, 1) + 1
            synthetic_counts[key] = count
            row.username = f"{row.username}-{count}"
            by_username[row.username.lower()] = row
            by_name[name_key] = row
            continue
        _fill_missing(existing, row)
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
