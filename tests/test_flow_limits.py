"""3-р аудит (service/flow.py): C1 — замын тохиргоо серверийн мэдэлд; H1 — нэг ажлын хэмжээ, graph-ийн хэмжээ;
M1 — сервер flow-ийн adapter/dataset/translation_cache-ийг ашигладаггүйг анхааруулах; Pipeline-ийг нийтийн түгжээн
дор угсрахгүй. LLM/DB-гүй."""

import os
import threading
import time
from dataclasses import fields
from types import SimpleNamespace as NS

import pytest

from mn_text2sql.pipeline.options import RunOptions, load_experiments, resolve_run
from mn_text2sql.service import flow as F


def _flow(*types, **params):
    nodes = [{"id": t, "type": t, "params": dict(params.get(t) or {})} for t in types]
    return {"nodes": nodes, "edges": [{"from": a, "to": b} for a, b in zip(types, types[1:], strict=False)]}


R = ("input", "retrieve", "writer", "output")


# ---------------- C1: замын/нэрийн утга (compile_flow) ----------------

@pytest.mark.parametrize("field,value", [
    ("glossary_file", "/etc/passwd"), ("glossary_file", "/dev/zero"), ("glossary_file", "C:\\Windows\\win.ini"),
    ("glossary_file", "knowledge/../config/web.yaml"), ("glossary_file", "../glossary.yaml"),
    ("glossary_file", "~/glossary.yaml"), ("glossary_file", "knowledge/glossary.txt"),
    ("glossary_file", "config/experiments.yaml"), ("glossary_file", "knowledge//glossary.yaml"),
    ("glossary_file", "knowledge/./glossary.yaml"), ("glossary_file", "knowledge/glossary.yaml\0.yaml"),
    ("glossary_select_cache", "/proc/self/environ"), ("glossary_select_cache", "build/../../x.json"),
    ("glossary_select_cache", "build/rerank/x.yaml"), ("index_dir", "/tmp"), ("index_dir", "build/../.."),
    ("index_dir", "build"), ("index_dir", "knowledge/.."), ("index_dir", "build/index/"),
    ("glossary_file", {"a": 1}), ("glossary_file", "knowledge/" + "a" * 300 + ".yaml"),
])
def test_path_options_reject_unsafe(field, value):
    with pytest.raises(ValueError):
        F.compile_flow(_flow(*R, retrieve={field: value}))


def test_path_options_allowlisted_values_compile():
    res = F.compile_flow(_flow(*R, retrieve={"glossary_file": "knowledge/glossary_plus.yaml",
                                             "glossary_select_cache": "build/rerank/bge_ft_dev.json",
                                             "index_dir": "build/index_web"}))
    s = res["spec"]
    assert (s["glossary_file"], s["glossary_select_cache"], s["index_dir"]) == (
        "knowledge/glossary_plus.yaml", "build/rerank/bge_ft_dev.json", "build/index_web")


@pytest.mark.parametrize("node,params,ok", [
    ("translate", {"translation_cache": "qwen3.5-9b.t2"}, True),
    ("translate", {"translation_cache": "../../etc/passwd"}, False),
    ("translate", {"translation_cache": "a/b"}, False),
    ("writer", {"adapter": "c4_en30"}, True), ("writer", {"adapter": "/models/x"}, False),
    ("critic", {"agent_llm": "qwen3.5-9b"}, True), ("critic", {"agent_llm": "http://evil:1/v1"}, False),
    ("retrieve", {"fewshot_rerank": "table_jaccard"}, True), ("retrieve", {"fewshot_rerank": "bogus"}, False),
    ("vote", {"adaptive_stop": "beta:0.8:w4"}, True), ("vote", {"adaptive_stop": "nonsense"}, False),
    ("input", {"variant": "xx"}, False), ("input", {"dataset": {"a": 1}}, False), ("input", {"variant": "en"}, True),
    ("rules", {"abstain_threshold": "nan"}, False), ("rules", {"abstain_threshold": "inf"}, False),
    ("retrieve", {"fewshot_k": "1e9"}, False), ("retrieve", {"fewshot_k": [1]}, False),
])
def test_option_values_validated(node, params, ok):
    types = ["input", "translate", "retrieve", "writer", "output"] if node in ("translate", "retrieve") else \
        ["input", "rules", "writer", "output"] if node == "rules" else \
        ["input", "writer", "vote", "observe", "critic", "output"] if node in ("critic", "vote") else \
        ["input", "writer", "output"]
    fl = _flow(*types, **{node: params})
    if ok:
        spec = F.compile_flow(fl)["spec"]
        for k, v in params.items():
            assert spec[k] == v
    else:
        with pytest.raises(ValueError):
            F.compile_flow(fl)


