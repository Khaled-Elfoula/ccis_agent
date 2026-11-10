"""Tests for CCIS page cleaning and chunking."""

import pytest

from scrapers.ccis import ParseError, chunk_text, parse

PAGE = """
<html lang="ar"><head><title>اتصل بنا | كلية علوم الحاسب والمعلومات</title></head>
<body>
<nav>القائمة الرئيسية</nav>
<div role="main">
  <h1>اتصل بنا</h1>
  <div class="breadcrumb">الرئيسية >> اتصل بنا</div>
  <p>للتواصل مع الكلية عبر القنوات التالية:</p>
  <p>وحدة العلاقات العامة والإعلام، هاتف 0114697568.</p>
  <p>نموذج طلب معادلة مقرر</p>
  <p>(PDF)</p>
  <div class="newsstyle"><a>خبر قديم</a></div>
  <p>تاريخ آخر تحديث : يناير 2026</p>
  <p>شارك هذه الصفحة</p>
  <script>alert(1)</script>
</div>
<footer>جميع الحقوق محفوظة</footer>
</body></html>
"""


def test_parse_keeps_content_and_drops_boilerplate():
    page = parse(PAGE)
    assert page.title == "اتصل بنا"
    assert page.lang == "ar"
    assert "0114697568" in page.text
    assert "نموذج طلب معادلة مقرر" in page.text
    for noise in ("القائمة الرئيسية", "الرئيسية >>", "خبر قديم", "تاريخ آخر تحديث",
                  "شارك هذه الصفحة", "جميع الحقوق محفوظة", "alert", "(PDF)"):
        assert noise not in page.text
    assert page.chunks


def test_parse_raises_when_the_layout_changes():
    with pytest.raises(ParseError):
        parse("<html><body><div role='main'>short</div></body></html>")


def test_chunk_text_respects_size_and_overlaps():
    text = "\n".join(f"سطر رقم {index} في صفحة الكلية" for index in range(60))
    chunks = chunk_text(text, size=200, overlap=50)
    assert len(chunks) > 1
    assert all(len(chunk) <= 200 for chunk in chunks)
    assert chunks[0].splitlines()[-1] in chunks[1]


def test_chunk_text_splits_a_very_long_line():
    chunks = chunk_text("كلمة " * 400, size=200, overlap=20)
    assert all(len(chunk) <= 200 for chunk in chunks)
