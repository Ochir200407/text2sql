"""Орчуулгын хэмжүүр (sacreBLEU-гүй орчинд): chrF++ ба домэйн хэмжүүр (нэр томьёо, утга, тоо хадгалалт).

chrF++ = тэмдэгтийн 1–6-gram + үгийн 1–2-gram, β = 2 (sacreBLEU-ийн CHRF(word_order=2)-той ижил зарчим; өөрийн
хэрэгжилт тул үнэмлэхүй тоог гаднын тайлантай харьцуулахгүй — зөвхөн өөрсдийн туршилтуудыг хооронд нь).
"""

from __future__ import annotations

import re
from collections import Counter

_PUNCT = re.compile(r"([.,;:!?()\[\]{}\"«»“”])")


def _char_ngrams(s: str, n: int) -> Counter:
    s = re.sub(r"\s+", "", s)
    return Counter(s[i:i + n] for i in range(len(s) - n + 1))


def _word_ngrams(s: str, n: int) -> Counter:
    toks = _PUNCT.sub(r" \1 ", s).split()
    return Counter(tuple(toks[i:i + n]) for i in range(len(toks) - n + 1))


def chrf(hyp: str, ref: str, char_order: int = 6, word_order: int = 2, beta: float = 2.0) -> float:
    """0..100. Хоосон hyp/ref → 0."""
    hyp, ref = hyp or "", ref or ""
    if not hyp.strip() or not ref.strip():
        return 0.0
    precs, recs = [], []
    for n in range(1, char_order + 1):
        h, r = _char_ngrams(hyp, n), _char_ngrams(ref, n)
        if not h or not r:
            continue
        m = sum((h & r).values())
        precs.append(m / sum(h.values()))
        recs.append(m / sum(r.values()))
    for n in range(1, word_order + 1):
        h, r = _word_ngrams(hyp, n), _word_ngrams(ref, n)
        if not h or not r:
            continue
        m = sum((h & r).values())
        precs.append(m / sum(h.values()))
        recs.append(m / sum(r.values()))
    if not precs:
        return 0.0
    p, r = sum(precs) / len(precs), sum(recs) / len(recs)
    if p + r == 0:
        return 0.0
    return 100.0 * (1 + beta ** 2) * p * r / (beta ** 2 * p + r)


def corpus_chrf(hyps: list[str], refs: list[str]) -> float:
    """Өгүүлбэрийн chrF-ийн дундаж (энгийн, тогтвортой)."""
    return sum(chrf(h, r) for h, r in zip(hyps, refs)) / max(1, len(hyps))


def digits_kept(src: str, hyp: str) -> bool:
    """Эх асуултын бүх тоо (2025, 30, 90 ...) орчуулгад байна уу."""
    nums = re.findall(r"\d+(?:[.,]\d+)?", src)
    return all(n in hyp for n in nums)


def values_kept(values: list[str], hyp: str) -> bool:
    """DB-ийн утга (кирилл, дагаваргүй) орчуулгад яг байна уу. Хоосон жагсаалт → True."""
    low = (hyp or "").lower()
    return all(str(v).lower() in low for v in (values or []))
