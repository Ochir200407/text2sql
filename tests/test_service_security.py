"""Вэб үйлчилгээний аюулгүй байдал (docs/research/R4_security_governance.md — A1, A3–A6, S1, R1, G2).

PostgreSQL/LLM-гүй: create_app-ийн хүнд хамаарлыг (Pipeline, Executor, semantic model) mock-оор солино.
"""
import json
import threading
import time
from types import SimpleNamespace as NS

import pytest

pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from mn_text2sql.security.sql_guard import SqlGuard  # noqa: E402
from mn_text2sql.service import auth  # noqa: E402
from mn_text2sql.service import flow as F  # noqa: E402

LOCAL = ("127.0.0.1", 50000)
BROWSER = "http://localhost:8080"   # ижил машины хөтөч (Host: localhost:8080)


def _app(mode, web_key, trusted=None):
    app = FastAPI(dependencies=[auth.capture_dependency()])
    ck = auth.make_check_key(web_key, mode, trusted)

    @app.get("/x")
    def x(k: str | None = None):
        ck(k)
        return {"ok": True}
    return app


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for v in ("T2S_WEB_KEY", "T2S_ADMIN_KEY", "T2S_INSECURE_OPEN", "T2S_AUDIT_KEY", "T2S_WEB_MODEL",
              "T2S_WEB_TRANSLATOR", "T2S_MAX_JOBS", "T2S_TRUSTED_HOSTS", "T2S_SQL_EDIT"):
        monkeypatch.delenv(v, raising=False)


# ---------------- check_key: түлхүүрийн бодлого ----------------

def test_dev_mode_without_key_is_open():
    assert TestClient(_app("dev", None)).get("/x").status_code == 200


