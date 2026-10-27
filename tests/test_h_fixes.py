"""H бүлгийн ерөнхий засварууд: boolean 0/1, схемээс гаргасан SQL засвар, fingerprint-ийн нийцтэй байдал."""

from mn_text2sql.pipeline.dialect import sqlite_to_postgres
from mn_text2sql.pipeline.options import RunOptions
from mn_text2sql.pipeline.postproc import SqlPostprocessor

BOOL = frozenset({"is_active", "is_restructured"})


def test_bool_literal_eq_neq_in():
    assert sqlite_to_postgres("SELECT COUNT(*) FROM loans WHERE is_active = 1 AND x = 1", BOOL) == \
        "SELECT COUNT(*) FROM loans WHERE is_active = TRUE AND x = 1"
    assert "FALSE = is_restructured" in sqlite_to_postgres("SELECT * FROM t WHERE 0 = is_restructured", BOOL)
    assert "is_active <> FALSE" in sqlite_to_postgres("SELECT * FROM t WHERE is_active <> 0", BOOL)
    assert "IN (FALSE, TRUE)" in sqlite_to_postgres("SELECT * FROM t WHERE is_active IN (0, 1)", BOOL)


def test_bool_literal_untouched_without_bool_cols():
    assert sqlite_to_postgres("SELECT * FROM t WHERE is_active = 1") == "SELECT * FROM t WHERE is_active = 1"


class _Res:
    def __init__(self, rows):
        self.ok, self.rows = True, rows


class _FakeExec:
    """information_schema ба DISTINCT асуулгад тогтмол хариу өгнө."""

    def run(self, sql, max_rows=None):
        if "information_schema.columns" in sql:
            return _Res([("t2s", "loans", "status_code", "text"), ("t2s", "loans", "status_name", "text"),
                         ("t2s", "loans", "disbursed_date", "date"), ("t2s", "loans", "amount", "numeric")])
        if "count(DISTINCT" in sql:
            return _Res([(3,)])
        if 'SELECT DISTINCT "status_code"::text' in sql:
            return _Res([("ACTIVE", "Идэвхтэй"), ("CLOSED", "Хаагдсан"), ("OVERDUE", "Хугацаа хэтэрсэн")])
        if 'SELECT DISTINCT "status_code"' in sql:
            return _Res([("ACTIVE",), ("CLOSED",), ("OVERDUE",)])
        if 'SELECT DISTINCT "status_name"' in sql:
            return _Res([("Идэвхтэй",), ("Хаагдсан",), ("Хугацаа хэтэрсэн",)])
        return _Res([])


def test_postproc_rules():
    pp = SqlPostprocessor(_FakeExec())
    sql, rules = pp.apply("SELECT COUNT(*) FROM loans WHERE status_name = 'CLOSED'")
    assert rules == ["R2"] and "status_code = 'CLOSED'" in sql
    sql, rules = pp.apply("SELECT COUNT(*) FROM loans l WHERE l.status_code = 'closed'")
    assert rules == ["R5"] and "'CLOSED'" in sql
    sql, rules = pp.apply("SELECT COUNT(*) FROM loans WHERE disbursed_date LIKE '2026-03%'")
    assert rules == ["R3"] and "CAST(disbursed_date AS TEXT) LIKE" in sql
    sql, rules = pp.apply("SELECT ROUND(AVG(amount), 2) FROM loans")
    assert rules == ["R4"] and "ROUND(CAST(AVG(amount) AS DECIMAL), 2)" in sql.replace("NUMERIC", "DECIMAL")
    # хадгалагдсан утгатай таарч байгаа бол хөндөхгүй
    assert pp.apply("SELECT * FROM loans WHERE status_code = 'ACTIVE'") == \
        ("SELECT * FROM loans WHERE status_code = 'ACTIVE'", [])


def test_fingerprint_ignores_late_defaults():
    fp = RunOptions().fingerprint()
    for k in ("postproc", "abstain_on_error", "intent_refuse_only", "translate_mode"):
        assert k not in fp
    assert RunOptions(postproc=True).fingerprint()["postproc"] is True
    assert RunOptions(translate_mode="bilingual").fingerprint()["translate_mode"] == "bilingual"
