"""gen_style (чуулгын 2-р үүсгэгч, M1cte/M1dec): анхдагч үед prompt өөрчлөгдөхгүй, cte/decompose үед зөвхөн Instructions-д
мөр нэмэгдэнэ (D2 LoRA-ийн сургалтын формат хэвээр)."""

import hashlib
import json

import pytest

from mn_text2sql.llm.prompts import (ARCTIC_PREFILL, ARCTIC_SYSTEM, GEN_STYLE_HINTS, PromptContext, build_omnisql,
                                     build_omnisql_plain, gen_style_hint)
from mn_text2sql.pipeline.options import RunOptions, load_experiments, resolve_run

SCHEMA = "CREATE TABLE t2s.loans (\n  loan_id INTEGER,\n  amount NUMERIC\n);"


def _ctxs(**kw):
    return [
        PromptContext(question="How many loans?", tables=["t2s.loans"], dialect="postgres_prompt", **kw),
        PromptContext(question="Share of overdue loans by branch?", tables=["t2s.loans"], dialect="postgres_prompt",
                      evidence_lines=["overdue = dpd > 0", "- today is 2026-06-30"], examples=[("q1", "SELECT 1")],
                      pivot=True, **kw),
        PromptContext(question="Q", tables=["t"], dialect="sqlite_native", intent_json='{"a":1}', **kw),
    ]


def _digest(msgs, pre) -> str:
    return hashlib.sha256(json.dumps([msgs, pre], ensure_ascii=False).encode()).hexdigest()[:16]


# gen_style нэмэхээс ӨМНӨХ кодоор (git HEAD eef96a4-ийн llm/prompts.py) ижил контекстоос гаргасан prompt-ын hash
HEAD_DIGESTS = ['1a2ba27d03e52eed', 'a4e43bbc4fbc195a', 'c8ea2f63affc4f76', '3413d9014db3f90b', 'ccd29b43e8aadb02',
                'd694edea4beb2d38']


def test_default_is_byte_identical_to_pre_change_prompt():
    got = [_digest(*b(c, SCHEMA)) for c in _ctxs() for b in (build_omnisql, build_omnisql_plain)]
    assert got == HEAD_DIGESTS


def test_explicit_default_equals_omitted():
    for c0, c1 in zip(_ctxs(), _ctxs(gen_style="default")):
        for b in (build_omnisql, build_omnisql_plain):
            assert b(c0, SCHEMA) == b(c1, SCHEMA)
    assert PromptContext(question="q", tables=[]).gen_style == "default" == RunOptions().gen_style


@pytest.mark.parametrize("style", ["cte", "decompose"])
def test_style_only_appends_to_instructions(style):
    for c0, c1 in zip(_ctxs(), _ctxs(gen_style=style)):
        (m0, p0), (m1, p1) = build_omnisql(c0, SCHEMA), build_omnisql(c1, SCHEMA)
        assert p0 == p1 == ARCTIC_PREFILL and m1[0] == m0[0] == {"role": "system", "content": ARCTIC_SYSTEM}
        u0, u1 = m0[1]["content"], m1[1]["content"]
        head, tail = u0.split("\n\nOutput Format:", 1)
        # Instructions-ийн сүүлийн мөрийн дараа зөвхөн заавар нэмэгдэнэ; бусад бүх хэсэг (схем, асуулт, гаралтын формат) ижил
        assert u1 == head + GEN_STYLE_HINTS[style] + "\n\nOutput Format:" + tail
        assert "NULLIF" in u1 and "WITH step1 AS" in u1
    assert ("one-to-many" in gen_style_hint(style)) == (style == "decompose")


def test_unknown_style_raises():
    with pytest.raises(ValueError):
        build_omnisql(PromptContext(question="q", tables=[], gen_style="ctes"), SCHEMA)


def test_experiments_m1cte():
    exp = load_experiments()
    a, c, d = (resolve_run(r, exp) for r in ("M1a", "M1cte", "M1dec"))
    assert c.gen_style == "cte" and d.gen_style == "decompose" and a.gen_style == "default"
    fa, fc = a.fingerprint(), c.fingerprint()
    # анхдагч gen_style нь хуучин run-уудын hash-д орохгүй (үргэлжлүүлэх боломж хэвээр)
    assert "gen_style" not in fa
    assert {k: v for k, v in fc.items() if k != "gen_style"} == fa and fc["gen_style"] == "cte"
