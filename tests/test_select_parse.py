"""C8 (docs/review/FULL_REVIEW_2026-10-30.md): хуваарилагчийн хариу уншилт — select_parse="strict" (M1ksf).

Анхдагч "legacy" нь M1ks-ийн хуучин зан төлөв (бүх бүхэл тоо), fingerprint өөрчлөгдөхгүй. DB/LLM-гүй (mock)."""

import pytest

from mn_text2sql.llm.client import MockLLM
from mn_text2sql.pipeline import orchestrator as orch
from mn_text2sql.pipeline.options import RunOptions, load_experiments, resolve_run
from mn_text2sql.pipeline.orchestrator import parse_selection


class _G:
    def __init__(self, i):
        self.id, self.en, self.term, self.definition_en, self.sql = f"g{i}", f"term {i}", f"нэр {i}", "", "x"


def _pipe(reply, **opt):
    p = orch.Pipeline.__new__(orch.Pipeline)
    p.opts = RunOptions(glossary=True, knowledge_select=True, **opt)
    p.llm = MockLLM(lambda m: reply)
    return p


@pytest.mark.parametrize("text,want", [
    ("<answer>2, 7</answer>", [2, 7]),
    ("<think>I compare 3 and 11; PAR30 is term 4</think><answer>4</answer>", [4]),
    ("<answer>[3], [5], [3]</answer>", [3, 5]),                    # давхардал
    ("<answer>PAR30, 2</answer>", [2]),                            # нэр доторх тоо
    ("<answer>2, 13, 0, 99</answer>", [2]),                        # хүрээнээс гадуур
    ("<answer>3.5 or 6</answer>", [6]),                            # бутархай
    ("<answer>none</answer>", []),
    ("<answer>None of the 12 terms</answer>", []),
    ("</think><answer></answer>", []),                             # L2: бодлын дараах хоосон <answer> = none
    ("<answer></answer>", None),                                   # L2: бодолгүй хоосон <answer> → fallback
    ("<answer>1</answer> later <answer>5, 6</answer>", [5, 6]),    # сүүлийн <answer>
    ("Term 3 mentions PAR30 and 7 is also relevant.\nAnswer: 2, 8", [2, 8]),   # <answer>-гүй → сүүлийн мөр
    ("<think>maybe 4 and 9 ... (тасарсан)", None),                  # дуусаагүй бодол
    ("I don't know", None),
    ("<answer>2,\n 9</answer>", [2, 9]),
    ("<answer>3.</answer>", [3]),
])
def test_parse_selection(text, want):
    assert parse_selection(text, 12) == want


def test_legacy_default_reads_every_integer():
    """Хуучин алдааг баримтжуулна: legacy нь «PAR30»-ийн 30-ыг хасдаг ч 3-ыг (бодлоос) авдаг."""
    terms = [_G(i) for i in range(1, 13)]
    reply = "Terms 3 and 11 are close; PAR30 → 4"                  # <answer>-гүй, бодлын текст
    legacy, _ = _pipe(reply)._select_glossary("q", terms)
    strict, _ = _pipe(reply, select_parse="strict")._select_glossary("q", terms)
    assert [g.id for g in legacy] == ["g3", "g11", "g4"]
    assert [g.id for g in strict] == ["g3", "g11", "g4"]            # сүүлийн мөр нь бүхэлдээ — ижил
    reply2 = "<answer>PAR30 and 2</answer>"
    assert [g.id for g in _pipe(reply2)._select_glossary("q", terms)[0]] == ["g2"]
    reply3 = "<answer>none (12 terms checked)</answer>"
    assert [g.id for g in _pipe(reply3)._select_glossary("q", terms)[0]] == ["g12"]        # legacy алдаа
    assert _pipe(reply3, select_parse="strict")._select_glossary("q", terms)[0] == []


def test_strict_fallback_and_cap():
    terms = [_G(i) for i in range(1, 13)]
    fb, _ = _pipe("<think>unfinished 5", select_parse="strict")._select_glossary("q", terms)
    assert [g.id for g in fb] == ["g1", "g2", "g3", "g4"]            # glossary_k=4
    many, _ = _pipe("<answer>1,2,3,4,5,6,7,8</answer>", select_parse="strict")._select_glossary("q", terms)
    assert len(many) == 6


def test_fingerprint_unchanged_and_m1ksf():
    exp = load_experiments()
    ks = resolve_run("M1ks", exp)
    assert ks.select_parse == "legacy" and "select_parse" not in ks.fingerprint()
    ksf = resolve_run("M1ksf", exp)
    assert ksf.select_parse == "strict" and ksf.knowledge_select
    assert {k: v for k, v in ksf.fingerprint().items() if k != "select_parse"} == ks.fingerprint()
