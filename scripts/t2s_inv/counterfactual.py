"""S3: «тодорхойлолт дагах» (counterfactual) — загвар компанийн ӨӨРЧЛӨГДДӨГ тогтмолуудыг (ерөнхий дэвтрийн дансны код, «хугацаа
хэтэрсэн»-ийн босго) цээжилж болохгүй, prompt-ын evidence-д өгсөн утгыг ДАГАХ ёстой (docs/MT_INVARIANT_PLAN.md §6.2).

Өөрийн жишээнээс glossary-ийн нэр томьёо ашигласан (glossary_terms) жишээг авч, тогтмолыг сольсон хувилбар үүсгэнэ:
  gl_code   : 4100→4110/4150, 4200→…, 4300→…, 5100→…, 5200→…, 5400→… (glossary-ийн SQL, definition_en, gold SQL-д зэрэг)
  overdue_k : «хугацаа хэтэрсэн» = days_past_due > 0 → > 3 / 7 / 15
Мөр бүр: evidence_lines (inference-тэй ижил хэлбэр: «<en> («<term>»): <definition_en> Use exactly: <sql>»), сольсон gold SQL.
Гүйцэтгэл (үр дүн) баталгаажуулалт шаардахгүй — зорилго нь SQL-ийн ТЕКСТ evidence-ийг дагах; sqlglot-оор задрах эсэхийг шалгана.
Eval (T-4)-д ӨӨР утга (жишээ нь 4170, босго 5/10) ашиглана — энд сургалтад оруулсан утгыг eval-д давтахгүй.

Ажиллуулах:  PYTHONPATH=src python scripts/t2s_inv/counterfactual.py
"""

from __future__ import annotations

import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "mt"))

import sqlglot  # noqa: E402

from build_mt_v0 import EvalGuard, read_jsonl  # noqa: E402
from mn_text2sql.knowledge.semantic import load_glossary  # noqa: E402

OUT = ROOT / "data" / "t2s_inv" / "v0"
EXAMPLE_FILES = ["knowledge/examples.jsonl", "knowledge/examples_plus.jsonl", "knowledge/examples_plus2.jsonl"]
GL_TERMS = {"interest_income", "fee_income", "penalty_income", "net_interest_income", "deposit_interest_expense",
            "gl_provision_expense", "operating_expense", "cost_to_income"}
GL_CODES = {"4100": ["4110", "4150"], "4200": ["4210", "4250"], "4300": ["4310", "4350"], "5100": ["5110", "5150"],
            "5200": ["5210", "5250"], "5400": ["5410", "5450"]}
OVERDUE_K = [3, 7, 15]


def evidence_line(g, definition_en: str, sql: str, en: str | None = None) -> str:
    return f"{en or g.en} («{g.term}»): {definition_en} Use exactly: {sql}"


def sub_codes(text: str, variant: int) -> str:
    for old, alts in GL_CODES.items():
        text = text.replace(old, alts[variant])
    return text


def main() -> None:
    g = {t.id: t for t in load_glossary("knowledge/glossary_plus.yaml")}
    guard = EvalGuard()
    rng = random.Random(11)
    rows, stats = [], Counter()
    for f in EXAMPLE_FILES:
        for d in read_jsonl(ROOT / f):
            terms = [t for t in (d.get("glossary_terms") or []) if t in g]
            sql = d.get("gold_sql") or ""
            if not terms or not sql:
                continue
            stats["with_terms"] += 1
            if (guard.hit("mn", d["question_mn"]) or guard.hit("en", d["question_en"])
                    or any(h["ratio"] >= 0.5 for h in guard.substring_hits({d["id"]: [d["question_mn"], d["question_en"]]}))):
                stats["dropped_eval_overlap"] += 1
                continue
            # ---- gl_code
            codes_in_sql = set(re.findall(r"'(4100|4200|4300|5100|5200|5400)'", sql))
            gl_terms = [t for t in terms if t in GL_TERMS]
            if codes_in_sql and gl_terms:
                covered = {c for t in gl_terms for c in GL_CODES if c in g[t].sql or c in g[t].definition_en}
                if codes_in_sql <= covered:   # SQL-ийн бүх код evidence-д байх ёстой
                    for v in (0, 1):
                        new_sql = sub_codes(sql, v)
                        if new_sql == sql:
                            continue
                        ev = [evidence_line(g[t], sub_codes(g[t].definition_en, v), sub_codes(g[t].sql, v), sub_codes(g[t].en, v))
                              for t in gl_terms]
                        rows.append({"id": f"{d['id']}@gl{v + 1}", "src_id": d["id"], "kind": "gl_code", "variant": v + 1,
                                     "domain": d.get("domain"), "family": d.get("family"), "question_mn": d["question_mn"],
                                     "question_en": d["question_en"], "evidence_lines": ev, "sql_orig": sql, "sql": new_sql,
                                     "changes": sorted({(c, GL_CODES[c][v]) for c in codes_in_sql})})
                        stats["gl_code"] += 1
                else:
                    stats["gl_code_skipped_uncovered"] += 1
            # ---- overdue_k
            if "overdue_loan" in terms and re.search(r"days_past_due\s*>\s*0\b", sql):
                q = d["question_en"] + " " + d["question_mn"]
                if re.search(r"1\+|\b0\s*(days|хоног)|>\s*0", q):
                    stats["overdue_skipped_question_mentions_threshold"] += 1
                else:
                    t = g["overdue_loan"]
                    for k in rng.sample(OVERDUE_K, 2):
                        pat = re.compile(r"days_past_due\s*>\s*0\b")
                        new_sql = pat.sub(f"days_past_due > {k}", sql)
                        ev = [evidence_line(t, pat.sub(f"days_past_due > {k}", t.definition_en).replace(
                            "days past due > 0", f"days past due > {k}"), pat.sub(f"days_past_due > {k}", t.sql))]
                        rows.append({"id": f"{d['id']}@od{k}", "src_id": d["id"], "kind": "overdue_k", "variant": k,
                                     "domain": d.get("domain"), "family": d.get("family"), "question_mn": d["question_mn"],
                                     "question_en": d["question_en"], "evidence_lines": ev, "sql_orig": sql, "sql": new_sql,
                                     "changes": [("days_past_due > 0", f"days_past_due > {k}")]})
                        stats["overdue_k"] += 1
    # sqlglot-оор задрах эсэх
    ok = []
    for r in rows:
        try:
            sqlglot.parse_one(r["sql"], read="postgres")
            ok.append(r)
        except sqlglot.errors.SqlglotError:
            stats["unparsable"] += 1
    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "counterfactual.jsonl", "w", encoding="utf-8", newline="\n") as fh:
        for r in ok:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    stats["rows"] = len(ok)
    print(json.dumps(dict(stats), ensure_ascii=False, indent=1))
    for kind in ("gl_code", "overdue_k"):
        r = next((x for x in ok if x["kind"] == kind), None)
        if r:
            print(f"\n--- {kind}: {r['id']}\nQ  :", r["question_en"], "\nEV :", r["evidence_lines"][0][:260],
                  "\nэх :", r["sql_orig"][:200], "\nшинэ:", r["sql"][:200])


if __name__ == "__main__":
    main()
