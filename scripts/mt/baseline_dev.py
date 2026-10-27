"""Одоогийн орчуулагчийн (qwen3.5-9b.fix кэш) суурь үзүүлэлт — ЗӨВХӨН dev дээр (test-д хүрэхгүй).

Ажиллуулах:  PYTHONPATH=src python scripts/mt/baseline_dev.py [--cache build/translations/qwen3.5-9b.fix.jsonl]
Гаралт: нэгтгэсэн тоо (асуулт тус бүрийн жагсаалтыг хэвлэхгүй, сургалтын өгөгдөлд ямар ч dev текст оруулахгүй).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mt_metrics import chrf, digits_kept, values_kept  # noqa: E402
from term_accuracy import load_curated, term_accuracy  # noqa: E402
from mn_text2sql.pipeline.translate_terms import load_lexicon, term_hints  # noqa: E402


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default="build/translations/qwen3.5-9b.fix.jsonl")
    ap.add_argument("--split", default="dev", choices=["dev"], help="test зориудаар нээгдээгүй")
    args = ap.parse_args()

    dev = {d["id"]: d for d in read_jsonl(ROOT / "evalset/dev.jsonl")}
    cache = defaultdict(dict)
    for d in read_jsonl(ROOT / args.cache):
        if d["id"] in dev:
            cache[d["id"]][d["variant"]] = d

    lex = load_lexicon()
    print(f"lexicon: {len(lex)} нэр томьёо (semantic_model + glossary + translation_terms)")
    print(f"dev асуулт: {len(dev)}, кэшэд бүх хоёр хувилбартай: {sum(1 for v in cache.values() if len(v) == 2)}")

    rows = []
    for qid, variants in cache.items():
        gold = dev[qid]["question_en"]
        for var, rec in variants.items():
            src = rec["source"]
            hyp = rec["text"]
            hints = term_hints(src)
            hit = 0
            for h in hints:
                en = re.split(r"\s*[(]", h.split(" = ", 1)[1])[0].split(" / ")[0].strip().lower()
                if en and en in hyp.lower():
                    hit += 1
            rows.append({
                "id": qid, "var": var, "chrf": chrf(hyp, gold), "digits": digits_kept(src, hyp),
                "values": values_kept(dev[qid].get("values") or [], hyp), "has_values": bool(dev[qid].get("values")),
                "n_hints": len(hints), "hint_hit": hit, "ok": bool(rec.get("ok", True)),
            })

    def agg(sel, name):
        sel = list(sel)
        if not sel:
            return
        n = len(sel)
        chrf_m = sum(r["chrf"] for r in sel) / n
        bad = sum(1 for r in sel if r["chrf"] < 45) / n
        dig = sum(r["digits"] for r in sel) / n
        hv = [r for r in sel if r["has_values"]]
        val = (sum(r["values"] for r in hv) / len(hv)) if hv else float("nan")
        th = sum(r["n_hints"] for r in sel)
        thh = sum(r["hint_hit"] for r in sel)
        print(f"{name:<14} n={n:<4} chrF++={chrf_m:5.1f}  chrF<45: {bad:5.1%}  тоо хадгалсан: {dig:5.1%}  "
              f"утга хадгалсан: {val:5.1%} (n={len(hv)})  нэр томьёо: {thh}/{th} = {thh / th if th else float('nan'):5.1%}")

    agg(rows, "бүгд")
    agg((r for r in rows if r["var"] == "mn"), "кирилл")
    agg((r for r in rows if r["var"] == "latin"), "латин")
    print("(«нэр томьёо» багана = lexicon-ийн 914 мөр, enum/шошго хэт өргөн тул лавлагаа биш — доорх curated хэмжүүрийг ашигла)")
    # curated нэр томьёоны зөв: эх нь ҮРГЭЛЖ кирилл эх (латин орчуулгын хувьд ч тухайн өгүүлбэрийн кирилл эх), 56 curated term
    cur = load_curated()
    for var in ("mn", "latin"):
        pairs = [(dev[q]["question_mn"], cache[q][var]["text"]) for q in cache if var in cache[q]]
        ok, tot, _ = term_accuracy(pairs, cur)
        print(f"curated нэр томьёо ({'кирилл' if var == 'mn' else 'латин'} орчуулга): {ok}/{tot} = {ok / max(1, tot):.1%}")
    ok, tot, _ = term_accuracy([(dev[q]["question_mn"], dev[q]["question_en"]) for q in cache], cur)
    print(f"curated нэр томьёо (dev-ийн жишиг англи, лавлагаа): {ok}/{tot} = {ok / max(1, tot):.1%}")
    print("placeholder алдсан (ok=False):", sum(1 for r in rows if not r["ok"]))

    # Нэг асуултын кирилл ба латин орчуулга хоорондоо нийцэж байна уу (хоёр бичигтэй ижил утга)
    cons = []
    for qid, v in cache.items():
        if len(v) == 2:
            cons.append(chrf(v["mn"]["text"], v["latin"]["text"]))
    print(f"кирилл ↔ латин орчуулгын харилцан chrF++: {sum(cons) / len(cons):.1f} (n={len(cons)})")
    cov = sum(1 for qid in dev if term_hints(dev[qid]["question_mn"]))
    print(f"dev асуултын {cov}/{len(dev)} ({cov / len(dev):.1%}) нь lexicon-ийн ≥1 нэр томьёо агуулна")


if __name__ == "__main__":
    main()