def test_shadow_without_key_only_local_direct():
    app = _app("shadow", None)
    assert TestClient(app).get("/x").status_code == 403                       # гадны хаяг
    local = TestClient(app, client=LOCAL, base_url=BROWSER)                   # ижил машины хөтөч
    assert local.get("/x").status_code == 200                                 # localhost шууд
    # Cloudflare tunnel / reverse proxy нь localhost-оос холбогдоно — толгойгоор нь ялгана
    assert local.get("/x", headers={"CF-Connecting-IP": "1.2.3.4"}).status_code == 403
    assert local.get("/x", headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 403


# ---------------- S2: түлхүүргүй горимын Host шалгалт (DNS rebinding, TCP forwarder) ----------------

@pytest.mark.parametrize("host", ["localhost:8080", "127.0.0.1:8080", "[::1]:8080", "LocalHost:8080", "localhost"])
def test_no_key_trusted_hosts_from_same_machine(host):
    c = TestClient(_app("shadow", None), client=LOCAL, base_url=BROWSER)
    assert c.get("/x", headers={"Host": host}).status_code == 200
    assert TestClient(_app("shadow", None), client=LOCAL, base_url="http://127.0.0.1:8080").get("/x").status_code == 200


@pytest.mark.parametrize("host", ["evil.example", "evil.example:8080", "testserver", "localhost.evil.example",
                                  "127.0.0.1.nip.io", "0.tcp.ngrok.io:12345", ""])
def test_no_key_untrusted_host_header_refused(host, caplog, monkeypatch):
    """DNS rebinding: халдагчийн домэйн 127.0.0.1 рүү заасан ч хөтөч өөрийн Host-ыг илгээнэ → 403 + чанга лог."""
    monkeypatch.setattr(auth, "_WARNED_HOSTS", set())
    c = TestClient(_app("pilot", None), client=LOCAL, base_url=BROWSER)
    with caplog.at_level("WARNING", logger="mn_text2sql.service.auth"):
        r = c.get("/x", headers={"Host": host})
    assert r.status_code == 403 and "localhost" in r.json()["detail"]
    if host:
        assert any("итгэмжлэгдээгүй" in m and "DNS rebinding" in m for m in caplog.messages)


def test_no_key_configured_trusted_hosts(monkeypatch):
    app = _app("shadow", None)
    assert TestClient(app, client=LOCAL, base_url="http://studio.lan:8080").get("/x").status_code == 403
    monkeypatch.setenv("T2S_TRUSTED_HOSTS", "studio.lan, other.lan:9000")
    app = _app("shadow", None)                                                # тохиргоо эхлэхэд уншигдана
    assert TestClient(app, client=LOCAL, base_url="http://studio.lan:8080").get("/x").status_code == 200
    assert TestClient(app, client=LOCAL, base_url="http://other.lan").get("/x").status_code == 200
    # service.trusted_hosts (create_app → make_check_key-ийн 3-р аргумент)
    app2 = _app("shadow", None, ["cfg.lan"])
    assert TestClient(app2, client=LOCAL, base_url="http://cfg.lan:1").get("/x").status_code == 200
    # итгэмжлэгдсэн Host ч гэсэн гадны хаяг эсвэл proxy толгойтой бол 403 хэвээр
    assert TestClient(app, base_url="http://studio.lan").get("/x").status_code == 403
    assert TestClient(app, client=LOCAL, base_url="http://studio.lan").get(
        "/x", headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 403


def test_host_check_only_in_no_key_mode():
    # түлхүүртэй бол Host-оос хамаарахгүй (түлхүүр хангалттай); dev горим хуучин шигээ нээлттэй
    assert TestClient(_app("shadow", "s3cret"), base_url="http://any.example").get("/x?k=s3cret").status_code == 200
    assert TestClient(_app("dev", None), base_url="http://any.example").get("/x").status_code == 200


def test_host_name_normalisation():
    assert auth.host_name("LocalHost:8080") == "localhost" and auth.host_name("localhost.") == "localhost"
    assert auth.host_name("[::1]:8080") == "[::1]" and auth.host_name("::1") == "[::1]"
    assert auth.host_name(None) == "" and auth.host_name("127.0.0.1:1") == "127.0.0.1"
    assert auth.trusted_hosts(["x.lan:80", ""]) >= {"localhost", "127.0.0.1", "[::1]", "x.lan"}


def test_insecure_open_escape_hatch(monkeypatch):
    monkeypatch.setenv("T2S_INSECURE_OPEN", "1")
    assert TestClient(_app("pilot", None)).get("/x").status_code == 200


@pytest.mark.parametrize("mode", ["dev", "shadow", "pilot"])
def test_key_via_header_or_query(mode):
    c = TestClient(_app(mode, "s3cret"), client=LOCAL)
    assert c.get("/x").status_code == 403                                     # localhost ч түлхүүргүй бол 403
    assert c.get("/x?k=wrong").status_code == 403
    assert c.get("/x", headers={"X-T2S-Key": "wrong"}).status_code == 403
    assert c.get("/x?k=s3cret").status_code == 200                            # хуучин холбоос
    assert c.get("/x", headers={"X-T2S-Key": "s3cret"}).status_code == 200    # шинэ JS


def test_safe_bind_host():
    assert auth.safe_bind_host("0.0.0.0", "shadow", None) == "127.0.0.1"
    assert auth.safe_bind_host("0.0.0.0", "shadow", "k") == "0.0.0.0"
    assert auth.safe_bind_host("0.0.0.0", "dev", None) == "0.0.0.0"
    assert auth.safe_bind_host("127.0.0.1", "pilot", None) == "127.0.0.1"


def test_admin_header_on_analyst_router(monkeypatch, tmp_path):
    from mn_text2sql.service import analyst as A
    monkeypatch.setenv("T2S_ADMIN_KEY", "adm1n")
    app = FastAPI()
    app.include_router(A.make_analyst_router(None, cache=A.ResultCache(),
                                             store=A.FeedbackStore(tmp_path / "c.jsonl", tmp_path / "v.jsonl")))
    c = TestClient(app)
    assert c.get("/analyst/candidates").status_code == 403
    assert c.get("/analyst/candidates", headers={"X-T2S-Admin": "nope"}).status_code == 403
    assert c.get("/analyst/candidates", headers={"X-T2S-Admin": "adm1n"}).status_code != 403
    assert c.get("/analyst/candidates?admin=adm1n").status_code != 403


# ---------------- SqlGuard: хоосон allow-list ----------------

def test_guard_empty_allowlist_fail_closed():
    assert SqlGuard({}).check("SELECT * FROM t2s.loans").ok                         # dev/тест: хуучин зан төлөв
    g = SqlGuard({}, fail_closed=True).check("SELECT * FROM t2s.loans")
    assert not g.ok and g.code == "forbidden_table"
    assert not SqlGuard({}, fail_closed=True).check("SELECT * FROM pg_roles").ok
    ok = SqlGuard({"loans": {"id"}}, fail_closed=True).check("SELECT id FROM t2s.loans")
    assert ok.ok


# ---------------- /flow/*: админ, shadow, audit, 429 ----------------

class FakeRunner:
    def __init__(self, gate: threading.Event | None = None, fail: str | None = None):
        self.gate, self.calls, self.fail = gate, [], fail
        self.cfg = {"service": {"mode": "pilot"}}

    def run(self, flow, q, base=None, progress=None, shadow=False):
        self.calls.append(shadow)
        if progress is not None:   # FlowRunner/orchestrator-ийн report()-той адил: англи орчуулга, төлөв, мөрийн тоо
            progress("translate", "How many active loans are there?", "done")
            progress("output", "answered, 5 мөр", "done")
        if self.gate is not None:
            self.gate.wait(5)
        if self.fail:
            raise RuntimeError(self.fail)
        aud = {"status": "answered", "sql": "SELECT 1", "tables": ["loans"], "row_count": 1}
        if shadow:
            return {"status": "shadow_logged", "_audit": aud}
        return {"status": "answered", "steps": [{"node": "output", "sql": "SELECT 1", "rows": [[1]]}],
                "warnings": [], "inspect": {"prompt": "..."}, "_audit": aud, "analyst": {}}


def _flow_client(monkeypatch, mode="pilot", require_admin=True, runner=None, max_jobs=4):
    monkeypatch.setattr(F, "compile_flow", lambda flow, base=None: {"spec": {}, "fingerprint": "fp", "warnings": []})
    records = []
    runner = runner or FakeRunner()
    app = FastAPI()
    app.include_router(F.make_router(runner, auth.make_check_key("s3cret", mode), audit=records.append, mode=mode,
                                     require_admin=require_admin, max_jobs=max_jobs))
    return TestClient(app, headers={"X-T2S-Key": "s3cret"}), records, runner


def _wait(c, jid):
    for _ in range(100):
        j = c.get(f"/flow/job/{jid}").json()
        if j["done"]:
            return j
        time.sleep(0.02)
    raise AssertionError("job дууссангүй")


def test_flow_save_and_logs_need_admin(monkeypatch, tmp_path):
    monkeypatch.setenv("T2S_ADMIN_KEY", "adm1n")
    saved = []
    monkeypatch.setattr(F, "save_flow", lambda *a: saved.append(a) or {"ok": True})
    monkeypatch.setattr(F, "delete_flow", lambda rid: True)
    c, _, _ = _flow_client(monkeypatch)
    body = {"id": "W1", "flow": {}}
    assert c.post("/flow/save", json=body).status_code == 403
    assert c.post("/flow/save?admin=bad", json=body).status_code == 403
    assert c.delete("/flow/W1").status_code == 403
    assert c.get("/flow/logs").status_code == 403
    assert not saved
    assert c.post("/flow/save", json=body, headers={"X-T2S-Admin": "adm1n"}).status_code == 200
    assert c.delete("/flow/W1?admin=adm1n").status_code == 200
    assert c.get("/flow/logs", headers={"X-T2S-Admin": "adm1n"}).status_code == 200


def test_flow_admin_closed_without_admin_key(monkeypatch):
    c, _, _ = _flow_client(monkeypatch)
    assert c.get("/flow/logs", headers={"X-T2S-Admin": ""}).status_code == 403


def test_local_builder_keeps_open_save(monkeypatch):
    """`t2s flow-ui` (require_admin=False) — локал угсрах горимын хуучин зан төлөв."""
    monkeypatch.setattr(F, "save_flow", lambda *a: {"ok": True})
    c, _, _ = _flow_client(monkeypatch, mode="dev", require_admin=False)
    assert c.post("/flow/save", json={"id": "W1", "flow": {}}).status_code == 200


def test_flow_run_job_key_required(monkeypatch):
    c, _, _ = _flow_client(monkeypatch)
    r = TestClient(c.app).post("/flow/run_job", json={"flow": {}, "question": "Зээлийн тоо"})
    assert r.status_code == 403


def test_flow_run_job_audited_pilot(monkeypatch):
    c, records, runner = _flow_client(monkeypatch, mode="pilot")
    jid = c.post("/flow/run_job", json={"flow": {}, "question": "Зээлийн тоо"}).json()["job"]
    j = _wait(c, jid)
    assert j["result"]["status"] == "answered" and "_audit" not in j["result"]
    assert runner.calls == [False]
    assert len(records) == 1 and records[0]["sql"] == "SELECT 1" and records[0]["question"] == "Зээлийн тоо"
    assert records[0]["source"] == "flow" and records[0]["mode"] == "pilot"


def test_flow_run_job_shadow_hides_results(monkeypatch):
    c, records, runner = _flow_client(monkeypatch, mode="shadow")
    jid = c.post("/flow/run_job", json={"flow": {}, "question": "Зээлийн тоо"}).json()["job"]
    j = _wait(c, jid)
    res = j["result"]
    assert res["status"] == "shadow_logged" and runner.calls == [True]
    dumped = json.dumps(res)
    assert "SELECT 1" not in dumped and "rows" not in dumped and res.get("inspect") is None
    assert records and records[0]["status"] == "answered" and records[0]["sql"] == "SELECT 1"


def test_flow_inspect_admin_only_in_shadow(monkeypatch):
    monkeypatch.setenv("T2S_ADMIN_KEY", "adm1n")
    c, _, runner = _flow_client(monkeypatch, mode="shadow")
    runner.inspect = lambda *a: {"prompt": "x"}
    assert c.post("/flow/inspect", json={"flow": {}, "question": "Зээлийн тоо"}).status_code == 403
    r = c.post("/flow/inspect", json={"flow": {}, "question": "Зээлийн тоо"}, headers={"X-T2S-Admin": "adm1n"})
    assert r.status_code == 200


def test_flow_run_job_concurrency_cap(monkeypatch):
    gate = threading.Event()
    c, _, _ = _flow_client(monkeypatch, runner=FakeRunner(gate), max_jobs=1)
    body = {"flow": {}, "question": "Зээлийн тоо"}
    jid = c.post("/flow/run_job", json=body).json()["job"]
    assert c.post("/flow/run_job", json=body).status_code == 429
    gate.set()
    _wait(c, jid)
    assert c.post("/flow/run_job", json=body).status_code == 200


# ---------------- create_app: /feedback, /ask_job (mock-той) ----------------

@pytest.fixture
def api_app(monkeypatch, tmp_path):
    import mn_text2sql.db.conn as conn
    import mn_text2sql.db.executor as ex
    import mn_text2sql.knowledge.semantic as sem
    import mn_text2sql.pipeline.orchestrator as orch
    from mn_text2sql.service import api

    gate = threading.Event()
    state = {"answer": None}   # тохируулбал FakePipe.ask нь энэ Answer-ийг буцаана (үгүй бол gate хүлээгээд алдаа)
    monkeypatch.setattr(sem, "cached_model", lambda path: NS(allowed=lambda: {"loans": {"id"}}, schema="t2s"))
    monkeypatch.setattr(conn, "bot_dsn", lambda cfg: "postgresql://mock")
    monkeypatch.setattr(ex, "Executor", lambda *a, **kw: NS())

    class FakePipe:
        def __init__(self, *a, **kw):
            self.llm, self.retriever, self.translations = None, None, {}

        def ask(self, q, variant=None, item_id=None):
            if state["answer"] is not None:
                return state["answer"]
            gate.wait(5)
            raise RuntimeError("mock LLM")
    monkeypatch.setattr(orch, "Pipeline", FakePipe)

    def make(mode="pilot", max_jobs=1):
        cfg = {"service": {"mode": mode, "run_id": "E1", "model": "m", "max_jobs": max_jobs},
               "llm": {"model": "m", "base_url": "http://localhost:1"},
               "pipeline": {"intent_check": True}, "knowledge": {"semantic_model": "x", "index_dir": "build/index"},
               "security": {"forbid_functions": [], "enforce_limit": 1000, "audit_log": str(tmp_path / "audit.jsonl"),
                            "audit_encrypt_key_env": "T2S_AUDIT_KEY", "audit_retention_days": 90},
               "project": {"as_of_date": "2026-06-30"},
               "db": {"statement_timeout_ms": 1000, "max_explain_cost": 1e7}}
        return api.create_app(cfg)
    return NS(make=make, gate=gate, tmp=tmp_path, state=state)


def test_feedback_requires_key(api_app, monkeypatch):
    monkeypatch.setenv("T2S_WEB_KEY", "s3cret")
    c = TestClient(api_app.make())
    body = {"request_id": "r1", "correct": True}
    assert c.post("/feedback", json=body).status_code == 403
    assert c.post("/feedback", json=body, headers={"X-T2S-Key": "s3cret"}).status_code == 200
    assert c.post("/feedback?k=s3cret", json=body).status_code == 200


def test_api_without_key_refuses_remote(api_app):
    app = api_app.make(mode="shadow")
    assert TestClient(app).post("/ask_job", json={"question": "Зээлийн тоо"}).status_code == 403
    assert TestClient(app).post("/feedback", json={"request_id": "r", "correct": True}).status_code == 403
    assert TestClient(app).get("/health").status_code == 200


def test_ask_job_concurrency_cap(api_app, monkeypatch):
    monkeypatch.setenv("T2S_WEB_KEY", "s3cret")
    c = TestClient(api_app.make(max_jobs=1), headers={"X-T2S-Key": "s3cret"})
    jid = c.post("/ask_job", json={"question": "Зээлийн тоо"}).json()["job"]
    assert c.post("/ask_job", json={"question": "Зээлийн тоо"}).status_code == 429
    api_app.gate.set()
    for _ in range(100):
        j = c.get(f"/job/{jid}").json()
        if j["done"]:
            break
        time.sleep(0.02)
    assert j["done"] and j["error"]
    assert c.post("/ask_job", json={"question": "Зээлийн тоо"}).status_code == 200
    # бүтэлгүй асуулт ч audit-д бичигдсэн
    lines = (api_app.tmp / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert any(json.loads(x)["status"] == "failed" for x in lines)


# ================= 2-р аудит (service/ + sql_guard.py): S1–S8 =================

def _job(c, path, jid):
    for _ in range(250):
        j = c.get(f"{path}/{jid}").json()
        if j["done"]:
            return j
        time.sleep(0.02)
    raise AssertionError("job дууссангүй")


def _fake_answer(cell: str):
    """Pipeline.ask-ийн бодит Answer (PG/LLM-гүй): 2 мөр, 2-р багана нь json_agg шиг аварга нүд."""
    from mn_text2sql.db.executor import ExecResult
    from mn_text2sql.pipeline.orchestrator import Answer, Candidate
    res = ExecResult(True, ["id", "blob"], [(1, cell), (2, "x")], 2)
    cand = Candidate(sql="SELECT id, blob FROM t2s.loans", ok=True, rows=2, tables=["loans"], result=res)
    return Answer(question="q", variant="mn", run_id="E1", model="m", status="answered", sql=cand.sql, result=res,
                  candidates=[cand], confidence=1.0, unanimous=True, chosen_index=0)


# ---------------- S1: нэг хуваалцсан зэрэг ажиллах хязгаар ----------------

def test_work_slots_unit():
    from fastapi import HTTPException
    s = auth.WorkSlots(1)
    with s.hold():
        assert s.busy == 1
        with pytest.raises(HTTPException) as ei:
            s.acquire_or_429()
        assert ei.value.status_code == 429 and ei.value.headers["Retry-After"]
    assert s.busy == 0
    with pytest.raises(RuntimeError), s.hold():
        raise RuntimeError("pipeline унав")
    assert s.busy == 0 and s.try_acquire()                  # алдааны дараа ч finally-д чөлөөлөгдсөн
    s.release()
    with pytest.raises(ValueError):                         # илүү release-ийг нуухгүй (BoundedSemaphore)
        s.release()


def test_shared_cap_covers_ask_flow_and_analyst(api_app, monkeypatch):
    """/ask_job нэг slot-ийг эзэлсэн үед синхрон /ask, /flow/run_job, /analyst/rerun бүгд 429 (өмнө нь /ask хязгааргүй,
    /flow/run_job өөрийн dict-тэй байв)."""
    monkeypatch.setenv("T2S_WEB_KEY", "s3cret")
    monkeypatch.setattr(F, "compile_flow", lambda flow, base=None: {"spec": {}, "fingerprint": "fp", "warnings": []})
    c = TestClient(api_app.make(max_jobs=1), headers={"X-T2S-Key": "s3cret"})
    q = {"question": "Зээлийн тоо"}
    jid = c.post("/ask_job", json=q).json()["job"]
    assert c.post("/ask", json=q).status_code == 429
    assert c.post("/ask_job", json=q).status_code == 429
    assert c.post("/flow/run_job", json={"flow": {}, **q}).status_code == 429
    r = c.post("/analyst/rerun", json={"sql": "SELECT id FROM t2s.loans"})
    assert r.status_code == 429 and r.headers.get("retry-after")
    api_app.gate.set()
    assert _job(c, "/job", jid)["error"]
    assert c.post("/ask", json=q).status_code == 503        # slot чөлөөлөгдсөн → mock LLM-ийн алдаа (429 биш)
    assert c.post("/ask", json=q).status_code == 503        # алдаатай /ask ч slot-оо finally-д буцаасан
    fj = c.post("/flow/run_job", json={"flow": {}, **q})
    assert fj.status_code == 200 and _job(c, "/flow/job", fj.json()["job"])["error"]
    assert c.post("/ask", json=q).status_code == 503        # flow job-ийн slot ч чөлөөлөгдсөн


def test_shared_cap_analyst_rerun_and_verified_run(monkeypatch, tmp_path):
    from mn_text2sql.service import analyst as A
    monkeypatch.setenv("T2S_ADMIN_KEY", "adm1n")
    calls = []
    big = "{" + "x" * 50_000 + "}"   # json_agg/string_agg шиг: бүхэл view нэг нүдэнд

    def run(sql, **kw):
        calls.append(sql)
        return NS(ok=True, columns=["id", "j"], rows=[(1, big)], rowcount=1, truncated=False, error=None,
                  error_code=None)
    runner = NS(guard=SqlGuard({"loans": {"id"}}, "t2s", None, None, enforce_limit=1000), executor=NS(run=run),
                cfg={"service": {"mode": "pilot"}, "security": {"enforce_limit": 1000}, "db": {}})
    vs = A.VerifiedStore(tmp_path / "v.jsonl")
    vid = vs.add("Зээлийн тоо", "SELECT id FROM t2s.loans")["id"]
    slots = auth.WorkSlots(1)
    app = FastAPI()
    app.include_router(A.make_analyst_router(None, cache=A.ResultCache(), runner=runner, verified=vs, slots=slots))
    c = TestClient(app)
    assert slots.try_acquire()                                   # өөр endpoint (жишээ нь /ask) slot-ийг эзэлсэн
    assert c.post("/analyst/rerun", json={"sql": "SELECT id FROM t2s.loans"}).status_code == 429
    assert c.post(f"/analyst/verified/{vid}/run").status_code == 429
    assert calls == []                                           # DB-д хүрээгүй
    slots.release()
    r = c.post("/analyst/rerun", json={"sql": "SELECT id FROM t2s.loans"})
    assert r.status_code == 200 and len(calls) == 1 and slots.busy == 0
    # S3: хариултын аварга нүд тайрагдана, мэдэгдэлтэй; кэш (экспорт) ч хязгаартай
    j = r.json()
    assert len(j["rows"][0][1]) <= A.MAX_CELL_CHARS and j["rows"][0][1].endswith("тэмдэгт]")
    assert any(n["kind"] == "size" for n in j["analyst"]["notices"])
    assert c.post(f"/analyst/verified/{vid}/run").status_code == 200 and slots.busy == 0


# ---------------- S2: локал Studio (shadow, түлхүүргүй) ижил машины хөтчөөс ----------------

def test_studio_shadow_no_key_local_browser(api_app, monkeypatch):
    monkeypatch.setattr(F, "compile_flow", lambda flow, base=None: {"spec": {}, "fingerprint": "fp", "warnings": []})
    api_app.gate.set()
    app = api_app.make(mode="shadow")
    c = TestClient(app, client=LOCAL, base_url=BROWSER)
    assert c.get("/builder").status_code == 200
    assert c.get("/flow/catalog").status_code == 200
    j = _job(c, "/flow/job", c.post("/flow/run_job", json={"flow": {}, "question": "Зээлийн тоо"}).json()["job"])
    assert j["error"] and "mock LLM" not in json.dumps(j, ensure_ascii=False)   # S6: shadow-д дэлгэрэнгүйгүй
    assert c.post("/ask_job", json={"question": "Зээлийн тоо"}).status_code == 200
    # DNS rebinding: ижил сервер, ижил 127.0.0.1 клиент, гэхдээ халдагчийн Host → 403
    evil = TestClient(app, client=LOCAL, base_url="http://evil.example:8080")
    assert evil.get("/flow/catalog").status_code == 403
    assert evil.post("/ask_job", json={"question": "Зээлийн тоо"}).status_code == 403
    assert evil.post("/analyst/rerun", json={"sql": "SELECT id FROM t2s.loans"}).status_code == 403


# ---------------- S3: хариулт/кэш/экспортын хэмжээ (api.py /ask) ----------------

def test_ask_response_and_cache_size_capped(api_app, monkeypatch):
    from mn_text2sql.service import analyst as A
    monkeypatch.setenv("T2S_WEB_KEY", "s3cret")
    api_app.state["answer"] = _fake_answer("Z" * 60_000)
    c = TestClient(api_app.make(mode=" Pilot "), headers={"X-T2S-Key": "s3cret"})   # S5: " Pilot " = pilot
    j = c.post("/ask", json={"question": "Зээлийн тоо"}).json()
    assert j["status"] == "answered" and j["rows"][1] == ["2", "x"]
    assert len(j["rows"][0][1]) <= A.MAX_CELL_CHARS and "тэмдэгт]" in j["rows"][0][1]
    assert any(n["kind"] == "size" for n in j["analyst"]["notices"])
    assert len(json.dumps(j)) < 40_000
    exp = c.get(f"/analyst/export/{j['analyst']['rid']}?fmt=csv")
    assert exp.status_code == 200 and exp.headers["X-T2S-Truncated"].startswith("cells=1")
    assert len(exp.content) < 20_000


def test_cap_rows_and_cache_budget(monkeypatch):
    from decimal import Decimal

    from mn_text2sql.service import analyst as A
    rows, cap = A.cap_rows([["a" * 20_000, Decimal("1.5")], ["b", None]])
    assert len(rows[0][0]) <= A.MAX_CELL_CHARS and rows[0][1] == 1.5 and rows[1] == ["b", None]
    assert cap["capped"] and cap["cells_cut"] == 1 and cap["rows_dropped"] == 0
    again, cap2 = A.cap_rows(rows)                                     # давтан хэрэглэхэд өөрчлөгдөхгүй
    assert again == rows and not cap2["capped"]
    many, cap3 = A.cap_rows([["x" * 1000] for _ in range(100)], max_bytes=10_000)
    assert 0 < len(many) < 100 and cap3["rows_dropped"] == 100 - len(many) and cap3["bytes"] <= 10_000
    assert A.cap_rows(None)[0] == [] and not A.cap_rows(None)[1]["capped"]
    # ResultCache: бичлэг ≤ max_entry_bytes, нийт ≤ max_total_bytes (хуучныг шахна), TTL-ийг put чөлөөлнө
    c = A.ResultCache(max_items=10, max_entry_bytes=5_000, max_total_bytes=12_000, max_cell_chars=1_000)
    info = c.put("a", {"question": "q", "sql": "s", "columns": ["j"],
                       "rows": [["x" * 50_000]] + [["y" * 900] for _ in range(20)]})
    e = c.get("a")
    assert info and info["cells_cut"] == 1 and info["rows_dropped"] > 0 and e["capped"] == info
    assert len(e["rows"][0][0]) <= 1_000 and e["_bytes"] <= 5_002
    for k in "bcd":
        c.put(k, {"columns": ["j"], "rows": [["z" * 900] for _ in range(4)]})
    assert c.total_bytes <= 12_000 and c.get("a") is None and c.get("d") is not None
    t = A.time.time()
    monkeypatch.setattr(A.time, "time", lambda: t + 4000)
    c.put("e", {"rows": []})
    assert list(c.items) == ["e"] and c.total_bytes == c.items["e"]["_bytes"]


def test_export_marks_truncated_result(tmp_path):
    from mn_text2sql.service import analyst as A
    cache = A.ResultCache()
    cache.put("abc999", {"question": "q", "sql": "SELECT 1", "columns": ["j"], "rows": [["=" + "x" * 30_000]],
                         "tables": [], "status": "answered"})
    app = FastAPI()
    app.include_router(A.make_analyst_router(None, cache=cache, store=A.FeedbackStore(tmp_path / "c", tmp_path / "v")))
    c = TestClient(app)
    r = c.get("/analyst/export/abc999?fmt=csv")
    text = r.content.decode("utf-8-sig")
    assert r.status_code == 200 and r.headers["X-T2S-Truncated"] == "cells=1; rows_dropped=0"
    assert len(text) < 12_000 and "'=xxx" in text                                # формулын хамгаалалт хэвээр
    pytest.importorskip("openpyxl")
    import io

    from openpyxl import load_workbook
    wb = load_workbook(io.BytesIO(c.get("/analyst/export/abc999?fmt=xlsx").content))
    assert any(row[0].value == "Анхаар" for row in wb["SQL"].iter_rows())


def test_build_extras_caps_giant_cell():
    from mn_text2sql.service import analyst as A
    res = NS(ok=True, columns=["j"], rows=[["[" + "{'a': 1}, " * 10_000 + "]"]], rowcount=1, truncated=False,
             error_code=None)
    ans = NS(question="q", status="answered", sql="SELECT json_agg(l) FROM t2s.loans l", result=res, candidates=[],
             chosen_index=None, confidence=None, unanimous=None)
    cache = A.ResultCache()
    x = A.build_extras(ans, cache=cache, verified=False)
    assert "error" not in x and any(n["kind"] == "size" for n in x["notices"])
    assert len(cache.get(x["rid"])["rows"][0][0]) <= A.MAX_CELL_CHARS


# ---------------- S5: service.mode-ийн нормчлол ----------------

def test_mode_normalised_once(monkeypatch, api_app):
    from mn_text2sql.service import analyst as A
    assert auth.norm_mode(" Shadow ") == "shadow" and auth.is_shadow("SHADOW\n") and not auth.is_shadow(None)
    assert auth.service_mode({"service": {"mode": " Pilot"}}) == "pilot"
    for cfg in ({"service": {}}, {"service": {"mode": None}}, {"service": {"mode": " "}}, {}, None):
        assert auth.service_mode(cfg) == "shadow"                        # байхгүй/хоосон → хамгийн хаалттай
    assert not A.sql_edit_enabled({"service": {"mode": "Shadow "}})      # өмнө нь "Shadow " != "shadow" → нээлттэй
    assert A.sql_edit_enabled({"service": {"mode": " PILOT"}})
    # flow router: " SHADOW " → shadow (үр дүн нууна)
    c, _, runner = _flow_client(monkeypatch, mode=" SHADOW ")
    j = _wait(c, c.post("/flow/run_job", json={"flow": {}, "question": "Зээлийн тоо"}).json()["job"])
    assert j["result"]["status"] == "shadow_logged" and runner.calls == [True]
    # create_app: " Shadow " → /ask хариу буцаахгүй, /health нормчилсон горимыг харуулна
    monkeypatch.setenv("T2S_WEB_KEY", "s3cret")
    api_app.state["answer"] = _fake_answer("v")
    c2 = TestClient(api_app.make(mode=" Shadow "), headers={"X-T2S-Key": "s3cret"})
    r = c2.post("/ask", json={"question": "Зээлийн тоо"}).json()
    assert set(r) == {"request_id", "status"} and r["status"] == "shadow_logged"
    assert c2.get("/health").json()["mode"] == "shadow"


# ---------------- S6: shadow-д /flow/job-ийн явц ба алдааг цэвэрлэнэ ----------------

def test_flow_progress_scrubbed_in_shadow(monkeypatch):
    c, _, _ = _flow_client(monkeypatch, mode="shadow")
    j = _wait(c, c.post("/flow/run_job", json={"flow": {}, "question": "Зээлийн тоо"}).json()["job"])
    assert [p["node"] for p in j["progress"]] == ["translate", "output"]          # алхам, хугацаа үлдэнэ (UI)
    assert all(p["msg"] == "" for p in j["progress"])
    dumped = json.dumps(j, ensure_ascii=False)
    assert "How many" not in dumped and "answered" not in dumped and "5 мөр" not in dumped
    c2, _, _ = _flow_client(monkeypatch, mode="pilot")                              # pilot: мессеж хэвээр
    j2 = _wait(c2, c2.post("/flow/run_job", json={"flow": {}, "question": "Зээлийн тоо"}).json()["job"])
    assert [p["msg"] for p in j2["progress"]] == ["How many active loans are there?", "answered, 5 мөр"]


def test_flow_error_scrubbed_in_shadow_but_audited(monkeypatch):
    secret = "connection to 10.0.0.5 failed: password authentication for t2s_bot"
    c, records, _ = _flow_client(monkeypatch, mode="shadow", runner=FakeRunner(fail=secret))
    j = _wait(c, c.post("/flow/run_job", json={"flow": {}, "question": "Зээлийн тоо"}).json()["job"])
    assert j["error"] and "RuntimeError" in j["error"] and "10.0.0.5" not in j["error"]
    assert records[0]["status"] == "failed" and "10.0.0.5" in records[0]["error"]   # дэлгэрэнгүй нь audit-д
    c2, records2, _ = _flow_client(monkeypatch, mode="pilot", runner=FakeRunner(fail=secret))
    j2 = _wait(c2, c2.post("/flow/run_job", json={"flow": {}, "question": "Зээлийн тоо"}).json()["job"])
    # 3-р аудит L5: pilot/түлхүүр эзэмшигчид ч дэлгэрэнгүйгүй — request id-аар audit-аас хайна
    assert "10.0.0.5" not in j2["error"] and "RuntimeError" in j2["error"]
    assert records2[0]["request_id"] in j2["error"] and "10.0.0.5" in records2[0]["error"]


# ---------------- S7: OpenAPI/docs зөвхөн dev горимд ----------------

@pytest.mark.parametrize("mode,code", [("pilot", 404), ("shadow", 404), ("live", 404), ("dev", 200)])
def test_openapi_docs_only_in_dev(api_app, mode, code):
    c = TestClient(api_app.make(mode=mode), client=LOCAL, base_url=BROWSER)
    for path in ("/openapi.json", "/docs", "/redoc"):
        assert c.get(path).status_code == code, (mode, path)


# ---------------- S8: jobs dict-ийг түгжээн дор цэвэрлэнэ ----------------

def test_flow_jobs_pruned_only_when_done(monkeypatch):
    runner = FakeRunner()
    c, _, _ = _flow_client(monkeypatch, runner=runner, max_jobs=4)
    body = {"flow": {}, "question": "Зээлийн тоо"}
    done = c.post("/flow/run_job", json=body).json()["job"]
    _wait(c, done)
    runner.gate = threading.Event()                                    # дараагийн job удаан ажиллана
    running = c.post("/flow/run_job", json=body).json()["job"]
    t = time.time()
    monkeypatch.setattr(time, "time", lambda: t + 3601)                # 1 цаг өнгөрөв
    third = c.post("/flow/run_job", json=body).json()["job"]
    assert c.get(f"/flow/job/{done}").status_code == 404               # дууссан, хуучин → цэвэрлэв
    assert c.get(f"/flow/job/{running}").status_code == 200            # ажиллаж буйг устгахгүй
    runner.gate.set()
    _wait(c, running)
    _wait(c, third)


def test_flow_jobs_bounded(monkeypatch):
    c, _, _ = _flow_client(monkeypatch, max_jobs=300)
    body = {"flow": {}, "question": "Зээлийн тоо"}
    jids = [c.post("/flow/run_job", json=body).json()["job"] for _ in range(205)]
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:   # бүгд дуусах хүртэл (аль хэдийн цэвэрлэгдсэн нь 404)
        st = [c.get(f"/flow/job/{j}") for j in jids]
        if all(r.status_code == 404 or r.json()["done"] for r in st):
            break
        time.sleep(0.05)
    last = c.post("/flow/run_job", json=body).json()["job"]
    assert c.get(f"/flow/job/{jids[0]}").status_code == 404 and c.get(f"/flow/job/{last}").status_code == 200
    assert c.get(f"/flow/job/{jids[-1]}").status_code == 200


def test_ask_jobs_pruned_under_lock(api_app, monkeypatch):
    monkeypatch.setenv("T2S_WEB_KEY", "s3cret")
    api_app.gate.set()
    c = TestClient(api_app.make(max_jobs=2), headers={"X-T2S-Key": "s3cret"})
    first = c.post("/ask_job", json={"question": "Зээлийн тоо"}).json()["job"]
    _job(c, "/job", first)
    t = time.time()
    monkeypatch.setattr(time, "time", lambda: t + 3601)
    second = c.post("/ask_job", json={"question": "Зээлийн тоо"}).json()["job"]
    assert c.get(f"/job/{first}").status_code == 404
    assert _job(c, "/job", second)["error"]


# ================= 3-р аудит (service/): H2, L5, L6, H1 — create_app =================

def test_health_minimal_without_access(api_app, monkeypatch):
    """L5: /health нь горим/туршилт/загварыг зөвхөн хандах эрхтэй хүсэлтэд харуулна."""
    monkeypatch.setenv("T2S_WEB_KEY", "s3cret")
    c = TestClient(api_app.make(mode="pilot"))
    assert c.get("/health").json() == {"ok": True}
    assert c.get("/health?k=wrong").json() == {"ok": True}
    full = c.get("/health", headers={"X-T2S-Key": "s3cret"}).json()
    assert full["ok"] and full["mode"] == "pilot" and full["run"] == "E1" and full["model"] == "m"
    monkeypatch.delenv("T2S_WEB_KEY")
    keyless = api_app.make(mode="shadow")
    assert TestClient(keyless).get("/health").json() == {"ok": True}                       # гадны хаяг
    assert TestClient(keyless, client=LOCAL, base_url=BROWSER).get("/health").json()["mode"] == "shadow"


def test_ask_error_generic_with_request_id(api_app, monkeypatch, caplog):
    """L5: түлхүүр эзэмшигчид ч exception-ий текст очихгүй — request id-аар лог/audit-аас."""
    monkeypatch.setenv("T2S_WEB_KEY", "s3cret")
    api_app.gate.set()
    c = TestClient(api_app.make(), headers={"X-T2S-Key": "s3cret"})
    r = c.post("/ask", json={"question": "Зээлийн тоо"})
    assert r.status_code == 503
    detail = r.json()["detail"]
    assert "mock LLM" not in detail and "request id" in detail and "RuntimeError" in detail
    rid = detail.split("request id: ")[1].split(")")[0]
    rows = [json.loads(x) for x in (api_app.tmp / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    failed = [x for x in rows if x.get("request_id") == rid]
    assert failed and failed[0]["status"] == "failed" and "mock LLM" in failed[0]["error"] and failed[0]["model"] == "m"


def test_feedback_lengths_and_rate_limit(api_app, monkeypatch):
    """L6: request_id/user/corrected_sql урттай, /feedback хурдны хязгаартай (feedback.jsonl-ийг дүүргэхгүй)."""
    monkeypatch.setenv("T2S_WEB_KEY", "s3cret")
    c = TestClient(api_app.make(), headers={"X-T2S-Key": "s3cret"})
    assert c.post("/feedback", json={"request_id": "r" * 65, "correct": True}).status_code == 422
    assert c.post("/feedback", json={"request_id": "", "correct": True}).status_code == 422
    assert c.post("/feedback", json={"request_id": "r1", "correct": True, "user": "u" * 201}).status_code == 422
    assert c.post("/feedback", json={"request_id": "r1", "correct": False,
                                     "corrected_sql": "SELECT 1 " + "x" * 8000}).status_code == 422
    codes = [c.post("/feedback", json={"request_id": "r1", "correct": True}).status_code for _ in range(40)]
    assert codes[:20] == [200] * 20 and 429 in codes


def test_request_body_size_limited(api_app, monkeypatch):
    """H1: хүсэлтийн бие ≤ service.max_body_bytes (413) — JSON-ийг санах ойд уншихаас өмнө."""
    monkeypatch.setenv("T2S_WEB_KEY", "s3cret")
    c = TestClient(api_app.make(), headers={"X-T2S-Key": "s3cret"})
    r = c.post("/feedback", json={"request_id": "r1", "correct": True, "comment": "x" * 300_000})
    assert r.status_code == 413
    assert c.post("/flow/compile", json={"flow": {"nodes": [{"id": "x" * 300_000}]}}).status_code == 413


def test_web_executor_gets_result_budget(api_app, monkeypatch):
    """H2: вэбийн Executor-т max_result_bytes (анхдагч 5 MB; service.max_result_bytes-ээр; 0 = унтраах)."""
    import mn_text2sql.db.executor as ex
    from mn_text2sql.service import api
    seen = []
    monkeypatch.setattr(ex, "Executor", lambda *a, **kw: seen.append(kw) or NS())
    api_app.make()
    assert seen[-1]["max_result_bytes"] == auth.DEFAULT_MAX_RESULT_BYTES == 5_000_000
    base = {"service": {"mode": "dev", "run_id": "E1", "model": "m", "max_result_bytes": 0}}
    assert auth.max_result_bytes(base) is None and auth.max_result_bytes({"service": {"max_result_bytes": 7}}) == 7
    assert auth.max_body_bytes({}) == auth.DEFAULT_MAX_BODY_BYTES and auth.max_body_bytes({"service": {"max_body_bytes": 0}}) == 0
    assert api.FeedbackIn.model_fields["request_id"].metadata   # урттай
