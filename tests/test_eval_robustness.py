"""Үнэлгээний найдвартай байдлын засварууд (2026-10-31 шалгалт): M2 gold-ийн алдаа, L7 тасарсан JSONL, L1 татгалзалтын
муруй, L8 meta, F7 code_digest/sessions, L3 infra тоолуур, L4 pick, L5 симулятор, L11 чуулгын зардал, L10 oracle.
DB, загваргүй — Executor/Pipeline-ийг хуурамчаар солино."""

import json
import logging
from types import SimpleNamespace as NS

import psycopg2
import pytest

from mn_text2sql.config import load_config
from mn_text2sql.db.executor import ExecResult
from mn_text2sql.eval import adaptive as ad
from mn_text2sql.eval import goldcheck, runner
from mn_text2sql.eval.dataset import EvalItem
from mn_text2sql.eval.metrics import load_records, summarize
from mn_text2sql.eval.pick import parse_choice
from mn_text2sql.eval.reselect import ensemble_cost, load_extra_costs
from mn_text2sql.pipeline.options import RunOptions
from mn_text2sql.pipeline.orchestrator import Answer, Candidate

OK1 = ExecResult(True, ["n"], [(1,)], 1)
CLOSED = ExecResult(False, error="server closed the connection unexpectedly | This probably means the server "
                                 "terminated abnormally", error_code="db_error")
TIMEOUT = ExecResult(False, error="Хугацаа хэтэрлээ (statement_timeout 20000 ms): ERROR:  canceling statement due to "
                                  "statement timeout", error_code="timeout")
SQL_ERR = ExecResult(False, error='ERROR:  column "x" does not exist | LINE 1: SELECT x FROM t2s.loans',
                     error_code="db_error")


def _item(qid="Q1", category="answerable", gold="SELECT 1", **kw):
    return EvalItem(id=qid, family=qid, category=category, difficulty="easy", question_mn=f"асуулт {qid}",
                    question_latin=f"asuult {qid}", question_en=f"question {qid}", gold_sql=gold, **kw)


def _answer(res=OK1, status="answered", stage=None):
    c = Candidate(raw_sql="SELECT 1", sql="SELECT 1", ok=bool(res and res.ok), result=res, error_stage=stage,
                  result_hash="h1" if res is not None and res.ok else None,
                  error=None if res is None or res.ok else res.error)
    a = Answer(question="q", variant="mn", run_id="T", model="m", status=status, sql="SELECT 1", result=res,
               candidates=[c], chosen_index=0)
    return a


CFG = {"eval": {"float_tolerance": 0.01, "relative_tolerance": 1e-6}}


# ------------------------------------------------------------------ L7: тасарсан JSONL
def test_load_records_drops_only_truncated_last_line(tmp_path, caplog):
    p = tmp_path / "r.jsonl"
    full = json.dumps({"question_id": "a", "q": "асуулт"}, ensure_ascii=False).encode()
    partial = json.dumps({"question_id": "b", "q": "асуулт"}, ensure_ascii=False).encode()[:-9]   # олон байтын дунд
    p.write_bytes(full + b"\n" + partial)
    with caplog.at_level(logging.WARNING):
        assert [r["question_id"] for r in load_records(p)] == ["a"]
    assert "сүүлийн мөр" in caplog.text
    p.write_bytes(full + b"\r\n" + full + b"\r\n")                      # CRLF — хэвийн
    assert len(load_records(p)) == 2
    p.write_bytes(partial + b"\n" + full + b"\n")                        # дунд мөр эвдэрсэн — алдаа хэвээр
    with pytest.raises(ValueError):
        load_records(p)


