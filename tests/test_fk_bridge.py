"""FK-гүүр (retrieval/fk_bridge.py) — DB, embedding, LLM-гүй."""

from mn_text2sql.knowledge.semantic import load_semantic_model
from mn_text2sql.pipeline.options import RunOptions, resolve_run
from mn_text2sql.retrieval.fk_bridge import BridgeParams, FKGraph, fk_expand
from mn_text2sql.retrieval.fk_bridge_eval import sql_tables

# loans, collaterals → customers (эцэг); loan_collaterals → loans, collaterals (холбоос хүснэгт)
REFS = {"customers": set(), "branches": set(), "loans": {"customers", "branches"},
        "collaterals": {"customers", "branches"}, "loan_collaterals": {"loans", "collaterals"},
        "loan_payments": {"loans", "customers"}, "accounts": {"customers"}, "transactions": {"accounts"},
        "exchange_rates": set()}
ONLY_BRIDGE = BridgeParams(hop_top=0)


def test_graph_is_undirected_and_ignores_self_fk():
    g = FKGraph({**REFS, "employees": {"employees", "branches"}})
    assert "loan_collaterals" in g.adj["loans"] and "loans" in g.adj["loan_collaterals"]
    assert "employees" not in g.adj["employees"]
    assert g.adj["exchange_rates"] == set()


def test_bridge_prefers_junction_over_shared_parent():
    g = FKGraph(REFS)
    # loans–collaterals: customers/branches (эцэг, торгуультай) биш loan_collaterals-аар холбоно
    assert g.best_path("loans", "collaterals", 2) == ["loans", "loan_collaterals", "collaterals"]
    out, added = fk_expand(g, ["loans", "collaterals", "customers"], 3, params=ONLY_BRIDGE)
    assert added == ["loan_collaterals"]
    assert out == ["loans", "collaterals", "customers", "loan_collaterals"]   # анхны эрэмбэ хэвээр


def test_no_change_when_adjacent_or_unreachable():
    g = FKGraph(REFS)
    assert fk_expand(g, ["loans", "customers"], 2, params=ONLY_BRIDGE) == (["loans", "customers"], [])
    # exchange_rates FK-гүй → зам байхгүй, юу ч нэмэхгүй
    assert fk_expand(g, ["loans", "exchange_rates"], 2, params=ONLY_BRIDGE)[1] == []
    assert fk_expand(g, [], 6) == ([], [])


def test_bridge_only_among_top_tables():
    g = FKGraph(REFS)
    # collaterals нь bridge_top=2-оос гадна → гүүр хэрэггүй
    p = BridgeParams(bridge_top=2, hop_top=0)
    assert fk_expand(g, ["loans", "customers", "collaterals"], 3, params=p)[1] == []


def test_max_path_limits_bridge_length():
    g = FKGraph(REFS)
    # transactions–loans: transactions → accounts → customers ← loans (3 ирмэг)
    assert fk_expand(g, ["transactions", "loans"], 2, params=BridgeParams(max_path=2, hop_top=0))[1] == []
    _, added = fk_expand(g, ["transactions", "loans"], 2, params=BridgeParams(max_path=3, hop_top=0, max_add=3))
    assert added == ["accounts", "customers"]


def test_hop_neighbours_use_scores_threshold_and_caps():
    g = FKGraph(REFS)
    scores = {"loans": 1.0, "loan_payments": 0.8, "loan_collaterals": 0.6, "branches": 0.2}
    p = BridgeParams(hop_top=1, hop_k=1, hop_min_score=0.5)
    assert fk_expand(g, ["loans"], 1, scores, p)[1] == ["loan_payments"]
    p2 = BridgeParams(hop_top=1, hop_k=5, hop_min_score=0.5, max_add=5)
    assert fk_expand(g, ["loans"], 1, scores, p2)[1] == ["loan_payments", "loan_collaterals"]   # branches < босго
    p3 = BridgeParams(hop_top=1, hop_k=5, hop_min_score=0.0, max_add=1)
    assert len(fk_expand(g, ["loans"], 1, scores, p3)[1]) == 1                                  # max_add


def test_core_k_excludes_hub_extras():
    g = FKGraph(REFS)
    # core_k=1: «customers» нь link_tables-ийн хаб нэмэлт → гүүрийн анкер биш
    assert fk_expand(g, ["loans", "collaterals"], 1, params=ONLY_BRIDGE)[1] == []


def test_real_semantic_model_bridges_loans_collaterals():
    m = load_semantic_model("knowledge/semantic_model.yaml")
    g = FKGraph.from_model(m)
    linked = ["loans", "gl_entries", "collaterals", "loan_restructures", "products", "loan_dpd_history",
              "branches", "customers", "employees"]
    out, added = fk_expand(g, linked, 6, params=ONLY_BRIDGE)
    assert "loan_collaterals" in added and len(added) <= ONLY_BRIDGE.max_add
    assert out[:len(linked)] == linked


def test_sql_tables_ignores_cte_and_unknown():
    known = {"loans", "customers"}
    sql = ("WITH x AS (SELECT customer_id FROM t2s.loans) SELECT * FROM x JOIN t2s.customers c USING (customer_id) "
           "CROSS JOIN generate_series(1, 2)")
    assert sql_tables(sql, known) == {"loans", "customers"}


def test_option_default_off_and_m1fk_config():
    assert RunOptions().fk_bridge is False
    assert "fk_bridge" not in RunOptions().fingerprint()        # хуучин run-уудын hash өөрчлөгдөхгүй
    o = resolve_run("M1fk")
    base = resolve_run("M1a")
    assert o.fk_bridge is True and base.fk_bridge is False
    assert o.translation_cache == base.translation_cache and o.adapter == base.adapter
