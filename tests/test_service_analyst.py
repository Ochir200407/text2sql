"""Шинжээчийн нэмэлт (service/analyst.py): SQL тайлбар, итгэл, хязгаар, график, CSV/XLSX, 👍/👎 — PG, GPU, LLM-гүй."""
import io
import json
from types import SimpleNamespace as NS

import pytest

from mn_text2sql.service import analyst as A

LABELS = {"loans": ("Зээл", {"amount": "Олгосон дүн", "branch_id": "Салбарын код", "status": "Төлөв",
                             "disbursed_date": "Олгосон огноо"}),
          "branches": ("Салбар", {"name": "Салбарын нэр"})}


# ---------------- SQL тайлбар ----------------

def test_explain_full_query_mentions_every_clause():
    sql = ("SELECT b.name, SUM(l.amount) AS total FROM t2s.loans l JOIN t2s.branches b ON b.id = l.branch_id "
           "WHERE l.status = 'ACTIVE' AND l.disbursed_date >= '2026-06-01' AND l.disbursed_date < '2026-07-01' "
           "GROUP BY b.name HAVING SUM(l.amount) > 0 ORDER BY total DESC LIMIT 5")
    x = A.explain_sql(sql, LABELS)
    d = dict(x["parts"])
    assert "Зээл (loans)" in d["Хүснэгтүүд (JOIN-оор холбосон)"] and "Салбар (branches)" in d["Хүснэгтүүд (JOIN-оор холбосон)"]
    assert "Төлөв нь «ACTIVE»" in d["Шүүлт"] and "Олгосон огноо ≥ «2026-06-01»" in d["Шүүлт"]
    assert d["Бүлэглэлт"] == "Салбарын нэр"
    assert d["Тооцоо"] == "Олгосон дүн — нийлбэр"
    assert "буурахаар" in d["Эрэмбэ"] and "Олгосон дүн" in d["Эрэмбэ"]   # alias → тооцооны тайлбар
    assert d["Хязгаар"] == "эхний 5 мөр"
    assert "Бүлгийн шүүлт" in d
    assert x["text"].startswith("Хүснэгтүүд")


def test_explain_without_labels_and_misc_predicates():
    sql = ("SELECT COUNT(*), COUNT(DISTINCT customer_id) FROM t2s.loans WHERE EXTRACT(YEAR FROM disbursed_date) = 2026 "
           "AND currency_code IN ('USD','EUR') AND closed_at IS NULL AND note IS NOT NULL AND term BETWEEN 6 AND 12 "
           "AND name ILIKE '%хаан%' LIMIT 1000")
    d = dict(A.explain_sql(sql, None, enforce_limit=1000)["parts"])
    assert d["Хүснэгт"] == "loans"
    f = d["Шүүлт"]
    assert "disbursed_date (он) нь 2026" in f and "аль нэг" in f and "closed_at хоосон" in f
    assert "note хоосон биш" in f and "6–12 хооронд" in f and "хэв маягтай" in f
    assert "мөрийн тоо" in d["Тооцоо"] and "давхардалгүй тоо" in d["Тооцоо"]
    assert d["Хязгаар"] == "хамгаалалтын дээд хязгаар 1000 мөр"


def test_explain_cte_flagged_and_cte_not_listed_as_table():
    x = A.explain_sql("WITH t AS (SELECT branch_id, SUM(amount) s FROM t2s.loans GROUP BY 1) SELECT * FROM t", LABELS)
    d = dict(x["parts"])
    assert x["complex"] and "Анхаар" in d
    assert d["Хүснэгт"] == "Зээл (loans)"


@pytest.mark.parametrize("bad", [None, "", "SELEC broken ((("])
def test_explain_never_raises(bad):
    assert A.explain_sql(bad) is None or isinstance(A.explain_sql(bad), dict)


# ---------------- итгэл, шалтгаан, хязгаар ----------------

def test_confidence_levels():
    assert A.confidence_info("answered", 1.0, True, 8)["level"] == "high"
    assert A.confidence_info("answered", 0.625, False, 8)["level"] == "medium"
    assert A.confidence_info("answered", 0.25, False, 8)["level"] == "low"
    one = A.confidence_info("answered", 1.0, True, 1)
    assert one["level"] == "unknown" and "Нэг л SQL" in one["reason"]   # ганц хувилбарын 100% итгэл төөрөгдүүлэхгүй


