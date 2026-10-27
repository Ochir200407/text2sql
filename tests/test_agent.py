"""G бүлэг (pipeline/agent.py): tool-ийн олдвор → засвар, critic, муу засварыг үгүйсгэх."""

import pytest

pytestmark = pytest.mark.db

WRONG = "SELECT count(*) FROM t2s.loan_applications WHERE channel = 'Mobile app'"
RIGHT = "SELECT count(*) FROM t2s.loan_applications WHERE channel = 'MOBILE'"
BROKEN = "SELECT count(*) FROM t2s.loan_applications WHERE chanel = 'MOBILE'"


def _pipe(cfg, responder, **opts):
    from mn_text2sql.llm.client import MockLLM
    from mn_text2sql.pipeline.options import resolve_run
    from mn_text2sql.pipeline.orchestrator import Pipeline
    o = resolve_run("A6", overrides={"agent": True, **opts})
    return Pipeline(cfg, o, "arctic-r1-7b", MockLLM(responder))


def _sql(s):
    return f"<answer>\n```sql\n{s}\n```\n</answer>"


def test_literal_tool_drives_revision(cfg):
    seen = {"critic": 0}

    def respond(msgs):
        text = msgs[-1]["content"]
        if "careful reviewer" in text:
            seen["critic"] += 1
            return "Looks fine.\nVERDICT: OK"
        if "Review of your query" in text:
            assert "Stored values of loan_applications.channel include" in text and "'MOBILE'" in text
            return _sql(RIGHT)
        return _sql(WRONG)

    ans = _pipe(cfg, respond).ask("Мобайл аппаар хэдэн зээлийн хүсэлт ирсэн бэ?")
    chosen = ans.candidates[0]
    assert "'MOBILE'" in ans.sql and ans.status == "answered"
    assert [s["round"] for s in chosen.agent_log] == [0, 1]
    assert any("No row has loan_applications.channel" in f for f in chosen.agent_log[0]["findings"])
    assert chosen.agent_log[1]["findings"] == [] and seen["critic"] == 2


def test_critic_revise_and_worse_revision_rejected(cfg):
    def respond(msgs):
        text = msgs[-1]["content"]
        if "careful reviewer" in text:
            return "VERDICT: REVISE - count only approved applications"
        if "Review of your query" in text:
            assert "Reviewer: count only approved applications" in text
            return _sql(BROKEN)
        return _sql(RIGHT)

    ans = _pipe(cfg, respond).ask("Мобайл аппаар хэдэн зээлийн хүсэлт ирсэн бэ?")
    log = ans.candidates[0].agent_log
    assert "'MOBILE'" in ans.sql and "chanel" not in ans.sql      # ажиллахгүй засвар хадгалагдахгүй
    assert log[0]["verdict"] == "REVISE" and log[-1]["accepted"] is False


def test_no_critic_no_findings_single_step(cfg):
    calls = {"n": 0}

    def respond(msgs):
        calls["n"] += 1
        return _sql(RIGHT)

    ans = _pipe(cfg, respond, agent_critic=False).ask("Мобайл аппаар хэдэн зээлийн хүсэлт ирсэн бэ?")
    assert calls["n"] == 1 and len(ans.candidates[0].agent_log) == 1


def test_planner_goes_into_writer_prompt(cfg):
    def respond(msgs):
        text = msgs[-1]["content"]
        if "Do NOT write SQL" in text:
            return "- table loan_applications\n- filter channel = 'MOBILE'\n```sql\nSELECT 1\n```"
        if "careful reviewer" in text:
            return "VERDICT: OK"
        assert "Plan: (draft" in text and "SELECT 1" not in text
        return _sql(RIGHT)

    ans = _pipe(cfg, respond, agent_plan=True).ask("Мобайл аппаар хэдэн зээлийн хүсэлт ирсэн бэ?")
    assert "channel = 'MOBILE'" in ans.retrieval["plan"] and ans.status == "answered"


def test_empty_result_is_soft_without_critic(cfg):
    calls = {"n": 0}
    empty = "SELECT count(*) FROM t2s.loan_applications WHERE channel = 'MOBILE' GROUP BY channel HAVING count(*) < 0"

    def respond(msgs):
        calls["n"] += 1
        return _sql(empty)

    ans = _pipe(cfg, respond, agent_critic=False).ask("Мобайл аппаар хэдэн зээлийн хүсэлт ирсэн бэ?")
    log = ans.candidates[0].agent_log
    assert calls["n"] == 1 and len(log) == 1 and "0 rows" in log[0]["findings"][0]   # хоосон үр дүнг албадан засуулахгүй


