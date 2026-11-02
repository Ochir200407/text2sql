"""3-р аудит (service/): L3 — локал builder-ийн Host шалгалт; L5 — алдааны текстийг клиентэд буцаахгүй; H1 —
/flow/inspect зэрэг ажиллах хязгаарт, /flow/compile хурдны хязгаартай, хүсэлтийн биеийн хэмжээ, асуултын урт;
too_large (H2)-ийн мэдэгдэл. PostgreSQL/LLM-гүй."""

import threading
from types import SimpleNamespace as NS

import pytest

pytest.importorskip("fastapi")
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from mn_text2sql.service import auth  # noqa: E402
from mn_text2sql.service import flow as F  # noqa: E402

LOCAL = ("127.0.0.1", 50000)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for v in ("T2S_WEB_KEY", "T2S_ADMIN_KEY", "T2S_INSECURE_OPEN", "T2S_TRUSTED_HOSTS", "T2S_SQL_EDIT"):
        monkeypatch.delenv(v, raising=False)


def _client(monkeypatch, runner, slots=None, mode="pilot"):
    monkeypatch.setattr(F, "compile_flow", lambda flow, base=None: {"spec": {}, "fingerprint": "fp", "warnings": [],
                                                                   "diff": {}, "path": []})
    app = FastAPI()
    app.add_middleware(auth.BodySizeLimit, max_bytes=10_000)
    app.include_router(F.make_router(runner, auth.make_check_key("s3cret", mode), mode=mode, slots=slots))
    return TestClient(app, headers={"X-T2S-Key": "s3cret"})


# ---------------- L3: `t2s flow-ui` (create_builder_app) ----------------

