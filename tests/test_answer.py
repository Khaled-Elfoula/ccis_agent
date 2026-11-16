"""Tests for cleaning the model's final answer."""

from agent.answer import clean_answer, collect_urls


def test_clean_answer_removes_an_invented_url():
    answer = "راجع https://fake.example.com/ccis للحصول على الشروط."
    assert clean_answer(answer, set()) == "راجع للحصول على الشروط."


def test_clean_answer_keeps_a_url_from_a_tool_result():
    url = "https://ccis.ksu.edu.sa/ar/masters"
    answer = f"المصدر: {url}"
    assert clean_answer(answer, collect_urls({"source_url": url})) == f"المصدر: {url}"


def test_clean_answer_keeps_a_url_the_user_wrote():
    url = "https://ccis.ksu.edu.sa/ar/contact-us"
    assert clean_answer(f"هذه الصفحة: {url}", collect_urls(f"افتح {url}")) == f"هذه الصفحة: {url}"


def test_clean_answer_keeps_a_known_subpage():
    url = "https://ccis.ksu.edu.sa/ar/masters"
    answer = "التفاصيل: https://ccis.ksu.edu.sa/ar/masters/cybersecurity"
    assert "cybersecurity" in clean_answer(answer, collect_urls({"source_url": url}))


def test_clean_answer_drops_an_unknown_markdown_link_but_keeps_its_label():
    answer = "[قسم علوم الحاسب](https://fake.example.com/cs)"
    assert clean_answer(answer, set()) == "قسم علوم الحاسب"


def test_clean_answer_removes_markdown_markers():
    answer = "### عنوان\n**د. سعد**\n* نقطة أولى\n* نقطة ثانية"
    assert clean_answer(answer, set()) == "عنوان\nد. سعد\n- نقطة أولى\n- نقطة ثانية"


def test_clean_answer_removes_a_source_line_left_empty_by_a_removed_url():
    answer = "الجواب صحيح.\nالمصدر: https://fake.example.com/x"
    assert clean_answer(answer, set()) == "الجواب صحيح."


def test_collect_urls_walks_nested_results():
    found = collect_urls(
        {"faculty": {"profile_url": "https://faculty.ksu.edu.sa/ar/x"}, "items": ["https://a.ksu.edu.sa"]}
    )
    assert found == {
        "https://faculty.ksu.edu.sa/ar/x",
        "https://a.ksu.edu.sa",
    }
