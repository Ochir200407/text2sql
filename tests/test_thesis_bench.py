"""Thesis benchmark (thesis/bench.py): gold хөрвүүлэлт, t2s qualify, SQLite timeout, харьцуулалтын тайлан."""

import json
import sqlite3

from mn_text2sql.thesis.bench import compare_report, qualify_t2s, run_sqlite, sqlite_gold_to_pg


def test_gold_conversion_rules():
    bools, dates = {"is_active"}, {"order_date", "shipped_date", "delivered_date"}
    pg = sqlite_gold_to_pg("SELECT AVG(julianday(delivered_date) - julianday(shipped_date)) FROM shipments", bools, dates)
    assert "EXTRACT(EPOCH FROM CAST(delivered_date AS TIMESTAMP)) / 86400.0" in pg
    pg = sqlite_gold_to_pg("SELECT COUNT(*), SUM(is_active) FROM stores WHERE is_active = 1", bools, dates)
    assert "is_active = TRUE" in pg and "SUM(CAST(is_active AS INT))" in pg
    pg = sqlite_gold_to_pg("SELECT COUNT(*) FROM orders WHERE order_date LIKE '2024%'", bools, dates)
    assert "CAST(order_date AS TEXT) LIKE '2024%'" in pg


def test_qualify_keeps_ctes():
    sql = qualify_t2s("WITH x AS (SELECT * FROM orders) SELECT * FROM x JOIN customers c ON c.customer_id = x.customer_id")
    assert "t2s.orders" in sql and "t2s.customers" in sql and "t2s.x" not in sql


def test_sqlite_timeout():
    conn = sqlite3.connect(":memory:")
    res = run_sqlite(conn, "WITH RECURSIVE r(n) AS (SELECT 1 UNION ALL SELECT n + 1 FROM r) SELECT count(*) FROM r",
                     timeout_s=0.5)
    assert not res.ok and res.error_code == "timeout"


def _write(path, recs):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")


def _rec(qid, ok, variant="mn", tags=()):
    return {"question_id": qid, "variant": variant, "category": "answerable", "difficulty": "easy", "status": "answered",
            "correct": ok, "raw_correct": ok, "exec_ok": True, "sql": "SELECT 1", "tags": list(tags)}


def test_compare_report(tmp_path):
    from mn_text2sql.config import load_config
    cfg = load_config("config/thesis.yaml", overrides={"eval": {"results_dir": str(tmp_path)}})
    runs = tmp_path / "runs" / "test"
    _write(runs / "EXT-thesis-agent" / "thesis-agent__mn__s0.jsonl",
           [_rec("q1", False), _rec("q2", True), _rec("q3", False, tags=["gold_empty"])])
    _write(runs / "G1" / "arctic-r1-7b__mn__s0.jsonl", [_rec("q1", True), _rec("q2", True), _rec("q3", True, tags=["gold_empty"])])
    for seed, oks in ((1, (True, False, True)), (2, (True, True, True))):        # E2: олон seed
        _write(runs / "E2" / f"arctic-r1-7b__mn__s{seed}.jsonl", [_rec(f"q{i + 1}", ok) for i, ok in enumerate(oks)])
    _write(runs / "B5" / "arctic-r1-7b__mn__s0__fs_traindev.jsonl", [_rec("q1", True), _rec("q2", False)])
    out = compare_report(cfg, splits=("test",), out=str(tmp_path / "C.md"))
    text = (tmp_path / "C.md").read_text(encoding="utf-8")
    assert not out["reference_missing"]
    assert "| G1 | arctic-r1-7b | 100.0 (n=3) | 100.0 |" in text           # gold хоосон-гүй багана
    assert "83.34 (n=3, 2 seed)" in text                                    # seed-үүдийн дундаж
    assert "| B5-fs_traindev |" in text and "(1.0, 2)" in text               # tag + n_pairs
