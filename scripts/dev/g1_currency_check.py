"""G1: gold SQL нь USD ба MNT дүнг хөрвүүлэлтгүй нэмж байна уу (docs/review/FULL_REVIEW_2026-10-30.md §3 «Нээлттэй»).

Зээлийн валютаар (BIZ_USD бүтээгдэхүүн) хадгалагддаг view-үүдийг (loan_writeoffs, loan_charges, loan_guarantors,
loan_collaterals, collaterals, collection_cases, collection_actions, collection_promises, loans) зөвхөн MNT мөрөөр
шүүсэн дэд асуулгаар сольж (sqlglot), gold-ийг хоёр удаа ажиллуулж үр дүнг харьцуулна. Ижил бол USD мөр тухайн gold-д
нөлөөлөхгүй; ялгаатай бол gold валют хольж байна. DB шаардлагатай (Kaggle: run_plan_l4.sh-ийн `g1` алхам).

    python scripts/dev/g1_currency_check.py [--ids K010,P009,...] > results/g1_currency.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import sqlglot
from sqlglot import exp

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from mn_text2sql.config import load_config  # noqa: E402
from mn_text2sql.db.conn import bot_dsn  # noqa: E402
from mn_text2sql.db.executor import Executor  # noqa: E402
from mn_text2sql.eval.compare import result_hash  # noqa: E402

# FULL_REVIEW G1: мөнгөн баганыг валютгүй нэмдэг gold (dev 9, test 4)
DEFAULT_IDS = {"dev": ["K010", "P009", "K018", "K029", "K038", "P008", "P031", "P032", "P033"],
               "test": ["X028", "P010", "K007", "K047"]}

# view → зөвхөн MNT мөрийн дэд асуулга (view-ийн бүх багана хэвээр)
MNT_ONLY = {
    "loans": "SELECT * FROM t2s.loans WHERE currency_code = 'MNT'",
    "loan_writeoffs": "SELECT w.* FROM t2s.loan_writeoffs w JOIN t2s.loans l ON l.loan_id = w.loan_id"
                      " WHERE l.currency_code = 'MNT'",
    "loan_charges": "SELECT * FROM t2s.loan_charges WHERE currency_code = 'MNT'",
    "loan_guarantors": "SELECT * FROM t2s.loan_guarantors WHERE currency_code = 'MNT'",
    "loan_collaterals": "SELECT * FROM t2s.loan_collaterals WHERE currency_code = 'MNT'",
    "collaterals": "SELECT * FROM t2s.collaterals WHERE currency_code = 'MNT'",
    "collection_cases": "SELECT * FROM t2s.collection_cases WHERE currency_code = 'MNT'",
    "collection_actions": "SELECT a.* FROM t2s.collection_actions a JOIN t2s.collection_cases c"
                          " ON c.case_id = a.case_id WHERE c.currency_code = 'MNT'",
    "collection_promises": "SELECT * FROM t2s.collection_promises WHERE currency_code = 'MNT'",
}


def mnt_only(sql: str) -> tuple[str, list[str]]:
    """MNT_ONLY-д байгаа t2s view бүрийг дэд асуулгаар солино (alias хэвээр, alias-гүй бол view-ийн нэр)."""
    tree = sqlglot.parse_one(sql, read="postgres")
    replaced = []
    for t in list(tree.find_all(exp.Table)):
        name = t.name
        if (t.db or "").lower() != "t2s" or name not in MNT_ONLY:
            continue
        alias = t.alias or name
        sub = exp.Subquery(this=sqlglot.parse_one(MNT_ONLY[name], read="postgres"),
                           alias=exp.TableAlias(this=exp.to_identifier(alias)))
        t.replace(sub)
        replaced.append(name)
    return tree.sql(dialect="postgres"), replaced


def check(ex: Executor, item: dict) -> dict:
    sql = item["gold_sql"]
    out = {"id": item["id"], "category": item.get("category")}
    try:
        sql2, replaced = mnt_only(sql)
    except Exception as e:  # noqa: BLE001 — parse алдааг тайланд бичнэ
        return {**out, "error": f"parse: {e}"}
    out["replaced"] = replaced
    a, b = ex.run(sql), ex.run(sql2)
    if not (a.ok and b.ok):
        return {**out, "error": f"exec: {a.error if not a.ok else b.error}"}
    out["same_result"] = result_hash(a.columns, a.rows) == result_hash(b.columns, b.rows)
    out["gold_sample"] = [list(map(str, r)) for r in a.rows[:3]]
    out["mnt_only_sample"] = [list(map(str, r)) for r in b.rows[:3]]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ids", default="", help="таслалаар (анхдагч: FULL_REVIEW G1-ийн 13 асуулт)")
    a = ap.parse_args()
    cfg = load_config()
    ex = Executor(bot_dsn(cfg), statement_timeout_ms=cfg["db"]["statement_timeout_ms"],
                  max_rows=int(cfg["pipeline"]["max_result_rows"]) * 100)
    want = {i.strip() for i in a.ids.split(",") if i.strip()}
    report = []
    for split, ids in DEFAULT_IDS.items():
        items = {}
        for line in (ROOT / f"evalset/{split}.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                items[d["id"]] = d
        for i in ids:
            if want and i not in want:
                continue
            report.append({"split": split, **check(ex, items[i])})
    # Сессийн цагийн бүс: compare.norm_value нь timestamptz-ийг UTC+8 руу хөрвүүлдэг — UTC сесст date_trunc(DATE) нь 08:00
    # болж gold-ийн огноотой таарахгүй байж болзошгүй (2026-11-01 dev шинжилгээ)
    tz = ex.run("SELECT current_setting('TimeZone')")
    print(json.dumps({"db_timezone": tz.rows[0][0] if tz.ok and tz.rows else f"error: {tz.error}"}, ensure_ascii=False))
    for r in report:
        print(json.dumps(r, ensure_ascii=False))
    diff = [r["id"] for r in report if r.get("same_result") is False]
    print(json.dumps({"summary": {"checked": len(report), "differs_with_usd": diff,
                                  "errors": [r["id"] for r in report if "error" in r]}}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
