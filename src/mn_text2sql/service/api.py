"""FastAPI үйлчилгээ (зааврын 6–7-р хэсэг: shadow → pilot, UX, audit, feedback loop).

* POST /ask       {question, variant?, user?} → SQL, үр дүн (≤ 200 мөр), ашигласан хүснэгт, монгол тайлбар, итгэл.
                  shadow горимд хариултыг хэрэглэгчид буцаахгүй (зөвхөн audit-д), pilot/live горимд буцаана.
* POST /feedback  {request_id, correct, corrected_sql?, comment?} → шинжээчийн үнэлгээ (жишээний сан руу
                  хянаж нэмэх feedback loop; docs/SECURITY.md).
* GET  /health
Audit log: асуулт бүр (хугацаа, хэрэглэгчийн hash, SQL, төлөв, хүснэгт, мөрийн тоо, хугацаа). Асуултын текст ба
feedback-ийг T2S_AUDIT_KEY (Fernet) түлхүүрээр шифрлэнэ; түлхүүргүй бол текстийг хадгалахгүй (зөвхөн hash).
Хадгалах хугацаа: security.audit_retention_days — эхлэхэд ба өдөр бүр хуучин мөрийг устгана.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import Config, resolve_path

log = logging.getLogger(__name__)


class AuditLog:
    def __init__(self, path: Path, key_env: str = "T2S_AUDIT_KEY", retention_days: int = 90):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.retention = timedelta(days=int(retention_days))
        self.lock = threading.Lock()
        self.fernet = None
        key = os.environ.get(key_env)
        if key:
            from cryptography.fernet import Fernet
            self.fernet = Fernet(key.encode() if isinstance(key, str) else key)
        else:
            log.warning("%s тохируулаагүй — асуултын текстийг audit-д хадгалахгүй (зөвхөн hash)", key_env)
        self._last_purge = 0.0
        self.purge()

    def _enc(self, text: str | None) -> str | None:
        if text is None:
            return None
        if self.fernet is None:
            return None
        return self.fernet.encrypt(text.encode("utf-8")).decode()

    def decrypt(self, token: str) -> str:
        if self.fernet is None:
            raise RuntimeError("түлхүүргүй")
        return self.fernet.decrypt(token.encode()).decode("utf-8")

    def write(self, record: dict, secret_fields: tuple[str, ...] = ("question", "comment", "corrected_sql", "sql")) -> None:
        rec = dict(record)
        for f in secret_fields:
            if f in rec and rec[f] is not None:
                rec[f + "_sha1"] = hashlib.sha1(str(rec[f]).encode("utf-8")).hexdigest()[:16]
                rec[f] = self._enc(str(rec[f]))
        rec["ts"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with self.lock, open(self.path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        if time.time() - self._last_purge > 86400:
            self.purge()

    def purge(self) -> int:
        self._last_purge = time.time()
        if not self.path.exists():
            return 0
        cutoff = datetime.now(timezone.utc) - self.retention
        kept, dropped = [], 0
        with self.lock:
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    ts = datetime.fromisoformat(json.loads(line)["ts"])
                except (ValueError, KeyError, json.JSONDecodeError):
                    dropped += 1
                    continue
                if ts >= cutoff:
                    kept.append(line)
                else:
                    dropped += 1
            if dropped:
                self.path.write_text("\n".join(kept) + ("\n" if kept else ""), encoding="utf-8")
        return dropped


# FastAPI нь `from __future__ import annotations`-тай үед загварыг модулийн түвшинд харах ёстой
from pydantic import BaseModel, Field  # noqa: E402


class AskIn(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
    variant: str | None = None
    user: str | None = None


class FeedbackIn(BaseModel):
    request_id: str
    correct: bool
    corrected_sql: str | None = None
    comment: str | None = Field(default=None, max_length=2000)
    user: str | None = None


def _user_hash(user: str | None) -> str | None:
    if not user:
        return None
    pepper = os.environ.get("T2S_USER_PEPPER", "t2s")
    return hashlib.sha256((pepper + ":" + user).encode("utf-8")).hexdigest()[:16]


def create_app(cfg: Config, run_id: str | None = None):
    from fastapi import FastAPI, HTTPException

    from ..db.conn import bot_dsn
    from ..db.executor import Executor
    from ..knowledge.semantic import cached_model
    from ..pipeline.options import resolve_run
    from ..pipeline.orchestrator import Pipeline
    from ..security.sql_guard import SqlGuard

    svc = cfg.get("service") or {}
    mode = svc.get("mode", "shadow")
    run_id = run_id or svc.get("run_id", "E1")
    model_name = svc.get("model") or cfg["llm"]["model"]
    opts = resolve_run(run_id, overrides={"intent_check": bool(cfg["pipeline"].get("intent_check", True))})
    model = cached_model(cfg["knowledge"]["semantic_model"])
    guard = SqlGuard(model.allowed(), model.schema, set(cfg["security"]["forbid_functions"]),
                     str(cfg["project"]["as_of_date"]), enforce_limit=int(cfg["security"]["enforce_limit"]))
    executor = Executor(bot_dsn(cfg), statement_timeout_ms=cfg["db"]["statement_timeout_ms"],
                        max_rows=int(cfg["security"]["enforce_limit"]), max_cost=float(cfg["db"]["max_explain_cost"]))
    # Вэб (локал CPU, LM Studio г.м. OpenAI-тэй нийцтэй сервер): T2S_WEB_MODEL = SQL загварын нэр (сервер дээрх),
    # T2S_WEB_TRANSLATOR = орчуулагч загварын нэр (байхгүй бол орчуулгагүй, монгол асуултаар)
    llm = translator = None
    from ..llm.client import make_llm
    if os.environ.get("T2S_WEB_MODEL"):
        llm = make_llm(dict(cfg["llm"], model=os.environ["T2S_WEB_MODEL"]))
    if os.environ.get("T2S_WEB_TRANSLATOR"):
        from ..config import load_models
        tname = os.environ["T2S_WEB_TRANSLATOR"]
        tspec = load_models().get("models", {}).get(tname, {})
        tcfg = dict(cfg["llm"], model=tspec.get("served_name", tname),
                    base_url=os.environ.get("T2S_WEB_TRANSLATOR_URL") or cfg["llm"]["base_url"])
        if tspec.get("extra_body"):
            tcfg["extra_body"] = tspec["extra_body"]
        translator = make_llm(tcfg)
    entity_keys_web = None
    if translator is not None:   # T2 орчуулга: маскыг DB-ийн оноосон нэрээр шүүнэ
        from ..pipeline.translate import ENTITY_COLUMNS
        from ..pipeline.translate_fix import entity_keys
        entity_keys_web = entity_keys(executor, ENTITY_COLUMNS)
    pipe = Pipeline(cfg, opts, model_name, llm=llm, executor=executor, guard=guard)
    audit = AuditLog(resolve_path(cfg["security"]["audit_log"]), cfg["security"]["audit_encrypt_key_env"],
                     int(cfg["security"]["audit_retention_days"]))
    feedback = AuditLog(resolve_path(cfg["security"]["audit_log"]).with_name("feedback.jsonl"),
                        cfg["security"]["audit_encrypt_key_env"], int(cfg["security"]["audit_retention_days"]))
    app = FastAPI(title="mn-text2sql", version="1.0")

    @app.get("/health")
    def health():
        return {"ok": True, "mode": mode, "run": run_id, "model": model_name}

    web_key = os.environ.get("T2S_WEB_KEY")

    def check_key(k: str | None) -> None:
        if web_key and k != web_key:
            raise HTTPException(status_code=403, detail="Холбоосны түлхүүр буруу (URL-ийн ?k=... хэсгийг бүтнээр нь нээнэ үү)")

    # Flow builder (/builder): pipeline-ийг node-оор угсарч, энэ серверийн загвар/DB-ээр нэг асуултаар турших
    from .flow import FlowRunner, make_router
    app.include_router(make_router(FlowRunner(cfg, model_name, pipe.llm, executor, guard, translator, entity_keys_web),
                                   check_key))

    @app.post("/ask")
    def ask(inp: AskIn, k: str | None = None):
        check_key(k)
        return answer(inp)

    # Вэб (Cloudflare tunnel ~100 с-ээс урт хүсэлтийг тасалдаг): POST /ask_job → {job}, GET /job/{job} → {done, result|error}
    jobs: dict[str, dict] = {}

    @app.post("/ask_job")
    def ask_job(inp: AskIn, k: str | None = None):
        check_key(k)
        import threading
        jid = uuid.uuid4().hex[:12]
        jobs[jid] = {"done": False, "t0": time.time()}

        def work():
            try:
                jobs[jid].update(result=answer(inp))
            except HTTPException as e:
                jobs[jid].update(error=e.detail)
            except Exception as e:  # pragma: no cover
                jobs[jid].update(error=f"{type(e).__name__}: {e}"[:800])
            jobs[jid]["done"] = True
        threading.Thread(target=work, daemon=True).start()
        for old in [j for j, v in jobs.items() if time.time() - v["t0"] > 3600]:
            jobs.pop(old, None)
        return {"job": jid}

    @app.get("/job/{jid}")
    def job(jid: str, k: str | None = None):
        check_key(k)
        if jid not in jobs:
            raise HTTPException(status_code=404, detail="Ажил олдсонгүй (сервер дахин эхэлсэн байж магадгүй)")
        v = jobs[jid]
        return {"done": v["done"], "elapsed_s": round(time.time() - v["t0"]), "result": v.get("result"),
                "error": v.get("error")}

    def answer(inp: AskIn) -> dict:
        rid = uuid.uuid4().hex[:12]
        t0 = time.perf_counter()
        question_en = term_hints = None
        try:
            item_id = None
            if translator is not None and opts.translate:
                from ..config import load_models
                from ..pipeline.translate import translate_one
                tr = translate_one(inp.question.strip(), translator, pipe.retriever, keys=entity_keys_web,
                                   hints=True, fmt=load_models().get("translator_prompt", "v1"))
                question_en = tr["text"]
                term_hints = tr.get("hints")
                item_id = f"live-{rid}"
                pipe.translations[f"{item_id}:{inp.variant or opts.variant}"] = question_en
            ans = pipe.ask(inp.question.strip(), inp.variant, item_id=item_id)
        except Exception as e:  # LLM/DB тасалдал
            audit.write({"request_id": rid, "user": _user_hash(inp.user), "question": inp.question, "status": "failed",
                         "error": str(e)[:300]})
            log.exception("ask %s амжилтгүй", rid)
            # түлхүүртэй вэб (зөвхөн эзэмшигч) бол шалтгааныг харуулна
            why = f" ({type(e).__name__}: {str(e)[:400]})" if web_key else ""
            raise HTTPException(status_code=503, detail="Түр алдаа гарлаа. Дахин оролдоно уу." + why) from e
        out = ans.to_dict(rows=200)
        if ans.chosen_index is not None and ans.chosen_index < len(ans.candidates):
            chosen = ans.candidates[ans.chosen_index]
        else:
            chosen = next((c for c in ans.candidates if (c.sql or c.raw_sql) == ans.sql),
                          ans.candidates[0] if ans.candidates else None)
        tables = chosen.tables if chosen else []
        audit.write({"request_id": rid, "user": _user_hash(inp.user), "question": inp.question, "mode": mode,
                     "status": ans.status, "sql": ans.sql, "tables": tables,
                     "guard": {"stage": chosen.error_stage if chosen else None,
                               "error": (chosen.error or "")[:200] if chosen and chosen.error else None},
                     "retrieval": {"tables": (ans.retrieval or {}).get("linked_tables"),
                                   "values": len((ans.retrieval or {}).get("values") or []),
                                   "glossary": (ans.retrieval or {}).get("glossary")},
                     "row_count": out.get("row_count"), "confidence": ans.confidence,
                     "latency_ms": round((time.perf_counter() - t0) * 1000, 1)})
        if mode == "shadow":
            return {"request_id": rid, "status": "shadow_logged"}
        return {"request_id": rid, "status": ans.status, "sql": ans.sql, "columns": out.get("columns"),
                "rows": out.get("rows"), "row_count": out.get("row_count"),
                "tables": tables, "explanation_mn": ans.explanation_mn,
                "confidence": ans.confidence, "question_en": question_en, "term_hints": term_hints,
                "linked_tables": (ans.retrieval or {}).get("linked_tables"),
                "examples": (ans.retrieval or {}).get("examples"),
                "error": (chosen.error or "")[:500] if chosen and chosen.error else None,
                "error_stage": chosen.error_stage if chosen else None,
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1)}

    @app.get("/")
    def index():
        from fastapi.responses import HTMLResponse
        return HTMLResponse((Path(__file__).with_name("static") / "index.html").read_text(encoding="utf-8"))

    @app.post("/feedback")
    def give_feedback(inp: FeedbackIn):
        if inp.corrected_sql:
            g = guard.check(inp.corrected_sql)
            if not g.ok:
                raise HTTPException(status_code=400, detail=f"Засварласан SQL зөвшөөрөгдөхгүй: {g.error}")
        feedback.write({"request_id": inp.request_id, "user": _user_hash(inp.user), "correct": inp.correct,
                        "corrected_sql": inp.corrected_sql, "comment": inp.comment})
        return {"ok": True}

    return app


def serve(cfg: Config, host: str = "0.0.0.0", port: int = 8080, run_id: str | None = None) -> None:
    import uvicorn
    uvicorn.run(create_app(cfg, run_id), host=host, port=port, log_level="info")
