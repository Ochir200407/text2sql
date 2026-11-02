"""synth_own (training/synth_own.py): DB-гүй тест — guard, parse, давхардалгүй, ойролцоо давхардлын шүүлт."""

import importlib.util
import json
import random
import re
from collections import Counter
from types import SimpleNamespace

import pytest
import sqlglot
from sqlglot import exp

from mn_text2sql.config import resolve_path
from mn_text2sql.eval.split import _cos, _vec
from mn_text2sql.text.normalize import is_cyrillic
from mn_text2sql.training import synth_own as so

SMALL = {k: 30 for k in so.PATTERNS}
# олон валюттай хүснэгтийн «түүхий» мөнгөн багана — хөрвүүлэлтгүй SUM хийх ёсгүй
RAW_MONEY = ("amount", "requested_amount", "approved_amount", "current_balance", "principal_amount", "market_value",
             "accepted_value", "alert_amount", "collected_amount", "outstanding_at_open", "promised_amount",
             "paid_amount", "waived_amount", "total_amount", "recovered_amount", "hold_amount", "fee_amount",
             "allocated_interest", "allocated_penalty", "allocated_principal", "interest_paid", "overdue_principal",
             "total_outstanding")


@pytest.fixture(scope="module")
def generated():
    rows, stats = so.generate(seed=11, quota=SMALL)
    return rows, stats


def test_fk_paths_declared_in_semantic_model():
    assert so.check_fk_paths() == []


def test_all_patterns_present_and_quota(generated):
    rows, _ = generated
    c = Counter(r["pattern"] for r in rows)
    assert set(c) == set(so.PATTERNS)
    assert all(v == SMALL[p] for p, v in c.items())


def test_guard_pass_rate(generated):
    rows, stats = generated
    guard = so.make_guard()
    ok = sum(guard.check(r["gold_sql"]).ok for r in rows)
    assert ok == len(rows)                       # гаргасан бүх мөр guard давна
    assert stats["guard_fail"] == {} or sum(stats["guard_fail"].values()) / sum(stats["attempts"].values()) < 0.02


def test_parse_validity(generated):
    rows, _ = generated
    for r in rows:
        stmts = [s for s in sqlglot.parse(r["gold_sql"], read="postgres") if s is not None]
        assert len(stmts) == 1 and isinstance(stmts[0], exp.Query), r["id"]
        assert stmts[0].sql(dialect="postgres")


def test_no_duplicates(generated):
    rows, _ = generated
    assert len({r["id"] for r in rows}) == len(rows)
    assert len({so.norm_sql(r["gold_sql"]) for r in rows}) == len(rows)
    assert len({r["question_mn"].lower() for r in rows}) == len(rows)


def test_row_schema_matches_examples(generated):
    rows, _ = generated
    ex_keys = set(json.loads(resolve_path("knowledge/examples.jsonl").read_text(encoding="utf-8").splitlines()[0]))
    for r in rows:
        assert ex_keys <= set(r), ex_keys - set(r)
        assert r["source"] == "synth_own" and r["needs_exec_check"] is True and r["pattern"] in so.PATTERNS
        assert r["category"] == "answerable" and r["difficulty"] in ("easy", "medium", "hard")


def test_question_scripts(generated):
    rows, _ = generated
    for r in rows:
        assert is_cyrillic(r["question_mn"]), r["question_mn"]
        assert not re.search(r"[а-яөүё]", r["question_latin"].lower()), r["question_latin"]
        assert r["question_en"] and not re.search(r"[а-яөүё]", r["question_en"].lower()), r["question_en"]


def test_pattern_shapes(generated):
    rows, _ = generated
    for r in rows:
        s = r["gold_sql"].upper()
        p = r["pattern"]
        if p == "ratio_nullif":
            assert "NULLIF(" in s
        if p == "lag_pop":
            assert "LAG(" in s and r["order_matters"]
        if p == "topk_ties":
            assert "RANK()" in s or "WITH TIES" in s
        if p == "join_fk":
            assert " JOIN " in s
        if p == "cte_multi":
            assert s.startswith("WITH ")
        if p == "multi_currency":
            assert "FX_TO_MNT" in s or "_MNT" in s or "EXCHANGE_RATES" in s


def test_multi_currency_rule(generated):
    """Олон валюттай дүнг хөрвүүлэлтгүй нийлбэрлэхгүй (анхны валютын багана зөвхөн amount_original-д)."""
    rows, _ = generated
    pat = re.compile(r"SUM\((?:COALESCE\()?\w+\.(" + "|".join(RAW_MONEY) + r")\b(?:, 0\))?\)", re.I)
    for r in rows:
        for m in pat.finditer(r["gold_sql"]):
            tail = r["gold_sql"][m.end():m.end() + 40]
            assert "AS amount_original" in tail, (r["id"], r["gold_sql"])


def test_enum_codes_come_from_semantic_model(generated):
    rows, _ = generated
    model = so.model()
    known = {v for t in model.tables.values() for c in t.columns.values() for v in c.values} | {"MNT", "USD"}
    for r in rows:
        assert set(r["enum_codes"]) <= known, r["enum_codes"]
        for code in r["enum_codes"]:
            assert f"'{code}'" in r["gold_sql"]


