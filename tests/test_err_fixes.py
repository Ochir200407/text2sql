"""2026-11-01 dev-ийн алдааны шинжилгээний засварууд (бүгд RunOptions-оор, анхдагч off — хуучин гаралт, fingerprint ижил):
guard_scope (alias-ийн хүрээ), transpile_fix (SQLite→PG F1–F5), extract_fallback (тасарсан сүүлийн блок), literal_fix
(postproc R6). DB, загваргүй — хуурамч executor/LLM; жишээ SQL нь dev-ийн бүтцийг (асуултын агуулгыг биш) дуурайна."""
from mn_text2sql.db.executor import ExecResult
from mn_text2sql.llm.client import LLMResponse
from mn_text2sql.pipeline import orchestrator as orch
from mn_text2sql.pipeline.dialect import column_types, sqlite_to_postgres
from mn_text2sql.pipeline.options import _LATE_DEFAULTS, RunOptions, load_experiments, resolve_run
from mn_text2sql.pipeline.postproc import SqlPostprocessor
from mn_text2sql.security.sql_guard import GuardResult, SqlGuard, extended_block, extract_sql
from mn_text2sql.service.flow import BY_TYPE

NEW = ("guard_scope", "transpile_fix", "extract_fallback", "literal_fix")

ALLOWED = {"loans": {"loan_id", "branch_id", "principal_outstanding_mnt", "days_past_due", "is_active_portfolio"},
           "loan_payments": {"payment_id", "branch_id", "payment_date", "amount"},
           "branches": {"branch_id", "branch_name"},
           "deposits": {"deposit_id", "branch_id", "maturity_date", "current_balance_mnt"}}

# dev X032-ийн бүтэц: CTE дотор `loan_payments AS lp`, гадна `loan_portfolio AS lp` (CTE)
CTE_ALIAS_REUSE = ("WITH loan_portfolio AS (SELECT l.branch_id, SUM(l.principal_outstanding_mnt) AS portfolio_mnt "
                   "FROM loans AS l GROUP BY l.branch_id), repayments AS (SELECT lp.branch_id, COUNT(lp.payment_id) AS n "
                   "FROM loan_payments AS lp GROUP BY lp.branch_id) SELECT b.branch_name, lp.portfolio_mnt, r.n "
                   "FROM branches AS b LEFT JOIN loan_portfolio AS lp ON b.branch_id = lp.branch_id "
                   "LEFT JOIN repayments AS r ON b.branch_id = r.branch_id")


def _guard(scoped):
    return SqlGuard(ALLOWED, "t2s", set(), "2026-06-30", scoped_columns=scoped)


# ---------------------------------------------------------------- options / flow
def test_new_options_default_off_and_hash_neutral():
    o = RunOptions()
    for k in NEW:
        assert getattr(o, k) is False and _LATE_DEFAULTS[k] is False
        assert k not in o.fingerprint()
    exp = load_experiments()
    for rid in ("M1a", "M1ks", "M1rr", "M1g8", "M1m", "I3t", "WEB"):
        fp = resolve_run(rid, exp).fingerprint()
        assert not set(NEW) & set(fp), rid
    on = resolve_run("M1a", exp, overrides=dict.fromkeys(NEW, True)).fingerprint()
    assert {k: v for k, v in on.items() if k not in NEW} == resolve_run("M1a", exp).fingerprint()


def test_flow_registration():
    assert BY_TYPE["input"]["fields"]["transpile_fix"] == {"kind": "bool"}
    assert BY_TYPE["writer"]["fields"]["guard_scope"] == {"kind": "bool"}
    assert BY_TYPE["writer"]["fields"]["extract_fallback"] == {"kind": "bool"}
    assert BY_TYPE["postproc"]["fields"]["literal_fix"] == {"kind": "bool"}


# ---------------------------------------------------------------- guard_scope
def test_guard_scope_cte_alias_reuse():
    old = _guard(False).check(CTE_ALIAS_REUSE)
    assert not old.ok and old.code == "unknown_column" and "lp.portfolio_mnt" in old.error   # хуучин буруу татгалзал
    new = _guard(True).check(CTE_ALIAS_REUSE)
    assert new.ok and new.tables == ["branches", "loan_payments", "loans"]


