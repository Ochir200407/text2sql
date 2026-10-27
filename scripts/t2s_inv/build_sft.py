"""T2S «тогтмол мэдлэг» (t2s_inv1) SFT өгөгдөл: prompt угсрах → Arctic-аар rejection sampling (PG дээр шалгана) → {prompt, completion}.
docs/MT_INVARIANT_PLAN.md §6.

Урсгал (S1 = өөрийн DB-ийн эх схем, S2 = нэр сольсон схем, S3 = тодорхойлолт дагах, S4 = татгалзах / near-miss):
  prompts  (локал/Kaggle, GPU-гүй)  — мөр бүрд Arctic-ийн prompt (build_omnisql, inference-тэй ижил загвар; few-shot-гүй, DDL нь тухайн
                                      схемийн нэрээр, comment монгол/англи хэвээр) → <dir>/prompts.jsonl
  sample   (Kaggle: PG + vLLM)      — n гаргалт; SQL-ийг тухайн схемийн search_path-д ажиллуулж gold-той strict_match; зөвийг
                                      (≤ keep, давхардалгүй) хадгална. Шийдэгдээгүйд gold-hint-тэй (сургалтын prompt нь hint-гүй)
                                      дахин; S4-ийн татгалзал нь загвар бичсэн reasoning биш, бэлэн загвараар. → <dir>/samples.jsonl
  records  (токенизатор)            — chat template + prefill → {prompt, completion} → <dir>/records.jsonl (train_mt/run_sft-ийн формат)

Жишээ:
  PYTHONPATH=src python scripts/t2s_inv/build_sft.py prompts --out data/t2s_inv/v1 [--verified]
  PYTHONPATH=src python scripts/t2s_inv/build_sft.py sample --dir data/t2s_inv/v1 --base-url http://127.0.0.1:8000/v1 --served arctic-r1-7b
  PYTHONPATH=src python scripts/t2s_inv/build_sft.py records --dir data/t2s_inv/v1 --model arctic-r1-7b
Шударга байдал: эх жишээг schema_variants.py аль хэдийн eval-guard-аар шүүсэн; S4-ийг check_behavior.py шүүнэ (алдаатай бол зогсоно).
Prompt = үнэлгээний I3-ийн schema хэсэгтэй ижил: dialect (анхдагч sqlite_transpile = best_a A7; загвар SQLite бичиж PG руу
хөрвүүлнэ), баганын жишээ утга (build/index/column_examples.json, нэр сольсон схемд шинэ нэрээр), describe. Ялгаа: few-shot-гүй
(нэр сольсон схемд эх нэртэй жишээ зөрчилдөнө) — үнэлгээ T1 (few-shot-тэй I3 + adapter) ба T1n (few-shot-гүй)-ээр хэмжинэ.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "mt"))

from schema_variants import pick_tables, rename_model, tables_in  # noqa: E402

from mn_text2sql.knowledge.semantic import load_semantic_model  # noqa: E402
from mn_text2sql.llm.prompts import PromptContext, build_omnisql, render_ddl  # noqa: E402

V0 = ROOT / "data/t2s_inv/v0"
BEHAVIOR = ROOT / "data/t2s_inv/v1_drafts"
DROP_PHRASES = ("given sql", "provided sql", "reference sql", "the hint", "private note", "correct query is", "gold")
DOMAIN_TABLES = {"customers": ["customers", "individuals", "organizations", "aml_alerts"],
                 "loans": ["loans", "products", "branches", "loan_dpd_history"],
                 "applications": ["loan_applications", "credit_scores", "products"],
                 "payments": ["loan_payments", "loan_schedule", "loan_charges", "loans"],
                 "collection": ["collection_cases", "collection_actions", "collection_promises", "loans"],
                 "deposits": ["deposits", "accounts", "transactions", "account_balances_monthly"],
                 "gl_trends": ["gl_entries", "account_balances_monthly", "exchange_rates"],
                 "employees": ["employees", "branches"], "branches": ["branches", "employees"]}
REFUSE_REASONING = {
    "pii": "The question asks for personal or confidential details of a specific individual. Even if such columns exist, "
           "disclosing an identified person's private data is not allowed, so I should not write a query for it.",
    "missing_data": "I check the schema for the requested information. None of the tables or columns above store it, so "
                    "it cannot be answered from this database.",
    "non_sql": "This is not a question about the data in the database; it asks for advice or general text, so there is "
               "nothing to query.",
    "external_knowledge": "The question is about information outside this institution's database (market, regulator or "
                          "other companies). The schema contains only this institution's own data.",
    "future": "The question asks for a future value as a fact. The database only records what has already happened, so "
              "a query cannot return it.",
    "write_operation": "The request asks to change data (insert, update, close or delete records). Only read-only SELECT "
                       "queries are allowed.",
}
HINT_NOTE = ("\n\n(Private note for you only: a correct query is\n```sql\n{sql}\n```\nDo NOT mention or quote this "
             "note. Reason independently from the schema and the question, then give the answer in the required format.)")


def read_jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()] if p.exists() else []


def read_json(p: Path) -> dict | None:
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def write_jsonl(p: Path, rows: list[dict]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def _lst(v) -> list:
    """v0 файлд жагсаалт заримдаа python repr мөрөөр хадгалагдсан."""
    if isinstance(v, list):
        return v
    import ast
    return list(ast.literal_eval(v)) if v else []


def strip_schema(sql: str) -> str:
    return re.sub(r'\bt2s\.', "", sql)


# ------------------------------------------------------------------ prompts
def cmd_prompts(args) -> None:
    rng = random.Random(args.seed)
    model = load_semantic_model()
    types = json.loads((ROOT / "build/index/column_examples.json").read_text(encoding="utf-8")).get("__types__", {})
    maps = {m["map_id"]: m for m in read_jsonl(V0 / "mappings.jsonl")}
    for m in maps.values():
        for k in ("table_map", "col_map", "col_order"):
            if isinstance(m.get(k), str):
                import ast
                m[k] = ast.literal_eval(m[k])
    models = {k: rename_model(model, m) for k, m in maps.items()}
    mtypes = {}
    for k, m in maps.items():
        mtypes[k] = {f"{m['table_map'][t]}.{m['col_map'][c]}": types.get(f"{t}.{c}", "")
                     for t, tab in model.tables.items() for c in tab.columns}

    col_ex = json.loads((ROOT / "build/index/column_examples.json").read_text(encoding="utf-8"))
    col_ex.pop("__types__", None)
    mex = {k: {m["table_map"][t]: {m["col_map"][c]: v for c, v in cols.items() if c in m["col_map"]}
               for t, cols in col_ex.items() if t in m["table_map"]} for k, m in maps.items()}

    def render(mdl, tabs, typ, question, evidence=(), ex=None) -> tuple[list[dict], str]:
        ctx = PromptContext(question=question, tables=tabs, describe=True, evidence_lines=list(evidence),
                            dialect=args.dialect)
        msgs, prefill = build_omnisql(ctx, render_ddl(mdl, tabs, typ, col_ex if ex is None else ex, True))
        return msgs, prefill

    out, stats = [], Counter()
    src = V0 / ("schema_variants.verified.jsonl" if args.verified else "schema_variants.jsonl")
    variants = read_jsonl(src)
    if args.verified and not variants:
        sys.exit(f"{src} алга — эхлээд verify_variants.py (PG)")
    originals: dict[str, dict] = {}
    for r in variants:                                                   # S2
        mp = r["mapping"]
        tabs = _lst(r["ddl_tables"])
        msgs, prefill = render(models[mp], tabs, mtypes[mp], r["question_en"], ex=mex[mp])
        # search_path-д зөвхөн нэр сольсон схем: эх нэрээр бичсэн SQL t2s.* руу «унаж» зөв гэж тооцогдохгүй (view нь t2s.*-ийг
        # бүрэн нэрээр заадаг)
        out.append({"id": r["id"], "kind": "S2", "style": r["style"], "mapping": mp, "search_path": f"t2s_{mp}",
                    "messages": msgs, "prefill": prefill, "gold_sql": r["sql"],
                    "order_matters": str(r.get("order_matters")) == "True"})
        originals.setdefault(r["src_id"], r)
        stats[f"S2:{r['style']}"] += 1
    for sid, r in originals.items():                                     # S1: эх схем (өөрийн DB-ийн чадварыг хадгална)
        used = tables_in(strip_schema(r["sql_orig"]))
        tabs = pick_tables(model, [t for t in used if t in model.tables], rng)
        msgs, prefill = render(model, tabs, types, r["question_en"])
        out.append({"id": f"{sid}@orig", "kind": "S1", "mapping": "orig", "search_path": "t2s", "messages": msgs,
                    "prefill": prefill, "gold_sql": r["sql_orig"], "order_matters": str(r.get("order_matters")) == "True"})
        stats["S1"] += 1
    for r in read_jsonl(V0 / "counterfactual.jsonl"):                   # S3
        used = tables_in(strip_schema(r["sql"]))
        tabs = pick_tables(model, [t for t in used if t in model.tables], rng)
        msgs, prefill = render(model, tabs, types, r["question_en"], _lst(r["evidence_lines"]))
        rec = {"id": r["id"], "kind": "S3", "mapping": "orig", "search_path": "t2s", "messages": msgs,
               "prefill": prefill, "gold_sql": r["sql"], "order_matters": False}
        if r.get("kind") == "gl_code":
            # шинэ код (4110 …) DB-д байхгүй → gold хоосон. Шалгалт: шинэ код SQL-д яг байх + кодыг эх утгаар нь буцааж
            # сольж ажиллуулсан үр дүн таарах (хоёулаа эх DB-ийн бодит өгөгдөл дээр)
            rec["reverse"] = [[f"'{new}'", f"'{old}'"] for old, new in _lst(r["changes"])]
        out.append(rec)
        stats["S3"] += 1
    beh = sorted(BEHAVIOR.glob("behavior_*.jsonl"))
    if beh:
        import check_behavior
        if check_behavior.main([str(f) for f in beh]):
            sys.exit("S4 ноорог check_behavior-оор алдаатай — засаад дахин")
    for f in beh:                                                         # S4
        for r in read_jsonl(f):
            if r["kind"] == "answer":
                used = [t for t in tables_in(strip_schema(r["gold_sql"])) if t in model.tables]
            else:
                used = [t for t in DOMAIN_TABLES.get(r.get("domain"), []) if t in model.tables][:2]
            tabs = pick_tables(model, used, rng)
            msgs, prefill = render(model, tabs, types, r["question_en"])
            rec = {"id": r["id"], "kind": "S4_" + r["kind"], "mapping": "orig", "search_path": "t2s", "messages": msgs,
                   "prefill": prefill, "gold_sql": r["gold_sql"], "order_matters": False}
            if r["kind"] == "refuse":
                rec["completion"] = (f" {REFUSE_REASONING[r['reason']]}\n</think>\n<answer>\n{r['explanation_en'].strip()}"
                                     f"\n</answer>")
                rec["reason"] = r["reason"]
            out.append(rec)
            stats[rec["kind"]] += 1
    if args.limit:
        rng.shuffle(out)
        out = out[:args.limit]
    write_jsonl(Path(args.out) / "prompts.jsonl", out)
    import hashlib
    inputs = [src, V0 / "counterfactual.jsonl", *beh]
    meta = {"prompts": len(out), "source": src.name, "dialect": args.dialect, **stats,
            "inputs_sha256": {f.name: hashlib.sha256(f.read_bytes()).hexdigest()[:16] for f in inputs if f.exists()}}
    (Path(args.out) / "prompts_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(meta, ensure_ascii=False, indent=1))


# ------------------------------------------------------------------ sample
def cmd_sample(args) -> None:
    from psycopg2.extensions import make_dsn, parse_dsn

    from mn_text2sql.config import load_config
    from mn_text2sql.db.conn import admin_dsn, connect
    from mn_text2sql.db.executor import Executor
    from mn_text2sql.eval.compare import strict_match
    from mn_text2sql.llm.client import OpenAICompatLLM
    from mn_text2sql.pipeline.dialect import sqlite_to_postgres
    from mn_text2sql.security.sql_guard import extract_sql

    d = Path(args.dir)
    prompts = read_jsonl(d / "prompts.jsonl")
    out_p = d / "samples.jsonl"
    done = {r["id"] for r in read_jsonl(out_p)}
    adm = admin_dsn(load_config())
    maps = sorted({p["mapping"] for p in prompts if p["mapping"] != "orig"})
    conn = connect(adm, autocommit=True)
    with conn.cursor() as cur:                                           # нэр сольсон view (verify_variants-тэй ижил)
        for m in maps:
            cur.execute((V0 / f"views_{m}.sql").read_text(encoding="utf-8"))
    conn.close()

    def dsn(path: str) -> str:
        p = parse_dsn(adm)
        p["options"] = f"-c search_path={path}"
        return make_dsn(**p)
    execs = {sp: Executor(dsn(sp), statement_timeout_ms=60000, max_rows=20000)
             for sp in {p["search_path"] for p in prompts}}
    llm = OpenAICompatLLM(args.base_url, args.served, timeout_s=600)
    stats: Counter = Counter()
    dialect = (read_json(d / "prompts_meta.json") or {}).get("dialect", "sqlite_transpile")
    res = execs["t2s"].run("SELECT DISTINCT lower(column_name) FROM information_schema.columns WHERE data_type = 'boolean' "
                           "AND table_schema NOT IN ('pg_catalog', 'information_schema')")
    bool_cols = frozenset(r[0] for r in res.rows) if res.ok else frozenset()    # view-ийн шинэ нэрс ч орно

    def norm_sql(x: str) -> str:
        return re.sub(r"\s+", " ", (x or "").strip().rstrip(";")).lower()

    def unrev(p: dict, sql: str) -> str:
        for new, old in p.get("reverse") or []:
            sql = sql.replace(new, old)
        return sql

    def correct(p: dict, gold, text: str) -> bool:
        sql = extract_sql(text)
        if not sql or "</answer>" not in text:
            return False
        if dialect == "sqlite_transpile":                                # үнэлгээний A7-тай ижил: SQLite → PG
            sql = sqlite_to_postgres(sql, bool_cols)
        if p.get("reverse"):
            if not all(new in sql for new, _ in p["reverse"]):         # тодорхойлолтын шинэ кодыг дагаагүй
                return False
            sql = unrev(p, sql)
        r = execs[p["search_path"]].run(sql)
        return bool(r.ok and strict_match(gold.columns, gold.rows, r.columns, r.rows, bool(p.get("order_matters"))))

    def one(p: dict) -> dict:
        base = {k: p[k] for k in ("id", "kind", "mapping")}
        if "completion" in p:                                           # S4 татгалзал — бэлэн
            return {**base, "completions": [p["completion"]], "how": "template"}
        gold = execs[p["search_path"]].run(unrev(p, p["gold_sql"]))
        if not gold.ok:
            return {**base, "completions": [], "how": "gold_error"}
        if not gold.rows:          # хоосон gold: 0 мөр буцаасан ямар ч SQL «зөв» болно → сургалтад оруулахгүй
            return {**base, "completions": [], "how": "gold_empty"}
        gnorm = norm_sql(p["gold_sql"])
        keep: list[str] = []
        seen: set[str] = set()
        for how, msgs in (("sample", p["messages"]),
                          ("hint", p["messages"][:-1] + [dict(p["messages"][-1], content=p["messages"][-1]["content"]
                                                              + HINT_NOTE.format(sql=p["gold_sql"]))])):
            r = llm.chat(msgs, n=args.n, temperature=args.temperature, top_p=0.95, max_tokens=args.max_tokens,
                         prefill=p["prefill"])
            for t in r.texts:
                body = t[len(p["prefill"]):] if t.startswith(p["prefill"]) else t
                if how == "hint":
                    think = body.split("</think>")[0]
                    if any(x in think.lower() for x in DROP_PHRASES) or gnorm in norm_sql(think):
                        stats["hint_dropped_copy"] += 1      # gold-ыг reasoning-д хуулсан/дурдсан
                        continue
                key = re.sub(r"\s+", " ", (extract_sql(body) or "").lower())
                if key in seen or not correct(p, gold, body):
                    continue
                seen.add(key)
                keep.append(body)
                if len(keep) >= args.keep:
                    break
            if keep:
                return {**base, "completions": keep, "how": how}
        return {**base, "completions": [], "how": "unsolved"}

    def safe(p: dict) -> dict:
        try:
            return one(p)
        except Exception as e:                                          # нэг prompt-ын алдаа бүхэлдээ зогсоохгүй
            return {"id": p["id"], "kind": p["kind"], "mapping": p["mapping"], "completions": [], "how": "error",
                    "error": str(e)[:300]}

    todo = [p for p in prompts if p["id"] not in done]
    print(f"{len(todo)} prompt (дууссан {len(done)})", flush=True)
    with open(out_p, "a", encoding="utf-8", newline="\n") as fh, ThreadPoolExecutor(args.workers) as pool:
        futs = [pool.submit(safe, p) for p in todo]
        for i, fut in enumerate(as_completed(futs), 1):
            res = fut.result()
            fh.write(json.dumps(res, ensure_ascii=False) + "\n")
            fh.flush()
            stats[f"{res['kind']}:{res['how']}"] += 1
            if i % 50 == 0:
                print(f"  {i}/{len(todo)} {dict(stats)}", flush=True)
    rep = Counter()
    for r in read_jsonl(out_p):
        rep[f"{r['kind']}:{r['how']}"] += 1
    (d / "sample_report.json").write_text(json.dumps(dict(sorted(rep.items())), ensure_ascii=False, indent=2),
                                          encoding="utf-8")
    print(json.dumps(dict(sorted(rep.items())), ensure_ascii=False, indent=1))


# ------------------------------------------------------------------ records
def cmd_records(args) -> None:
    from transformers import AutoTokenizer

    from mn_text2sql.config import load_models
    d = Path(args.dir)
    mspec = load_models()["models"][args.model]
    tok = AutoTokenizer.from_pretrained(mspec["hf_id"], trust_remote_code=True)
    eos = tok.eos_token or ""
    prompts = {p["id"]: p for p in read_jsonl(d / "prompts.jsonl")}
    rows, stats, lens = [], Counter(), []
    for s in read_jsonl(d / "samples.jsonl"):
        p = prompts.get(s["id"])
        if not p:
            continue
        prompt = tok.apply_chat_template(p["messages"], tokenize=False, add_generation_prompt=True) + (p["prefill"] or "")
        for c in s["completions"]:
            n = len(tok(prompt + c, add_special_tokens=False)["input_ids"])
            if n > args.max_length:
                stats["dropped_too_long"] += 1
                continue
            rows.append({"prompt": prompt, "completion": c + eos, "id": s["id"], "kind": s["kind"]})
            lens.append(n)
            stats[s["kind"]] += 1
    random.Random(42).shuffle(rows)
    write_jsonl(d / "records.jsonl", [{"prompt": r["prompt"], "completion": r["completion"]} for r in rows])
    rep = {"records": len(rows), **stats, "tokens_total": sum(lens), "tokens_max": max(lens, default=0)}
    (d / "records_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(rep, ensure_ascii=False, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("prompts")
    p.add_argument("--out", default="data/t2s_inv/v1")
    p.add_argument("--verified", action="store_true", help="зөвхөн PG-ээр баталгаажсан S2 (schema_variants.verified.jsonl)")
    p.add_argument("--seed", type=int, default=11)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--dialect", default="sqlite_transpile", choices=["sqlite_transpile", "postgres_prompt"],
                   help="үнэлгээний best_a-тай ижил (A7 = sqlite_transpile)")
    p = sub.add_parser("sample")
    p.add_argument("--dir", default="data/t2s_inv/v1")
    p.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    p.add_argument("--served", default="arctic-r1-7b")
    p.add_argument("--n", type=int, default=4)
    p.add_argument("--keep", type=int, default=2)
    p.add_argument("--temperature", type=float, default=0.8)
    p.add_argument("--max-tokens", type=int, default=2048)
    p.add_argument("--workers", type=int, default=16)
    p = sub.add_parser("records")
    p.add_argument("--dir", default="data/t2s_inv/v1")
    p.add_argument("--model", default="arctic-r1-7b")
    p.add_argument("--max-length", type=int, default=6144)
    args = ap.parse_args()
    {"prompts": cmd_prompts, "sample": cmd_sample, "records": cmd_records}[args.cmd](args)


if __name__ == "__main__":
    main()
