"""Туршилтын run: eval set × хувилбар (mn/latin/en/mixed) × seed → асуулт бүрийн JSONL + summary.

Туршилтын төлөвлөгөөний "Дүрэм":
  * run бүрт: eval set-ийн хувилбар, DB snapshot, prompt template, загварын revision (HF commit), кодын хувилбар;
  * greedy нэг удаа; sampling-тай run (E2) 3 seed (дундаж ± std — report);
  * асуулт бүрийн JSONL: question_id, config_id, SQL, result hash, correct, latency, tokens, error type.
Гаралт: results/runs/<split>/<config_id>/<model>__<variant>__s<seed>[__tag].jsonl (+ .summary.json, .meta.json;
дэд бүтцийн алдаатай асуултын тоолуур .attempts.json, тасарсан сүүлийн мөр .jsonl.partial — байвал)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import psycopg2

from ..config import ROOT, Config, load_models, resolve_path
from ..db.conn import bot_dsn
from ..db.executor import ExecResult, Executor
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
    if cfg["eval"].get(f"{split}_file"):   # 2026-11-02: "fresh" = evalset/fresh.jsonl (нэрлэсэн нэмэлт хуваалт)
        return [cfg["eval"][f"{split}_file"]]
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


# F7: run-ий мөрөнд (хариулт, оноо) нөлөөлөх кодын хүрээ: prompt угсралт, хайлт, guard, текст, гүйцэтгэл.
# training/thesis/service/datagen-ийг оруулахгүй — тэдгээрийн өөрчлөлт resume-д хуурамч анхааруулга өгөхгүй.
CODE_DIGEST_DIRS = ("llm", "pipeline", "retrieval", "security", "text", "knowledge", "db")
# eval/-ээс зөвхөн мөрөнд нөлөөлөх модуль: оноо (runner, compare), eval set (dataset), gold-ийн guard (goldcheck.make_guard),
# онлайн эрт зогсолт (adaptive.AdaptiveStopper). Офлайн/тайлангийн модуль (report, reselect, metrics-ийн summary, latex,
# errors, judge, pick, annotate, split, testsuite) ОРОХГҮЙ; metrics-ээс мөрөнд нөлөөлөх нь NO_ANSWER — утгыг нь hash-лана.
CODE_DIGEST_EVAL = ("runner.py", "compare.py", "dataset.py", "goldcheck.py", "adaptive.py")
# Хариулт/оноонд нөлөөлөх тохиргоо: default.yaml (retrieval, pipeline, db timeout/өртөг, eval-ийн хүлцэл), models.yaml
# (max_tokens, sampling, schema_token_budget). experiments.yaml-ийн тухайн run-ий тохиргоо opts_hash-д аль хэдийн орсон
# (бусад run-ий засвар хуурамч анхааруулга өгнө); training/thesis/web.yaml хариултад нөлөөгүй.
CODE_DIGEST_CONFIG = ("config/default.yaml", "config/models.yaml")


def code_digest(root: Path = ROOT) -> str:
    """Resume-ийн хувилбарын шалгалт (F7): CODE_DIGEST_DIRS-ийн *.py, config.py, CODE_DIGEST_EVAL, CODE_DIGEST_CONFIG,
    knowledge/*.yaml ба few-shot сан knowledge/*.jsonl-ийн sha1 (12) + metrics.NO_ANSWER. Детерминист: posix замаар
    эрэмбэлнэ (Windows/Linux-ийн Path-ийн эрэмбэ ялгаатай), CRLF → LF. options_hash-д ОРОХГҮЙ (хуучин run-ий файлын
    нэр/hash хэвээр) — зөвхөн бичлэг ба meta-д."""
    pkg = root / "src" / "mn_text2sql"
    files = [pkg / "config.py"] + [p for d in CODE_DIGEST_DIRS for p in (pkg / d).rglob("*.py")] \
        + [pkg / "eval" / f for f in CODE_DIGEST_EVAL] + [root / f for f in CODE_DIGEST_CONFIG] \
        + list((root / "knowledge").glob("*.yaml")) + list((root / "knowledge").glob("*.jsonl"))
    rel = {f.relative_to(root).as_posix(): f for f in files if f.is_file()}
    h = hashlib.sha1()
    for name in sorted(rel):
        h.update(name.encode())
        h.update(rel[name].read_bytes().replace(b"\r\n", b"\n"))   # Windows/Kaggle-ийн мөрийн төгсгөл ижил hash өгнө
    h.update(("NO_ANSWER=" + ",".join(sorted(NO_ANSWER))).encode())
    return h.hexdigest()[:12]


def version_mismatch(existing: list[dict], meta: dict) -> dict:
    """Resume үед өмнөх бичлэгүүдийн prompt_version / code_digest одоогийнхоос өөр эсэх (F7).
    Буцаах: {key: [өмнөх утгууд (эрэмбэлсэн), одоо]}; бичлэгт тухайн талбар байхгүй (хуучин run) бол харьцуулахгүй."""
    out = {}
    for k in ("prompt_version", "code_digest"):
        prev = sorted({str(r[k]) for r in existing if r.get(k) is not None})
        cur = meta.get(k)
        if prev and cur is not None and prev != [str(cur)]:
            out[k] = [prev, cur]
    return out


def strict_resume() -> bool:
    """T2S_RESUME_STRICT=1 бол prompt/кодын хувилбар зөрсөн resume-ийг зогсооно (анхдагч: зөвхөн анхааруулга)."""
    return os.environ.get("T2S_RESUME_STRICT", "0").strip().lower() in ("1", "true", "yes")


SESSION_KEYS = ("started", "finished", "code_digest", "prompt_version", "questions_this_run", "records_written",
                "infra_unsaved")


def session_entry(m: dict) -> dict:
    """F7: нэг session-ий хураангуй (.meta.json-ий sessions[]) — мөрүүдийг аль кодын хувилбар бичсэнийг тодорхойлно."""
    cv = m.get("code_version") or {}
    return {**{k: m.get(k) for k in SESSION_KEYS}, "code_version": cv.get("commit") or cv.get("tree_sha1")}


def session_history(prev_meta: dict | None, n_existing: int = 0) -> list[dict]:
    """Өмнөх .meta.json-ий sessions[]; хуучин (sessions-гүй) meta бол түүний өөрийнх нь нэг session гэж; meta-гүй
    (зөвхөн .jsonl хуулсан) өмнөх мөрүүд байвал тодорхойгүй session гэж тэмдэглэнэ."""
    if not prev_meta:
        return [{"unknown_session": True, "records_written": n_existing}] if n_existing else []
    return list(prev_meta.get("sessions") or [session_entry(prev_meta)])


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
    хувилбар) асуултын текстээр (by_question) хайна.

    L10: буцаах SQL нь gold буюу PostgreSQL — `oracle = True` тэмдэг (name="oracle"-тэй хамт). dialect=sqlite_transpile
    үед pipeline энэ тэмдэгтэй LLM-ийн SQL-ийг SQLite→PostgreSQL хөрвүүлэлт ба postproc-д ОРУУЛАХГҮЙ
    (orchestrator.Pipeline._oracle_gold)."""

    oracle = True

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

# M2: зөвхөн холболтын түвшний алдаа (psycopg2 OperationalError/InterfaceError: сервер холболтыг хаасан, холболт
# тасарсан/хаагдсан, DB унтарч/эхэлж байна, SSL тасарсан). Сервер талын SQL-ийн алдаа «ERROR:»-оор эхэлдэг тул (синтакс,
# багана алга, төрөл, 0-д хуваах — мессеж дотроо литерал давтсан ч) ХЭЗЭЭ Ч энд орохгүй.
_CONN_ERROR = re.compile(
    r"server closed the connection|connection already closed|terminating connection|could not connect"
    r"|could not (?:receive|send) data|no connection to the server|connection to server .*failed"
    r"|connection (?:refused|reset|timed out|failed)|SSL (?:SYSCALL error|connection has been closed|error)"
    r"|EOF detected|the database system is (?:shutting down|starting up|in recovery|not yet accepting)"
    r"|cannot connect now|\b(?:OperationalError|InterfaceError|PoolError)\b", re.I)


def connection_error(res) -> bool:
    """ExecResult нь DB-ийн холболтын (дэд бүтцийн) алдаа эсэх — загварын/gold-ийн SQL-ийн алдаа биш (M2).
    Executor нь хаагдсан холболт дээрх psycopg2-ийн алдааг connection, бусад OperationalError/InterfaceError-ийг db_error,
    Python-ий бусад exception-ийг client_error гэж тэмдэглэдэг (2026-10-31) тул connection/db_error-ийг мессежээр
    (libpq-ийн стандарт мессеж) шалгана; «ERROR:»-оор эхэлсэн (SQL) мессеж үргэлж False."""
    if res is None or getattr(res, "ok", True) or getattr(res, "error_code", None) not in ("connection", "db_error"):
        return False
    msg = (getattr(res, "error", None) or "").strip()
    return not msg.upper().startswith("ERROR:") and bool(_CONN_ERROR.search(msg))


class GoldCache:
    """Gold SQL-ийн үр дүнгийн кэш. M2: амжилтгүй гүйцэтгэлийг КЭШЛЭХГҮЙ (дараагийн асуулт/хувилбар дахин оролдоно);
    хугацаа хэтэрсэн (timeout) эсвэл холболтын алдааг нэг удаа дахин оролдоно. Guard-ийн татгалзал (None) тогтмол тул
    кэшлэнэ. Executor.run-ий psycopg2 exception (зөвхөн _conn/getconn-оос: DB унтарсан, pool) нь холболтын амжилтгүй
    үр дүн болно — run бүхэлдээ унахгүй, асуулт дэд бүтцийн алдаа болно; бусад exception (код алдаа) хэвээр унана."""

    def __init__(self, guard, executor: Executor):
        self.guard = guard
        self.executor = executor
        self._cache: dict[str, object] = {}
        self._lock = threading.Lock()

    def _run(self, sql: str):
        try:
            return self.executor.run(sql)
        except psycopg2.Error as e:   # Executor._conn (getconn/set_session) — холболтын түвшний алдаа
            return ExecResult(False, error=f"connection failed: {type(e).__name__}: {e}"[:600], error_code="connection")

    def get(self, sql: str):
        with self._lock:
            if sql in self._cache:
                return self._cache[sql]
        g = self.guard.check(sql)
        res = self._run(g.sql) if g.ok else None
        if res is not None and not res.ok and (res.error_code == "timeout" or connection_error(res)):
            log.warning("gold SQL амжилтгүй (%s) — нэг удаа дахин оролдов: %s", res.error_code, (res.error or "")[:160])
            res = self._run(g.sql)
        if res is None or res.ok:
            with self._lock:
                self._cache[sql] = res
        else:
            log.warning("gold SQL амжилтгүй (%s) — кэшлэхгүй, бичлэгт gold_error: %s", res.error_code,
                        (res.error or "")[:160])
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
    out = {"correct": bool(correct), "raw_correct": bool(raw_correct), "table_recall": tr, "value_recall": vr,
           "strict_correct": bool(correct and strict) if item.category != "unanswerable" else bool(correct),
           "soft_f1": sf1, "exec_ok": exec_ok,
           "empty": bool(exec_ok and res.rowcount == 0), "cand_correct": cand_correct or None}
    failed = [g for g in golds if g is None or not getattr(g, "ok", False)]
    if failed:
        # M2: gold (эсвэл alt/тайлбар) амжилтгүй — оноо хуучнаараа (тэр gold-той таарахгүй), харин бичлэгт харагдахуйц,
        # хасах боломжтой тэмдэг (зөвхөн алдаатай үед — хэвийн бичлэгийн бүтэц хэвээр)
        out["gold_error"] = True
        out["gold_error_code"] = ",".join(sorted({_gold_error_code(g) for g in failed}))
    return out


def _gold_error_code(g) -> str:
    """guard (gold нь guard-ыг давсангүй) | connection (DB холболт — дэд бүтэц) | timeout | cost_too_high | db_error."""
    if g is None:
        return "guard"
    return "connection" if connection_error(g) else (getattr(g, "error_code", None) or "error")


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
        "rescued": sum(bool(getattr(c, "rescued", False)) for c in ans.candidates) or None,   # R7 #5
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


def serving_info(cfg: Config, model: str, mspec: dict, query_server: bool = True) -> dict:
    """Reproducibility (R7 #1): бодит vLLM аргумент (serve_model.sh-ийн дүрмээр T2S_VLLM_ARGS__<нэр> →
    T2S_VLLM_ARGS → models.yaml), сервер /v1/models (max_model_len, root), vLLM хувилбар, GPU. options_hash-д
    ОРОХГҮЙ — зөвхөн meta-д. Аль нэг нь олдохгүй бол None (run-ийг зогсоохгүй)."""
    env_key = "T2S_VLLM_ARGS__" + model.replace("-", "_").replace(".", "_")
    env_args = os.environ.get(env_key) or os.environ.get("T2S_VLLM_ARGS") or None
    out: dict = {"vllm_args_env": env_args,
                 "vllm_args_source": (env_key if os.environ.get(env_key) else "T2S_VLLM_ARGS") if env_args
                 else "models.yaml",
                 "vllm_args_effective": env_args or mspec.get("vllm_args"),
                 "server_models": None, "vllm_version": None, "gpu": None}
    if query_server:
        import httpx
        base = cfg["llm"]["base_url"].rstrip("/")
        try:
            r = httpx.get(base + "/models", timeout=5)
            r.raise_for_status()
            out["server_models"] = [{k: m.get(k) for k in ("id", "root", "parent", "max_model_len")}
                                    for m in r.json().get("data", [])]
        except Exception as e:  # noqa: BLE001 — meta-гийн мэдээлэл, run-д нөлөөгүй
            log.debug("serving_info /models: %s", e)
        try:   # vLLM-ийн /version нь /v1-ээс гадна
            r = httpx.get((base[:-3] if base.endswith("/v1") else base) + "/version", timeout=5)
            r.raise_for_status()
            out["vllm_version"] = r.json().get("version")
        except Exception as e:  # noqa: BLE001
            log.debug("serving_info /version: %s", e)
    if not out["vllm_version"]:
        try:
            from importlib.metadata import PackageNotFoundError, version
            out["vllm_version"] = version("vllm")
        except (ImportError, PackageNotFoundError, ValueError):
            pass
    try:
        q = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"],
                           capture_output=True, text=True, timeout=10)
        if q.returncode == 0 and q.stdout.strip():
            out["gpu"] = [line.strip() for line in q.stdout.strip().splitlines()]
    except (OSError, subprocess.SubprocessError):
        pass
    return out


RUNTIME_KEYS = ("max_tokens", "workers", "vllm_args_effective")


def runtime_mismatch(prev_meta: dict | None, meta: dict) -> dict:
    """Resume үед өмнөх .meta.json-ий hash-д ордоггүй тохиргоо (max_tokens, workers, vLLM аргумент) өөр эсэх.
    Буцаах: {key: [өмнөх, одоо]}; өмнөх meta-д тухайн түлхүүр байхгүй (хуучин run) бол харьцуулахгүй."""
    if not prev_meta:
        return {}
    cur = dict(meta, **(meta.get("serving") or {}))
    prev = dict(prev_meta, **(prev_meta.get("serving") or {}))
    return {k: [prev[k], cur.get(k)] for k in RUNTIME_KEYS if k in prev and prev[k] != cur.get(k)}


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


# ---------------------------------------------------------------------------
# Run-ий файл: нэг write-аар нэмэх, тасарсан сүүлийн мөр (L7), дэд бүтцийн алдааны тоолуур (L3)
# ---------------------------------------------------------------------------

def append_record(path: Path, rec: dict) -> None:
    """L7: бичлэгийг НЭГ write() дуудлагаар (буфергүй binary append, O_APPEND) нэмнэ — текст горимын буфер >8 KB
    бичлэгийг хэд хэдэн хэсэг болгож, Kaggle-ийн kill/timeout-д мөрийг дундуур нь тасалдаг байв. Тасарсан ч зөвхөн
    сүүлийн мөр (load_records хаяна, resume-д repair_jsonl_tail засна). Мөрийн төгсгөл үргэлж LF."""
    data = (json.dumps(rec, ensure_ascii=False, default=str) + "\n").encode("utf-8")
    with open(path, "ab", buffering=0) as fh:
        n = fh.write(data)
        while n < len(data):   # ердийн файлд нэг дуудлагаар бүтэн бичигдэнэ; хэсэгчилсэн бол үлдэгдлийг
            n += fh.write(data[n:])


def repair_jsonl_tail(path: Path) -> int:
    """L7 (resume-ийн өмнө): файл бүтэн мөрөөр (LF) төгсөөгүй бол дараагийн append тасарсан мөртэй нийлж ДУНД мөрийг
    эвдэнэ (load_records-д засагдахгүй). Сүүлийн мөр бүтэн JSON бол зөвхөн LF нэмнэ; эс бөгөөс тасарсан хэсгийг
    <run>.jsonl.partial-д хадгалж (устгахгүй), файлыг сүүлийн бүтэн мөр хүртэл тайрна. Буцаах: зөөсөн байтын тоо."""
    if not path.exists():
        return 0
    data = path.read_bytes()
    if not data or data.endswith(b"\n"):
        return 0
    cut = data.rfind(b"\n") + 1
    tail = data[cut:]
    try:
        json.loads(tail.decode("utf-8").strip())
        with open(path, "ab") as fh:
            fh.write(b"\n")
        return 0
    except ValueError:
        pass
    with open(path.with_name(path.name + ".partial"), "ab") as fh:
        fh.write(tail + b"\n")
    with open(path, "r+b") as fh:
        fh.truncate(cut)
    log.warning("%s: тасарсан сүүлийн мөрийг (%d байт) %s.partial руу зөөв — тэр асуултыг дахин асууна", path.name,
                len(tail), path.name)
    return len(tail)


def infra_error(ans: Answer) -> str | None:
    """Хариулт биш, дэд бүтцийн алдаа (бичлэг хийхгүй — дахин асууна): LLM-ийн дуудлага (error_stage="llm") эсвэл
    M2: таамгийн SQL-ийг гүйцэтгэх үеийн DB холболтын алдаа (connection_error; SQL-ийн алдаа ХЭЗЭЭ Ч биш)."""
    for c in ans.candidates:
        if c.error_stage == "llm":
            return f"llm: {c.error or ''}"[:500]
        if c.error_stage == "exec" and connection_error(c.result):
            return f"db connection: {c.error or ''}"[:500]
    return None


def attempts_path(path: Path) -> Path:
    """L3: асуулт бүрийн дэд бүтцийн алдааны тоолуур — run-ий хажууд (Kaggle-ийн архивт results/-ийн хамт хадгалагдана)."""
    return path.with_suffix(".attempts.json")


def load_attempts(path: Path) -> dict[str, int]:
    p = attempts_path(path)
    if not p.exists():
        return {}
    try:
        return {str(k): int(v) for k, v in json.loads(p.read_text(encoding="utf-8")).items()}
    except (OSError, ValueError, TypeError, AttributeError) as e:
        log.warning("%s уншсангүй (%s) — тоолуурыг 0-ээс", p.name, e)
        return {}


def bump_attempts(path: Path, failed: list[str]) -> dict[str, int]:
    """L3: энэ session-д дэд бүтцийн алдаагаар хадгалагдаагүй асуулт бүрийн тоолуурыг +1 (атомаар бичнэ). Буцаах: бүх тоо."""
    counts = load_attempts(path)
    for qid in failed:
        counts[qid] = counts.get(qid, 0) + 1
    p = attempts_path(path)
    tmp = p.with_name(p.name + ".tmp")
    tmp.write_text(json.dumps(counts, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)
    return counts


def infra_healthy(cfg: Config, executor: Executor, served: str, check_llm: bool) -> bool:
    """L3: session-ий төгсгөлд DB (SELECT 1) ба (жинхэнэ vLLM-тэй үед) served model жинхэнээр үүсгэж (max_tokens=1)
    чадаж байгаа эсэх. Тасалдлын (vLLM/DB унтарсан) үеийн алдааг асуултын тоолуурт тооцохгүй — тоолуур зөвхөн
    дэд бүтэц хэвийн үед давтагдсан, тухайн асуултын алдааг (context хэтэрсэн г.м.) хязгаарлана."""
    try:
        if not executor.run("SELECT 1").ok:
            return False
        if check_llm:
            import httpx
            r = httpx.post(cfg["llm"]["base_url"].rstrip("/") + "/chat/completions", timeout=120,
                           json={"model": served, "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1,
                                 "temperature": 0.0})
            r.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001 — шалгалт амжилтгүй = дэд бүтэц хэвийн биш
        log.warning("дэд бүтцийн шалгалт амжилтгүй: %s", str(e)[:200])
        return False


def infra_record(cfg: Config, item: EvalItem, variant: str, seed: int, question: str, error: str, attempts: int,
                 gold: GoldCache, meta: dict) -> dict:
    """L3: max_infra_attempts session-д (дэд бүтэц хэвийн үед) дэд бүтцийн алдаатай асуулт — error_stage="infra",
    status="error" (бүх ангилалд буруу, TrustSQL −c), харагдахуйц бичлэг; run дуусах боломжтой (хязгааргүй асуухгүй)."""
    from ..pipeline.orchestrator import Candidate
    ans = Answer(question=question, variant=variant, run_id=meta["config_id"], model=meta["model"])
    ans.status = "error"
    ans.candidates = [Candidate(error=f"{attempts} session-д дэд бүтцийн алдаа: {error}"[:500], error_stage="infra")]
    ans.chosen_index = 0
    rec = build_record(cfg, item, variant, seed, question, ans, score_answer(cfg, item, ans, gold), meta)
    rec["infra_attempts"] = attempts
    return rec


def file_digest(p: Path | str | None) -> dict | None:
    """L8: кэш/өгөгдлийн файлын зам + sha1 (12; CRLF → LF, Windows/Kaggle ижил) — meta-д (options_hash-д ОРОХГҮЙ)."""
    if p is None:
        return None
    p = Path(p)
    name = p.relative_to(ROOT).as_posix() if p.is_absolute() and p.is_relative_to(ROOT) else p.as_posix()
    if not p.is_file():
        return {"path": name, "exists": False}
    return {"path": name, "sha1": hashlib.sha1(p.read_bytes().replace(b"\r\n", b"\n")).hexdigest()[:12]}


def _sha1_file(p: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha1()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(chunk), b""):
            h.update(b)
    return h.hexdigest()[:12]


WEIGHT_SUFFIXES = (".json", ".safetensors", ".bin", ".model", ".txt", ".jinja")


def served_identity(served: str, adapter: str | None, mspec: dict, serving: dict | None,
                    max_hash_bytes: int = 1 << 30) -> dict:
    """L8: served нэр яг аль жин болох — суурь загвар (hf_id), LoRA adapter-ийн нэр, vLLM /v1/models-ийн root/parent;
    root нь локал хавтас бол жингийн файл бүрийн хэмжээ + sha1 (жижгээс нь эхлэн нийт ≤ max_hash_bytes: LoRA ~100–300 MB
    бүтнээрээ, нэгтгэсэн 15 GB загварын shard-ыг зөвхөн хэмжээгээр). options_hash-д ОРОХГҮЙ — зөвхөн meta-д."""
    entry = next((m for m in (serving or {}).get("server_models") or [] if m.get("id") == served), None) or {}
    out: dict = {"served": served, "adapter": adapter, "base_hf_id": mspec.get("hf_id"),
                 "root": entry.get("root"), "parent": entry.get("parent")}
    root = Path(entry["root"]) if entry.get("root") else None
    try:
        if root is not None and root.is_dir():
            sizes = {p: p.stat().st_size for p in root.iterdir() if p.is_file() and p.suffix in WEIGHT_SUFFIXES}
            files, budget = {}, max_hash_bytes
            for p, size in sorted(sizes.items(), key=lambda kv: (kv[1], kv[0].name)):
                digest = None
                if size <= budget:
                    digest, budget = _sha1_file(p), budget - size
                files[p.name] = {"bytes": size, "sha1": digest}
            out["files"] = dict(sorted(files.items()))
    except OSError as e:   # meta-гийн мэдээлэл — run-ийг зогсоохгүй
        out["files_error"] = str(e)[:200]
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
    caches: dict = {}   # L8: бодитоор ашигласан кэш файл (зам + sha1) — meta-д (hash-д ОРОХГҮЙ)
    if opts.translate:
        name = opts.translation_cache or registry.get("translator")
        tr_path = resolve_path("build/translations") / f"{name}.jsonl"
        translations = _load_cache(tr_path)
        if not translations:
            raise FileNotFoundError("A4: орчуулгын кэш алга. Эхлээд: python -m mn_text2sql translate --split "
                                    f"{split} (орчуулагч: {registry.get('translator')})")
        caches["translation"] = dict(file_digest(tr_path), name=name, source="options.translation_cache"
                                     if opts.translation_cache else "models.yaml:translator")
    if opts.intent:
        int_path = resolve_path("build/intents") / f"{registry.get('intent_model')}.jsonl"
        intents = _load_cache(int_path)
        if not intents:
            raise FileNotFoundError("F7: intent кэш алга. Эхлээд: python -m mn_text2sql intent --split "
                                    f"{split} (загвар: {registry.get('intent_model')})")
        caches["intents"] = dict(file_digest(int_path), model=registry.get("intent_model"))
    router = None
    if opts.semantic_router:
        from ..pipeline.router import SemanticRouter
        router = SemanticRouter(cfg)
        if not router.cache:
            raise FileNotFoundError("F8: router-ийн кэш алга. Эхлээд (router загвар асаалттай): python -m mn_text2sql "
                                    f"route --split {split} (загвар: {router.model_name})")
        caches["router"] = dict(file_digest(resolve_path("build/routes") / f"{router.model_name}.jsonl"),
                                model=router.model_name)
    if opts.glossary and opts.glossary_select_cache:   # R: сургасан reranker-ийн урьдчилсан сонголт (build/rerank/<tag>.json)
        caches["glossary_select"] = file_digest(resolve_path(opts.glossary_select_cache))
    _check_cache_coverage(items, variants, translations, "A4 орчуулга", "translate")
    _check_cache_coverage(items, variants, intents, "F7 intent", "intent")
    if router is not None:
        _check_cache_coverage(items, variants, router.cache, "F8 router", "route")
    pipe = Pipeline(cfg, opts, model, llm or oracle_llm, executor=executor, guard=guard, translations=translations,
                    intents=intents, router=router)
    pipe.item_families = {it.id: it.family for it in items if it.family}
    gold = GoldCache(guard, executor)
    init_value_maps(executor)
    serving = serving_info(cfg, model, mspec, query_server=llm is None and oracle_llm is None)
    meta = {"config_id": opts.run_id + ("-oracle" if oracle else ""), "model": model, "split": Path(split).stem,
            "options": opts.fingerprint(), "eval_set": eval_set_version(cfg, files),
            "db_snapshot": db_snapshot(executor, cfg), "prompt_version": prompt_version(family),
            "model_revision": model_revision(mspec), "code_version": code_version(),
            "code_digest": code_digest(),   # F7: resume-ийн хувилбарын шалгалт (hash-д ОРОХГҮЙ)
            "llm_base_url": cfg["llm"]["base_url"], "served_model": served, "baseline": opts.baseline(),
            "vllm_args": mspec.get("vllm_args"), "index": index_info(pipe),
            # R7 #1: hash-д ордоггүй гүйцэтгэлийн тохиргоо (resume-д зөрвөл анхааруулна)
            "max_tokens": pipe.max_tokens, "workers": workers, "serving": serving,
            # L8: served нэрийн цаадах жин (adapter/нэгтгэсэн загвар) ба ашигласан кэш файл (hash-д ОРОХГҮЙ)
            "served_identity": served_identity(served, opts.adapter, mspec, serving), "caches": caches,
            "started": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    seeds = seeds or (list(opts.seeds) if opts.sampling else [0])
    max_infra = int(cfg["eval"].get("max_infra_attempts", 3))   # L3: ≤ 0 бол хязгааргүй дахин асууна (хуучин зан төлөв)
    summaries = []
    for variant in variants:
        for seed in seeds:
            path = out_path(cfg, split, meta["config_id"], model, variant, seed, tag)
            path.parent.mkdir(parents=True, exist_ok=True)
            if overwrite and path.exists():
                path.unlink()
            if overwrite:
                attempts_path(path).unlink(missing_ok=True)   # L3: шинэ run — өмнөх тоолуур хамаарахгүй
            run_hash = options_hash(meta, variant, seed)
            repair_jsonl_tail(path)   # L7: тасарсан сүүлийн мөр дараагийн append-тай нийлж дунд мөрийг эвдэхгүй
            existing = load_records(path) if path.exists() else []
            if existing and existing[0].get("opts_hash") != run_hash:
                raise RuntimeError(f"{path.name}: өмнөх бичлэгүүд өөр тохиргоогоор (vars/--set/served model) хийгдсэн. "
                                   "Үргэлжлүүлэх боломжгүй — --overwrite (дахин) эсвэл --tag (тусдаа) ашиглана.")
            meta_path = path.with_suffix(".meta.json")
            prev_meta = None
            if existing and meta_path.exists():
                try:
                    prev_meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except (OSError, ValueError) as e:
                    log.debug("өмнөх meta уншсангүй: %s", e)
            mismatch = runtime_mismatch(prev_meta, meta) if existing else {}
            if mismatch:
                log.warning("%s: resume — hash ижил боловч гүйцэтгэлийн тохиргоо өөр (%s). Мөрүүд холилдоно; "
                            "цэвэр харьцуулалтад --tag эсвэл --overwrite ашиглана.", path.name,
                            ", ".join(f"{k}: {a} → {b}" for k, (a, b) in mismatch.items()))
            vmismatch = version_mismatch(existing, meta) if existing else {}
            if vmismatch:
                msg = (f"{path.name}: resume — өмнөх бичлэгүүд өөр prompt/кодын хувилбараар хийгдсэн ("
                       + ", ".join(f"{k}: {a} → {b}" for k, (a, b) in vmismatch.items()) + ")")
                if strict_resume():
                    raise RuntimeError(msg + ". T2S_RESUME_STRICT=1 — --overwrite (дахин) эсвэл --tag (тусдаа) ашиглана.")
                log.warning("%s. Мөрүүд холилдоно; цэвэр харьцуулалтад --tag/--overwrite (T2S_RESUME_STRICT=1 бол "
                            "зогсооно).", msg)
            done = {r["question_id"] for r in existing}
            todo = [it for it in items if it.id not in done and it.question(variant)]
            log.info("%s %s %s s%d: %d асуулт (%d өмнө хийгдсэн)", meta["config_id"], model, variant, seed,
                     len(todo), len(done))
            lock = threading.Lock()
            t0 = time.time()
            fails = {"consecutive": 0, "total": 0, "written": 0}
            infra_seen: dict[str, str] = {}   # L3: энэ session-д дэд бүтцийн алдаагаар хадгалагдаагүй асуулт → алдаа
            max_fail = int(cfg["eval"].get("max_consecutive_llm_failures", 5))

            def work(it: EvalItem, variant=variant, seed=seed, path=path, run_hash=run_hash, fails=fails, lock=lock,
                     infra_seen=infra_seen, max_fail=max_fail):
                if fails["consecutive"] >= max_fail:
                    return None
                q = it.question(variant)
                if oracle_llm is not None:
                    oracle_llm.local.sql = it.gold_sql or (it.interpretations[0]["sql"] if it.interpretations
                                                           else None)
                    oracle_llm.remember(q, oracle_llm.local.sql)
                try:
                    ans = pipe.ask(q, variant, item_id=it.id, seed=seed if opts.sampling else None)
                    err = infra_error(ans)
                except Exception as e:  # LLM/сүлжээний тасалдал гэх мэт — хариулт биш
                    log.error("асуулт %s: %s", it.id, e)
                    ans, err = None, f"{type(e).__name__}: {e}"[:500]
                sc = None
                if err is None:
                    sc = score_answer(cfg, it, ans, gold)
                    if "connection" in (sc.get("gold_error_code") or "").split(","):
                        err = "gold SQL: DB холболтын алдаа"   # M2: дэд бүтэц — gold-ийн алдаа гэж буруу бичихгүй
                if err is not None:
                    # Дэд бүтцийн алдааг (vLLM унтарсан, OOM, adapter алга, DB холболт) хариулт болгон ХАДГАЛАХГҮЙ —
                    # дахин ажиллуулахад энэ асуулт дахин асуугдана (L3: max_infra session-ийн дараа infra бичлэг)
                    with lock:
                        fails["consecutive"] += 1
                        fails["total"] += 1
                        infra_seen[it.id] = err
                    return None
                with lock:
                    fails["consecutive"] = 0
                rec = build_record(cfg, it, variant, seed, q, ans, sc, meta)
                rec["opts_hash"] = run_hash
                rec["code_digest"] = meta["code_digest"]
                with lock:
                    append_record(path, rec)
                    fails["written"] += 1
                return rec["correct"]

            n_ok = 0
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futs = [pool.submit(work, it) for it in todo]
                for i, f in enumerate(as_completed(futs), 1):
                    n_ok += bool(f.result())
                    if i % 25 == 0 or i == len(futs):
                        log.info("  %d/%d (зөв %d), %.0f с", i, len(futs), n_ok, time.time() - t0)
            n_infra_rec = 0
            if infra_seen and max_infra > 0:
                # L3: дэд бүтэц ХЭВИЙН үед (тасалдал биш) давтагдсан асуулт бүрийн алдааг тоолж, max_infra session-ийн
                # дараа error_stage="infra" (буруу, харагдахуйц) гэж бичнэ — run хязгааргүй дахин асуухгүй, дуусна
                if infra_healthy(cfg, executor, served, check_llm=llm is None and oracle_llm is None):
                    counts = bump_attempts(path, sorted(infra_seen))
                    by_id = {it.id: it for it in todo}
                    for qid in sorted(infra_seen):
                        if counts[qid] < max_infra:
                            continue
                        it = by_id[qid]
                        rec = infra_record(cfg, it, variant, seed, it.question(variant), infra_seen[qid], counts[qid],
                                           gold, meta)
                        rec["opts_hash"] = run_hash
                        rec["code_digest"] = meta["code_digest"]
                        append_record(path, rec)
                        n_infra_rec += 1
                        log.warning("%s: %d session дэд бүтцийн алдаатай — error_stage=infra (буруу) гэж бичив: %s", qid,
                                    counts[qid], infra_seen[qid][:200])
                else:
                    log.warning("DB/vLLM хэвийн биш (тасалдал) — %d асуултын infra тоолуурыг нэмэхгүй", len(infra_seen))
            if fails["consecutive"] >= max_fail:
                raise RuntimeError(f"LLM/DB {max_fail} удаа дараалан амжилтгүй ({served} @ {cfg['llm']['base_url']}; "
                                   f"сүүлийн алдаа: {list(infra_seen.values())[-1][:200] if infra_seen else '-'}). Run "
                                   "зогслоо; хадгалагдсан бичлэг хэвээр — vLLM/DB-ийг сэргээгээд дахин ажиллуулна.")
            if fails["total"] - n_infra_rec:
                log.warning("%d асуулт дэд бүтцийн (LLM/DB) алдаагаар хадгалагдаагүй — дахин ажиллуулахад асуугдана",
                            fails["total"] - n_infra_rec)
            wall = time.time() - t0
            records = load_records(path) if path.exists() else []   # бүх асуулт дэд бүтцийн алдаатай бол файл алга
            summ = summarize(records, cfg["eval"]["penalty_c"])
            summ.update({"config_id": meta["config_id"], "model": model, "split": meta["split"], "variant": variant,
                         "seed": seed, "tag": tag, "file": str(path.relative_to(ROOT)) if path.is_relative_to(ROOT)
                         else str(path)})
            sum_path = path.with_suffix(".summary.json")
            if todo:
                summ["gpu_seconds_per_q"] = round(wall / len(todo), 3)
            elif sum_path.exists():   # F7: хийх зүйлгүй resume — мөрүүдийг үүсгэсэн session-ий хэмжилтийг хадгална
                try:
                    prev_gpu = json.loads(sum_path.read_text(encoding="utf-8")).get("gpu_seconds_per_q")
                except (OSError, ValueError):
                    prev_gpu = None
                if prev_gpu is not None:
                    summ["gpu_seconds_per_q"] = prev_gpu
            sum_path.write_text(json.dumps(summ, ensure_ascii=False, indent=2), encoding="utf-8")
            meta_out = dict(meta, variant=variant, seed=seed, opts_hash=run_hash, finished=datetime.now(
                timezone.utc).isoformat(timespec="seconds"), wall_seconds=round(wall, 1), questions_this_run=len(todo),
                gpu_seconds_per_q=round(wall / len(todo), 3) if todo else None,
                llm_usage={"prompt": getattr(pipe.llm, "total_prompt_tokens", None),
                           "completion": getattr(pipe.llm, "total_completion_tokens", None)},
                records_written=fails["written"] + n_infra_rec, infra_unsaved=fails["total"] - n_infra_rec)
            if mismatch:
                meta_out["resume_runtime_mismatch"] = mismatch
            if vmismatch:
                meta_out["resume_version_mismatch"] = vmismatch
            # F7: session бүрийн хувилбар (code_digest, prompt_version) — олон session-оор үүссэн мөрүүдийг тодорхойлно
            meta_out["sessions"] = session_history(prev_meta, len(existing)) + [session_entry(meta_out)]
            if todo or not meta_path.exists():
                if not todo:
                    meta_out["meta_reconstructed"] = True   # мөрүүд өмнө (meta-гүй) үүссэн — энэ session юу ч бичээгүй
                meta_path.write_text(json.dumps(meta_out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
            else:
                log.info("%s: хийх асуулт алга — .meta.json хэвээр (мөрүүдийг үүсгэсэн session-уудыг тодорхойлно)",
                         path.name)
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
