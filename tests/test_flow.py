"""Flow builder (service/flow.py): тохиргоо ↔ flow хоёр тийш нэг утгатай, замын шалгалт."""
from dataclasses import fields

import pytest

from mn_text2sql.pipeline.options import RunOptions, load_experiments, resolve_run
from mn_text2sql.service.flow import NODES, compile_flow, decompose

SKIP = {"run_id", "desc", "group", "base", "compare_to", "models"}


def test_every_experiment_roundtrips():
    exp = load_experiments()
    for rid in exp["runs"]:
        o = resolve_run(rid, exp)
        res = compile_flow(decompose(o, exp), None, exp)
        want = {f.name: getattr(o, f.name) for f in fields(RunOptions) if f.name not in SKIP}
        got = {k: res["spec"][k] for k in want}
        assert got == want, rid
        assert compile_flow(decompose(o, exp), rid, exp)["diff"] == {}, rid


def test_every_option_belongs_to_a_node():
    owned = {k for n in NODES for k in n["fields"]} | {n["flag"] for n in NODES if n.get("flag")}
    assert {f.name for f in fields(RunOptions)} - owned - SKIP == set()


def _flow(*types):
    nodes = [{"id": t, "type": t, "params": {}} for t in types]
    return {"nodes": nodes, "edges": [{"from": a, "to": b} for a, b in zip(types, types[1:])]}


def test_adding_agent_nodes_to_current_system_gives_k1():
    fl = decompose(resolve_run("I3t2"))
    path = [n["type"] for n in fl["nodes"]]
    i = path.index("postproc") if "postproc" in path else path.index("output")
    for t in ("critic", "observe"):
        fl["nodes"].append({"id": t, "type": t, "params": {}})
    order = path[:i] + ["observe", "critic"] + path[i:]
    fl["edges"] = [{"from": a, "to": b} for a, b in zip(order, order[1:])]
    res = compile_flow(fl, "I3t2")
    assert res["diff"] == {"agent": True}
    assert res["fingerprint"] == compile_flow(decompose(resolve_run("K1")), "K1")["fingerprint"]


def test_critic_without_agent_loop_is_rejected():
    with pytest.raises(ValueError):
        compile_flow(_flow("input", "writer", "critic", "output"))


def test_disconnected_and_misordered_nodes_warn():
    fl = _flow("input", "writer", "translate", "output")
    fl["nodes"].append({"id": "vote", "type": "vote", "params": {"candidates": 8}})
    res = compile_flow(fl)
    assert res["spec"]["translate"] is True and res["spec"]["candidates"] == 1
    assert any("тооцохгүй" in w for w in res["warnings"]) and any("дараалал" in w for w in res["warnings"])


def test_missing_writer_or_broken_path_is_rejected():
    with pytest.raises(ValueError):
        compile_flow(_flow("input", "translate", "output"))
    fl = _flow("input", "writer", "output")
    fl["edges"] = fl["edges"][:1]
    with pytest.raises(ValueError):
        compile_flow(fl)
