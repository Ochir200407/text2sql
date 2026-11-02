"""C4 (FULL_REVIEW_2026-10-30 §3): repair_strict — засварын давталтын засвар. Анхдагч (False) үед хуучин зан төлөв
байт-байтаар хэвээр, fingerprint/opts_hash өөрчлөгдөхгүй. DB, загваргүй — хуурамч LLM, executor, guard."""
from dataclasses import fields

from mn_text2sql.db.executor import ExecResult
from mn_text2sql.llm.client import LLMResponse
from mn_text2sql.pipeline import orchestrator as orch
from mn_text2sql.pipeline.options import _LATE_DEFAULTS, RunOptions, load_experiments, resolve_run
from mn_text2sql.security.sql_guard import GuardResult
from mn_text2sql.service.flow import NODES

REFUSAL = "Sorry, this question cannot be answered from the database."


def _block(tag: str) -> str:
    return f"```sql\nSELECT {tag}\n```"


class _LLM:
    """n > 1 = эхний үүсгэлт (initial); n == 1 = засвар — repairs дарааллаар (Exception бол raise)."""

    def __init__(self, initial, repairs, tin=100, tout=40):
        self.initial, self.repairs, self.tin, self.tout = initial, list(repairs), tin, tout

    def chat(self, messages, *, n=1, temperature=0.0, top_p=1.0, max_tokens=2048, stop=None, seed=None,
             prefill=None):
        if n > 1 or not self.repairs:
            return LLMResponse(self.initial[:n], self.tin, self.tout, 1.0, "mock")
        r = self.repairs.pop(0)
        if isinstance(r, Exception):
            raise r
        return LLMResponse([r], 50, 10, 1.0, "mock")


class _Exec:
    def __init__(self):
        self.calls = 0

    def run(self, sql, max_rows=None, explain=False):
        self.calls += 1
        tag = sql.split()[-1]
        if tag.startswith("bad"):
            return ExecResult(False, error=f"column {tag} does not exist", error_code="db_error")
        if tag.startswith("empty"):
            return ExecResult(True, ["x"], [], 0)
        return ExecResult(True, ["x"], [(tag,)], 1)


class _Guard:
    def check(self, sql, rewrite=True):
        return GuardResult(True, sql, tables=["t"])


def _pipe(strict: bool, initial, repairs, **kw) -> orch.Pipeline:
    p = orch.Pipeline.__new__(orch.Pipeline)
    p.opts = RunOptions(candidates=len(initial), sampling=True, repair_rounds=2, repair_on_empty=True,
                        repair_strict=strict, **kw)
    p.llm, p.family, p.max_tokens = _LLM(initial, repairs), "omnisql", 256
    p.mspec = {"sampling": {"temperature": 0.8}}
    p.executor, p.guard = _Exec(), _Guard()
    return p


def _run(p):
    return p.generate(None, seed=1, prompt=([{"role": "user", "content": "q"}], None))


# ------------------------------------------------------------------ тохиргоо
def test_option_default_off_and_hash_stable():
    assert RunOptions().repair_strict is False and _LATE_DEFAULTS["repair_strict"] is False
    assert "repair_strict" not in RunOptions().fingerprint()
    assert RunOptions(repair_strict=True).fingerprint()["repair_strict"] is True
    owned = {k for n in NODES for k in n["fields"]}
    assert "repair_strict" in owned and "repair_strict" in {f.name for f in fields(RunOptions)}
    exp = load_experiments()
    for rid in exp["runs"]:
        o = resolve_run(rid, exp)
        if not o.repair_strict:
            assert "repair_strict" not in o.fingerprint(), rid   # өмнөх run-уудын opts_hash хэвээр


# ------------------------------------------------------------------ (1) засварын LLM дуудлагын алдаа
def test_repair_call_error_default_keeps_original():
    c = _run(_pipe(False, [_block("bad1"), _block("good")], [RuntimeError("LLM дуудлага амжилтгүй: timeout")]))
    assert c[0].error_stage == "exec" and not c[0].ok and c[0].repairs == 0


def test_repair_call_error_strict_marks_llm():
    c = _run(_pipe(True, [_block("bad1"), _block("good")], [RuntimeError("LLM дуудлага амжилтгүй: timeout")]))
    assert c[0].error_stage == "llm" and not c[0].ok and "Засварын LLM" in c[0].error
    assert c[1].ok and c[1].sql == "SELECT good"


def test_repair_call_error_strict_empty_candidate_not_answered():
    c = _run(_pipe(True, [_block("empty1"), _block("good")], [RuntimeError("LLM дуудлага амжилтгүй: 503")]))
    assert c[0].error_stage == "llm" and not c[0].ok and c[0].result_hash is None


