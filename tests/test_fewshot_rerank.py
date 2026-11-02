"""Хүснэгт мэдэх few-shot сонголт (fewshot_rerank, M1fs; R5 З1): Jaccard + андуурагддаг хамтрагчийн торгууль, анхдагч
унтраалттай үед эрэмбэ/дуудлага өөрчлөгдөхгүй, тохиргооны hash. DB, embedding, LLM-гүй."""

from dataclasses import fields

import pytest

from mn_text2sql.pipeline import orchestrator as orch
from mn_text2sql.pipeline.options import _LATE_DEFAULTS, RunOptions, load_experiments, resolve_run
from mn_text2sql.retrieval import fewshot_rerank as fr
from mn_text2sql.retrieval.indexes import Example, ExampleIndex
from mn_text2sql.retrieval.retriever import RetrievalResult

PAIRS = (("loans", "loan_applications"), ("loan_writeoffs", "loan_payments"), ("loan_schedule", "loan_payments"))


def _ex(i, q, sql):
    return Example(id=f"e{i}", question_mn=q, question_latin="", question_en="", sql=sql)


# Бүгд «зээл» гэсэн ижил асуулттай (BM25 оноо ойролцоо) — эрэмбийг хүснэгт шийднэ
EXS = [
    _ex(0, "зээлийн хүсэлт хэд вэ", "SELECT count(*) FROM t2s.loan_applications"),
    _ex(1, "зээлийн төлбөр хэд вэ", "SELECT sum(amount) FROM t2s.loan_payments WHERE status = 'POSTED'"),
    _ex(2, "зээлийн хуваарь хэд вэ", "SELECT count(*) FROM t2s.loan_schedule"),
    _ex(3, "зээлийн тоо хэд вэ", "SELECT count(*) FROM t2s.loans"),
    _ex(4, "зээлийн салбар хэд вэ",
        "WITH x AS (SELECT * FROM t2s.loans) SELECT b.name, count(*) FROM x JOIN t2s.branches b USING (branch_id) GROUP BY 1"),
]


# ------------------------------------------------------------------ цэвэр функцууд
def test_sql_tables_strips_schema_and_cte():
    assert fr.sql_tables(EXS[4].sql) == {"loans", "branches"}
    assert fr.sql_tables("") == frozenset()
    assert fr.jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3) and fr.jaccard(set(), set()) == 0.0


def test_partners_only_when_partner_not_linked():
    assert fr.partners_to_avoid(["loan_writeoffs", "loans"], PAIRS) == {"loan_payments", "loan_applications"}
    assert fr.partners_to_avoid(["loans", "loan_applications"], PAIRS) == frozenset()     # хоёулаа linked
    assert fr.partners_to_avoid(["t2s.loan_schedule"], PAIRS) == {"loan_payments"}
    assert fr.partners_to_avoid(["customers"], PAIRS) == frozenset()


def test_make_rerank_modes():
    assert fr.make_rerank(None, ["loans"]) is None and fr.make_rerank("table_jaccard", []) is None
    rr = fr.make_rerank("table_jaccard", ["loans", "branches", "customers", "products", "deposits"], PAIRS)
    assert rr.tables == {"loans", "branches", "customers"} and rr.avoid == {"loan_applications"}
    with pytest.raises(ValueError):
        fr.make_rerank("skeleton", ["loans"])


def test_real_yaml_pairs_loaded():
    pairs = fr.confusable_pairs()
    assert ("loans", "loan_applications") in pairs and all(len(p) == 2 for p in pairs)


def test_penalty_lowers_score():
    rr = fr.TableRerank(frozenset({"loans"}), frozenset({"loan_payments"}))
    a = fr.rerank_score(0.5, frozenset({"loans"}), rr)
    b = fr.rerank_score(0.5, frozenset({"loans", "loan_payments"}), rr)
    assert a == pytest.approx(0.5 * 0.5 + 0.5 * 1.0)
    assert b == pytest.approx(0.25 + 0.5 * 0.5 - fr.PARTNER_PENALTY)


