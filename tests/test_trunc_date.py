"""trunc_date (postproc R7, V27 dev-ийн алдааны шинжилгээ; анхдагч off — хуучин гаралт, fingerprint ижил):
гаралтын DATE_TRUNC('month', x) → CAST(… AS DATE). DB, загваргүй — хуурамч executor/guard/postproc."""
from mn_text2sql.db.executor import ExecResult
from mn_text2sql.pipeline import orchestrator as orch
from mn_text2sql.pipeline.options import _LATE_DEFAULTS, RunOptions, load_experiments, resolve_run
from mn_text2sql.pipeline.postproc import cast_date_trunc
from mn_text2sql.security.sql_guard import GuardResult
from mn_text2sql.service.flow import BY_TYPE


# ---------------------------------------------------------------- options / flow
def test_default_off_and_hash_neutral():
    assert RunOptions().trunc_date is False and _LATE_DEFAULTS["trunc_date"] is False
    exp = load_experiments()
    for rid in exp["runs"]:
        opts = resolve_run(rid, exp)
        if opts.trunc_date:   # тодорхой асаасан run (WEB2, 2026-11-02) — fingerprint-д орох ёстой
            assert opts.fingerprint()["trunc_date"] is True, rid
            continue
        assert "trunc_date" not in opts.fingerprint(), rid
    on = resolve_run("M1afx", exp, overrides={"trunc_date": True}).fingerprint()
    assert on.pop("trunc_date") is True and on == resolve_run("M1afx", exp).fingerprint()


def test_flow_registration():
    assert BY_TYPE["postproc"]["fields"]["trunc_date"] == {"kind": "bool"}


# ---------------------------------------------------------------- cast_date_trunc
def test_projection_cast_keeps_group_where_order():
    # dev K036-ийн бүтэц: гаралт, GROUP BY, ORDER BY-д ижил DATE_TRUNC
    sql = ("SELECT DATE_TRUNC('MONTH', promised_date) AS month, COUNT(*) AS n FROM collection_promises "
           "WHERE promised_date >= DATE_TRUNC('MONTH', CAST('2026-06-30' AS DATE)) "
           "GROUP BY DATE_TRUNC('MONTH', promised_date) ORDER BY DATE_TRUNC('MONTH', promised_date)")
    out, rules = cast_date_trunc(sql)
    assert rules == ["R7"]
    assert out.startswith("SELECT CAST(DATE_TRUNC('MONTH', promised_date) AS DATE) AS month, COUNT(*) AS n")
    assert "WHERE promised_date >= DATE_TRUNC('MONTH', CAST('2026-06-30' AS DATE))" in out
    assert "GROUP BY DATE_TRUNC('MONTH', promised_date) ORDER BY DATE_TRUNC('MONTH', promised_date)" in out


def test_cte_and_unaliased_projection():
    # dev K040/P040-ийн бүтэц: CTE-ийн гаралт → гадна LAG ба огноотой харьцуулалт
    sql = ("WITH m AS (SELECT DATE_TRUNC('month', opened_date) AS month, COUNT(*) AS c FROM collection_cases "
           "GROUP BY DATE_TRUNC('month', opened_date)) SELECT month, c, c - LAG(c) OVER (ORDER BY month) AS d "
           "FROM m WHERE month >= CAST('2026-01-01' AS DATE)")
    out, rules = cast_date_trunc(sql)
    assert rules == ["R7"] and "SELECT CAST(DATE_TRUNC('MONTH', opened_date) AS DATE) AS month" in out
    assert out.count("AS DATE) AS month") == 1 and "WHERE month >= CAST('2026-01-01' AS DATE)" in out
    out, rules = cast_date_trunc("SELECT DATE_TRUNC('year', d), SUM(x) FROM t GROUP BY 1")
    assert rules == ["R7"] and out == "SELECT CAST(DATE_TRUNC('YEAR', d) AS DATE), SUM(x) FROM t GROUP BY 1"


def test_untouched_cases():
    same = ["SELECT COUNT(*) FROM loans",                                               # DATE_TRUNC алга
            "SELECT DATE_TRUNC('hour', created_at) AS h FROM transactions",              # өдрөөс нарийн нэгж
            "SELECT CAST(DATE_TRUNC('month', d) AS DATE) AS m FROM t",                   # аль хэдийн DATE
            "SELECT TO_CHAR(DATE_TRUNC('month', d), 'YYYY-MM') AS m FROM t",             # гаралт нь текст
            "SELECT x FROM t WHERE d >= DATE_TRUNC('month', CAST('2026-06-30' AS DATE))",  # зөвхөн WHERE-д
            "SELECT DISTINCT DATE_TRUNC('month', d) AS m FROM t ORDER BY DATE_TRUNC('month', d)",  # DISTINCT
            "SELECT DATE_TRUNC('month', d) AS m FROM t UNION SELECT DATE_TRUNC('month', e) FROM u",  # UNION
            "SELECT DATE_TRUNC('month', d FROM",                                         # задлагдахгүй
            ""]
    for s in same:
        assert cast_date_trunc(s) == (s, []), s


# ---------------------------------------------------------------- pipeline plumbing
class _Exec:
    def run(self, sql, max_rows=None, explain=False):
        return ExecResult(True, ["m"], [("2026-01-01",)], 1)


class _Guard:
    def __init__(self):
        self.seen = []

    def check(self, sql, rewrite=True):
        self.seen.append(sql)
        return GuardResult(True, sql, tables=["t"])


class _PP:
    def __init__(self):
        self.calls = []

    def apply(self, sql, hits=None):
        self.calls.append(hits)
        return sql, []


def _pipe(**kw):
    p = orch.Pipeline.__new__(orch.Pipeline)
    p.opts = RunOptions(dialect="sqlite_native", **kw)
    p.executor, p.guard, p._postproc = _Exec(), _Guard(), _PP()
    return p


SQL = "SELECT DATE_TRUNC('month', d) AS m, COUNT(*) FROM t GROUP BY DATE_TRUNC('month', d)"


def test_process_sql_only_with_option_and_postproc():
    off = _pipe(postproc=True)
    assert off.process_sql(SQL).sql == SQL and off.guard.seen == [SQL]            # анхдагч: хуучин SQL
    on = _pipe(postproc=True, trunc_date=True)
    c = on.process_sql(SQL)
    assert c.ok and c.sql.startswith("SELECT CAST(DATE_TRUNC('MONTH', d) AS DATE) AS m")
    assert on._postproc.calls == [None]                                           # R6-ийн дуудлага өөрчлөгдөөгүй
    no_pp = _pipe(postproc=False, trunc_date=True)                                # postproc node-гүй үед ажиллахгүй
    assert no_pp.process_sql(SQL).sql == SQL
