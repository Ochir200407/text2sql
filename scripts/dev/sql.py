"""Bot role-оор (t2s_bot, зөвхөн t2s view) SQL ажиллуулж үр дүнг хэвлэнэ — eval set бичихэд утга шалгах туслах.

Хэрэглээ:
  PYTHONPATH=src python scripts/dev/sql.py "SELECT branch_name FROM t2s.branches"
  PYTHONPATH=src python scripts/dev/sql.py --max 50 "SELECT ..."
Орчны хувьсагч: T2S_ADMIN_DSN (жишээ: postgresql://postgres@localhost:55432/postgres), T2S_DB_NAME
"""

from __future__ import annotations

import argparse
import sys

from mn_text2sql.config import load_config
from mn_text2sql.db.conn import bot_dsn
from mn_text2sql.db.executor import Executor
from mn_text2sql.eval.goldcheck import make_guard


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("sql")
    ap.add_argument("--max", type=int, default=30)
    ap.add_argument("--no-guard", action="store_true")
    args = ap.parse_args()
    cfg = load_config()
    sql = args.sql
    if not args.no_guard:
        g = make_guard(cfg).check(sql)
        if not g.ok:
            print("GUARD:", g.error)
            return 2
        sql = g.sql
    ex = Executor(bot_dsn(cfg), statement_timeout_ms=30000, max_rows=100000)
    res = ex.run(sql)
    ex.close()
    if not res.ok:
        print("ERROR:", res.error)
        return 1
    print(" | ".join(res.columns))
    for row in res.rows[: args.max]:
        print(" | ".join("NULL" if v is None else str(v) for v in row))
    print(f"-- {res.rowcount} мөр, {res.elapsed_ms:.0f} ms")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
