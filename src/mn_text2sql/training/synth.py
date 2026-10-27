"""C3: өөрийн DB-ийн синтетик жишээ (OmniSQL-ийн синтезийн санаа, шалгагдах хувилбар).

1. Загвар: жишээний сан (knowledge/examples.jsonl, eval-д ороогүй 160 асуулт)-ийн gold SQL.
2. SQL хувилбар: WHERE-ийн тогтмолыг (текст утга → тухайн баганын өөр DB утга; тоон босго; LIMIT; он) сольж,
   bot эрхээр ажиллуулж хоосон биш, анхныхаас өөр үр дүнтэйг л үлдээнэ.
3. Асуулт: LLM (орчуулагч загвар) анхны асуулт/SQL-ийг жишээ болгон шинэ SQL-д тохирох монгол ба англи асуулт
   бичнэ; латиныг галигийн хоёр хэв маягаар.
4. dev/test-тэй давхардлыг training.data.dedup_against_eval шүүнэ (асуулт ба үр дүнгийн hash).
"""

from __future__ import annotations

import json
import logging
import random
import re
import threading
from concurrent.futures import ThreadPoolExecutor

import sqlglot
from sqlglot import exp

from ..config import Config, load_models
from ..db.conn import bot_dsn
from ..db.executor import Executor
from ..eval.compare import result_hash
from ..eval.goldcheck import make_guard
from ..retrieval.build import index_dir
from ..retrieval.indexes import load_json
from .data import TrainItem, human_items, train_dir, write_items

log = logging.getLogger(__name__)

QGEN_SYSTEM = (
    "You write questions that employees of a Mongolian non-bank financial institution would ask. Given an example "
    "question with its SQL, and a NEW SQL query, write the question that the NEW SQL answers, in the same style. "
    "Keep database values exactly as in the SQL (Mongolian names stay in Cyrillic). Return JSON only: "
    '{"mn": "<Mongolian Cyrillic question>", "en": "<English question>"}')


def _alias_map(tree) -> dict[str, str]:
    m = {}
    for t in tree.find_all(exp.Table):
        if t.name:
            m[(t.alias_or_name or t.name).lower()] = t.name.lower()
    return m


def _values_by_column(cfg: Config) -> dict[tuple[str, str], list[str]]:
    out: dict[tuple[str, str], list[str]] = {}
    for e in load_json(index_dir(cfg) / "values.json"):
        out.setdefault((e["table"], e["column"]), []).append(e["value"])
    return out


def sql_variants(sql: str, values: dict, rng: random.Random, k: int = 6) -> list[str]:
    """Тогтмолыг сольсон k хүртэлх хувилбар (давхардалгүй)."""
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except Exception:
        return []
    amap = _alias_map(tree)
    single_table = len(set(amap.values())) == 1
    slots = []
    for node in tree.find_all(exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE):
        col, lit = node.this, node.expression
        if isinstance(col, exp.Column) and isinstance(lit, exp.Literal):
            table = amap.get((col.table or "").lower()) or (next(iter(amap.values())) if single_table else None)
            slots.append((lit, table, col.name.lower()))
    for lim in tree.find_all(exp.Limit):
        if isinstance(lim.expression, exp.Literal) and not lim.expression.is_string:
            slots.append((lim.expression, None, "__limit__"))
    if not slots:
        return []
    out, seen = [], {sql}
    for _ in range(k * 4):
        t2 = tree.copy()
        lits2 = [n for n in t2.find_all(exp.Literal)]
        lits1 = [n for n in tree.find_all(exp.Literal)]
        idx = {id(a): b for a, b in zip(lits1, lits2)}
        changed = False
        for lit, table, column in slots:
            tgt = idx.get(id(lit))
            if tgt is None or rng.random() < 0.3:
                continue
            if column == "__limit__":
                tgt.replace(exp.Literal.number(rng.choice([3, 5, 10, 15])))
                changed = True
            elif lit.is_string:
                pool = [v for v in values.get((table, column), []) if v != lit.this]
                if re.fullmatch(r"\d{4}-\d{2}-\d{2}", lit.this):
                    y, mth, d = lit.this.split("-")
                    new_y = rng.choice(["2024", "2025", "2026"])
                    new = f"{new_y}-{mth}-{d}" if new_y != "2026" or mth <= "06" else f"2025-{mth}-{d}"
                    tgt.replace(exp.Literal.string(new))
                    changed = True
                elif pool:
                    tgt.replace(exp.Literal.string(rng.choice(pool)))
                    changed = True
            else:
                try:
                    v = float(lit.this)
                except ValueError:
                    continue
                if 2000 <= v <= 2100:
                    nv = rng.choice([2024, 2025, 2026])
                else:
                    nv = v * rng.choice([0.5, 0.75, 1.5, 2.0])
                    nv = int(round(nv, -int(max(0, len(str(int(abs(nv)))) - 2)))) if nv >= 10 else round(nv, 2)
                tgt.replace(exp.Literal.number(nv))
                changed = True
        if changed:
            s2 = t2.sql(dialect="postgres")
            if s2 not in seen:
                seen.add(s2)
                out.append(s2)
        if len(out) >= k:
            break
    return out


