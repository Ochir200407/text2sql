from mn_text2sql.eval.metrics import abstention_curve, mcnemar, summarize, trust_score
from mn_text2sql.llm.prompts import est_tokens, parse_sqrl_action, strip_code_fences
from mn_text2sql.pipeline.options import load_experiments, parse_set, resolve_run
from mn_text2sql.text.normalize import STYLE_VUW, script_of, strip_suffix, translit, weak_latin_keys


def test_translit_and_keys():
    assert translit("хүү", STYLE_VUW) == "hvv"
    assert script_of("Дархан salbar") == "mixed"
    assert weak_latin_keys("Дархан")[0] in weak_latin_keys("darkhan") + weak_latin_keys("darhan")
    assert strip_suffix("дарханы") == "дархан"


def test_every_experiment_resolves_and_differs_from_base():
    exp = load_experiments()
    a1 = resolve_run("A1", exp).fingerprint()
    for rid in exp["runs"]:
        o = resolve_run(rid, exp)
        assert o.models, rid
        if rid != "A1" and o.base:
            assert o.fingerprint() != resolve_run(o.base, exp).fingerprint() or o.dataset != "nbfi", rid
    assert resolve_run("B1", exp).dialect == resolve_run(exp["vars"]["best_a"], exp).dialect
    assert resolve_run("B1", exp, var_overrides={"best_a": "A7"}).dialect == "sqlite_transpile"
    assert a1["describe"] is False and resolve_run("B5", exp).fewshot_k == 4


def test_parse_set():
    assert parse_set(["fewshot_k=3", "describe=false", "abstain_threshold=0.5", "adapter=c1"]) == \
        {"fewshot_k": 3, "describe": False, "abstain_threshold": 0.5, "adapter": "c1"}


def test_sqrl_parsing_and_tokens():
    assert parse_sqrl_action("t</think> plan\n<sql> SELECT 1 </sql>") == ("sql", "SELECT 1")
    assert parse_sqrl_action("<answer>\n```sql\nSELECT 2\n```\n</answer>") == ("answer", "SELECT 2")
    assert strip_code_fences("```sql\nSELECT 3\n```") == "SELECT 3"
    assert est_tokens("абвг" * 100) > est_tokens("abcd" * 100)


def _rec(qid, correct, cat="answerable", status="answered", conf=None):
    return {"question_id": qid, "variant": "mn", "category": cat, "status": status, "correct": correct,
            "raw_correct": correct, "confidence": conf, "sql": "SELECT 1", "exec_ok": True}


def test_trust_and_summary():
    assert trust_score(_rec("a", True), 10) == 1
    assert trust_score(_rec("a", False), 10) == -10
    assert trust_score(_rec("a", False, status="abstained"), 10) == 0
    assert trust_score(_rec("u", False, cat="unanswerable"), 10) == -10
    assert trust_score(_rec("u", True, cat="unanswerable", status="abstained"), 10) == 1
    s = summarize([_rec("a", True), _rec("b", False), _rec("u", True, cat="unanswerable", status="abstained")])
    assert s["ex"] == 50.0 and s["n_unanswerable"] == 1 and s["unanswerable_detected_pct"] == 100.0


def test_mcnemar_and_curve():
    a = [_rec(str(i), i < 50) for i in range(100)]
    b = [_rec(str(i), i < 65) for i in range(100)]
    m = mcnemar(a, b)
    assert m["wins"] == 15 and m["losses"] == 0 and m["significant"]
    curve = abstention_curve([_rec(str(i), i % 2 == 0, conf=(1.0 if i % 2 == 0 else 0.25)) for i in range(10)])
    assert curve[-1]["selective_accuracy"] == 100.0
