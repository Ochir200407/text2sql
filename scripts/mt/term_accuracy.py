"""Нэр томьёоны зөв (curated): glossary + lexicon-ийн (56) монгол нэр томьёо эх асуултад байвал англи орчуулгад түүний англи хэлбэр
(en эсвэл en_aliases) байна уу. lexicon-ийн бүх 914 мөрийг (enum, баганын шошго — «дунд», «аас дээш» гэх мэт хэт өргөн) ашигладаг
төлөвлөгөөний анхны хэмжүүрийг орлоно — тэр нь жишиг (хүний бичсэн) орчуулгад ч ~42% л гарсан (docs/MT_INVARIANT_PLAN.md §3).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from mn_text2sql.pipeline.translate_terms import _WORD, _stem  # noqa: E402
from mn_text2sql.text.normalize import cyr_key  # noqa: E402


def _en_forms(t: dict) -> list[str]:
    forms = [t["en"]] + list(t.get("en_aliases") or [])
    return [re.split(r"\s*[(]", f)[0].strip().lower() for f in forms if f]


def load_curated() -> list[tuple[list[tuple[str, ...]], list[str], dict]]:
    """[(монгол хэллэгүүдийн stem-үүд, англи хэлбэрүүд, term)] — урт хэллэг эхэндээ."""
    out = []
    for line in (ROOT / "data/mt/v0/terms.jsonl").read_text(encoding="utf-8").splitlines():
        t = json.loads(line)
        if t.get("quality") != "curated":
            continue
        phrases = []
        for m in [t["mn"]] + list(t.get("mn_aliases") or []):
            toks = _WORD.findall(cyr_key(m))
            if toks and sum(len(w) for w in toks) >= 5:
                phrases.append(tuple(_stem(w) for w in toks))
        if phrases:
            out.append((phrases, _en_forms(t), t))
    return out


def matches(question: str, phrases: list[tuple[str, ...]]) -> bool:
    words = _WORD.findall(cyr_key(question))
    for stems in phrases:
        n = len(stems)
        for i in range(len(words) - n + 1):
            if all(words[i + k].startswith(stems[k]) for k in range(n)):
                return True
    return False


_STOP = {"of", "the", "a", "an", "in", "and", "or", "for", "to", "by", "at", "on"}


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def present(form: str, hyp_words: list[str]) -> bool:
    """англи хэлбэрийн БҮХ агуулгын үг (дараалалгүй, дагавар/тоо нэгийг тэвчиж) орчуулгад байна уу:
    «loan disbursement» ⊂ «disbursements of loans» ✓."""
    toks = [w for w in _words(form) if w not in _STOP]
    if not toks:
        return False
    return all(any(h.startswith(t[:max(3, len(t) - 2)]) for h in hyp_words) for t in toks)


def term_accuracy(pairs: list[tuple[str, str]], curated=None) -> tuple[int, int, list[str]]:
    """pairs = [(монгол эх, англи орчуулга)] → (зөв, нийт, алдсан term id-ууд)."""
    curated = curated or load_curated()
    ok = tot = 0
    missed: list[str] = []
    for src, hyp in pairs:
        hw = _words(hyp)
        for phrases, forms, t in curated:
            if matches(src, phrases):
                tot += 1
                if any(present(f, hw) for f in forms):
                    ok += 1
                else:
                    missed.append(t["id"])
    return ok, tot, missed


if __name__ == "__main__":
    rows = []
    for dom in ("gl_trends", "cross", "applications", "loans", "collection", "customers", "payments", "deposits"):
        p = ROOT / f"data/mt/v1_drafts/{dom}.jsonl"
        if p.exists():
            rows += [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    ok, tot, missed = term_accuracy([(r["mn"], r["en"]) for r in rows])
    print(f"drafts ({len(rows)} мөр): curated нэр томьёо {ok}/{tot} = {ok / max(1, tot):.1%}")
    from collections import Counter
    c = Counter(missed)
    cur = {t["id"]: t for _, _, t in load_curated()}
    for k, v in c.most_common(10):
        print(f"  алдсан {v:>3}  {cur[k]['mn']}  → {cur[k]['en']}")
