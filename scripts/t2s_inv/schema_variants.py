"""S2: схемийн нэр сольсон хувилбар — Text2SQL загвар хүснэгт/баганын нэрийг цээжлэхгүй, prompt-ын схемийг (DDL + монгол/
англи comment) уншиж сэтгэх чадвартай болгох сургалтын өгөгдөл (docs/MT_INVARIANT_PLAN.md §6.2).

Нэг хувилбар (mapping) = хүснэгт ба баганын НЭРИЙН ГАНЦ ХУВААРИЛАЛТ (нэг баганын нэр бүх хүснэгтэд ижил нэрэнд орно — бодит DB-ийн
нэршлийн дүрэм шиг: branch_id → br_id хаа сайгүй). Утга нь comment-д (монгол/англи тайлбар, enum) хэвээр үлдэнэ.

Хэв маяг:  abbr (товчлол: loans→lns, principal_outstanding→prin_out)   syn (англи синоним: customers→clients)
           mn (монгол галиг: loans→zeel, branch_name→salbariin_ner)      opaque (t01.c001; утга нь comment-д)

Урсгал:  gold SQL ─(sqlglot AST: Table/Column/Alias идентификатор солих; t2s. угтварыг хасах)→ шинэ SQL
         ─(sqlglot qualify: шинэ схемийн эсрэг статик шалгалт)→ DDL-ийг нэрсэн сольсон semantic model-оос render_ddl-ээр.
Гүйцэтгэлийн баталгаажуулалт (PG дээр, эх gold ба шинэ SQL-ийн үр дүн таарах) → verify_variants.py (Kaggle/Colab-ийн PG-д;
локалд PG байхгүй). Eval-тэй давхцсан жишээг хасна (шударга байдлын дүрэм).

Ажиллуулах:  PYTHONPATH=src python scripts/t2s_inv/schema_variants.py [--variants 4] [--mappings-per-style 3] [--seed 7]
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "mt"))

import sqlglot  # noqa: E402
from sqlglot import exp  # noqa: E402
from sqlglot.errors import SqlglotError  # noqa: E402
from sqlglot.optimizer.qualify import qualify  # noqa: E402

from build_mt_v0 import EvalGuard, read_jsonl  # noqa: E402  (eval-guard-ийг дахин ашиглана)
from mn_text2sql.knowledge.semantic import Column, SemanticModel, Table, load_semantic_model  # noqa: E402
from mn_text2sql.llm.prompts import render_ddl  # noqa: E402
from mn_text2sql.text.normalize import STYLE_UOV, translit  # noqa: E402

OUT = ROOT / "data" / "t2s_inv" / "v0"
EXAMPLE_FILES = ["knowledge/examples.jsonl", "knowledge/examples_plus.jsonl", "knowledge/examples_plus2.jsonl"]
STYLES = ["abbr", "syn", "mn", "opaque"]

ABBR = {
    "id": "id", "date": "dt", "code": "cd", "amount": "amt", "name": "nm", "status": "st", "type": "tp",
    "customer": "cust", "currency": "ccy", "branch": "br", "rate": "rt", "principal": "prin", "loan": "ln",
    "interest": "int", "paid": "pd", "no": "no", "outstanding": "out", "product": "prod", "account": "acct",
    "term": "trm", "months": "mon", "penalty": "pen", "due": "due", "balance": "bal", "closed": "clsd",
    "collateral": "coll", "payment": "pmt", "total": "tot", "overdue": "od", "transaction": "txn", "count": "cnt",
    "score": "scr", "fee": "fee", "days": "d", "allocated": "alloc", "current": "cur", "opened": "opn",
    "number": "num", "category": "cat", "guarantor": "guar", "ratio": "rto", "application": "appl", "channel": "chn",
    "maturity": "mat", "past": "pst", "new": "nw", "old": "old", "value": "val", "action": "act", "employee": "emp",
    "last": "lst", "risk": "rsk", "level": "lvl", "income": "inc", "approved": "appr", "reason": "rsn",
    "classification": "clsf", "restructure": "rstr", "charge": "chg", "deposit": "dep", "province": "prov",
    "district": "dist", "department": "dept", "position": "pos", "manager": "mgr", "employment": "empl",
    "segment": "seg", "industry": "ind", "annual": "ann", "revenue": "rev", "requested": "req", "decision": "dec",
    "effective": "eff", "bucket": "bkt", "method": "mth", "recovered": "rec", "valuation": "valn",
    "guarantee": "gtee", "entry": "ent", "journal": "jnl", "history": "hist", "schedule": "sched", "collection": "coll",
    "case": "case", "monthly": "mth", "credit": "crd", "exchange": "fx", "alert": "alrt", "score": "scr",
    "organization": "org", "individual": "indv", "writeoff": "wo", "promise": "prm", "balances": "bals",
}
SYN = {
    "customer": "client", "branch": "office", "loan": "credit", "employee": "staff", "application": "request",
    "amount": "sum", "outstanding": "remaining", "product": "offering", "collateral": "pledge", "guarantor": "surety",
    "balance": "remainder", "term": "tenor", "penalty": "fine", "status": "state", "type": "kind", "payment": "repayment",
    "organization": "company", "individual": "person", "case": "matter", "action": "step", "alert": "flag",
}
# Монгол галигтай (латин үсгээр) нэршил: token тус бүрийг монгол үндэс болгоно — бодит монгол DB-ийн нэршил шиг
# (zeel_id, salbar_ner, undsen_uldegdel). Мэдэхгүй token (dti, mnt, ltv ...) англиар хэвээр.
MN_TOK = {
    "id": "id", "date": "ognoo", "code": "kod", "amount": "dun", "name": "ner", "status": "tolov", "type": "torol",
    "customer": "khariltsagch", "currency": "valyut", "branch": "salbar", "rate": "khuvi", "principal": "undsen",
    "loan": "zeel", "interest": "khuu", "paid": "tulsun", "no": "dugaar", "outstanding": "uldegdel",
    "product": "buteegdekhuun", "account": "dans", "term": "khugatsaa", "months": "sar", "penalty": "torguuli",
    "due": "tuluh", "balance": "uldegdel", "closed": "khaagdsan", "collateral": "bartsaa", "payment": "tulbur",
    "total": "niit", "overdue": "khetersen", "transaction": "guilgee", "count": "too", "score": "onoo", "max": "deed",
    "min": "door", "fee": "shimtgel", "days": "khonog", "allocated": "khuvaarilsan", "current": "odoogiin",
    "opened": "neesen", "number": "dugaar", "category": "angilal", "guarantor": "batlan_daagch", "ratio": "kharitsaa",
    "application": "khuselt", "channel": "suvag", "maturity": "duusakh", "past": "khetersen", "old": "khuuchin",
    "new": "shine", "value": "utga", "case": "keis", "action": "uildel", "employee": "ajiltan", "last": "suuliin",
    "risk": "ersdel", "level": "tuvshin", "income": "orlogo", "approved": "batlagdsan", "reason": "shaltgaan",
    "classification": "angilal", "restructure": "butsiin_uurchlult", "charge": "khuraamj", "deposit": "khadgalamj",
    "alert": "sanuulga", "province": "aimag", "district": "duureg", "active": "idevkhtei", "first": "ankh",
    "department": "kheltes", "position": "albantushaal", "manager": "menejer", "employment": "ajil", "year": "jil",
    "annual": "jiliin", "revenue": "orlogo", "requested": "khusesen", "decision": "shiidver", "effective": "khuchintei",
    "bucket": "buleg", "method": "arga", "writeoff": "khasalt", "recovered": "sergeesen", "valuation": "unelgee",
    "guarantee": "batalgaa", "valid": "khuchintei", "called": "duudsan", "closing": "khaalt", "stage": "shat",
    "open": "nee", "promised": "amlasan", "model": "model", "grade": "zereglel", "rule": "durem", "entry": "burtgel",
    "journal": "jurnal", "history": "tuukh", "schedule": "khuvaari", "collection": "baraguulalt", "exchange": "solikh",
    "organization": "baiguullaga", "individual": "khuvi_khun", "credit": "zeeliin", "agency": "agentlag",
    "monthly": "sar_buriin", "promise": "amlalt", "segment": "segment", "industry": "uildverlel",
}


def _mn_tok(word: str) -> str:
    for w in (word, word[:-1] if word.endswith("s") else None, word[:-2] if word.endswith("es") else None,
              word[:-3] + "y" if word.endswith("ies") else None):
        if w and w in MN_TOK:
            return MN_TOK[w]
    return word


RESERVED = {"all", "analyse", "analyze", "and", "any", "array", "as", "asc", "asymmetric", "both", "case", "cast", "check",
            "collate", "column", "constraint", "create", "current_date", "current_time", "default", "desc", "distinct",
            "do", "else", "end", "except", "false", "fetch", "for", "foreign", "from", "grant", "group", "having", "in",
            "initially", "intersect", "into", "leading", "limit", "not", "null", "offset", "on", "only", "or", "order",
            "primary", "references", "select", "some", "symmetric", "table", "then", "to", "trailing", "true", "union",
            "unique", "user", "using", "when", "where", "window", "with", "key", "level", "rank", "role", "type"}


# ----------------------------------------------------------------------------- нэр үүсгэх
def _plural(w: str) -> str:
    if w in {"staff", "fee", "id"}:
        return w if w == "staff" else w + "s"
    if w == "person":
        return "people"
    if w.endswith("y") and w[-2:-1] not in "aeiou":
        return w[:-1] + "ies"
    return w + ("es" if w.endswith(("s", "x", "ch", "sh")) else "s")


def _tok(word: str, table: dict[str, str]) -> str:
    if word in table:
        return table[word]
    if word.endswith("ies") and word[:-3] + "y" in table:
        return _plural(table[word[:-3] + "y"])
    if word.endswith("es") and word[:-2] in table:
        return _plural(table[word[:-2]])
    if word.endswith("s") and word[:-1] in table:
        return _plural(table[word[:-1]])
    return word


def _short(word: str) -> str:
    return word if len(word) <= 4 else word[0] + re.sub(r"[aeiou]", "", word[1:])[:4]


def make_name(old: str, style: str, mn_head: str, idx: int, kind: str) -> str:
    if style == "abbr":
        parts = [_tok(w, ABBR) if _tok(w, ABBR) != w else _short(w) for w in old.split("_")]
        return "_".join(parts)
    if style == "syn":
        return "_".join(_tok(w, SYN) for w in old.split("_"))
    if style == "mn":
        return "_".join(_mn_tok(w) for w in old.split("_"))
    if style == "opaque":
        return f"{'t' if kind == 'table' else 'c'}{idx:03d}"
    raise ValueError(style)


def _fix(name: str, taken: set[str]) -> str:
    name = re.sub(r"[^a-z0-9_]", "", name.lower()) or "x"
    if name[0].isdigit() or name in RESERVED:
        name = "x_" + name
    base, k = name, 2
    while name in taken:
        name, k = f"{base}_{k}", k + 1
    taken.add(name)
    return name


def _head(mn: str) -> str:
    return re.split(r"\s*[(:;,]", str(mn or ""))[0].strip()


def build_mapping(model: SemanticModel, style: str, rng: random.Random, map_id: str) -> dict:
    """{map_id, style, table_map, col_map, col_order}: col_map нь бүх хүснэгтэд нийтлэг (нэг баганын нэр → нэг шинэ нэр)."""
    tmap, taken_t = {}, set()
    for i, (t, tab) in enumerate(sorted(model.tables.items()), 1):
        tmap[t] = _fix(make_name(t, style, _head(tab.mn), i, "table"), taken_t)
    # баганын нэр: БҮХ хүснэгтэд нийтлэг, ГЛОБАЛ давтагдашгүй (injective) → урвуу mapping-аар SQL-ийг буцааж шалгах боломжтой
    first_mn: dict[str, str] = {}
    for tab in model.tables.values():
        for c, col in tab.columns.items():
            first_mn.setdefault(c, _head(col.mn))
    cmap: dict[str, str] = {}
    taken_c: set[str] = set()
    for i, c in enumerate(sorted(first_mn), 1):
        cmap[c] = _fix(make_name(c, style, first_mn[c], i, "col"), taken_c)
    order = {t: rng.sample(list(tab.columns), len(tab.columns)) if rng.random() < 0.5 else list(tab.columns)
             for t, tab in model.tables.items()}
    return {"map_id": map_id, "style": style, "table_map": tmap, "col_map": cmap, "col_order": order}


# ----------------------------------------------------------------------------- semantic model-ийг нэр сольж хуулах
def rename_model(model: SemanticModel, mp: dict) -> SemanticModel:
    tm, cm = mp["table_map"], mp["col_map"]
    tables: dict[str, Table] = {}
    for t, tab in model.tables.items():
        nt = tm[t]
        cols: dict[str, Column] = {}
        for c in mp["col_order"][t]:
            col = tab.columns[c]
            fk = None
            if col.fk:
                ft, fc = col.fk.split(".")
                fk = f"{tm[ft]}.{cm[fc]}"
            cols[cm[c]] = Column(table=nt, name=cm[c], mn=col.mn, en=col.en, syn=list(col.syn), values=dict(col.values),
                                 pii=col.pii, fk=fk, type=col.type, examples=[])
        tables[nt] = Table(name=nt, mn=tab.mn, en=tab.en, syn=list(tab.syn), pk=[cm[p] for p in tab.pk], columns=cols)
    return SemanticModel(schema=model.schema, tables=tables, notes_mn=list(model.notes_mn))


# ----------------------------------------------------------------------------- SQL дахин бичих + статик шалгалт
def rewrite_sql(sql: str, mp: dict, schema_name: str = "t2s") -> str:
    tm, cm = mp["table_map"], mp["col_map"]
    tree = sqlglot.parse_one(sql, read="postgres")
    aliases = {a.alias for a in tree.find_all(exp.TableAlias)} | {a.alias for a in tree.find_all(exp.Alias)}
    for ident in list(tree.find_all(exp.Identifier)):
        p, name = ident.parent, ident.name
        if isinstance(p, exp.Table):
            if p.args.get("this") is ident and name in tm:
                ident.set("this", tm[name])
            elif p.args.get("db") is ident and name == schema_name:
                p.set("db", None)
        elif isinstance(p, exp.Column):
            if p.args.get("this") is ident:
                if name in cm:
                    ident.set("this", cm[name])
            elif p.args.get("table") is ident and name in tm and name not in aliases:
                ident.set("this", tm[name])
        elif isinstance(p, exp.Alias) and p.args.get("alias") is ident:
            if name in cm:
                ident.set("this", cm[name])
        elif isinstance(p, exp.TableAlias) and p.args.get("this") is ident:
            if name in tm:
                ident.set("this", tm[name])
    return tree.sql(dialect="postgres")


def inverse(mp: dict) -> dict:
    return {"table_map": {v: k for k, v in mp["table_map"].items()}, "col_map": {v: k for k, v in mp["col_map"].items()}}


def normalize(sql: str, schema_name: str = "t2s") -> str:
    return _strip_db(sqlglot.parse_one(sql, read="postgres"), schema_name).sql(dialect="postgres")


def _strip_db(tree: exp.Expression, schema_name: str = "t2s") -> exp.Expression:
    for t in tree.find_all(exp.Table):
        db = t.args.get("db")
        if db is not None and db.name == schema_name:
            t.set("db", None)
    return tree


def validate(sql: str, schema: dict[str, dict[str, str]]) -> str | None:
    """sqlglot qualify-аар үл мэдэгдэх хүснэгт/баганыг илрүүлнэ. None = зөв, эс бөгөөс алдааны мессеж."""
    try:
        tree = _strip_db(sqlglot.parse_one(sql, read="postgres"))
        cte = {c.alias for c in tree.find_all(exp.CTE)}
        for t in tree.find_all(exp.Table):   # qualify нь үл мэдэгдэх ХҮСНЭГТийг үл тоодог тул өөрөө шалгана
            if isinstance(t.this, exp.Identifier) and t.name not in schema and t.name not in cte:
                return f"UnknownTable: {t.name}"
        qualify(tree, schema=schema, dialect="postgres", validate_qualify_columns=True, identify=False,
                quote_identifiers=False, expand_stars=False)
        return None
    except (SqlglotError, KeyError, ValueError, RecursionError) as e:
        return f"{type(e).__name__}: {str(e)[:160]}"


def tables_in(sql: str) -> list[str]:
    tree = sqlglot.parse_one(sql, read="postgres")
    cte = {c.alias for c in tree.find_all(exp.CTE)}
    out = []
    for t in tree.find_all(exp.Table):
        if t.name not in cte and t.name not in out:
            out.append(t.name)
    return out


# ----------------------------------------------------------------------------- DDL ба DB-ийн view (баталгаажуулалтад)
def pick_tables(model_new: SemanticModel, used: list[str], rng: random.Random, total: int = 6) -> list[str]:
    tabs = list(used)
    for u in list(tabs):
        for n in sorted(model_new.neighbors(u)):
            if n not in tabs and len(tabs) < total:
                tabs.append(n)
    rest = [t for t in model_new.tables if t not in tabs]
    rng.shuffle(rest)
    while len(tabs) < min(total, len(model_new.tables)) and rest:
        tabs.append(rest.pop())
    rng.shuffle(tabs)
    return tabs


def views_sql(model: SemanticModel, mp: dict) -> str:
    sch = f"t2s_{mp['map_id']}"
    lines = [f"DROP SCHEMA IF EXISTS {sch} CASCADE;", f"CREATE SCHEMA {sch};"]
    for t, tab in model.tables.items():
        cols = ", ".join(f"{c} AS {mp['col_map'][c]}" for c in tab.columns)
        lines.append(f"CREATE VIEW {sch}.{mp['table_map'][t]} AS SELECT {cols} FROM t2s.{t};")
    lines.append(f"GRANT USAGE ON SCHEMA {sch} TO t2s_bot; GRANT SELECT ON ALL TABLES IN SCHEMA {sch} TO t2s_bot;")
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------------- гол
def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", type=int, default=4, help="жишээ бүрт хэдэн хувилбар")
    ap.add_argument("--mappings-per-style", type=int, default=3)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--with-ddl", action="store_true",
                    help="DDL-ийг бүх мөрд оруулна (~40 МБ); үгүй бол зөвхөн preview.jsonl-д (хувилбар бүрээс жишээ)")
    args = ap.parse_args()
    rng = random.Random(args.seed)

    model = load_semantic_model()
    types = json.loads((ROOT / "build/index/column_examples.json").read_text(encoding="utf-8")).get("__types__", {})
    schema_orig = {t: {c: types.get(f"{t}.{c}", "text") for c in tab.columns} for t, tab in model.tables.items()}
    guard = EvalGuard()

    stats: Counter = Counter()
    base = []
    for f in EXAMPLE_FILES:
        for d in read_jsonl(ROOT / f):
            if not d.get("gold_sql"):
                continue
            stats["examples"] += 1
            if (guard.hit("mn", d["question_mn"]) or guard.hit("en", d["question_en"])
                    or any(h["ratio"] >= 0.5 for h in guard.substring_hits({d["id"]: [d["question_mn"], d["question_en"]]}))):
                stats["dropped_eval_overlap"] += 1
                continue
            err = validate(d["gold_sql"], schema_orig)
            if err:
                stats["orig_unverifiable"] += 1
                stats[f"orig_err:{err.split(':')[0]}"] += 1
                continue
            base.append(d)
            if args.limit and len(base) >= args.limit:
                break
        if args.limit and len(base) >= args.limit:
            break
    stats["usable_examples"] = len(base)

    mappings = []
    for style in STYLES:
        for k in range(args.mappings_per_style):
            mappings.append(build_mapping(model, style, rng, f"{style[:2]}{k + 1:02d}"))
    new_models = {m["map_id"]: rename_model(model, m) for m in mappings}
    new_schemas, new_types = {}, {}
    for m in mappings:
        ty, sch = {}, {}
        for t, tab in model.tables.items():
            nt = m["table_map"][t]
            sch[nt] = {}
            for c in tab.columns:
                nc = m["col_map"][c]
                ty[f"{nt}.{nc}"] = types.get(f"{t}.{c}", "")
                sch[nt][nc] = types.get(f"{t}.{c}", "text") or "text"
        new_types[m["map_id"]], new_schemas[m["map_id"]] = ty, sch

    rows, preview = [], []
    for d in base:
        picks = rng.sample(mappings, min(args.variants, len(mappings)))
        for mp in picks:
            stats["attempts"] += 1
            try:
                new_sql = rewrite_sql(d["gold_sql"], mp)
            except SqlglotError as e:
                stats["rewrite_error"] += 1
                continue
            err = validate(new_sql, new_schemas[mp["map_id"]])
            if err:
                stats["new_invalid"] += 1
                stats[f"new_err:{err.split(':')[0]}"] += 1
                continue
            if rewrite_sql(new_sql, inverse(mp), schema_name="") != normalize(d["gold_sql"]):
                stats["roundtrip_mismatch"] += 1   # урвуу нэр солилтоор эх SQL буцаж гарахгүй → хувилбарыг хасна
                stats[f"roundtrip_mismatch:{mp['style']}"] += 1
                continue
            used = tables_in(new_sql)
            nm = new_models[mp["map_id"]]
            rec = {"id": f"{d['id']}@{mp['map_id']}", "src_id": d["id"], "mapping": mp["map_id"], "style": mp["style"],
                   "domain": d.get("domain"), "family": d.get("family"), "question_mn": d["question_mn"],
                   "question_en": d["question_en"], "sql_orig": d["gold_sql"], "sql": new_sql, "tables": used,
                   "order_matters": bool(d.get("order_matters", False))}
            tabs = pick_tables(nm, used, rng)
            rec["ddl_tables"] = tabs
            if args.with_ddl:
                rec["ddl"] = render_ddl(nm, tabs, new_types[mp["map_id"]], {}, True)
            elif sum(1 for p in preview if p["mapping"] == mp["map_id"]) < 3:
                preview.append(dict(rec, ddl=render_ddl(nm, tabs, new_types[mp["map_id"]], {}, True)))
            rows.append(rec)
            stats["ok"] += 1
            stats[f"ok:{mp['style']}"] += 1

    OUT.mkdir(parents=True, exist_ok=True)
    with open(OUT / "schema_variants.jsonl", "w", encoding="utf-8", newline="\n") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(OUT / "preview.jsonl", "w", encoding="utf-8", newline="\n") as fh:
        for r in preview:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(OUT / "mappings.jsonl", "w", encoding="utf-8", newline="\n") as fh:
        for m in mappings:
            fh.write(json.dumps(m, ensure_ascii=False) + "\n")
    for m in mappings:
        (OUT / f"views_{m['map_id']}.sql").write_text(views_sql(model, m), encoding="utf-8", newline="\n")
    manifest = {"created": datetime.now().isoformat(timespec="seconds"), "seed": args.seed, "args": vars(args),
                "stats": dict(stats), "mappings": [{"map_id": m["map_id"], "style": m["style"]} for m in mappings],
                "sha256": {"schema_variants.jsonl": hashlib.sha256((OUT / "schema_variants.jsonl").read_bytes()).hexdigest()},
                "note": "Гүйцэтгэлийн баталгаажуулалт (PG) хийгдээгүй — verify_variants.py-г Kaggle/Colab-д ажиллуулна."}
    (OUT / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(manifest["stats"], ensure_ascii=False, indent=1))
    for style in STYLES:
        r = next((x for x in rows if x["style"] == style and len(x["sql"]) > 90), None)
        if r:
            print(f"\n--- {style}: {r['id']}\nэх :", r["sql_orig"], "\nшинэ:", r["sql"])


if __name__ == "__main__":
    main()