# ---------------- H1: ажлын хэмжээ ба graph ----------------

def test_work_limits_clamped_with_warning():
    fl = _flow("input", "retrieve", "schema_link", "writer", "repair", "vote", "observe", "output",
               retrieve={"fewshot_k": -3, "glossary_k": 999, "select_pool": 10**9},
               schema_link={"top_tables": 10**12},
               writer={"agent_max_steps": 1000},
               repair={"repair_rounds": 99, "repair_workers": 64},
               vote={"candidates": 1000, "seeds": list(range(100))},
               observe={"agent_rounds": 50})
    res = F.compile_flow(fl)
    s = res["spec"]
    assert (s["candidates"], s["repair_rounds"], s["agent_rounds"], s["agent_max_steps"], s["repair_workers"]) == \
        (16, 5, 5, 8, 8)
    assert (s["fewshot_k"], s["glossary_k"], s["select_pool"], s["top_tables"]) == (0, 20, 50, 50)
    assert s["seeds"] == list(range(16))
    assert sum("мужаас гадуур" in w for w in res["warnings"]) == 9 and any("seeds" in w for w in res["warnings"])
    assert F.compile_flow(_flow("input", "rules", "writer", "output", rules={"abstain_threshold": 7}))[
        "spec"]["abstain_threshold"] == 1.0


def test_experiments_are_within_limits_and_compile_unchanged():
    """Flow-г experiments-ээс угсардаг тул тэдгээрийн бүх утга хязгаарын дотор — хумих/алдаа гарахгүй."""
    exp = load_experiments()
    for rid in exp["runs"]:
        for vo in ({}, {"best_a": "A6", "best_config": "B5"}, {"best_a": "A7", "best_config": "H4"}):
            o = resolve_run(rid, exp, var_overrides=vo)
            for k, (lo, hi) in F.LIMITS.items():
                assert lo <= getattr(o, k) <= hi, (rid, k, getattr(o, k))
            assert len(o.seeds) <= F.MAX_SEEDS
            res = F.compile_flow(F.decompose(o, exp), None, exp)
            assert not any("мужаас гадуур" in w or "хумив" in w for w in res["warnings"]), (rid, res["warnings"])
            want = {f.name: getattr(o, f.name) for f in fields(RunOptions)
                    if f.name not in {"run_id", "desc", "group", "base", "compare_to", "models"}}
            assert {k: res["spec"][k] for k in want} == want, rid


@pytest.mark.parametrize("flow", [
    [], "x", {"nodes": {}, "edges": []}, {"nodes": [], "edges": "a"},
    {"nodes": [{"id": i, "type": "writer"} for i in range(F.MAX_NODES + 1)], "edges": []},
    {"nodes": [], "edges": [{"from": "a", "to": "b"}] * (F.MAX_EDGES + 1)},
    {"nodes": [{"id": {"x": 1}, "type": "input"}], "edges": []},
    {"nodes": [{"id": "a", "type": "evil"}], "edges": []},
    {"nodes": [{"id": "a", "type": "writer", "params": [1, 2]}], "edges": []},
    {"nodes": [{"id": "a", "type": "writer"}], "edges": [["a", "b"]]},
])
def test_graph_shape_and_size(flow):
    with pytest.raises(ValueError):
        F.compile_flow(flow)


