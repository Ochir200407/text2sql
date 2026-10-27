"""F1 үнэлгээ: монгол BIRD дээр хоёр загварыг өөрсдийн SQLite протоколоор ажиллуулна (A1 ба B5-ийн аналог).

F1  = A1: түүхий SQLite DDL + асуулт (evidence-гүй).
F1b = B5: + баганын тайлбар (BIRD database_description), + evidence (орчуулсан), + жишээ утга; few-shot нь
      data/bird/train_examples.jsonl байвал (BIRD train-ийн монгол хувилбар) — байхгүй бол алгасна.
EX нь BIRD-ийн албан ёсны дүрмээр: set(таамаг мөр) == set(gold мөр). Гүйцэтгэл SQLite read-only (mode=ro).
bird_prompt / bird_generate / bird_correct нь сургалтын rejection sampling-д (BIRD train) дахин ашиглагдана.
"""

from __future__ import annotations

import csv
import json
import logging
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from ..config import Config, load_models, resolve_path
from ..llm.client import make_llm
from ..llm.prompts import (
    SQRL_FINISH, SQRL_FIRST_USER, SQRL_OBSERVATION, PromptContext, build_generic, build_omnisql,
    build_omnisql_plain, build_sqrl_system,
    parse_sqrl_action, strip_think,
)
from ..pipeline.options import RunOptions
from ..security.sql_guard import extract_sql
from .data import DIFF_MAP, bird_dir, db_path, load_bird_mn

log = logging.getLogger(__name__)
_local = threading.local()


def _conn(path) -> sqlite3.Connection:
    cache = getattr(_local, "conns", None)
    if cache is None:
        cache = _local.conns = {}
    key = str(path)
    if key not in cache:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
        c.text_factory = lambda b: b.decode("utf-8", errors="replace")
        cache[key] = c
    return cache[key]


def run_sqlite(path, sql: str, timeout_s: float = 30.0, max_rows: int = 100000) -> tuple[bool, list | str]:
    conn = _conn(path)
    deadline = time.monotonic() + timeout_s
    conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10000)
    try:
        cur = conn.execute(sql)
        return True, cur.fetchmany(max_rows)
    except Exception as e:  # sqlite3.Error, interrupted
        return False, str(e)[:300]
    finally:
        conn.set_progress_handler(None, 0)


def schema_ddl(path) -> str:
    rows = _conn(path).execute("SELECT sql FROM sqlite_master WHERE type='table' AND sql IS NOT NULL "
                               "AND name NOT LIKE 'sqlite_%'").fetchall()
    return "\n\n".join(r[0].strip() + ";" for r in rows)


def column_notes(path, with_values: bool) -> list[str]:
    """BIRD database_description/*.csv-ээс баганын тайлбар (+ текст баганын жишээ утга)."""
    ddir = Path(path).parent / "database_description"
    notes = []
    if ddir.exists():
        for f in sorted(ddir.glob("*.csv")):
            raw = f.read_bytes()
            text = ""
            for enc in ("utf-8-sig", "cp1252", "latin-1"):
                try:
                    text = raw.decode(enc)
                    break
                except UnicodeDecodeError:
                    continue
            for r in csv.DictReader(text.splitlines()):
                col = (r.get("original_column_name") or "").strip()
                desc = (r.get("column_description") or r.get("column_name") or "").strip()
                vals = (r.get("value_description") or "").strip()
                if col and (desc or vals):
                    notes.append((f"{f.stem}.{col}: {desc}" + (f" ({vals[:200]})" if vals else "")).replace("\n", " "))
    if with_values:
        conn = _conn(path)
        for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            for col in conn.execute(f'PRAGMA table_info("{t}")').fetchall():
                if (col[2] or "").upper() in ("TEXT", ""):
                    ok, vals = run_sqlite(path, f'SELECT DISTINCT "{col[1]}" FROM "{t}" WHERE "{col[1]}" IS NOT NULL '
                                                f"LIMIT 3", 5)
                    if ok and vals:
                        notes.append(f"{t}.{col[1]} e.g. " + ", ".join(repr(v[0])[:40] for v in vals))
    return notes


def search_values_sqlite(path, keyword: str, limit: int = 10) -> str:
    """F10-ийн search_values tool-ийн SQLite хувилбар (BIRD сургалтын траекторт)."""
    conn = _conn(path)
    out = []
    kw = keyword.strip().replace("'", "''")[:60]
    for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
        for col in conn.execute(f'PRAGMA table_info("{t}")').fetchall():
            if (col[2] or "").upper() not in ("TEXT", ""):
                continue
            ok, rows = run_sqlite(path, f'SELECT DISTINCT "{col[1]}" FROM "{t}" WHERE "{col[1]}" LIKE \'%{kw}%\' LIMIT 2', 3)
            if ok:
                out += [f"{t}.{col[1]} = {r[0]}" for r in rows]
            if len(out) >= limit:
                return "\n".join(out)
    return "\n".join(out) or "(no matching stored values)"


