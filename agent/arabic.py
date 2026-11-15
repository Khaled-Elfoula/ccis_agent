"""Arabic normalization shared by the indexer and the search tool."""

import re

TATWEEL = "\u0640"
_TASHKEEL = re.compile(r"[\u0610-\u061A\u064B-\u065F\u0670\u06D6-\u06ED]")
_ALEF = re.compile("[\u0623\u0625\u0622\u0671]")
_ARABIC_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
_NON_WORD = re.compile(r"[^\w]+", re.UNICODE)
_ARABIC_LETTER = re.compile(r"[\u0621-\u064A\u0660-\u0669\u06F0-\u06F9]")
_LATIN_LETTER = re.compile(r"[A-Za-z]")
_WHITESPACE = re.compile(r"\s+")

STOPWORDS = frozenset(
    {
        "في", "من", "علي", "على", "عن", "الي", "الى", "هو", "هي", "هل",
        "ما", "ماذا", "كيف", "متي", "اين", "لماذا", "او", "و", "التي", "الذي",
        "the", "a", "an", "of", "to", "is", "are", "and", "or", "in", "on",
        "for", "what", "when", "how", "where", "why", "do", "does", "can",
    }
)


def normalize_arabic(text: str) -> str:
    """Remove tashkeel and tatweel, unify alef/ya/ta-marbuta, and use ASCII digits."""
    if not text:
        return ""
    text = text.replace(TATWEEL, "")
    text = _TASHKEEL.sub("", text)
    text = _ALEF.sub("\u0627", text)
    text = text.replace("\u0649", "\u064A").replace("\u0629", "\u0647")
    text = text.translate(_ARABIC_DIGITS)
    return _WHITESPACE.sub(" ", text).strip()


ARTICLE = "ال"
MIN_ARTICLE_STEM = 5


def search_form(text: str) -> str:
    """Normalize text and drop the Arabic article, for indexing and matching."""
    return " ".join(_without_article(token) for token in normalize_arabic(text).split())


def _without_article(token: str) -> str:
    """Drop a leading 'ال' so that 'الأقسام' also matches 'أقسام'."""
    if token.startswith(ARTICLE) and len(token) >= MIN_ARTICLE_STEM:
        return token[len(ARTICLE) :]
    return token


def tokenize(text: str) -> list[str]:
    """Split text into search tokens, dropping stopwords and 1-letter noise."""
    tokens = [tok for tok in _NON_WORD.split(search_form(text)) if len(tok) > 1]
    content = [tok for tok in tokens if tok not in STOPWORDS]
    return content or tokens


def fts_query(text: str) -> str:
    """Build an FTS5 OR query with prefix matching from free text."""
    return " OR ".join(f'"{tok}"*' for tok in tokenize(text))

def detect_language(text: str) -> str:
    """Report the language a message is written in: 'ar' or 'en'."""
    arabic = len(_ARABIC_LETTER.findall(text or ""))
    latin = len(_LATIN_LETTER.findall(text or ""))
    return "ar" if arabic > latin else "en"
