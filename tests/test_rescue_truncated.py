"""R7 #5 rescue_truncated (budget forcing) ба R7 #1 reproducibility meta. DB, загваргүй — хуурамч LLM/executor/guard."""
import threading

from mn_text2sql.db.executor import ExecResult
from mn_text2sql.eval import runner
from mn_text2sql.llm.client import LLMResponse
from mn_text2sql.pipeline import orchestrator as orch
from mn_text2sql.pipeline.options import RunOptions, load_experiments, resolve_run
from mn_text2sql.security.sql_guard import GuardResult
from mn_text2sql.service.flow import BY_TYPE

PREFILL = "Let me solve this step by step.\n<think>"
LOOP = PREFILL + " I need the loans table. " + "Hmm, wait. " * 50          # SQL-гүй, max_tokens-д тасарсан
GOOD = PREFILL + " easy</think>\n<answer>\n```sql\nSELECT good\n```\n</answer>"


class _LLM:
    """Эхний дуудлага (prefill == PREFILL): initial гаралт + finish_reason. Дараагийнх нь аврах үргэлжлэл."""

    def __init__(self, initial, reasons, cont="SELECT rescued\n```\n</answer>", echo_prefill=True, boom=False):
        self.initial, self.reasons, self.cont = initial, reasons, cont
        self.echo_prefill, self.boom = echo_prefill, boom
        self.calls = []
        self.lock = threading.Lock()

    def chat(self, messages, *, n=1, temperature=0.0, top_p=1.0, max_tokens=2048, stop=None, seed=None,
             prefill=None):
        with self.lock:
            self.calls.append({"n": n, "temperature": temperature, "max_tokens": max_tokens, "prefill": prefill,
                               "seed": seed, "messages": messages})
        if prefill == PREFILL:
            return LLMResponse(list(self.initial[:n]), 1000, 3072 * n, 1.0, "mock",
                               {"choices": [{"finish_reason": r} for r in self.reasons[:n]]})
        if self.boom:
            raise RuntimeError("context length")
        return LLMResponse([(prefill if self.echo_prefill else "") + self.cont], 1100, 20, 1.0, "mock",
                           {"choices": [{"finish_reason": "stop"}]})


class _Exec:
    def run(self, sql, max_rows=None, explain=False):
        return ExecResult(True, ["x"], [(sql.split()[-1],)], 1)


class _Guard:
    def check(self, sql, rewrite=True):
        return GuardResult(True, sql, tables=["t"])


def _pipe(llm, rescue=True, n=1, **kw) -> orch.Pipeline:
    p = orch.Pipeline.__new__(orch.Pipeline)
    p.opts = RunOptions(candidates=n, sampling=n > 1, rescue_truncated=rescue, **kw)
    p.llm, p.family, p.max_tokens = llm, "omnisql", 3072
    p.mspec = {"sampling": {"temperature": 0.8}}
    p.executor, p.guard = _Exec(), _Guard()
    return p


MSGS = [{"role": "system", "content": "s"}, {"role": "user", "content": "q"}]


def _gen(p, seed=None):
    return p.generate(None, seed, prompt=(MSGS, PREFILL))


def test_default_off_and_hash_neutral():
    assert RunOptions().rescue_truncated is False
    assert "rescue_truncated" not in RunOptions().fingerprint()
    assert RunOptions(rescue_truncated=True).fingerprint()["rescue_truncated"] is True
    llm = _LLM([LOOP], ["length"])
    cands = _gen(_pipe(llm, rescue=False))
    assert len(llm.calls) == 1 and not cands[0].rescued and cands[0].sql is None


def test_rescue_truncated_without_sql():
    llm = _LLM([LOOP, GOOD], ["length", "stop"])
    cands = _gen(_pipe(llm, n=2), seed=3)
    assert len(llm.calls) == 2
    c = llm.calls[1]
    assert c["n"] == 1 and c["temperature"] == 0.0 and c["max_tokens"] == orch.RESCUE_MAX_TOKENS == 512
    assert c["prefill"] == LOOP.rstrip() + "\n</think>\n<answer>\n```sql\n" and c["messages"] == MSGS
    assert c["seed"] == 3
    assert cands[0].rescued and cands[0].sql == "SELECT rescued" and cands[0].ok
    assert not cands[1].rescued and cands[1].sql == "SELECT good"
    # аврах токен нийлбэрт орно (2 хувилбарт хуваагдана)
    assert cands[0].tokens_out == (3072 * 2 + 20) // 2


def test_no_rescue_when_stopped_or_parsable():
    truncated_but_sql = PREFILL + " x</think>\n<answer>\n```sql\nSELECT early\n```\n" + "loop " * 40
    llm = _LLM([truncated_but_sql, LOOP], ["length", "stop"])
    cands = _gen(_pipe(llm, n=2))
    assert len(llm.calls) == 1                                  # parsable тасарсан, эсвэл stop → дуудлагагүй
    assert cands[0].sql == "SELECT early" and not any(c.rescued for c in cands)


def test_rescue_when_reasoning_mentions_with_select():
    """extract_sql нь «with overdue status» гэх мэт үгийг SQL гэж барьдаг — </think> хүрээгүй бол аврана."""
    looping = PREFILL + " I should select loans with overdue status. " * 30
    llm = _LLM([looping], ["length"])
    cands = _gen(_pipe(llm))
    assert len(llm.calls) == 2 and cands[0].rescued and cands[0].sql == "SELECT rescued"