# ---------------- C1: серверийн талын шалгалт (_server_path, _vet_paths) ----------------

def _tree(root):
    (root / "knowledge").mkdir()
    (root / "build" / "idx").mkdir(parents=True)
    (root / "knowledge" / "g.yaml").write_text("terms: []\n", encoding="utf-8")
    (root / "knowledge" / "dir.yaml").mkdir()
    (root / "build" / "c.json").write_text("{}", encoding="utf-8")
    for f in F.INDEX_FILES:
        (root / "build" / "idx" / f).write_text("{}", encoding="utf-8")
    (root / "build" / "half").mkdir()
    (root / "build" / "half" / "meta.json").write_text("{}", encoding="utf-8")


def test_server_path_checks(tmp_path, monkeypatch):
    _tree(tmp_path)
    sp = lambda n, v: F._server_path(n, v, root=tmp_path)   # noqa: E731
    assert sp("glossary_file", "knowledge/g.yaml") == (tmp_path / "knowledge" / "g.yaml").resolve()
    assert sp("glossary_select_cache", "build/c.json") is not None
    assert sp("index_dir", "build/idx") is not None
    assert sp("glossary_file", "knowledge/missing.yaml") is None          # байхгүй
    assert sp("glossary_file", "knowledge/dir.yaml") is None              # энгийн файл биш
    assert sp("index_dir", "build/half") is None                          # Retriever-ийн файлууд дутуу
    assert sp("index_dir", "build/c.json") is None                        # хавтас биш
    assert sp("glossary_file", str(tmp_path / "knowledge" / "g.yaml")) is None   # абсолют
    monkeypatch.setitem(F.PATH_FIELDS, "glossary_file", ((".yaml",), 5))  # хэмжээний дээд хязгаар
    assert sp("glossary_file", "knowledge/g.yaml") is None


def test_server_path_rejects_symlink_escape(tmp_path):
    _tree(tmp_path)
    outside = tmp_path.parent / f"outside_{os.getpid()}.yaml"
    outside.write_text("terms: []\n", encoding="utf-8")
    try:
        try:
            (tmp_path / "knowledge" / "link.yaml").symlink_to(outside)
        except (OSError, NotImplementedError):
            pytest.skip("symlink үүсгэх эрхгүй (Windows)")
        assert F._server_path("glossary_file", "knowledge/link.yaml", root=tmp_path) is None
    finally:
        outside.unlink()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO (POSIX) алга")
def test_server_path_rejects_fifo(tmp_path):
    _tree(tmp_path)
    os.mkfifo(tmp_path / "knowledge" / "pipe.yaml")
    assert F._server_path("glossary_file", "knowledge/pipe.yaml", root=tmp_path) is None   # уншиж гацахгүй


def test_vet_paths_falls_back_with_notes():
    o = RunOptions(glossary_file="knowledge/missing_x.yaml", glossary_select_cache="build/rerank/missing_x.json",
                   index_dir="build/missing_index_x")
    notes = []
    F._vet_paths(o, notes, "{idx} индекс алга")
    assert (o.glossary_file, o.glossary_select_cache, o.index_dir) == (None, None, None)
    assert len(notes) == 3 and notes[0] == "build/missing_index_x индекс алга"
    ok = RunOptions(glossary_file="knowledge/glossary_plus.yaml")
    F._vet_paths(ok, notes, "{idx}")
    assert ok.glossary_file == "knowledge/glossary_plus.yaml" and len(notes) == 3


# ---------------- C1: Pipeline-ийг нийтийн түгжээн дор угсрахгүй ----------------