def test_repair_call_client_error_strict_keeps_original():
    # 4xx (context-ийн урт) нь тогтвортой алдаа — дахин ажиллуулсан ч засагдахгүй тул өмнөх хувилбар үлдэнэ
    e = RuntimeError("LLM дуудлага амжилтгүй: Client error '400 Bad Request' for url 'http://x/v1/chat/completions'")
    c = _run(_pipe(True, [_block("bad1"), _block("good")], [e]))
    assert c[0].error_stage == "exec"
    assert orch._transient_llm_error(RuntimeError("Client error '429 Too Many Requests'"))


# ------------------------------------------------------------------ (2) засварын дараах татгалзалт
def test_refusal_after_empty_repair_default_dropped():
    c = _run(_pipe(False, [_block("empty1"), _block("good")], [REFUSAL]))
    assert c[0].ok and c[0].error_stage == "empty" and c[0].repairs == 0


def test_refusal_after_empty_repair_strict_kept():
    c = _run(_pipe(True, [_block("empty1"), _block("good")], [REFUSAL]))
    assert c[0].error_stage == "refusal" and c[0].sql is None and c[0].repairs == 1
    assert (c[0].tokens_in, c[0].tokens_out) == (50 + 50, 20 + 10)


def test_agent_refusal_after_repair_strict_kept():
    def agent(strict):
        p = _pipe(strict, ["x"], [])
        p.family = "sqrl"
        p.opts.candidates = 1
        p.llm = _LLM(["x"], ["<answer>SELECT empty1</answer>", REFUSAL])
        return p._agent([{"role": "user", "content": "q"}], {}, 1)
    assert agent(False).error_stage == "empty"
    assert agent(True).error_stage == "refusal"


# ------------------------------------------------------------------ токены нийлбэр
def test_reverted_repair_tokens_counted_only_when_strict():
    # хоосон → засвар алдаатай → хоосон хувилбар руу буцна; засварын дуудлагын токен strict үед тоологдоно
    d = _run(_pipe(False, [_block("empty1"), _block("good")], [_block("bad1")]))
    s = _run(_pipe(True, [_block("empty1"), _block("good")], [_block("bad1")]))
    assert d[0].error_stage == s[0].error_stage == "empty"
    assert (d[0].tokens_in, d[0].tokens_out) == (50, 20)
    assert (s[0].tokens_in, s[0].tokens_out) == (100, 30)


def test_generation_token_remainder_distributed_when_strict():
    initial = [_block("good"), _block("good"), _block("good")]
    d = _run(_pipe(False, initial, [], ))
    s = _run(_pipe(True, initial, []))
    assert sum(c.tokens_in for c in d) == 99 and sum(c.tokens_out for c in d) == 39     # 100 // 3 · 3
    assert sum(c.tokens_in for c in s) == 100 and sum(c.tokens_out for c in s) == 40
    assert [c.tokens_in for c in s] == [34, 33, 33]


def test_parallel_strict_matches_sequential():
    initial = [_block("good"), _block("empty1"), _block("bad1")]
    seq = _run(_pipe(True, initial, [REFUSAL, _block("good2")], repair_workers=1))
    par_p = _pipe(True, initial, [], repair_workers=3)

    class _ByPrev(_LLM):   # зэрэгцээ үед дараалал тодорхойгүй тул засвар өмнөх SQL-ээс хамаарна
        def chat(self, messages, *, n=1, **kw):
            if n > 1:
                return LLMResponse(initial, 100, 40, 1.0, "mock")
            prev = messages[-2]["content"]
            return LLMResponse([REFUSAL if "empty1" in prev else _block("good2")], 50, 10, 1.0, "mock")
    par_p.llm = _ByPrev(initial, [])
    par = _run(par_p)
    sig = [(c.sql, c.ok, c.error_stage, c.tokens_in, c.tokens_out) for c in seq]
    assert sig == [(c.sql, c.ok, c.error_stage, c.tokens_in, c.tokens_out) for c in par]


# ------------------------------------------------------------------ (3) bool_cols
class _FailExec:
    def __init__(self):
        self.calls = 0

    def run(self, sql, max_rows=None, explain=False):
        self.calls += 1
        return ExecResult(False, error="connection refused", error_code="db_error")


def test_failed_bool_cols_lookup_cached_by_default():
    p = _pipe(False, ["x"], [])
    p.executor = _FailExec()
    assert p.bool_cols() == frozenset() and p.bool_cols() == frozenset()
    assert p.executor.calls == 1


def test_failed_bool_cols_lookup_not_cached_when_strict():
    # L5 (2026-10-31): strict үед хоосон олонлог буцаахгүй — дэд бүтцийн алдаа (runner дахин асууна); кэшлэхгүй
    import pytest
    p = _pipe(True, ["x"], [])
    p.executor = _FailExec()
    for _ in range(2):
        with pytest.raises(orch.InfraError):
            p.bool_cols()
    assert p.executor.calls == 2

    class _Ok:
        def run(self, sql, max_rows=None, explain=False):
            return ExecResult(True, ["c"], [("is_active",)], 1)
    p.executor = _Ok()
    assert p.bool_cols() == frozenset({"is_active"})
