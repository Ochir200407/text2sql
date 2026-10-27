"""Алдааны шинжилгээ (туршилтын төлөвлөгөө: шилдэг тохиргоо бүрийн 50 алдааг NL2SQL-BUGs ангиллаар, монгол хэлний
онцлог шошготой — утгын бичлэг, дагавар, галиг, нэр томьёо, буруу хүснэгт).

Автомат шошго нь зөвхөн САНАЛ: gold ба таамаг SQL-ийн бүтцийг (sqlglot AST) харьцуулж ангиллыг таамаглана. Эцсийн
шошгыг хүн CSV-ийн manual_* баганад тавина (docs/EVAL.md "Алдааны шинжилгээ").
"""

from __future__ import annotations

import csv
import json
import random
from collections import Counter
from pathlib import Path

import sqlglot
from rapidfuzz import fuzz
from sqlglot import exp

from ..config import Config, resolve_path
from ..text.normalize import cyr_key, script_of, strip_suffix, tokens, weak_latin_keys
from .dataset import load_all_eval_items
from .metrics import load_records

# NL2SQL-BUGs (Liu et al., 2025): 9 үндсэн ангилал, 31 дэд ангилал
NL2SQL_BUGS = {
    "Attribute": ["Attribute Mismatch", "Attribute Redundancy", "Attribute Missing"],
    "Table": ["Table Mismatch", "Table Redundancy", "Table Missing", "Join Condition Mismatch", "Join Type Mismatch"],
    "Value": ["Value Mismatch", "Data Format Mismatch"],
    "Operator": ["Comparison Operator", "Logical Operator"],
    "Condition": ["Explicit Condition Missing", "Explicit Condition Mismatch", "Explicit Condition Redundancy",
                  "Implicit Condition Missing"],
    "Function": ["Aggregate Functions", "Window Functions", "Date/Time Functions", "Conversion Functions",
                 "Math Functions", "String Functions", "Conditional Functions"],
    "Clause": ["Clause Missing", "Clause Redundancy"],
    "Subquery": ["Subquery Missing", "Subquery Mismatch", "Partial Query"],
    "Other": ["ASC/DESC", "DISTINCT", "Other"],
}
MN_TAGS = ["утгын бичлэг", "дагавар", "галиг", "нэр томьёо", "буруу хүснэгт", "огноо", "валют", "dialect", "бусад"]
# Нэр томьёоны тодорхойлолтод оролцдог баганууд (glossary.yaml) — gold ашигласан, таамаг ашиглаагүй бол "нэр томьёо"
TERM_COLUMNS = {"is_active_portfolio", "days_past_due", "principal_outstanding_mnt", "total_outstanding_mnt",
                "status_code", "classification_code", "is_npl", "dpd_bucket", "segment_code", "is_rural",
                "decision_status", "outcome", "is_kept", "provision_rate", "account_type"}
DATE_FUNCS = (exp.DateTrunc, exp.Extract, exp.DateAdd, exp.DateSub, exp.CurrentDate, exp.Interval, exp.DateDiff,
              exp.TimestampTrunc)


def _parse(sql: str | None):
    if not sql:
        return None
    try:
        return sqlglot.parse_one(sql, read="postgres")
    except Exception:
        return None


def sql_parts(tree) -> dict:
    if tree is None:
        return {}
    sel = tree if isinstance(tree, exp.Select) else tree.find(exp.Select)
    where = tree.find(exp.Where)
    return {
        "tables": {t.name.lower() for t in tree.find_all(exp.Table) if t.name},
        "columns": {c.name.lower() for c in tree.find_all(exp.Column) if c.name},
        "n_select": len(sel.expressions) if sel is not None else 0,
        "aggs": sorted(type(a).__name__ for a in tree.find_all(exp.AggFunc)),
        "strings": {lit.this for lit in tree.find_all(exp.Literal) if lit.is_string},
        "numbers": {lit.this for lit in tree.find_all(exp.Literal) if not lit.is_string},
        "cmp_ops": sorted(type(n).__name__ for n in tree.find_all(exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE,
                                                                     exp.Like, exp.ILike, exp.In, exp.Between)),
        "n_and": len(list(tree.find_all(exp.And))), "n_or": len(list(tree.find_all(exp.Or))),
        "n_where_preds": _count_preds(where.this) if where is not None else 0,
        "group": tree.find(exp.Group) is not None, "order": tree.find(exp.Order) is not None,
        "limit": tree.find(exp.Limit) is not None, "having": tree.find(exp.Having) is not None,
        "distinct": tree.find(exp.Distinct) is not None,
        "desc": [bool(o.args.get("desc")) for o in tree.find_all(exp.Ordered)],
        "windows": len(list(tree.find_all(exp.Window))),
        "subqueries": len(list(tree.find_all(exp.Subquery))) + len(list(tree.find_all(exp.CTE))),
        "joins": sorted((j.side or j.kind or "INNER").upper() for j in tree.find_all(exp.Join)),
        "date_funcs": len([n for n in tree.walk() if isinstance(n, DATE_FUNCS)]),
        "casts": len(list(tree.find_all(exp.Cast))),
        "case": len(list(tree.find_all(exp.Case))),
    }


