"""RAG-ийн дөрвөн индекс (зааврын 2-р хэсэг): схем, DB утга, бизнес нэр томьёо, баталгаажсан жишээ.

Хайлт = hybrid (char n-gram BM25 + embedding). Утгын индекс: хоёр түлхүүр (кирилл, сул латин),
дараа нь edit distance-ээр дахин эрэмбэлнэ. Жишээ: нэр/утгыг placeholder болгосон асуултаар хайж,
SQL бүтцээр (skeleton) дахин эрэмбэлнэ (DAIL-SQL).
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from rapidfuzz import fuzz

from ..text.normalize import (
    Transliterator, cyr_key, script_of, stem_variants, tokens, weak_latin_keys,
)
from .bm25 import BM25, normalize_scores
from .embed import cosine_top

STOPWORDS = {
    "вэ", "бэ", "уу", "үү", "юу", "хэд", "хэдэн", "ямар", "аль", "нь", "ба", "болон", "эсвэл", "бол", "гэж", "гэсэн",
    "дээр", "доор", "дотор", "тухай", "хүртэл", "хойш", "өмнө", "бүх", "бүр", "тус", "тэр", "энэ", "тэдгээр", "гарга",
    "харуул", "жагсаа", "хэл", "гаргана", "уу", "вэ?", "хэдий", "хэчнээн", "ийн", "ын", "we", "be", "yu", "hed",
    "heden", "yamar", "ali", "ni", "ba", "bolon", "eswel", "bol", "gej", "garga", "haruul", "jagsaa", "hel", "bn",
    "baina", "байна", "байгаа", "baigaa", "the", "of", "in", "and", "how", "many", "what", "which", "is", "are",
    "for", "by", "to", "a", "an", "with", "show", "list", "give", "me",
}
NUMBER = re.compile(r"\b\d[\d.,]*\b")


# ---------------------------------------------------------------------------
# Схемийн индекс
# ---------------------------------------------------------------------------

@dataclass
class SchemaDoc:
    table: str
    column: str | None
    text: str


class SchemaIndex:
    def __init__(self, docs: list[SchemaDoc], emb: np.ndarray | None = None):
        self.docs = docs
        self.bm25 = BM25([d.text for d in docs])
        self.emb = emb

    @staticmethod
    def build_docs(model) -> list[SchemaDoc]:
        docs = []
        for t in model.tables.values():
            docs.append(SchemaDoc(t.name, None, " ".join([t.name.replace("_", " "), t.mn, t.en, *t.syn])))
            for c in t.columns.values():
                parts = [c.name.replace("_", " "), c.mn, c.en, *c.syn, *c.values.values()]
                docs.append(SchemaDoc(t.name, c.name, " ".join(p for p in parts if p)))
        return docs

    def search(self, query: str, qvec: np.ndarray | None = None, alpha: float = 0.5, k: int = 60) -> dict:
        """{table: {"score": float, "columns": {col: score}}}"""
        bm = normalize_scores(dict(self.bm25.top(query, k * 3)))
        em = cosine_top(self.emb, qvec, k * 3) if qvec is not None else {}
        merged: dict[int, float] = {}
        for i in set(bm) | set(em):
            merged[i] = alpha * bm.get(i, 0.0) + (1 - alpha) * em.get(i, 0.0) if em else bm.get(i, 0.0)
        out: dict[str, dict] = {}
        for i, s in sorted(merged.items(), key=lambda kv: -kv[1])[:k]:
            d = self.docs[i]
            entry = out.setdefault(d.table, {"score": 0.0, "columns": {}})
            if d.column is None:
                entry["score"] = max(entry["score"], s)
            else:
                entry["columns"][d.column] = max(entry["columns"].get(d.column, 0.0), s)
                entry["score"] = max(entry["score"], 0.9 * s)
        return out


# ---------------------------------------------------------------------------
# Утгын индекс
# ---------------------------------------------------------------------------

@dataclass
class ValueEntry:
    table: str
    column: str
    value: str
    freq: int = 1


@dataclass
class ValueHit:
    table: str
    column: str
    value: str
    score: float
    keyword: str


# Ижил оноотой утгыг эрэмбэлэхэд гол хүснэгтүүдийг түрүүлүүлнэ
_TABLE_PRIORITY = {"branches": 0, "products": 0, "loans": 1, "customers": 1, "deposits": 2, "accounts": 2,
                   "employees": 3, "loan_applications": 3}


class ValueIndex:
    def __init__(self, entries: list[ValueEntry]):
        self.entries = entries
        self.bm25 = BM25([e.value for e in entries], n_min=2, n_max=3)
        self.keys = [(cyr_key(e.value), weak_latin_keys(e.value)[0]) for e in entries]

    def keywords(self, question: str, mode: str = "basic", translit: Transliterator | None = None) -> list[str]:
        """Асуултаас хайх түлхүүр үгс: дан үг, 2–3 үгийн хэллэг. mode='rule' (F2): дагавар хасах + галиг хувиргалт."""
        toks = [t for t in tokens(question) if len(t) >= 2]
        cands: list[str] = []
        for n in (3, 2, 1):
            for i in range(len(toks) - n + 1):
                gram = toks[i:i + n]
                if n == 1 and (gram[0].lower() in STOPWORDS or len(gram[0]) < 3 or gram[0].isdigit()):
                    continue
                if any(g.lower() in STOPWORDS for g in gram):
                    continue
                cands.append(" ".join(gram))
        if mode in ("rule", "learned"):
            extra = []
            for c in cands:
                parts = c.split()
                stripped = " ".join(parts[:-1] + [stem_variants(parts[-1])[-1]]) if parts else c
                if stripped != c.lower():
                    extra.append(stripped)
                if translit is not None and script_of(c) == "latin":
                    cy = translit.sentence(c)
                    extra.append(cy)
                    cp = cy.split()
                    if cp:
                        extra.append(" ".join(cp[:-1] + [stem_variants(cp[-1])[-1]]))
            cands += extra
        seen, out = set(), []
        for c in cands:
            key = c.lower()
            if key not in seen:
                seen.add(key)
                out.append(c)
        return out

    def search(self, question: str, per_keyword: int = 10, top_total: int = 16, mode: str = "basic",
               translit: Transliterator | None = None, min_score: float = 0.85) -> list[ValueHit]:
        hits: dict[tuple[str, str, str], ValueHit] = {}
        for kw in self.keywords(question, mode, translit):
            kw_c = cyr_key(kw)
            kw_l = weak_latin_keys(kw)
            for i, _ in self.bm25.top(kw, per_keyword):
                e = self.entries[i]
                vc, vl = self.keys[i]
                s_cyr = fuzz.ratio(kw_c, vc) / 100.0
                s_lat = max(fuzz.ratio(k, vl) for k in kw_l) / 100.0
                # хэсэгчилсэн тааралт: түлхүүр нь утгын эхний хэсэг (Дарханы ↔ Дархан салбар) эсвэл утга + богино
                # дагавар. Урт түлхүүр дотор богино утга орсныг (LN-2025-... ↔ '2025-06') тооцохгүй.
                s_part = max((fuzz.partial_ratio(k, vl) for k in kw_l
                              if 0.6 * len(vl) <= len(k) <= len(vl) + 4), default=0) / 100.0 \
                    if len(kw_c) >= 5 else 0.0
                # дагаварт уян: хэсэгчилсэн тааралт зөвхөн урт түлхүүр/утгад, бага жинтэй
                score = max(s_cyr, s_lat, 0.9 * s_part if len(vl) >= 5 and s_part >= 0.95 else 0.0)
                if len(kw_c) <= 4 and s_cyr < 0.95:   # богино үг: зөвхөн ижил бичгээр яг таарвал (хүү ↛ HO)
                    continue
                if score < min_score:
                    continue
                key = (e.table, e.column, e.value)
                if key not in hits or hits[key].score < score:
                    hits[key] = ValueHit(e.table, e.column, e.value, round(score, 3), kw)
        ranked = sorted(hits.values(), key=lambda h: (-h.score, -len(h.value), _TABLE_PRIORITY.get(h.table, 9)))
        per_col: dict[tuple[str, str], int] = {}
        per_val: dict[str, int] = {}
        out = []
        for h in ranked:
            k = (h.table, h.column)
            # нэг багана ≤ 2 утга; ижил утга (USD, MNT ...) олон хүснэгтэд байвал ≤ 3 удаа
            if per_col.get(k, 0) >= 2 or per_val.get(h.value, 0) >= 3:
                continue
            per_col[k] = per_col.get(k, 0) + 1
            per_val[h.value] = per_val.get(h.value, 0) + 1
            out.append(h)
            if len(out) >= top_total:
                break
        return out


# ---------------------------------------------------------------------------
# Нэр томьёо
# ---------------------------------------------------------------------------

class GlossaryIndex:
    def __init__(self, terms: list, emb: np.ndarray | None = None):
        self.terms = terms
        self.by_id = {t.id: t for t in terms}
        self.bm25 = BM25([t.text() for t in terms])
        self.emb = emb

    def search(self, query: str, qvec: np.ndarray | None = None, k: int = 4, alpha: float = 0.5,
               follow_related: bool = True, min_score: float = 0.25) -> list:
        bm = normalize_scores(dict(self.bm25.top(query, 20)))
        em = cosine_top(self.emb, qvec, 20) if qvec is not None else {}
        scores = {}
        for i in set(bm) | set(em):
            scores[i] = alpha * bm.get(i, 0.0) + (1 - alpha) * em.get(i, 0.0) if em else bm.get(i, 0.0)
        picked = []
        for i, s in sorted(scores.items(), key=lambda kv: -kv[1]):
            if s < min_score or len(picked) >= k:
                break
            picked.append(self.terms[i])
        if follow_related:
            for t in list(picked):
                for rid in t.related[:1]:
                    r = self.by_id.get(rid)
                    if r and r not in picked and len(picked) < k + 2:
                        picked.append(r)
        return picked


# ---------------------------------------------------------------------------
# Жишээний сан (DAIL-SQL)
# ---------------------------------------------------------------------------

def mask_question(question: str, values: list[str]) -> str:
    q = question
    for v in sorted(values, key=len, reverse=True):
        if v:
            q = re.sub(re.escape(v), "<утга>", q, flags=re.I)
    return NUMBER.sub("<тоо>", q)


def sql_skeleton(sql: str) -> list[str]:
    """SQL-ийн бүтэц: түлхүүр үг, функц, оператор; нэр, тоо, текстийг хасна."""
    s = re.sub(r"'(?:[^']|'')*'", " _v_ ", sql or "")
    s = re.sub(r"\b\d+(?:\.\d+)?\b", " _n_ ", s)
    toks = re.findall(r"[A-Za-z_]+|[<>=!]+|[(),*]", s.upper())
    kw = {"SELECT", "FROM", "WHERE", "GROUP", "BY", "ORDER", "HAVING", "JOIN", "LEFT", "INNER", "ON", "AND", "OR",
          "NOT", "IN", "EXISTS", "CASE", "WHEN", "THEN", "ELSE", "END", "LIMIT", "WITH", "AS", "DISTINCT", "COUNT",
          "SUM", "AVG", "MIN", "MAX", "ROUND", "FILTER", "OVER", "PARTITION", "ROW_NUMBER", "RANK", "DENSE_RANK",
          "LAG", "LEAD", "DATE_TRUNC", "EXTRACT", "COALESCE", "NULLIF", "BETWEEN", "LIKE", "ILIKE", "IS", "NULL",
          "UNION", "DESC", "ASC", "_V_", "_N_", "INTERVAL", "CURRENT_DATE", "PERCENTILE_CONT", "WITHIN"}
    return [t for t in toks if t in kw or not t.isalpha()]


def skeleton_similarity(a: str, b: str) -> float:
    sa, sb = sql_skeleton(a), sql_skeleton(b)
    if not sa or not sb:
        return 0.0
    from collections import Counter
    ca, cb = Counter(sa), Counter(sb)
    inter = sum((ca & cb).values())
    return 2 * inter / (sum(ca.values()) + sum(cb.values()))


@dataclass
class Example:
    id: str
    question_mn: str
    question_latin: str
    question_en: str
    sql: str
    values: list[str] = field(default_factory=list)
    family: str = ""

    def question(self, variant: str) -> str:
        return {"latin": self.question_latin, "en": self.question_en}.get(variant) or self.question_mn


class ExampleIndex:
    def __init__(self, examples: list[Example], emb: np.ndarray | None = None):
        self.examples = examples
        self.masked = [mask_question(e.question_mn, e.values) for e in examples]
        self.bm25 = BM25(self.masked)
        self.emb = emb

    def search(self, question: str, values: list[str], qvec: np.ndarray | None = None, k: int = 4,
               preliminary_sql: str | None = None, alpha: float = 0.5, exclude_families: set[str] | None = None,
               pool: int = 20) -> list[Example]:
        masked = mask_question(question, values)
        bm = normalize_scores(dict(self.bm25.top(masked, pool)))
        em = cosine_top(self.emb, qvec, pool) if qvec is not None else {}
        sc = {}
        for i in set(bm) | set(em):
            sc[i] = alpha * bm.get(i, 0.0) + (1 - alpha) * em.get(i, 0.0) if em else bm.get(i, 0.0)
        cands = [i for i, _ in sorted(sc.items(), key=lambda kv: -kv[1])[:pool]]
        if exclude_families:
            cands = [i for i in cands if self.examples[i].family not in exclude_families]
        if preliminary_sql:
            cands.sort(key=lambda i: -(0.5 * sc.get(i, 0) + 0.5 * skeleton_similarity(preliminary_sql,
                                                                                       self.examples[i].sql)))
        return [self.examples[i] for i in cands[:k]]


# ---------------------------------------------------------------------------
# Хадгалах / ачаалах
# ---------------------------------------------------------------------------

def save_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=0, default=str), encoding="utf-8")


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def to_dicts(items) -> list[dict]:
    return [asdict(x) for x in items]