def bird_prompt(question: str, evidence: str, path, opts: RunOptions, family: str,
                examples: list[tuple[str, str]] | None = None, hint_sql: str | None = None,
                include_evidence: bool | None = None) -> tuple[list[dict], str | None]:
    """include_evidence=None: opts.glossary-гаар (F1 = evidence-гүй, F1b = evidence-тэй). Сургалтын sampling
    BIRD-ийн стандарт оролтоор (evidence-тэй) — include_evidence=True."""
    lines = []
    if opts.describe or opts.values:
        lines += column_notes(path, opts.values)[:80]
    use_ev = opts.glossary if include_evidence is None else include_evidence
    if use_ev and evidence:
        lines.append(evidence)
    ctx = PromptContext(question=question, tables=[], evidence_lines=lines, examples=list(examples or []),
                        dialect="sqlite_native", max_steps=opts.agent_max_steps, pivot=opts.pivot,
                        search_values_tool=opts.search_values_tool)
    schema = schema_ddl(path)
    if family == "sqrl":
        msgs = [{"role": "system", "content": build_sqrl_system(ctx, schema)},
                {"role": "user", "content": SQRL_FIRST_USER.format(question=question)}]
        prefill = None
    elif family == "omnisql":
        msgs, prefill = build_omnisql(ctx, schema)
    elif family == "omnisql_plain":
        msgs, prefill = build_omnisql_plain(ctx, schema)
    else:
        msgs, prefill = build_generic(ctx, schema)
    if hint_sql:
        msgs[-1] = dict(msgs[-1], content=msgs[-1]["content"] + HINT_TEMPLATE.format(sql=hint_sql))
    return msgs, prefill


HINT_TEMPLATE = ("\n\n(Private note for you only: a correct query is\n```sql\n{sql}\n```\nDo NOT mention or quote this "
                 "note. Reason independently from the schema and the question, then give the answer in the required "
                 "format.)")