def test_append_record_single_write_and_tail_repair(tmp_path):
    p = tmp_path / "m__mn__s0.jsonl"
    big = {"question_id": "a", "raw_output": "ж" * 20000}                 # > 8 KB
    runner.append_record(p, big)
    assert p.read_bytes().endswith(b"\n") and b"\r\n" not in p.read_bytes()
    good = p.read_bytes()
    # Kaggle kill: дараагийн бичлэг дундуураа тасарсан
    p.write_bytes(good + json.dumps({"question_id": "b", "x": "ү" * 50}, ensure_ascii=False).encode()[:30])
    assert runner.repair_jsonl_tail(p) > 0
    assert p.read_bytes() == good and (tmp_path / "m__mn__s0.jsonl.partial").exists()
    runner.append_record(p, {"question_id": "b"})
    assert [r["question_id"] for r in load_records(p)] == ["a", "b"]
    # бүтэн JSON боловч LF-гүй — зөвхөн LF нэмнэ (мөрийг хаяхгүй)
    p.write_bytes(p.read_bytes() + b'{"question_id": "c"}')
    assert runner.repair_jsonl_tail(p) == 0
    runner.append_record(p, {"question_id": "d"})
    assert [r["question_id"] for r in load_records(p)] == ["a", "b", "c", "d"]
    assert runner.repair_jsonl_tail(tmp_path / "missing.jsonl") == 0


# ------------------------------------------------------------------ L1: муруй summarize-ээр
def test_summarize_curve_counts_all_categories():
    base = {"variant": "mn", "n_candidates": 8, "unanimous": False}
    recs = [dict(base, question_id="a", category="answerable", status="answered", confidence=1.0, raw_correct=True,
                 correct=True, cand_correct=[True] * 8),
            dict(base, question_id="u", category="unanswerable", status="answered", confidence=0.25,
                 raw_correct=False, correct=False),
            dict(base, question_id="s", category="unanswerable", status="answered", confidence=1.0,   # 1 хувилбар
                 raw_correct=False, correct=False, n_candidates=1)]
    curve = {p["threshold"]: p for p in summarize(recs)["voting"]["abstention_curve"]}
    # u (олон хувилбартай, хариулах боломжгүй) муруйд орно: босго 0.5-аас дээш татгалзана (+1), босгогүй −10
    assert curve[0.5]["trust_c10"] == 1.0 and curve[0.0]["trust_c10"] == -4.5
    # coverage/selective нь зөвхөн хариулах боломжтойгоор хэвээр
    assert curve[0.5]["coverage"] == 100.0 and curve[0.5]["selective_accuracy"] == 100.0


def test_summarize_counts_gold_and_infra_errors_only_when_present():
    recs = [{"question_id": "a", "category": "answerable", "status": "answered", "correct": True}]
    s = summarize(recs)
    assert "gold_errors" not in s and "infra_errors" not in s           # хуучин summary-ийн бүтэц хэвээр
    recs += [{"question_id": "b", "category": "answerable", "status": "error", "correct": False, "gold_error": True},
             {"question_id": "c", "category": "answerable", "status": "error", "correct": False,
              "error_stage": "infra"}]
    s = summarize(recs)
    assert s["gold_errors"] == 1 and s["infra_errors"] == 1 and s["error_stages"]["infra"] == 1


# ------------------------------------------------------------------ M2: gold-ийн алдаа, холболтын алдаа
def test_connection_error_only_for_connection_level_messages():
    assert runner.connection_error(CLOSED)
    assert runner.connection_error(ExecResult(False, error="connection already closed", error_code="db_error"))
    assert runner.connection_error(ExecResult(False, error="FATAL:  terminating connection due to administrator "
                                                           "command", error_code="db_error"))
    assert runner.connection_error(ExecResult(False, error="OperationalError: could not connect to server",
                                              error_code="connection"))
    assert not runner.connection_error(SQL_ERR)
    assert not runner.connection_error(TIMEOUT)
    assert not runner.connection_error(OK1) and not runner.connection_error(None)
    # SQL-ийн алдаа литералаа давтсан ч (ERROR:) холболт биш; executor-ын «connection» (MemoryError г.м.) мессежгүй бол үгүй
    assert not runner.connection_error(ExecResult(False, error='ERROR:  invalid input syntax for type integer: '
                                                               '"could not connect"', error_code="db_error"))
    assert not runner.connection_error(ExecResult(False, error="", error_code="connection"))


