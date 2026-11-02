"""R1.2: орчуулгын дараах шалгалт (pipeline/translate_check.py) ба translate_check: bilingual (M1tc)."""
from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

from mn_text2sql.pipeline.options import RunOptions, load_experiments, resolve_run
from mn_text2sql.pipeline.translate_check import (english_numbers, keys_from_values, source_numbers,
                                                  translation_check)

KEYS = keys_from_values([{"value": "Эрдэнэт салбар"}, {"value": "Баасан"}, {"value": "Сар"}, {"value": "UB01"}])


def test_clean_translation_not_flagged():
    assert translation_check("2025 онд нийт хэдэн зээлийн хүсэлт ирсэн бэ?",
                             "How many loan applications were received in 2025?") == []


def test_number_normalization_million_month_quarter():
    assert source_numbers("5 сая төгрөг") == {5_000_000}
    assert source_numbers("1.2 тэрбум") == {1_200_000_000}
    assert source_numbers("2025 оны 3-р сар") == {2025, 3}
    assert 3 in english_numbers("in March 2025")[0] and 1 in english_numbers("the first quarter")[0]
    for en in ("Set the approved amount to 5 million MNT.", "Set the approved amount to 5,000,000 MNT.",
               "Set the approved amount to five million MNT."):
        assert "number_missing" not in translation_check("Батлагдсан дүнг 5 сая төгрөг болго", en), en
    assert "number_missing" in translation_check("Батлагдсан дүнг 5 сая төгрөг болго", "Set the amount to 5 MNT.")


def test_month_and_quarter_changed():
    src = "2026 оны 3-р сарын үйл ажиллагааны зардал хэд вэ?"
    assert translation_check(src, "What were the operating expenses for March 2026?") == []
    assert "number_missing" in translation_check(src, "What were the operating expenses for April 2026?")
    q = "2025 оны 1-р улиралд хэдэн зээл олгосон бэ?"
    assert translation_check(q, "How many loans were disbursed in the first quarter of 2025?") == []
    assert translation_check(q, "How many loans were disbursed in Q1 2025?") == []
    assert "number_missing" in translation_check(q, "How many loans were disbursed in the second quarter of 2025?")
    # «may» (модаль) нь 5-р сар биш
    assert "number_missing" in translation_check("2026 оны 5-р сард", "This may be in 2026")


def test_number_added_and_placeholder():
    f = translation_check("Энэ сард хэдэн зээл олгосон бэ?", "How many loans were disbursed in [V1] 2024?")
    assert "placeholder_left" in f and "number_added" in f
    # [V1]-ийн цифр тоо биш
    assert "number_added" not in translation_check("Данснаас хассан зээл", "Loans removed from the [V1] dataset")


def test_cyrillic_leftovers_and_quoted_values():
    assert "cyrillic_left" in translation_check("Хурдан зээлийн тоо", "How many хурдан loans?")
    # хуурамч маск (R6 1b): туслах үг хашилтад
    src = "2025 оны 12-р сарын эцсийн байдлаар хэдэн зээл байсан бэ?"
    assert "cyrillic_quoted" in translation_check(src, 'How many loans were there at the end of December 2025 at "байсан"?',
                                                  keys=KEYS)
    assert "cyrillic_quoted" in translation_check(src, 'How many loans at the end of December 2025 at "байсан"?')
    # эх асуултад ишлэлтэй бичигдсэн утга, DB-ийн оноосон нэр — хэвээр үлдэх ёстой
    assert translation_check("«Маш ноцтой» түвшний сэрэмжлүүлэг хэд вэ?",
                             'How many alerts have the "Маш ноцтой" level?') == []
    assert translation_check("Эрдэнэт салбараас олгосон зээл", 'Loans disbursed by the "Эрдэнэт салбар" branch',
                             keys=KEYS) == []
    # өгүүлбэрийн эхний том үсэгтэй үг DB-д «Сар» гэж таарсан ч оноосон нэр биш
    src2 = "2026 онд сар бүр хэдэн харилцагч нэмэгдсэн бэ? Сарын дугаар, тоог гарга."
    assert "cyrillic_quoted" in translation_check(src2, 'How many customers each month in 2026? The "Сарын" number.',
                                                  keys=KEYS)


def test_value_lost():
    src = "LA-2026-001297 дугаартай хүсэлтийн төлөв"
    assert translation_check(src, 'Status of application "LA-2026-001297"') == []
    assert "value_lost" in translation_check(src, "Status of the application")
    assert "value_lost" in translation_check("UB01 салбар", 'The branch ("UB01")')    # unmask төгсгөлд нэмсэн
    assert "value_lost" in translation_check("Эрдэнэт салбар", "the branch", values=["Эрдэнэт салбар"])


def test_bilingual_text_skipped():
    assert translation_check("байсан", "байсан\n(English translation: at \"байсан\")") == []


def test_option_default_off_and_fingerprints():
    assert RunOptions().translate_check == "off"
    assert "translate_check" not in RunOptions().fingerprint()
    exp = load_experiments()
    m1a, m1tc = resolve_run("M1a", exp), resolve_run("M1tc", exp)
    assert m1tc.translate_check == "bilingual" and m1tc.translate_mode == "en_prompt"
    h = lambda o: hashlib.sha1(json.dumps(o.fingerprint(), sort_keys=True, default=str).encode()).hexdigest()  # noqa: E731
    assert h(m1a) != h(m1tc)
    fps = {}
    for rid in exp["runs"]:
        try:
            fps.setdefault(h(resolve_run(rid, exp)), []).append(rid)
        except Exception:   # ${vars}-аас хамаарах run
            continue
    assert fps[h(m1tc)] == ["M1tc"]


def _fake_pipe(mode: str, check: str, en: str):
    from mn_text2sql.pipeline.orchestrator import Pipeline
    fake = SimpleNamespace(opts=RunOptions(translate=True, translate_mode=mode, translate_check=check),
                           translations={"X1:mn": en}, retriever=None)
    fake.translation_flags = lambda q, e: Pipeline.translation_flags(fake, q, e)
    return lambda q: Pipeline.question_forms(fake, q, "mn", "X1")


def test_question_forms_fallback_only_when_flagged():
    src = "2025 оны 12-р сарын эцэст хэдэн зээл байсан бэ?"
    bad, good = 'How many loans at the end of December 2025 at "байсан"?', "How many loans at the end of December 2025?"
    assert _fake_pipe("en_prompt", "off", bad)(src) == (src, bad)               # анхдагч — өөрчлөлтгүй
    assert _fake_pipe("en_prompt", "bilingual", good)(src) == (src, good)       # тэмдэглээгүй — en_prompt хэвээр
    assert _fake_pipe("en_prompt", "bilingual", bad)(src) == (src, f"{src}\n(English translation: {bad})")
    assert _fake_pipe("replace", "bilingual", bad)(src) == (src, f"{src}\n(English translation: {bad})")


def test_flow_registers_translate_check():
    from mn_text2sql.service.flow import BY_TYPE
    f = BY_TYPE["translate"]["fields"]["translate_check"]
    assert f["kind"] == "enum" and "bilingual" in f["options"] and "off" in f["options"]
