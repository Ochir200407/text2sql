"""2026-10-31 шалгалтын C4/C8 нэмэлт засвар (L1–L6) ба oracle harness (L10). DB, загваргүй — хуурамч LLM/executor.

Бүгд repair_strict / select_parse="strict" үед л — анхдагч (legacy) зам өөрчлөгдөөгүй (test_repair_strict.py,
test_select_parse.py-ийн анхдагч тест хэвээр)."""
from types import SimpleNamespace

import httpx
import pytest

from mn_text2sql.db.executor import ExecResult
from mn_text2sql.llm import client as cl
from mn_text2sql.llm.client import LLMCallError, LLMResponse, MockLLM, OpenAICompatLLM
from mn_text2sql.pipeline import orchestrator as orch
from mn_text2sql.pipeline.options import RunOptions
from mn_text2sql.pipeline.orchestrator import parse_selection, reparse_selection, selection_raw_full
from mn_text2sql.security.sql_guard import GuardResult


def _block(tag: str) -> str:
    return f"```sql\nSELECT {tag}\n```"


class _G:
    def __init__(self, i):
        self.id, self.en, self.term, self.definition_en, self.sql = f"g{i}", f"term {i}", f"нэр {i}", "", "x"


TERMS = [_G(i) for i in range(1, 13)]


# ------------------------------------------------------------------ L2: parse_selection
@pytest.mark.parametrize("text,want", [
    ("<answer>None needed except 3</answer>", [3]),                 # дугаар байвал «none»-оос түрүүлнэ
    ("<answer>none (12 terms checked)</answer>", []),               # «12 terms» = тоолсон тоо
    ("<answer>3 terms: 1, 4, 7</answer>", [1, 4, 7]),
    ("<answer>None of these 12 apply</answer>", []), ("<answer>Only 3 of 12</answer>", [3]),
    ("</think><answer></answer>", []), ("<think>x</think>\n<answer> </answer>", []),
    ("<answer></answer>", None), ("Let me think <answer></answer>", None),
    ("<answer>1-3</answer>", None), ("<answer>1–3</answer>", None), ("<answer>1—3</answer>", None),
    ("<answer>2 - 5</answer>", [2, 5]), ("<answer>2 – 5</answer>", [2, 5]),
    ("<answer>−1, 4</answer>", [4]),
])
def test_parse_selection_l2(text, want):
    assert parse_selection(text, 12) == want


# ------------------------------------------------------------------ L1: select_raw_full + reparse
ROUNDTRIP = ["<answer>2, 7</answer>", "<think>I compare 3 and 11</think><answer>4</answer>",
             "reasoning 3 and 9 ...</think>\n<answer>1</answer> later <answer>5, 6</answer>",
             "</think><answer></answer>", "<answer></answer>", "<think>unfinished 5",
             "Term 3 mentions PAR30.\nAnswer: 2, 8", "x " * 2000 + "</think><answer>3, 9</answer>",
             "<think>a</think> mid <think>b</think><answer>none</answer>"]


@pytest.mark.parametrize("text", ROUNDTRIP)
def test_select_raw_full_reparses_like_online(text):
    raw = selection_raw_full(text)
    assert len(raw) <= 1000 + len("</think>")
    assert parse_selection(raw, 12) == parse_selection(text, 12)
    assert reparse_selection({"select_raw": "ignored", "select_raw_full": raw}, 12) == parse_selection(text, 12)


def test_reparse_old_records_wraps_select_raw():
    assert reparse_selection({"select_raw": "2, 7"}, 12) == [2, 7]
    assert reparse_selection({"select_raw": "Answer: PAR30 and 4"}, 12) == [4]
    assert reparse_selection({"select_raw": "none"}, 12) == []
    assert reparse_selection({}, 12) is None and reparse_selection(None, 12) is None


def _sel_pipe(reply, **opt):
    p = orch.Pipeline.__new__(orch.Pipeline)
    p.opts = RunOptions(glossary=True, knowledge_select=True, **opt)
    p.llm = reply if hasattr(reply, "chat") else MockLLM(lambda m: reply)
    return p


