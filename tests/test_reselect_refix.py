"""reselect --refix (M1afx-ийн 4 засварыг хадгалсан raw_output-д GPU-гүй): засваргүй дахин гаргалт хадгалсан SQL-ийг яг
давтсан бичлэгт л засвар хэрэглэнэ (runner raw_output-ийн сүүлийн 4000 тэмдэгтийг л хадгалдаг — тасархай бол хадгалсан
SQL), олон хувилбартай ба raw_output-гүй бичлэг хэвээр, extract_fallback нь _single_turn-тэй ижил. DB-гүй — process_sql,
Executor, guard, оноог хуурамчаар солино."""
import json
from collections import Counter
from types import SimpleNamespace

import pytest

import mn_text2sql.eval.reselect as rs
import mn_text2sql.eval.runner as runner
import mn_text2sql.security.sql_guard as sg
from mn_text2sql.cli import build_parser
from mn_text2sql.config import load_config
from mn_text2sql.pipeline import orchestrator as orch


class Ex:
    def __init__(self, *a, **k):
        pass

    def run(self, sql, max_rows=None):
        from mn_text2sql.db.executor import ExecResult
        return ExecResult(True, ["c"], [], 0)

    def close(self):
        pass


def _block(sql):
    return f"<think>x</think>\n<answer>\n```sql\n{sql}\n```\n</answer>"


@pytest.fixture
def refixer(tmp_path, monkeypatch):
    calls = []

    def fake_process(self, raw_sql, hits=None):
        calls.append((raw_sql, hits, self.opts.transpile_fix))
        c = orch.Candidate(raw_sql=raw_sql)
        if raw_sql == "BROKEN":
            c.sql, c.error, c.error_stage = raw_sql, "syntax error", "exec"
        else:
            c.sql, c.ok = f"{raw_sql} /*fix={self.opts.transpile_fix}*/", True
        return c

    monkeypatch.setattr(orch.Pipeline, "process_sql", fake_process)
    monkeypatch.setattr(sg, "extended_block", lambda text, raw: "FULL" if raw == "BROKEN" else None)
    f = tmp_path / "M1z" / "arctic-r1-7b__mn__s0.jsonl"
    f.parent.mkdir()
    f.with_suffix(".meta.json").write_text(json.dumps({"options": {"dialect": "sqlite_transpile", "postproc": True,
                                                                   "guard_scope": None}}), encoding="utf-8")

    def make(mode):
        return rs.make_refixer(load_config(), f, Ex(), mode)
    return make, calls


def test_afx_applies_fixes_only_when_unfixed_rederivation_matches(refixer):
    make, calls = refixer
    fn, guard = make("afx")
    assert guard.scoped_columns is True                       # guard_scope
    ok = {"raw_output": _block("SELECT 1"), "sql": "SELECT 1 /*fix=False*/", "n_candidates": 1,
          "retrieval": {"values": [["branches", "branch_name", "Дархан салбар", 1.0]]}}
    assert fn(ok) == ["SELECT 1 /*fix=True*/"]
    assert calls[-1] == ("SELECT 1", [("branches", "branch_name", "Дархан салбар", 1.0)], True)   # literal_fix-ийн hits
    trunc = {"raw_output": _block("SELECT 2"), "sql": "SELECT 9", "n_candidates": 1}
    assert fn(trunc) == ["SELECT 9"]                         # тасархай raw_output → хадгалсан SQL
    multi = {"raw_output": _block("SELECT 3"), "cand_sqls": ["a", "b"], "sql": "a", "n_candidates": 2}
    assert fn(multi) == ["a", "b"]
    assert fn({"raw_output": None, "sql": "S", "status": "refused", "n_candidates": 0}) == ["S"]
    assert fn.stats == Counter({"derived": 1, "changed": 1, "trunc": 1, "kept": 2})


def test_extract_fallback_like_single_turn(refixer):
    make, _calls = refixer
    fn, _ = make("afx")
    rec = {"raw_output": _block("BROKEN"), "sql": "BROKEN", "n_candidates": 1}
    assert fn(rec) == ["FULL /*fix=True*/"]
    fn0, guard0 = make("none")
    assert guard0.scoped_columns is False
    assert fn0(rec) == ["BROKEN"]                             # засваргүй: fallback-гүй


def test_none_mode_reports_mismatch(refixer):
    make, _calls = refixer
    fn, _ = make("none")
    assert fn({"raw_output": _block("SELECT 2"), "sql": "SELECT 9", "n_candidates": 1}) == ["SELECT 2 /*fix=False*/"]
    assert fn.stats == Counter({"derived": 1, "changed": 1})
    with pytest.raises(ValueError):
        rs.make_refixer(load_config(), None, Ex(), "all")


def test_reselect_refix_wiring(tmp_path, monkeypatch):
    seen, guards = [], []

    class Guard:
        def __init__(self, kw):
            guards.append(kw)

        def check(self, s):
            seen.append(s)
            return SimpleNamespace(ok=False, error="stop", sql=s, tables=[])

    def fake_refixer(cfg, path, ex, mode):
        def fn(r):
            fn.stats["derived"] += 1
            return [f"FIXED {path.parent.name} {r['question_id']}"]
        fn.stats = Counter()
        return fn, None

    monkeypatch.setattr(rs, "make_guard", lambda cfg, **kw: Guard(kw))
    monkeypatch.setattr(rs, "make_refixer", fake_refixer)
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
    for run in ("M1x", "M1y"):
        d = tmp_path / "runs" / "dev" / run
        d.mkdir(parents=True)
        rec = {"question_id": it["id"], "category": "answerable", "variant": "mn", "model": "arctic-r1-7b",
               "status": "answered", "n_candidates": 1, "question": it["question_mn"], "confidence": 1.0,
               "sql": f"SELECT stored_{run}"}
        (d / "arctic-r1-7b__mn__s0.jsonl").write_text(json.dumps(rec, ensure_ascii=False) + "\n", encoding="utf-8")
    rs.reselect_run(cfg, "M1x", "M1x_fx", "dev", rule_refuse=False, rule_clarify=False, extra=["M1y"],
                    refix="afx")
    assert sorted(seen) == [f"FIXED M1x {it['id']}", f"FIXED M1y {it['id']}"]
    assert {} in guards and {"scoped_columns": True} in guards   # gold-ийн guard хэвээр, хувилбарынх scoped
    out_f = tmp_path / "runs" / "dev" / "M1x_fx" / "arctic-r1-7b__mn__s0.jsonl"
    assert json.loads(out_f.read_text(encoding="utf-8"))["reselect"]["refix"] == "afx"
    summ = json.loads(out_f.with_suffix(".summary.json").read_text(encoding="utf-8"))
    assert summ["refix"] == "afx" and summ["refix_changed"] == 1 and summ["refix_stats"] == {"derived": 1}


def test_cli_flag():
    assert build_parser().parse_args(["reselect", "--refix", "afx"]).refix == "afx"
    assert build_parser().parse_args(["reselect"]).refix is None
    with pytest.raises(SystemExit):
        build_parser().parse_args(["reselect", "--refix", "all"])
