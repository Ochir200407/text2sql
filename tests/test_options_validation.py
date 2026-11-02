"""M3 (2026-10-31 шалгалт): үл мэдэгдэх --set/--var түлхүүр ба тогтсон утгатай талбарын буруу утга чимээгүй
алгасагдахгүй (`--set repair_strct=true` өмнө нь M1a-ийн файлыг үргэлжлүүлж хувилбар гэж мэдээлэгддэг байв)."""
from dataclasses import fields

import pytest

from mn_text2sql.llm.prompts import ENUM_CODE_MODES, GEN_STYLE_HINTS
from mn_text2sql.pipeline import intent_check, options
from mn_text2sql.pipeline.options import ENUM_OPTIONS, RunOptions, load_experiments, parse_set, resolve_run
from mn_text2sql.retrieval import fewshot_rerank, indexes, retriever
from mn_text2sql.service.flow import NODES

# scripts/**, config/*.env, docs-ийн тушаалд хэрэглэгдсэн --var хослолууд (fp_dump-ийн 4 хослол)
COMBOS = [{}, {"best_a": "A6", "best_config": "B5"}, {"best_a": "A7", "best_config": "H4"},
          {"best_a": "A7", "best_config": "B5"},
          {"best_a": "A7", "best_sft": "C3", "best_sft_adapter": "c3", "best_config": "D2"}]   # vast_eval_plus.sh


def test_unknown_set_key_raises():
    with pytest.raises(ValueError, match="repair_strct"):
        resolve_run("M1a", overrides=parse_set(["repair_strct=true"]))


def test_unknown_var_raises():
    with pytest.raises(ValueError, match="best_aa"):
        resolve_run("B5", var_overrides={"best_aa": "A7"})


@pytest.mark.parametrize("key,val", [("translate_mode", "en-prompt"), ("select_parse", "strict2"),
                                     ("intent_rules", "v3"), ("enum_codes", "All"), ("low_conf_action", "ask"),
                                     ("fewshot_rerank", "jaccard"), ("dialect", "postgres"), ("repair_strict", "yes")])
def test_invalid_enum_or_bool_value_raises(key, val):
    with pytest.raises(ValueError, match=key):
        resolve_run("M1a", overrides={key: val})


def test_parse_set_requires_equals():
    with pytest.raises(ValueError):
        parse_set(["repair_strict"])
    with pytest.raises(ValueError):
        parse_set(["=x"])
    assert parse_set(["adaptive_stop=beta:0.8:w4", "translate=true", "agent_max_steps=0"]) == \
        {"adaptive_stop": "beta:0.8:w4", "translate": True, "agent_max_steps": 0}


def test_every_config_resolves_under_used_var_combos():
    exp = load_experiments()
    for rid in exp["runs"]:
        for vo in COMBOS:
            resolve_run(rid, exp, var_overrides=vo)      # алдаагүй (fp_dump-ийн шалгалттай ижил)


@pytest.mark.parametrize("rid,over", [
    ("F1", {"adapter": "c3"}), ("C6", {"agent_max_steps": 0}), ("B5", {"index_dir": "build/index_thesis_traindev"}),
    ("D2", {"translate": True}), ("E2", {"adaptive_stop": "beta:0.8:w4"}), ("A1", {"intent_check": True}),
    ("A6", {"agent": True, "agent_max_steps": 0}), ("B5", {"pivot": False, "search_values_tool": False,
                                                          "candidates": 1, "repair_rounds": 0}),
    ("M1a", {"intent_rules": "v2", "repair_strict": True, "select_parse": "strict"}),
])
def test_existing_set_usages_still_resolve(rid, over):
    o = resolve_run(rid, overrides=over)
    assert all(getattr(o, k) == v for k, v in over.items())


def test_enum_values_match_consumers():
    assert ENUM_OPTIONS["enum_codes"] == tuple(ENUM_CODE_MODES)
    assert set(ENUM_OPTIONS["gen_style"]) == set(GEN_STYLE_HINTS)
    assert ENUM_OPTIONS["mask_mode"] == tuple(indexes.MASK_MODES)
    assert ENUM_OPTIONS["value_match"] == tuple(indexes.VALUE_MATCH_MODES)
    assert ENUM_OPTIONS["translit_vocab"] == tuple(retriever.TRANSLIT_VOCABS)
    assert ENUM_OPTIONS["fewshot_rerank"] == (None, *fewshot_rerank.MODES)
    assert ENUM_OPTIONS["intent_rules"] == tuple(intent_check.INTENT_RULES)
    from mn_text2sql.pipeline.selection import RULE_VERSIONS
    assert ENUM_OPTIONS["rules_version"] == tuple(RULE_VERSIONS)
    defaults = RunOptions()
    for k, allowed in ENUM_OPTIONS.items():
        assert getattr(defaults, k) in allowed, k
    for n in NODES:                                     # flow builder-ийн сонголт зөвшөөрөгдсөн утга л
        for k, meta in n["fields"].items():
            if meta["kind"] == "enum" and k in ENUM_OPTIONS:
                assert set(meta["options"]) <= set(ENUM_OPTIONS[k]), k


def test_validate_reports_all_bad_fields():
    o = RunOptions(run_id="X", translate_mode="nope", sampling="1")
    with pytest.raises(ValueError) as e:
        o.validate()
    assert "translate_mode" in str(e.value) and "sampling" in str(e.value)
    assert RunOptions().validate().run_id == "custom"
    assert {f.name for f in fields(RunOptions)} >= set(ENUM_OPTIONS)
    assert options.resolve_run("A1").validate() is not None