def test_select_raw_full_only_when_strict():
    reply = "<think>3?</think><answer>1</answer> wait <answer>5</answer>"
    legacy, strict = {}, {}
    _sel_pipe(reply)._select_glossary("q", TERMS, legacy)
    picked, _ = _sel_pipe(reply, select_parse="strict")._select_glossary("q", TERMS, strict)
    assert set(legacy) == {"select_raw"} and legacy["select_raw"] == "1"          # анхдагч бичлэг хэвээр
    assert strict["select_raw"] == "1" and [g.id for g in picked] == ["g5"]
    assert reparse_selection(strict, 12) == [5]                                   # онлайнтай ижил
    assert reparse_selection({"select_raw": strict["select_raw"]}, 12) == [1]     # хуучин бичлэгийн хязгаар


# ------------------------------------------------------------------ L3: HTTP кодоор ангилах
def test_error_status_and_transient_classes():
    for s in (400, 404, 413, 422):
        assert not cl.is_transient(LLMCallError("x", status=s))
    for s in (401, 403, 408, 429, 500, 502, 503, None):
        assert cl.is_transient(LLMCallError("x", status=s))
    assert cl.error_status(RuntimeError("LLM дуудлага амжилтгүй: Client error '413 Payload Too Large' for url")) == 413
    assert cl.error_status(RuntimeError("Server error '503 Service Unavailable'")) == 503
    assert cl.error_status(TimeoutError("read timeout")) is None
    assert isinstance(LLMCallError("m", 400), RuntimeError) and str(LLMCallError("m", 400)) == "m"


@pytest.mark.parametrize("respond,status", [
    (lambda req: httpx.Response(400, text="bad request"), 400),
    (lambda req: httpx.Response(503, text="overloaded"), 503),
    (lambda req: httpx.Response(429, text="slow down"), 429),
])
def test_client_error_carries_status(monkeypatch, respond, status):
    monkeypatch.setattr(cl.time, "sleep", lambda s: None)
    llm = OpenAICompatLLM("http://llm.test/v1", "m", max_retries=1)
    llm.client = httpx.Client(transport=httpx.MockTransport(respond))
    with pytest.raises(LLMCallError) as e:
        llm.chat([{"role": "user", "content": "q"}])
    assert e.value.status == status and str(e.value).startswith("LLM дуудлага амжилтгүй: ")


def test_client_transport_error_has_no_status(monkeypatch):
    monkeypatch.setattr(cl.time, "sleep", lambda s: None)

    def boom(req):
        raise httpx.ConnectError("refused", request=req)
    llm = OpenAICompatLLM("http://llm.test/v1", "m", max_retries=1)
    llm.client = httpx.Client(transport=httpx.MockTransport(boom))
    with pytest.raises(LLMCallError) as e:
        llm.chat([{"role": "user", "content": "q"}])
    assert e.value.status is None and cl.is_transient(e.value)


# ------------------------------------------------------------------ засварын гинж (test_repair_strict.py-ийн загвар)
class _LLM:
    def __init__(self, initial, repairs):
        self.initial, self.repairs = initial, list(repairs)

    def chat(self, messages, *, n=1, temperature=0.0, top_p=1.0, max_tokens=2048, stop=None, seed=None,
             prefill=None):
        if n > 1 or not self.repairs:
            return LLMResponse(self.initial[:n], 100, 40, 1.0, "mock")
        r = self.repairs.pop(0)
        if isinstance(r, Exception):
            raise r
        return LLMResponse([r], 50, 10, 1.0, "mock")


class _Exec:
    def __init__(self, bool_ok=True):
        self.bool_ok, self.calls = bool_ok, 0

    def run(self, sql, max_rows=None, explain=False):
        self.calls += 1
        if "information_schema" in sql:
            return ExecResult(True, ["c"], [("is_active",)], 1) if self.bool_ok else \
                ExecResult(False, error="connection refused", error_code="db_error")
        tag = sql.split()[-1]
        if tag.startswith("bad"):
            return ExecResult(False, error=f"column {tag} does not exist", error_code="db_error")
        if tag.startswith("empty"):
            return ExecResult(True, ["x"], [], 0)
        return ExecResult(True, ["x"], [(tag,)], 1)