def test_guard_scope_still_rejects_real_unknown_columns():
    g = _guard(True)
    bad_in_cte = CTE_ALIAS_REUSE.replace("COUNT(lp.payment_id)", "COUNT(lp.portfolio_mnt)")   # lp = loan_payments энд
    r = g.check(bad_in_cte)
    assert not r.ok and r.code == "unknown_column" and "loan_payments" in r.error
    # correlated дэд асуулга гадна талын alias-ийг (loans AS l) ашиглана — гадна хүрээнээс тайлж шалгана
    corr = ("SELECT b.branch_name FROM branches AS b JOIN loans AS l ON l.branch_id = b.branch_id WHERE EXISTS "
            "(SELECT 1 FROM loan_payments AS p WHERE p.branch_id = l.branch_id AND l.no_such_col > 0)")
    assert _guard(False).check(corr).code == "unknown_column"
    r = g.check(corr)
    assert not r.ok and r.code == "unknown_column" and "no_such_col" in r.error
    # хүрээнд олдохгүй тодотгол (хүснэгтийн жинхэнэ нэр alias-тай үед) — хуучин глобал шалгалт хэвээр
    real_name = "SELECT loans.bogus FROM loans AS l"
    assert _guard(True).check(real_name).code == _guard(False).check(real_name).code == "unknown_column"


def test_guard_scope_same_result_on_simple_sql():
    sqls = ["SELECT l.loan_id, b.branch_name FROM loans AS l JOIN branches AS b ON b.branch_id = l.branch_id",
            "SELECT d.current_balance_mnt FROM (SELECT * FROM deposits) AS d",
            "WITH loans AS (SELECT 1 AS x) SELECT loans.x FROM loans",
            "SELECT b.bogus FROM branches AS b"]
    for s in sqls:
        a, b = _guard(False).check(s), _guard(True).check(s)
        assert (a.ok, a.code, a.sql) == (b.ok, b.code, b.sql), s


# ---------------------------------------------------------------- transpile_fix (F1–F5)
BOOL = frozenset({"is_npl", "str_reported", "is_active_portfolio"})
TYPES = column_types({"loans.is_npl": "boolean", "loans.disbursement_date": "date", "gl_entries.fiscal_year": "smallint",
                      "individuals.birth_year": "smallint", "loans.principal_outstanding_mnt": "numeric",
                      "branches.branch_name": "character varying", "aml_alerts.str_reported": "boolean",
                      "loan_applications.submitted_date": "date", "loan_applications.status_code": "character varying"})
KNOWN = frozenset(TYPES) | {"loan_id", "branch_id", "severity"}


def _fix(sql):
    return sqlite_to_postgres(sql, BOOL, fix=True, col_types=TYPES, known_cols=KNOWN)


def test_transpile_default_unchanged():
    sql = "SELECT x AS y FROM t GROUP BY x HAVING y > 1"
    assert sqlite_to_postgres(sql, BOOL) == "SELECT x AS y FROM t GROUP BY x HAVING y > 1"


def test_f1_having_alias_inlined():
    sql = ("SELECT b.branch_name, ROUND(100.0 * SUM(CASE WHEN l.is_npl THEN 1 ELSE 0 END) / COUNT(l.loan_id), 2) AS "
           "npl_ratio FROM loans l JOIN branches b ON l.branch_id = b.branch_id GROUP BY b.branch_name "
           "HAVING npl_ratio > 15 ORDER BY npl_ratio DESC")
    out = _fix(sql)
    assert "HAVING ROUND(" in out and "HAVING npl_ratio" not in out and "ORDER BY npl_ratio DESC" in out
    assert _fix("SELECT x + 1 AS y FROM t WHERE y > 3") == "SELECT x + 1 AS y FROM t WHERE (x + 1) > 3"
    # alias нь DB-ийн багана бол хөндөхгүй (SQLite ч баганыг түрүүлж таньдаг)
    assert _fix("SELECT a AS branch_name FROM t WHERE branch_name = 'x'").endswith("WHERE branch_name = 'x'")
    # WHERE-д агрегат alias оруулахгүй
    assert "WHERE n > 1" in _fix("SELECT COUNT(*) AS n FROM t WHERE n > 1")
    # дэд асуулгын гаралтын нэртэй давхцвал хөндөхгүй
    assert "HAVING total > 1" in _fix("SELECT s.k, SUM(s.v) AS total FROM (SELECT k, v AS total FROM t) AS s "
                                      "GROUP BY s.k HAVING total > 1")


def test_f2_strftime_substr_numeric_context():
    out = _fix("SELECT (strftime('%m', submitted_date) - 1) / 3 + 1 AS q FROM loan_applications")
    assert "(CAST(TO_CHAR(CAST(submitted_date AS TIMESTAMP), 'MM') AS INT) - 1)" in out
    assert "fiscal_year = CAST(TO_CHAR(" in _fix("SELECT 1 FROM gl_entries WHERE fiscal_year = strftime('%Y', 'now')")
    out = _fix("SELECT 1 FROM gl WHERE strftime('%Y', d) = strftime('%Y', date('now')) - 1")
    assert out.count("AS INT)") == 2
    # текст харьцуулалт / текст формат хэвээр
    for sql in ("SELECT 1 FROM t WHERE strftime('%Y', d) = '2025'", "SELECT strftime('%Y-%m', d) || 'x' FROM t",
                "SELECT 1 FROM t WHERE strftime('%m', d) IN ('01', '02')"):
        assert _fix(sql) == sqlite_to_postgres(sql, BOOL), sql