class _Ex:
    def __init__(self, results):
        self.results, self.calls = list(results), []

    def run(self, sql, max_rows=None, explain=False):
        self.calls.append(sql)
        r = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(r, Exception):
            raise r
        return r


_GUARD = NS(check=lambda sql: NS(ok=not sql.startswith("DROP"), sql=sql, error="guard", tables=[]))


def test_gold_cache_does_not_cache_failures_and_retries_once():
    ex = _Ex([TIMEOUT, OK1])
    g = runner.GoldCache(_GUARD, ex)
    assert g.get("SELECT 1").ok and len(ex.calls) == 2                  # timeout → нэг удаа дахин
    assert g.get("SELECT 1").ok and len(ex.calls) == 2                  # амжилттайг кэшилнэ
    ex = _Ex([SQL_ERR])
    g = runner.GoldCache(_GUARD, ex)
    assert not g.get("SELECT 2").ok and len(ex.calls) == 1              # SQL-ийн алдааг дахин оролдохгүй
    assert not g.get("SELECT 2").ok and len(ex.calls) == 2              # кэшлээгүй — дараагийн асуулт дахин
    ex = _Ex([psycopg2.OperationalError("FATAL:  sorry, too many clients already")])
    res = runner.GoldCache(_GUARD, ex).get("SELECT 3")                  # getconn exception → холболтын алдаа (унахгүй)
    assert not res.ok and runner.connection_error(res) and len(ex.calls) == 2
    with pytest.raises(KeyError):                                       # кодын алдаа нуугдахгүй
        runner.GoldCache(_GUARD, _Ex([KeyError("bug")])).get("SELECT 4")
    ex = _Ex([OK1])
    g = runner.GoldCache(_GUARD, ex)
    assert g.get("DROP x") is None and g.get("DROP x") is None and not ex.calls   # guard-ийн татгалзал тогтмол


def test_score_answer_flags_gold_error():
    it = _item()
    ok_gold = runner.GoldCache(_GUARD, _Ex([OK1]))
    sc = runner.score_answer(CFG, it, _answer(), ok_gold)
    assert sc["correct"] and "gold_error" not in sc                     # хэвийн бичлэгийн бүтэц хэвээр
    sc = runner.score_answer(CFG, it, _answer(), runner.GoldCache(_GUARD, _Ex([TIMEOUT])))
    assert not sc["correct"] and sc["gold_error"] and sc["gold_error_code"] == "timeout"
    sc = runner.score_answer(CFG, it, _answer(), runner.GoldCache(_GUARD, _Ex([CLOSED])))
    assert sc["gold_error_code"] == "connection"
    sc = runner.score_answer(CFG, _item(gold="DROP t"), _answer(), ok_gold)
    assert sc["gold_error_code"] == "guard"


def test_infra_error_detects_llm_and_db_connection_only():
    assert runner.infra_error(_answer(stage="llm")).startswith("llm")
    assert runner.infra_error(_answer(CLOSED, status="error", stage="exec")).startswith("db connection")
    assert runner.infra_error(_answer(SQL_ERR, status="error", stage="exec")) is None   # загварын SQL-ийн алдаа
    assert runner.infra_error(_answer()) is None


def test_oracle_llm_marks_postgres_sql():
    """L10: orchestrator-ийн _oracle_gold() нь `llm.oracle is True` (эсвэл name == "oracle") үед A7-ийн хөрвүүлэлтийг алгасна."""
    o = runner.OracleLLM("omnisql")
    assert o.oracle is True and o.name == "oracle"
    from mn_text2sql.pipeline import orchestrator as orch
    if hasattr(orch.Pipeline, "_oracle_gold"):                          # orchestrator-ийн тал (өөр agent-ийн файл)
        p = orch.Pipeline.__new__(orch.Pipeline)
        p.llm = o
        assert p._oracle_gold()


