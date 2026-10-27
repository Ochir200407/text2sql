"""Шинээр бичсэн жишээг (knowledge/examples_plus/*.jsonl) eval-тэй давхардлаас шүүж нэг файлд нэгтгэнэ.

    python scripts/dev/dedup_examples.py            # → knowledge/examples_plus.jsonl + товч тайлан
    python scripts/dev/dedup_examples.py --src-dir knowledge/examples_plus2 --out knowledge/examples_plus2.jsonl         --pool knowledge/examples.jsonl knowledge/examples_plus.jsonl --combined knowledge/examples_plus_all.jsonl
        # 2-р шат: өмнөх сангуудтай давхардлыг хасаж, examples_plus + examples_plus2-ийг нэг файлд (I4-ийн индекс)

Шүүлт (training.data.dedup_against_eval): dev/test-ийн аль нэг хувилбартай тэмдэгтийн 3-gram cosine ≥ 0.8
(embedding байвал ≥ 0.9) эсвэл gold SQL-ийн үр дүн eval-ийн gold-той ижил бол хасна. Мөн одоо байгаа жишээний сантай
(knowledge/examples.jsonl) ≥ 0.9 ойролцоог, давхардсан id-г хасна. Хасагдсан мөрийн зөвхөн id-г хэвлэнэ (eval-ийн
агуулгыг хэвлэхгүй).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from mn_text2sql.config import load_config  # noqa: E402
from mn_text2sql.eval.split import _cos, _vec  # noqa: E402
from mn_text2sql.training.data import TrainItem, dedup_against_eval  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src-dir", default="knowledge/examples_plus")
    ap.add_argument("--out", default="knowledge/examples_plus.jsonl")
    ap.add_argument("--pool", nargs="+", default=["knowledge/examples.jsonl"],
                    help="одоо байгаа жишээний сангууд (≥ 0.9 ойролцоо ба ижил id-г хасна)")
    ap.add_argument("--combined", help="pool-ийн examples.jsonl-ээс бусад файл + шинэ жишээг нэгтгэсэн гаралт")
    a = ap.parse_args()
    cfg = load_config()
    src = sorted((ROOT / a.src_dir).glob("*.jsonl"))
    recs = []
    for f in src:
        recs += [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines() if x.strip()]
    pool = [json.loads(x) for f in a.pool for x in (ROOT / f).read_text(encoding="utf-8").splitlines() if x.strip()]
    seen, uniq, dup_ids = {p["id"] for p in pool}, [], []
    for r in recs:
        if r.get("id") in seen or not r.get("gold_sql") or r.get("category") != "answerable":
            dup_ids.append(r.get("id"))
            continue
        seen.add(r["id"])
        uniq.append(r)
    pool_vecs = [_vec(p["question_mn"]) for p in pool]
    near_pool = []
    kept_pool = []
    for r in uniq:
        v = _vec(r["question_mn"])
        best = max((_cos(v, pv) for pv in pool_vecs), default=0.0)
        (near_pool if best >= 0.9 else kept_pool).append(r)
    items = [TrainItem(id=r["id"], source="examples_plus", engine="postgres", gold_sql=r["gold_sql"],
                       question_en=r.get("question_en", ""), question_mn=r["question_mn"],
                       question_latin=r.get("question_latin", "")) for r in kept_pool]
    kept, dropped = dedup_against_eval(cfg, items, threshold=0.8, use_embeddings=False)
    keep_ids = {it.id for it in kept}
    out = [r for r in kept_pool if r["id"] in keep_ids]
    dst = ROOT / a.out
    dst.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out), encoding="utf-8", newline="\n")
    if a.combined:
        prev = [x for f in a.pool if Path(f).name != "examples.jsonl"
                for x in (ROOT / f).read_text(encoding="utf-8").splitlines() if x.strip()]
        (ROOT / a.combined).write_text("".join(x + "\n" for x in prev)
                                       + "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in out),
                                       encoding="utf-8", newline="\n")
    by_dom: dict[str, int] = {}
    for r in out:
        by_dom[r["domain"]] = by_dom.get(r["domain"], 0) + 1
    print(json.dumps({"files": [f.name for f in src], "read": len(recs), "invalid_or_dup_id": dup_ids,
                      "near_existing_pool": [r["id"] for r in near_pool],
                      "dropped_vs_eval": [{"id": d["id"], **({"sim": d["sim"]} if "sim" in d else
                                                             {"reason": d.get("reason")})} for d in dropped],
                      "kept": len(out), "by_domain": by_dom, "out": str(dst)}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
