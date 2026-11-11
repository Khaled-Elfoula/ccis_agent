"""Tests for Arabic name normalization and faculty matching (spec.md section 8)."""

import json

from agent.names import (
    CANDIDATE_FLOOR,
    MAX_CANDIDATES,
    load_overrides,
    match_faculty,
    match_tokens,
    name_tokens,
    normalize_name,
    strip_titles,
)

FACULTY = [
    {
        "username": "salahmadi",
        "name_ar": "سعد عبدالله الأحمدي",
        "name_en": "Saad Abdullah AlAhmadi",
    },
    {"username": "asoudani", "name_ar": "عادل خليفة سوداني", "name_en": None},
    {"username": "salman", "name_ar": "عبدالملك سلمان السلمان", "name_en": None},
    {"username": "msalem", "name_ar": "محمد عبدالله السالم", "name_en": None},
    {"username": "msalem2", "name_ar": "محمد سعد السالم", "name_en": None},
    {"username": "swakeel", "name_ar": "سامي بن صالح الوكيل", "name_en": "Sami Alwakeel"},
    {"username": "sharbi", "name_ar": "سامي الحربي", "name_en": "Sami AlHarbi"},
]


def test_normalize_strips_titles_tashkeel_and_alef_variants():
    assert normalize_name("أ.د. سَعْد عَبدالله الأَحْمَدِي") == "سعد عبدالله الاحمدي"
    assert normalize_name("  د. سعد   الأحمدي ") == "سعد الاحمدي"


def test_normalize_folds_ta_marbuta_and_alef_maqsura():
    assert normalize_name("سلمى") == normalize_name("سلمي")
    assert normalize_name("حمزة") == normalize_name("حمزه")


def test_strip_titles_handles_spaced_and_latin_titles():
    assert strip_titles("أ. د. احمد عبدالله سند") == "احمد عبدالله سند"
    assert strip_titles("Dr. Saad AlAhmadi") == "Saad AlAhmadi"
    assert strip_titles("أحمد سند") == "أحمد سند"


def test_name_tokens_are_normalized():
    assert name_tokens("أ.م.د. عادل خليفة سوداني") == ["عادل", "خليفه", "سوداني"]


def test_folding_drops_hyphens_apostrophes_and_the_article():
    assert match_tokens("Al-Ahmadi") == ["ahmadi"]
    assert match_tokens("AlAhmadi") == ["ahmadi"]
    assert match_tokens("Sa'ad Al-Ahmadi") == ["saad", "ahmadi"]
    assert match_tokens("سعد الأحمدي") == ["سعد", "احمدي"]


def test_folding_treats_bin_and_ibn_as_separators():
    assert match_tokens("Sami bin Salih Alwakeel") == ["sami", "salih", "wakil"]
    assert match_tokens("سامي بن صالح الوكيل") == ["سامي", "صالح", "وكيل"]
    assert match_tokens("سامي ابن صالح الوكيل") == ["سامي", "صالح", "وكيل"]


def test_folding_unifies_common_transliteration_variants():
    for variant in ("Abdullah", "Abdallah", "Abdulla"):
        assert match_tokens(variant) == ["abdullah"]
    for variant in ("Mohammed", "Muhammad", "Mohamed"):
        assert match_tokens(variant) == ["muhammad"]
    assert match_tokens("Saed") == match_tokens("Saad") == ["saad"]
    assert match_tokens("Samy") == match_tokens("Sami") == ["sami"]
    assert match_tokens("Al-Wakeel") == match_tokens("Alwakeel") == ["wakil"]


def test_folding_keeps_saud_distinct_from_saad():
    """Saud (سعود) is a different name, so it must not be folded into Saad (سعد)."""
    assert match_tokens("Saud") != match_tokens("Saad")


def test_accepts_an_exact_name():
    match = match_faculty("عادل خليفة سوداني", FACULTY)
    assert match.accepted["username"] == "asoudani"
    assert match.candidates == []


