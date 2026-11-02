"""Шинжээчийн дараагийн боломжууд (docs/STUDIO_NEXT.md): үр дүнгийн монгол хураангуй, дараагийн асуулт, баталгаажсан
асуулгын сан, SQL засварлаж дахин ажиллуулах. PG, GPU, LLM-гүй — Executor-ыг хуурамчаар орлуулна."""
import json
from types import SimpleNamespace as NS

import pytest

from mn_text2sql.security.sql_guard import SqlGuard
from mn_text2sql.service import analyst as A

LABELS = {"loans": ("Зээл", {"loan_id": "Зээлийн ID", "principal_amount": "Зээлийн үндсэн дүн (гэрээний)",
                             "branch_id": "Зээл олгосон салбар", "status_name": "Зээлийн төлөвийн нэр",
                             "currency_code": "Зээлийн валют (MNT, USD)", "disbursed_date": "Олгосон огноо",
                             "closed_date": "Хаасан огноо"})}
ALLOWED = {"loans": set(LABELS["loans"][1])}


# ---------------- үр дүнгийн хураангуй ----------------

def test_summary_single_value_and_empty():
    assert A.summarize_result(["total"], [[1234567.5]])["text"] == "Хариулт: 1 234 567.5."
    assert "хоосон" in A.summarize_result(["a"], [])["text"]
    assert A.summarize_result([], []) is None
    one = A.summarize_result(["principal_amount", "status_name"], [[10, "ACTIVE"]], labels=LABELS)["text"]
    assert "Зээлийн үндсэн дүн — 10" in one and "(гэрээний)" not in one


def test_summary_category_top_bottom_share():
    s = A.summarize_result(["branch", "total"], [["УБ", 600], ["Дархан", 300], ["Эрдэнэт", 100]])
    t = s["text"]
    assert "Нийт 3 мөр" in t and "хамгийн их нь «УБ» (600)" in t and "хамгийн бага нь «Эрдэнэт» (100)" in t
    assert "Нийлбэр 1 000" in t and "60%" in t


def test_summary_ratio_column_not_summed_and_truncated_note():
    t = A.summarize_result(["branch", "avg_rate"], [["a", 10.5], ["b", 12], ["c", 11]], row_count=1000,
                           truncated=True)["text"]
    assert "Нийлбэр" not in t and "бүрэн биш" in t and "Нийт 1000 мөр" in t


def test_summary_time_series_change():
    t = A.summarize_result(["month", "total"], [["2026-03", 150], ["2026-01", 100], ["2026-02", 120]])["text"]
    assert "2026-01 үед 100 → 2026-03 үед 150 (+50.0%)" in t and "Хамгийн өндөр нь 2026-03" in t


# ---------------- дараагийн асуулт ----------------

def test_followups_for_scalar_aggregate():
    f = A.suggest_followups("Идэвхтэй зээлийн нийт дүн хэд вэ?",
                            "SELECT SUM(principal_amount) FROM t2s.loans WHERE status_name = 'ACTIVE'", LABELS)
    texts = [x["text"] for x in f]
    assert "Салбар тус бүрээр" in texts
    assert any(t.startswith("Сар бүрээр (Олгосон огноо") for t in texts)
    assert all("Зээлийн төлөвийн нэр" not in t for t in texts)          # шүүсэн баганаар дахин задлахгүй
    assert all(x["question"].startswith("Идэвхтэй зээлийн нийт дүн хэд вэ — ") for x in f)   # бүтэн асуулт
    assert len(f) <= 4


def test_followups_grouped_and_listing_and_year_filter():
    g = A.suggest_followups("Төлөв тус бүрийн дүн", "SELECT status_name, SUM(principal_amount) FROM t2s.loans "
                            "WHERE EXTRACT(YEAR FROM disbursed_date) = 2026 GROUP BY status_name LIMIT 1000",
                            LABELS, enforce_limit=1000)
    t = [x["text"] for x in g]
    assert "Хамгийн их 5" in t and "Нийт дүн (задаргаагүй)" in t and "Өмнөх онтой харьцуулах" in t
    top5 = A.suggest_followups("x", "SELECT status_name, COUNT(*) FROM t2s.loans GROUP BY 1 ORDER BY 2 DESC LIMIT 5",
                               LABELS, enforce_limit=1000)
    assert "Хамгийн их 5" not in [x["text"] for x in top5]
    lst = [x["text"] for x in A.suggest_followups("Зээлүүд", "SELECT loan_id FROM t2s.loans", LABELS)]
    assert lst[:2] == ["Нийт тоо", "Салбар тус бүрийн тоо"]


