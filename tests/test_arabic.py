"""Tests for Arabic normalization and FTS query building."""

from agent.arabic import detect_language, fts_query, normalize_arabic, search_form, tokenize


def test_removes_tashkeel_and_tatweel():
    assert normalize_arabic("كُلِّيَّةُ عُلُومِ الحَاسُوبِ") == "كليه علوم الحاسوب"
    assert normalize_arabic("الحـــاسوب") == "الحاسوب"


def test_unifies_alef_ya_and_ta_marbuta():
    assert normalize_arabic("أحمد") == normalize_arabic("احمد")
    assert normalize_arabic("إدارة") == normalize_arabic("اداره")
    assert normalize_arabic("آمن") == "امن"
    assert normalize_arabic("مستشفى") == "مستشفي"


def test_normalizes_arabic_indic_digits():
    assert normalize_arabic("عام ١٤٤٨") == "عام 1448"
    assert normalize_arabic("٣.٢٥") == "3.25"


def test_collapses_whitespace_and_handles_empty():
    assert normalize_arabic("  شروط   القبول \n") == "شروط القبول"
    assert normalize_arabic("") == ""


def test_tokenize_drops_stopwords_and_punctuation():
    assert tokenize("ما هي شروط القبول في الماجستير؟") == ["شروط", "قبول", "ماجستير"]


def test_search_form_drops_the_article():
    assert search_form("الأقسام الأكاديمية") == "اقسام اكاديميه"
    assert search_form("الكلية") == "كليه"
    assert search_form("المال") == "مال"
    assert search_form("الآن") == "الان"


def test_fts_query_is_or_with_prefixes():
    assert fts_query("شروط القبول") == '"شروط"* OR "قبول"*'


def test_fts_query_empty_for_punctuation_only():
    assert fts_query("؟!.") == ""


def test_detect_language_follows_the_script():
    assert detect_language("ما هي شروط القبول؟") == "ar"
    assert detect_language("What are the admission requirements?") == "en"
    assert detect_language("سعد الأحمدي office 2179") == "ar"
    assert detect_language("") == "en"
