"""MT v1: агентын ноорог (data/mt/v1_drafts/*.jsonl) + v0 хос → data/mt/v1/pairs.jsonl (+ MANIFEST.json).

Алхам: (1) ноорог бүрийг eval-guard, давхардлаар дахин шалгана; (2) хоёр дахь агентын хяналт (data/mt/v1_review/<домэйн>.jsonl:
{"id","verdict": ok|fix|bad,"en_fixed"?,"mn_fixed"?}) байвал хэрэглэнэ — bad хасна, fix засна; (3) entity-тэй мөрөөс «»-тэй хувилбар
(`mn_marked`, `en_marked`: DB-ийн каноник утгыг хоёр талд «…»-аар) үүсгэнэ; (4) латин синтетик (3); (5) split; (6) v0-тэй нэгтгэнэ.

Ажиллуулах:  PYTHONPATH=src python scripts/mt/merge_drafts.py [--no-review]
v2:          PYTHONPATH=src python scripts/mt/merge_drafts.py --drafts data/mt/v1_drafts data/mt/v2_drafts --out data/mt/v2
             (v2_drafts: санхүүгийн хэллэгийн асуулт phr_<бүлэг>.jsonl; хэллэгийн толь phr_<бүлэг>_expr.jsonl → <out>/phrases.jsonl,
             render_sft.py нь pairs.jsonl-ийн хавтсанд phrases.jsonl байвал автоматаар уншина; val-д орсон хэллэгийн толийг хасна)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from build_mt_v0 import (  # noqa: E402
    ROOT,
    EvalGuard,
    cos,
    grams,
    noisy_latin,
    norm,
    read_jsonl,
    vnorm,
    write_jsonl,
)

from mn_text2sql.text.normalize import STYLE_UOV, STYLE_VUW, translit  # noqa: E402

DRAFTS = ROOT / "data/mt/v1_drafts"
REVIEW = ROOT / "data/mt/v1_review"
OUT = ROOT / "data/mt/v1"


def markable(e: dict) -> bool:
    """inference-ийн span илрүүлэгч (pipeline/translate.ENTITY_COLUMNS) зөвхөн оноосон нэрийг (салбар, аймаг, дүүрэг, ажилтны нэр ...)
    тэмдэглэдэг; enum/дансны код (HIGH, 4100) тэмдэглэхгүй → сургалтад ч «»-гүй (inference-тэй зөрөхгүй)."""
    from mn_text2sql.pipeline.translate import ENTITY_COLUMNS
    return str(e.get("column", "")).split(".")[-1] in ENTITY_COLUMNS


def mark(p: dict) -> tuple[str | None, str | None]:
    """entity-тэй мөрөөс «каноник утга»-тай хувилбар. Монгол талд дагавартай хэлбэрийг (Дархан салбарын) каноникоор (Дархан салбар)
    сольж дагаврыг хасна — inference-ийн span илрүүлэгчтэй ижил."""
    ents = [e for e in (p.get("entities") or []) if markable(e)]
    if not ents:
        return None, None
    mn_m, en_m = p["mn"], p["en"]
    for e in ents:
        if e["mn"] not in mn_m:
            return None, None
        mn_m = mn_m.replace(e["mn"], f"«{e['value']}»", 1)
        en_m, n = re.subn(re.escape(e["en"]), f"«{e['value']}»", en_m, count=1, flags=re.I)
        if n == 0:
            return None, None
    return mn_m, en_m


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-review", action="store_true")
    ap.add_argument("--seed", type=int, default=5)
    ap.add_argument("--drafts", nargs="+", default=[str(DRAFTS)])
    ap.add_argument("--out", default=str(OUT))
    args = ap.parse_args()
    out_dir = Path(args.out) if Path(args.out).is_absolute() else ROOT / args.out
    rng = random.Random(args.seed)
    guard = EvalGuard()
    v0 = read_jsonl(ROOT / "data/mt/v0/pairs.jsonl")
    for p in v0:
        p.setdefault("source", "v0")
    stats: Counter = Counter()

    review: dict[str, dict] = {}
    if not args.no_review and REVIEW.exists():
        for f in REVIEW.glob("*.jsonl"):
            for r in read_jsonl(f):
                review[r["id"]] = r

    drafts = []
    files = []
    for dd in args.drafts:
        dd = Path(dd) if Path(dd).is_absolute() else ROOT / dd
        files += [f for f in sorted(dd.glob("*.jsonl")) if not f.stem.endswith("_expr")]
    for f in files:
        for d in read_jsonl(f):
            stats["draft_rows"] += 1
            rv = review.get(d["id"])
            if rv and rv.get("verdict") == "bad":
                stats["review_bad_dropped"] += 1
                continue
            if rv and rv.get("verdict") == "fix":
                d["en"] = rv.get("en_fixed") or d["en"]
                d["mn"] = rv.get("mn_fixed") or d["mn"]
                if "entities_fixed" in rv:
                    d["entities"] = rv["entities_fixed"]
                stats["review_fixed"] += 1
            if (guard.hit("mn", d["mn"]) or guard.hit("en", d["en"])
                    or any(h["ratio"] >= 0.5 for h in guard.substring_hits({d["id"]: [d["mn"], d["en"]]}))):
                stats["eval_overlap_dropped"] += 1
                continue
            drafts.append(d)

    # давхардал (v0 болон ноорог хоорондоо): mn-ийн 3-gram cosine ≥ 0.9
    seen = [(grams(p["mn"]), vnorm(grams(p["mn"]))) for p in v0]
    rows = []
    for d in drafts:
        g = grams(d["mn"])
        ng = vnorm(g)
        # D5 минимал хос (pair): нэг үгээр ялгаатай нь зорилго — ойролцоо давхардлаар хасахгүй (яг давхардлыг л)
        if d.get("pair") and not any(norm(d["mn"]) == norm(x["mn"]) for x in rows):
            pass
        elif any(cos(g, g0, ng, n0) >= 0.9 for g0, n0 in seen):
            stats["near_duplicate_dropped"] += 1
            continue
        seen.append((g, ng))
        mn_m, en_m = mark(d)
        is_mixed = d.get("register") == "R4"
        synth = [noisy_latin(d["mn"], rng) for _ in range(2)]
        for style in (STYLE_VUW, STYLE_UOV):
            s = translit(d["mn"], style)
            if s not in synth:
                synth.append(s)
        # хэллэгийн асуулт: хэллэгээр (expr) нь хуваана — нэг хэллэгийн асуултууд train/val-д хуваагдахгүй
        # D5: минимал хосоор (pair) хуваана — нэг хосын мөрүүд train/val-д хуваагдахгүй
        val = int(hashlib.sha1((d.get("expr") or d.get("pair") or d["id"]).encode()).hexdigest(), 16) % 10 == 0
        rows.append({"id": d["id"], "kind": "question", "domain": d["domain"], "family": d["id"], "register": d["register"],
                     "mn": d["mn"], "latin": synth[0], "mixed": d["mn"] if is_mixed else None, "en": d["en"],
                     "values": [e["value"] for e in d.get("entities") or []], "entities": d.get("entities") or [],
                     "mn_marked": mn_m, "en_marked": en_m, "latin_synth": synth[1:4], "terms": d.get("terms") or [],
                     "expr": d.get("expr"), "pair": d.get("pair"), "trap": d.get("trap"),
                     "source": "phrase_draft" if d.get("expr") else ("d5_draft" if d.get("pair") else "v1_draft"), "quality": "agent_llm" + ("_reviewed" if d["id"] in review else ""),
                     "split": "val" if val else "train"})
        stats[f"domain:{d['domain']}"] += 1
        stats[f"register:{d['register']}"] += 1
        stats["marked"] += bool(mn_m)
    out = v0 + rows
    OUT_DIR = out_dir
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    sha = write_jsonl(OUT_DIR / "pairs.jsonl", out)
    # хэллэгийн толь (v2): eval-guard-аар шүүгээд нэг файлд; render_sft --phrases богино «хэллэг → англи» хос болгоно
    exprs = []
    for dd in args.drafts:
        dd = Path(dd) if Path(dd).is_absolute() else ROOT / dd
        for f in sorted(dd.glob("*_expr.jsonl")):
            for e in read_jsonl(f):
                if guard.hit("mn", e["mn"]) or guard.hit("en", e["en"]):
                    stats["expr_eval_overlap_dropped"] += 1
                    continue
                exprs.append(e)
    val_expr = {r["expr"] for r in rows if r.get("expr") and r["split"] == "val"}
    exprs = [e for e in exprs if e["id"] not in val_expr]     # val асуултын хэллэгийг толь хэлбэрээр ч сургахгүй
    (OUT_DIR / "phrases.jsonl").unlink(missing_ok=True)
    if exprs:
        seen_e: dict[str, dict] = {}
        for e in exprs:
            seen_e.setdefault(e["mn"].strip().lower(), e)
        write_jsonl(OUT_DIR / "phrases.jsonl", list(seen_e.values()))
        stats["phrases"] = len(seen_e)
    stats["v0"] = len(v0)
    stats["v1_added"] = len(rows)
    stats["total"] = len(out)
    stats["val"] = sum(1 for p in out if p["split"] == "val")
    manifest = {"created": datetime.now().isoformat(timespec="seconds"), "seed": args.seed, "stats": dict(stats),
                "sha256": {"pairs.jsonl": sha}, "review_files": len(review),
                "note": "val = hash(id|family) % 10 == 0. «»-тэй хувилбар зөвхөн entity-тэй ноорогт."}
    (OUT_DIR / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest["stats"], ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
