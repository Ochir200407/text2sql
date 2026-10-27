"""T-2 (схем-солилт) eval: dev (эсвэл test) асуултын gold SQL-ийг СУРГАЛТАД ОГТ ОРООГҮЙ 4 нэршлийн хэв маягаар дахин бичнэ.
docs/MT_INVARIANT_PLAN.md §6.4 T-2.

Сургалтын хэв маяг (schema_variants.py: abbr, syn, mn, opaque) нь seed-ээс үл хамаарах детерминист нэр өгдөг тул eval-д
шинэ хэв маяг хэрэгтэй:
  syn2  — өөр англи синоним (loan → facility, customer → borrower, …)
  short — бүх үгийг эгшиггүй товчлол (ABBR толь ашиглахгүй) + хүснэгтэд `tb_` угтвар
  mn2   — монгол галигийн өөр бичлэг (kh → h, ts → c) + 6-аас урт үгийг 5 үсгээр (zeel_husel, haril, undse_ulded)
  opq2  — санамсаргүй код (хүснэгт e01…, багана a001…; сургалтын t###/c###-ээс өөр, дараалал холилдсон)
Eval-ийн текстийг хуулахгүй — зөвхөн id, mapping, дахин бичсэн gold SQL, DDL-ийн хүснэгтүүд (асуултыг evalset/-ээс уншина).
Гаралт: evalset_shift/<split>/{mappings.jsonl, items.jsonl, views_<map>.sql, MANIFEST.json} (data/-аас тусдаа — сургалтын glob-д орохгүй).

Ажиллуулах:  PYTHONPATH=src python scripts/t2s_inv/make_shift_eval.py [--split dev] [--seed 2026]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "mt"))

from schema_variants import (  # noqa: E402
    MN_TOK,
    SqlglotError,
    _fix,
    _short,
    _tok,
    inverse,
    load_semantic_model,
    normalize,
    pick_tables,
    rewrite_sql,
    tables_in,
    validate,
    views_sql,
)

STYLES = ["syn2", "short", "mn2", "opq2"]
SYN2 = {
    "customer": "borrower", "branch": "outlet", "loan": "facility", "employee": "worker", "application": "proposal",
    "amount": "money", "outstanding": "unpaid", "product": "plan", "collateral": "security", "guarantor": "cosigner",
    "balance": "holding", "term": "duration", "penalty": "surcharge", "status": "condition", "type": "variety",
    "payment": "remittance", "organization": "firm", "individual": "natural_person", "case": "file", "action": "activity",
    "alert": "warning", "deposit": "saving", "account": "ledger", "transaction": "movement", "date": "day",
    "id": "key", "name": "title", "code": "ref", "principal": "capital", "rate": "pct", "paid": "settled", "due": "owed",
    "closed": "ended", "opened": "started", "total": "overall", "overdue": "late", "score": "rating", "category": "class",
    "channel": "route", "risk": "exposure", "level": "tier", "reason": "cause", "decision": "verdict", "income": "earnings",
    "employment": "job", "district": "area", "province": "region", "currency": "money_unit", "number": "ref_no",
    "days": "day_count", "interest": "yield", "past": "behind", "count": "tally", "value": "worth",
}


def mn2(word: str) -> str:
    """Монгол галиг, өөр бичлэг: kh → h, ts → c (zeel_khuselt → zeel_huselt)."""
    for w in (word, word[:-1] if word.endswith("s") else None):
        if w and w in MN_TOK:
            t = MN_TOK[w].replace("kh", "h").replace("ts", "c")
            return "_".join(x[:5] if len(x) > 6 else x for x in t.split("_"))   # урт үгийг товчлох (harilcagch → haril)
    return word


def make_name(old: str, style: str, kind: str, code: str) -> str:
    if style == "syn2":
        return "_".join(_tok(w, SYN2) for w in old.split("_"))
    if style == "short":
        return ("tb_" if kind == "table" else "") + "_".join(_short(w) for w in old.split("_"))
    if style == "mn2":
        return "_".join(mn2(w) for w in old.split("_"))
    if style == "opq2":
        return code
    raise ValueError(style)


def build_mapping(model, style: str, rng: random.Random, map_id: str) -> dict:
    tables = sorted(model.tables)
    tcodes = [f"e{i:02d}" for i in rng.sample(range(10, 100), len(tables))]
    tmap, taken = {}, set()
    for t, code in zip(tables, tcodes, strict=True):
        tmap[t] = _fix(make_name(t, style, "table", code), taken)
    cols = sorted({c for tab in model.tables.values() for c in tab.columns})
    ccodes = [f"a{i:03d}" for i in rng.sample(range(100, 1000), len(cols))]
    cmap, taken_c = {}, set()
    for c, code in zip(cols, ccodes, strict=True):
        cmap[c] = _fix(make_name(c, style, "col", code), taken_c)
    order = {t: rng.sample(list(tab.columns), len(tab.columns)) for t, tab in model.tables.items()}
    return {"map_id": map_id, "style": style, "table_map": tmap, "col_map": cmap, "col_order": order}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", default="dev", choices=["dev", "test"])
    ap.add_argument("--seed", type=int, default=2026)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    model = load_semantic_model()
    types = json.loads((ROOT / "build/index/column_examples.json").read_text(encoding="utf-8")).get("__types__", {})
    out = ROOT / "evalset_shift" / args.split
    out.mkdir(parents=True, exist_ok=True)
    maps = [build_mapping(model, s, rng, f"x{s}") for s in STYLES]
    # сургалтын mapping-тай нэр давхцал (хүснэгт/баганын шинэ нэр) — бага байх ёстой
    train_names = set()
    for line in (ROOT / "data/t2s_inv/v0/mappings.jsonl").read_text(encoding="utf-8").splitlines():
        m = json.loads(line)
        tm = m["table_map"] if isinstance(m["table_map"], dict) else eval(m["table_map"])  # noqa: S307 (өөрийн файл)
        cm = m["col_map"] if isinstance(m["col_map"], dict) else eval(m["col_map"])  # noqa: S307
        train_names |= set(tm.values()) | set(cm.values())
    orig_names = set(model.tables) | {c for tab in model.tables.values() for c in tab.columns}
    overlap = {}
    for m in maps:
        names = set(m["table_map"].values()) | set(m["col_map"].values())
        changed = {n for n in names if n not in orig_names}
        overlap[m["map_id"]] = {"names": len(names), "same_as_original": len(names - changed),
                                "seen_in_training": len(changed & train_names)}
    schema_orig = {t: {c: types.get(f"{t}.{c}", "text") for c in tab.columns} for t, tab in model.tables.items()}
    new_schemas = {m["map_id"]: {m["table_map"][t]: {m["col_map"][c]: s for c, s in cols.items()}
                                 for t, cols in schema_orig.items()} for m in maps}
    stats: Counter = Counter()
    items = []
    for line in (ROOT / f"evalset/{args.split}.jsonl").read_text(encoding="utf-8").splitlines():
        d = json.loads(line)
        if not d.get("gold_sql"):
            continue
        stats["answerable"] += 1
        if validate(d["gold_sql"], schema_orig):
            stats["orig_unverifiable"] += 1
            continue
        used = [t for t in tables_in(normalize(d["gold_sql"])) if t in model.tables]
        tabs = pick_tables(model, used, random.Random(f"{args.seed}:{d['id']}"))
        rec = {"id": d["id"], "order_matters": bool(d.get("order_matters")), "tables_orig": tabs, "gold": {"orig": d["gold_sql"]}}
        for m in maps:
            try:
                new = rewrite_sql(d["gold_sql"], m)
            except SqlglotError:
                stats[f"rewrite_error:{m['style']}"] += 1
                continue
            if validate(new, new_schemas[m["map_id"]]) or \
                    rewrite_sql(new, inverse(m), schema_name="") != normalize(d["gold_sql"]):
                stats[f"invalid:{m['style']}"] += 1
                continue
            rec["gold"][m["map_id"]] = new
            stats[f"ok:{m['style']}"] += 1
        items.append(rec)
    with open(out / "items.jsonl", "w", encoding="utf-8", newline="\n") as fh:
        for r in items:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(out / "mappings.jsonl", "w", encoding="utf-8", newline="\n") as fh:
        for m in maps:
            fh.write(json.dumps(m, ensure_ascii=False) + "\n")
    for m in maps:
        (out / f"views_{m['map_id']}.sql").write_text(views_sql(model, m), encoding="utf-8", newline="\n")
    man = {"created": datetime.now().isoformat(timespec="seconds"), "split": args.split, "seed": args.seed,
           "items": len(items), "stats": dict(stats), "name_overlap": overlap,
           "sha256_items": hashlib.sha256((out / "items.jsonl").read_bytes()).hexdigest()}
    (out / "MANIFEST.json").write_text(json.dumps(man, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(man, ensure_ascii=False, indent=1))
    for m in maps:
        print(m["map_id"], {k: m["table_map"][k] for k in ("loans", "customers", "loan_applications")},
              {k: m["col_map"][k] for k in ("principal_outstanding", "branch_name", "days_past_due") if k in m["col_map"]})


if __name__ == "__main__":
    main()