class _Guard:
    def __init__(self):
        self.seen = []

    def check(self, sql, rewrite=True):
        self.seen.append(sql)
        return GuardResult(True, sql, tables=["t"])


def _pipe(strict, initial, repairs, **kw):
    p = orch.Pipeline.__new__(orch.Pipeline)
    p.opts = RunOptions(candidates=len(initial), sampling=True, repair_rounds=2, repair_on_empty=True,
                        repair_strict=strict, **kw)
    p.llm, p.family, p.max_tokens = _LLM(initial, repairs), "omnisql", 256
    p.mspec = {"sampling": {"temperature": 0.8}}
    p.executor, p.guard = _Exec(), _Guard()
    return p


def _run(p):
    return p.generate(None, seed=1, prompt=([{"role": "user", "content": "q"}], None))


def test_repair_status_codes_strict():
    keep = _run(_pipe(True, [_block("bad1"), _block("good")], [LLMCallError("LLM дуудлага амжилтгүй: x", 422)]))
    assert keep[0].error_stage == "exec"                     # тогтвортой — өмнөх хувилбар
    infra = _run(_pipe(True, [_block("bad1"), _block("good")], [LLMCallError("LLM дуудлага амжилтгүй: x", 401)]))
    assert infra[0].error_stage == "llm"
    legacy = _run(_pipe(False, [_block("bad1"), _block("good")], [LLMCallError("LLM дуудлага амжилтгүй: x", 503)]))
    assert legacy[0].error_stage == "exec"                   # анхдагч зам хэвээр


def test_strict_transient_clears_pre_repair_result():
    """L4: хоосон (ok) хувилбарын засвар түр зуурын алдаагаар тасарвал to_dict засварын өмнөх үр дүнг буцаахгүй."""
    c = _run(_pipe(True, [_block("empty1"), _block("good")], [LLMCallError("LLM дуудлага амжилтгүй: 503", 503)]))
    assert c[0].error_stage == "llm" and c[0].result is None and c[0].rows == 0 and c[0].result_hash is None
    ans = orch.Answer(question="q", variant="mn", run_id="X", model="m", candidates=[c[0]], result=c[0].result)
    assert "rows" not in ans.to_dict() and "columns" not in ans.to_dict()
    d = _run(_pipe(False, [_block("empty1"), _block("good")], [LLMCallError("LLM дуудлага амжилтгүй: 503", 503)]))
    assert d[0].ok and d[0].result is not None               # анхдагч: хоосон хувилбар хэвээр


def test_strict_bool_cols_failure_raises_through_generate():
    """L5: A7 (sqlite_transpile) + strict — boolean баганын жагсаалт уншигдахгүй бол асуулт дэд бүтцийн алдаа."""
    p = _pipe(True, [_block("good"), _block("good")], [], dialect="sqlite_transpile")
    p.executor = _Exec(bool_ok=False)
    with pytest.raises(orch.InfraError):
        _run(p)
    q = _pipe(False, [_block("good"), _block("good")], [], dialect="sqlite_transpile")
    q.executor = _Exec(bool_ok=False)
    assert all(c.ok for c in _run(q))                        # анхдагч: хоосон олонлогоор үргэлжилнэ


# ------------------------------------------------------------------ L6: хуваарилагч ба adaptive давалгаа
class _Raise:
    def __init__(self, err):
        self.err = err

    def chat(self, messages, **kw):
        raise self.err


def test_selector_failure_strict_transient_is_infra():
    with pytest.raises(orch.InfraError):
        _sel_pipe(_Raise(RuntimeError("LLM дуудлага амжилтгүй: timeout")), repair_strict=True)._select_glossary("q", TERMS)
    perm, tok = _sel_pipe(_Raise(LLMCallError("x", 400)), repair_strict=True)._select_glossary("q", TERMS)
    assert [g.id for g in perm] == ["g1", "g2", "g3", "g4"] and tok == (0, 0)    # тогтвортой — top-k хэвээр
    legacy, _ = _sel_pipe(_Raise(RuntimeError("timeout")))._select_glossary("q", TERMS)
    assert [g.id for g in legacy] == ["g1", "g2", "g3", "g4"]


