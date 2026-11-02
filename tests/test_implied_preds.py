"""T1 далд нөхцөл (implied_preds, M1ip): YAML-ын бүрэн бүтэн байдал, dev-ийн дэмжлэгийн тоо, сонголтын дүрэм,
анхдагч үед prompt өөрчлөгдөхгүй. Зөвхөн evalset/dev.jsonl-ийг уншина (test-ийн gold-ыг ХЭЗЭЭ Ч үгүй)."""

import json
import re
from dataclasses import fields

import pytest
import yaml

from mn_text2sql.config import resolve_path
from mn_text2sql.knowledge import table_hints as th
from mn_text2sql.llm.prompts import build_omnisql
from mn_text2sql.pipeline import orchestrator as orch
from mn_text2sql.pipeline.options import _LATE_DEFAULTS, RunOptions, load_experiments, resolve_run
from mn_text2sql.retrieval.retriever import RetrievalResult

TABLES = yaml.safe_load(resolve_path("knowledge/semantic_model.yaml").read_text(encoding="utf-8"))["tables"]
RAW = yaml.safe_load(resolve_path("knowledge/table_hints.yaml").read_text(encoding="utf-8"))["default_predicates"]
P = th.load_predicates()


def _dev() -> list[dict]:
    rows = [json.loads(x) for x in resolve_path("evalset/dev.jsonl").read_text(encoding="utf-8").splitlines()
            if x.strip()]
    assert rows and all(r["split"] == "dev" for r in rows)     # eval leakage-ээс сэргийлэх: зөвхөн dev
    return rows


# ------------------------------------------------------------------ YAML
def test_predicates_reference_real_columns():
    assert 3 <= len(P) <= 8
    for p in P:
        assert p.table in TABLES, p.table
        cols = set(TABLES[p.table]["columns"])
        for ident in re.findall(r"\b[a-z]+_[a-z_]+\b", p.hint + " " + p.sql):
            assert ident in cols or ident in TABLES, (p.table, ident)
        assert p.hint.startswith("Implied condition:") and p.hint.isascii() and len(p.hint) < 300


def test_dev_support_recorded_matches_dev_gold():
    """YAML-д бичсэн dev_support нь dev-ийн gold SQL-ээс дахин тооцсонтой яг тэнцүү; дүрэм бүр хүчтэй дэмжлэгтэй."""
    got = th.predicate_support(_dev())
    assert len(got) == len(RAW)
    for raw, s in zip(RAW, got, strict=True):
        want = raw["dev_support"]
        assert {k: s[k] for k in want} == want, (s["table"], s["sql"])
        assert s["fires"] >= 5 and s["fires_with_predicate"] / s["fires"] >= 0.9, s
        if not raw.get("when"):
            # нөхцөлгүй дүрэм: хүснэгтийн gold-ын олонхид (≥ 60%) нөхцөл байна
            assert s["with_predicate"] / s["table_queries"] >= 0.6, s


# ------------------------------------------------------------------ сонголт
def test_skip_keep_when_rules():
    got = th.implied_predicates(["loan_payments"], "How many repayments came in during May 2026?")
    assert len(got) == 1 and "loan_payments.status = 'POSTED'" in got[0]
    assert th.implied_predicates(["loan_payments"], "What share of payments failed by method?") == []
    # loans: disbursement → идэвхтэй багцыг алгасаж, «олгосон» дүрэм идэвхжинэ
    dis = th.implied_predicates(["loans"], "How many loans were disbursed in 2025?")
    assert len(dis) == 1 and "disbursement_date IS NOT NULL" in dis[0]
    # keep («active-portfolio») нь skip («disbursed»)-ийг давна
    both = th.implied_predicates(["loans"], "How many active-portfolio loans disbursed more than 90 days ago?")
    assert any("is_active_portfolio" in h for h in both) and any("disbursement_date" in h for h in both)
    assert th.implied_predicates(["loans"], "How many loans are pending disbursement?") == []
    assert th.implied_predicates(["deposits"], "How many deposits were withdrawn before maturity?") == []
    assert len(th.implied_predicates(["deposits"], "Total balance of deposits in MNT?")) == 1