def test_f3_substr_on_date_column():
    out = _fix("SELECT CAST((SUBSTR(disbursement_date, 6, 2) - 1) / 3 + 1 AS INTEGER) FROM loans")
    assert "CAST(SUBSTRING(CAST(disbursement_date AS TEXT) FROM 6 FOR 2) AS INT) - 1" in out
    assert "SUBSTRING(branch_name FROM 1 FOR 2)" in _fix("SELECT SUBSTR(branch_name, 1, 2) FROM branches")


def test_f4_boolean_aggregates():
    out = _fix("SELECT severity, ROUND(AVG(str_reported) * 100, 2) FROM aml_alerts GROUP BY severity")
    assert "AVG(CAST(str_reported AS INT))" in out
    out = _fix("SELECT MAX(is_npl), MIN(is_npl), SUM(status = 'X'), is_npl * 2 FROM loans")
    assert "BOOL_OR(is_npl)" in out and "BOOL_AND(is_npl)" in out
    assert "SUM(CAST(status = 'X' AS INT))" in out and "CAST(is_npl AS INT) * 2" in out
    assert "SUM(CASE WHEN is_npl THEN 1 ELSE 0 END)" in _fix("SELECT SUM(CASE WHEN is_npl THEN 1 ELSE 0 END) FROM loans")


def test_f5_strftime_modifiers():
    out = _fix("SELECT strftime('%Y-%m', due_date, '+1 month', '-1 day') FROM s")
    assert out == "SELECT TO_CHAR(((CAST(due_date AS TIMESTAMP) + INTERVAL '1 MONTH') - INTERVAL '1 DAY'), 'YYYY-MM') FROM s"
    bad = "SELECT strftime('%Y-%m', d, 'start of quarter') FROM t"            # SQLite-д хүчингүй modifier — хэвээр
    assert _fix(bad) == sqlite_to_postgres(bad, BOOL)


def test_fix_only_touches_sql_that_fails_in_postgres():
    ok_sqls = ["SELECT COUNT(*) FROM loans WHERE is_active_portfolio = 1 AND disbursement_date >= date('now', '-30 days')",
               "SELECT strftime('%Y', disbursement_date) AS y, COUNT(*) FROM loans GROUP BY y ORDER BY y",
               "SELECT b.branch_name, SUM(l.principal_outstanding_mnt) AS s FROM loans l JOIN branches b "
               "ON b.branch_id = l.branch_id GROUP BY b.branch_name HAVING SUM(l.principal_outstanding_mnt) > 0"]
    for s in ok_sqls:
        assert _fix(s) == sqlite_to_postgres(s, BOOL), s


# ---------------------------------------------------------------- extract_fallback
LOOPED = ("<think>x</think>\n```sql\nSELECT a\nFROM t\nJOIN u ON t.id = u.id\nWHERE u.k = 1;\n```\n\nThus:\n"
          "```sql\nSELECT a\nFROM t\nJOIN u ON t.id = u.id\nWHERE u.k = 1;\n```\n\nFinally:\n```sql\nSELECT a\n"
          "FROM t\nJOIN u ON t.id = u\n```")


def test_extended_block():
    last = extract_sql(LOOPED)
    assert last.endswith("t.id = u")
    assert extended_block(LOOPED, last) == "SELECT a\nFROM t\nJOIN u ON t.id = u.id\nWHERE u.k = 1;"
    assert extended_block(LOOPED, "SELECT b FROM t") is None
    complete = "```sql\nSELECT a FROM t WHERE k = 1 AND j = 2\n```\nshorter:\n```sql\nSELECT a FROM t WHERE k = 1\n```"
    assert extended_block(complete, extract_sql(complete)).endswith("j = 2")    # дуудагч зөвхөн алдаатай үед ашиглана
    assert extended_block("", "x") is None and extended_block("```sql\nSELECT 1\n```", None) is None


class _Exec:
    def run(self, sql, max_rows=None, explain=False):
        if "WHERE" not in sql:
            return ExecResult(False, error='ERROR:  column "u" does not exist', error_code="db_error")
        return ExecResult(True, ["a"], [(1,)], 1)


class _Guard:
    def check(self, sql, rewrite=True):
        return GuardResult(True, sql, tables=["t"])


class _LLM:
    def __init__(self, text):
        self.text = text

    def chat(self, messages, *, n=1, temperature=0.0, top_p=1.0, max_tokens=2048, stop=None, seed=None, prefill=None):
        return LLMResponse([self.text] * n, 10, 5, 1.0, "mock", {"choices": [{"finish_reason": "stop"}] * n})