def bird_generate(path, messages: list[dict], prefill: str | None, family: str, llm, max_tokens: int,
                  opts: RunOptions, temperature: float = 0.0, top_p: float = 1.0, n: int = 1) -> list[dict]:
    """n гаралт. omnisql/generic: нэг хүсэлтээр n; sqrl: n тусдаа agent траектор."""
    outs = []
    if family != "sqrl":
        r = llm.chat(messages, n=n, temperature=temperature, top_p=top_p, max_tokens=max_tokens, prefill=prefill)
        for t in r.texts:
            outs.append({"sql": extract_sql(t), "messages": list(messages), "prefill": prefill, "raw_turns": [t],
                         "steps": 0, "tokens": (r.prompt_tokens // max(1, n), r.completion_tokens // max(1, n))})
        return outs
    for _ in range(n):
        conv, raw, steps, tin, tout, sql = list(messages), [], 0, 0, 0, None
        for _turn in range(opts.agent_max_steps + 3):
            r = llm.chat(conv, temperature=temperature, top_p=top_p, max_tokens=max_tokens)
            tin, tout = tin + r.prompt_tokens, tout + r.completion_tokens
            raw.append(r.texts[0])
            content = strip_think(r.texts[0]).strip()
            conv.append({"role": "assistant", "content": content})
            kind, body = parse_sqrl_action(content)
            if kind == "answer":
                sql = body
                break
            if kind == "sql" and steps < opts.agent_max_steps:
                steps += 1
                ok, res = run_sqlite(path, body, 15, 20)
                obs = "\n".join("\t".join(str(v)[:60] for v in row) for row in res) if ok else f"ERROR: {res}"
                conv.append({"role": "user", "content": SQRL_OBSERVATION.format(obs=obs or "(empty)")})
                continue
            if kind == "search_values" and opts.search_values_tool and steps < opts.agent_max_steps:
                steps += 1
                conv.append({"role": "user", "content": SQRL_OBSERVATION.format(obs=search_values_sqlite(path, body))})
                continue
            conv.append({"role": "user", "content": SQRL_FINISH})
        outs.append({"sql": sql, "messages": conv, "prefill": None, "raw_turns": raw, "steps": steps,
                     "tokens": (tin, tout)})
    return outs


def bird_correct(path, sql: str | None, gold_sql: str, gold_cache: dict | None = None) -> tuple[bool, bool]:
    """(гүйцэтгэгдсэн эсэх, зөв эсэх) — BIRD-ийн set тэнцүү байдлаар."""
    if not sql:
        return False, False
    key = (str(path), gold_sql)
    if gold_cache is not None and key in gold_cache:
        ok_g, gold = gold_cache[key]
    else:
        ok_g, gold = run_sqlite(path, gold_sql, 60)
        if gold_cache is not None:
            gold_cache[key] = (ok_g, gold)
    ok_p, pred = run_sqlite(path, sql, 30)
    return ok_p, bool(ok_g and ok_p and set(map(tuple, pred)) == set(map(tuple, gold)))


def _bird_examples(cfg: Config) -> list[dict]:
    p = bird_dir(cfg) / "train_examples.jsonl"
    if not p.exists():
        return []
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def run_bird_experiment(cfg: Config, opts: RunOptions, model: str | None = None, split: str = "dev",
                        limit: int | None = None, workers: int | None = None, variants: list[str] | None = None,
                        reviewed_only: bool = False, tag: str = "") -> list[dict]:
    from ..eval.metrics import summarize
    registry = load_models()
    rows = load_bird_mn(cfg, reviewed_only)
    if limit:
        rows = rows[:limit]
    examples = _bird_examples(cfg) if opts.fewshot_k > 0 else []
    if opts.fewshot_k > 0 and not examples:
        log.warning("F1b: data/bird/train_examples.jsonl алга — few-shot-гүй ажиллана")
    summaries = []
    for m in ([model] if model else opts.models):
        mspec = registry["models"].get(m, {"family": "generic", "served_name": m})
        family = mspec.get("family", "generic")
        llm_cfg = dict(cfg["llm"], model=opts.adapter or mspec.get("served_name", m))
        if mspec.get("extra_body"):
            llm_cfg["extra_body"] = mspec["extra_body"]
        llm = make_llm(llm_cfg)
        max_tokens = int(mspec.get("max_tokens", 2048))
        for variant in (variants or [opts.variant]):
            cid = opts.run_id + (f"-{tag}" if tag else "")
            out = resolve_path(cfg["eval"]["results_dir"]) / "runs" / "bird_mn" / cid / f"{m}__{variant}__s0.jsonl"
            out.parent.mkdir(parents=True, exist_ok=True)
            done = set()
            if out.exists():
                done = {json.loads(x)["question_id"] for x in out.read_text(encoding="utf-8").splitlines() if x.strip()}
            lock = threading.Lock()
            gold_cache: dict = {}

            def work(d, variant=variant, out=out, m=m, family=family, llm=llm, max_tokens=max_tokens):
                path = db_path(cfg, d["db_id"])
                q = {"mn": d["question_mn"], "latin": d["question_latin"], "en": d["question_en"]}[variant]
                ev = d["evidence_en"] if variant == "en" else d.get("evidence_mn", "")
                exs = [(e["question_mn"], e["SQL"]) for e in examples
                       if e.get("db_id") != d["db_id"]][: opts.fewshot_k] if examples else []
                t0 = time.perf_counter()
                msgs, prefill = bird_prompt(q, ev, path, opts, family, exs)
                g = bird_generate(path, msgs, prefill, family, llm, max_tokens, opts)[0]
                ok_p, correct = bird_correct(path, g["sql"], d["SQL"], gold_cache)
                rec = {"question_id": d["question_id"], "config_id": cid, "model": m, "split": "bird_mn",
                       "variant": variant, "seed": 0, "category": "answerable",
                       "difficulty": DIFF_MAP.get(d["difficulty"], d["difficulty"]), "domain": d["db_id"],
                       "question": q, "sql": g["sql"], "status": "answered" if ok_p else "error", "correct": correct,
                       "raw_correct": correct, "exec_ok": ok_p, "error_stage": None if ok_p else
                       ("extract" if not g["sql"] else "exec"), "agent_steps": g["steps"],
                       "latency_ms": round((time.perf_counter() - t0) * 1000, 1), "prompt_tokens": g["tokens"][0],
                       "completion_tokens": g["tokens"][1], "reviewed": d.get("reviewed", False),
                       "ts": datetime.now(timezone.utc).isoformat(timespec="seconds")}
                with lock, open(out, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

            todo = [d for d in rows if d["question_id"] not in done]
            with ThreadPoolExecutor(max_workers=int(workers or cfg["eval"]["workers"])) as pool:
                for f in as_completed([pool.submit(work, d) for d in todo]):
                    f.result()
            recs = [json.loads(x) for x in out.read_text(encoding="utf-8").splitlines() if x.strip()]
            from ..eval.runner import code_version, model_revision, prompt_version
            import hashlib
            data_path = bird_dir(cfg) / "bird_mn.jsonl"
            meta = {"config_id": cid, "model": m, "split": "bird_mn", "variant": variant, "options": opts.fingerprint(),
                    "eval_set": {"file": str(data_path), "sha1": hashlib.sha1(data_path.read_bytes()).hexdigest()[:12],
                                 "reviewed_only": reviewed_only},
                    "prompt_version": prompt_version(family), "model_revision": model_revision(mspec),
                    "code_version": code_version(), "served_model": llm_cfg["model"], "baseline": opts.baseline(),
                    "finished": datetime.now(timezone.utc).isoformat(timespec="seconds")}
            out.with_suffix(".meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2, default=str),
                                                     encoding="utf-8")
            summ = summarize(recs, cfg["eval"]["penalty_c"])
            summ.update({"config_id": cid, "model": m, "split": "bird_mn", "variant": variant, "seed": 0})
            out.with_suffix(".summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=2), encoding="utf-8")
            summaries.append({"config_id": cid, "model": m, "variant": variant, "n": summ["n"],
                              "ex": summ["ex"], "file": str(out)})
    return summaries