# ------------------------------------------------------------------ F7: code_digest-ийн хүрээ
def _digest_tree(root):
    pkg = root / "src" / "mn_text2sql"
    for d in ("pipeline", "eval", "training"):
        (pkg / d).mkdir(parents=True)
    (root / "config").mkdir()
    (root / "knowledge").mkdir()
    files = {pkg / "config.py": b"A = 1\n", pkg / "pipeline" / "orchestrator.py": b"x = 1\n",
             pkg / "eval" / "compare.py": b"c = 1\n", pkg / "eval" / "report.py": b"r = 1\n",
             pkg / "eval" / "metrics.py": b"m = 1\n", pkg / "eval" / "reselect.py": b"s = 1\n",
             pkg / "training" / "sft.py": b"t = 1\n", root / "config" / "default.yaml": b"retrieval: {top_tables: 6}\n",
             root / "config" / "models.yaml": b"models: {}\n", root / "config" / "experiments.yaml": b"runs: {}\n",
             root / "config" / "training.yaml": b"lr: 1\n", root / "knowledge" / "glossary.yaml": b"a: 1\n",
             root / "knowledge" / "examples.jsonl": b'{"q": 1}\n'}
    for p, b in files.items():
        p.write_bytes(b)
    return files


@pytest.mark.parametrize("rel,affects", [
    ("config/default.yaml", True), ("config/models.yaml", True), ("knowledge/examples.jsonl", True),
    ("src/mn_text2sql/eval/compare.py", True), ("src/mn_text2sql/pipeline/orchestrator.py", True),
    ("src/mn_text2sql/eval/report.py", False), ("src/mn_text2sql/eval/metrics.py", False),
    ("src/mn_text2sql/eval/reselect.py", False), ("config/experiments.yaml", False),
    ("config/training.yaml", False), ("src/mn_text2sql/training/sft.py", False)])
def test_code_digest_covers_answer_affecting_files_only(tmp_path, rel, affects):
    _digest_tree(tmp_path)
    d0 = runner.code_digest(tmp_path)
    p = tmp_path / rel
    p.write_bytes(p.read_bytes().replace(b"\n", b"\r\n"))               # CRLF — ижил
    assert runner.code_digest(tmp_path) == d0
    p.write_bytes(p.read_bytes() + b"# changed\n")
    assert (runner.code_digest(tmp_path) != d0) is affects


def test_session_history_describes_rows():
    m1 = {"started": "t0", "finished": "t1", "code_digest": "c1", "prompt_version": "p1", "questions_this_run": 300,
          "code_version": {"commit": "abc"}}
    hist = runner.session_history(m1)                                    # хуучин (sessions-гүй) meta
    assert hist == [dict(runner.session_entry(m1))] and hist[0]["code_version"] == "abc"
    m2 = dict(m1, sessions=hist + [{"code_digest": "c2"}])
    assert [s["code_digest"] for s in runner.session_history(m2)] == ["c1", "c2"]
    assert runner.session_history(None, 5) == [{"unknown_session": True, "records_written": 5}]
    assert runner.session_history(None) == []


# ------------------------------------------------------------------ L8: meta
def test_file_digest_and_served_identity(tmp_path):
    a, b = tmp_path / "a.jsonl", tmp_path / "b.jsonl"
    a.write_bytes(b'{"x": 1}\n')
    b.write_bytes(b'{"x": 1}\r\n')
    assert runner.file_digest(a)["sha1"] == runner.file_digest(b)["sha1"]
    assert runner.file_digest(tmp_path / "none.jsonl")["exists"] is False and runner.file_digest(None) is None
    adp = tmp_path / "d2__arctic"
    adp.mkdir()
    (adp / "adapter_config.json").write_text('{"r": 32}', encoding="utf-8")
    (adp / "adapter_model.safetensors").write_bytes(b"\0" * 2048)
    (adp / "notes.md").write_text("x", encoding="utf-8")
    serving = {"server_models": [{"id": "d2", "root": str(adp), "parent": "arctic-r1-7b"}]}
    ident = runner.served_identity("d2", "d2", {"hf_id": "Snowflake/Arctic-Text2SQL-R1-7B"}, serving,
                                   max_hash_bytes=1024)
    assert ident["root"] == str(adp) and ident["parent"] == "arctic-r1-7b" and ident["adapter"] == "d2"
    assert set(ident["files"]) == {"adapter_config.json", "adapter_model.safetensors"}
    assert ident["files"]["adapter_config.json"]["sha1"] and ident["files"]["adapter_model.safetensors"]["sha1"] is None
    assert runner.served_identity("x", None, {}, None)["root"] is None