# ------------------------------------------------------------------ ExampleIndex.search
def test_search_none_is_unchanged_and_rerank_promotes_table_match():
    idx = ExampleIndex(EXS, None)
    base = [e.id for e in idx.search("зээлийн хуваарь хэд вэ", [], k=5)]
    assert [e.id for e in idx.search("зээлийн хуваарь хэд вэ", [], k=5, table_rerank=None)] == base
    rr = fr.TableRerank(frozenset({"loans", "branches"}))
    got = [e.id for e in idx.search("зээлийн хуваарь хэд вэ", [], k=5, table_rerank=rr)]
    assert got[0] == "e4" and base[0] == "e2"            # хүснэгт таарсан жишээ (Jaccard 1.0) түрүүлнэ
    assert got.index("e3") < got.index("e0") and got.index("e3") < got.index("e1")   # Jaccard 0.5 > 0
    assert set(got) <= {e.id for e in EXS}


def test_search_partner_penalty_demotes_confusable_example():
    idx = ExampleIndex(EXS, None)
    # linked = loan_schedule; loan_payments нь хамтрагч → e1 (loan_payments) e2-ийн дараа, торгуультай
    rr = fr.TableRerank(frozenset({"loan_schedule"}), frozenset({"loan_payments"}), penalty=1.0)
    got = [e.id for e in idx.search("зээлийн төлбөр хэд вэ", [], k=5, table_rerank=rr)]
    assert got[0] == "e2" and got[-1] == "e1"


# ------------------------------------------------------------------ тохиргоо
def test_option_default_and_fingerprint_unchanged():
    assert RunOptions().fewshot_rerank is None and _LATE_DEFAULTS["fewshot_rerank"] is None
    assert "fewshot_rerank" not in RunOptions().fingerprint()
    exp = load_experiments()
    for rid in exp["runs"]:
        o = resolve_run(rid, exp)
        if not o.fewshot_rerank:
            assert "fewshot_rerank" not in o.fingerprint(), rid   # өмнөх run-уудын opts_hash хэвээр
    m, a = resolve_run("M1fs", exp), resolve_run("M1a", exp)
    assert m.fewshot_rerank == "table_jaccard" and m.base == "M1a" and m.fewshot_k > 0
    diff = {f.name for f in fields(RunOptions) if getattr(m, f.name) != getattr(a, f.name)}
    assert diff <= {"run_id", "desc", "group", "base", "fewshot_rerank"}
    assert m.fingerprint() != a.fingerprint()


# ------------------------------------------------------------------ build_context → retrieve kwargs (mock)
class _Model:
    tables = {}
    notes_mn = []


class _Ret:
    def __init__(self):
        self.calls = []

    def retrieve(self, *a, **kw):
        self.calls.append(kw)
        return RetrievalResult(question="q", question_cyr="q", linked_tables=["loans"], examples=[EXS[3]])


def _orch(**opt):
    o = orch.Pipeline.__new__(orch.Pipeline)
    o.opts = RunOptions(fewshot_k=1, dialect="postgres_prompt", **opt)
    o.retriever = _Ret()
    o.item_families, o.translations, o.intents = {}, {}, {}
    o.model, o.cfg = _Model(), {"project": {"as_of_date": "2026-06-30"}, "retrieval": {"max_columns_per_table": 14}}
    o._all_tables = lambda: ["loans"]
    o._example_sql = lambda s: s
    return o


def test_build_context_passes_option_only_when_set():
    off = _orch()
    ctx0, _ = off.build_context("How many loans?", "mn", None)
    assert "fewshot_rerank" not in off.retriever.calls[0]          # анхдагч: retrieve-ийн дуудлага хуучинтай ижил
    on = _orch(fewshot_rerank="table_jaccard")
    ctx1, _ = on.build_context("How many loans?", "mn", None)
    assert on.retriever.calls[0]["fewshot_rerank"] == "table_jaccard"
    assert ctx0 == ctx1                                              # mock ижил жишээ буцаасан тул prompt ижил