@pytest.mark.parametrize("q,sql", [("", "SELECT 1"), ("q", None), ("q", "SELEC ((")])
def test_followups_never_raise(q, sql):
    assert A.suggest_followups(q, sql) == []


# ---------------- баталгаажсан асуулгын сан ----------------

def test_verified_store_add_dedupe_match_remove(tmp_path):
    vs = A.VerifiedStore(tmp_path / "v.jsonl")
    a = vs.add("Идэвхтэй зээлийн тоо хэд вэ?", "SELECT COUNT(*) FROM t2s.loans WHERE status_name='ACTIVE';",
               tables=["loans"])
    assert a["id"].startswith("VQ-") and not a["duplicate"]
    assert vs.add("идэвхтэй зээлийн тоо хэд вэ", "select count(*)  from t2s.loans where status_name='ACTIVE'")["duplicate"]
    exact = vs.match("Идэвхтэй  зээлийн тоо хэд вэ?")
    assert exact[0]["score"] == 1.0
    assert vs.match("Идэвхтэй зээлүүдийн тоо хэд вэ")          # залгавар өөр — язгуураар таарна
    assert vs.match("Хадгаламжийн хүү") == []
    ex = json.loads(vs.as_examples().splitlines()[0])
    assert {"id", "question_mn", "gold_sql", "tables"} <= set(ex) and ex["tables"] == ["loans"]
    assert vs.remove(a["id"]) and not vs.remove(a["id"]) and vs.all() == []
    with pytest.raises(ValueError):
        vs.add("", "SELECT 1")


def test_verified_info_same_sql_ignores_guard_limit(tmp_path):
    vs = A.VerifiedStore(tmp_path / "v.jsonl")
    vs.add("Зээлийн тоо", "SELECT COUNT(*) FROM t2s.loans")
    m = A.verified_info("Зээлийн тоо", "SELECT COUNT(*) FROM t2s.loans LIMIT 1000", vs, enforce_limit=1000)
    assert m[0]["same_sql"]
    assert not A.verified_info("Зээлийн тоо", "SELECT COUNT(loan_id) FROM t2s.loans", vs)[0]["same_sql"]
    assert A.verified_info("Зээлийн тоо", "x", None) == []


def _ans(question="Төлөв тус бүрийн дүн", sql="SELECT status_name, SUM(principal_amount) AS total FROM t2s.loans "
                                               "GROUP BY status_name"):
    res = NS(ok=True, columns=["status_name", "total"], rows=[["ACTIVE", 500], ["CLOSED", 200], ["LEGAL", 50]],
             rowcount=3, truncated=False, error_code=None)
    cand = NS(sql=sql, raw_sql=sql, error=None, error_stage=None, tables=["loans"], result=res)
    return NS(question=question, sql=sql, status="answered", confidence=1.0, unanimous=True, candidates=[cand],
              chosen_index=0, result=res)


def test_build_extras_adds_summary_followups_verified(tmp_path, monkeypatch):
    monkeypatch.delenv("T2S_SQL_EDIT", raising=False)
    vs = A.VerifiedStore(tmp_path / "v.jsonl")
    vs.add("Төлөв тус бүрийн дүн", _ans().sql)
    x = A.build_extras(_ans(), labels=LABELS, cache=A.ResultCache(), verified=vs)
    assert "хамгийн их нь «ACTIVE» (500)" in x["summary"]["text"]
    assert x["followups"] and x["verified"][0]["same_sql"]
    assert x["sql"] == _ans().sql and x["sql_edit"] is False          # cfg-гүй = shadow → засвар хаалттай
    monkeypatch.setenv("T2S_SQL_EDIT", "1")
    assert A.build_extras(_ans(), labels=LABELS, cache=None, verified=False)["sql_edit"] is True
    assert A.build_extras(_ans(), labels=LABELS, cache=None, verified=False)["verified"] == []


def test_sql_edit_enabled_rules(monkeypatch):
    monkeypatch.delenv("T2S_SQL_EDIT", raising=False)
    assert not A.sql_edit_enabled(None) and not A.sql_edit_enabled({"service": {"mode": "shadow"}})
    assert A.sql_edit_enabled({"service": {"mode": "pilot"}})
    monkeypatch.setenv("T2S_SQL_EDIT", "0")
    assert not A.sql_edit_enabled({"service": {"mode": "live"}})


