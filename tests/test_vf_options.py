"""M1vf (docs/review/FULL_REVIEW_2026-10-30.md): хайлтын засварууд шинэ сонголт, анхдагчаар унтраалттай.

F1 value_match="strict" (кирилл утгын тааралт), F3 mask_mode="values" (few-shot-ийн маск), F5 enum_codes="all", L6
translit_vocab="no_eval", C8 хуваарилагчийн түүхий хариу бичлэгт, F2 term_hints(mode="lexicon2"). Анхдагч үед хуучин зан
төлөв (prompt, retrieve-ийн дуудлага, fingerprint ижил). DB, embedding, LLM-гүй (mock)."""

import copy
from dataclasses import fields

import numpy as np
import pytest

from mn_text2sql.knowledge.semantic import cached_model
from mn_text2sql.llm.client import MockLLM
from mn_text2sql.llm.prompts import PromptContext, render_ddl
from mn_text2sql.pipeline import orchestrator as orch
from mn_text2sql.pipeline.options import _LATE_DEFAULTS, RunOptions, load_experiments, resolve_run
from mn_text2sql.pipeline.translate_terms import term_hints
from mn_text2sql.retrieval.indexes import (
    Example,
    ExampleIndex,
    MatchedValueHit,
    ValueEntry,
    ValueHit,
    ValueIndex,
    mask_question,
    mask_values,
    stem_forms,
)
from mn_text2sql.retrieval.retriever import RetrievalResult, Retriever, translit_vocabulary
from mn_text2sql.text.normalize import Transliterator, weak_latin_key

NEW = {"value_match": "legacy", "mask_mode": "legacy", "enum_codes": "first9", "translit_vocab": "pool"}

ENTRIES = [ValueEntry(t, c, v) for t, c, v in [
    ("employees", "last_name", "Баасан"), ("employees", "first_name", "Хулан"), ("employees", "last_name", "Тогоо"),
    ("branches", "province", "Ховд"), ("customers", "district", "Мөрөн"), ("individuals", "occupation", "Тогооч"),
    ("customers", "district", "Хан-Уул"), ("customers", "province", "Дархан-Уул"),
    ("branches", "branch_name", "Дархан салбар"), ("customers", "segment_code", "VIP"),
    ("credit_scores", "risk_grade_name", "Өндөр"), ("loan_applications", "status_name", "Батлагдсан"),
    ("gl_entries", "period_code", "2026-03"),
]]


def _vi():
    return ValueIndex(ENTRIES)


def _vals(hits):
    return {h.value for h in hits}


# ------------------------------------------------------------------ тохиргоо
def test_options_default_off_and_fingerprints_unchanged():
    o = RunOptions()
    for k, v in NEW.items():
        assert getattr(o, k) == v and _LATE_DEFAULTS[k] == v and k not in o.fingerprint()
    exp = load_experiments()
    for rid in exp["runs"]:
        if rid != "M1vf":
            assert not set(NEW) & set(resolve_run(rid, exp).fingerprint()), rid   # өмнөх run-уудын opts_hash хэвээр
    m, a = resolve_run("M1vf", exp), resolve_run("M1a", exp)
    assert (m.value_match, m.mask_mode, m.enum_codes, m.translit_vocab) == ("strict", "values", "all", "pool")
    diff = {f.name for f in fields(RunOptions) if getattr(m, f.name) != getattr(a, f.name)}
    assert diff - {"run_id", "desc", "group", "base"} == {"value_match", "mask_mode", "enum_codes"}
    fps = [resolve_run(r, exp).fingerprint() for r in exp["runs"] if r != "M1vf"]
    assert m.fingerprint() not in fps


# ------------------------------------------------------------------ F1: утгын тааралт
def test_legacy_value_match_unchanged():
    vi = _vi()
    # хуучин зан төлөв (засваргүй): сул латин түлхүүр + хэсэгчилсэн тааралт
    assert [(h.value, h.score) for h in vi.search("байсан")] == [("Баасан", 0.909)]
    assert _vals(vi.search("хувьд")) == {"Ховд"} and _vals(vi.search("өөрөө")) == {"Мөрөн"}
    assert _vals(vi.search("өгөөч")) == {"Тогооч"} and "Дархан-Уул" in _vals(vi.search("Хан-Уул"))
    for q in ("байсан", "Хан-Уулын харилцагч", "Дархан салбарын VIP"):
        a, b = vi.search(q), vi.search(q, match="legacy")
        assert a == b and all(type(h) is ValueHit for h in a)