def test_pipeline_build_does_not_hold_global_lock(monkeypatch):
    runner = F.FlowRunner({"knowledge": {"index_dir": "build/index"}}, "arctic-r1-7b", NS(name="arctic-r1-7b"),
                          None, None)
    started, release, builds = threading.Event(), threading.Event(), []

    def slow_build(spec, fp):
        builds.append(fp)
        started.set()
        release.wait(5)
        return NS(fp=fp, flow_notes=[])
    monkeypatch.setattr(runner, "_build", slow_build)
    runner.pipes["cached"] = NS(fp="cached")
    t1 = threading.Thread(target=lambda: runner.pipeline({}, "slow"))
    t2 = threading.Thread(target=lambda: runner.pipeline({}, "slow"))   # ижил тохиргоо — хүлээгээд кэшээс
    t1.start()
    assert started.wait(5)
    t2.start()
    t0 = time.perf_counter()
    assert runner.pipeline({}, "cached").fp == "cached"                  # угсралт явж байхад кэш шууд
    assert time.perf_counter() - t0 < 1.0
    assert not runner.lock.locked()
    release.set()
    t1.join(5)
    t2.join(5)
    assert builds == ["slow"] and runner.pipes["slow"].fp == "slow"
    for i in range(6):                                                  # ≤ MAX_PIPES
        runner.pipes[f"x{i}"] = NS()
    runner.pipeline({}, "new")
    assert len(runner.pipes) <= F.FlowRunner.MAX_PIPES and "new" in runner.pipes


# ---------------- M1: серверийн загвар/өгөгдөл/орчуулга ----------------

def test_served_notes_warn_on_mismatch():
    r = F.FlowRunner({}, "arctic-r1-7b", NS(name="d2"), None, None, dataset="nbfi")
    assert r.served_model() == "d2"
    notes = r.served_notes({"adapter": None, "dataset": "nbfi"})
    assert len(notes) == 1 and "«arctic-r1-7b»" in notes[0] and "«d2»" in notes[0]
    assert r.served_notes({"adapter": "d2", "dataset": "nbfi"}) == []
    notes = r.served_notes({"adapter": "d2", "dataset": "bird_mn", "translate": True, "translation_cache": "q.t2"})
    assert len(notes) == 2 and "bird_mn" in notes[0] and "q.t2" in notes[1]
    r.label = "ДЕМО"
    assert r.served_notes({"adapter": "x", "dataset": "bird_mn"}) == []


def _answer():
    from mn_text2sql.db.executor import ExecResult
    from mn_text2sql.pipeline.orchestrator import Answer, Candidate
    res = ExecResult(True, ["n"], [(5,)], 1)
    cand = Candidate(sql="SELECT 5", ok=True, rows=1, tables=["loans"], result=res)
    return Answer(question="q", variant="mn", run_id="x", model="m", status="answered", sql=cand.sql, result=res,
                  candidates=[cand], confidence=1.0, unanimous=True, chosen_index=0)


@pytest.mark.parametrize("shadow", [False, True])
def test_run_records_served_model_and_warns(monkeypatch, shadow):
    r = F.FlowRunner({"security": {"enforce_limit": 1000}, "db": {}, "knowledge": {}}, "arctic-r1-7b",
                     NS(name="d2"), None, None)
    pipe = NS(ask=lambda q, v, item_id=None: _answer(), translations={}, retriever=None, model=None, flow_notes=[])
    monkeypatch.setattr(r, "pipeline", lambda spec, fp: pipe)
    out = r.run(_flow("input", "writer", "output", writer={"adapter": "c3"}), "Зээлийн тоо", shadow=shadow)
    assert out["_audit"]["model"] == "d2" and out["_audit"]["flow_adapter"] == "c3"
    if not shadow:
        assert any("«c3»" in w and "«d2»" in w for w in out["warnings"])
        # prompt-ын харагдац унасан ч (хуурамч pipe) алдааны текст биш ерөнхий мессеж
        assert "AttributeError" in out["inspect"]["error"] and "prompt_for" not in out["inspect"]["error"]
