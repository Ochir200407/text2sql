"""DB-тэй бүрэн шалгалт (pytest -m db). Локал/серверийн PostgreSQL дээр db-build, index-build хийгдсэн байх ёстой."""

import pytest

pytestmark = pytest.mark.db


def test_pipeline_with_mock_llm(cfg):
    from mn_text2sql.llm.client import MockLLM
    from mn_text2sql.pipeline.options import resolve_run
    from mn_text2sql.pipeline.orchestrator import Pipeline
    sql = "SELECT count(*) FROM loans WHERE is_active_portfolio AND days_past_due > 0"
    for rid, model, reply in (("A6", "arctic-r1-7b", f"</think><answer>```sql\n{sql}\n```</answer>"),
                              ("A1", "sqrl-9b", f"ok\n<answer> {sql} </answer>")):
        p = Pipeline(cfg, resolve_run(rid), model, MockLLM(lambda m, r=reply: r))
        a = p.ask("Хугацаа хэтэрсэн (1+ хоног) зээл хэд вэ?")
        assert a.status == "answered" and a.result.ok and a.result.rows[0][0] > 0


def test_security_suite_all_blocked(cfg):
    from mn_text2sql.security.injection import run_security_suite
    res = run_security_suite(cfg)
    assert res["all_blocked"], (res["sql_failed"], res["prompt_failed"], res["rls"])


def test_knowledge_layer_consistent(cfg):
    from mn_text2sql.knowledge.check import check_knowledge
    assert check_knowledge(cfg)["errors"] == []


def test_oracle_run_scores_gold_as_correct(cfg, tmp_path):
    """Harness: gold SQL-ийг таамаг болгоход хариулах боломжтой бүх асуулт зөв гарах ёстой."""
    import json

    from mn_text2sql.config import resolve_path
    from mn_text2sql.eval.runner import run_experiment
    dev = resolve_path(cfg["eval"]["dev_file"])
    if not dev.exists():
        pytest.skip("evalset/dev.jsonl алга (evalset-build)")
    cfg = json.loads(json.dumps(cfg))
    from mn_text2sql.config import _to_config
    cfg = _to_config(cfg)
    cfg["eval"]["results_dir"] = str(tmp_path)
    out = run_experiment(cfg, "A6", model="arctic-r1-7b", split="dev", limit=25, workers=4, oracle=True)
    assert out and out[0]["ex"] == 100.0