def _pipe(text, **kw):
    p = orch.Pipeline.__new__(orch.Pipeline)
    p.opts = RunOptions(**kw)
    p.llm, p.family, p.max_tokens = _LLM(text), "omnisql", 256
    p.mspec = {"sampling": {"temperature": 0.8}}
    p.executor, p.guard = _Exec(), _Guard()
    return p


def test_extract_fallback_in_pipeline():
    msgs = ([{"role": "user", "content": "q"}], None)
    off = _pipe(LOOPED).generate(None, 0, prompt=msgs)[0]
    assert not off.ok and off.error_stage == "exec"
    on = _pipe(LOOPED, extract_fallback=True).generate(None, 0, prompt=msgs)[0]
    assert on.ok and on.sql.endswith("WHERE u.k = 1") and on.text == LOOPED


# ---------------------------------------------------------------- literal_fix (postproc R6)
class _Res:
    def __init__(self, rows):
        self.ok, self.rows = True, rows


class _PPExec:
    VALS = {"branch_name": ["Дархан салбар", "Эрдэнэт салбар", "Баянзүрх салбар"],
            "account_type_name": ["Харилцах данс", "Хадгаламжийн данс"]}

    def run(self, sql, max_rows=None):
        if "information_schema.columns" in sql:
            return _Res([("t2s", "branches", "branch_name", "text"), ("t2s", "branches", "branch_id", "bigint"),
                         ("t2s", "accounts", "account_type_name", "text")])
        for c, vals in self.VALS.items():
            if f'count(DISTINCT "{c}")' in sql:
                return _Res([(len(vals),)])
            if f'SELECT DISTINCT "{c}"' in sql:
                return _Res([(v,) for v in vals])
        return _Res([])


HITS = [("branches", "branch_name", "Дархан салбар", 0.963), ("branches", "district", "Дархан", 1.0)]


def test_r6_maps_untranslated_literal_to_retrieved_value():
    pp = SqlPostprocessor(_PPExec())
    sql = "SELECT COUNT(*) FROM branches AS b WHERE b.branch_name = 'Darkhan'"
    assert pp.apply(sql) == (sql, [])                                   # hits-гүй (анхдагч) — хуучин зан төлөв
    out, rules = pp.apply(sql, hits=HITS)
    assert rules == ["R6"] and "b.branch_name = 'Дархан салбар'" in out
    assert pp.apply("SELECT 1 FROM branches WHERE branch_name = 'Дархан салбар'", hits=HITS)[1] == []   # хүчинтэй утга


def test_r6_ambiguity_guards():
    pp = SqlPostprocessor(_PPExec())
    tie = [("accounts", "account_type_name", "Харилцах данс", 0.9), ("accounts", "account_type_name", "Хадгаламжийн данс", 0.9)]
    one = "SELECT 1 FROM accounts WHERE account_type_name = 'Current Account'"
    assert pp.apply(one, hits=tie)[1] == []                             # тэнцүү оноо — сонгохгүй
    top = [(t, c, v, s + (0.05 if i == 0 else 0)) for i, (t, c, v, s) in enumerate(tie)]
    assert "'Харилцах данс'" in pp.apply(one, hits=top)[0]
    in_list = "SELECT 1 FROM accounts WHERE account_type_name IN ('Current Account', 'Savings Account')"
    out, rules = pp.apply(in_list, hits=tie)                           # k буруу ↔ k олдвор — олонлогоор
    assert rules == ["R6"] and "'Харилцах данс'" in out and "'Хадгаламжийн данс'" in out
    two_preds = ("SELECT SUM(CASE WHEN account_type_name = 'Current Account' THEN 1 END), SUM(CASE WHEN "
                 "account_type_name = 'Savings Account' THEN 1 END) FROM accounts")
    assert pp.apply(two_preds, hits=top)[1] == []                      # аль нь аль болохыг мэдэхгүй
    assert pp.apply("SELECT 1 FROM branches WHERE branch_name = 'Darkhan'", hits=[])[1] == []


def test_r6_hits_reach_postproc_only_with_option():
    seen = []

    class _PP:
        def apply(self, sql, hits=None):
            seen.append(hits)
            return sql, []

    p = _pipe("")
    p.opts = RunOptions(postproc=True, dialect="sqlite_native")
    p._postproc = _PP()
    p.process_sql("SELECT 1 WHERE x", hits=HITS)
    p.opts = RunOptions(postproc=True, dialect="sqlite_native", literal_fix=True)
    p.process_sql("SELECT 1 WHERE x", hits=HITS)
    p.process_sql("SELECT 1 WHERE x")
    assert seen == [None, HITS, None]