def test_strict_rejects_lossy_latin_key_matches():
    vi = _vi()
    for q in ("байсан", "хувьд", "өөрөө", "өгөөч", "Хууль", "өнөөдөр", "тоогоор", "CC-2026-000312 дугаартай"):
        assert vi.search(q, match="strict") == [], q
    hits = vi.search("Хан-Уул", match="strict")
    assert _vals(hits) == {"Хан-Уул"} and hits[0].kind == "exact"     # «Дархан-Уул» (угтвар биш) хасагдана


def test_strict_keeps_stem_and_prefix_matches():
    vi = _vi()
    by = {h.value: h for h in vi.search("Ховдын Дархан салбарын VIP-ийн харилцагч Хан-Уулд", match="strict")}
    assert by["Ховд"].kind == "stem" and by["Дархан салбар"].kind == "stem" and by["VIP"].kind == "stem"
    assert by["Хан-Уул"].kind == "stem" and "Дархан-Уул" not in by
    assert all(isinstance(h, MatchedValueHit) for h in by.values())
    assert stem_forms("дарханы салбарын") >= {"дархан салбар", "дарханы салбар"}
    assert "vip" in stem_forms("vip-ийн") and "2026-03" in stem_forms("2026-03-ны")
    with pytest.raises(ValueError):
        vi.search("x", match="fuzzy")


class _Stub:
    def __init__(self, out):
        self.out = out

    def search(self, *a, **kw):
        return copy.deepcopy(self.out)       # retrieve() res.tables-ийг өөрчилдөг


def _retriever(entries=ENTRIES, examples=None, embedder=None, emb=None):
    r = Retriever.__new__(Retriever)
    r.cfg = {"retrieval": {"hybrid_alpha": 0.5, "value_candidates_per_keyword": 10, "value_top_total": 16,
                           "top_tables": 6, "fk_neighbors": False}, "eval": {"dev_file": "evalset/dev.jsonl"}}
    r.model = cached_model("knowledge/semantic_model.yaml")
    r.schema, r.glossary = _Stub({}), _Stub([])
    r.values = ValueIndex(entries)
    r.examples = ExampleIndex(examples or [Example("AX01", "Барьцаа шаарддаг зээл", "", "", "SELECT 1")], emb)
    r.translit, r.translit_learned, r.embedder, r.glossary_terms = Transliterator([]), None, embedder, []
    return r


def test_strict_boost_only_on_strong_hits():
    r = _retriever()
    q = "Батлагдсан хүсэлт байсан уу"
    legacy = r.retrieve(q, fewshot_k=0, want_glossary=False)
    strict = r.retrieve(q, fewshot_k=0, want_glossary=False, value_match="strict")
    assert "employees" in legacy.linked_tables and "Баасан" in _vals(legacy.values)       # «байсан → Баасан»
    assert "employees" not in strict.linked_tables and _vals(strict.values) == {"Батлагдсан"}
    assert "loan_applications" in strict.linked_tables
    # ойролцоо (fuzzy) кирилл тааралт prompt-д үлдэх боловч хүснэгтийг өсгөхгүй
    r2 = _retriever([ValueEntry("individuals", "occupation", "Хуульч")])
    fz = r2.retrieve("Хууль зөрчсөн", fewshot_k=0, want_glossary=False, value_match="strict")
    assert [h.kind for h in fz.values] == ["fuzzy"] and "individuals" not in fz.linked_tables
    assert "individuals" in r2.retrieve("Хууль зөрчсөн", fewshot_k=0, want_glossary=False).linked_tables


# ------------------------------------------------------------------ F3: few-shot-ийн маск
def test_mask_values_skips_first_word_unless_value():
    keys = {"ховд": "Ховд", "дархан салбар": "Дархан салбар"}
    assert mask_values("Барьцаа шаарддаг зээлийн бүтээгдэхүүн", [], keys) == []
    assert mask_values("Ховдод хэдэн харилцагч байна", [], keys) == ["Ховдод"]           # эхний үг = бодит утга
    got = mask_values("LN-2025-000123 зээлийн «Дархан салбар» үлдэгдэл", [], keys)
    assert set(got) == {"LN-2025-000123", "Дархан салбар"}
    hit = ValueHit("loan_applications", "status_name", "Батлагдсан", 1.0, "Батлагдсан")
    assert mask_values("Батлагдсан хүсэлт", [hit], keys) == ["Батлагдсан"]
    # хуучин _quoted() эхний үгийг маскжуулж, re.I-ээр дараагийн «зээлийн»-г ч устгадаг байв
    assert mask_question("Зээлийн тоо, зээлийн дүн", ["Зээлийн"]) == "<утга> тоо, <утга> дүн"


