"""Arabic-aware name normalization and faculty name matching (spec.md section 8)."""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from rapidfuzz import fuzz

from agent.arabic import normalize_arabic

THRESHOLD = 85.0
RUNNER_UP_GAP = 5.0
# Below this the query is not a person we know: no match beats a guess.
CANDIDATE_FLOOR = 50.0
# How close a query's family name must be to one of a candidate's tokens.
FAMILY_TOKEN_RATIO = 75.0
MAX_CANDIDATES = 3
OVERRIDES_PATH = Path(__file__).resolve().parent.parent / "data" / "name_overrides.json"

ARABIC_TITLES = frozenset({"أ.د", "أ.م.د", "د", "أ"})
LATIN_TITLES = frozenset({"dr", "prof", "professor"})
_PUNCTUATION = re.compile(r"[^\w\s]+", re.UNICODE)
_WHITESPACE = re.compile(r"\s+")
_JOINERS = str.maketrans("", "", "-'\u2019\u02bc`")

# Tokens that only join name parts ("Sami bin Salih", "سامي بن صالح") and identify nobody.
NAME_SEPARATORS = frozenset({"bin", "ibn", "ben", "bint", "بن", "ابن", "بنت"})
# The Arabic article: on its own ("Al-Ahmadi" -> "al ahmadi") or fused ("AlAhmadi", "الأحمدي").
ARTICLES = ("al", "el", "ال")
MIN_ARTICLE_STEM = 3
# Words that begin with the letters of an article but are not article + name.
NOT_ARTICLES = frozenset({"allah", "ullah", "ali", "alaa"})
# "Abdul" / "Abdel" / "Abdol" fused to the rest of the name: "Abdulrahman" == "Abdul Rahman".
FUSED_ABD = ("abdul", "abdel", "abdol", "abdal")

# Spelling variants of one name, folded to a single form.
# Saud (سعود) is deliberately absent: it is a different name from Saad (سعد).
TOKEN_ALIASES = {
    "abdullah": "abdullah",
    "abdallah": "abdullah",
    "abdulla": "abdullah",
    "abdalla": "abdullah",
    "abdollah": "abdullah",
    "abdolah": "abdullah",
    "muhammad": "muhammad",
    "mohammed": "muhammad",
    "mohamed": "muhammad",
    "mohamad": "muhammad",
    "mohammad": "muhammad",
    "muhammed": "muhammad",
    "ahmad": "ahmad",
    "ahmed": "ahmad",
    "ahmet": "ahmad",
    "saad": "saad",
    "saed": "saad",
    "husayn": "husayn",
    "husain": "husayn",
    "hussain": "husayn",
    "hussein": "husayn",
    "salih": "salih",
    "saleh": "salih",
    "abd": "abd",
    "abdul": "abd",
    "abdel": "abd",
    "abdol": "abd",
    "abdal": "abd",
}


def strip_titles(text: str) -> str:
    """Drop leading honorifics such as أ.د. / أ. / د. / Dr / Prof."""
    tokens = (text or "").split()
    while tokens:
        head = tokens[0].strip(".").lower()
        if head in ARABIC_TITLES or head in LATIN_TITLES:
            tokens.pop(0)
            continue
        break
    return " ".join(tokens)


def normalize_name(text: str) -> str:
    """Normalize a person's name: no titles, tashkeel, joiners or punctuation.

    Hyphens and apostrophes are removed rather than replaced with a space, so
    "Al-Ahmadi" stays one word and "Sa'ad" does not split into "sa ad".
    """
    if not text:
        return ""
    text = strip_titles(text)
    text = normalize_arabic(text)
    text = text.translate(_JOINERS)
    text = _PUNCTUATION.sub(" ", text)
    text = _WHITESPACE.sub(" ", text).strip().lower()
    return text


def name_tokens(text: str) -> list[str]:
    """Split a name into its normalized tokens."""
    return normalize_name(text).split()


def _fold_spelling(token: str) -> str:
    """Fold common Latin transliteration variants: ee/oo/ou, ph, and a final y."""
    token = token.replace("ee", "i").replace("oo", "u").replace("ou", "u")
    token = token.replace("ph", "f")
    if len(token) > 2 and token.endswith("y"):
        token = token[:-1] + "i"
    return token


def _fold_token(token: str) -> list[str]:
    """Fold one token: no joiner characters, articles or name separators; spelling unified."""
    token = token.translate(_JOINERS)
    if not token or token in NAME_SEPARATORS or token in ARTICLES:
        return []
    if token not in NOT_ARTICLES:
        for article in ARTICLES:
            if token.startswith(article) and len(token) - len(article) >= MIN_ARTICLE_STEM:
                token = token[len(article) :]
                break
    if token in TOKEN_ALIASES:
        return [_fold_spelling(TOKEN_ALIASES[token])]
    for prefix in FUSED_ABD:
        if token.startswith(prefix) and len(token) - len(prefix) >= MIN_ARTICLE_STEM:
            return ["abd", _fold_spelling(token[len(prefix) :])]
    return [_fold_spelling(token)]