# ------------------------------------------------------------------ L3: infra тоолуур + бүтэн run (хуурамч DB/LLM)
class _FakePipe:
    """pipe.ask: асуулт бүрийн хариу MODE[id]-аар: ok | llm | dbconn | sqlerr."""
    MODE: dict = {}

    def __init__(self, cfg, opts, model, llm, **kw):
        self.llm, self.max_tokens, self.opts = llm, 64, opts

    def ask(self, q, variant, item_id=None, seed=None):
        mode = self.MODE.get(item_id, "ok")
        if mode == "llm":
            return _answer(None, status="error", stage="llm")
        if mode == "dbconn":
            return _answer(CLOSED, status="error", stage="exec")
        if mode == "sqlerr":
            return _answer(SQL_ERR, status="error", stage="exec")
        return _answer()


class _FakeExecutor:
    gold = OK1

    def __init__(self, *a, **kw):
        pass

    def run(self, sql, max_rows=None, explain=False):
        return OK1 if sql == "SELECT 1" else self.gold   # SELECT 1 — Q1-ийн gold ба infra_healthy-ийн шалгалт

    def close(self):
        pass


@pytest.fixture
def fake_run(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "Executor", _FakeExecutor)
    monkeypatch.setattr(runner, "bot_dsn", lambda cfg, dbname=None: "dsn")
    monkeypatch.setattr(runner, "make_guard", lambda cfg: _GUARD)
    monkeypatch.setattr(runner, "Pipeline", _FakePipe)
    monkeypatch.setattr(runner, "db_snapshot", lambda ex, cfg: {"fingerprint": "fp"})
    monkeypatch.setattr(runner, "init_value_maps", lambda ex: 0)
    monkeypatch.setattr(runner, "eval_set_version", lambda cfg, files: {"version": "t", "sha1": "s", "files": []})
    monkeypatch.setattr(runner, "model_revision", lambda mspec: {"hf_id": None, "revision": None})
    monkeypatch.setattr(runner, "code_version", lambda: {"commit": "test"})
    monkeypatch.setattr(runner, "serving_info", lambda *a, **kw: {"server_models": None})
    cfg = load_config()
    cfg["eval"]["results_dir"] = str(tmp_path)
    _FakePipe.MODE = {}
    _FakeExecutor.gold = OK1

    def run(items):
        return runner._run_model(cfg, RunOptions(run_id="TX"), "arctic-r1-7b", "dev", [], items, ["mn"], 2, False,
                                 "", None, False, llm=object())
    path = tmp_path / "runs" / "dev" / "TX" / "arctic-r1-7b__mn__s0.jsonl"
    return run, path, cfg