class _Emb:
    def __init__(self):
        self.texts = []

    def encode(self, texts, is_query=False):
        self.texts += list(texts)
        return np.ones((len(texts), 3), dtype=np.float32)


def _spy_examples(r):
    calls = []
    orig = r.examples.search

    def search(question, values, qvec=None, **kw):
        calls.append((question, list(values), qvec))
        return orig(question, values, qvec, **kw)
    r.examples.search = search
    return calls


def test_mask_mode_values_masks_query_identically_for_bm25_and_embedding():
    emb = _Emb()
    r = _retriever(embedder=emb, emb=np.ones((1, 3), dtype=np.float32))
    calls = _spy_examples(r)
    q = "Барьцаа шаарддаг 3 зээл"
    r.retrieve(q, fewshot_k=1, want_glossary=False)
    assert calls[-1][1] == ["Барьцаа"] and emb.texts == [q]          # хуучин: эхний үг маск, embedding маскгүй
    r.retrieve(q, fewshot_k=1, want_glossary=False, mask_mode="values")
    assert calls[-1][1] == [] and emb.texts[-1] == mask_question(q, []) == "Барьцаа шаарддаг <тоо> зээл"
    assert emb.texts[-2] == q                                         # схем/нэр томьёоны хайлтын асуулт хэвээр
    with pytest.raises(ValueError):
        r.retrieve(q, fewshot_k=1, mask_mode="all")


def test_mask_mode_values_without_embeddings_is_bm25_only():
    r = _retriever()
    calls = _spy_examples(r)
    r.retrieve("Ховдод хэдэн харилцагч байна", fewshot_k=1, want_glossary=False, mask_mode="values")
    assert calls[-1][1] == ["Ховдод"] and calls[-1][2] is None       # оноосон нэрийн span (хуучин тааралт «Ховд»-г олдоггүй)
    r.retrieve("Ховдод хэдэн харилцагч байна", fewshot_k=1, want_glossary=False, mask_mode="values",
               value_match="strict")
    assert calls[-1][1] == ["Ховд", "Ховдод"]                         # strict-ийн утгын тааралт (дагавар хассан) + span


# ------------------------------------------------------------------ L6: галиг хувиргагчийн үгийн сан
def test_translit_vocab_no_eval_excludes_dev_questions(tmp_path):
    dev = tmp_path / "dev.jsonl"
    dev.write_text('{"id": "D001"}\n', encoding="utf-8")
    exs = [Example("D001", "шүдэнзний асуулт", "", "", "SELECT 1"), Example("AX01", "давхардлын жишээ", "", "", "SELECT 2")]
    r = _retriever(examples=exs)
    r.cfg["eval"]["dev_file"] = str(dev)
    r._learned = lambda vocab: None
    vocab = translit_vocabulary(r.values.entries, r.model, [], r.examples.examples)
    assert vocab[:len(ENTRIES)] == [e.value for e in ENTRIES] and vocab[-2:] == [e.question_mn for e in exs]
    r.translit = Transliterator(vocab)
    pool, _ = r.translits("pool")
    clean, _ = r.translits("no_eval")
    assert pool is r.translit and r.translits("no_eval")[0] is clean            # кэштэй
    assert weak_latin_key("шүдэнзний") in pool.by_key and weak_latin_key("шүдэнзний") not in clean.by_key
    assert weak_latin_key("давхардлын") in clean.by_key
    with pytest.raises(ValueError):
        r.translits("all")


# ------------------------------------------------------------------ F5: enum-ийн бүх код
def test_enum_codes_all_lists_every_code():
    model = cached_model("knowledge/semantic_model.yaml")
    base = render_ddl(model, ["gl_entries"], {}, {}, True)
    assert render_ddl(model, ["gl_entries"], {}, {}, True, enum_limit=9) == base
    full = render_ddl(model, ["gl_entries"], {}, {}, True, enum_limit=None)
    assert "1110=Касс" in base and "4100=" not in base and "5400=" not in base
    assert all(f"{c}=" in full for c in ("4100", "4200", "4300", "5100", "5200", "5400"))
    assert full.replace("\n", "").count("=") > base.replace("\n", "").count("=")


def _pipe(**opt):
    p = orch.Pipeline.__new__(orch.Pipeline)
    p.opts = RunOptions(dialect="postgres_prompt", **opt)
    p.model = cached_model("knowledge/semantic_model.yaml")
    p.family, p.types, p.col_examples = "omnisql", {}, {}
    p.mspec, p.cfg = {}, {"retrieval": {"schema_token_budget": 100000}, "project": {"as_of_date": "2026-06-30"}}
    return p