def synthesize(cfg: Config, tcfg: dict, model_name: str | None = None, base_url: str | None = None,
               seed: int = 42, workers: int = 16, llm=None) -> dict:
    from ..bird.data import latin_variant
    from ..llm.client import make_llm
    rng = random.Random(seed)
    per = int(tcfg["data"].get("synth_per_template", 6))
    cap = int(tcfg["data"].get("synth_max", 3000))
    if llm is None:
        registry = load_models()
        model_name = model_name or registry.get("translator")
        mspec = registry["models"].get(model_name, {})
        llm_cfg = dict(cfg["llm"], model=mspec.get("served_name", model_name))
        if base_url:
            llm_cfg["base_url"] = base_url
        if mspec.get("extra_body"):
            llm_cfg["extra_body"] = mspec["extra_body"]
        llm = make_llm(llm_cfg)
    guard = make_guard(cfg)
    ex = Executor(bot_dsn(cfg), statement_timeout_ms=cfg["db"]["statement_timeout_ms"], maxconn=workers + 2)
    values = _values_by_column(cfg)
    templates = human_items(cfg)
    jobs = []
    for t in templates:
        g = guard.check(t.gold_sql)
        base = ex.run(g.sql) if g.ok else None
        base_hash = result_hash(base.columns, base.rows) if base is not None and base.ok else None
        for s in sql_variants(t.gold_sql, values, rng, per):
            jobs.append((t, s, base_hash))
    rng.shuffle(jobs)
    jobs = jobs[: cap * 2]
    lock = threading.Lock()
    out_items: list[TrainItem] = []
    seen_hashes: set[str] = set()
    stats = {"templates": len(templates), "candidates": len(jobs), "kept": 0, "empty_or_same": 0, "qgen_failed": 0}

    def work(job):
        t, sql, base_hash = job
        g = guard.check(sql)
        if not g.ok:
            return
        r = ex.run(g.sql)
        if not r.ok or r.rowcount == 0 or all(all(v is None for v in row) for row in r.rows):
            with lock:
                stats["empty_or_same"] += 1
            return
        h = result_hash(r.columns, r.rows)
        with lock:
            if h == base_hash or h in seen_hashes or len(out_items) >= cap:
                stats["empty_or_same"] += 1
                return
            seen_hashes.add(h)
        msgs = [{"role": "system", "content": QGEN_SYSTEM},
                {"role": "user", "content": f"Example question: {t.question_mn}\nExample SQL: {t.gold_sql}\n"
                                            f"NEW SQL: {sql}"}]
        resp = llm.chat(msgs, temperature=0.3, max_tokens=300)
        m = re.search(r"\{.*\}", resp.texts[0].split("</think>")[-1], re.S)
        try:
            obj = json.loads(m.group(0)) if m else None
        except json.JSONDecodeError:
            obj = None
        if not obj or not obj.get("mn"):
            with lock:
                stats["qgen_failed"] += 1
            return
        item = TrainItem(id=f"synth-{t.id}-{h[:8]}", source="own_synth", engine="postgres", gold_sql=sql,
                         question_mn=obj["mn"].strip(), question_en=(obj.get("en") or "").strip(),
                         question_latin=latin_variant(obj["mn"], random.Random(h)), difficulty=t.difficulty,
                         extra={"template": t.id})
        with lock:
            out_items.append(item)
            stats["kept"] += 1

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(work, jobs))
    ex.close()
    path = train_dir(tcfg) / "own_synth.jsonl"
    write_items(sorted(out_items, key=lambda x: x.id), path)
    stats["file"] = str(path)
    return stats
