"""value_list_max (2026-11-02): цөөн утгатай текст баганын БҮХ утга DDL-д + «утгыг орчуулахгүй» заавар. Анхдагч (0) үед
prompt хуучинтай ижил. Test-ийн алдаа: утга prompt-д байсан ч загвар «'Darkhan'», «'Teacher'» гэж орчуулсан."""
from types import SimpleNamespace as NS

from mn_text2sql.knowledge.semantic import cached_model
from mn_text2sql.llm.prompts import VALUE_NOTE, PromptContext, build_omnisql, render_ddl
from mn_text2sql.pipeline.options import RunOptions
from mn_text2sql.pipeline.orchestrator import Pipeline
from mn_text2sql.retrieval.indexes import ValueEntry

MODEL = cached_model("knowledge/semantic_model.yaml")
TYPES = {"branches.branch_name": "text", "branches.province": "text", "branches.branch_code": "text"}
EX = {"branches": {"branch_name": ["Арвайхээр салбар", "Баянгол салбар", "Дархан салбар"]}}


def test_default_ddl_unchanged_and_full_values_listed():
    base = render_ddl(MODEL, ["branches"], TYPES, EX, True)
    assert base == render_ddl(MODEL, ["branches"], TYPES, EX, True, full_values=None)
    assert "e.g. Арвайхээр салбар, Баянгол салбар" in base
    fv = {"branches": {"branch_name": ["Дархан салбар", "Чойбалсан салбар", "Арвайхээр салбар"]}}
    text = render_ddl(MODEL, ["branches"], TYPES, EX, True, full_values=fv)
    assert "values: 'Дархан салбар', 'Чойбалсан салбар', 'Арвайхээр салбар'" in text
    assert "e.g. Арвайхээр салбар" not in text


def test_value_note_only_when_enabled():
    ctx = PromptContext(question="q", tables=["branches"], dialect="postgres_prompt")
    msgs, _ = build_omnisql(ctx, "CREATE TABLE branches (x TEXT);")
    assert VALUE_NOTE not in msgs[1]["content"]
    ctx.value_note = True
    msgs, _ = build_omnisql(ctx, "CREATE TABLE branches (x TEXT);")
    assert VALUE_NOTE in msgs[1]["content"]


def test_full_values_groups_and_limits():
    p = Pipeline.__new__(Pipeline)
    p.opts = RunOptions(value_list_max=2)
    p.retriever = NS(values=NS(entries=[
        ValueEntry("branches", "branch_name", "Дархан салбар", 3), ValueEntry("branches", "branch_name", "Эрдэнэт салбар", 5),
        ValueEntry("individuals", "occupation", "Багш"), ValueEntry("individuals", "occupation", "Эмч"),
        ValueEntry("individuals", "occupation", "Жолооч")]))
    out = p._full_values(2)
    assert out == {"branches": {"branch_name": ["Эрдэнэт салбар", "Дархан салбар"]}}   # 3 утгатай багана хасагдана
    assert p._full_values(2) is out                                                      # кэш
    assert RunOptions().value_list_max == 0
