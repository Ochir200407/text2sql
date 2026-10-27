"""Char n-gram BM25 (монгол хэлний нөхцөлд үгийн хэсгээр тааруулахад — зааврын 2-р хэсэг)."""

from __future__ import annotations

import math
from collections import Counter, defaultdict

from ..text.normalize import char_ngrams, cyr_key, weak_latin_keys


def doc_terms(text: str, n_min: int = 2, n_max: int = 4, with_latin: bool = True) -> list[str]:
    """Кирилл түлхүүр ба сул латин түлхүүрийн n-gram (хоёр бичигт ижил индекс)."""
    base = cyr_key(text)
    terms = char_ngrams(base, n_min, n_max)
    if with_latin:
        for k in weak_latin_keys(base):
            terms.extend("~" + g for g in char_ngrams(k, n_min, n_max))
    return terms


class BM25:
    def __init__(self, docs: list[str], k1: float = 1.2, b: float = 0.75, n_min: int = 2, n_max: int = 4):
        self.k1, self.b = k1, b
        self.n_min, self.n_max = n_min, n_max
        self.doc_len = []
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for i, d in enumerate(docs):
            tf = Counter(doc_terms(d, n_min, n_max))
            self.doc_len.append(sum(tf.values()))
            for t, c in tf.items():
                self.postings[t].append((i, c))
        self.n = len(docs)
        self.avgdl = (sum(self.doc_len) / self.n) if self.n else 0.0
        self.idf = {t: math.log(1 + (self.n - len(p) + 0.5) / (len(p) + 0.5)) for t, p in self.postings.items()}

    def scores(self, query: str) -> dict[int, float]:
        out: dict[int, float] = defaultdict(float)
        for t, qc in Counter(doc_terms(query, self.n_min, self.n_max)).items():
            posting = self.postings.get(t)
            if not posting:
                continue
            idf = self.idf[t]
            for i, c in posting:
                dl = self.doc_len[i]
                out[i] += idf * c * (self.k1 + 1) / (c + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1)))
        return out

    def top(self, query: str, k: int = 10) -> list[tuple[int, float]]:
        sc = self.scores(query)
        return sorted(sc.items(), key=lambda kv: -kv[1])[:k]


def normalize_scores(scores: dict[int, float]) -> dict[int, float]:
    if not scores:
        return {}
    mx = max(scores.values())
    return {k: (v / mx if mx > 0 else 0.0) for k, v in scores.items()}
