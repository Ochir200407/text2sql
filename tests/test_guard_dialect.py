import pytest

from mn_text2sql.pipeline.dialect import sqlite_to_postgres
from mn_text2sql.security.sql_guard import SqlGuard, extract_sql

ALLOWED = {"loans": {"loan_id", "branch_id", "days_past_due", "disbursement_date", "principal_outstanding_mnt"},
           "branches": {"branch_id", "branch_name"}}


@pytest.fixture()
def guard():
    return SqlGuard(ALLOWED, "t2s", None, "2026-06-30")


@pytest.mark.parametrize("sql", [
    "DROP TABLE t2s.loans", "DELETE FROM loans", "UPDATE loans SET days_past_due = 0", "SELECT 1; SELECT 2",
    "SELECT pg_sleep(5)", "SELECT * FROM core.customers", "SELECT * INTO x FROM loans", "SELECT * FROM loans FOR UPDATE",
    "SELECT set_config('a','b',false)", "SELECT version()", "SELECT lo_import('/etc/passwd')",
    "SELECT * FROM pg_catalog.pg_authid", "COPY loans TO '/tmp/x'", "SELECT l.nope FROM loans l",
])
def test_blocked(guard, sql):
    assert not guard.check(sql).ok


def test_rewrites_dates(guard):
    r = guard.check("SELECT count(*) FROM loans WHERE disbursement_date >= CURRENT_DATE - INTERVAL '30 days' "
                    "AND disbursement_date < now() AND disbursement_date <> 'today'::date")
    assert r.ok
    assert "2026-06-30" in r.sql and "CURRENT_DATE" not in r.sql.upper() and "NOW()" not in r.sql.upper()
    assert "'today'" not in r.sql


def test_enforce_limit():
    g = SqlGuard(ALLOWED, "t2s", None, "2026-06-30", enforce_limit=100)
    assert "LIMIT 100" in g.check("SELECT loan_id FROM loans").sql
    assert "LIMIT 5" in g.check("SELECT loan_id FROM loans LIMIT 5").sql
    assert "LIMIT 100" in g.check("SELECT loan_id FROM loans LIMIT 5000").sql


# 2-р аудит S4: FETCH FIRST n-ийг уншиж min(n, хязгаар); WITH TIES/NEXT/OFFSET хэвээр (өмнө нь LIMIT 100 болдог байв)
@pytest.mark.parametrize("sql,tail", [
    ("SELECT loan_id FROM loans ORDER BY loan_id FETCH FIRST 5 ROWS ONLY", "ORDER BY loan_id FETCH FIRST 5 ROWS ONLY"),
    ("SELECT loan_id FROM loans ORDER BY days_past_due DESC FETCH FIRST 5 ROWS WITH TIES",
     "FETCH FIRST 5 ROWS WITH TIES"),
    ("SELECT loan_id FROM loans ORDER BY days_past_due FETCH FIRST 5000 ROWS WITH TIES", "FETCH FIRST 100 ROWS WITH TIES"),
    ("SELECT loan_id FROM loans ORDER BY loan_id FETCH FIRST 100 ROWS ONLY", "FETCH FIRST 100 ROWS ONLY"),
    ("SELECT loan_id FROM loans ORDER BY loan_id OFFSET 10 ROWS FETCH NEXT 500 ROWS ONLY",
     "OFFSET 10 FETCH NEXT 100 ROWS ONLY"),
    ("SELECT loan_id FROM loans ORDER BY loan_id FETCH FIRST ROW ONLY", "FETCH FIRST ROWS ONLY"),   # n байхгүй = 1
    # n тоо биш → дахин бичихгүй (мөрийн тоог Executor-ийн max_rows хязгаарлана)
    ("SELECT loan_id FROM loans ORDER BY loan_id FETCH FIRST (2 + 3) ROWS ONLY", "FETCH FIRST (2 + 3) ROWS ONLY"),
    ("SELECT loan_id FROM loans UNION SELECT branch_id FROM branches ORDER BY 1 FETCH FIRST 7 ROWS ONLY",
     "ORDER BY 1 FETCH FIRST 7 ROWS ONLY"),
])
def test_enforce_limit_reads_fetch(sql, tail):
    r = SqlGuard(ALLOWED, "t2s", None, "2026-06-30", enforce_limit=100).check(sql)
    assert r.ok and r.sql.endswith(tail) and "LIMIT" not in r.sql, r.sql


def test_fetch_untouched_without_enforce_limit():
    """Үнэлгээ (eval.goldcheck.make_guard, Pipeline-ийн анхдагч guard) enforce_limit=None — FETCH-ийг хөндөхгүй."""
    import sqlglot
    sql = "SELECT loan_id FROM loans ORDER BY days_past_due FETCH FIRST 5000 ROWS WITH TIES"
    r = SqlGuard(ALLOWED, "t2s", None, "2026-06-30").check(sql)
    assert r.ok and r.sql == sqlglot.parse_one(sql, read="postgres").sql(dialect="postgres")


