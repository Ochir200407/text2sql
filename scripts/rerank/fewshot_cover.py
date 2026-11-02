"""M1fs (R5 З1) офлайн шалгалт: few-shot жишээний gold-хүснэгтийн «cover»-ийг сонголтын хувилбараар харьцуулна. CPU, LLM/DB/
embedding-гүй (BM25 only), ЗӨВХӨН dev (test-ийн gold-ыг хэзээ ч уншихгүй).

    PYTHONIOENCODING=utf-8 python scripts/rerank/fewshot_cover.py                        # H4-ийн linked, build/index (R5 §2.6)
    PYTHONIOENCODING=utf-8 python scripts/rerank/fewshot_cover.py --values dev            # R5-ийн симуляцитай яг ижил утгын маск
    PYTHONIOENCODING=utf-8 python scripts/rerank/fewshot_cover.py --index build/index_plus_dev --exclude-family

Хувилбар бүр нь pipeline-ийн ExampleIndex.search-ийг (retrieval/indexes.py) шууд дууддаг — M1fs-ийн бодит кодын замаар:
  logged       — run-д бүртгэгдсэн жишээ (hybrid BM25 + bge-m3)
  bm25         — table_rerank-гүй (BM25 top-k)
  jaccard      — fewshot_rerank=table_jaccard, торгуульгүй (R5 §2.6 «BM25 top-20 → + хүснэгтийн Jaccard (linked top-3)»)
  jaccard+pen  — M1fs-ийн анхдагч (андуурагддаг хамтрагчийн торгууль retrieval/fewshot_rerank.PARTNER_PENALTY)
  oracle       — gold хүснэгтээр (дээд хязгаар; зөвхөн харьцуулалтад)
Хэмжүүр: cover = gold-ын бүх хүснэгтийг агуулсан жишээ ≥ 1; union = k жишээний нэгдэл gold-ыг агуулна; partner = gold-д
байхгүй андуурагддаг хамтрагч (table_hints.yaml-ийн хос) жишээнд орсон асуултын хувь.
linked_tables нь run-ийн бүртгэлээс (inference-д бэлэн) — gold-оос биш.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from mn_text2sql.retrieval import fewshot_rerank as fr  # noqa: E402
from mn_text2sql.retrieval.indexes import Example, ExampleIndex  # noqa: E402


def read_jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def _quoted(text: str) -> list[str]:
    return re.findall(r"[\"'«“](.+?)[\"'»”]", text)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--run", default="H4", help="results/runs/dev/<run> (бүртгэгдсэн linked_tables, асуулт)")
    ap.add_argument("--index", default="build/index", help="жишээний сан (examples.json)")
    ap.add_argument("--k", type=int, default=4)
    ap.add_argument("--pool", type=int, default=20)
    ap.add_argument("--values", choices=["logged", "dev"], default="logged",
                    help="маскын утга: logged = run-ийн retrieval values (inference-д бэлэн); dev = evalset-ийн тэмдэглэгээ (R5)")
    ap.add_argument("--exclude-family", action="store_true", help="асуултын family-г жишээнээс хасах (index_plus_dev)")
    args = ap.parse_args()

    dev = {d["id"]: d for d in read_jsonl(ROOT / "evalset/dev.jsonl")}
    assert all(d.get("split", "dev") == "dev" for d in dev.values()), "зөвхөн dev"
    run_dir = ROOT / "results/runs/dev" / args.run
    run_file = next(p for p in sorted(run_dir.glob("*.jsonl")))
    rows = [r for r in read_jsonl(run_file) if r.get("category") == "answerable" and r["question_id"] in dev]
    assert all(r.get("split", "dev") == "dev" for r in rows), "зөвхөн dev"

    raw = json.loads((ROOT / args.index / "examples.json").read_text(encoding="utf-8"))
    exs = [Example(**{k: e.get(k) for k in ("id", "question_mn", "question_latin", "question_en", "sql")},
                   values=e.get("values") or [], family=e.get("family") or "") for e in raw]
    idx = ExampleIndex(exs, None)
    pos = {e.id: i for i, e in enumerate(exs)}
    etab = [idx.example_tables(i) for i in range(len(exs))]
    pairs = fr.confusable_pairs()

    def no_pen(rr):
        return rr and fr.TableRerank(rr.tables, frozenset(), rr.weight, 0.0)

    stats: dict[str, list[int]] = {}
    n = 0
    for r in rows:
        g = dev[r["question_id"]]
        gold = fr.sql_tables(g["gold_sql"])
        if not gold:
            continue
        ret = r.get("retrieval") or {}
        linked = ret.get("linked_tables") or []
        q = g["question_mn"] if r.get("variant", "mn") == "mn" else r.get("question") or g["question_mn"]
        vals = (g.get("values") or []) if args.values == "dev" else [v[2] for v in ret.get("values") or []]
        vals = list(vals) + _quoted(q)
        fam = {g["family"]} if args.exclude_family and g.get("family") else None

        def search(rr):
            return [pos[e.id] for e in idx.search(q, vals, None, k=args.k, pool=args.pool, exclude_families=fam,
                                                  table_rerank=rr)]
        rr = fr.make_rerank("table_jaccard", linked, pairs)
        variants = {
            "logged": [pos[x] for x in ret.get("examples") or [] if x in pos],
            "bm25": search(None),
            "jaccard": search(no_pen(rr)),
            "jaccard+pen (M1fs)": search(rr),
            "oracle (gold tables)": search(fr.TableRerank(gold)),
        }
        n += 1
        for name, sel in variants.items():
            ts = [etab[i] for i in sel]
            u = frozenset().union(*ts) if ts else frozenset()
            partner = any((a in gold and b not in gold and b in u) or (b in gold and a not in gold and a in u)
                          for a, b in pairs)
            s = stats.setdefault(name, [0, 0, 0])
            s[0] += any(gold <= t for t in ts)
            s[1] += gold <= u
            s[2] += partner

    print(f"dev answerable n={n}; run={args.run}; index={args.index} ({len(exs)}); k={args.k} pool={args.pool} "
          f"values={args.values} exclude_family={args.exclude_family}; penalty={fr.PARTNER_PENALTY}")
    print(f"{'variant':24s} {'cover':>16s} {'union':>8s} {'partner':>8s}")
    for name, (c, u, p) in stats.items():
        print(f"{name:24s} {100*c/n:6.1f}% ({c:3d}/{n}) {100*u/n:7.1f}% {100*p/n:7.1f}%")


if __name__ == "__main__":
    main()