def test_accepts_a_name_without_the_middle_name():
    match = match_faculty("سعد الاحمدي", FACULTY)
    assert match.accepted["username"] == "salahmadi"


def test_accepts_a_misspelled_name_with_different_letters():
    match = match_faculty("سعد عبدالله الاحمدى", FACULTY)
    assert match.accepted["username"] == "salahmadi"


def test_accepts_an_english_name():
    match = match_faculty("Saad AlAhmadi", FACULTY)
    assert match.accepted["username"] == "salahmadi"


def test_short_english_name_with_hyphen_resolves_to_the_right_person():
    """The demo case: "Dr. Saad Al-Ahmadi" must never land on somebody else."""
    match = match_faculty("Dr. Saad Al-Ahmadi", FACULTY)
    best = match.accepted or match.candidates[0]
    assert best["username"] == "salahmadi"
    assert {row["username"] for row in match.candidates} <= {"salahmadi"}


def test_both_wakeel_spellings_resolve_to_the_same_person():
    for query in ("Sami Alwakeel", "Sami Al-Wakeel", "Dr. Sami Al Wakeel"):
        match = match_faculty(query, FACULTY)
        assert match.accepted["username"] == "swakeel"
        assert match.candidates == []


def test_a_heavier_misspelling_returns_candidates_instead_of_guessing():
    match = match_faculty("سعد عبد اللة الاحمدي", FACULTY)
    assert match.accepted is None
    assert match.candidates[0]["username"] == "salahmadi"


def test_ambiguous_name_returns_candidates():
    match = match_faculty("محمد السالم", FACULTY)
    assert match.accepted is None
    assert {row["username"] for row in match.candidates} == {"msalem", "msalem2"}


def test_ambiguous_short_name_returns_multiple_candidates():
    match = match_faculty("Sami", FACULTY)
    assert match.accepted is None
    assert {row["username"] for row in match.candidates} == {"swakeel", "sharbi"}


def test_a_below_threshold_match_is_a_candidate_not_an_answer():
    """Saud shares only the family name with Saad: ask, do not answer."""
    match = match_faculty("Saud AlAhmadi", FACULTY)
    assert match.accepted is None
    assert [row["username"] for row in match.candidates] == ["salahmadi"]


def test_candidates_are_capped():
    match = match_faculty("سعد", FACULTY)
    assert match.accepted is None
    assert len(match.candidates) <= MAX_CANDIDATES


def test_first_name_only_is_not_enough():
    match = match_faculty("سعد", FACULTY)
    assert match.accepted is None
    assert match.candidates


def test_a_misspelled_family_name_is_a_candidate_not_a_match():
    match = match_faculty("Saad Al-Ahmdi", FACULTY)
    assert match.accepted is None
    assert match.candidates[0]["username"] == "salahmadi"


def test_a_different_family_name_is_not_a_candidate():
    """Sharing only the first name must not put a stranger in front of the user."""
    match = match_faculty("محمد الحسين", FACULTY)
    assert match.accepted is None
    assert match.candidates == []


def test_unknown_name_finds_nothing():
    match = match_faculty("خالد الغامدي", FACULTY)
    assert match.accepted is None
    assert match.candidates == []


def test_nonsense_name_finds_nothing():
    match = match_faculty("zzz qqq", FACULTY)
    assert match.accepted is None
    assert match.candidates == []
    assert CANDIDATE_FLOOR == 50.0


def test_empty_query_finds_nothing():
    assert match_faculty("   ", FACULTY).accepted is None


def test_overrides_file_is_read_and_normalized(tmp_path):
    path = tmp_path / "name_overrides.json"
    path.write_text(json.dumps({"أ.د. سعد الاحمدي": "salahmadi"}), encoding="utf-8")
    assert load_overrides(path) == {"سعد الاحمدي": "salahmadi"}


def test_a_missing_or_broken_overrides_file_is_ignored(tmp_path):
    assert load_overrides(tmp_path / "missing.json") == {}
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert load_overrides(broken) == {}