def test_refusal_and_clarify_reasons_are_plain_mongolian():
    r = A.confidence_info("refused", None, None, 0, stopped_early=True)
    assert r["level"] == "none" and "шүүлтээр" in r["reason"] and r["tips"]
    assert "SQL бичээгүй" in A.confidence_info("refused", 0.0, None, 1, "refusal")["reason"]
    c = A.confidence_info("clarify", 0.375, False, 8)
    assert "38%" in c["reason"] and len(c["tips"]) == 3
    assert "хариулахаас татгалзав" in A.confidence_info("abstained", 0.0, None, 8, "exec")["reason"]
    assert "statement_timeout" in A.confidence_info("error", None, None, 1, "exec", "timeout")["reason"]


def test_notices_limit_shown_timeout():
    n = A.notices(row_count=1000, shown=20, truncated=False, limit=1000, exportable=True)
    kinds = [x["kind"] for x in n]
    assert kinds == ["limit", "shown"] and "CSV" in n[1]["text"]
    assert A.notices(row_count=5, shown=20, limit=1000) == []
    t = A.notices(row_count=None, shown=None, limit=1000, timeout_ms=20000, error_code="timeout")
    assert t[0]["kind"] == "timeout" and "20 секунд" in t[0]["text"]


# ---------------- график ----------------

def test_chart_bar_for_category_and_measure():
    c = A.suggest_chart(["branch", "total"], [["УБ", "1200.5"], ["Дархан", "300"], ["Эрдэнэт", 50]])
    assert c["type"] == "bar" and c["x"] == "branch" and c["points"][0] == ["УБ", 1200.5]


def test_chart_line_for_time_sorted():
    rows = [["2026-03", 3], ["2026-01", 1], ["2026-02", 2]]
    c = A.suggest_chart(["month", "n"], rows)
    assert c["type"] == "line" and [p[0] for p in c["points"]] == ["2026-01", "2026-02", "2026-03"]
    assert A.suggest_chart(["year", "n"], [[2024, 1], [2025, 2], [2026, 3]])["type"] == "line"


def test_chart_none_cases():
    assert A.suggest_chart(["n"], [[5]])["type"] == "none"                                   # ганц тоо
    assert A.suggest_chart(["a", "b"], [["x", "y"], ["z", "w"]])["type"] == "none"           # тоогүй
    assert A.suggest_chart(["a", "b", "n"], [["x", "y", 1], ["z", "w", 2]])["type"] == "none"  # 2 ангилал
    many = [[f"c{i}", i] for i in range(40)]
    assert A.suggest_chart(["c", "n"], many)["type"] == "none"
    # *_id тоон багана хэмжигдэхүүн биш, ангилал
    c = A.suggest_chart(["branch_id", "total", "cnt"], [[1, 10, 1], [2, 20, 2]])
    assert c["type"] == "bar" and c["x"] == "branch_id" and c["y"] == "total" and "note" in c


# ---------------- CSV / XLSX ----------------

def test_csv_bom_and_formula_injection_guard():
    b = A.to_csv(["нэр", "дүн"], [["=HYPERLINK(\"http://x\")", -5], ["@cmd", None], ["Болд", "-12.5"]])
    assert b.startswith(b"\xef\xbb\xbf")
    text = b.decode("utf-8-sig")
    assert "'=HYPERLINK" in text and "'@cmd" in text
    assert ",-5\r\n" in text and "-12.5" in text and "'-12.5" not in text   # сөрөг тоо хэвээр


def test_xlsx_roundtrip():
    pytest.importorskip("openpyxl")
    from openpyxl import load_workbook
    data = A.to_xlsx(["нэр", "дүн"], [["=1+1", 1.5], ["Болд", 2]], question="асуулт", sql="SELECT 1")
    wb = load_workbook(io.BytesIO(data))
    ws = wb["Үр дүн"]
    assert [c.value for c in ws[1]] == ["нэр", "дүн"]
    assert ws["A2"].value == "'=1+1" and ws["A2"].data_type == "s" and ws["B3"].value == 2
    assert wb["SQL"]["B2"].value == "SELECT 1"


# ---------------- кэш, үнэлгээ ----------------