# 2-р аудит S3: XML mapping функц (хүснэгтийг текстээр авч allow-list-ийг тойрно, бүтэн хүснэгт/DB нэг нүдэнд)
@pytest.mark.parametrize("fn", sorted({f"{s}_to_{k}" for s in ("table", "query", "cursor", "schema", "database")
                                       for k in ("xml", "xmlschema", "xml_and_xmlschema")}))
def test_xml_mapping_functions_blocked(guard, fn):
    r = guard.check(f"SELECT {fn}('t2s.loans', true, false, '')")
    assert not r.ok and r.code == "forbidden_function" and fn in r.error


@pytest.mark.parametrize("sql", [
    "SELECT * FROM table_to_xml('t2s.loans', true, false, '') AS x",
    "SELECT loan_id FROM loans WHERE loan_id::text = (SELECT table_to_xml('t2s.loans', true, false, '')::text)",
    "SELECT \"table_to_xml\"('t2s.loans', true, false, '')", "SELECT TABLE_TO_XML('t2s.loans', true, false, '')",
    "SELECT pg_catalog.schema_to_xml('t2s', true, false, '')", "SELECT database_to_xml(true, false, '')",
    "SELECT cursor_to_xml('c'::refcursor, 10, true, false, '')",
])
def test_xml_mapping_blocked_anywhere(guard, sql):
    assert not guard.check(sql).ok


def test_aggregates_still_allowed(guard):
    """json_agg/string_agg хэвээр зөвшөөрөгдөнө — аварга нүдийг үйлчилгээ хэмжээгээр тайрна (analyst.cap_rows)."""
    assert guard.check("SELECT string_agg(branch_name, ', ') FROM branches").ok
    assert guard.check("SELECT json_agg(b) FROM branches b").ok


def test_extract_sql():
    assert extract_sql("x</think>\n<answer>\n```sql\nSELECT 1\n```\n</answer>") == "SELECT 1"
    assert extract_sql("Here:\n```sql\nSELECT 2\n```") == "SELECT 2"
    assert extract_sql("no sql here") is None


def test_sqlite_dates_to_postgres():
    out = sqlite_to_postgres("SELECT COUNT(*) FROM loans WHERE disbursement_date >= date('now','-30 days')")
    assert "CURRENT_DATE" in out and "INTERVAL '30 DAY'" in out.upper().replace("DAYS", "DAY")
    assert "DATE_TRUNC('MONTH'" in sqlite_to_postgres("SELECT date('now','start of month')").upper()
    assert "EXTRACT(EPOCH" in sqlite_to_postgres("SELECT julianday('now') - julianday(d) FROM t").upper()
    assert sqlite_to_postgres("SELECT CAST('now' AS DATE)") == "SELECT CURRENT_DATE"


# 3-р аудит (guard M1): SQL-ийг текстээр авч гүйцэтгэдэг функц — дотор нь бичсэн асуулга allow-list-ийг тойрно
@pytest.mark.parametrize("sql", [
    "SELECT * FROM ts_stat('SELECT to_tsvector(rolname) FROM pg_authid')",
    "SELECT word FROM ts_stat($$SELECT to_tsvector(branch_name) FROM t2s.branches$$)",
    "SELECT TS_STAT('SELECT 1')", "SELECT \"ts_stat\"('SELECT 1')",
    "SELECT ts_rewrite('a'::tsquery, 'SELECT a, b FROM pg_authid')",
    "SELECT * FROM crosstab('SELECT 1, 2, 3') AS ct(a int, b int)",
    "SELECT * FROM crosstab3('SELECT 1, 2, 3')", "SELECT * FROM crosstab_hash('SELECT 1', 'SELECT 2') AS t(a int)",
    "SELECT * FROM connectby('t2s.loans', 'loan_id', 'branch_id', '1', 0) AS t(a int, b int, c int)",
    "SELECT * FROM xpath_table('id', 'doc', 'pg_authid', '/a', 'true') AS t(id int)",
    "SELECT loan_id FROM loans WHERE loan_id IN (SELECT ndoc FROM ts_stat('SELECT 1'))",
])
def test_query_string_functions_blocked(guard, sql):
    r = guard.check(sql)
    assert not r.ok and r.code in ("forbidden_function", "forbidden_table"), (sql, r.code)


def test_text_search_functions_still_allowed(guard):
    """ts_stat/ts_rewrite-ээс бусад бүрэн текст хайлтын функц (to_tsvector, plainto_tsquery) хэвээр зөвшөөрөгдөнө."""
    assert guard.check("SELECT branch_name FROM branches WHERE to_tsvector(branch_name) @@ plainto_tsquery('x')").ok