def _count_preds(node) -> int:
    if isinstance(node, (exp.And, exp.Or)):
        return _count_preds(node.left) + _count_preds(node.right)
    if isinstance(node, exp.Paren):
        return _count_preds(node.this)
    return 1


def value_tags(pred_strings: set[str], gold_strings: set[str]) -> list[str]:
    """Таамгийн текст утга gold-ынхоос ялгаатай үед монгол хэлний шалтгааныг таамаглана."""
    tags = []
    for p in pred_strings - gold_strings:
        best, best_g = 0.0, None
        for g in gold_strings:
            s = fuzz.ratio(p.lower(), g.lower())
            if s > best:
                best, best_g = s, g
        if best_g is None:
            continue
        if script_of(p) in ("latin", "mixed") and script_of(best_g) == "cyrillic":
            tags.append("галиг")
        elif " ".join(p.lower().split()) == " ".join(best_g.lower().split()):
            tags.append("утгын бичлэг")          # зөвхөн том/жижиг үсэг, хоосон зай
        elif _stems(p) == _stems(best_g):
            tags.append("дагавар")               # үгийн үндэс ижил, нөхцөл/дагавар зөрсөн (Дарханы ↔ Дархан)
        elif any(k in weak_latin_keys(p) for k in weak_latin_keys(best_g)) or best >= 75:
            tags.append("утгын бичлэг")
    return tags


def _stems(text: str) -> list[str]:
    return [strip_suffix(t) for t in tokens(cyr_key(text))]


def auto_tags(rec: dict, gold_sql: str | None) -> tuple[list[str], list[str], str]:
    stage = rec.get("error_stage")
    err = (rec.get("error") or "").lower()
    bugs, mn, detail = [], [], ""
    if rec.get("status") in ("abstained", "refused", "clarify"):
        return ["Other"], ["бусад"], "татгалзсан"
    if stage in ("extract", "llm"):
        return ["Other"], ["бусад"], "SQL гаргаж аваагүй"
    if stage in ("guard", "exec"):
        if "unknown table" in err or "does not exist" in err and "relation" in err:
            bugs.append("Table Mismatch")
            mn.append("буруу хүснэгт")
        elif "column" in err and ("does not exist" in err or "байхгүй" in err):
            bugs.append("Attribute Mismatch")
        elif "function" in err and "does not exist" in err:
            bugs.append("Date/Time Functions" if any(k in err for k in ("date", "julian", "strftime")) else "Other")
            mn.append("dialect")
        elif "syntax" in err or "синтакс" in err:
            bugs.append("Other")
            mn.append("dialect")
        else:
            bugs.append("Other")
        detail = f"{stage}: {err[:160]}"
    gp, pp = sql_parts(_parse(gold_sql)), sql_parts(_parse(rec.get("sql")))
    if gp and pp:
        if gp["tables"] != pp["tables"]:
            if gp["tables"] - pp["tables"] and pp["tables"] - gp["tables"]:
                bugs.append("Table Mismatch")
            elif gp["tables"] - pp["tables"]:
                bugs.append("Table Missing")
            else:
                bugs.append("Table Redundancy")
            mn.append("буруу хүснэгт")
        elif gp["joins"] != pp["joins"]:
            bugs.append("Join Type Mismatch")
        if gp["n_select"] != pp["n_select"]:
            bugs.append("Attribute Redundancy" if pp["n_select"] > gp["n_select"] else "Attribute Missing")
        elif gp["columns"] != pp["columns"] and not (gp["tables"] != pp["tables"]):
            bugs.append("Attribute Mismatch")
        if gp["aggs"] != pp["aggs"]:
            bugs.append("Aggregate Functions")
        if gp["strings"] != pp["strings"]:
            vt = value_tags(pp["strings"], gp["strings"])
            if vt:
                bugs.append("Value Mismatch")
                mn += vt
            elif pp["strings"] - gp["strings"]:
                bugs.append("Value Mismatch")
        if gp["numbers"] != pp["numbers"]:
            bugs.append("Value Mismatch")
        if gp["cmp_ops"] != pp["cmp_ops"]:
            bugs.append("Comparison Operator")
        if (gp["n_and"], gp["n_or"]) != (pp["n_and"], pp["n_or"]) and gp["n_where_preds"] == pp["n_where_preds"]:
            bugs.append("Logical Operator")
        if pp["n_where_preds"] < gp["n_where_preds"]:
            bugs.append("Explicit Condition Missing")
        elif pp["n_where_preds"] > gp["n_where_preds"]:
            bugs.append("Explicit Condition Redundancy")
        for key, name in (("group", "GROUP BY"), ("having", "HAVING"), ("order", "ORDER BY"), ("limit", "LIMIT")):
            if gp[key] != pp[key]:
                bugs.append("Clause Missing" if gp[key] else "Clause Redundancy")
                detail += f" {name}{'−' if gp[key] else '+'}"
        if gp["distinct"] != pp["distinct"]:
            bugs.append("DISTINCT")
        if gp["order"] and pp["order"] and gp["desc"][:1] != pp["desc"][:1]:
            bugs.append("ASC/DESC")
        if gp["windows"] != pp["windows"]:
            bugs.append("Window Functions")
        if gp["subqueries"] != pp["subqueries"]:
            bugs.append("Subquery Missing" if gp["subqueries"] > pp["subqueries"] else "Subquery Mismatch")
        if gp["date_funcs"] != pp["date_funcs"]:
            bugs.append("Date/Time Functions")
            mn.append("огноо")
        missing_terms = (gp["columns"] & TERM_COLUMNS) - pp["columns"]
        if missing_terms:
            mn.append("нэр томьёо")
            detail += " term:" + ",".join(sorted(missing_terms))
        if any(c.endswith("_mnt") for c in gp["columns"]) and not any(c.endswith("_mnt") for c in pp["columns"]):
            mn.append("валют")
    if not bugs:
        bugs.append("Implicit Condition Missing" if stage == "empty" else "Other")
    if not mn:
        mn.append("бусад")
    return list(dict.fromkeys(bugs)), list(dict.fromkeys(mn)), detail.strip()


