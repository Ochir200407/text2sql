"""T-2 (схем-солилт) хэмжилт: ижил асуулт, ижил compact prompt — эх схем (orig) ба сургалтад ороогүй 4 нэршил (make_shift_eval.py)
дээрх EX. Загвар бүрийн «эх → шилжүүлсэн» уналт = DB-ийн нэрийг цээжилсэн хэмжээ. docs/MT_INVARIANT_PLAN.md §6.4 T-2.

Prompt = build_sft.py-ийн сургалтын prompt-той ижил (Arctic build_omnisql, few-shot-гүй, describe, баганын жишээ утга, dialect).
Асуулт: --question en (eval-ийн англи жишээ — орчуулгын нөлөөг тусгаарлана) эсвэл cache (build/translations/<--cache>.jsonl, mn).
Үр дүн: results/shift/<split>/<tag>.jsonl (+ <tag>.summary.json). Тасарвал дахин ажиллуулахад дууссаныг алгасна.

Kaggle (PG + vLLM served загвар):
  PYTHONPATH=src python scripts/t2s_inv/eval_shift.py --served arctic-r1-7b --tag base     # суурь Arctic
  PYTHONPATH=src python scripts/t2s_inv/eval_shift.py --served d2 --tag d2                  # одоогийн D2
  PYTHONPATH=src python scripts/t2s_inv/eval_shift.py --served inv1 --tag inv1              # t2s_inv1
test-д зөвхөн эцэст нэг удаа: --split test --final-test.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "mt"))

from schema_variants import load_semantic_model, rename_model  # noqa: E402

from mn_text2sql.llm.prompts import PromptContext, build_omnisql, render_ddl  # noqa: E402


def read_jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--split", default="dev", choices=["dev", "test"])
    ap.add_argument("--final-test", action="store_true")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    ap.add_argument("--served", required=True)
    ap.add_argument("--tag", required=True)
    ap.add_argument("--set", default="shift", choices=["shift", "defs"],
                    help="shift = T-2 (нэршил солих), defs = T-4 (тодорхойлолт дагах; make_def_eval.py)")
    ap.add_argument("--maps", default="orig,xsyn2,xshort,xmn2,xopq2")
    ap.add_argument("--question", default="en", choices=["en", "cache"])
    ap.add_argument("--cache", default="qwen3.5-9b.fix")
    ap.add_argument("--dialect", default="sqlite_transpile", choices=["sqlite_transpile", "postgres_prompt"])
    ap.add_argument("--max-tokens", type=int, default=2048)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    if args.split == "test" and not args.final_test:
        sys.exit("test-д зөвхөн эцэст нэг удаа: --final-test")

    from psycopg2.extensions import make_dsn, parse_dsn

    from mn_text2sql.config import load_config
    from mn_text2sql.db.conn import admin_dsn, connect
    from mn_text2sql.db.executor import Executor
    from mn_text2sql.eval.compare import strict_match
    from mn_text2sql.llm.client import OpenAICompatLLM
    from mn_text2sql.pipeline.dialect import sqlite_to_postgres
    from mn_text2sql.security.sql_guard import extract_sql

    d = ROOT / "evalset_shift" / args.split
    maps = {m["map_id"]: m for m in read_jsonl(d / "mappings.jsonl")}
    if args.set == "defs":       # T-4: эх схем, evidence-д сургалтад ороогүй тогтмол (make_def_eval.py)
        items = [dict(r, gold={"orig": r["gold"]}) for r in read_jsonl(d / "definitions.jsonl")]
        want = ["orig"]
    else:
        items = read_jsonl(d / "items.jsonl")
        want = [m for m in args.maps.split(",") if m == "orig" or m in maps]
    if args.limit:
        items = items[:args.limit]
    questions = {r["id"]: r for r in read_jsonl(ROOT / f"evalset/{args.split}.jsonl")}
    cache = {}
    if args.question == "cache":
        for r in read_jsonl(ROOT / f"build/translations/{args.cache}.jsonl"):
            if r.get("variant") == "mn":
                cache[r["id"]] = r.get("text")

    model = load_semantic_model()
    types = json.loads((ROOT / "build/index/column_examples.json").read_text(encoding="utf-8"))
    col_types = types.pop("__types__", {})
    models = {"orig": (model, col_types, types, {t: t for t in model.tables})}
    for k, m in maps.items():
        nt = {f"{m['table_map'][t]}.{m['col_map'][c]}": col_types.get(f"{t}.{c}", "")
              for t, tab in model.tables.items() for c in tab.columns}
        ex = {m["table_map"][t]: {m["col_map"][c]: v for c, v in cols.items() if c in m["col_map"]}
              for t, cols in types.items() if t in m["table_map"]}
        models[k] = (rename_model(model, m), nt, ex, m["table_map"])

    adm = admin_dsn(load_config())
    conn = connect(adm, autocommit=True)
    with conn.cursor() as cur:
        for k in want:
            if k != "orig":
                cur.execute((d / f"views_{k}.sql").read_text(encoding="utf-8"))
    conn.close()

    def dsn(path: str) -> str:
        p = parse_dsn(adm)
        p["options"] = f"-c search_path={path}"
        return make_dsn(**p)
    execs = {k: Executor(dsn("t2s" if k == "orig" else f"t2s_{k}"), statement_timeout_ms=60000, max_rows=20000)
             for k in want}
    res = execs["orig"].run("SELECT DISTINCT lower(column_name) FROM information_schema.columns WHERE data_type = 'boolean' "
                            "AND table_schema NOT IN ('pg_catalog', 'information_schema')")
    bool_cols = frozenset(r[0] for r in res.rows) if res.ok else frozenset()
    llm = OpenAICompatLLM(args.base_url, args.served, timeout_s=600)

    out_dir = ROOT / "results" / "shift" / args.split
    out_dir.mkdir(parents=True, exist_ok=True)
    if args.set == "defs":
        args.tag += ".defs"
    out_p = out_dir / f"{args.tag}.jsonl"
    done = {(r["id"], r["map"]) for r in read_jsonl(out_p)}

    def one(it: dict, k: str) -> dict:
        mdl, typ, ex, tmap = models[k]
        qid = it.get("src_id", it["id"])
        q = cache.get(qid) if args.question == "cache" else questions[qid]["question_en"]
        tabs = [tmap[t] for t in it["tables_orig"]]
        ctx = PromptContext(question=q or "", tables=tabs, describe=True, dialect=args.dialect,
                            evidence_lines=list(it.get("evidence_lines") or []))
        msgs, prefill = build_omnisql(ctx, render_ddl(mdl, tabs, typ, ex, True))
        r = llm.chat(msgs, temperature=0.0, max_tokens=args.max_tokens, prefill=prefill)
        text = r.texts[0]
        sql = extract_sql(text)
        rec = {"id": it["id"], "map": k, "sql": sql, "correct": False, "error": None}
        if not sql:
            rec["error"] = "no_sql"
            return rec
        if args.dialect == "sqlite_transpile":
            sql = sqlite_to_postgres(sql, bool_cols)
        gold_sql = it["gold"][k]
        rev = it.get("reverse") or []
        if rev:                     # T-4 дансны код: шинэ код SQL-д яг байх, дараа нь эх утгаар буцааж ажиллуулна
            if not all(new in sql for new, _ in rev):
                rec["error"] = "definition_not_followed"
                return rec
            for new, old in rev:
                sql, gold_sql = sql.replace(new, old), gold_sql.replace(new, old)
        g = execs[k].run(gold_sql)
        p = execs[k].run(sql)
        if not g.ok:
            rec["error"] = "gold_error"
        elif not p.ok:
            rec["error"] = (p.error or "exec")[:200]
        else:
            rec["correct"] = bool(strict_match(g.columns, g.rows, p.columns, p.rows, it["order_matters"]))
        return rec

    def safe(it, k):
        try:
            return one(it, k)
        except Exception as e:      # нэг асуултын алдаа бүхэлдээ зогсоохгүй
            return {"id": it["id"], "map": k, "sql": None, "correct": False, "error": f"exception: {str(e)[:200]}"}

    todo = [(it, k) for it in items for k in want if k in it["gold"] and (it["id"], k) not in done]
    print(f"{len(todo)} ажил (дууссан {len(done)}), загвар {args.served}", flush=True)
    with open(out_p, "a", encoding="utf-8", newline="\n") as fh, ThreadPoolExecutor(args.workers) as pool:
        futs = [pool.submit(safe, it, k) for it, k in todo]
        for i, f in enumerate(as_completed(futs), 1):
            fh.write(json.dumps(f.result(), ensure_ascii=False) + "\n")
            fh.flush()
            if i % 100 == 0:
                print(f"  {i}/{len(todo)}", flush=True)

    rows = read_jsonl(out_p)
    by = defaultdict(dict)
    for r in rows:
        by[r["id"]][r["map"]] = r["correct"]
    summary: dict = {"tag": args.tag, "served": args.served, "question": args.question, "split": args.split, "maps": {}}
    for k in want:
        ids = [i for i in by if k in by[i] and "orig" in by[i]]
        n = len(ids)
        if not n:
            continue
        ok = sum(by[i][k] for i in ids)
        ok_o = sum(by[i]["orig"] for i in ids)
        lost = sum(1 for i in ids if by[i]["orig"] and not by[i][k])
        gained = sum(1 for i in ids if by[i][k] and not by[i]["orig"])
        summary["maps"][k] = {"n": n, "ex": round(100 * ok / n, 1), "ex_orig_same_ids": round(100 * ok_o / n, 1),
                              "delta": round(100 * (ok - ok_o) / n, 1), "lost": lost, "gained": gained}
    shifted = [v for k, v in summary["maps"].items() if k != "orig"]
    if shifted:
        summary["mean_shift_ex"] = round(sum(v["ex"] for v in shifted) / len(shifted), 1)
        summary["mean_delta"] = round(sum(v["delta"] for v in shifted) / len(shifted), 1)
    summary["errors"] = dict(Counter((r["error"] or "ok").split(":")[0][:40] for r in rows).most_common(8))
    (out_dir / f"{args.tag}.summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    for ex in execs.values():
        ex.close()


if __name__ == "__main__":
    main()
