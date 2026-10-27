"""Туршилтын run: eval set × хувилбар (mn/latin/en/mixed) × seed → асуулт бүрийн JSONL + summary.

Туршилтын төлөвлөгөөний "Дүрэм":
  * run бүрт: eval set-ийн хувилбар, DB snapshot, prompt template, загварын revision (HF commit), кодын хувилбар;
  * greedy нэг удаа; sampling-тай run (E2) 3 seed (дундаж ± std — report);
  * асуулт бүрийн JSONL: question_id, config_id, SQL, result hash, correct, latency, tokens, error type.
Гаралт: results/runs/<split>/<config_id>/<model>__<variant>__s<seed>[__tag].jsonl (+ .summary.json, .meta.json)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from ..config import ROOT, Config, load_models, resolve_path
from ..db.conn import bot_dsn
from ..db.executor import Executor
from ..llm.client import MockLLM
from ..pipeline.options import RunOptions, resolve_run
from ..pipeline.orchestrator import Answer, Pipeline
from .compare import results_match, soft_f1
from .dataset import EvalItem, load_all_eval_items, load_items
from .goldcheck import make_guard
from .metrics import NO_ANSWER, load_records, summarize

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Run-ийн мета
# ---------------------------------------------------------------------------

def split_files(cfg: Config, split: str) -> list[str]:
    if split == "dev":
        return [cfg["eval"]["dev_file"]]
    if split == "test":
        return [cfg["eval"]["test_file"]]
    if split == "all":
        return [cfg["eval"]["dev_file"], cfg["eval"]["test_file"]]
    return [split]  # шууд файлын зам


def eval_set_version(cfg: Config, files: list[str]) -> dict:
    h = hashlib.sha1()
    for f in files:
        h.update(resolve_path(f).read_bytes())
    manifest = resolve_path("evalset/MANIFEST.json")
    version = "v1"
    if manifest.exists():
        version = json.loads(manifest.read_text(encoding="utf-8")).get("version", version)
    return {"version": version, "sha1": h.hexdigest()[:12], "files": files}


def code_version() -> dict:
    try:
        commit = subprocess.run(["git", "-C", str(ROOT), "rev-parse", "--short", "HEAD"], capture_output=True,
                                text=True, timeout=10)
        if commit.returncode == 0 and commit.stdout.strip():
            dirty = subprocess.run(["git", "-C", str(ROOT), "status", "--porcelain", "--", "src", "config",
                                    "knowledge"], capture_output=True, text=True, timeout=10).stdout.strip()
            return {"commit": commit.stdout.strip(), "dirty": bool(dirty)}
    except (OSError, subprocess.SubprocessError):
        pass
    # git commit байхгүй үед: эх кодын hash
    h = hashlib.sha1()
    for p in sorted(list((ROOT / "src").rglob("*.py")) + list((ROOT / "config").glob("*.yaml"))
                    + list((ROOT / "knowledge").glob("*.yaml"))):
        h.update(p.relative_to(ROOT).as_posix().encode())
        h.update(p.read_bytes())
    return {"commit": None, "tree_sha1": h.hexdigest()[:12]}


def prompt_version(family: str) -> str:
    src = (ROOT / "src" / "mn_text2sql" / "llm" / "prompts.py").read_bytes()
    return f"{family}:{hashlib.sha1(src).hexdigest()[:10]}"


def model_revision(mspec: dict) -> dict:
    """HF commit: T2S_MODEL_REVISION орчны хувьсагч → HF кэшийн snapshot → models.yaml-ийн revision."""
    hf_id = mspec.get("hf_id")
    rev = os.environ.get("T2S_MODEL_REVISION")
    if not rev and hf_id:
        try:
            from huggingface_hub import scan_cache_dir
            for repo in scan_cache_dir().repos:
                if repo.repo_id == hf_id:
                    revs = sorted(repo.revisions, key=lambda r: r.last_modified, reverse=True)
                    if revs:
                        rev = revs[0].commit_hash
                    break
        except Exception:  # huggingface_hub байхгүй / кэш байхгүй
            pass
    return {"hf_id": hf_id, "revision": rev or mspec.get("revision")}


def db_snapshot(executor: Executor, cfg: Config) -> dict:
    res = executor.run("SELECT t2s.business_date(), (SELECT count(*) FROM t2s.loans), "
                       "(SELECT round(sum(principal_amount)) FROM t2s.loans), (SELECT count(*) FROM t2s.transactions)")
    if not res.ok:
        # ББСБ-ийн биш DB (thesis benchmark г.м.): t2s-ийн бүх хүснэгтийн мөрийн тооноос fingerprint
        tabs = executor.run("SELECT table_name FROM information_schema.tables WHERE table_schema = 't2s' "
                            "AND table_type IN ('BASE TABLE', 'VIEW') ORDER BY 1")
        if not tabs.ok or not tabs.rows:
            return {"db": cfg["db"]["name"], "error": res.error}
        counts = {}
        for (t,) in tabs.rows:
            r = executor.run(f'SELECT count(*) FROM t2s."{t}"')
            counts[t] = r.rows[0][0] if r.ok else None
        fp = hashlib.sha1(json.dumps(counts, sort_keys=True).encode()).hexdigest()[:10]
        bd = executor.run("SELECT t2s.business_date()")
        return {"db": cfg["db"]["name"], "business_date": str(bd.rows[0][0]) if bd.ok else None, "fingerprint": fp,
                "row_counts": counts}
    bd, n_loans, s_principal, n_txn = res.rows[0]
    fp = hashlib.sha1(f"{n_loans}|{s_principal}|{n_txn}".encode()).hexdigest()[:10]
    return {"db": cfg["db"]["name"], "business_date": str(bd), "fingerprint": fp, "loans": n_loans,
            "seed": cfg["project"]["seed"], "scale": cfg["data"]["scale"]}


# ---------------------------------------------------------------------------
# Oracle LLM (harness шалгах): gold SQL-ийг загварын протоколоор буцаана
# ---------------------------------------------------------------------------

class OracleLLM(MockLLM):
    """Thread бүрийн одоогийн асуултын gold SQL (local.sql); pipeline дотор шинэ thread үүсвэл (sqrl-ийн олон
    хувилбар) асуултын текстээр (by_question) хайна."""

    def __init__(self, family: str):
        self.local = threading.local()
        self.family = family
        self.by_question: dict[str, str] = {}
        self._lock = threading.Lock()   # олон worker зэрэг бичиж, уншина (dict-ийг давтаж байхад өөрчлөгдөхгүй)
        super().__init__(self._respond, name="oracle")

    def remember(self, question: str, sql: str | None) -> None:
        with self._lock:
            self.by_question[question] = sql

    def _respond(self, messages) -> str:
        sql = getattr(self.local, "sql", None)
        if not sql:
            text = "\n".join(m.get("content") or "" for m in messages)
            with self._lock:
                pairs = list(self.by_question.items())
            sql = next((s for q, s in pairs if q and q in text), None)
        if not sql:
            # Хариулах боломжгүй асуулт: төгс загвар протоколын дагуу татгалзана (TrustSQL-ийн замыг шалгана)
            refusal = "I cannot answer this question from the database."
            if self.family == "sqrl":
                return f"Oracle.\n<answer> {refusal} </answer>"
            if self.family == "omnisql":
                return f" oracle</think>\n<answer>{refusal}</answer>"
            return refusal
        if self.family == "sqrl":
            return f"Oracle.\n<answer> {sql} </answer>"
        if self.family == "omnisql":
            return f" oracle</think>\n<answer>\n```sql\n{sql}\n```\n</answer>"
        return f"```sql\n{sql}\n```"


# ---------------------------------------------------------------------------
# Үнэлгээ
# ---------------------------------------------------------------------------

class GoldCache:
    def __init__(self, guard, executor: Executor):
        self.guard = guard
        self.executor = executor
        self._cache: dict[str, object] = {}
        self._lock = threading.Lock()

    def get(self, sql: str):
        with self._lock:
            if sql in self._cache:
                return self._cache[sql]
        g = self.guard.check(sql)
        res = self.executor.run(g.sql) if g.ok else None
        with self._lock:
            self._cache[sql] = res
        return res


_VALUE_MAPS: list[dict] = []   # код↔нэрийн map (run эхлэхэд DB-ээс; compare.build_code_maps)


def _match(gold_res, res, order_matters: bool, cfg: Config, lenient: bool = True) -> bool:
    return bool(gold_res is not None and gold_res.ok and res is not None and res.ok and
                results_match(gold_res.columns, gold_res.rows, res.columns, res.rows, order_matters,
                              float(cfg["eval"]["float_tolerance"]), float(cfg["eval"]["relative_tolerance"]),
                              lenient_periods=lenient, value_maps=_VALUE_MAPS if lenient else None))


def init_value_maps(executor: Executor) -> int:
    from .compare import build_code_maps
    global _VALUE_MAPS
    _VALUE_MAPS = build_code_maps(executor)
    return len(_VALUE_MAPS)


def score_answer(cfg: Config, item: EvalItem, ans: Answer, gold: GoldCache) -> dict:
    golds = [gold.get(s) for s in item.gold_sqls()]
    res = ans.result
    exec_ok = bool(res is not None and res.ok)
    raw_correct = any(_match(g, res, item.order_matters, cfg) for g in golds) if exec_ok else False
    strict = raw_correct and any(_match(g, res, item.order_matters, cfg, lenient=False) for g in golds)
    answered = ans.status not in NO_ANSWER
    if item.category == "unanswerable":
        correct = not answered
        raw_correct = False
    elif item.category == "ambiguous" and ans.status == "clarify":
        correct = True   # GUIDE.md: «зорилготой тодруулах асуулт асуусан ... бол зөвд тооц» (metrics.trust_score-тэй нийцүүлэв)
    else:
        correct = raw_correct and answered and ans.status != "error"
    sf1 = None
    if item.category == "answerable" and golds and golds[0] is not None and golds[0].ok:
        sf1 = round(soft_f1(golds[0].rows, res.rows), 4) if exec_ok else 0.0
    cand_correct = []
    if len(ans.candidates) > 1:
        for c in ans.candidates:
            cand_correct.append(bool(c.ok and any(_match(g, c.result, item.order_matters, cfg) for g in golds)))
    tr = vr = None
    ret = ans.retrieval or {}
    if item.category == "answerable" and item.gold_sql and ret.get("linked_tables"):
        gt = gold_tables(item.gold_sql)
        if gt:
            tr = round(len(gt & set(ret["linked_tables"])) / len(gt), 4)
    if item.category == "answerable" and item.values and ret.get("values") is not None:
        got = {str(v[2]).lower() for v in ret["values"]}
        vr = round(sum(1 for v in item.values if str(v).lower() in got) / len(item.values), 4)
    return {"correct": bool(correct), "raw_correct": bool(raw_correct), "table_recall": tr, "value_recall": vr,
            "strict_correct": bool(correct and strict) if item.category != "unanswerable" else bool(correct),
            "soft_f1": sf1, "exec_ok": exec_ok,
            "empty": bool(exec_ok and res.rowcount == 0), "cand_correct": cand_correct or None}


def gold_tables(sql: str) -> set[str]:
    import sqlglot
    from sqlglot import exp
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except Exception:
        return set()
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    return {t.name.lower() for t in tree.find_all(exp.Table) if t.name and t.name.lower() not in ctes}


def build_record(cfg: Config, item: EvalItem, variant: str, seed: int, question: str, ans: Answer, sc: dict,
                 meta: dict) -> dict:
    chosen = None
    if ans.candidates:
        if ans.chosen_index is not None and ans.chosen_index < len(ans.candidates):
            chosen = ans.candidates[ans.chosen_index]
        else:
            chosen = next((c for c in ans.candidates if (c.sql or c.raw_sql) == ans.sql), ans.candidates[0])
    return {
        "question_id": item.id, "family": item.family, "config_id": meta["config_id"], "model": meta["model"],
        "split": meta["split"], "variant": variant, "seed": seed, "category": item.category,
        "difficulty": item.difficulty, "domain": item.domain, "contains_value": item.contains_value,
        "tags": item.tags, "question": question, "question_used": ans.question_used if ans.question_used != question
        else None, "sql": ans.sql, "status": ans.status, "result_hash": chosen.result_hash if chosen else None,
        "row_count": ans.result.rowcount if ans.result is not None and ans.result.ok else None,
        **sc, "error_stage": chosen.error_stage if chosen else None,
        "error": (chosen.error or "")[:500] if chosen and chosen.error else None,
        "confidence": ans.confidence, "unanimous": ans.unanimous, "n_candidates": len(ans.candidates),
        "cand_hashes": [c.result_hash for c in ans.candidates] if len(ans.candidates) > 1 else None,
        "cand_sqls": [c.sql or c.raw_sql for c in ans.candidates] if len(ans.candidates) > 1 else None,
        "repairs": chosen.repairs if chosen else 0, "agent_steps": chosen.agent_steps if chosen else 0,
        "latency_ms": ans.latency_ms, "gen_ms": ans.timings.get("generate_ms"),
        "prompt_tokens": ans.prompt_tokens, "completion_tokens": ans.completion_tokens,
        "routed": ans.routed or None, "retrieval": ans.retrieval or None,
        "agent_log": chosen.agent_log if chosen is not None and chosen.agent_log else None,
        "agent_ms": ans.timings.get("agent_ms"),
        "raw_output": (chosen.text[-4000:] if chosen and chosen.text else None),
        "eval_set": meta["eval_set"]["version"] + ":" + meta["eval_set"]["sha1"],
        "db_snapshot": meta["db_snapshot"].get("fingerprint"), "prompt_version": meta["prompt_version"],
        "model_revision": meta["model_revision"].get("revision"),
        "code_version": meta["code_version"].get("commit") or meta["code_version"].get("tree_sha1"),
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def options_hash(meta: dict, variant: str, seed: int) -> str:
    """Resume-ийн хамгаалалт: тохиргоо (vars/--set орсон), served model, хувилбар, seed өөрчлөгдвөл өөр hash."""
    key = {"options": meta.get("options"), "served": meta.get("served_model"), "variant": variant, "seed": seed,
           "eval_set": (meta.get("eval_set") or {}).get("sha1")}
    return hashlib.sha1(json.dumps(key, sort_keys=True, default=str).encode()).hexdigest()[:12]


def preflight_llm(cfg: Config, served: str) -> None:
    """vLLM асаалттай, хэрэгтэй served model (эсвэл LoRA adapter) бүртгэлтэй эсэхийг run-ээс өмнө шалгана."""
    import httpx
    url = cfg["llm"]["base_url"].rstrip("/") + "/models"
    try:
        r = httpx.get(url, timeout=10)
        r.raise_for_status()
        names = {m.get("id") for m in r.json().get("data", [])}
    except Exception as e:
        raise RuntimeError(f"vLLM-д холбогдож чадсангүй ({url}): {e}. scripts/server/serve_model.sh-ээр асаана.") from e
    if served not in names:
        raise RuntimeError(f"vLLM дээр '{served}' алга (бүртгэлтэй: {sorted(n for n in names if n)}). Зөв загвар/adapter-"
                           "ийг асаана (LORA=... эсвэл MODEL_PATH/SERVED).")


def index_info(pipe) -> dict:
    """Run-ийн мета: индексийн хувилбар, embedding ачаалагдсан эсэх (BM25 руу чимээгүй унахаас сэргийлэх)."""
    r = getattr(pipe, "retriever", None)
    if r is None:
        return {"used": False}
    return {"used": True, "dir": str(getattr(r, "index_dir", "")), "meta": getattr(r, "index_meta", None),
            "embedder": getattr(getattr(r, "embedder", None), "model_name", None)}


def _check_cache_coverage(items, variants, cache: dict | None, label: str, cmd: str) -> None:
    if cache is None:
        return
    sources = {}
    for src in CACHE_SOURCES.values():
        sources.update(src)
    stale = [f"{it.id}:{v}" for it in items for v in variants if it.question(v) and f"{it.id}:{v}" in sources
             and sources[f"{it.id}:{v}"].strip() != it.question(v).strip()]
    if stale:
        raise FileNotFoundError(f"{label}: {len(stale)} кэш хуучирсан (асуулт өөрчлөгдсөн, жишээ {stale[:3]}). Кэшийг "
                                f"устгаад дахин: python -m mn_text2sql {cmd} ...")
    missing = [f"{it.id}:{v}" for it in items for v in variants if it.question(v) and not cache.get(f"{it.id}:{v}")]
    if missing:
        raise FileNotFoundError(f"{label}: {len(missing)} асуулт/хувилбарын кэш алга (жишээ: {missing[:3]}). "
                                f"python -m mn_text2sql {cmd} --split ... --variants {','.join(variants)}")


def out_path(cfg: Config, split: str, config_id: str, model: str, variant: str, seed: int, tag: str = "") -> Path:
    base = resolve_path(cfg["eval"]["results_dir"]) / "runs" / Path(split).stem / config_id
    name = f"{model}__{variant}__s{seed}" + (f"__{tag}" if tag else "")
    return base / f"{name}.jsonl"


CACHE_SOURCES: dict[str, dict[str, str]] = {}


def _load_cache(path: Path) -> dict:
    """{id:variant → текст/json}; эх асуултыг (source) CACHE_SOURCES-д хадгалж хуучирсан кэшийг илрүүлнэ."""
    out, src = {}, {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                key = f"{d['id']}:{d['variant']}"
                out[key] = d.get("text") or d.get("json")
                if d.get("source"):
                    src[key] = d["source"]
    CACHE_SOURCES[str(path)] = src
    return out


def run_experiment(cfg: Config, config_id: str, model: str | None = None, split: str = "dev",
                   variants: list[str] | None = None, limit: int | None = None, workers: int | None = None,
                   oracle: bool = False, tag: str = "", var_overrides: dict | None = None,
                   set_overrides: dict | None = None, seeds: list[int] | None = None, overwrite: bool = False,
                   ids: list[str] | None = None, llm=None) -> list[dict]:
    """Нэг туршилтын ID-г нэг (эсвэл бүх) загвар дээр ажиллуулна. Буцаах: summary жагсаалт."""
    opts = resolve_run(config_id, overrides=set_overrides, var_overrides=var_overrides)
    if opts.dataset != "nbfi":
        from .bird import run_bird_experiment
        return run_bird_experiment(cfg, opts, model, split=split, limit=limit, workers=workers, variants=variants,
                                   tag=tag)
    models = [model] if model else list(opts.models)
    variants = variants or [opts.variant]
    files = split_files(cfg, split)
    items = load_items(files)
    if ids:
        items = [it for it in items if it.id in set(ids)]
    if limit:
        items = items[:limit]
    workers = int(workers or cfg["eval"]["workers"])
    summaries = []
    for m in models:
        summaries.extend(_run_model(cfg, opts, m, split, files, items, variants, workers, oracle, tag, seeds,
                                    overwrite, llm))
    return summaries


def _run_model(cfg: Config, opts: RunOptions, model: str, split: str, files: list[str], items: list[EvalItem],
               variants: list[str], workers: int, oracle: bool, tag: str, seeds: list[int] | None,
               overwrite: bool, llm=None) -> list[dict]:
    registry = load_models()
    mspec = registry["models"].get(model, {})
    family = mspec.get("family", "generic")
    executor = Executor(bot_dsn(cfg), statement_timeout_ms=cfg["db"]["statement_timeout_ms"],
                        max_rows=int(cfg["pipeline"]["max_result_rows"]) * 100, maxconn=max(8, workers * 2),
                        max_cost=float(cfg["db"]["max_explain_cost"]))
    guard = make_guard(cfg)
    oracle_llm = OracleLLM(family) if oracle else None
    served = opts.adapter or mspec.get("served_name", model)
    if llm is None and oracle_llm is None:
        preflight_llm(cfg, served)
    translations = intents = None
    if opts.translate:
        name = opts.translation_cache or registry.get("translator")
        translations = _load_cache(resolve_path("build/translations") / f"{name}.jsonl")
        if not translations:
            raise FileNotFoundError("A4: орчуулгын кэш алга. Эхлээд: python -m mn_text2sql translate --split "
                                    f"{split} (орчуулагч: {registry.get('translator')})")
    if opts.intent:
        intents = _load_cache(resolve_path("build/intents") / f"{registry.get('intent_model')}.jsonl")
        if not intents:
            raise FileNotFoundError("F7: intent кэш алга. Эхлээд: python -m mn_text2sql intent --split "
                                    f"{split} (загвар: {registry.get('intent_model')})")
    router = None
    if opts.semantic_router:
        from ..pipeline.router import SemanticRouter
        router = SemanticRouter(cfg)
        if not router.cache:
            raise FileNotFoundError("F8: router-ийн кэш алга. Эхлээд (router загвар асаалттай): python -m mn_text2sql "
                                    f"route --split {split} (загвар: {router.model_name})")
    _check_cache_coverage(items, variants, translations, "A4 орчуулга", "translate")
    _check_cache_coverage(items, variants, intents, "F7 intent", "intent")
    if router is not None:
        _check_cache_coverage(items, variants, router.cache, "F8 router", "route")
    pipe = Pipeline(cfg, opts, model, llm or oracle_llm, executor=executor, guard=guard, translations=translations,
                    intents=intents, router=router)
    pipe.item_families = {it.id: it.family for it in items if it.family}
    gold = GoldCache(guard, executor)
    init_value_maps(executor)
    meta = {"config_id": opts.run_id + ("-oracle" if oracle else ""), "model": model, "split": Path(split).stem,
            "options": opts.fingerprint(), "eval_set": eval_set_version(cfg, files),
            "db_snapshot": db_snapshot(executor, cfg), "prompt_version": prompt_version(family),
            "model_revision": model_revision(mspec), "code_version": code_version(),
            "llm_base_url": cfg["llm"]["base_url"], "served_model": served, "baseline": opts.baseline(),
            "vllm_args": mspec.get("vllm_args"), "index": index_info(pipe),
            "started": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    seeds = seeds or (list(opts.seeds) if opts.sampling else [0])
    summaries = []
    for variant in variants:
        for seed in seeds:
            path = out_path(cfg, split, meta["config_id"], model, variant, seed, tag)
            path.parent.mkdir(parents=True, exist_ok=True)
            if overwrite and path.exists():
                path.unlink()
            run_hash = options_hash(meta, variant, seed)
            existing = load_records(path) if path.exists() else []
            if existing and existing[0].get("opts_hash") != run_hash:
                raise RuntimeError(f"{path.name}: өмнөх бичлэгүүд өөр тохиргоогоор (vars/--set/served model) хийгдсэн. "
                                   "Үргэлжлүүлэх боломжгүй — --overwrite (дахин) эсвэл --tag (тусдаа) ашиглана.")
            done = {r["question_id"] for r in existing}
            todo = [it for it in items if it.id not in done and it.question(variant)]
            log.info("%s %s %s s%d: %d асуулт (%d өмнө хийгдсэн)", meta["config_id"], model, variant, seed,
                     len(todo), len(done))
            lock = threading.Lock()
            t0 = time.time()
            fails = {"consecutive": 0, "total": 0}
            max_fail = int(cfg["eval"].get("max_consecutive_llm_failures", 5))

            def work(it: EvalItem, variant=variant, seed=seed, path=path, run_hash=run_hash):
                if fails["consecutive"] >= max_fail:
                    return None
                q = it.question(variant)
                if oracle_llm is not None:
                    oracle_llm.local.sql = it.gold_sql or (it.interpretations[0]["sql"] if it.interpretations
                                                           else None)
                    oracle_llm.remember(q, oracle_llm.local.sql)
                try:
                    ans = pipe.ask(q, variant, item_id=it.id, seed=seed if opts.sampling else None)
                    infra = any(c.error_stage == "llm" for c in ans.candidates)
                except Exception as e:  # LLM/сүлжээний тасалдал гэх мэт — хариулт биш
                    log.error("асуулт %s: %s", it.id, e)
                    ans, infra = None, True
                if infra:
                    # Дэд бүтцийн алдааг (vLLM унтарсан, OOM, adapter алга) хариулт болгон ХАДГАЛАХГҮЙ — дахин
                    # ажиллуулахад энэ асуулт дахин асуугдана
                    with lock:
                        fails["consecutive"] += 1
                        fails["total"] += 1
                    return None
                with lock:
                    fails["consecutive"] = 0
                sc = score_answer(cfg, it, ans, gold)
                rec = build_record(cfg, it, variant, seed, q, ans, sc, meta)
                rec["opts_hash"] = run_hash
                with lock, open(path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
                return rec["correct"]

            n_ok = 0
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futs = [pool.submit(work, it) for it in todo]
                for i, f in enumerate(as_completed(futs), 1):
                    n_ok += bool(f.result())
                    if i % 25 == 0 or i == len(futs):
                        log.info("  %d/%d (зөв %d), %.0f с", i, len(futs), n_ok, time.time() - t0)
            if fails["consecutive"] >= max_fail:
                raise RuntimeError(f"LLM {max_fail} удаа дараалан амжилтгүй ({served} @ {cfg['llm']['base_url']}). Run "
                                   "зогслоо; хадгалагдсан бичлэг хэвээр — vLLM-ийг сэргээгээд дахин ажиллуулна.")
            if fails["total"]:
                log.warning("%d асуулт LLM-ийн алдаагаар хадгалагдаагүй — дахин ажиллуулахад асуугдана", fails["total"])
            wall = time.time() - t0
            records = load_records(path)
            summ = summarize(records, cfg["eval"]["penalty_c"])
            summ.update({"config_id": meta["config_id"], "model": model, "split": meta["split"], "variant": variant,
                         "seed": seed, "tag": tag, "file": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT)
                         else str(path)})
            path.with_suffix(".summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=2),
                                                         encoding="utf-8")
            meta_out = dict(meta, variant=variant, seed=seed, opts_hash=run_hash, finished=datetime.now(
                timezone.utc).isoformat(timespec="seconds"), wall_seconds=round(wall, 1), questions_this_run=len(todo),
                gpu_seconds_per_q=round(wall / len(todo), 3) if todo else None,
                llm_usage={"prompt": getattr(pipe.llm, "total_prompt_tokens", None),
                           "completion": getattr(pipe.llm, "total_completion_tokens", None)})
            if todo:
                summ["gpu_seconds_per_q"] = meta_out["gpu_seconds_per_q"]
                path.with_suffix(".summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=2),
                                                             encoding="utf-8")
            path.with_suffix(".meta.json").write_text(json.dumps(meta_out, ensure_ascii=False, indent=2,
                                                                 default=str), encoding="utf-8")
            summaries.append({k: summ[k] for k in ("config_id", "model", "split", "variant", "seed", "n", "ex",
                                                   "ex_ci95", "executable_pct", "latency_ms", "file")})
    executor.close()
    return summaries


# ---------------------------------------------------------------------------
# Test-suite оноо (EX-ээр зөв бичлэгүүдийг хуулбар DB-үүд дээр дахин шалгана)
# ---------------------------------------------------------------------------

def ts_score_files(cfg: Config, files: list[str]) -> list[dict]:
    from .compare import build_code_maps
    from .testsuite import TestSuite
    guard = make_guard(cfg)
    main_ex = Executor(bot_dsn(cfg), statement_timeout_ms=cfg["db"]["statement_timeout_ms"])
    ts = TestSuite(cfg, guard, value_maps=build_code_maps(main_ex))
    main_ex.close()
    if not ts.dbs:
        raise FileNotFoundError("Test-suite DB алга. Эхлээд: python -m mn_text2sql testsuite-build")
    items = {it.id: it for it in load_all_eval_items(cfg)}
    out = []
    for f in files:
        path = resolve_path(f)
        records = load_records(path)
        for r in records:
            it = items.get(r["question_id"])
            if r.get("category") != "answerable" or it is None:
                continue
            if r.get("correct") and r.get("sql"):
                res = ts.check(r["sql"], it.gold_sqls(), it.order_matters)
                r["ts_correct"], r["ts_failed_db"] = res["ts_correct"], res["failed_db"] or None
            else:
                r["ts_correct"] = False
        with open(path, "w", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r, ensure_ascii=False, default=str) + "\n")
        summ = summarize(records, cfg["eval"]["penalty_c"])
        sp = path.with_suffix(".summary.json")
        old = json.loads(sp.read_text(encoding="utf-8")) if sp.exists() else {}
        old.update({k: v for k, v in summ.items()})
        sp.write_text(json.dumps(old, ensure_ascii=False, indent=2), encoding="utf-8")
        out.append({"file": f, "ex": summ["ex"], "ts_ex": summ["ts_ex"]})
    ts.close()
    return out


# ---------------------------------------------------------------------------
# Гадаад таамаг (одоогийн систем г.м.) — ижил 300/200 асуулт дээр ижил аргаар үнэлэх
# ---------------------------------------------------------------------------

def score_external(cfg: Config, pred_file: str, name: str, split: str = "dev", variant: str = "mn") -> dict:
    """pred_file: JSONL {"question_id"|"id", "sql", "variant"?, "status"?, "latency_ms"?}.
    status = abstained/refused/clarify бол хариулаагүй гэж тооцно."""
    files = split_files(cfg, split)
    items = {it.id: it for it in load_items(files)}
    executor = Executor(bot_dsn(cfg), statement_timeout_ms=cfg["db"]["statement_timeout_ms"], max_rows=200000)
    guard = make_guard(cfg)
    gold = GoldCache(guard, executor)
    init_value_maps(executor)
    meta = {"config_id": f"EXT-{name}", "model": name, "split": Path(split).stem, "eval_set": eval_set_version(cfg, files),
            "db_snapshot": db_snapshot(executor, cfg), "prompt_version": "external", "model_revision": {},
            "code_version": code_version()}
    path = out_path(cfg, split, meta["config_id"], name, variant, 0)
    path.parent.mkdir(parents=True, exist_ok=True)
    preds = {}
    for line in resolve_path(pred_file).read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            if d.get("variant", variant) == variant:
                preds[d.get("question_id") or d.get("id")] = d
    from ..pipeline.orchestrator import Candidate
    with open(path, "w", encoding="utf-8") as fh:
        for qid, it in items.items():
            p = preds.get(qid, {})
            ans = Answer(question=it.question(variant) or "", variant=variant, run_id=meta["config_id"], model=name)
            sql = p.get("sql")
            cand = Candidate(raw_sql=sql)
            if p.get("status") in NO_ANSWER:
                ans.status = p["status"]
            elif not sql:
                ans.status, cand.error_stage, cand.error = "error", "extract", "таамаг алга"
            else:
                g = guard.check(sql)
                if not g.ok:
                    ans.status, cand.error_stage, cand.error = "error", "guard", g.error
                else:
                    res = executor.run(g.sql)
                    cand.result, cand.sql, cand.ok = res, g.sql, res.ok
                    if not res.ok:
                        ans.status, cand.error_stage, cand.error = "error", "exec", res.error
                    elif res.rowcount == 0:
                        cand.error_stage = "empty"
                ans.result = cand.result
            ans.sql = sql
            ans.candidates = [cand]
            ans.latency_ms = p.get("latency_ms") or 0.0
            sc = score_answer(cfg, it, ans, gold)
            fh.write(json.dumps(build_record(cfg, it, variant, 0, ans.question, ans, sc, meta), ensure_ascii=False,
                                default=str) + "\n")
    executor.close()
    summ = summarize(load_records(path), cfg["eval"]["penalty_c"])
    summ.update({"config_id": meta["config_id"], "model": name, "split": meta["split"], "variant": variant, "seed": 0})
    path.with_suffix(".summary.json").write_text(json.dumps(summ, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"file": str(path), "ex": summ["ex"], "n": summ["n"]}