def test_selection_tables_and_cap():
    preds = tuple(th.ImpliedPredicate(t, "x = 1", t, "x") for t in ("a", "b", "c", "d", "e"))
    # prompt-д байхгүй хүснэгт орохгүй; linked байхгүй бол prompt-ын хүснэгт
    assert th.implied_predicates(["t2s.a", "b"], "q", ["a", "c"], preds) == ["a"]
    assert th.implied_predicates(None, "q", ["c", "a"], preds) == ["a", "c"]       # файлын дарааллаар
    assert th.implied_predicates([], "q", [], preds) == []
    assert len(th.implied_predicates(list("abcde"), "q", None, preds)) == th.DEFAULT_PRED_CAP == 4


def test_bad_yaml_rejected(tmp_path):
    p = tmp_path / "h.yaml"
    p.write_text(yaml.safe_dump({"default_predicates": [{"table": "loans", "sql": "x", "hint": "h"}]}),
                 encoding="utf-8")
    with pytest.raises(ValueError):
        th.load_predicates(str(p))


# ------------------------------------------------------------------ тохиргоо
def test_option_default_and_fingerprint_unchanged():
    assert RunOptions().implied_preds is False and _LATE_DEFAULTS["implied_preds"] is False
    assert RunOptions().pick_diff is False and _LATE_DEFAULTS["pick_diff"] is False
    exp = load_experiments()
    for rid in exp["runs"]:
        o = resolve_run(rid, exp)
        fp = o.fingerprint()
        if not o.implied_preds:
            assert "implied_preds" not in fp, rid
        if not o.pick_diff:
            assert "pick_diff" not in fp, rid
    m, a = resolve_run("M1ip", exp), resolve_run("M1a", exp)
    assert m.implied_preds is True and m.base == "M1a"
    diff = {f.name for f in fields(RunOptions) if getattr(m, f.name) != getattr(a, f.name)}
    assert diff <= {"run_id", "desc", "group", "base", "implied_preds"}
    # M1ip-ийн тохиргоо бусад run-тай давхардахгүй (fingerprint ялгаатай)
    fps = {rid: json.dumps(resolve_run(rid, exp).fingerprint(), sort_keys=True, default=str) for rid in exp["runs"]}
    assert [r for r, f in fps.items() if f == fps["M1ip"]] == ["M1ip"]
    d = resolve_run("M1enspd", exp)
    assert d.pick_diff is True and d.base == "M1ensp"


# ------------------------------------------------------------------ build_context (DB/загваргүй)
class _Model:
    tables = {t: None for t in TABLES}
    notes_mn = []


class _Ret:
    def retrieve(self, *a, **kw):
        hit = type("V", (), {"table": "loans", "column": "status_code", "value": "ACTIVE", "score": 1.0})()
        return RetrievalResult(question="q", question_cyr="q", linked_tables=["loan_payments", "loans", "branches"],
                               values=[hit])


def _orch(translations=None, **opt):
    o = orch.Pipeline.__new__(orch.Pipeline)
    o.opts = RunOptions(values=True, dialect="postgres_prompt", **opt)
    o.retriever = _Ret()
    o.item_families, o.translations, o.intents = {}, translations or {}, {}
    o.model, o.cfg = _Model(), {"project": {"as_of_date": "2026-06-30"}, "retrieval": {"max_columns_per_table": 14}}
    return o


def test_default_off_is_byte_identical(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("implied_preds=False үед дуудагдах ёсгүй")
    monkeypatch.setattr(th, "implied_predicates", boom)
    ctx0, _ = _orch().build_context("How many repayments in May?", "mn", None)
    ctx1, _ = _orch(implied_preds=False).build_context("How many repayments in May?", "mn", None)
    assert ctx0.evidence_lines == ["Stored value 'ACTIVE' exists in loans.status_code"]
    assert ctx0 == ctx1 and build_omnisql(ctx0, "S") == build_omnisql(ctx1, "S")


def test_on_appends_after_values_and_uses_translation():
    ctx, _ = _orch(implied_preds=True).build_context("How many repayments came in during May?", "mn", None)
    ev = ctx.evidence_lines
    assert ev[0].startswith("Stored value") and any("status = 'POSTED'" in e for e in ev)
    msgs = build_omnisql(ctx, "S")[0][1]["content"]
    assert "Implied condition: payments received" in msgs
    # skip үгийг англи орчуулгаар шалгана (асуулт монгол, translate=True)
    o = _orch(translations={"P1:mn": "What share of payments failed?"}, implied_preds=True, translate=True)
    ctx2, _ = o.build_context("Төлбөрийн хэдэн хувь амжилтгүй болсон бэ?", "mn", "P1")
    assert not any("POSTED" in e for e in ctx2.evidence_lines)