def test_ngram_index_matches_split_cos():
    rng = random.Random(0)
    words = ["зээл", "салбар", "хүсэлт", "дүн", "төгрөг", "харилцагч", "loan", "branch", "2025", "сар"]
    texts = [(f"e{i}", " ".join(rng.choice(words) for _ in range(6))) for i in range(40)]
    idx = so.NgramIndex(texts)
    for _ in range(20):
        q = " ".join(rng.choice(words) for _ in range(5))
        eid, s = idx.best(q)
        exp_best = max(_cos(_vec(q), _vec(t)) for _, t in texts)
        assert abs(s - exp_best) < 1e-9


def test_near_dup_filter_removes_eval_like_rows(generated):
    rows, _ = generated
    a, b, c = rows[0], rows[1], rows[2]
    eval_qs = [("X1", a["question_mn"] + " ?"), ("X2", "огт өөр асуулт байна")]
    eval_sqls = [("X3", so.norm_sql(b["gold_sql"])), ("X4", so.norm_sql(c["gold_sql"]).replace("desc", "asc"))]
    keep, dropped = so.near_dup_filter(rows, eval_qs, eval_sqls)
    ids = {d["id"] for d in dropped}
    assert a["id"] in ids and b["id"] in ids and c["id"] in ids
    assert len(keep) + len(dropped) == len(rows)
    assert all(set(d) == {"id", "pattern", "eval_id", "sim", "field"} for d in dropped)   # eval-ийн текст бичихгүй


def test_candidates_file_is_filtered():
    """build/synth_own/ гаралт: neardup_report дахь мөр candidates.jsonl-д үгүй; цөөн мөрийг дахин шалгана."""
    out = resolve_path("build/synth_own")
    cand, rep, st = out / "candidates.jsonl", out / "neardup_report.json", out / "stats.json"
    if not (cand.exists() and rep.exists() and st.exists()):
        pytest.skip("build/synth_own үүсээгүй (python scripts/synth/generate.py)")
    rows = [json.loads(x) for x in cand.read_text(encoding="utf-8").splitlines() if x.strip()]
    dropped = json.loads(rep.read_text(encoding="utf-8"))
    stats = json.loads(st.read_text(encoding="utf-8"))
    assert len(rows) + len(dropped) == stats["raw"] and stats["neardup_removed"] == len(dropped)
    assert not ({d["id"] for d in dropped} & {r["id"] for r in rows})
    if not resolve_path("evalset/dev.jsonl").exists():
        return
    qs, sqls = so.load_eval_texts()
    sample = random.Random(0).sample(rows, min(60, len(rows)))
    keep, again = so.near_dup_filter(sample, qs, sqls)
    assert again == [] and len(keep) == len(sample)


def _load_exec_validate():
    path = resolve_path("scripts/synth/exec_validate.py")
    spec = importlib.util.spec_from_file_location("exec_validate", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_exec_validate_degenerate_rules():
    ev = _load_exec_validate()

    def res(cols, rows, truncated=False):
        return SimpleNamespace(columns=cols, rows=rows, rowcount=len(rows), truncated=truncated)

    assert ev.degenerate({"pattern": "join_fk"}, res(["a", "v"], []), 100) == "empty"
    assert ev.degenerate({"pattern": "enum_filter"}, res(["n"], [(0,)]), 100) == "single_zero"
    assert ev.degenerate({"pattern": "ratio_nullif"}, res(["pct"], [(None,)]), 100) == "all_null"
    assert ev.degenerate({"pattern": "lag_pop"}, res(["p", "v"], [("2025-01-01", 3)]), 100) == "short_series"
    assert ev.degenerate({"pattern": "join_fk"}, res(["a", "v"], [("x", 1), ("y", 1)]), 100) == "constant_metric"
    assert ev.degenerate({"pattern": "join_fk"}, res(["a", "v"], [("x", 1)] * 5), 3) == "too_many_rows"
    assert ev.degenerate({"pattern": "join_fk"}, res(["a", "v"], [("x", 1), ("y", 2)]), 100) is None


def test_min_template_diversity():
    """Хэв маяг бүр ≥ 8 өөр монгол асуултын загвартай; давхар «нийт», буруу «төлөвөөр» гарахгүй."""
    rows, _ = so.generate(seed=7, quota={k: 150 for k in so.PATTERNS})
    counts = so.template_counts(rows)
    assert set(counts) == set(so.PATTERNS)
    assert all(v >= 8 for v in counts.values()), counts
    for r in rows:
        q = r["question_mn"].lower()
        assert "нийт нийт" not in q and "төлөвөөр" not in q and "тус бүрээр нь" not in q, r["question_mn"]
        assert "total total" not in r["question_en"].lower(), r["question_en"]


def test_mn_instr_case_forms():
    assert so.mn_instr("зээлийн төлөв") == "зээлийн төлвөөр"
    assert so.mn_instr("активын ангилал") == "активын ангиллаар"
    assert so.mn_instr("бичилтийн төрөл (шимтгэл/торгууль)") == "бичилтийн төрлөөр (шимтгэл/торгууль)"
    assert so.mn_instr("эрсдэлийн түвшин") == "эрсдэлийн түвшнээр"
