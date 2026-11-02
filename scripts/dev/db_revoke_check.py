"""DB түвшний хамгаалалт (db_t2s/110_roles_rls.sql-ийн REVOKE): bot role-оор guard-гүйгээр шууд дуудахад SQL-ийг текстээр
гүйцэтгэдэг (ts_stat, ts_rewrite) ба хүснэгт/асуулгыг нэг нүдэнд буцаадаг (*_to_xml) функц, pg_sleep «permission denied»
өгөх ёстой; хяналтын энгийн SELECT амжилттай. Мөн вэбийн server-side cursor-ийн замыг (bounded_checks) бодит DB дээр
шалгана (REVIEW §4: өмнө нь зөвхөн хуурамч холболтоор). DB шаардлагатай (Kaggle: run_plan_l4.sh-ийн `secdb` алхам).

    python scripts/dev/db_revoke_check.py > results/db_revoke_check.json
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from mn_text2sql.config import load_config  # noqa: E402
from mn_text2sql.db.conn import bot_dsn  # noqa: E402
from mn_text2sql.db.executor import Executor  # noqa: E402

# (нэр, SQL, хориотой эсэх) — хориотойд «permission denied» хүлээнэ
CHECKS = [
    ("control", "SELECT count(*) FROM t2s.branches", False),
    ("ts_stat", "SELECT * FROM ts_stat('SELECT to_tsvector(''simple'', ''a'')')", True),
    ("ts_rewrite", "SELECT ts_rewrite('a'::tsquery, 'SELECT ''a''::tsquery, ''b''::tsquery')", True),
    ("table_to_xml", "SELECT table_to_xml('t2s.branches', true, false, '')", True),
    ("query_to_xml", "SELECT query_to_xml('SELECT 1', true, false, '')", True),
    ("database_to_xml", "SELECT database_to_xml(true, false, '')", True),
    ("pg_sleep", "SELECT pg_sleep(0)", True),
]


def bounded_checks(cfg) -> list[dict]:
    """Вэбийн server-side cursor-ийн зам (Executor.max_result_bytes → _run_bounded, нэрлэсэн cursor) бодит DB дээр:
    энгийн замтай ижил мөр, санах ойн төсөв хэтэрвэл too_large, бичих үйлдэл READ ONLY транзакцад хориотой."""
    dsn, to = bot_dsn(cfg), cfg["db"]["statement_timeout_ms"]
    plain = Executor(dsn, statement_timeout_ms=to, max_rows=1000)
    big = Executor(dsn, statement_timeout_ms=to, max_rows=1000, max_result_bytes=5_000_000, fetch_rows=50)
    tiny = Executor(dsn, statement_timeout_ms=to, max_rows=1000, max_result_bytes=2_000, fetch_rows=50)
    out = []
    try:
        sql = "SELECT branch_id, branch_name FROM t2s.branches ORDER BY branch_id"
        a, b = plain.run(sql), big.run(sql)
        out.append({"check": "bounded_same_rows", "forbidden": False,
                    "ok": a.ok and b.ok and a.columns == b.columns and a.rows == b.rows and a.rowcount > 0,
                    "error": (a.error or b.error or "")[:160] or None})
        c = tiny.run("SELECT * FROM t2s.loans")
        out.append({"check": "bounded_too_large", "forbidden": False, "ok": not c.ok and c.error_code == "too_large",
                    "error": (c.error or "")[:160] or None})
        w = plain.run("CREATE TEMP TABLE t2s_smoke AS SELECT 1 AS x")
        w2 = big.run("CREATE TEMP TABLE t2s_smoke AS SELECT 1 AS x")
        out.append({"check": "write_blocked", "forbidden": False,
                    "ok": not w.ok and "read-only" in (w.error or "").lower() and not w2.ok,
                    "error": (w.error or "")[:160] or None})
    finally:
        for e in (plain, big, tiny):
            e.close()
    return out


def main() -> int:
    cfg = load_config()
    ex = Executor(bot_dsn(cfg), statement_timeout_ms=cfg["db"]["statement_timeout_ms"], max_rows=10)
    rows = []
    for name, sql, forbidden in CHECKS:
        res = ex.run(sql)
        err = (res.error or "") if not res.ok else ""
        ok = ("permission denied" in err.lower()) if forbidden else res.ok
        rows.append({"check": name, "forbidden": forbidden, "ok": ok, "error": err[:160] or None})
    ex.close()
    rows += bounded_checks(cfg)
    for r in rows:
        print(json.dumps(r, ensure_ascii=False))
    print(json.dumps({"summary": {"checked": len(rows), "failed": [r["check"] for r in rows if not r["ok"]]}},
                     ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
