"""Retriever: дөрвөн индексийг ачаалж асуулт бүрд схем, утга, нэр томьёо, жишээг буцаана."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from functools import lru_cache

import numpy as np

from ..config import Config
from ..knowledge.semantic import load_glossary, load_semantic_model
from ..text.normalize import Transliterator, script_of, tokens
from .build import index_dir
from .embed import get_embedder
from .fk_bridge import FKGraph, fk_expand
from .fewshot_rerank import make_rerank
from .indexes import (
    MASK_MODES,
    STRONG_KINDS,
    Example,
    ExampleIndex,
    GlossaryIndex,
    SchemaDoc,
    SchemaIndex,
    ValueEntry,
    ValueHit,
    ValueIndex,
    load_json,
    mask_question,
    mask_values,
)

TRANSLIT_VOCABS = ("pool", "no_eval")   # RunOptions.translit_vocab (L6): "no_eval" = eval-ийн асуултыг үгийн сангаас хасна

log = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    question: str
    question_cyr: str
    tables: dict = field(default_factory=dict)            # {table: {"score", "columns": {col: score}}}
    linked_tables: list[str] = field(default_factory=list)
    values: list[ValueHit] = field(default_factory=list)
    glossary: list = field(default_factory=list)
    examples: list[Example] = field(default_factory=list)
    ms: float = 0.0


class Retriever:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        rc = cfg["retrieval"]
        d = index_dir(cfg)
        self.model = load_semantic_model(cfg["knowledge"]["semantic_model"])
        self.glossary_terms = load_glossary(cfg["knowledge"]["glossary"])
        meta = load_json(d / "meta.json") if (d / "meta.json").exists() else {}
        if not meta:
            raise FileNotFoundError(f"Индекс олдсонгүй ({d}). Эхлээд: python -m mn_text2sql index-build")
        self.col_examples = load_json(d / "column_examples.json")
        docs = [SchemaDoc(**x) for x in load_json(d / "schema_docs.json")]
        entries = [ValueEntry(**x) for x in load_json(d / "values.json")]
        examples = [Example(**x) for x in load_json(d / "examples.json")]
        self.index_dir = d
        self.index_meta = {k: meta.get(k) for k in ("created", "embedding_model", "values", "examples", "schema_docs")}
        self.embedder = None
        emb_s = emb_g = emb_e = None
        if meta.get("embedding_model"):
            self.embedder = get_embedder(meta["embedding_model"], rc["embedding_device"],
                                         int(rc["embedding_batch_size"]))
            if self.embedder is None and os.environ.get("T2S_ALLOW_BM25_FALLBACK") != "1":
                raise RuntimeError(f"Индекс embedding-тэй ({meta['embedding_model']}) боловч embedding загвар "
                                   "ачаалагдсангүй. Чимээгүй BM25 руу унахгүйн тулд зогслоо: sentence-transformers "
                                   "суулгах, T2S_EMBED_DEVICE=cpu, эсвэл зориуд бол T2S_ALLOW_BM25_FALLBACK=1.")
            if self.embedder is not None:
                emb_s = np.load(d / "schema_emb.npy")
                emb_g = np.load(d / "glossary_emb.npy")
                if (d / "examples_emb.npy").exists():
                    emb_e = np.load(d / "examples_emb.npy")
        self.schema = SchemaIndex(docs, emb_s)
        self.values = ValueIndex(entries)
        if emb_g is not None and len(emb_g) != len(self.glossary_terms):
            raise RuntimeError(f"{d}/glossary_emb.npy-ийн мөр ({len(emb_g)}) glossary-ийн нэр томьёотой "
                               f"({len(self.glossary_terms)}, {cfg['knowledge']['glossary']}) таарахгүй — индексийг "
                               "тэр glossary-гоор бүтээ (index-build --glossary ... --out ...) эсвэл RunOptions.glossary_file")
        self.glossary = GlossaryIndex(self.glossary_terms, emb_g)
        self.examples = ExampleIndex(examples, emb_e)
        vocab = translit_vocabulary(entries, self.model, self.glossary_terms, examples)
        self.translit = Transliterator(vocab)
        # F3 (value_mode=learned): лог-оос сурсан (галиг → кирилл) хосууд өндөр жинтэй, n-gram засвартай
        self.translit_learned = self._learned(vocab)

    def _learned(self, vocab: list[str]) -> Transliterator | None:
        from ..text.learned import load_learned
        pairs, rewriter = load_learned(self.cfg)
        if not pairs:
            return None
        learned = Transliterator(vocab)
        for lat, cyr, n in pairs:
            learned.add_pair(lat, cyr, weight=1000 + n)
        learned.rewriter = rewriter
        return learned

    def translits(self, vocab_mode: str = "pool") -> tuple[Transliterator, Transliterator | None]:
        """(галиг хувиргагч, сурсан хувиргагч). L6 (translit_vocab="no_eval"): үгийн сангаас eval-ийн (dev) асуултыг —
        index_plus_dev г.м. жишээний санд байгаа — хасна (dev-ийн латин асуулт өөрийн кирилл хэлбэрээр хөрвүүлэгдэхгүй).
        "pool" (анхдагч) = хуучин үгийн сан, өөрчлөлтгүй."""
        if vocab_mode == "pool":
            return self.translit, self.translit_learned
        if vocab_mode not in TRANSLIT_VOCABS:
            raise ValueError(f"translit_vocab: үл мэдэгдэх горим {vocab_mode!r} (боломжит: {TRANSLIT_VOCABS})")
        if getattr(self, "_translit_no_eval", None) is None:
            from .build import _eval_ids
            ids = _eval_ids(self.cfg, ("dev_file",))   # жишээний санд test хэзээ ч ордоггүй (build.load_example_pool)
            vocab = translit_vocabulary(self.values.entries, self.model, self.glossary_terms,
                                        [e for e in self.examples.examples if e.id not in ids])
            self._translit_no_eval = (Transliterator(vocab), self._learned(vocab))
        return self._translit_no_eval

    def entity_keys(self) -> dict[str, str]:
        """DB-ийн оноосон нэрийн баганын утга {cyr_key: утга} — утгын индексээс (DB-гүй; translate_fix.entity_keys-тэй
        ижил баганууд). mask_mode="values"-д."""
        if getattr(self, "_entity_keys", None) is None:
            from ..pipeline.translate import ENTITY_COLUMNS
            from ..text.normalize import cyr_key
            keys: dict[str, str] = {}
            for e in self.values.entries:
                if e.column in ENTITY_COLUMNS and e.value:
                    keys.setdefault(cyr_key(e.value), e.value)
            self._entity_keys = keys
        return self._entity_keys

    # ------------------------------------------------------------------
    def to_cyrillic(self, question: str, translit: Transliterator | None = None) -> str:
        return (translit or self.translit).sentence(question) if script_of(question) in ("latin", "mixed") else question

    def link_tables(self, scored: dict, top_k: int, fk_neighbors: bool = True) -> list[str]:
        ranked = sorted(scored.items(), key=lambda kv: -kv[1]["score"])
        picked = [t for t, _ in ranked[:top_k]]
        if fk_neighbors:
            extra = []
            for t in picked:
                for n in sorted(self.model.neighbors(t)):
                    if n not in picked and n not in extra and n in ("branches", "products", "customers", "loans",
                                                                    "employees"):
                        extra.append(n)
            picked += extra[: max(2, top_k // 2)]
        return picked

    def retrieve(self, question: str, variant: str = "mn", *, value_mode: str = "basic", fewshot_k: int = 4,
                 glossary_k: int = 4, top_tables: int | None = None, preliminary_sql: str | None = None,
                 want_values: bool = True, want_glossary: bool = True, want_examples: bool = True,
                 exclude_families: set[str] | None = None, fk_bridge: bool = False,
                 fewshot_rerank: str | None = None, value_match: str = "legacy", mask_mode: str = "legacy",
                 translit_vocab: str = "pool") -> RetrievalResult:
        """value_match / mask_mode / translit_vocab (M1vf: F1 / F3 / L6) — анхдагч утгаараа хуучин зан төлөв яг хэвээр."""
        t0 = time.perf_counter()
        rc = self.cfg["retrieval"]
        if mask_mode not in MASK_MODES:
            raise ValueError(f"mask_mode: үл мэдэгдэх горим {mask_mode!r} (боломжит: {MASK_MODES})")
        tl, tl_learned = self.translits(translit_vocab)
        q_cyr = self.to_cyrillic(question, tl)
        query = question if q_cyr == question else f"{question} {q_cyr}"
        qvec = self.embedder.encode([query], is_query=True)[0] if self.embedder is not None else None
        res = RetrievalResult(question=question, question_cyr=q_cyr)
        res.tables = self.schema.search(query, qvec, float(rc["hybrid_alpha"]))
        vm = {"match": value_match} if value_match != "legacy" else {}
        if want_values:
            res.values = self.values.search(question if value_mode == "basic" else question,
                                            per_keyword=int(rc["value_candidates_per_keyword"]),
                                            top_total=int(rc["value_top_total"]), mode=value_mode,
                                            translit=None if value_mode == "basic" else
                                            (tl_learned or tl) if value_mode == "learned"
                                            else tl, **vm)
        if want_glossary:
            res.glossary = self.glossary.search(query, qvec, k=glossary_k, alpha=float(rc["hybrid_alpha"]))
        # Нэр томьёо ба утгын тааралтын хүснэгтүүдийг schema linking-д нэмнэ (зааврын 2-р хэсэг: recall ≥95%)
        boosts: dict[str, float] = {}
        for g in self.glossary.search(query, qvec, k=3, alpha=float(rc["hybrid_alpha"])):
            for t in g.tables:
                boosts[t] = max(boosts.get(t, 0.0), 0.8)
        for h in self.values.search(question, per_keyword=5, top_total=8, mode="basic", **vm):
            # strict (F1): зөвхөн яг/дагавар хассан/угтвар тааралт хүснэгтийг өсгөнө (ойролцоо кирилл тааралт биш)
            if h.score >= 0.9 and (not vm or getattr(h, "kind", "") in STRONG_KINDS):
                boosts[h.table] = max(boosts.get(h.table, 0.0), 0.7)
        for t, b in boosts.items():
            if t in self.model.tables:
                entry = res.tables.setdefault(t, {"score": 0.0, "columns": {}})
                entry["score"] = max(entry["score"], b)
        res.linked_tables = self.link_tables(res.tables, top_tables or int(rc["top_tables"]),
                                             bool(rc["fk_neighbors"]))
        if fk_bridge:
            # FK-гүүр: салангид шилдэг хүснэгтүүдийн завсрын ба 1 алхмын хүснэгт (≤2 нэмэлт; retrieval/fk_bridge.py)
            if getattr(self, "fk_graph", None) is None:
                self.fk_graph = FKGraph.from_model(self.model)
            res.linked_tables, _ = fk_expand(self.fk_graph, res.linked_tables, top_tables or int(rc["top_tables"]),
                                             {t: v["score"] for t, v in res.tables.items()})
        if want_examples and fewshot_k > 0:
            q_ex, ex_vec = (q_cyr if variant == "latin" else question), qvec
            if mask_mode == "values":
                # F3: зөвхөн бодит утга/оноосон нэр/regex-ийг маскжуулна; embedding-ийн асуулт ч ижил маскаар (жишээний
                # санг build_indexes мөн маскжуулсан асуултаар encode хийдэг)
                masks = mask_values(q_ex, res.values, self.entity_keys())
                if self.embedder is not None and self.examples.emb is not None:
                    ex_vec = self.embedder.encode([mask_question(q_ex, masks)], is_query=True)[0]
            else:
                masks = [h.value for h in res.values] + _quoted(question)
            res.examples = self.examples.search(q_ex, masks, ex_vec,
                                                k=fewshot_k, preliminary_sql=preliminary_sql,
                                                exclude_families=exclude_families,
                                                # M1fs (R5 З1): linked[:3]-ийн хүснэгтээр дахин эрэмбэлэх (None = хуучин)
                                                **({"table_rerank": make_rerank(fewshot_rerank, res.linked_tables)}
                                                   if fewshot_rerank else {}))
        res.ms = (time.perf_counter() - t0) * 1000
        return res


def _quoted(text: str) -> list[str]:
    return [t for t in tokens(text) if t[:1].isupper() and len(t) > 3]


def translit_vocabulary(entries, model, glossary_terms, examples) -> list[str]:
    """Галиг хувиргагчийн үгийн сан (дараалал нь тэнцсэн жингийн сонголтод нөлөөлнө — хуучин дарааллаар): DB-ийн утга,
    хүснэгт/баганын тайлбар, синоним, enum нэр, нэр томьёо, жишээний асуулт."""
    vocab = [e.value for e in entries]
    for t in model.tables.values():
        vocab += [t.mn, *t.syn]
        for c in t.columns.values():
            vocab += [c.mn, *c.syn, *c.values.values()]
    for g in glossary_terms:
        vocab += [g.term, *g.aliases, g.definition_mn]
    vocab += [e.question_mn for e in examples]
    return vocab


@lru_cache(maxsize=2)
def _cached(cfg_key: str, cfg_json: str) -> Retriever:
    import json

    from ..config import _to_config
    return Retriever(_to_config(json.loads(cfg_json)))


def get_retriever(cfg: Config) -> Retriever:
    import json
    return _cached(str(cfg["knowledge"]["index_dir"]), json.dumps(cfg, sort_keys=True, default=str))