def test_builder_app_requires_trusted_local_host(monkeypatch):
    saved = []
    monkeypatch.setattr(F, "save_flow", lambda *a: saved.append(a) or {"ok": True})
    app = F.create_builder_app()
    local = TestClient(app, client=LOCAL, base_url="http://localhost:8090")
    assert local.get("/flow/catalog").status_code == 200
    assert local.post("/flow/save", json={"id": "W1", "flow": {}}).status_code == 200 and len(saved) == 1
    # DNS rebinding: ижил 127.0.0.1 клиент, халдагчийн Host → 403, хадгалахгүй
    evil = TestClient(app, client=LOCAL, base_url="http://evil.example:8090")
    assert evil.get("/flow/catalog").status_code == 403
    assert evil.post("/flow/save", json={"id": "W2", "flow": {}}).status_code == 403
    assert evil.post("/flow/compile", json={"flow": {}}).status_code == 403
    # гадны хаяг (LAN) эсвэл proxy-ийн толгойтой — 403
    assert TestClient(app, base_url="http://localhost:8090").get("/flow/catalog").status_code == 403
    assert local.get("/flow/catalog", headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 403
    assert len(saved) == 1
    assert local.get("/builder").status_code == 200                  # статик хуудас


def test_builder_app_with_key(monkeypatch):
    monkeypatch.setenv("T2S_WEB_KEY", "k3y")
    app = F.create_builder_app()
    c = TestClient(app, base_url="http://studio.lan")
    assert c.get("/flow/catalog").status_code == 403
    assert c.get("/flow/catalog", headers={"X-T2S-Key": "k3y"}).status_code == 200


def test_flow_ui_binds_loopback_without_key():
    assert auth.safe_bind_host("0.0.0.0", F.BUILDER_MODE, None) == "127.0.0.1"
    assert auth.safe_bind_host("0.0.0.0", F.BUILDER_MODE, "k") == "0.0.0.0"


# ---------------- L5 + H1: /flow/inspect ----------------

class InspRunner:
    def __init__(self, fail=None, gate=None):
        self.fail, self.gate, self.calls = fail, gate, 0

    def inspect(self, flow, q, base=None, translation=None):
        self.calls += 1
        if self.gate is not None:
            self.gate.wait(5)
        if self.fail:
            raise self.fail
        return {"messages": [], "warnings": []}


def test_inspect_error_is_generic_with_request_id(monkeypatch, caplog):
    secret = "while parsing a block mapping in /etc/shadow line 1: root:$6$abc"
    c = _client(monkeypatch, InspRunner(fail=RuntimeError(secret)))
    with caplog.at_level("ERROR", logger="mn_text2sql.service.flow"):
        r = c.post("/flow/inspect", json={"flow": {}, "question": "Зээлийн тоо"})
    assert r.status_code == 500
    detail = r.json()["detail"]
    assert "request id" in detail and "shadow" not in detail and "$6$" not in detail and "/etc" not in detail
    rid = detail.split("request id: ")[1].split(")")[0]
    assert any(rid in rec.getMessage() for rec in caplog.records)     # дэлгэрэнгүй нь серверийн лог-д
    # ValueError (JSONDecodeError г.м.) ч 400 биш — ерөнхий 500 (өмнө нь guard() текстийг нь буцаадаг байв)
    c2 = _client(monkeypatch, InspRunner(fail=ValueError("Expecting value: line 1 column 1 (char 0) SECRET")))
    r2 = c2.post("/flow/inspect", json={"flow": {}, "question": "Зээлийн тоо"})
    assert r2.status_code == 500 and "SECRET" not in r2.json()["detail"]


def test_inspect_compile_errors_stay_400(monkeypatch):
    c = _client(monkeypatch, InspRunner())
    monkeypatch.setattr(F, "compile_flow", lambda flow, base=None: (_ for _ in ()).throw(ValueError("fewshot_k: буруу")))
    r = c.post("/flow/inspect", json={"flow": {}, "question": "Зээлийн тоо"})
    assert r.status_code == 400 and r.json()["detail"] == "fewshot_k: буруу"


def test_inspect_uses_shared_work_slots(monkeypatch):
    gate = threading.Event()
    runner = InspRunner(gate=gate)
    slots = auth.WorkSlots(1)
    c = _client(monkeypatch, runner, slots=slots)
    assert slots.try_acquire()                                         # /ask эсвэл run_job slot-ийг эзэлсэн
    assert c.post("/flow/inspect", json={"flow": {}, "question": "Зээлийн тоо"}).status_code == 429
    assert runner.calls == 0
    slots.release()
    gate.set()
    assert c.post("/flow/inspect", json={"flow": {}, "question": "Зээлийн тоо"}).status_code == 200
    assert slots.busy == 0


@pytest.mark.parametrize("path", ["/flow/inspect", "/flow/run_job"])
def test_question_length_capped(monkeypatch, path):
    runner = InspRunner()
    runner.run = lambda *a, **kw: {"status": "answered", "_audit": {}}
    c = _client(monkeypatch, runner)
    assert c.post(path, json={"flow": {}, "question": "з" * (F.MAX_QUESTION + 1)}).status_code == 400
    assert c.post(path, json={"flow": {}, "question": ["list"]}).status_code == 400
    assert c.post(path, json={"flow": {}, "question": "Зээлийн тоо"}).status_code == 200


def test_inspect_translation_validated(monkeypatch):
    c = _client(monkeypatch, InspRunner())
    body = {"flow": {}, "question": "Зээлийн тоо", "translation": {"x": 1}}
    assert c.post("/flow/inspect", json=body).status_code == 400


# ---------------- H1: /flow/compile хурдны хязгаар, биеийн хэмжээ ----------------

def test_compile_rate_limited(monkeypatch):
    t = [0.0]

    class FrozenRate(auth.RateLimit):   # router-ийн хурдны хязгаарыг хуурамч цагтай (тест тогтвортой)
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw, clock=lambda: t[0])
    monkeypatch.setattr(auth, "RateLimit", FrozenRate)
    c = _client(monkeypatch, None)
    codes = [c.post("/flow/compile", json={"flow": {}}).status_code for _ in range(31)]
    assert codes == [200] * 30 + [429]
    t[0] += 1.0                                                        # 1 с → 10 токен
    assert [c.post("/flow/compile", json={"flow": {}}).status_code for _ in range(11)] == [200] * 10 + [429]


