"""Зэрэгцээ засвар (repair_workers): гаралт дараалсантай ижил, хугацаа богиносно, нэг гинжний алдаа тусгаарлагдана.
DB, загваргүй — хуурамч LLM, executor, guard."""
import re
import threading
import time
from dataclasses import fields

import pytest

from mn_text2sql.db.executor import ExecResult
from mn_text2sql.llm.client import LLMResponse
from mn_text2sql.pipeline import orchestrator as orch
from mn_text2sql.pipeline.options import RunOptions
from mn_text2sql.security.sql_guard import GuardResult
from mn_text2sql.service.flow import NODES

# 8 хувилбар: зөв, алдаатай (2 удаа засна), хоосон, ... — засвар нь хувилбараас хамаарсан SQL буцаана
INITIAL = ["good_a", "bad1", "good_b", "empty1", "bad2", "good_a", "bad3", "good_b"]
REPAIR = {"bad1": "bad1x", "bad1x": "good_b",       # 2 алхмын гинж
          "bad2": "good_a", "empty1": "good_b",
          "bad3": "bad3x", "bad3x": "bad3y"}         # 2 удаа засаад ч алдаатай


def _block(tag: str) -> str:
    return f"```sql\nSELECT {tag}\n```"


class _LLM:
    """n > 1 = эхний үүсгэлт; n == 1 = засвар (сүүлийн assistant echo-гийн SQL-ээс хамаарна, sleep-тэй)."""

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.lock = threading.Lock()
        self.calls = 0

    def chat(self, messages, *, n=1, temperature=0.0, top_p=1.0, max_tokens=2048, stop=None, seed=None,
             prefill=None):
        with self.lock:
            self.calls += 1
        if n > 1:
            return LLMResponse([_block(t) for t in INITIAL[:n]], 100, 40 * n, 1.0, "mock")
        prev = re.search(r"SELECT (\w+)", messages[-2]["content"]).group(1)
        time.sleep(self.delay)
        if "llmboom" in prev:
            raise RuntimeError("context too long")
        return LLMResponse([_block(REPAIR.get(prev, prev))], 50, 10, 1.0, "mock")


class _Exec:
    def run(self, sql, max_rows=None, explain=False):
        tag = sql.split()[-1]
        if tag == "crash":
            raise ConnectionError("DB холболт тасарсан")
        if tag.startswith("bad"):
            return ExecResult(False, error=f"column {tag} does not exist", error_code="db_error")
        if tag.startswith("empty"):
            return ExecResult(True, ["x"], [], 0)
        return ExecResult(True, ["x"], [(tag.split("_")[-1],)], 1)


class _Guard:
    def check(self, sql, rewrite=True):
        return GuardResult(True, sql, tables=["t"])


def _pipe(workers: int, delay: float = 0.0, initial=None) -> orch.Pipeline:
    p = orch.Pipeline.__new__(orch.Pipeline)
    p.opts = RunOptions(candidates=len(initial or INITIAL), sampling=True, repair_rounds=2, repair_on_empty=True,
                        repair_workers=workers)
    p.llm, p.family, p.max_tokens = _LLM(delay), "omnisql", 256
    p.mspec = {"sampling": {"temperature": 0.8}}
    p.executor, p.guard = _Exec(), _Guard()
    return p


def _sig(cands):
    return [(c.sql, c.ok, c.error, c.error_stage, c.result_hash, c.repairs, c.raw_turns, c.tokens_in,
             c.tokens_out, c.messages) for c in cands]


def _run(p):
    return p.generate(None, seed=1, prompt=([{"role": "user", "content": "q"}], None))


def test_option_default_sequential_and_not_in_fingerprint():
    assert RunOptions().repair_workers == 1
    assert "repair_workers" not in RunOptions().fingerprint()
    assert "repair_workers" not in RunOptions(repair_workers=4).fingerprint()   # хурдны тохиргоо — hash-д нөлөөгүй
    owned = {k for n in NODES for k in n["fields"]}
    assert "repair_workers" in owned and "repair_workers" in {f.name for f in fields(RunOptions)}


def test_parallel_outputs_identical_to_sequential():
    seq, par = _run(_pipe(1)), _run(_pipe(4))
    assert _sig(seq) == _sig(par)
    assert [c.repairs for c in seq] == [0, 2, 0, 1, 1, 0, 2, 0]
    v1, v4 = orch.Pipeline.vote(seq), orch.Pipeline.vote(par)
    assert seq.index(v1[0]) == par.index(v4[0]) and v1[1:] == v4[1:]
    assert v1[0].result_hash == next(c.result_hash for c in seq if c.sql == "SELECT good_b")


def test_parallel_is_faster_with_slow_llm():
    d = 0.15     # 4 гинжинд 2 + 1 + 1 + 2 = 6 засварын дуудлага — дараалсан ≈ 6·d, 4 thread ≈ 2·d
    t0 = time.perf_counter()
    _run(_pipe(1, d))
    seq_s = time.perf_counter() - t0
    t0 = time.perf_counter()
    _run(_pipe(4, d))
    par_s = time.perf_counter() - t0
    assert seq_s >= 6 * d * 0.9
    assert par_s < seq_s * 0.6


def test_exception_in_one_chain_is_isolated():
    initial = ["good_a", "bad1", "crash", "bad2"]
    global INITIAL
    saved, INITIAL = INITIAL, initial
    try:
        with pytest.raises(ConnectionError):      # дараалсан горим: анхны зан төлөв (алдаа дээш гарна)
            _run(_pipe(1, initial=initial))
        par = _run(_pipe(4, initial=initial))
    finally:
        INITIAL = saved
    assert len(par) == 4
    assert par[2].error_stage == "llm" and "ConnectionError" in par[2].error and not par[2].ok
    assert [c.sql for i, c in enumerate(par) if i != 2] == ["SELECT good_a", "SELECT good_b", "SELECT good_a"]
    assert all(c.ok for i, c in enumerate(par) if i != 2)


def test_failed_repair_call_keeps_previous_candidate_in_parallel():
    # засварын LLM дуудлага унавал өмнөх хувилбар үлдэнэ (_single_turn), бусад гинж үргэлжилнэ
    cands = _pipe(4)._repair_chains([{"role": "user", "content": "q"}], None,
                                    [_block("bad_llmboom"), _block("good_c")], 0, 0, 1, 0.0)
    assert [c.ok for c in cands] == [False, True] and cands[1].sql == "SELECT good_c"


def test_progress_callback_reaches_workers():
    seen = []
    orch.set_progress(lambda node, msg, state: seen.append((node, threading.current_thread().name)))
    try:
        _run(_pipe(4))
    finally:
        orch.set_progress(None)
    assert any(n == "repair" and t.startswith("repair") for n, t in seen)
