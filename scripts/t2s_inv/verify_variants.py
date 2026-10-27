"""S2 хувилбарын ГҮЙЦЭТГЭЛИЙН баталгаажуулалт: эх gold SQL (схем t2s) ба нэр сольсон SQL (схем t2s_<mapping>, view-ээр) нь PG дээр
ижил үр дүн өгч байгааг шалгана. Таарсан мөрийг л сургалтад оруулна.

⚠ Локалд PostgreSQL байхгүй тул энэ скриптийг ТУРШААГҮЙ (синтакс ба импортыг л шалгасан). Kaggle/Colab-д `bootstrap.sh` (PG + DB seed 42)
дараа нэг удаа ажиллуулж, эхний ажиллалтад `--limit 50`-аар шалга.

Ажиллуулах:
  PYTHONPATH=src python scripts/t2s_inv/verify_variants.py [--dir data/t2s_inv/v0] [--limit 0]
Гаралт: <dir>/schema_variants.verified.jsonl, <dir>/verify_report.json
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from psycopg2.extensions import make_dsn, parse_dsn

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from mn_text2sql.config import load_config  # noqa: E402
from mn_text2sql.db.conn import admin_dsn, connect  # noqa: E402
from mn_text2sql.db.executor import Executor  # noqa: E402
from mn_text2sql.eval.compare import strict_match  # noqa: E402


def with_search_path(dsn: str, path: str) -> str:
    params = parse_dsn(dsn)
    params["options"] = f"-c search_path={path}"
    return make_dsn(**params)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="data/t2s_inv/v0")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    d = ROOT / args.dir
    cfg = load_config()
    adm = admin_dsn(cfg)

    rows = [json.loads(x) for x in (d / "schema_variants.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    if args.limit:
        rows = rows[: args.limit]
    maps = sorted({r["mapping"] for r in rows})
    conn = connect(adm, autocommit=True)
    with conn.cursor() as cur:
        for m in maps:
            cur.execute((d / f"views_{m}.sql").read_text(encoding="utf-8"))
    conn.close()

    base = Executor(with_search_path(adm, "t2s"), statement_timeout_ms=60000, max_rows=20000)
    per_map = {m: Executor(with_search_path(adm, f"t2s_{m}"), statement_timeout_ms=60000, max_rows=20000) for m in maps}
    gold_cache: dict[str, object] = {}
    stats: Counter = Counter()
    fails: list[dict] = []
    ok_rows = []
    for r in rows:
        stats["total"] += 1
        g = gold_cache.get(r["sql_orig"])
        if g is None:
            g = gold_cache[r["sql_orig"]] = base.run(r["sql_orig"])
        if not g.ok:
            stats["orig_exec_error"] += 1
            continue
        if not g.rows:      # хоосон үр дүн: аль ч SQL «таарна» — баталгаа болохгүй
            stats["orig_empty"] += 1
            continue
        p = per_map[r["mapping"]].run(r["sql"])
        if not p.ok:
            stats["new_exec_error"] += 1
            stats[f"new_exec_error:{r['style']}"] += 1
            fails.append({"id": r["id"], "style": r["style"], "error": (p.error or "")[:200]})
            continue
        if strict_match(g.columns, g.rows, p.columns, p.rows, bool(r.get("order_matters"))):
            stats["verified"] += 1
            stats[f"verified:{r['style']}"] += 1
            ok_rows.append(r)
        else:
            stats["mismatch"] += 1
            stats[f"mismatch:{r['style']}"] += 1
            fails.append({"id": r["id"], "style": r["style"], "error": "үр дүн таарсангүй"})
    with open(d / "schema_variants.verified.jsonl", "w", encoding="utf-8", newline="\n") as fh:
        for r in ok_rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    report = {"stats": dict(stats), "failures_sample": fails[:50]}
    (d / "verify_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report["stats"], ensure_ascii=False, indent=1))
    base.close()
    for ex in per_map.values():
        ex.close()


if __name__ == "__main__":
    main()
