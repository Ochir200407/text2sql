"""F9: санал зөрөлтөөр сонгох идэвхтэй сургалт (туршилтын төлөвлөгөө, 7-р санаа).

Шошгогүй асуултын сан (shadow горимын лог эсвэл синтетик асуулт) дээр E2 маягийн run (8 хувилбар) ажиллуулсны
дараа: санал хамгийн их зөрсөн n асуулт (F9a) ба санамсаргүй n асуулт (F9b)-ыг шинжээчээр тэмдэглүүлэхээр гаргана.
Тэмдэглэсний дараа:  index-build --extra-examples build/annotations/f9_disagreement.jsonl --out build/index_f9a
                     index-build --extra-examples build/annotations/f9_random.jsonl --out build/index_f9b
                     eval --run F9a,F9b  → "100 тэмдэглэгээ тутмын өсөлт" (F9a − B5, F9b − B5).
Сан нь dev/test асуулт агуулах ёсгүй (runner нь eval-ийн ID-г индексээс хасдаг ч сан өөрөө тусдаа байх ёстой).
"""

from __future__ import annotations

import json
import random
from collections import Counter

from ..config import Config, resolve_path
from .metrics import load_records


def _candidates(r: dict) -> list[dict]:
    hashes, sqls = r.get("cand_hashes") or [], r.get("cand_sqls") or []
    counts = Counter(h for h in hashes if h)
    out, seen = [], set()
    for h, s in zip(hashes, sqls):
        if h and h not in seen:
            seen.add(h)
            out.append({"votes": counts[h], "sql": s})
    return sorted(out, key=lambda x: -x["votes"])[:4]


def select_annotations(cfg: Config, pool_run: str, n: int = 100, seed: int = 0) -> dict:
    recs = [r for r in load_records(resolve_path(pool_run)) if (r.get("n_candidates") or 1) > 1]
    if not recs:
        raise ValueError("Сангийн run олон хувилбартай (E2 маягийн, candidates > 1) байх ёстой")
    from .dataset import load_all_eval_items
    evals = load_all_eval_items(cfg)
    eval_ids = {it.id for it in evals}
    eval_q = {(q or "").strip() for it in evals for q in (it.question_mn, it.question_latin, it.question_en,
                                                          it.question_mixed)}
    leaked = [r["question_id"] for r in recs if r["question_id"] in eval_ids or (r.get("question") or "").strip() in eval_q]
    if leaked:
        raise ValueError(f"Сан dev/test асуулт агуулж байна ({len(leaked)}, жишээ {leaked[:3]}) — F9-ийн сан eval-ээс "
                         "тусдаа (shadow лог эсвэл синтетик) байх ёстой")
    rng = random.Random(seed)
    disagreement = sorted(recs, key=lambda r: (r.get("confidence") if r.get("confidence") is not None else 1.0,
                                               rng.random()))[:n]
    rnd = rng.sample(recs, min(n, len(recs)))
    out_dir = resolve_path("build/annotations")
    out_dir.mkdir(parents=True, exist_ok=True)
    files = {}
    for name, rows in (("f9_disagreement", disagreement), ("f9_random", rnd)):
        path = out_dir / f"{name}.jsonl"
        with open(path, "w", encoding="utf-8") as fh:
            for r in rows:
                fh.write(json.dumps({
                    "id": f"F9-{r['question_id']}", "family": f"F9-{r['question_id']}", "pool": "examples",
                    "question_mn": r.get("question"), "question_latin": "", "question_en": "",
                    "agreement": r.get("confidence"), "candidates": _candidates(r),
                    "gold_sql": "", "values": [], "annotator": "", "notes": ""}, ensure_ascii=False) + "\n")
        files[name] = str(path)
    overlap = len({r["question_id"] for r in disagreement} & {r["question_id"] for r in rnd})
    return {"pool": len(recs), "selected_each": n, "overlap": overlap, "files": files,
            "mean_agreement": {"disagreement": round(sum(r.get("confidence") or 0 for r in disagreement) /
                                                     max(1, len(disagreement)), 3),
                               "random": round(sum(r.get("confidence") or 0 for r in rnd) / max(1, len(rnd)), 3)},
            "next": "gold_sql-ийг шинжээч бөглөнө → check-gold → index-build --extra-examples ... --out build/index_f9a"}
