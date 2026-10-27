"""Thesis benchmark: text2sql-thesis-ийн 150 кейсийг шинэ систем дээр ажиллуулах орчин (docs/THESIS_BENCH.md).

text2sql-thesis-д юу ч бичихгүй — зөвхөн уншина:
  build/data.db (цахим худалдааны SQLite DB), schema/tables/*.yaml (тайлбар), resources/glossary.yaml (монгол
  нэр томьёо → хүснэгт/багана/утга), evals/cases/*.json (150 кейс, SQLite gold).

Гаралт (шинэ системийн дотор):
  data/thesis/ecom.sqlite                  data.db-ийн хуулбар — thesis-ийн SQLite таамгийг ижил шалгуураар оноолоход
  PostgreSQL DB `thesis_ecom`, схем t2s    шинэ системийн guard / executor / retrieval ажиллах орчин (ижил өгөгдөл)
  knowledge/thesis/semantic_model.yaml     schema/*.yaml + glossary.yaml-ийн хүснэгт/багана/утгын синоним
  knowledge/thesis/glossary.yaml           хоосон (thesis-д бизнес томьёо байхгүй; B3 = ямар ч нөлөөгүй)
  knowledge/thesis/examples.jsonl          thesis-ийн train split (29) — few-shot сан (dev/test орохгүй)
  data/thesis/dev.jsonl, test.jsonl        EvalItem формат; gold = SQLite gold-оос PostgreSQL руу хөрвүүлж, ХОЁР DB дээрх
                                           үр дүн нь тэнцүү гэж баталгаажсан SQL (gold_overrides.yaml-аар гар засвар)
  data/thesis/MANIFEST.json                эх файлын sha1, хөрвүүлэлтийн тайлан, хасагдсан кейс

thesis-score: thesis-ийн eval-ийн JSON (results/*.json, кейс бүрийн pred_sql) → SQLite дээр гүйцэтгэж, SQLite gold-той
шинэ системийн шалгуураар (eval/compare.results_match) харьцуулна → results/thesis/runs/<split>/EXT-<name>/... (шинэ
системийн бичлэгийн формат) — `t2s compare`, `report`-оор шууд харьцуулна.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import sqlglot
import yaml
from sqlglot import exp

from ..config import Config, resolve_path
from ..db.conn import connection, with_dbname
from ..db.executor import ExecResult
from ..eval.compare import results_match
from ..text.normalize import STYLE_VUW, translit

log = logging.getLogger(__name__)

DATA_DIR = "data/thesis"
KNOW_DIR = "knowledge/thesis"
DIFF_MAP = {"simple": "easy", "moderate": "medium", "challenging": "hard"}
PG_TYPES = {"BIGINT": "bigint", "INTEGER": "integer", "REAL": "numeric", "DATE": "date", "TIMESTAMP": "timestamp",
            "BOOLEAN": "boolean"}
PII_COLUMNS = {"email", "phone", "street", "contact_email"}


def _sha1(p: Path) -> str:
    return hashlib.sha1(p.read_bytes()).hexdigest()[:12]


def _pg_type(sqlite_type: str) -> str:
    t = (sqlite_type or "").upper().strip()
    if t in PG_TYPES:
        return PG_TYPES[t]
    m = re.fullmatch(r"VARCHAR\((\d+)\)", t)
    if m:
        return f"varchar({m.group(1)})"
    m = re.fullmatch(r"DECIMAL\((\d+),\s*(\d+)\)", t)
    if m:
        return f"numeric({m.group(1)},{m.group(2)})"
    return "text"


def _q(ident: str) -> str:
    return '"' + ident.replace('"', '""') + '"'


# ---------------------------------------------------------------------------
# 1. Эх файлууд
# ---------------------------------------------------------------------------

def load_cases(src: Path) -> list[dict]:
    cases = []
    for f in sorted((src / "evals" / "cases").glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        cases += d if isinstance(d, list) else d.get("cases", [])
    return cases


def sqlite_schema(db: Path) -> dict[str, list[tuple[str, str]]]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        out = {}
        for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                                 "ORDER BY rowid"):
            out[t] = [(c[1], c[2]) for c in conn.execute(f"PRAGMA table_info({_q(t)})")]
        return out
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 2. PostgreSQL хуулбар (схем t2s, bot role зөвхөн SELECT)
# ---------------------------------------------------------------------------

def load_postgres(cfg: Config, db: Path, schema_yaml: dict, overwrite: bool) -> dict:
    from psycopg2.extras import execute_values

    from ..db.loader import create_database
    name = cfg["db"]["name"]
    create_database(cfg, name, drop=overwrite)
    tables = sqlite_schema(db)
    src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    counts = {}
    bot, pw = cfg["db"]["bot_role"], cfg["db"]["bot_password"].replace("'", "''")
    with connection(with_dbname(cfg["db"]["admin_dsn"], name)) as conn, conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS t2s CASCADE; CREATE SCHEMA t2s;")
        for t, cols in tables.items():
            pk = (schema_yaml.get(t) or {}).get("primary_key") or []
            ddl = ", ".join(f"{_q(c)} {_pg_type(ty)}" for c, ty in cols)
            if pk:
                ddl += f", PRIMARY KEY ({', '.join(_q(c) for c in pk)})"
            cur.execute(f"CREATE TABLE t2s.{_q(t)} ({ddl})")
            bool_idx = [i for i, (_, ty) in enumerate(cols) if ty.upper() == "BOOLEAN"]
            rows = src.execute(f"SELECT {', '.join(_q(c) for c, _ in cols)} FROM {_q(t)}").fetchall()
            if bool_idx:
                rows = [tuple(bool(v) if i in bool_idx and v is not None else v for i, v in enumerate(r)) for r in rows]
            if rows:
                execute_values(cur, f"INSERT INTO t2s.{_q(t)} VALUES %s", rows, page_size=5000)
            for c, _ in cols:
                if c.endswith("_id") and c not in pk:
                    cur.execute(f"CREATE INDEX ON t2s.{_q(t)} ({_q(c)})")
            counts[t] = len(rows)
        cur.execute("CREATE OR REPLACE FUNCTION t2s.business_date() RETURNS date LANGUAGE sql IMMUTABLE AS "
                    f"$$ SELECT DATE '{cfg['project']['as_of_date']}' $$")
        cur.execute(f"""DO $$ BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{bot}') THEN
              CREATE ROLE {bot} LOGIN PASSWORD '{pw}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
            END IF; END $$;""")
        cur.execute(f"ALTER ROLE {bot} SET default_transaction_read_only = on")
        cur.execute(f"ALTER ROLE {bot} SET search_path = t2s")
        cur.execute(f'REVOKE CREATE ON SCHEMA public FROM PUBLIC; GRANT CONNECT ON DATABASE "{name}" TO {bot}; '
                    f"GRANT USAGE ON SCHEMA t2s TO {bot}; GRANT SELECT ON ALL TABLES IN SCHEMA t2s TO {bot}; "
                    f"GRANT EXECUTE ON FUNCTION t2s.business_date() TO {bot};")
        cur.execute("ANALYZE")
    src.close()
    return counts


# ---------------------------------------------------------------------------
# 3. Мэдлэгийн давхарга: semantic model (thesis-ийн schema YAML + glossary-ийн синоним)
# ---------------------------------------------------------------------------

def build_semantic_model(cfg: Config, schema_yaml: dict, glossary: list[dict], tables: dict) -> dict:
    syn_t: dict[str, list[str]] = {}
    syn_c: dict[tuple[str, str], list[str]] = {}
    val_en: dict[tuple[str, str], dict[str, str]] = {}
    for g in glossary:
        kind, target, mn = g.get("kind"), g.get("target") or "", str(g.get("mn") or "")
        if kind == "table" and target:
            syn_t.setdefault(target, []).append(mn)
        elif kind == "column" and "." in target:
            syn_c.setdefault(tuple(target.split(".", 1)), []).append(mn)
        elif kind == "value" and "." in target:
            val_en.setdefault(tuple(target.split(".", 1)), {})[mn] = str(g.get("en") or mn)
    out_tables = {}
    for t, cols in tables.items():
        y = schema_yaml.get(t) or {}
        ycols = {c["name"]: c for c in y.get("columns") or []}
        columns = {}
        for c, _ in cols:
            yc = ycols.get(c, {})
            spec: dict = {"mn": yc.get("desc_mn", ""), "en": yc.get("desc_en", "")}
            if syn_c.get((t, c)):
                spec["syn"] = sorted(set(syn_c[(t, c)]))
            values = dict.fromkeys(yc.get("enum_values") or [])
            values.update(val_en.get((t, c), {}))
            if values:
                spec["values"] = {str(k): (v if v is not None else str(k)) for k, v in values.items()}
            if yc.get("fk"):
                spec["fk"] = yc["fk"]
            if c in PII_COLUMNS:
                spec["pii"] = True
            columns[c] = spec
        out_tables[t] = {"mn": y.get("description_mn", ""), "en": y.get("description_en", ""),
                         "syn": sorted(set(syn_t.get(t, []))), "pk": y.get("primary_key") or [], "columns": columns}
    return {"version": 1, "schema": "t2s", "dialect": "postgresql",
            "notes_mn": [f"Өгөгдлийн 'өнөөдөр' = t2s.business_date() = {cfg['project']['as_of_date']} "
                         "(өгөгдөл 2023-01-01 … 2025-12-31). CURRENT_DATE-ийг ч ашиглаж болно.",
                         "Мөнгөн дүн бүгд төгрөгөөр (MNT)."],
            "tables": out_tables}


# ---------------------------------------------------------------------------
# 4. Gold: SQLite → PostgreSQL (sqlglot + энэ схемд зориулсан засвар), хоёр DB дээр үр дүнгээр баталгаажуулна
# ---------------------------------------------------------------------------

def sqlite_gold_to_pg(sql: str, bool_cols: set[str], date_cols: set[str]) -> str:
    tree = sqlglot.parse_one(sql, read="sqlite")
    for node in list(tree.find_all(exp.Anonymous)):
        if node.name.lower() == "julianday" and node.expressions:
            arg = node.expressions[0].sql(dialect="postgres")
            node.replace(sqlglot.parse_one(f"(EXTRACT(EPOCH FROM CAST({arg} AS TIMESTAMP)) / 86400.0)",
                                           read="postgres"))
    for node in list(tree.find_all(exp.EQ, exp.NEQ)):
        col, lit = node.this, node.expression
        if isinstance(lit, exp.Column):
            col, lit = lit, col
        if isinstance(col, exp.Column) and col.name in bool_cols and isinstance(lit, exp.Literal) \
                and lit.this in ("0", "1"):
            lit.replace(exp.Boolean(this=lit.this == "1"))
    for agg in list(tree.find_all(exp.Sum, exp.Avg)):
        if isinstance(agg.this, exp.Column) and agg.this.name in bool_cols:
            agg.this.replace(exp.Cast(this=agg.this.copy(), to=exp.DataType.build("INT")))
    for node in list(tree.find_all(exp.Like, exp.Substring)):
        if isinstance(node.this, exp.Column) and node.this.name in date_cols:
            node.this.replace(exp.Cast(this=node.this.copy(), to=exp.DataType.build("TEXT")))
    return tree.sql(dialect="postgres")


def qualify_t2s(sql: str) -> str:
    """Хүснэгтийн нэрийг t2s.-ээр (шинэ системийн gold-ийн хэлбэр; admin холболтын search_path-аас үл хамаарна)."""
    tree = sqlglot.parse_one(sql, read="postgres")
    ctes = {c.alias_or_name for c in tree.find_all(exp.CTE)}
    for t in tree.find_all(exp.Table):
        if t.name and not t.args.get("db") and t.name not in ctes:
            t.set("db", exp.to_identifier("t2s"))
    return tree.sql(dialect="postgres")


def run_sqlite(conn: sqlite3.Connection, sql: str, timeout_s: float = 60.0) -> ExecResult:
    """SQLite read-only гүйцэтгэл, хугацааны хязгаартай (thesis-ийн executor-той ижил 60 с) — декарт join зогсоно."""
    import time
    deadline = time.monotonic() + timeout_s
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10000)
    try:
        cur = conn.execute(sql)
        rows = cur.fetchmany(100001)
        return ExecResult(ok=True, columns=[d[0] for d in cur.description or []], rows=[tuple(r) for r in rows],
                          rowcount=len(rows))
    except Exception as e:  # sqlite3.Error, interrupted (хугацаа хэтэрсэн)
        return ExecResult(ok=False, error=str(e)[:300], error_code="timeout" if "interrupt" in str(e) else None)
    finally:
        conn.set_progress_handler(None, 0)


def literal_values(sql: str, question: str) -> list[str]:
    """Gold SQL-ийн текст литерал асуултад үгчлэн гарсан бол (утгын хайлтын recall, few-shot mask)."""
    lits = re.findall(r"'((?:[^']|'')+)'", sql or "")
    ql = (question or "").lower()
    out = []
    for v in lits:
        v = v.replace("''", "'").strip("%")
        if len(v) >= 2 and not re.fullmatch(r"[\d\-: .%]+", v) and v.lower() in ql and v not in out:
            out.append(v)
    return out


def import_thesis(cfg: Config, src: str, overwrite: bool = False) -> dict:
    src_p = Path(src).resolve()
    db_src = src_p / "build" / "data.db"
    if not db_src.exists():
        raise FileNotFoundError(f"{db_src} алга — thesis-д `python tasks.py gen-data` хийгдсэн байх ёстой")
    data_dir, know_dir = resolve_path(DATA_DIR), resolve_path(KNOW_DIR)
    data_dir.mkdir(parents=True, exist_ok=True)
    know_dir.mkdir(parents=True, exist_ok=True)
    db = data_dir / "ecom.sqlite"
    shutil.copyfile(db_src, db)
    schema_yaml = {}
    for f in sorted((src_p / "schema" / "tables").glob("*.yaml")):
        y = yaml.safe_load(f.read_text(encoding="utf-8"))
        schema_yaml[y["name"]] = y
    glossary = yaml.safe_load((src_p / "resources" / "glossary.yaml").read_text(encoding="utf-8")) or []
    cases = load_cases(src_p)
    tables = sqlite_schema(db)

    counts = load_postgres(cfg, db, schema_yaml, overwrite)
    sm = build_semantic_model(cfg, schema_yaml, glossary, tables)
    (know_dir / "semantic_model.yaml").write_text(
        "# ҮҮСГЭСЭН ФАЙЛ (t2s thesis-import) — text2sql-thesis/schema/tables/*.yaml + resources/glossary.yaml\n"
        + yaml.safe_dump(sm, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8", newline="\n")
    (know_dir / "glossary.yaml").write_text(
        "# Thesis-ийн DB-д бизнес нэр томьёоны (тодорхойлолт + SQL) сан байхгүй — B3 нь энэ benchmark дээр нөлөөгүй.\n"
        "# Thesis-ийн glossary.yaml (монгол үг → хүснэгт/багана/утга) нь semantic_model-ийн синоним болж орсон.\n"
        "version: 1\nterms: []\n", encoding="utf-8", newline="\n")

    bool_cols = {c for cols in tables.values() for c, ty in cols if ty.upper() == "BOOLEAN"}
    date_cols = {c for cols in tables.values() for c, ty in cols if ty.upper() in ("DATE", "TIMESTAMP")}
    overrides_path = know_dir / "gold_overrides.yaml"
    overrides = (yaml.safe_load(overrides_path.read_text(encoding="utf-8")) or {}) if overrides_path.exists() else {}

    from ..db.conn import admin_dsn
    from ..db.executor import Executor
    pg = Executor(admin_dsn(cfg), statement_timeout_ms=60000, max_rows=100001)
    lite = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows_by_split: dict[str, list[dict]] = {"train": [], "dev": [], "test": []}
    report = {"converted_auto": 0, "converted_override": 0, "excluded": []}
    for c in cases:
        cid, gold_lite = c["id"], (c.get("gold_sql") or {}).get("sqlite")
        if not gold_lite:
            report["excluded"].append({"id": cid, "reason": "SQLite gold алга"})
            continue
        if c.get("gold_nondeterministic"):
            report["excluded"].append({"id": cid, "reason": "gold детерминист бус (thesis E-33)"})
            continue
        g_lite = run_sqlite(lite, gold_lite)
        if not g_lite.ok:
            report["excluded"].append({"id": cid, "reason": f"SQLite gold алдаатай: {g_lite.error}"})
            continue
        if cid in overrides:
            pg_sql, how = overrides[cid].strip(), "override"
        else:
            try:
                pg_sql, how = sqlite_gold_to_pg(gold_lite, bool_cols, date_cols), "auto"
            except Exception as e:  # sqlglot parse
                report["excluded"].append({"id": cid, "reason": f"хөрвүүлэлт: {e}"[:200]})
                continue
        pg_sql = qualify_t2s(pg_sql)
        g_pg = pg.run(pg_sql)
        same = g_pg.ok and results_match(g_lite.columns, g_lite.rows, g_pg.columns, g_pg.rows, False, 0.01, 1e-6,
                                         lenient_periods=False)
        if not same:
            report["excluded"].append({"id": cid, "reason": "PostgreSQL gold-ийн үр дүн SQLite-тай таарахгүй",
                                       "pg_sql": pg_sql, "pg_error": g_pg.error,
                                       "rows_lite": len(g_lite.rows), "rows_pg": g_pg.rowcount if g_pg.ok else None})
            continue
        report["converted_" + how] += 1
        vals = literal_values(gold_lite, c.get("question_mn", ""))
        rows_by_split.setdefault(c.get("split", "test"), []).append({
            "id": cid, "family": cid, "domain": "thesis_ecom", "category": "answerable",
            "difficulty": DIFF_MAP.get(c.get("difficulty"), "medium"),
            "question_mn": c["question_mn"], "question_latin": translit(c["question_mn"], STYLE_VUW),
            "question_en": c.get("question_en", ""), "question_mixed": None,
            "gold_sql": pg_sql, "alt_sql": [], "order_matters": False,
            "tags": (c.get("tags") or []) + (["gold_empty"] if c.get("gold_returns_empty") else []),
            "contains_value": bool(vals), "values": vals, "glossary_terms": [],
            "notes": f"text2sql-thesis {cid} ({c.get('split')}); SQLite gold: {gold_lite}",
            "gold_sql_sqlite": gold_lite, "split": c.get("split", "test")})
    pg.close()
    lite.close()
    for split in ("dev", "test"):
        with open(data_dir / f"{split}.jsonl", "w", encoding="utf-8", newline="\n") as fh:
            for r in rows_by_split.get(split, []):
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(know_dir / "examples.jsonl", "w", encoding="utf-8", newline="\n") as fh:
        for r in rows_by_split.get("train", []):
            fh.write(json.dumps(dict(r, id="TR-" + r["id"]), ensure_ascii=False) + "\n")
    # Thesis-ийн few-shot сан = train + dev (test-ээс гадуурх бүгд). Test дээр тэнцүү нөхцөлөөр харьцуулахад:
    # index-build --extra-examples data/thesis/examples_dev.jsonl --out build/index_thesis_traindev (run_thesis_bench.sh)
    with open(data_dir / "examples_dev.jsonl", "w", encoding="utf-8", newline="\n") as fh:
        for r in rows_by_split.get("dev", []):
            fh.write(json.dumps(dict(r, id="DV-" + r["id"]), ensure_ascii=False) + "\n")
    manifest = {
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": str(src_p),
        "source_sha1": {"data.db": _sha1(db_src),
                        "cases": {f.name: _sha1(f) for f in sorted((src_p / "evals" / "cases").glob("*.json"))},
                        "glossary.yaml": _sha1(src_p / "resources" / "glossary.yaml")},
        "pg_db": cfg["db"]["name"], "row_counts": counts, "as_of_date": str(cfg["project"]["as_of_date"]),
        "n_cases": len(cases), "n": {k: len(v) for k, v in rows_by_split.items()}, **report,
    }
    (data_dir / "MANIFEST.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
                                            newline="\n")
    return {k: manifest[k] for k in ("n_cases", "n", "converted_auto", "converted_override")} | {
        "excluded": [(e["id"], e["reason"]) for e in report["excluded"]]}


# ---------------------------------------------------------------------------
# 5. thesis-ийн өөрийн системийн таамгийг ижил шалгуураар оноолох
# ---------------------------------------------------------------------------

class _SqliteGold:
    """GoldCache-ийн орлуулагч: PostgreSQL gold-ийн оронд тухайн кейсийн SQLite gold-ийг SQLite дээр гүйцэтгэнэ."""

    def __init__(self, conn: sqlite3.Connection, pg_to_lite: dict[str, str]):
        self.conn, self.map, self.cache = conn, pg_to_lite, {}

    def get(self, sql: str):
        if sql not in self.cache:
            self.cache[sql] = run_sqlite(self.conn, self.map.get(sql, sql))
        return self.cache[sql]


def score_thesis_results(cfg: Config, result_files: list[str], name: str = "thesis-agent",
                         dialect: str = "sqlite", llm: str | None = None) -> list[dict]:
    """thesis-ийн results/*.json (кейс бүрийн pred_sql) → шинэ системийн бичлэг (split бүр, хэл бүрээр)."""
    from ..eval.dataset import load_items
    from ..eval.metrics import load_records, summarize
    from ..eval.runner import build_record, code_version, eval_set_version, out_path, score_answer
    from ..pipeline.orchestrator import Answer, Candidate
    db = resolve_path(DATA_DIR) / "ecom.sqlite"
    lite = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    preds: dict[tuple[str, str], dict] = {}
    sources = []
    for f in result_files:
        d = json.loads(resolve_path(f).read_text(encoding="utf-8"))
        conf = d.get("config") if isinstance(d.get("config"), dict) else {}
        sources.append({"file": str(f), "label": d.get("label"), "timestamp": d.get("timestamp"),
                        "model": conf.get("model") or (conf.get("llm") or {}).get("model")})
        for r in d.get("results") or []:
            if r.get("dialect") == dialect:
                preds[(r["id"], r.get("language", "mn"))] = r
    out = []
    for split in ("dev", "test"):
        files = [cfg["eval"][f"{split}_file"]]
        items = load_items(files)
        gold = _SqliteGold(lite, {it.gold_sql: it.raw["gold_sql_sqlite"] for it in items})
        meta = {"config_id": f"EXT-{name}", "model": name, "split": split, "eval_set": eval_set_version(cfg, files),
                "db_snapshot": {"fingerprint": "thesis-sqlite"}, "prompt_version": "thesis", "model_revision": {},
                "code_version": code_version(), "llm": llm or next((x["model"] for x in sources if x["model"]), None)}
        for variant in ("mn", "en"):
            have = [it for it in items if (it.id, variant) in preds]
            if not have:
                continue
            path = out_path(cfg, split, meta["config_id"], name, variant, 0)
            path.parent.mkdir(parents=True, exist_ok=True)
            n_thesis_ex = 0
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                for it in have:
                    p = preds[(it.id, variant)]
                    n_thesis_ex += bool(p.get("ex"))
                    ans = Answer(question=p.get("question") or it.question(variant) or "", variant=variant,
                                 run_id=meta["config_id"], model=name)
                    sql = p.get("pred_sql")
                    cand = Candidate(raw_sql=sql, sql=sql)
                    if not sql:
                        ans.status, cand.error_stage, cand.error = "error", "extract", p.get("error") or "таамаг алга"
                    else:
                        res = run_sqlite(lite, sql)
                        cand.result, cand.ok = res, res.ok
                        ans.result = res
                        if not res.ok:
                            ans.status, cand.error_stage, cand.error = "error", "exec", res.error
                        elif res.rowcount == 0:
                            cand.error_stage = "empty"
                    ans.sql, ans.candidates = sql, [cand]
                    ans.latency_ms = p.get("latency_ms") or 0.0
                    ans.prompt_tokens = p.get("prompt_tokens") or 0
                    sc = score_answer(cfg, it, ans, gold)
                    rec = build_record(cfg, it, variant, 0, ans.question, ans, sc, meta)
                    rec["thesis_ex"] = bool(p.get("ex"))
                    rec["llm_calls"] = p.get("llm_calls")
                    fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
            summ = summarize(load_records(path), cfg["eval"]["penalty_c"])
            summ.update({"config_id": meta["config_id"], "model": name, "split": split, "variant": variant, "seed": 0,
                         "thesis_own_ex": round(100 * n_thesis_ex / len(have), 2), "sources": sources,
                         "missing": len(items) - len(have)})
            path.with_suffix(".summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=2),
                                                         encoding="utf-8")
            path.with_suffix(".meta.json").write_text(json.dumps(dict(meta, variant=variant, sources=sources,
                                                                      baseline=None), ensure_ascii=False, indent=2,
                                                                 default=str), encoding="utf-8")
            out.append({"split": split, "variant": variant, "n": summ["n"], "ex": summ["ex"],
                        "thesis_own_ex": summ["thesis_own_ex"], "missing": summ["missing"], "file": str(path)})
    lite.close()
    return out


# ---------------------------------------------------------------------------
# 6. Харьцуулалтын тайлан: thesis-ийн agent систем vs шинэ системийн run бүр (ижил кейс, ижил шалгуур)
# ---------------------------------------------------------------------------

RUN_FILE_TAGGED = re.compile(r"(?P<model>.+?)__(?P<variant>mn|latin|en|mixed)__s(?P<seed>\d+)(?:__(?P<tag>[\w.-]+))?\.jsonl")


def _ex(recs: list[dict], drop_empty: bool = False) -> float | None:
    ans = [r for r in recs if r.get("category") == "answerable" and not (drop_empty and "gold_empty" in (r.get("tags") or []))]
    return round(100 * sum(bool(r.get("correct")) for r in ans) / len(ans), 2) if ans else None


def compare_report(cfg: Config, splits: tuple[str, ...] = ("dev", "test"), reference: str = "EXT-thesis-agent",
                   out: str = "results/thesis/COMPARE.md") -> dict:
    from ..eval.metrics import load_records, mcnemar, summarize
    root = resolve_path(cfg["eval"]["results_dir"]) / "runs"
    lines = [
        "# Thesis benchmark: text2sql-thesis-ийн agent систем vs шинэ систем", "",
        "- **Кейс:** text2sql-thesis/evals/cases. Детерминист бус gold-той 6 кейсийг хассан. train (29) = шинэ системийн "
        "few-shot сан.",
        "- **Шалгуур:** бүх системд ижил (eval/compare.results_match, мөрийн multiset). Thesis-ийн таамгийг SQLite дээр "
        "SQLite gold-той, шинэ системийг PostgreSQL дээр PostgreSQL gold-той харьцуулна. Хоёр gold-ийн үр дүн тэнцүү гэж "
        "баталгаажсан.",
        "- **Таамгийн гүйцэтгэл диалектаас хамаарна:** SQLite нь сул (LIKE том жижиг үсэг ялгахгүй, bool = 1), PostgreSQL "
        "хатуу. SQLite протоколоор (sqlite_native) бичсэн шинэ системийн SQL PostgreSQL дээр ажиллана.",
        "- **Few-shot сангийн тэгш бус байдал:** thesis-ийн сан нь train + dev (test-ээс гадуурх бүгд). Шинэ системийн сан нь "
        "зөвхөн train. Test дээр тэнцүү нөхцөлтэй харьцуулахыг `-fs_traindev` tag-тай мөрөөс харна (MATCHED_FEWSHOT=1).",
        "- **«gold хоосон-гүй» багана:** `gold_empty` кейс (5) нь ямар ч хоосон таамгийг зөв гэж тооцдог. Энэ багана тэдгээрийг "
        "хассан EX-ийг харуулна.",
        "- **McNemar:** суурь = thesis-ийн agent систем, ижил (асуулт, хэл) хос дээр, n = хосын тоо. Олон seed-тэй run (E2) "
        "бол EX = seed-үүдийн дундаж, McNemar = хамгийн бага seed. docs/THESIS_BENCH.md", ""]
    data: dict = {}
    pc = cfg["eval"]["penalty_c"]
    for split in splits:
        sdir = root / split
        if not sdir.exists():
            continue
        runs: dict[tuple[str, str], dict[str, dict[int, list]]] = {}
        for f in sorted(sdir.glob("*/*.jsonl")):
            m = RUN_FILE_TAGGED.fullmatch(f.name)
            if not m or m.group("tag") in ("wrong",):
                continue
            sysname = f.parent.name + (f"-{m.group('tag')}" if m.group("tag") else "")
            runs.setdefault((sysname, m.group("model")), {}).setdefault(m.group("variant"), {})[int(m.group("seed"))] = \
                load_records(f)
        if not runs:
            continue
        ref = next((v for (s, _), v in runs.items() if s == reference), None)
        variants = [v for v in ("mn", "en", "latin") if any(v in r for r in runs.values())]
        lines += [f"## {split}", "",
                  "| Систем | Загвар | " + " | ".join(f"EX {v}" for v in variants)
                  + " | EX mn, gold хоосон-гүй | EX mn: хялбар / дунд / хүнд | Гүйцэтгэгдсэн % (mn) | "
                  + " | ".join(f"Δ vs thesis {v} (p, n)" for v in variants) + " |",
                  "|---|---|" + "---|" * (2 * len(variants) + 3)]
        data[split] = {}
        for key in sorted(runs, key=lambda k: (k[0] != reference, k[0], k[1])):
            per = runs[key]
            cells, deltas, row = [], [], {}
            for v in variants:
                seeds = per.get(v)
                if not seeds:
                    cells.append("—")
                    deltas.append("—")
                    continue
                exs = [summarize(recs, pc)["ex"] for recs in seeds.values()]
                exs = [x for x in exs if x is not None]
                mean = round(sum(exs) / len(exs), 2) if exs else None
                first = seeds[min(seeds)]
                row[v] = {"n": len(first), "ex": mean, "seeds": sorted(seeds)}
                cells.append(f"{mean} (n={len(first)}{', ' + str(len(seeds)) + ' seed' if len(seeds) > 1 else ''})")
                if ref and v in ref and key[0] != reference:
                    mc = mcnemar(ref[v][min(ref[v])], first)
                    row[v]["mcnemar"] = mc
                    deltas.append(f"{mc['delta_ex']:+} ({mc['p_value']}, {mc['n_pairs']}){' *' if mc['significant'] else ''}"
                                  if mc["delta_ex"] is not None else "—")
                else:
                    deltas.append("—")
            mn_recs = per["mn"][min(per["mn"])] if per.get("mn") else None
            mn = summarize(mn_recs, pc) if mn_recs else None
            diff = " / ".join(str((mn["by_difficulty"].get(d) or {}).get("ex", "—")) for d in ("easy", "medium", "hard")) \
                if mn else "—"
            no_empty = _ex(mn_recs, drop_empty=True) if mn_recs else "—"
            lines.append(f"| {key[0]} | {key[1]} | " + " | ".join(cells) + f" | {no_empty} | {diff} | "
                         f"{mn['executable_pct'] if mn else '—'} | " + " | ".join(deltas) + " |")
            data[split][f"{key[0]}|{key[1]}"] = row
        lines += ["", "`*` = p < 0.05 (exact McNemar). Δ = ижил хос асуулт дээрх EX-ийн зөрүү (шинэ − thesis).", ""]
    ref_missing = not any(k.startswith(reference + "|") for s in data.values() for k in s)
    if ref_missing:
        lines += [f"> {reference} алга. Thesis-ийн системийн таамгийг `t2s --config config/thesis.yaml thesis-score "
                  "--file <thesis results/*.json>` эсвэл `ORIGINAL=1 bash scripts/server/run_thesis_bench.sh`-оор "
                  "оноолно.", ""]
    path = resolve_path(out)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return {"out": str(path), "splits": list(data), "reference_missing": ref_missing}
