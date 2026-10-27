"""T-4 (тодорхойлолт дагах) eval: dev/test асуултын glossary нэр томьёоны тогтмолыг (ерөнхий дэвтрийн дансны код, «хугацаа хэтэрсэн»-ийн
босго) СУРГАЛТАД ОРООГҮЙ утгаар (4170 …, > 5 / > 10 хоног) evidence ба gold SQL-д зэрэг сольно. Загвар prompt-ын тодорхойлолтыг
дагавал зөв, өөрийн «санаж буй» утгаар бичвэл буруу. docs/MT_INVARIANT_PLAN.md §6.4 T-4. Сургалтын S3 = counterfactual.py (4110/4150, 3/7/15).

Шалгалт (eval_shift.py --set defs): шинэ код SQL-д яг байх + кодыг эх утгаар буцааж сольсон SQL-ийн үр дүн gold-той таарах (шинэ
код DB-д байхгүй тул шууд ажиллуулбал хоосон); босгын хувилбарыг шууд ажиллуулна.
Гаралт: evalset_shift/<split>/definitions.jsonl (eval-ийн текстгүй: id, evidence, gold, reverse, хүснэгт).

Ажиллуулах:  PYTHONPATH=src python scripts/t2s_inv/make_def_eval.py [--split dev]
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "mt"))

from counterfactual import GL_TERMS, evidence_line  # noqa: E402
from schema_variants import load_semantic_model, normalize, pick_tables, tables_in  # noqa: E402

from mn_text2sql.knowledge.semantic import load_glossary  # noqa: E402

EVAL_CODES = {"4100": "4170", "4200": "4270", "4300": "4370", "5100": "5170", "5200": "5270", "5400": "5470"}
EVAL_K = [5, 10]


def sub(text: str) -> str:
    for old, new in EVAL_CODES.items():
        text = text.replace(old, new)
    return text


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev", choices=["dev", "test"])
    args = ap.parse_args()
    g = {t.id: t for t in load_glossary("knowledge/glossary_plus.yaml")}
    model = load_semantic_model()
    out, stats = [], Counter()
    for line in (ROOT / f"evalset/{args.split}.jsonl").read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        terms = [t for t in (d.get("glossary_terms") or []) if t in g]
        sql = d.get("gold_sql") or ""
        if not terms or not sql:
            continue
        used = [t for t in tables_in(normalize(sql)) if t in model.tables]
        tabs = pick_tables(model, used, random.Random(f"def:{d['id']}"))
        base = {"src_id": d["id"], "tables_orig": tabs, "order_matters": bool(d.get("order_matters"))}
        codes = set(re.findall(r"'(4100|4200|4300|5100|5200|5400)'", sql))
        gl_terms = [t for t in terms if t in GL_TERMS]
        if codes and gl_terms:
            covered = {c for t in gl_terms for c in EVAL_CODES if c in g[t].sql or c in g[t].definition_en}
            if codes <= covered:
                ev = [evidence_line(g[t], sub(g[t].definition_en), sub(g[t].sql), sub(g[t].en)) for t in gl_terms]
                out.append({"id": f"{d['id']}@glx", **base, "kind": "gl_code", "evidence_lines": ev, "gold": sub(sql),
                            "reverse": [[f"'{EVAL_CODES[c]}'", f"'{c}'"] for c in sorted(codes)]})
                stats["gl_code"] += 1
            else:
                stats["gl_uncovered"] += 1
        if "overdue_loan" in terms and re.search(r"days_past_due\s*>\s*0\b", sql):
            q = d["question_en"] + " " + d["question_mn"]
            if re.search(r"1\+|\b0\s*(days|хоног)|>\s*0", q):
                stats["overdue_question_mentions_threshold"] += 1
            else:
                t = g["overdue_loan"]
                pat = re.compile(r"days_past_due\s*>\s*0\b")
                for k in EVAL_K:
                    ev = [evidence_line(t, pat.sub(f"days_past_due > {k}", t.definition_en).replace(
                        "days past due > 0", f"days past due > {k}"), pat.sub(f"days_past_due > {k}", t.sql))]
                    out.append({"id": f"{d['id']}@odx{k}", **base, "kind": "overdue_k", "evidence_lines": ev,
                                "gold": pat.sub(f"days_past_due > {k}", sql), "reverse": []})
                    stats["overdue_k"] += 1
        # PAR30 / PAR90: тодорхойлолтын босгыг (> 30 → > 45, > 90 → > 120) evidence ба gold-д зэрэг сольно. Асуулт өөрөө
        # босгыг (PAR30-аас бусад хэлбэрээр) дурдсан бол алгасна
        for tid, old, new in (("par30", 30, 45), ("par90", 90, 120)):
            pat = re.compile(rf"days_past_due\s*>\s*{old}\b")
            if tid in terms and pat.search(sql) and not re.search(rf"\b{old}\b", d["question_en"].replace(f"PAR{old}", "")):
                t = g[tid]
                rep = f"days_past_due > {new}"
                ev = [evidence_line(t, pat.sub(rep, t.definition_en).replace(f"PAR{old}", f"PAR{old} (threshold {new} days)"),
                                    pat.sub(rep, t.sql))]
                out.append({"id": f"{d['id']}@{tid}x", **base, "kind": tid, "evidence_lines": ev, "gold": pat.sub(rep, sql),
                            "reverse": []})
                stats[tid] += 1
    p = ROOT / "evalset_shift" / args.split / "definitions.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        for r in out:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(json.dumps({"rows": len(out), **stats}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