def test_refusal_not_pushed_into_sql(cfg):
    calls = {"n": 0}

    def respond(msgs):
        calls["n"] += 1
        return "This question asks for personal data; I cannot answer it from the database."

    ans = _pipe(cfg, respond).ask("Харилцагч Батын утасны дугаар хэд вэ?")
    assert calls["n"] == 1 and ans.sql is None and len(ans.candidates[0].agent_log) == 1


def test_e1_repair_then_agent_keeps_full_conversation(cfg):
    """H1: E1-ийн засварын дараах revise нь system/схем/асуулттай бүрэн яриаг үргэлжлүүлнэ."""
    seen = {}

    def respond(msgs):
        text = msgs[-1]["content"]
        if "careful reviewer" in text:
            return "VERDICT: REVISE - count only mobile"
        if "Review of your query" in text:
            seen["roles"] = [m["role"] for m in msgs]
            seen["has_question"] = any("Мобайл" in (m.get("content") or "") for m in msgs)
            return _sql(RIGHT)
        if "The SQL query failed" in text:
            return _sql(WRONG)
        return _sql(BROKEN)

    ans = _pipe(cfg, respond, repair_rounds=1).ask("Мобайл аппаар хэдэн зээлийн хүсэлт ирсэн бэ?")
    assert seen["roles"][0] in ("system", "user") and seen["has_question"] and len(seen["roles"]) >= 5
    assert "'MOBILE'" in ans.sql


def test_literal_case_insensitive_and_soft_with_rows(cfg):
    """M1: lower(col) = 'mobile' нь худал олдвор биш; үр дүнтэй үед литерал олдвор зөөлөн (засуулахгүй)."""
    from mn_text2sql.pipeline.agent import AgentLoop
    pipe = _pipe(cfg, lambda m: _sql(RIGHT), agent_critic=False)
    loop = AgentLoop(pipe)
    assert loop.check_literals("SELECT count(*) FROM t2s.loan_applications WHERE lower(channel) = 'mobile'") == []
    f = loop.check_literals("SELECT count(*) FROM t2s.loan_applications WHERE channel IN ('MOBILE', 'Mobil')")
    assert f and not f[0].hard
    f = loop.check_literals(WRONG, has_rows=True)
    assert f and not f[0].hard


def test_literal_tool_hides_pii_values(cfg):
    """M2: хувийн мэдээллийн баганын хадгалсан утгыг санал болгохгүй."""
    from mn_text2sql.pipeline.agent import AgentLoop
    loop = AgentLoop(_pipe(cfg, lambda m: _sql(RIGHT)))
    f = loop.check_literals("SELECT * FROM t2s.customers WHERE customer_name = 'Байхгүй Нэр'")
    assert f and "personal data" in f[0].text and "include:" not in f[0].text


def test_critic_unparsed_recorded(cfg):
    def respond(msgs):
        if "careful reviewer" in msgs[-1]["content"]:
            return "<think>the query looks ok I think"            # дуусаагүй think, VERDICT-гүй
        return _sql(RIGHT)

    ans = _pipe(cfg, respond).ask("Мобайл аппаар хэдэн зээлийн хүсэлт ирсэн бэ?")
    assert ans.candidates[0].agent_log[0]["verdict"] == "UNPARSED" and "'MOBILE'" in ans.sql


def test_sqrl_revise_runs_exploration_then_answer(cfg):
    """M3: sqrl revise-д <sql> судлах алхмыг гүйцэтгэж, дараа нь <answer>-ийг авна."""
    from mn_text2sql.llm.client import MockLLM
    from mn_text2sql.pipeline.options import resolve_run
    from mn_text2sql.pipeline.orchestrator import Pipeline
    state = {"rev": 0}

    def respond(msgs):
        text = msgs[-1]["content"]
        if "careful reviewer" in text:
            return "VERDICT: OK"
        if "This was your final answer" in text:
            state["rev"] += 1
            return "<sql> SELECT DISTINCT channel FROM t2s.loan_applications </sql>"
        if state["rev"] and "MOBILE" in text:                    # судалгааны үр дүн ирсний дараа
            return f"<answer> {RIGHT} </answer>"
        return f"<answer> {WRONG} </answer>"

    o = resolve_run("A6", overrides={"agent": True, "agent_max_steps": 0})
    ans = Pipeline(cfg, o, "sqrl-9b", MockLLM(respond)).ask("Мобайл аппаар хэдэн зээлийн хүсэлт ирсэн бэ?")
    assert "'MOBILE'" in ans.sql
    roles = [m["role"] for m in ans.candidates[0].messages]
    assert roles[-1] == "assistant" and roles.count("assistant") >= 3