def test_rate_limit_unit():
    t = [0.0]
    rl = auth.RateLimit(rate_per_s=2, burst=3, clock=lambda: t[0])
    assert [rl.try_acquire() for _ in range(4)] == [True, True, True, False]
    t[0] += 0.5                                                        # 0.5 с × 2/с = 1 токен
    assert rl.try_acquire() and not rl.try_acquire()
    t[0] += 100
    assert sum(rl.try_acquire() for _ in range(10)) == 3              # burst-аас хэтрэхгүй
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        rl.check_or_429()
    assert ei.value.status_code == 429 and ei.value.headers["Retry-After"]


def test_body_size_limit_content_length(monkeypatch):
    c = _client(monkeypatch, None)
    big = {"flow": {"nodes": [{"id": "x" * 20_000}]}}
    r = c.post("/flow/compile", json=big)
    assert r.status_code == 413 and "too large" in r.json()["detail"]
    assert c.post("/flow/compile", json={"flow": {}}).status_code == 200


def test_body_size_limit_chunked(monkeypatch):
    """Content-Length-гүй (chunked) биеийг уншиж байхад тоолж 413 — бүхэлд нь санах ойд уншихгүй."""
    import asyncio
    c = _client(monkeypatch, None)
    r = c.post("/flow/compile", content=(b" " * 1000 for _ in range(50)), headers={"Content-Type": "application/json"})
    assert r.status_code == 413
    # серверийн тал: ASGI-ийн receive-ийг шууд дуудаж, хэдэн хэсэг уншсаныг тоолно (TestClient биеийг өөрөө буфердана)
    received, out = [], []

    async def receive():
        received.append(1)
        return {"type": "http.request", "body": b" " * 1000, "more_body": len(received) < 50}

    async def send(msg):
        out.append(msg)
    scope = {"type": "http", "method": "POST", "path": "/flow/compile", "raw_path": b"/flow/compile",
             "query_string": b"", "headers": [(b"content-type", b"application/json"), (b"x-t2s-key", b"s3cret"),
                                              (b"host", b"testserver")],
             "client": ("1.2.3.4", 1), "server": ("testserver", 80), "scheme": "http", "http_version": "1.1",
             "root_path": "", "app": c.app}
    asyncio.run(c.app(scope, receive, send))
    assert out[0]["status"] == 413 and len(received) <= 12              # ≈ 10 KB-ийн дараа зогссон


def test_body_size_limit_disabled_and_non_http():
    import asyncio
    calls = []

    async def app(scope, receive, send):
        calls.append(scope["type"])
    mw = auth.BodySizeLimit(app, max_bytes=0)
    asyncio.run(mw({"type": "http", "headers": [(b"content-length", b"999999999")]}, None, None))
    asyncio.run(auth.BodySizeLimit(app, max_bytes=10)({"type": "lifespan"}, None, None))
    assert calls == ["http", "lifespan"]


# ---------------- H2: too_large-ийн мэдэгдэл (analyst) ----------------

def test_too_large_result_explained():
    from mn_text2sql.service import analyst as A
    res = NS(ok=False, columns=[], rows=[], rowcount=0, truncated=False, error_code="too_large",
             error="Үр дүн хэт том … Result too large.")
    ans = NS(question="q", status="exec_error", sql="SELECT repeat('x', 100000000) FROM t2s.loans", result=res,
             candidates=[], chosen_index=None, confidence=None, unanimous=None)
    x = A.build_extras(ans, cache=None, verified=False)
    assert any(n["kind"] == "size" for n in x["notices"])
    assert "хэт том" in x["confidence"]["reason"]
