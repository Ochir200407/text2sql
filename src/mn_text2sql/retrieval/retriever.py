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
from .indexes import (
    Example,
    ExampleIndex,
    GlossaryIndex,
    SchemaDoc,
    SchemaIndex,
    ValueEntry,
    ValueHit,
    ValueIndex,
    load_json,
)

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
        vocab = [e.value for e in entries]
        for t in self.model.tables.values():
            vocab += [t.mn, *t.syn]
            for c in t.columns.values():
                vocab += [c.mn, *c.syn, *c.values.values()]
        for g in self.glossary_terms:
            vocab += [g.term, *g.aliases, g.definition_mn]
        vocab += [e.question_mn for e in examples]
        self.translit = Transliterator(vocab)
        # F3 (value_mode=learned): лог-оос сурсан (галиг → кирилл) хосууд өндөр жинтэй, n-gram засвартай
        self.translit_learned = None
        from ..text.learned import load_learned
        pairs, rewriter = load_learned(cfg)
        if pairs:
            learned = Transliterator(vocab)
            for lat, cyr, n in pairs:
                learned.add_pair(lat, cyr, weight=1000 + n)
            learned.rewriter = rewriter
            self.translit_learned = learned

    # ------------------------------------------------------------------
    def to_cyrillic(self, question: str) -> str:
        return self.translit.sentence(question) if script_of(question) in ("latin", "mixed") else question

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
                 exclude_families: set[str] | None = None) -> RetrievalResult:
        t0 = time.perf_counter()
        rc = self.cfg["retrieval"]
        q_cyr = self.to_cyrillic(question)
        query = question if q_cyr == question else f"{question} {q_cyr}"
        qvec = self.embedder.encode([query], is_query=True)[0] if self.embedder is not None else None
        res = RetrievalResult(question=question, question_cyr=q_cyr)
        res.tables = self.schema.search(query, qvec, float(rc["hybrid_alpha"]))
        if want_values:
            res.values = self.values.search(question if value_mode == "basic" else question,
                                            per_keyword=int(rc["value_candidates_per_keyword"]),
                                            top_total=int(rc["value_top_total"]), mode=value_mode,
                                            translit=None if value_mode == "basic" else
                                            (self.translit_learned or self.translit) if value_mode == "learned"
                                            else self.translit)
        if want_glossary:
            res.glossary = self.glossary.search(query, qvec, k=glossary_k, alpha=float(rc["hybrid_alpha"]))
        # Нэр томьёо ба утгын тааралтын хүснэгтүүдийг schema linking-д нэмнэ (зааврын 2-р хэсэг: recall ≥95%)
        boosts: dict[str, float] = {}
        for g in self.glossary.search(query, qvec, k=3, alpha=float(rc["hybrid_alpha"])):
            for t in g.tables:
                boosts[t] = max(boosts.get(t, 0.0), 0.8)
        for h in self.values.search(question, per_keyword=5, top_total=8, mode="basic"):
            if h.score >= 0.9:
                boosts[h.table] = max(boosts.get(h.table, 0.0), 0.7)
        for t, b in boosts.items():
            if t in self.model.tables:
                entry = res.tables.setdefault(t, {"score": 0.0, "columns": {}})
                entry["score"] = max(entry["score"], b)
        res.linked_tables = self.link_tables(res.tables, top_tables or int(rc["top_tables"]),
                                             bool(rc["fk_neighbors"]))
        if want_examples and fewshot_k > 0:
            res.examples = self.examples.search(q_cyr if variant == "latin" else question,
                                                [h.value for h in res.values] + _quoted(question), qvec,
                                                k=fewshot_k, preliminary_sql=preliminary_sql,
                                                exclude_families=exclude_families)
        res.ms = (time.perf_counter() - t0) * 1000
        return res


def _quoted(text: str) -> list[str]:
    return [t for t in tokens(text) if t[:1].isupper() and len(t) > 3]


@lru_cache(maxsize=2)
def _cached(cfg_key: str, cfg_json: str) -> Retriever:
    import json

    from ..config import _to_config
    return Retriever(_to_config(json.loads(cfg_json)))


def get_retriever(cfg: Config) -> Retriever:
    import json
    return _cached(str(cfg["knowledge"]["index_dir"]), json.dumps(cfg, sort_keys=True, default=str))