def test_schema_text_enum_option():
    ctx = PromptContext(question="q", tables=["gl_entries", "products"], describe=True)
    d, a = _pipe().schema_text(ctx), _pipe(enum_codes="all").schema_text(ctx)
    assert "5400=" not in d and "5400=" in a and "CURRENT_ACC=" in a and "CURRENT_ACC=" not in d
    assert d == _pipe(enum_codes="first9").schema_text(ctx)
    with pytest.raises(ValueError):
        _pipe(enum_codes="top20").schema_text(ctx)


# ------------------------------------------------------------------ orchestrator: дуудлага ба C8
class _Ret:
    def __init__(self):
        self.calls = []
        self.glossary_terms = []

    def retrieve(self, *a, **kw):
        self.calls.append(kw)
        return RetrievalResult(question="q", question_cyr="q", linked_tables=["loans"])


def _orch(**opt):
    o = orch.Pipeline.__new__(orch.Pipeline)
    o.opts = RunOptions(fewshot_k=1, values=True, dialect="postgres_prompt", **opt)
    o.retriever = _Ret()
    o.item_families, o.translations, o.intents = {}, {}, {}
    o.model, o.cfg = cached_model("knowledge/semantic_model.yaml"), {"project": {"as_of_date": "2026-06-30"},
                                                                    "retrieval": {"max_columns_per_table": 14}}
    o._all_tables = lambda: ["loans"]
    o._example_sql = lambda s: s
    return o


def test_build_context_passes_options_only_when_set():
    off = _orch()
    off.build_context("q", "mn", None)
    assert not set(NEW) & set(off.retriever.calls[0])            # анхдагч: retrieve-ийн дуудлага хуучинтай ижил
    on = _orch(value_match="strict", mask_mode="values", translit_vocab="no_eval", enum_codes="all")
    on.build_context("q", "mn", None)
    kw = on.retriever.calls[0]
    assert (kw["value_match"], kw["mask_mode"], kw["translit_vocab"]) == ("strict", "values", "no_eval")
    assert "enum_codes" not in kw                                  # F5 нь зөвхөн DDL-д (schema_text)


class _G:
    def __init__(self, i):
        self.id, self.en, self.term, self.definition_en, self.sql = f"g{i}", f"term {i}", f"нэр {i}", "", "x"


def test_selector_raw_output_recorded_without_changing_choice():
    p = _orch(knowledge_select=True, glossary=True)
    p.llm = MockLLM(lambda m: "<think>3 сонголт</think><answer>2, 7</answer>")
    terms = [_G(i) for i in range(1, 8)]
    picked0, _ = p._select_glossary("q", terms)
    info: dict = {}
    picked1, _ = p._select_glossary("q", terms, info)
    assert [g.id for g in picked0] == [g.id for g in picked1] == ["g2", "g7"]
    assert info["select_raw"] == "2, 7"


# ------------------------------------------------------------------ F2: орчуулагчийн нэр томьёоны зөвлөмж (lexicon2)
def test_lexicon2_fixes_negation_merge_and_digits():
    assert any(h.startswith("барьцаа") for h in term_hints("Барьцаагүй зээл хэд байна?"))      # хуучин алдаа
    assert not any(h.startswith("барьцаа") for h in term_hints("Барьцаагүй зээл хэд байна?", mode="lexicon2"))
    old = term_hints("Хугацаатай хадгаламжийн тоо")
    new = term_hints("Хугацаатай хадгаламжийн тоо", mode="lexicon2")
    assert any("demand deposit" in h and "term deposit" in h for h in old)
    assert any("term deposit" in h for h in new) and not any("demand" in h for h in new)
    assert any("demand" in h for h in term_hints("Хугацаагүй хадгаламжийн үлдэгдэл", mode="lexicon2"))
    assert any("DPD_180_PLUS" in h for h in term_hints("30-аас дээш хоног хэтэрсэн зээл"))
    assert not any("DPD_180_PLUS" in h for h in term_hints("30-аас дээш хоног хэтэрсэн зээл", mode="lexicon2"))
    assert any("DPD_180_PLUS" in h for h in term_hints("180-аас дээш хоног хэтэрсэн зээл", mode="lexicon2"))
    assert any("INACTIVE" in h for h in term_hints("Идэвхгүй харилцагч хэд вэ", mode="lexicon2"))
    assert term_hints("Идэвхтэй зээл") == term_hints("Идэвхтэй зээл", mode="lexicon")
    with pytest.raises(ValueError):
        term_hints("зээл", mode="lexicon3")