def match_tokens(text: str) -> list[str]:
    """Tokens of a name after title, article, separator and transliteration folding."""
    raw = normalize_name(text).split()
    folded: list[str] = []
    index = 0
    while index < len(raw):
        token = raw[index]
        if token == "abd" and index + 1 < len(raw) and raw[index + 1] in {"allah", "ullah"}:
            folded.append("abdullah")
            index += 2
            continue
        folded.extend(_fold_token(token))
        index += 1
    return folded


def name_key(text: str) -> str:
    """The folded token string two names are compared as."""
    return " ".join(match_tokens(text))


def _edges_match(query: list[str], candidate: list[str]) -> bool:
    """True when the first and last folded tokens are identical."""
    if not query or not candidate:
        return False
    return query[0] == candidate[0] and query[-1] == candidate[-1]


@dataclass
class Candidate:
    """A faculty row with its similarity score and the tokens that produced it."""

    score: float
    row: dict
    tokens: list[str] = field(default_factory=list)


@dataclass
class Match:
    """Result of a name lookup: one confident row, or candidates to choose from."""

    accepted: dict | None = None
    candidates: list[dict] = field(default_factory=list)


def _row_token_lists(row: dict) -> list[list[str]]:
    """The folded token lists a faculty row can be matched on."""
    names = []
    for key in ("name_ar", "name_en"):
        tokens = match_tokens(row.get(key) or "")
        if tokens:
            names.append(tokens)
    return names


def _family_name_matches(family: str, tokens: list[str]) -> bool:
    """True when the query's family name matches one of the candidate's tokens."""
    return any(fuzz.ratio(family, token) >= FAMILY_TOKEN_RATIO for token in tokens)


def _best_candidate(query_key: str, row: dict) -> Candidate:
    """Score a row on both its names, keeping the tokens of the better one.

    Two names are only compared when they share a folded token, and, once the query
    carries a family name, when that family name matches too. Otherwise the score would
    just measure how many letters two unrelated names happen to have in common, and a
    shared first name alone would make a stranger look like a candidate.
    """
    query_tokens = query_key.split()
    query_set = set(query_tokens)
    best = Candidate(0.0, row)
    for tokens in _row_token_lists(row):
        if not query_set & set(tokens):
            continue
        if len(query_tokens) > 1 and not _family_name_matches(query_tokens[-1], tokens):
            continue
        score = fuzz.token_set_ratio(query_key, " ".join(tokens))
        if score > best.score:
            best = Candidate(score, row, tokens)
    return best


def score_row(query: str, row: dict) -> float:
    """Best token-set similarity between a query and a row's Arabic or English name."""
    return _best_candidate(name_key(query), row).score


def match_faculty(
    query: str,
    rows: list[dict],
    threshold: float = THRESHOLD,
    gap: float = RUNNER_UP_GAP,
    limit: int = MAX_CANDIDATES,
) -> Match:
    """Match a name against faculty rows, accepting only clear winners.

    A confident match is accepted. Anything scoring at or above CANDIDATE_FLOOR but
    not confident enough to accept comes back as candidates to confirm with the user,
    never as an answer. Below CANDIDATE_FLOOR the query matches nobody.
    """
    key = name_key(query)
    if not key or not rows:
        return Match()
    query_tokens = key.split()

    scored = sorted(
        (_best_candidate(key, row) for row in rows),
        key=lambda candidate: candidate.score,
        reverse=True,
    )
    scored = [candidate for candidate in scored if candidate.score >= CANDIDATE_FLOOR]
    if not scored:
        return Match()

    best = scored[0]
    runner_up = scored[1].score if len(scored) > 1 else 0.0
    if (
        best.score >= threshold
        and _edges_match(query_tokens, best.tokens)
        and best.score - runner_up >= gap
    ):
        return Match(accepted=best.row)
    return Match(candidates=[candidate.row for candidate in scored[:limit]])


def load_overrides(path: str | Path | None = None) -> dict[str, str]:
    """Read data/name_overrides.json as {normalized name: username}."""
    overrides_path = Path(path) if path is not None else OVERRIDES_PATH
    try:
        data = json.loads(overrides_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        normalize_name(key): value
        for key, value in data.items()
        if isinstance(key, str) and isinstance(value, str) and value.strip()
    }