def test_unclosed_block_is_closed():
    llm = _LLM([LOOP], ["length"], cont="SELECT a, b FROM t WHERE x = 1")
    cands = _gen(_pipe(llm))
    assert cands[0].rescued and cands[0].sql == "SELECT a, b FROM t WHERE x = 1"
    assert cands[0].text.endswith("```\n</answer>")


def test_prefill_ignored_or_error_keeps_original():
    llm = _LLM([LOOP], ["length"], echo_prefill=False)          # сервер prefill-гүйгээр шинэ хариу өгсөн
    cands = _gen(_pipe(llm))
    assert len(llm.calls) == 2 and not cands[0].rescued and cands[0].text == LOOP
    llm = _LLM([LOOP], ["length"], boom=True)
    cands = _gen(_pipe(llm))
    assert not cands[0].rescued and cands[0].text == LOOP and cands[0].error_stage != "llm"


def test_missing_finish_reason_no_rescue():
    llm = _LLM([LOOP], ["length"])
    llm.reasons = []                                           # MockLLM шиг raw-гүй хариу
    cands = _gen(_pipe(llm))
    assert len(llm.calls) == 1 and not cands[0].rescued


def test_adaptive_path_rescues():
    llm = _LLM([GOOD, LOOP, GOOD, GOOD], ["stop", "length", "stop", "stop"])
    p = _pipe(llm, n=4, adaptive_stop="agree:2")
    cands = _gen(p, seed=0)
    assert any(c.rescued for c in cands)


def test_flow_and_experiment_registration():
    assert BY_TYPE["writer"]["fields"]["rescue_truncated"] == {"kind": "bool"}
    exp = load_experiments()
    m1rt, m1a = resolve_run("M1rt", exp), resolve_run("M1a", exp)
    assert m1rt.rescue_truncated and not m1a.rescue_truncated
    a, b = m1a.fingerprint(), m1rt.fingerprint()
    assert "rescue_truncated" not in a and {k: v for k, v in b.items() if k != "rescue_truncated"} == a


# ---------------------------------------------------------------- R7 #1: reproducibility meta
def test_serving_info_env_override(monkeypatch):
    monkeypatch.setenv("T2S_VLLM_ARGS", "--dtype half --tensor-parallel-size 2")
    monkeypatch.setenv("T2S_VLLM_ARGS__arctic_r1_7b", "--dtype half --tensor-parallel-size 2 --enable-prefix-caching")
    info = runner.serving_info({"llm": {"base_url": "http://x/v1"}}, "arctic-r1-7b", {"vllm_args": "--dtype bfloat16"},
                               query_server=False)
    assert info["vllm_args_effective"].endswith("--enable-prefix-caching")
    assert info["vllm_args_source"] == "T2S_VLLM_ARGS__arctic_r1_7b"
    monkeypatch.delenv("T2S_VLLM_ARGS__arctic_r1_7b")
    monkeypatch.delenv("T2S_VLLM_ARGS")
    info = runner.serving_info({"llm": {"base_url": "http://x/v1"}}, "arctic-r1-7b", {"vllm_args": "--dtype bfloat16"},
                               query_server=False)
    assert info["vllm_args_effective"] == "--dtype bfloat16" and info["vllm_args_source"] == "models.yaml"
    assert info["server_models"] is None


def test_options_hash_ignores_runtime_meta():
    base = {"options": {"a": 1}, "served_model": "m", "eval_set": {"sha1": "x"}}
    h = runner.options_hash(base, "mn", 0)
    assert runner.options_hash(dict(base, max_tokens=1536, workers=4, serving={"gpu": ["T4"]}), "mn", 0) == h


def test_runtime_mismatch():
    cur = {"max_tokens": 3072, "workers": 8, "serving": {"vllm_args_effective": "A"}}
    assert runner.runtime_mismatch(None, cur) == {}
    assert runner.runtime_mismatch({"vllm_args": "old"}, cur) == {}                # хуучин meta — харьцуулахгүй
    assert runner.runtime_mismatch(dict(cur), cur) == {}
    prev = {"max_tokens": 1536, "workers": 4, "serving": {"vllm_args_effective": "B"}}
    assert runner.runtime_mismatch(prev, cur) == {"max_tokens": [1536, 3072], "workers": [4, 8],
                                                  "vllm_args_effective": ["B", "A"]}


def test_strict_transient_rescue_failure_is_infra():
    """repair_strict: аврах дуудлагын түр алдаа → InfraError → generate() «llm» нэр дэвшигч (runner дахин асууна);
    400 нь байнгын → хуучнаараа (тасарсан гаралт)."""
    from mn_text2sql.llm.client import LLMCallError
    llm = _LLM([LOOP], ["length"], boom=True)                  # RuntimeError, статусгүй → түр
    cands = _gen(_pipe(llm, repair_strict=True))
    assert len(cands) == 1 and cands[0].error_stage == "llm" and "rescue_truncated" in cands[0].error

    class _Perm(_LLM):
        def chat(self, messages, **kw):
            if kw.get("prefill") != PREFILL:
                raise LLMCallError("Client error '400 Bad Request'", status=400)
            return super().chat(messages, **kw)
    cands = _gen(_pipe(_Perm([LOOP], ["length"]), repair_strict=True))
    assert not cands[0].rescued and cands[0].text == LOOP
