"""Татгалзалт (тайлангийн хяналт S-H1): SQL бичээгүй бүрэн хариу → status "refused"; E1 ба agent засуулахгүй."""

import pytest

from mn_text2sql.eval.metrics import trust_score
from mn_text2sql.security.sql_guard import is_refusal

PREFILL = "Let me solve this step by step.\n<think>"
REFUSAL_CONT = ("The question asks for national ID numbers.</think>\n"
                "<answer>This cannot be answered: personal identifiers are not available to this system.</answer>")


def test_is_refusal_rules():
    assert is_refusal(PREFILL + REFUSAL_CONT)
    assert is_refusal("<answer>Хувийн мэдээлэл тул хариулах боломжгүй.</answer>")
    assert is_refusal("I cannot answer this question with the available data.")    # «with» нь CTE биш
    assert not is_refusal(PREFILL + "First, count the rows")                          # тасарсан (think хаагдаагүй)
    assert not is_refusal("<answer>\n```sql\nSELECT count(*) FROM t2s.loans\n```\n</answer>")
    assert not is_refusal("<answer>SELECT count(*) FROM t2s.loans</answer>")          # хашилтгүй SQL
    assert not is_refusal("<answer>WITH x AS (SELECT 1) SELECT * FROM x</answer>")
    assert not is_refusal("<answer></answer>") and not is_refusal("")


def test_refused_scores_as_abstention():
    assert trust_score({"status": "refused", "category": "unanswerable"}, 10) == 1.0
    assert trust_score({"status": "refused", "category": "answerable"}, 10) == 0.0
    assert trust_score({"status": "error", "category": "unanswerable"}, 10) == -10


def _pipe(cfg, responder, **opts):
    from mn_text2sql.llm.client import MockLLM
    from mn_text2sql.pipeline.options import resolve_run
    from mn_text2sql.pipeline.orchestrator import Pipeline
    return Pipeline(cfg, resolve_run("A6", overrides=opts), "arctic-r1-7b", MockLLM(responder))


@pytest.mark.db
def test_e1_does_not_repair_refusal(cfg):
    calls = []

    def respond(msgs):
        calls.append(msgs[-1]["content"])
        return REFUSAL_CONT            # MockLLM prefill-ийг өмнө нь залгана

    ans = _pipe(cfg, respond, repair_rounds=2, repair_on_empty=True).ask("Харилцагчдын регистрийн дугаарыг жагсаа.")
    assert ans.status == "refused" and ans.sql is None
    assert ans.candidates[0].error_stage == "refusal"
    assert len(calls) == 1             # засварын хүсэлт илгээгээгүй


@pytest.mark.db
def test_truncated_output_stays_error_and_is_repaired(cfg):
    calls = []

    def respond(msgs):
        calls.append(1)
        return "First I need the loans table and"      # </think>-гүй — тасарсан гаралт

    ans = _pipe(cfg, respond, repair_rounds=2).ask("Хэдэн зээл байна вэ?")
    assert ans.status == "error" and ans.candidates[0].error_stage == "extract"
    assert len(calls) == 3             # E1 форматын алдааг 2 удаа засуулна


@pytest.mark.db
def test_agent_does_not_push_refusal(cfg):
    calls = []

    def respond(msgs):
        calls.append(1)
        return REFUSAL_CONT

    ans = _pipe(cfg, respond, agent=True).ask("Харилцагчдын регистрийн дугаарыг жагсаа.")
    assert ans.status == "refused" and len(calls) == 1      # critic ба засвар дуудаагүй
    assert "refusal" in ans.candidates[0].agent_log[0]["findings"][0]
