"""/analyst/* endpoint-ууд (FastAPI TestClient) — PG, загваргүй: кэшийг гараар дүүргэнэ."""
import io
import json

import pytest

pytest.importorskip("fastapi")
from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from mn_text2sql.service import analyst as A  # noqa: E402


@pytest.fixture
def client(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("T2S_AUDIT_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("T2S_ADMIN_KEY", "adm1n")
    cache = A.ResultCache()
    store = A.FeedbackStore(tmp_path / "cand.jsonl", tmp_path / "votes.jsonl")

    def ck(k):
        if k != "s3cret":
            raise HTTPException(status_code=403, detail="key")
    app = FastAPI()
    app.include_router(A.make_analyst_router(ck, cache=cache, store=store))
    cache.put("abc123", {"question": "Салбараар дүн", "sql": "SELECT 1", "columns": ["салбар", "дүн"],
                         "rows": [["=cmd", 1], ["УБ", 2]], "tables": ["loans"], "status": "answered"})
    return TestClient(app), tmp_path


def test_key_required(client):
    c, _ = client
    assert c.get("/analyst/export/abc123?fmt=csv").status_code == 403
    assert c.post("/analyst/feedback", json={"rid": "abc123", "good": True}).status_code == 403


def test_csv_export(client):
    c, _ = client
    r = c.get("/analyst/export/abc123?fmt=csv&k=s3cret")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    assert r.content.decode("utf-8-sig").splitlines()[1] == "'=cmd,1"
    assert c.get("/analyst/export/ffff99?fmt=csv&k=s3cret").status_code == 404
    assert c.get("/analyst/export/abc123?fmt=pdf&k=s3cret").status_code == 400


def test_xlsx_export(client):
    pytest.importorskip("openpyxl")
    from openpyxl import load_workbook
    c, _ = client
    r = c.get("/analyst/export/abc123?fmt=xlsx&k=s3cret")
    assert r.status_code == 200
    assert load_workbook(io.BytesIO(r.content))["Үр дүн"]["A3"].value == "УБ"


def test_feedback_and_candidates_download(client):
    c, tmp = client
    assert c.get("/analyst/candidates?k=s3cret").status_code == 403          # хэрэглэгчийн түлхүүр хүрэлцэхгүй
    assert c.get("/analyst/candidates?admin=adm1n").status_code == 404
    j = c.post("/analyst/feedback?k=s3cret", json={"rid": "abc123", "good": True}).json()
    assert j["candidate_added"]
    assert c.post("/analyst/feedback?k=s3cret", json={"rid": "zz; drop", "good": True}).status_code == 422
    assert c.post("/analyst/feedback?k=s3cret", json={"rid": "ffff99", "good": True}).status_code == 404
    lines = c.get("/analyst/candidates?admin=adm1n").text.splitlines()
    assert json.loads(lines[0])["question_mn"] == "Салбараар дүн"
    assert (tmp / "cand.jsonl").exists()


def test_feedback_cannot_inject_sql(client):
    """Үйлчлүүлэгч SQL илгээж чадахгүй: нэр дэвшигчийн SQL зөвхөн серверийн кэшээс."""
    c, tmp = client
    c.post("/analyst/feedback?k=s3cret", json={"rid": "abc123", "good": True, "sql": "DROP TABLE x"})
    assert "DROP" not in (tmp / "cand.jsonl").read_text(encoding="utf-8")


def test_js_served_and_wired_into_pages():
    from mn_text2sql.service.flow import make_router
    app = FastAPI()
    app.include_router(make_router(None))
    c = TestClient(app)
    r = c.get("/analyst/analyst.js")
    assert r.status_code == 200 and "T2SAnalyst" in r.text
    assert '<script src="/analyst/analyst.js">' in c.get("/builder").text
    from pathlib import Path
    idx = (Path(A.__file__).with_name("static") / "index.html").read_text(encoding="utf-8")
    assert "/analyst/analyst.js" in idx and "T2SAnalyst.render" in idx


def test_candidates_closed_without_admin_key(client, monkeypatch):
    c, _ = client
    monkeypatch.delenv("T2S_ADMIN_KEY")
    assert c.get("/analyst/candidates?admin=").status_code == 403