def test_infra_failures_capped_after_three_healthy_sessions(fake_run):
    run, path, _ = fake_run
    items = [_item("Q1"), _item("Q2", gold="SELECT 2"), _item("Q3", gold="SELECT 3")]
    _FakePipe.MODE = {"Q2": "llm", "Q3": "dbconn"}
    for n in (1, 2):
        run(items)
        assert {r["question_id"] for r in load_records(path)} == {"Q1"}      # дэд бүтцийн алдааг хадгалахгүй
        assert runner.load_attempts(path) == {"Q2": n, "Q3": n}
    meta = json.loads(path.with_suffix(".meta.json").read_text(encoding="utf-8"))
    assert meta["infra_unsaved"] == 2 and len(meta["sessions"]) == 2 and meta["served_identity"]["served"]
    run(items)                                                                # 3 дахь session — infra гэж бичнэ
    recs = {r["question_id"]: r for r in load_records(path)}
    assert set(recs) == {"Q1", "Q2", "Q3"}
    for q in ("Q2", "Q3"):
        assert recs[q]["error_stage"] == "infra" and recs[q]["status"] == "error" and not recs[q]["correct"]
        assert recs[q]["infra_attempts"] == 3 and recs[q]["opts_hash"] == recs["Q1"]["opts_hash"]
    summ = json.loads(path.with_suffix(".summary.json").read_text(encoding="utf-8"))
    assert summ["n"] == 3 and summ["infra_errors"] == 2
    meta_before = path.with_suffix(".meta.json").read_text(encoding="utf-8")
    run(items)                                                                # хийх зүйлгүй — meta хэвээр
    assert path.with_suffix(".meta.json").read_text(encoding="utf-8") == meta_before
    summ2 = json.loads(path.with_suffix(".summary.json").read_text(encoding="utf-8"))
    assert summ2.get("gpu_seconds_per_q") == summ.get("gpu_seconds_per_q") is not None


def test_infra_outage_not_counted_and_sql_errors_are_answers(fake_run, monkeypatch):
    run, path, _ = fake_run
    _FakePipe.MODE = {"Q2": "llm", "Q3": "sqlerr"}
    monkeypatch.setattr(runner, "infra_healthy", lambda *a, **kw: False)    # тасалдал — тоолохгүй
    run([_item("Q1"), _item("Q2", gold="SELECT 2"), _item("Q3", gold="SELECT 3")])
    recs = {r["question_id"]: r for r in load_records(path)}
    assert set(recs) == {"Q1", "Q3"} and recs["Q3"]["error_stage"] == "exec" and not recs["Q3"]["correct"]
    assert runner.load_attempts(path) == {}


def test_gold_failure_recorded_with_flag_and_gold_connection_is_infra(fake_run):
    run, path, _ = fake_run
    _FakeExecutor.gold = TIMEOUT
    run([_item("Q1", gold="SELECT 9")])
    rec = load_records(path)[0]
    assert not rec["correct"] and rec["gold_error"] and rec["gold_error_code"] == "timeout"
    assert json.loads(path.with_suffix(".summary.json").read_text(encoding="utf-8"))["gold_errors"] == 1
    _FakeExecutor.gold = CLOSED
    run([_item("Q1", gold="SELECT 9"), _item("Q2", gold="SELECT 8")])       # gold-ийн холболтын алдаа → infra
    assert [r["question_id"] for r in load_records(path)] == ["Q1"]
    assert runner.load_attempts(path) == {"Q2": 1}


def test_resume_repairs_truncated_tail(fake_run):
    run, path, _ = fake_run
    run([_item("Q1")])
    path.write_bytes(path.read_bytes() + b'{"question_id": "Q2", "status": "ans')   # kill
    run([_item("Q1"), _item("Q2", gold="SELECT 2")])
    assert [r["question_id"] for r in load_records(path)] == ["Q1", "Q2"]
    assert path.with_name(path.name + ".partial").exists()


# ------------------------------------------------------------------ L9 / M2: check-gold
def test_check_gold_uses_eval_cost_limit_and_warns_degenerate(monkeypatch):
    seen = {}
    res = {"SELECT 0": ExecResult(True, ["n", "m"], [(0, None)], 1),
           "SELECT 5": ExecResult(False, error="Асуулгын өртөг хэт өндөр", error_code="cost_too_high"),
           "SELECT 1": OK1}

    class Ex:
        def __init__(self, dsn, **kw):
            seen.update(kw)

        def run(self, sql, max_rows=None, explain=False):
            return res.get(sql, OK1)

        def close(self):
            pass
    cfg = load_config()
    monkeypatch.setattr(goldcheck, "Executor", Ex)
    monkeypatch.setattr(goldcheck, "bot_dsn", lambda cfg, dbname=None: "dsn")
    monkeypatch.setattr(goldcheck, "make_guard", lambda cfg: _GUARD)
    monkeypatch.setattr(goldcheck, "load_items", lambda files: [_item("Z", gold="SELECT 0"), _item("C", gold="SELECT 5"),
                                                                _item("K", gold="SELECT 1")])
    rep = goldcheck.check_gold_files(cfg, ["x"])
    assert seen["max_cost"] == float(cfg["db"]["max_explain_cost"])
    assert any(w["id"] == "Z" and "0/NULL" in w["warning"] for w in rep["warnings"])
    assert any(e["id"] == "C" and "өртөг" in e["error"] for e in rep["errors"])
    assert not any(x["id"] == "K" for x in rep["warnings"] + rep["errors"])


