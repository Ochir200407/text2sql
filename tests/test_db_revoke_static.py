"""db_t2s/110_roles_rls.sql-ийн REVOKE-ийн статик шалгалт (PostgreSQL-гүй): SQL-ийг текстээр гүйцэтгэдэг ба *_to_xml*
функцүүд PUBLIC-ээс хураагдсан, PG-ийн хувилбарт байхгүй гарын үсгийг to_regprocedure-оор алгасдаг, guard-ын хоригтой
нийцтэй, scripts/dev/db_revoke_check.py-ийн хориотой шалгалт бүр REVOKE-д байгаа."""
import importlib.util
import re
from pathlib import Path

from mn_text2sql.security.sql_guard import FORBIDDEN_INFIXES, QUERY_STRING_FUNCS

ROOT = Path(__file__).resolve().parents[1]
SQL = (ROOT / "db_t2s" / "110_roles_rls.sql").read_text(encoding="utf-8")


def _revoked() -> set[str]:
    names = set(re.findall(r"pg_catalog\.(\w+)\(", SQL.split("REVOKE EXECUTE ON FUNCTION", 1)[1]))
    return names


def test_dollar_quotes_balanced_and_guarded():
    assert SQL.count("$$") % 2 == 0
    block = SQL.rsplit("DO $$", 1)[1]
    assert "to_regprocedure(f) IS NOT NULL" in block and "REVOKE EXECUTE ON FUNCTION %s FROM PUBLIC" in block
    assert block.count("BEGIN") == 1 and block.count("END LOOP;") == 1 and block.count("END IF;") == 1


def test_query_string_and_xml_functions_revoked():
    rev = _revoked()
    for f in ("ts_stat", "ts_rewrite", "query_to_xml", "table_to_xml", "schema_to_xml", "database_to_xml",
              "cursor_to_xml", "pg_sleep", "set_config"):
        assert f in rev, f
    assert {"ts_stat", "ts_rewrite"} <= QUERY_STRING_FUNCS and "_to_xml" in FORBIDDEN_INFIXES


def test_revoke_check_script_matches_sql():
    spec = importlib.util.spec_from_file_location("db_revoke_check_t", ROOT / "scripts" / "dev" / "db_revoke_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    rev = _revoked()
    forbidden = [name for name, _sql, bad in mod.CHECKS if bad]
    assert forbidden and all(name in rev for name in forbidden)
    assert any(not bad for _n, _s, bad in mod.CHECKS)          # хяналтын SELECT


def test_revoke_check_main_with_fake_executor(monkeypatch, capsys):
    """DB-гүй: хуурамч Executor-оор main() бүтэн ажиллаж, хүлээгдэх зан төлөвт failed = [] гарна."""
    import json

    from mn_text2sql.db.executor import ExecResult

    spec = importlib.util.spec_from_file_location("db_revoke_check_t2", ROOT / "scripts" / "dev" / "db_revoke_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    class FakeEx:
        def __init__(self, dsn, statement_timeout_ms=0, max_rows=0, max_result_bytes=None, fetch_rows=1):
            self.budget = max_result_bytes

        def run(self, sql):
            s = sql.lower()
            if s.startswith("create"):
                return ExecResult(False, error="cannot execute CREATE TABLE AS in a read-only transaction",
                                  error_code="db_error")
            if any(f in s for f in ("ts_stat", "ts_rewrite", "_to_xml", "pg_sleep")):
                return ExecResult(False, error="permission denied for function x", error_code="db_error")
            if self.budget and self.budget < 10_000 and "t2s.loans" in s:
                return ExecResult(False, error="too large", error_code="too_large")
            return ExecResult(True, ["a"], [(1,)], 1)

        def close(self):
            pass

    monkeypatch.setattr(mod, "Executor", FakeEx)
    monkeypatch.setattr(mod, "bot_dsn", lambda cfg: "")
    monkeypatch.setattr(mod, "load_config", lambda: {"db": {"statement_timeout_ms": 1000}})
    assert mod.main() == 0
    lines = [json.loads(x) for x in capsys.readouterr().out.splitlines() if x.strip()]
    assert lines[-1]["summary"]["failed"] == [] and lines[-1]["summary"]["checked"] == len(mod.CHECKS) + 3
