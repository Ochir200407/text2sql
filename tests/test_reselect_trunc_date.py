"""reselect --trunc-date (R7): хадгалсан хувилбарт pipeline-ийн trunc_date-тэй ижил засварыг guard-аас өмнө хэрэглэнэ
(GPU-гүй); анхдагч off бол SQL хэвээр. DB-гүй — Executor/guard/оноог хуурамчаар солино."""
import json
from types import SimpleNamespace

import pytest

import mn_text2sql.eval.reselect as rs
import mn_text2sql.eval.runner as runner
from mn_text2sql.cli import build_parser
from mn_text2sql.config import load_config

SQL = ("SELECT DATE_TRUNC('month', disbursed_date) AS m, COUNT(*) FROM t2s.loans "
       "GROUP BY DATE_TRUNC('month', disbursed_date)")


@pytest.mark.parametrize("on", [False, True])
def test_trunc_date_rewrites_candidates_before_guard(tmp_path, monkeypatch, on):
    seen = []

    class Guard:
        def check(self, s):
            seen.append(s)
            return SimpleNamespace(ok=False, error="stop", sql=s, tables=[])

    class Ex:
        def __init__(self, *a, **k):
            pass

        def close(self):
            pass

    monkeypatch.setattr(rs, "make_guard", lambda cfg: Guard())
    monkeypatch.setattr(rs, "Executor", Ex)
    monkeypatch.setattr(rs, "bot_dsn", lambda cfg: "")
    monkeypatch.setattr(rs, "LiteralChecker", lambda *a, **k: None)
    monkeypatch.setattr(rs, "enum_codes", lambda *a, **k: {})
    monkeypatch.setattr(rs, "cached_model", lambda *a, **k: None)
    monkeypatch.setattr(runner, "GoldCache", lambda *a, **k: None)
    monkeypatch.setattr(runner, "init_value_maps", lambda *a, **k: None)
    monkeypatch.setattr(runner, "score_answer", lambda cfg, it, ans, gold: {"correct": False})
    cfg = load_config()
    dev = [json.loads(x) for x in open(cfg["eval"]["dev_file"], encoding="utf-8") if x.strip()]
    it = next(d for d in dev if d["category"] == "answerable")
    cfg["eval"]["results_dir"] = str(tmp_path)
    src = tmp_path / "runs" / "dev" / "M1x"
    src.mkdir(parents=True)
    rec = {"question_id": it["id"], "category": "answerable", "variant": "mn", "model": "arctic-r1-7b",
           "status": "answered", "n_candidates": 1, "question": it["question_mn"], "confidence": 1.0, "sql": SQL}
    (src / "arctic-r1-7b__mn__s0.jsonl").write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
    rs.reselect_run(cfg, "M1x", "M1x_t", "dev", rule_refuse=False, rule_clarify=False, trunc_date=on)
    assert len(seen) == 1
    assert ("CAST(DATE_TRUNC('MONTH', disbursed_date) AS DATE)" in seen[0]) is on
    out = json.loads((tmp_path / "runs" / "dev" / "M1x_t" / "arctic-r1-7b__mn__s0.jsonl").read_text(encoding="utf-8"))
    assert out["reselect"].get("trunc_date", False) is on


def test_cli_flag():
    a = build_parser().parse_args(["reselect", "--from", "M1ens", "--to", "M1ens_t", "--trunc-date"])
    assert a.trunc_date is True
    assert build_parser().parse_args(["reselect"]).trunc_date is False
