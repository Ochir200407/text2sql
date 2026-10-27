"""Орчуулгын кэшийн masking засвар (pipeline/translate_fix.py)."""
from mn_text2sql.pipeline.translate_fix import fix_record, span_value

KEYS = {"дархан салбар": "Дархан салбар", "баасан": "Баасан", "ховд": "Ховд"}


def test_span_value():
    assert span_value("Дархан салбараас", "...", KEYS) == "Дархан салбар"
    assert span_value("Ховдод", "...", KEYS) == "Ховд"
    assert span_value("байсан", "...", KEYS) is None          # «Баасан»-тай бүдэг таарсан ч өөр үг
    assert span_value("Байвал", "...", KEYS) is None          # өгүүлбэрийн эхний үг
    assert span_value("LN-2024-000368", "...", KEYS) is True  # бизнес түлхүүр
    assert span_value("2026-06-30", "...", KEYS) is True
    assert span_value("Тэргүүн", "«Тэргүүн» бүтээгдэхүүн", KEYS) is True   # ишлэл


def test_spurious_mask_falls_back_to_bilingual():
    rec = {"source": "Хэдэн зээл байсан бэ?", "spans": {"[V1]": "байсан"},
           "raw": "How many loans were there at [V1]?", "text": 'How many loans were there at "байсан"?'}
    text, reasons = fix_record(rec, KEYS)
    assert reasons == ["spurious_mask"]
    assert text == "Хэдэн зээл байсан бэ?\n(English translation: How many loans were there at байсан?)"


def test_invented_placeholder_and_canonical_and_currency():
    rec = {"source": "Багцын зээл хэд вэ?", "spans": {}, "raw": "How many loans in the [V1] group?",
           "text": "How many loans in the [V1] group?"}
    text, reasons = fix_record(rec, KEYS)
    assert reasons == ["invented_placeholder"] and "[V1]" not in text and text.startswith("Багцын зээл хэд вэ?\n")
    rec = {"source": "Дархан салбараас олгосон зээл", "spans": {"[V1]": "Дархан салбараас"},
           "raw": "Loans disbursed at the [V1] branch", "text": 'Loans disbursed at the "Дархан салбараас" branch'}
    assert fix_record(rec, KEYS) == ('Loans disbursed at the "Дархан салбар" branch', ["canonical_value"])
    rec = {"source": "...", "spans": {}, "raw": "How many Tenge?", "text": "How many Tenge?"}
    assert fix_record(rec, KEYS) == ("How many tugrik (MNT)?", ["currency_name"])
    ok = {"source": "...", "spans": {"[V1]": "Дархан салбар"}, "raw": "at [V1]", "text": 'at "Дархан салбар"'}
    assert fix_record(ok, KEYS) == ('at "Дархан салбар"', [])
