"""synth_own мөрийг PostgreSQL дээр гүйцэтгэж шалгана (Kaggle-д; локал машинд PG асаахгүй).

    python scripts/synth/exec_validate.py --in build/synth_own/candidates.jsonl \
        --out build/synth_own/validated.jsonl --train-out data/train/own_synth.jsonl --append

Мөр бүр: SQL хамгаалагч (as-of дахин бичилттэй) → bot role-оор гүйцэтгэл. Хасах шалтгаан:
  guard_fail | exec_error | timeout | empty | all_null | single_zero (1×1 = 0/NULL) | too_many_rows |
  constant_metric (олон мөр, хэмжигдэхүүний багана бүгд ижил) | short_series (LAG < 2 үе) |
  same_result_as_eval (dev/test gold-ийн үр дүнгийн hash-тай ижил — leakage) | dup_result (өөр synth мөртэй ижил).
Үлдсэн мөрт result_hash, rowcount, exec_ms, needs_exec_check: false нэмнэ. --train-out өгвөл training.data.TrainItem
(source=own_synth, engine=postgres) бичнэ — дараа нь `python -m mn_text2sql.training dedup` → own_synth_clean.jsonl.
"""
from __future__ import annotations

import argparse
import json
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))


def _read(path: Path) -> list[dict]:
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def degenerate(row: dict, res, max_rows: int) -> str | None:
    """Хоосон/утгагүй үр дүнгийн шалтгаан (байхгүй бол None)."""
    if res.rowcount == 0:
        return "empty"
    if res.truncated or res.rowcount > max_rows:
        return "too_many_rows"
    if all(all(v is None for v in r) for r in res.rows):
        return "all_null"
    if res.rowcount == 1 and len(res.columns) == 1 and (res.rows[0][0] is None or res.rows[0][0] == 0):
        return "single_zero"
    if row.get("pattern") == "lag_pop" and res.rowcount < 2:
        return "short_series"
    if res.rowcount > 1 and len(res.columns) > 1:
        # эхний багана = бүлэг/хугацаа; бусад нь хэмжигдэхүүн — бүгд тогтмол бол мэдээлэлгүй
        metric_cols = range(1, len(res.columns))
        if all(len({r[i] for r in res.rows}) == 1 for i in metric_cols):
            return "constant_metric"
    return None


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in", dest="inp", default="build/synth_own/candidates.jsonl")
    ap.add_argument("--out", default="build/synth_own/validated.jsonl")
    ap.add_argument("--rejected", default="build/synth_own/rejected.jsonl")
    ap.add_argument("--train-out", help="TrainItem JSONL (жишээ: data/train/own_synth.jsonl)")
    ap.add_argument("--append", action="store_true", help="--train-out файлд нэмэх (өмнөх C3 мөрийг хадгална)")
    ap.add_argument("--config", help="нэмэлт YAML (config/default.yaml-ийг дарна)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--max-rows", type=int, default=2000)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--no-eval-hash", action="store_true", help="dev/test-ийн үр дүнгийн hash шалгалтыг алгасах")
    a = ap.parse_args(argv)

    from mn_text2sql.config import load_config, resolve_path
    from mn_text2sql.db.conn import bot_dsn
    from mn_text2sql.db.executor import Executor
    from mn_text2sql.eval.compare import result_hash
    from mn_text2sql.eval.dataset import load_all_eval_items
    from mn_text2sql.eval.goldcheck import make_guard

    cfg = load_config(a.config)
    rows = _read(resolve_path(a.inp))
    if a.limit:
        rows = rows[: a.limit]
    guard = make_guard(cfg)
    ex = Executor(bot_dsn(cfg), statement_timeout_ms=cfg["db"]["statement_timeout_ms"], maxconn=a.workers + 2)

    # leakage: dev/test gold-ийн үр дүнгийн hash (training.data.dedup_against_eval-тэй ижил санаа)
    eval_hashes: dict[str, str] = {}
    if not a.no_eval_hash:
        for it in load_all_eval_items(cfg):
            for s in it.gold_sqls():
                g = guard.check(s)
                r = ex.run(g.sql) if g.ok else None
                if r is not None and r.ok and r.rowcount > 0:
                    eval_hashes[result_hash(r.columns, r.rows)] = it.id

    lock = threading.Lock()
    kept, rejected = [], []
    seen: dict[str, str] = {}
    reasons = Counter()

    def work(row: dict) -> None:
        g = guard.check(row["gold_sql"])
        if not g.ok:
            reason, res, h = "guard_fail", None, None
        else:
            t0 = time.perf_counter()
            res = ex.run(g.sql, max_rows=a.max_rows + 1)
            row["exec_ms"] = round((time.perf_counter() - t0) * 1000, 1)
            if not res.ok:
                reason, h = ("timeout" if res.error_code == "timeout" else "exec_error"), None
            else:
                reason = degenerate(row, res, a.max_rows)
                h = result_hash(res.columns, res.rows, bool(row.get("order_matters")))
                if reason is None and result_hash(res.columns, res.rows) in eval_hashes:
                    reason = "same_result_as_eval"
        with lock:
            if reason is None and res.rowcount > 1 and h in seen:
                reason = "dup_result"
            if reason:
                reasons[reason] += 1
                rejected.append({"id": row["id"], "pattern": row["pattern"], "reason": reason,
                                 "error": (res.error if res is not None and not res.ok else g.error)})
                return
            seen[h] = row["id"]
            row.update({"needs_exec_check": False, "exec_ok": True, "result_hash": h, "rowcount": res.rowcount,
                        "executed_sql": g.sql})
            kept.append(row)

    with ThreadPoolExecutor(max_workers=a.workers) as pool:
        list(pool.map(work, rows))
    ex.close()
    kept.sort(key=lambda r: r["id"])
    out = resolve_path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept), encoding="utf-8")
    rej = resolve_path(a.rejected)
    rej.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rejected), encoding="utf-8")

    stats = {"in": len(rows), "kept": len(kept), "rejected": len(rejected), "reasons": dict(reasons),
             "kept_by_pattern": dict(Counter(r["pattern"] for r in kept)),
             "rejected_by_pattern": dict(Counter(r["pattern"] for r in rejected)),
             "eval_result_hashes": len(eval_hashes), "out": str(out)}
    if a.train_out:
        from mn_text2sql.training.data import TrainItem, read_items, write_items
        items = [TrainItem(id=f"synth_own-{r['id']}", source="own_synth", engine="postgres", gold_sql=r["gold_sql"],
                           question_mn=r["question_mn"], question_en=r["question_en"],
                           question_latin=r["question_latin"], difficulty=r["difficulty"],
                           extra={"origin": "synth_own", "pattern": r["pattern"], "family": r["family"],
                                  "result_hash": r["result_hash"],
                                  # L10: sampling/GRPO-ийн зөвийн шалгалт дарааллыг үнэлгээний дүрмээр (TrainItem.order_matters)
                                  **({"order_matters": True} if r.get("order_matters") else {})})
                 for r in kept]
        tpath = resolve_path(a.train_out)
        if a.append:
            prev = [it for it in read_items(tpath) if not it.id.startswith("synth_own-")]
            items = prev + items
        write_items(items, tpath)
        stats["train_out"] = {"path": str(tpath), "n": len(items)}
    resolve_path("build/synth_own/exec_stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2),
                                                              encoding="utf-8")
    sys.stdout.write(json.dumps(stats, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