# ------------------------------------------------------------------ L4: pick
def test_parse_choice_ignores_answer_inside_reasoning_and_takes_last():
    assert parse_choice("<think>first guess <answer>A</answer></think><answer>B</answer>") == "B"
    assert parse_choice("<answer>A</answer> on reflection <answer>B</answer>") == "B"
    assert parse_choice("compare... <answer>A</answer> hmm </think>\n<answer>B</answer>") == "B"   # prefill-ийн <think>
    assert parse_choice("<think>long <answer>A</answer> ... (тасарсан)") is None                    # хаагдаагүй reasoning
    assert parse_choice("<think>a b</think><answer> B </answer>") == "B"


# ------------------------------------------------------------------ L5: симуляторын EX_нийт
def _ad_item(category, const=None, hashes=("a",) * 8, correct=(True,) * 8):
    return ad.Item(qid=category + str(const), category=category, const=const, hashes=list(hashes),
                   correct=list(correct), bad=frozenset())


def test_simulator_credits_categories_like_score_answer():
    full = ad.Policy("full", (8,))
    cases = [(_ad_item("ambiguous", "clarify"), True), (_ad_item("ambiguous", "refused"), False),
             (_ad_item("ambiguous"), True), (_ad_item("ambiguous", correct=(False,) * 8), False),
             (_ad_item("ambiguous", hashes=(None,) * 8, correct=(False,) * 8), False),   # abstain — буруу
             (_ad_item("unanswerable", "refused"), True), (_ad_item("unanswerable"), False),
             (_ad_item("unanswerable", hashes=(None,) * 8, correct=(False,) * 8), True)]
    for it, want in cases:
        assert ad.credit_other(it, ad.run_item(it, full)) is want, (it.category, it.const, it.hashes[0])
    res = ad.evaluate([it for it, _ in cases], full)
    assert res["ex_total"][0] == pytest.approx(100 * 4 / 8)


# ------------------------------------------------------------------ L11: чуулгын зардал
def test_ensemble_cost_sums_extra_runs(tmp_path):
    for rid, rows in {"H4": [{"question_id": "q1", "latency_ms": 10.5, "gen_ms": 8.0, "prompt_tokens": 100,
                              "completion_tokens": 20}],
                      "I3": [{"question_id": "q1", "latency_ms": 4.0, "gen_ms": None, "prompt_tokens": 50,
                              "completion_tokens": 5}]}.items():
        (tmp_path / rid).mkdir()
        (tmp_path / rid / "arctic-r1-7b__mn__s0.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows),
                                                                    encoding="utf-8")
    extra = load_extra_costs(tmp_path, ["H4", "I3"], "arctic-r1-7b__mn")
    assert extra["q1"] == {"latency_ms": 14.5, "gen_ms": None, "prompt_tokens": 150, "completion_tokens": 25}
    base = {"latency_ms": 100.0, "gen_ms": 90.0, "prompt_tokens": 1000, "completion_tokens": 200}
    assert ensemble_cost(base, extra["q1"]) == {"latency_ms": 114.5, "gen_ms": None, "prompt_tokens": 1150,
                                                "completion_tokens": 225}
    assert ensemble_cost(base, None) == base                              # нэмэлт run-д асуулт алга
