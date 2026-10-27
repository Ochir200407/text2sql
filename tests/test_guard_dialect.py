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
