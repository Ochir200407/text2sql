"""Латин → кирилл хөрвүүлэгчийн нарийвчлал (dev дээр, eval текстгүй үгийн сангаар): үгийн нарийвчлал ба chrF++ (кирилл эхтэй харьцуулсан).

Ажиллуулах:  PYTHONPATH=src python scripts/mt/translit_eval.py
Үгийн сан = DB-ийн утга, semantic_model, glossary, жишээний асуулт (dev/test ОРООГҮЙ). `improved=True` = MT-ийн латин оролтын сайжруулалт
(src/mn_text2sql/text/normalize.py::Transliterator).
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mt_metrics import chrf  # noqa: E402
from mn_text2sql.knowledge.semantic import load_glossary, load_semantic_model  # noqa: E402
from mn_text2sql.text.normalize import Transliterator  # noqa: E402


def rd(p: str) -> list[dict]:
    return [json.loads(x) for x in (ROOT / p).read_text(encoding="utf-8").splitlines() if x.strip()]


def vocabulary() -> list[str]:
    m = load_semantic_model()
    glo = load_glossary() + load_glossary("knowledge/glossary_plus.yaml")
    vocab = [v["value"] for v in json.loads((ROOT / "build/index/values.json").read_text(encoding="utf-8"))]
    for t in m.tables.values():
        vocab += [t.mn, *t.syn]
        for c in t.columns.values():
            vocab += [c.mn, *c.syn, *c.values.values()]
    for g in glo:
        vocab += [g.term, *g.aliases, g.definition_mn]
    for f in ("knowledge/examples.jsonl", "knowledge/examples_plus.jsonl", "knowledge/examples_plus2.jsonl"):
        vocab += [e["question_mn"] for e in rd(f)]
    return vocab


def words(s: str) -> list[str]:
    return [w.lower() for w in re.findall(r"[0-9A-Za-zА-Яа-яЁёӨөҮү]+", s)]


def main() -> None:
    vocab = vocabulary()
    dev = rd("evalset/dev.jsonl")
    for name, tr in (("анхдагч", Transliterator(vocab)), ("improved", Transliterator(vocab, improved=True))):
        ok = tot = 0
        cs = []
        for d in dev:
            pred = tr.sentence(d["question_latin"])
            g, p = words(d["question_mn"]), words(pred)
            tot += len(g)
            ok += sum(1 for a, b in zip(g, p) if a == b) if len(g) == len(p) else sum(1 for a in g if a in p)
            cs.append(chrf(pred, d["question_mn"]))
        print(f"{name:<10} үгийн нарийвчлал {ok / tot:5.1%}  chrF++ {sum(cs) / len(cs):5.1f}  (n={len(dev)})")


if __name__ == "__main__":
    main()