def bug_category(sub: str) -> str:
    for cat, subs in NL2SQL_BUGS.items():
        if sub in subs:
            return cat
    return "Other"


def error_sample(cfg: Config, run_file: str, n: int = 50, seed: int = 0, out_dir: str | None = None) -> dict:
    """Буруу хариулсан (хариулах боломжтой) асуултаас n-ийг санамсаргүй сонгож CSV (Excel-д нээгдэх) үүсгэнэ."""
    path = resolve_path(run_file)
    recs = [r for r in load_records(path) if r.get("category") == "answerable" and not r.get("correct")]
    items = {it.id: it for it in load_all_eval_items(cfg)}
    rng = random.Random(seed)
    sample = rng.sample(recs, min(n, len(recs)))
    rows = []
    cat_counts, mn_counts = Counter(), Counter()
    for r in sorted(sample, key=lambda x: x["question_id"]):
        it = items.get(r["question_id"])
        gold = it.gold_sql if it else None
        bugs, mn, detail = auto_tags(r, gold)
        for b in bugs:
            cat_counts[bug_category(b)] += 1
        mn_counts.update(mn)
        rows.append({"question_id": r["question_id"], "variant": r.get("variant"), "difficulty": r.get("difficulty"),
                     "question": r.get("question"), "gold_sql": gold, "pred_sql": r.get("sql"),
                     "error_stage": r.get("error_stage") or "wrong_result", "error": r.get("error"),
                     "auto_nl2sql_bugs": "; ".join(bugs), "auto_category": "; ".join(dict.fromkeys(
                         bug_category(b) for b in bugs)), "auto_mn_tags": "; ".join(mn), "auto_detail": detail,
                     "manual_nl2sql_bugs": "", "manual_mn_tags": "", "notes": ""})
    out = resolve_path(out_dir) if out_dir else resolve_path(cfg["eval"]["results_dir"]) / "errors"
    out.mkdir(parents=True, exist_ok=True)
    stem = f"{path.parent.name}__{path.stem}"
    csv_path = out / f"{stem}.csv"
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["question_id"])
        w.writeheader()
        w.writerows(rows)
    summary = {"run": str(run_file), "wrong_total": len(recs), "sampled": len(rows),
               "auto_category_counts": dict(cat_counts.most_common()), "auto_mn_tag_counts": dict(mn_counts.most_common()),
               "csv": str(csv_path)}
    (out / f"{stem}.summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def manual_summary(csv_file: str) -> dict:
    """Хүн шошголсон CSV-ээс (manual_* багана) эцсийн тархалт."""
    cats, mn = Counter(), Counter()
    with open(resolve_path(csv_file), encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            for b in filter(None, (x.strip() for x in (row.get("manual_nl2sql_bugs") or "").split(";"))):
                cats[bug_category(b) if b not in NL2SQL_BUGS else b] += 1
            mn.update(filter(None, (x.strip() for x in (row.get("manual_mn_tags") or "").split(";"))))
    return {"category_counts": dict(cats.most_common()), "mn_tag_counts": dict(mn.most_common()),
            "file": str(Path(csv_file))}
