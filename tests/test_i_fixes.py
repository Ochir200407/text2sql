"""I бүлэг: санал зөрөхөд тодруулга (E3c), dev-ийн жишээний сан (test хэзээ ч үгүй), өөрийн family-г хасах."""
import json

import pytest

from mn_text2sql.config import load_config
from mn_text2sql.eval import report
from mn_text2sql.pipeline.options import RunOptions
from mn_text2sql.pipeline.orchestrator import Answer, Candidate, clarify_mn
from mn_text2sql.retrieval.build import load_example_pool
from mn_text2sql.retrieval.indexes import Example, ExampleIndex


def _rec(qid, cat, status, correct, conf):
    return {"question_id": qid, "category": cat, "status": status, "correct": correct, "confidence": conf,
            "variant": "mn", "difficulty": "easy"}


def test_derive_clarify_scores_by_category(tmp_path, monkeypatch):
    cfg = load_config()
    cfg["eval"]["results_dir"] = str(tmp_path)
    src = tmp_path / "runs" / "dev" / "E2"
    src.mkdir(parents=True)
    recs = [_rec("A1", "answerable", "answered", True, 0.375),     # бага итгэл → тодруулга → буруу
            _rec("A2", "answerable", "answered", True, 0.875),     # хэвээр зөв
            _rec("U1", "unanswerable", "answered", False, 0.25),   # тодруулга → зөв
            _rec("U2", "unanswerable", "refused", True, 0.0),      # татгалзсан хэвээр
            _rec("M1", "ambiguous", "answered", False, 0.375),     # тодруулга → зөв (GUIDE.md)
            _rec("M2", "ambiguous", "abstained", False, 0.0)]      # алдаагаар татгалзсан → тодруулга → зөв
    (src / "arctic-r1-7b__mn__s1__h_H4.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs),
                                                          encoding="utf-8")
    out = report.derive_abstention(cfg, "E2", "E3c", 0.5, "dev", action="clarify", tag="h_H4")
    assert len(out) == 1
    got = {r["question_id"]: r for r in map(json.loads, (tmp_path / "runs" / "dev" / "E3c" /
                                                         "arctic-r1-7b__mn__s1__h_H4.jsonl").read_text(
        encoding="utf-8").splitlines())}
    assert got["A1"]["status"] == "clarify" and not got["A1"]["correct"]
    assert got["A2"]["status"] == "answered" and got["A2"]["correct"]
    assert got["U1"]["correct"] and got["U2"]["status"] == "refused" and got["U2"]["correct"]
    assert got["M1"]["correct"] and got["M2"]["status"] == "clarify" and got["M2"]["correct"]
    # abstain (E3) хэвээр: хоёрдмол асуултад татгалзах нь зөв биш
    out2 = report.derive_abstention(cfg, "E2", "E3", 0.5, "dev", action="abstain", tag="h_H4")
    got2 = {r["question_id"]: r for r in map(json.loads, (tmp_path / "runs" / "dev" / "E3" /
                                                          "arctic-r1-7b__mn__s1__h_H4.jsonl").read_text(
        encoding="utf-8").splitlines())}
    assert out2 and got2["M1"]["status"] == "abstained" and not got2["M1"]["correct"]
    assert got2["U1"]["correct"]


def test_low_conf_action_default_keeps_fingerprint():
    a = RunOptions(run_id="X").fingerprint()
    b = RunOptions(run_id="X", low_conf_action="abstain").fingerprint()
    c = RunOptions(run_id="X", low_conf_action="clarify").fingerprint()
    assert a == b and "low_conf_action" not in a and c.get("low_conf_action") == "clarify"


def test_clarify_message_lists_interpretations():
    c1 = Candidate(sql="SELECT 1", ok=True, rows=1, result_hash="h1", tables=["t2s.loans"])
    c2 = Candidate(sql="SELECT 2", ok=True, rows=3, result_hash="h2", tables=["t2s.loan_payments"])
    ans = Answer(question="q", variant="mn", run_id="E3c", model="m", candidates=[c1, c1, c2], confidence=0.375,
                 status="clarify")
    msg = clarify_mn(ans)
    assert "1) t2s.loans: 1 мөр" in msg and "2) t2s.loan_payments: 3 мөр" in msg


def test_dev_examples_never_include_test():
    cfg = load_config()
    base = load_example_pool(cfg)
    plus = load_example_pool(cfg, include_dev=True)
    test_ids = {json.loads(x)["id"] for x in open(cfg["eval"]["test_file"], encoding="utf-8") if x.strip()}
    dev = [json.loads(x) for x in open(cfg["eval"]["dev_file"], encoding="utf-8") if x.strip()]
    dev_ans = {d["id"] for d in dev if d["category"] == "answerable" and d.get("gold_sql")}
    ids_base, ids_plus = {e.id for e in base}, {e.id for e in plus}
    assert not ids_base & (dev_ans | test_ids)
    assert not ids_plus & test_ids
    assert dev_ans <= ids_plus and len(plus) == len(base) + len(dev_ans)


@pytest.mark.parametrize("exclude", [None, {"F-1"}])
def test_example_search_excludes_own_family(exclude):
    exs = [Example(id=f"E{i}", question_mn=q, question_latin="", question_en="", sql="SELECT 1", family=f)
           for i, (q, f) in enumerate([("зээлийн тоо хэд вэ", "F-1"), ("зээлийн тоо хэд вэ салбараар", "F-1"),
                                       ("хадгаламжийн дүн", "F-2"), ("зээлийн дундаж хүү", "F-3")])]
    got = ExampleIndex(exs).search("зээлийн тоо хэд вэ", [], k=4, exclude_families=exclude)
    fams = {e.family for e in got}
    assert ("F-1" in fams) == (exclude is None)


def test_index_build_glossary_requires_out():
    import pytest

    from mn_text2sql.cli import main
    with pytest.raises(SystemExit, match="--out"):
        main(["index-build", "--glossary", "knowledge/glossary_plus.yaml"])