class _Ret:
    glossary_terms: list = []

    def retrieve(self, question, variant, **kw):
        return SimpleNamespace(glossary=list(TERMS), linked_tables=[], values=[], examples=[], ms=0.0,
                               question_cyr=question, tables={})


def test_ask_marks_selector_infra_failure_as_llm_candidate():
    p = _sel_pipe(_Raise(RuntimeError("LLM дуудлага амжилтгүй: 503")), repair_strict=True)
    p.retriever, p.item_families, p.translations, p.intents, p.router = _Ret(), {}, {}, {}, None
    p.model_name = "m"
    ans = p.ask("Хэдэн зээл байна?", "mn", item_id="X1")
    assert ans.status == "error" and len(ans.candidates) == 1
    assert ans.candidates[0].error_stage == "llm" and "Хуваарилагч" in ans.candidates[0].error


class _WaveLLM:
    def __init__(self, err):
        self.err, self.calls = err, 0

    def chat(self, messages, *, n=1, **kw):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(["a", "b", "c", "d"][:n], 10 * n, 5 * n, 1.0, "mock")
        raise self.err


def _wave_pipe(err, strict, monkeypatch):
    monkeypatch.setattr(orch.Pipeline, "_decoding", lambda self, s: {"temperature": 0.8 if s else 0.0, "top_p": 1.0})
    p = orch.Pipeline.__new__(orch.Pipeline)
    p.opts = RunOptions(candidates=8, sampling=True, adaptive_stop="beta:0.8:w4", repair_strict=strict)
    p.llm, p.family, p.max_tokens = _WaveLLM(err), "omnisql", 256

    def single(messages, prefill, text, tin, tout, seed):
        return orch.Candidate(raw_sql=text, sql=text, ok=True, result_hash=text)
    p._single_turn = single
    return p.generate(None, 1, ([{"role": "user", "content": "q"}], None))


def test_adaptive_later_wave_failure(monkeypatch):
    transient = LLMCallError("LLM дуудлага амжилтгүй: timeout", None)
    assert len(_wave_pipe(transient, False, monkeypatch)) == 4                     # анхдагч: өмнөх давалгаагаар
    strict = _wave_pipe(transient, True, monkeypatch)
    assert len(strict) == 1 and strict[0].error_stage == "llm"
    assert len(_wave_pipe(LLMCallError("x", 400), True, monkeypatch)) == 4         # тогтвортой — анхдагчтай адил


# ------------------------------------------------------------------ L10: oracle harness
def _oracle_pipe(llm, monkeypatch, calls):
    monkeypatch.setattr(orch, "sqlite_to_postgres", lambda sql, bc: calls.append(sql) or f"{sql} /*pg*/")
    p = orch.Pipeline.__new__(orch.Pipeline)
    p.opts = RunOptions(dialect="sqlite_transpile", postproc=True)
    p.llm, p.executor, p.guard = llm, _Exec(), _Guard()
    p._postproc = SimpleNamespace(apply=lambda sql: (f"{sql} /*pp*/", ["r"]))
    return p


def test_oracle_gold_skips_transpile_and_postproc(monkeypatch):
    from mn_text2sql.eval.runner import OracleLLM
    gold = "SELECT count(*) FROM t2s.loans WHERE disbursement_date::date >= DATE '2026-01-01' AND tag_good"
    calls: list = []
    p = _oracle_pipe(OracleLLM("omnisql"), monkeypatch, calls)
    c = p.process_sql(gold)
    assert p.guard.seen == [gold] and calls == [] and c.ok
    calls2: list = []
    q = _oracle_pipe(MockLLM(lambda m: ""), monkeypatch, calls2)
    q.process_sql("SELECT 1 FROM t2s.loans WHERE good")
    assert calls2 and q.guard.seen == ["SELECT 1 FROM t2s.loans WHERE good /*pg*/ /*pp*/"]
