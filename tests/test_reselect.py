"""reselect: загварын татгалзал (нэг хувилбартай run) дээр дүрэм онлайнтай адил түрүүлж ажиллана (tester M1)."""
import json

from mn_text2sql.config import load_config
from mn_text2sql.eval.reselect import reselect_run


def test_rule_precedes_model_refusal_in_single_candidate_runs(tmp_path):
    cfg = load_config()
    dev = [json.loads(x) for x in open(cfg["eval"]["dev_file"], encoding="utf-8") if x.strip()]
    amb = next(d for d in dev if d["category"] == "ambiguous")
    una, una2 = [d for d in dev if d["category"] == "unanswerable"][:2]
    cfg["eval"]["results_dir"] = str(tmp_path)
    src = tmp_path / "runs" / "dev" / "I3"
    src.mkdir(parents=True)
    base = {"variant": "mn", "model": "arctic-r1-7b", "sql": None, "cand_sqls": None, "confidence": 1.0}
    recs = [dict(base, question_id=amb["id"], category="ambiguous", status="refused", n_candidates=1,
                 question="Сүүлийн 3 сарын олголт хэд вэ?"),                 # загвар татгалзсан, дүрэм → тодруулга
            dict(base, question_id=una["id"], category="unanswerable", status="refused", n_candidates=0,
                 question=una["question_mn"]),                                # intent_check-ийн татгалзал хэвээр
            dict(base, question_id=una2["id"], category="unanswerable", status="refused", n_candidates=1,
                 sql="SELECT 1 FROM t2s.no_such_table", question=una2["question_mn"])]   # хувилбар ажиллахгүй → татгалзал
    (src / "arctic-r1-7b__mn__s0.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in recs),
                                                     encoding="utf-8")
    reselect_run(cfg, "I3", "I3r", "dev")
    out = {r["question_id"]: r for r in map(json.loads, (tmp_path / "runs" / "dev" / "I3r" /
                                                        "arctic-r1-7b__mn__s0.jsonl").read_text(
        encoding="utf-8").splitlines())}
    assert out[amb["id"]]["status"] == "clarify" and out[amb["id"]]["correct"]
    assert out[una["id"]]["status"] == "refused" and out[una["id"]]["correct"]
    assert out[una2["id"]]["status"] == "refused" and out[una2["id"]]["correct"]     # tester L5


def test_merge_candidates_puts_weighted_extra_first():
    from mn_text2sql.eval.reselect import merge_candidates
    assert merge_candidates(["a", "b"], ["x"], 3) == ["x", "x", "x", "a", "b"]
    assert merge_candidates(["a"], [], 2) == ["a"]
    assert merge_candidates([], ["x", "y"], 1) == ["x", "y"]


def test_ensemble_extra_run_outvotes_base_with_weight(tmp_path):
    """E5: суурь run-ий 2 буруу хувилбарыг нэмэлт run-ий зөв хувилбар жин 3-аар давна, жин 1-ээр давахгүй."""
    cfg = load_config()
    dev = [json.loads(x) for x in open(cfg["eval"]["dev_file"], encoding="utf-8") if x.strip()]
    it = next(d for d in dev if d["id"] == "A001")
    cfg["eval"]["results_dir"] = str(tmp_path)
    base_dir, extra_dir = tmp_path / "runs" / "dev" / "E2X", tmp_path / "runs" / "dev" / "I3tX"
    base_dir.mkdir(parents=True)
    extra_dir.mkdir(parents=True)
    wrong = "SELECT count(*) FROM t2s.loan_applications"
    rec = {"question_id": "A001", "category": "answerable", "variant": "mn", "model": "arctic-r1-7b",
           "status": "answered", "n_candidates": 2, "question": it["question_mn"], "confidence": 1.0}
    (base_dir / "arctic-r1-7b__mn__s1__h_H4.jsonl").write_text(
        json.dumps(dict(rec, sql=wrong, cand_sqls=[wrong, wrong]), ensure_ascii=False) + "\n", encoding="utf-8")
    (extra_dir / "arctic-r1-7b__mn__s0.jsonl").write_text(
        json.dumps(dict(rec, sql=it["gold_sql"], n_candidates=1), ensure_ascii=False) + "\n", encoding="utf-8")
    got = {}
    for w in (1, 3):
        reselect_run(cfg, "E2X", f"E5w{w}", "dev", tag="h_H4", extra=["I3tX"], extra_weight=w, rule_clarify=False)
        out = json.loads((tmp_path / "runs" / "dev" / f"E5w{w}" / "arctic-r1-7b__mn__s1__h_H4.jsonl").read_text(
            encoding="utf-8").splitlines()[0])
        got[w] = out["correct"]
    assert got == {1: False, 3: True}
