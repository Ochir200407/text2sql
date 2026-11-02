"""FK-гүүрийн офлайн үнэлгээ (зөвхөн dev, GPU/DB/embedding-гүй).

dev run-ийн retrieval.linked_tables дээр fk_expand-ийг дахин тоглуулж gold хүснэгтийн бүрэн хамралт, дундаж recall,
нэмэгдсэн хүснэгтийн тоог (prompt-ийн хэмжээ) зөв/буруу асуултаар харьцуулна. 1 алхмын хөршийн оноо = BM25-only
схемийн хайлт (амьд run hybrid оноо ашиглана — ойролцоо).

    PYTHONPATH=src python -m mn_text2sql.retrieval.fk_bridge_eval results/runs/dev/H4/arctic-r1-7b__mn__s0.jsonl \
        --correct results/runs/dev/M1a_answerable_correct.txt
"""

from __future__ import annotations

import argparse
import ast
import itertools
import json
from pathlib import Path

from ..config import resolve_path
from ..knowledge.semantic import load_semantic_model
from .fk_bridge import BridgeParams, FKGraph, fk_expand
from .indexes import SchemaDoc, SchemaIndex


def sql_tables(sql: str, known: set[str]) -> set[str]:
    """SQL-ийн хүснэгтүүд (CTE нэр хасна, зөвхөн схемийн хүснэгт)."""
    import sqlglot
    from sqlglot import exp
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except Exception:
        return set()
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    return {t.name.lower() for t in tree.find_all(exp.Table) if t.name and t.name.lower() not in ctes} & known


def _no_test(path: str) -> str:
    p = Path(path).as_posix().lower()
    if "/test/" in f"/{p}" or p.endswith("test.jsonl"):
        raise SystemExit(f"test split хориотой (зөвхөн dev): {path}")
    return path


def load_rows(run: str, evalset: str, known: set[str], correct_file: str | None) -> list[dict]:
    gold = {}
    for line in open(resolve_path(_no_test(evalset)), encoding="utf-8"):
        d = json.loads(line)
        if d.get("category") == "answerable" and d.get("gold_sql") and d.get("split", "dev") == "dev":
            g = sql_tables(d["gold_sql"], known)
            if g:
                gold[d["id"]] = g
    ok_ids = None
    if correct_file:
        ok_ids = {x.strip() for x in open(resolve_path(correct_file), encoding="utf-8") if x.strip()}
    rows = []
    for line in open(resolve_path(_no_test(run)), encoding="utf-8"):
        r = json.loads(line)
        if r.get("split", "dev") != "dev" or r["question_id"] not in gold:
            continue
        ret = r.get("retrieval")
        ret = ast.literal_eval(ret) if isinstance(ret, str) else (ret or {})
        if not ret.get("linked_tables"):
            continue
        right = (r["question_id"] in ok_ids) if ok_ids is not None else bool(r.get("correct"))
        rows.append({"id": r["question_id"], "q": r.get("question") or "", "linked": list(ret["linked_tables"]),
                     "gold": gold[r["question_id"]], "right": right})
    return rows


def bm25_scores(rows: list[dict], index_dir: str) -> None:
    docs = [SchemaDoc(**x) for x in json.loads((resolve_path(index_dir) / "schema_docs.json").read_text("utf-8"))]
    idx = SchemaIndex(docs, None)
    for r in rows:
        r["scores"] = {t: v["score"] for t, v in idx.search(r["q"]).items()}


def evaluate(rows: list[dict], graph: FKGraph, core_k: int, params: BridgeParams | None) -> dict:
    out = {}
    for name, sub in (("all", rows), ("wrong", [r for r in rows if not r["right"]]),
                      ("right", [r for r in rows if r["right"]])):
        n = len(sub) or 1
        full = rec = add = 0.0
        for r in sub:
            tabs, added = (fk_expand(graph, r["linked"], core_k, r.get("scores"), params) if params
                           else (r["linked"], []))
            got = r["gold"] & set(tabs)
            full += got == r["gold"]
            rec += len(got) / len(r["gold"])
            add += len(added)
        out[name] = {"n": len(sub), "full": round(full / n, 4), "recall": round(rec / n, 4), "added": round(add / n, 3)}
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("runs", nargs="+")
    ap.add_argument("--evalset", default="evalset/dev.jsonl")
    ap.add_argument("--correct", default=None, help="зөв хариулсан ID-ийн жагсаалт (жнь M1a_answerable_correct.txt)")
    ap.add_argument("--index", default="build/index")
    ap.add_argument("--core-k", type=int, default=6, help="run-ийн top_tables")
    ap.add_argument("--grid", action="store_true", help="параметрийн тор (dev дээр тааруулах)")
    a = ap.parse_args(argv)
    model = load_semantic_model("knowledge/semantic_model.yaml")
    graph = FKGraph.from_model(model)
    grid = [BridgeParams()]
    if a.grid:
        grid = [BridgeParams(bridge_top=bt, max_path=mp, hop_top=ht, hop_k=1, hop_min_score=hs, max_add=ma)
                for bt, mp, ht, hs, ma in itertools.product((2, 3, 4, 6), (2, 3), (0, 1, 2), (0.0, 0.3, 0.5),
                                                            (1, 2, 3))
                if not (ht == 0 and hs > 0)]
    for run in a.runs:
        rows = load_rows(run, a.evalset, set(model.tables), a.correct)
        bm25_scores(rows, a.index)
        print(f"## {run}  (dev, {len(rows)} хариулах боломжтой асуулт)")
        print("before", json.dumps(evaluate(rows, graph, a.core_k, None)))
        for p in grid:
            print(p, json.dumps(evaluate(rows, graph, a.core_k, p)))


if __name__ == "__main__":
    main()