def test_cache_eviction_and_ttl(monkeypatch):
    from decimal import Decimal
    c = A.ResultCache(max_items=2, ttl_s=10)
    c.put("a", {"rows": [[Decimal("1.50"), Decimal("3")]]})
    assert c.get("a")["rows"] == [[1.5, 3]]
    c.put("b", {}); c.put("c", {})
    assert c.get("a") is None and c.get("c") is not None
    t = A.time.time()
    monkeypatch.setattr(A.time, "time", lambda: t + 11)
    assert c.get("c") is None


def _key(monkeypatch):
    from cryptography.fernet import Fernet
    monkeypatch.setenv("T2S_AUDIT_KEY", Fernet.generate_key().decode())


def test_feedback_without_key_stores_no_text(tmp_path, monkeypatch):
    monkeypatch.delenv("T2S_AUDIT_KEY", raising=False)
    st = A.FeedbackStore(tmp_path / "cand.jsonl", tmp_path / "votes.jsonl")
    j = st.vote("abc123", {"question": "Зээл", "sql": "SELECT 1", "status": "answered"}, True)
    assert not j["candidate_added"] and "T2S_AUDIT_KEY" in j["note"]
    assert not (tmp_path / "cand.jsonl").exists()


def test_feedback_writes_only_candidate_file(tmp_path, monkeypatch):
    _key(monkeypatch)
    st = A.FeedbackStore(tmp_path / "cand.jsonl", tmp_path / "votes.jsonl")
    e = {"question": "Идэвхтэй зээл хэд вэ?", "sql": "SELECT COUNT(*) FROM t2s.loans", "status": "answered",
         "tables": ["loans"]}
    assert st.vote("abc123", e, True)["candidate_added"]
    assert st.vote("abc124", e, True)["duplicate"]
    st.vote("abc125", e, False)
    st.vote("abc126", dict(e, status="abstained"), True)   # хариулаагүйг нэр дэвшүүлэхгүй
    cands = [json.loads(x) for x in (tmp_path / "cand.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(cands) == 1 and cands[0]["review"] == "pending" and not cands[0]["gold_sql"].startswith("SELECT")  # диск дээр шифртэй
    assert json.loads(st.export_plain().splitlines()[0])["gold_sql"].startswith("SELECT")
    votes = (tmp_path / "votes.jsonl").read_text(encoding="utf-8")
    assert "Идэвхтэй" not in votes and votes.count("\n") == 4   # санал бүрт текст биш hash
    assert st.count() == 1
    assert A.CANDIDATES_FILE.startswith("build/") and "examples.jsonl" != A.CANDIDATES_FILE


# ---------------- Answer → нэмэлт ----------------

def _ans(status="answered", rows=None, conf=1.0, n=3, error_code=None):
    res = NS(ok=error_code is None, columns=["branch", "total"], rows=rows or [["УБ", 10], ["Дархан", 5]],
             rowcount=len(rows or [1, 2]), truncated=False, error_code=error_code)
    cands = [NS(sql="SELECT branch, SUM(amount) AS total FROM t2s.loans GROUP BY branch", raw_sql=None,
                tables=["loans"], error_stage=None, error=None, result=res) for _ in range(n)]
    return NS(question="q", status=status, sql=cands[0].sql if n else None, result=res, candidates=cands,
              chosen_index=0 if n else None, confidence=conf, unanimous=conf == 1.0)


def test_build_extras_caches_and_describes():
    cache = A.ResultCache()
    cfg = {"security": {"enforce_limit": 1000}, "db": {"statement_timeout_ms": 20000}}
    x = A.build_extras(_ans(), cfg=cfg, labels=LABELS, cache=cache, question="Салбараар дүн")
    assert x["confidence"]["level"] == "high" and x["chart"]["type"] == "bar"
    assert x["export"] and x["feedback"] and dict(x["sql_explain"]["parts"])["Бүлэглэлт"] == "branch"
    assert cache.get(x["rid"])["question"] == "Салбараар дүн"


def test_build_extras_timeout_and_refusal_without_candidates():
    x = A.build_extras(_ans(status="error", error_code="timeout"), cfg={"db": {"statement_timeout_ms": 5000}},
                       cache=A.ResultCache())
    assert any(n["kind"] == "timeout" for n in x["notices"]) and not x["export"]
    r = A.build_extras(_ans(status="refused", n=0), cache=A.ResultCache())
    assert r["confidence"]["level"] == "none" and "шүүлтээр" in r["confidence"]["reason"] and r["sql_explain"] is None


def test_build_extras_never_raises():
    assert "error" in A.build_extras(object())