# ---------------- API: SQL засвар, баталгаажсан сан ----------------

class FakeExecutor:
    def __init__(self):
        self.calls = []

    def run(self, sql, max_rows=None, explain=False):
        self.calls.append(sql)
        return NS(ok=True, columns=["status_name", "n"], rows=[("ACTIVE", 7), ("CLOSED", 3)], rowcount=2,
                  truncated=False, error=None, error_code=None)


@pytest.fixture
def api(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient
    monkeypatch.setenv("T2S_ADMIN_KEY", "adm1n")
    monkeypatch.delenv("T2S_SQL_EDIT", raising=False)
    ex = FakeExecutor()
    guard = SqlGuard(ALLOWED, "t2s", set(), "2026-06-30", enforce_limit=1000)
    runner = NS(guard=guard, executor=ex, cfg={"service": {"mode": "pilot"}, "security": {"enforce_limit": 1000},
                                               "db": {"statement_timeout_ms": 20000}})
    cache, vs, audit = A.ResultCache(), A.VerifiedStore(tmp_path / "v.jsonl"), []

    def ck(k):
        if k != "s3cret":
            raise HTTPException(status_code=403, detail="key")
    app = FastAPI()
    app.include_router(A.make_analyst_router(ck, cache=cache, store=A.FeedbackStore(tmp_path / "c.jsonl",
                                                                                     tmp_path / "votes.jsonl"),
                                             runner=runner, verified=vs, audit=audit.append))
    cache.put("abc123", {"question": "Төлөв тус бүрийн зээлийн тоо", "sql": "SELECT status_name, COUNT(*) AS n "
                         "FROM t2s.loans GROUP BY status_name LIMIT 1000", "columns": ["status_name", "n"],
                         "rows": [["ACTIVE", 7]], "tables": ["loans"], "status": "answered"})
    return NS(c=TestClient(app), ex=ex, cache=cache, vs=vs, audit=audit, runner=runner)


def test_rerun_runs_through_guard_with_limit(api):
    r = api.c.post("/analyst/rerun?k=s3cret", json={"rid": "abc123",
                                                    "sql": "SELECT status_name, COUNT(*) AS n FROM t2s.loans "
                                                           "WHERE currency_code = 'MNT' GROUP BY status_name"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["status"] == "edited" and j["rows"] == [["ACTIVE", 7], ["CLOSED", 3]]
    assert "LIMIT 1000" in api.ex.calls[0]                          # guard хязгаар нэмсэн SQL-ийг ажиллуулна
    assert j["analyst"]["confidence"]["label"] == "гараар засварласан"
    assert j["analyst"]["summary"]["text"].startswith("Нийт 2 мөр")
    assert api.cache.get(j["request_id"])["status"] == "edited"   # экспорт ажиллана, 👍 нэр дэвшигч болохгүй
    assert api.c.get(f"/analyst/export/{j['request_id']}?fmt=csv&k=s3cret").status_code == 200
    assert api.audit[-1]["status"] == "edited" and "засварласан" in api.audit[-1]["question"]
    vote = api.c.post("/analyst/feedback?k=s3cret", json={"rid": j["request_id"], "good": True}).json()
    assert not vote["candidate_added"]


@pytest.mark.parametrize("bad", ["DROP TABLE t2s.loans", "SELECT 1; DELETE FROM t2s.loans",
                                 "SELECT * FROM public.users", "SELECT * FROM t2s.secret_table",
                                 "UPDATE t2s.loans SET status_name = 'X'", "SELECT pg_sleep(100)",
                                 "SELECT l.password FROM t2s.loans l"])
def test_rerun_rejects_unsafe_sql_without_touching_db(api, bad):
    r = api.c.post("/analyst/rerun?k=s3cret", json={"sql": bad})
    assert r.status_code == 400 and "зөвшөөрөгдөхгүй" in r.json()["detail"]
    assert api.ex.calls == [] and api.audit[-1]["status"] == "edited_rejected"


def test_rerun_access_control(api, monkeypatch):
    body = {"sql": "SELECT COUNT(*) FROM t2s.loans"}
    assert api.c.post("/analyst/rerun", json=body).status_code == 403                  # T2S_WEB_KEY
    assert api.c.post("/analyst/rerun?k=s3cret", json={"sql": "x" * 9000}).status_code == 422
    assert api.c.post("/analyst/rerun?k=s3cret", json={"sql": "SELECT 1", "rid": "zz;x"}).status_code == 422
    monkeypatch.setenv("T2S_SQL_EDIT", "0")
    assert api.c.post("/analyst/rerun?k=s3cret", json=body).status_code == 403
    monkeypatch.delenv("T2S_SQL_EDIT")
    api.runner.cfg["service"]["mode"] = "shadow"                                      # shadow-д үр дүн харуулахгүй
    assert api.c.post("/analyst/rerun?k=s3cret", json=body).status_code == 403
    assert api.ex.calls == []


def test_rerun_without_db_is_501(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    monkeypatch.setenv("T2S_SQL_EDIT", "1")
    app = FastAPI()
    app.include_router(A.make_analyst_router(None, cache=A.ResultCache(), verified=A.VerifiedStore(tmp_path / "v")))
    assert TestClient(app).post("/analyst/rerun", json={"sql": "SELECT 1"}).status_code == 501


def test_verified_admin_flow(api):
    c = api.c
    assert c.post("/analyst/verified", json={"rid": "abc123"}).status_code == 403
    assert c.post("/analyst/verified?admin=wrong", json={"rid": "abc123"}).status_code == 403
    j = c.post("/analyst/verified?admin=adm1n", json={"rid": "abc123", "note": "шалгав"}).json()
    assert j["ok"] and not j["duplicate"]
    vid = j["id"]
    assert c.post("/analyst/verified?admin=adm1n", json={"rid": "abc123"}).json()["duplicate"]
    assert c.post("/analyst/verified?admin=adm1n", json={"rid": "ffff99"}).status_code == 404
    # гараар SQL — guard шалгана
    bad = c.post("/analyst/verified?admin=adm1n", json={"question": "x", "sql": "DELETE FROM t2s.loans"})
    assert bad.status_code == 400
    ok = c.post("/analyst/verified?admin=adm1n", json={"question": "Зээлийн тоо", "sql": "SELECT COUNT(*) FROM t2s.loans"})
    assert ok.status_code == 200 and ok.json()["id"] != vid
    items = c.get("/analyst/verified?k=s3cret").json()["items"]
    assert len(items) == 2 and items[0]["tables"] == ["loans"]
    assert c.get("/analyst/verified").status_code == 403
    assert c.get("/analyst/verified?k=s3cret&q=Төлөв тус бүрийн зээлийн тоо").json()["items"][0]["id"] == vid
    exp = c.get("/analyst/verified/export?admin=adm1n")
    assert exp.status_code == 200 and json.loads(exp.text.splitlines()[0])["gold_sql"].startswith("SELECT")
    assert c.get("/analyst/verified/export?k=s3cret").status_code == 403
    # LLM-гүй шууд ажиллуулах — SQL нь серверийн сангаас
    run = c.post(f"/analyst/verified/{vid}/run?k=s3cret").json()
    assert run["status"] == "verified" and run["analyst"]["confidence"]["level"] == "high"
    assert run["analyst"]["verified"][0]["same_sql"]
    assert c.post("/analyst/verified/VQ-00000000/run?k=s3cret").status_code == 404
    assert c.delete(f"/analyst/verified/{vid}?k=s3cret").status_code == 403
    assert c.delete(f"/analyst/verified/{vid}?admin=adm1n").json()["ok"]
    assert c.delete(f"/analyst/verified/{vid}?admin=adm1n").status_code == 404


def test_verified_raw_sql_needs_guard(tmp_path, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    monkeypatch.setenv("T2S_ADMIN_KEY", "adm1n")
    app = FastAPI()
    app.include_router(A.make_analyst_router(None, cache=A.ResultCache(), verified=A.VerifiedStore(tmp_path / "v")))
    r = TestClient(app).post("/analyst/verified?admin=adm1n", json={"question": "q", "sql": "SELECT 1"})
    assert r.status_code == 501


def test_js_has_new_features():
    from pathlib import Path
    js = (Path(A.__file__).with_name("static") / "analyst.js").read_text(encoding="utf-8")
    for s in ("/analyst/rerun", "/analyst/verified/", "followups", "summary"):
        assert s in js
