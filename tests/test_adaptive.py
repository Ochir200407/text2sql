"""Дасан зохицох self-consistency (eval/adaptive.py) ба orchestrator-ийн hook — DB, загваргүй."""
import json
from dataclasses import fields

import pytest

from mn_text2sql.eval import adaptive as ad
from mn_text2sql.llm.client import LLMResponse
from mn_text2sql.pipeline import orchestrator as orch
from mn_text2sql.pipeline.options import RunOptions


def test_beta_leader_prob_closed_form():
    assert ad.beta_leader_prob(0, 0) == pytest.approx(0.5)
    assert ad.beta_leader_prob(1, 0) == pytest.approx(0.75)
    assert ad.beta_leader_prob(2, 0) == pytest.approx(0.875)
    assert ad.beta_leader_prob(3, 1) == pytest.approx(0.8125)      # 4 хувилбараас 3 нийлсэн → 0.8-ийг давна
    assert ad.beta_leader_prob(2, 1) == pytest.approx(0.6875)
    assert ad.beta_leader_prob(4, 4) == pytest.approx(0.5)


def _c(h, degenerate=False):
    c = orch.Candidate(raw_sql="x")
    if h:
        c.ok, c.result_hash, c.sql = True, h, f"SELECT '{h}'"
    else:
        c.error, c.error_stage = "boom", "exec"
    c.degenerate = degenerate
    return c


@pytest.mark.parametrize("hs,bad", [
    (["a", "b", "b", "a"], set()),           # тэнцвэл эхэлж гарсан нь
    ([None, "b", "a", "a"], set()),
    (["a", "a", "b"], {"a"}),                # шүүлт
    (["a", "a"], {"a"}),                     # бүгд шүүгдвэл шүүлтгүй
    ([None, None], set()),
])
def test_vote_matches_pipeline_vote(hs, bad):
    cands = [_c(h, h in bad) for h in hs]
    chosen, _, _ = orch.Pipeline.vote(cands, use_filter=True)
    k = ad.vote(hs, frozenset(bad))
    if k is None:
        assert not chosen.ok
    else:
        assert cands[k] is chosen


def test_parse_spec_and_schedule():
    p = ad.parse_spec("beta:0.8:w4")
    assert (p.rule, p.param, p.waves, p.greedy) == ("beta", 0.8, (4, 4), False)
    assert ad.parse_spec("g+beta:0.8:w2").greedy
    assert ad.parse_spec("agree:2").waves == (2, 6)
    assert ad.parse_spec("esc:w2").waves == (2, 2, 2, 2)
    assert ad.parse_spec("beta:0.9:w3").waves == (3, 3, 2)
    assert ad.schedule(ad.parse_spec("beta:0.8:w4"), 6) == [4, 2]
    assert ad.schedule(ad.parse_spec("fixed:4"), 8) == [4]
    with pytest.raises(ValueError):
        ad.parse_spec("magic:1")


def _item(hs, correct, **kw):
    return ad.Item(qid="Q", category="answerable", const=None, hashes=hs, correct=correct,
                   bad=frozenset(), **kw)


def test_run_item_stops_early_on_agreement():
    it = _item(["a"] * 8, [True] * 8)
    o = ad.run_item(it, ad.parse_spec("beta:0.8:w4"))
    assert o.n_gen == 4 and o.correct and o.waves == [4]
    o = ad.run_item(it, ad.parse_spec("agree:2"))
    assert o.n_gen == 2


def test_run_item_disagreement_uses_all():
    hs = ["a", "b", "c", "d", "a", "b", "a", "a"]
    it = _item(hs, [h == "a" for h in hs])
    o = ad.run_item(it, ad.parse_spec("beta:0.8:w4"))
    assert o.n_gen == 8 and o.correct
    assert ad.run_item(it, ad.Policy("f1", (1,))).n_gen == 1


def test_greedy_first_and_const_status():
    it = _item(["b"] * 8, [False] * 8, g_hash="a", g_correct=True, g_weight=1)
    o = ad.run_item(it, ad.parse_spec("g+beta:0.8:w4"))
    assert o.n_gen == 5 and not o.correct                 # greedy + 4; 4 «b» нь greedy-г дийлнэ
    it.const = "refused"
    assert not ad.run_item(it, ad.parse_spec("beta:0.8:w4")).answered


def test_infer_bad_reproduces_filtered_choice():
    hs = ["z", "z", "z", "a", "a", None]
    bad = ad._infer_bad(hs, "a", "answered")
    assert bad == {"z"} and hs[ad.vote(hs, bad)] == "a"
    assert ad._infer_bad(hs, "z", "abstained") == {"z", "a"}


