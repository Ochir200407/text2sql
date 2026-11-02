"""scripts/rerank/rr.py-ийн сургалтын өгөгдөл (B8, FULL_REVIEW_2026-10-30): эерэг нэр томьёо оруулах (v1 = V25/M1rr-ийн
хуучин зан төлөв хэвээр, v2 = засвар), holdout-д эерэг оруулахгүй (v2), dev/test-ийн асуулт сургалтад ороогүй. Загвар/torch-гүй."""
import importlib.util
import random
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("rr", ROOT / "scripts" / "rerank" / "rr.py")
rr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rr)


def _v1_inline(c, need, pool):
    """V25-д ажилласан анхны код (rr.py cmd_train-ийн мөр) — v1 үүнтэй яг ижил байх ёстой."""
    for t in need:
        if t not in c:
            c = c[:-1] + [t] if len(c) >= pool else c + [t]
    return c


def test_v1_reproduces_original_exactly():
    rnd = random.Random(0)
    ids = [f"t{i}" for i in range(30)]
    for _ in range(500):
        c = rnd.sample(ids, rnd.randint(0, 14))
        need = rnd.sample(ids, rnd.randint(0, 4))
        assert rr.inject_positives(c, need, 12, "v1") == _v1_inline(list(c), need, 12)


def test_v1_bug_drops_positive_when_two_missing():
    c = [f"n{i}" for i in range(12)]
    out = rr.inject_positives(c, ["a", "b"], 12, "v1")
    assert "a" not in out and "b" in out          # B8: саяхан оруулсан эерэг дараагийнхаар дарагдсан


def test_v1_bug_displaces_retrieved_positive():
    c = [f"n{i}" for i in range(11)] + ["p"]       # «p» эерэг, хамгийн доод рангид олдсон
    out = rr.inject_positives(c, ["a", "p"], 12, "v1")
    assert not {"a", "p"} <= set(out)              # «a» нь «p»-г дарж, дахин оруулсан «p» нь «a»-г дарна


@pytest.mark.parametrize("need", [["a", "b"], ["a", "p"], ["p", "a"], ["a", "b", "c"], ["a", "a"]])
def test_v2_keeps_every_positive_and_pool_size(need):
    c = [f"n{i}" for i in range(11)] + ["p"]
    out = rr.inject_positives(c, need, 12, "v2")
    assert set(need) <= set(out)
    assert len(out) == 12 and len(set(out)) == len(out)
    negs = [x for x in c if x not in need]
    assert [x for x in out if x not in need] == negs[: 12 - len(set(need))]   # доод рангийн сөрөг л хасагдана


def test_v2_short_pool_appends_and_all_positive_pool():
    assert rr.inject_positives(["x"], ["a"], 12, "v2") == ["x", "a"]
    full = [f"p{i}" for i in range(12)]
    out = rr.inject_positives(full, full + ["q"], 12, "v2")
    assert set(out) == set(full) | {"q"}           # сөрөг үлдээгүй бол эерэгийг хасахгүй (pool-оос давна)


def _items():
    return [{"id": f"e{i}", "family": f"f{i % 10}", "q_mn": f"q{i}", "q_en": f"q{i}", "need": ["gold"] if i % 2 else []}
            for i in range(40)]


def test_build_candidates_holdout_not_injected_in_v2():
    items = _items()
    random.seed(13); hold = rr.split_holdout(items)
    assert hold
    cand = lambda q: [f"n{j}" for j in range(14)]            # хайлт хэзээ ч «gold»-ыг олохгүй
    rr.build_candidates(items, hold, cand, "v2")
    for it in items:
        assert it["retrieved"] == cand(it["q_mn"])
        if it["family"] in hold:
            assert it["cands"] == it["retrieved"]             # holdout = бодит хайлт
        else:
            assert set(it["need"]) <= set(it["cands"])
    v1 = _items(); rr.build_candidates(v1, hold, cand, "v1")
    assert all(set(it["need"]) <= set(it["cands"]) for it in v1)   # v1: holdout-д ч оруулдаг (хөөрөгдсөн)
    held = [(set(it["need"]), it["retrieved"]) for it in items if it["family"] in hold and it["need"]]
    assert rr.metrics(held)["recall"] == 0.0
    with pytest.raises(ValueError):
        rr.build_candidates(_items(), hold, cand, "v3")


def test_split_holdout_same_for_both_versions():
    random.seed(13); a = rr.split_holdout(_items())
    random.seed(13); b = rr.split_holdout(_items())
    assert a == b


def test_eval_overlap_detects_leaks():
    items = [{"id": "e1", "family": "F1", "q_mn": "Зээл хэд вэ?", "q_en": "How many loans?"}]
    ev = [{"id": "x", "family": "G", "question_mn": "зээл  хэд вэ?", "gold_sql": "select 1"},
          {"id": "e1", "family": "F1", "question_mn": "өөр", "question_en": "HOW many loans?"},
          {"id": "y", "family": "H", "question_mn": "өөр2", "gold_sql": "SELECT 1"}]
    o = rr.eval_overlap(items, ev, [{"gold_sql": "SELECT  1"}])
    assert o == {"id": 1, "family": 1, "question": 2, "gold_sql": 2}


def test_real_training_data_has_no_eval_questions():
    if not (ROOT / "evalset" / "test.jsonl").exists() or not (ROOT / "knowledge" / "examples.jsonl").exists():
        pytest.skip("evalset/knowledge алга")
    terms = rr.Candidates.terms_only()
    items = rr.load_items(terms)
    res = rr.check_no_eval_leak(items)        # давхцал байвал SystemExit
    assert set(res) == {"dev", "test"}
    for sp in ("dev", "test"):
        assert res[sp]["id"] == res[sp]["family"] == res[sp]["question"] == 0