def test_load_items_reads_only_given_dev_dirs(tmp_path):
    rows = [{"question_id": "Q1", "category": "answerable", "status": "answered", "result_hash": "a",
             "correct": True, "cand_hashes": ["a", "a", "b"], "cand_correct": [True, True, False],
             "gen_ms": 800.0, "latency_ms": 1000.0}]
    for d in ("E4", "E2"):
        (tmp_path / d).mkdir()
        (tmp_path / d / ad.RUN_FILE).write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    items = ad.load_items(tmp_path, "E4", greedy_run=None)
    assert len(items) == 1 and items[0].ovh_ms == 200.0 and items[0].g_weight == 0
    res = ad.evaluate(items, ad.Policy("fixed1", (1,)))
    assert res["ex_ans"][0] == 100.0 and res["mean_n"][0] == 1.0


def test_option_default_off_and_not_in_fingerprint():
    assert RunOptions().adaptive_stop is None
    assert "adaptive_stop" not in RunOptions().fingerprint()
    assert RunOptions(adaptive_stop="beta:0.8:w4").fingerprint()["adaptive_stop"] == "beta:0.8:w4"
    assert "adaptive_stop" in {f.name for f in fields(RunOptions)}


class _LLM:
    """Дуудлага бүрийн n-ийг бүртгэнэ; хариу — урьдчилан өгсөн SQL-ийн дараалал."""

    def __init__(self, sqls):
        self.sqls, self.calls = list(sqls), []

    def chat(self, messages, *, n=1, temperature=0.0, top_p=1.0, max_tokens=2048, stop=None, seed=None,
             prefill=None):
        self.calls.append((n, temperature, seed))
        out = [self.sqls.pop(0) for _ in range(n)]
        return LLMResponse(out, 10 * n, 5 * n, 1.0, "mock")


def _pipe(llm, **opt):
    p = orch.Pipeline.__new__(orch.Pipeline)
    p.opts = RunOptions(candidates=8, sampling=True, **opt)
    p.llm, p.family, p.max_tokens = llm, "omnisql", 256
    p.model_cfg = {"sampling": {"temperature": 0.8}}

    def single(messages, prefill, text, tin, tout, seed):
        return _c(text)
    p._single_turn = single
    return p


def _decoding_patch(monkeypatch):
    monkeypatch.setattr(orch.Pipeline, "_decoding",
                        lambda self, s: {"temperature": 0.8 if s else 0.0, "top_p": 1.0})


def test_hook_stops_after_first_wave(monkeypatch):
    _decoding_patch(monkeypatch)
    llm = _LLM(["a"] * 8)
    cands = _pipe(llm, adaptive_stop="beta:0.8:w4").generate(None, 1, ([{"role": "user", "content": "q"}], None))
    assert len(cands) == 4 and [c[0] for c in llm.calls] == [4]


def test_hook_continues_and_varies_seed(monkeypatch):
    _decoding_patch(monkeypatch)
    llm = _LLM(["a", "b", "c", "d", "a", "a", "a", "a"])
    cands = _pipe(llm, adaptive_stop="beta:0.8:w4").generate(None, 1, ([{"role": "user", "content": "q"}], None))
    assert len(cands) == 8 and [c[0] for c in llm.calls] == [4, 4]
    assert llm.calls[0][2] != llm.calls[1][2]           # давалгаа бүр өөр seed


def test_wave_seeds_are_prefix_of_one_call(monkeypatch):
    """vLLM n>1: хүүхэд j нь seed+j — давалгааны seed = seed + өмнө түүвэрлэсэн тоо (давхцахгүй, нэг n=8-ийн угтвар)."""
    _decoding_patch(monkeypatch)
    llm = _LLM(["a", "b", "c", "d", "a", "a", "a", "a"])
    _pipe(llm, adaptive_stop="beta:0.8:w4").generate(None, 7, ([{"role": "user", "content": "q"}], None))
    assert [c[2] for c in llm.calls] == [7, 11]
    llm = _LLM(["a", "b", "c", "a", "b", "c", "d", "e", "f"])
    _pipe(llm, adaptive_stop="g+beta:0.8:w2").generate(None, 7, ([{"role": "user", "content": "q"}], None))
    seeds = [c[2] for c in llm.calls[1:]]                 # greedy-ийн дараах түүвэрлэсэн давалгаанууд
    assert seeds[:2] == [7, 9]


def test_hook_greedy_first(monkeypatch):
    _decoding_patch(monkeypatch)
    llm = _LLM(["a"] * 9)
    _pipe(llm, adaptive_stop="g+beta:0.8:w2").generate(None, 1, ([{"role": "user", "content": "q"}], None))
    assert llm.calls[0][:2] == (1, 0.0) and llm.calls[1][0] == 2 and len(llm.calls) == 2


def test_default_path_is_single_batched_call(monkeypatch):
    _decoding_patch(monkeypatch)
    llm = _LLM(["a"] * 8)
    p = _pipe(llm)
    assert p.opts.adaptive_stop is None
    cands = p.generate(None, 1, ([{"role": "user", "content": "q"}], None))
    assert len(cands) == 8 and llm.calls == [(8, 0.8, 1)]
