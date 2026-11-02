"""Хүснэгтийн ялгах зөвлөмж (table_hints, M1th): YAML-ын бүрэн бүтэн байдал, сонголтын дүрэм, анхдагч үед prompt өөрчлөгдөхгүй."""

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
H = th.load_hints()


# ------------------------------------------------------------------ YAML
def test_hints_reference_real_tables_and_columns():
    assert len([h for h in H if len(h.tables) == 2]) >= 10
    for h in H:
        assert 1 <= len(h.tables) <= 2 and all(t in TABLES for t in h.tables), h.tables
        # snake_case нэр бүр нь тухайн хүснэгтийн багана эсвэл хүснэгтийн нэр (semantic_model-д үндэслэсэн)
        cols = {c for t in h.tables for c in TABLES[t]["columns"]}
        for ident in re.findall(r"\b[a-z]+_[a-z_]+\b", h.hint):
            assert ident in cols or ident in TABLES, (h.tables, ident)
        assert len(h.hint) < 300 and h.hint.isascii()


def test_top_confusion_pairs_present():
    pairs = {frozenset(h.tables) for h in H}
    for a, b in [("loans", "loan_applications"), ("loan_writeoffs", "loan_payments"), ("deposits", "accounts"),
                 ("loan_schedule", "loan_payments")]:
        assert frozenset((a, b)) in pairs


# ------------------------------------------------------------------ сонголт
def test_selection_requires_prompt_and_linked_tables():
    hints = (th.TableHint(("loans", "loan_applications"), "A"), th.TableHint(("deposits", "accounts"), "B"),
             th.TableHint(("loan_payments",), "C"))
    # accounts prompt-д байхгүй → B орохгүй; loan_payments linked-д байхгүй → C орохгүй
    got = th.select_hints(["loans", "deposits"], ["t2s.loans", "t2s.loan_applications", "t2s.deposits", "loan_payments"],
                          hints)
    assert got == ["A"]
    # linked байхгүй (retrieval-гүй) → prompt-ын хүснэгтийг linked гэж үзнэ
    assert th.select_hints(None, ["loans", "loan_applications", "loan_payments"], hints) == ["A", "C"]
    assert th.select_hints([], [], hints) == []


def test_selection_ranking_and_cap():
    hints = tuple(th.TableHint((t,), t) for t in ("a", "b", "c", "d", "e", "f", "g"))
    prompt = list("abcdefgx")
    # хоёр хүснэгт хоёулаа linked → түрүүнд; дараа нь linked-ийн эрэмбээр; ≤ cap
    got = th.select_hints(["x", "g", "f", "e", "d", "c", "b", "a"], prompt, hints, cap=5)
    assert got == ["g", "f", "e", "d", "c"]
    assert len(th.select_hints(["x"] + list("abcdefg"), prompt, hints)) == th.DEFAULT_CAP == 5
    one_linked = th.select_hints(["a", "x", "z"], list("abx"), (th.TableHint(("a", "b"), "ab"),
                                                                   th.TableHint(("a", "x"), "ax")))
    assert one_linked == ["ax", "ab"]


def test_real_hints_for_typical_linked_set():
    linked = ["loans", "loan_payments", "loan_applications", "customers", "products"]
    got = th.select_hints(linked, list(TABLES))
    assert 1 <= len(got) <= 5
    assert any("loan_applications =" in g for g in got) and any("loan_payments" in g for g in got)


# ------------------------------------------------------------------ тохиргоо
def test_option_default_and_fingerprint_unchanged():
    assert RunOptions().table_hints is False and _LATE_DEFAULTS["table_hints"] is False
    exp = load_experiments()
    for rid in exp["runs"]:
        o = resolve_run(rid, exp)
        if not o.table_hints:
            assert "table_hints" not in o.fingerprint(), rid   # өмнөх run-уудын opts_hash хэвээр
    m = resolve_run("M1th", exp)
    a = resolve_run("M1a", exp)
    assert m.table_hints is True and m.base == "M1a"
    diff = {f.name for f in fields(RunOptions) if getattr(m, f.name) != getattr(a, f.name)}
    assert diff <= {"run_id", "desc", "group", "base", "table_hints"}


# ------------------------------------------------------------------ build_context (DB/загваргүй)
class _Model:
    tables = {t: None for t in TABLES}
    notes_mn = []


class _Ret:
    def __init__(self, linked):
        self.linked = linked

    def retrieve(self, *a, **kw):
        hit = type("V", (), {"table": "loans", "column": "status_code", "value": "ACTIVE", "score": 1.0})()
        return RetrievalResult(question="q", question_cyr="q", linked_tables=list(self.linked), values=[hit])


def _orch(**opt):
    o = orch.Pipeline.__new__(orch.Pipeline)
    o.opts = RunOptions(values=True, dialect="postgres_prompt", **opt)
    o.retriever = _Ret(["loans", "loan_applications", "deposits", "accounts"])
    o.item_families, o.translations, o.intents = {}, {}, {}
    o.model, o.cfg = _Model(), {"project": {"as_of_date": "2026-06-30"}, "retrieval": {"max_columns_per_table": 14}}
    return o


def test_default_off_is_byte_identical(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("table_hints=False үед зөвлөмж дуудагдах ёсгүй")
    monkeypatch.setattr(th, "select_hints", boom)
    ctx0, _ = _orch().build_context("How many loans?", "mn", None)
    ctx1, _ = _orch(table_hints=False).build_context("How many loans?", "mn", None)
    assert ctx0.evidence_lines == ["Stored value 'ACTIVE' exists in loans.status_code"]
    assert ctx0 == ctx1 and build_omnisql(ctx0, "S") == build_omnisql(ctx1, "S")


def test_on_appends_hints_after_values():
    ctx, _ = _orch(table_hints=True).build_context("How many loans?", "mn", None)
    ev = ctx.evidence_lines
    assert ev[0].startswith("Stored value") and 2 <= len(ev) <= 6
    assert any(e.startswith("loan_applications =") for e in ev) and any(e.startswith("deposits =") for e in ev)
    base, _ = _orch().build_context("How many loans?", "mn", None)
    msgs_on, msgs_off = build_omnisql(ctx, "S")[0][1]["content"], build_omnisql(base, "S")[0][1]["content"]
    assert msgs_on != msgs_off and "loan_applications = loan requests" in msgs_on


@pytest.mark.parametrize("bad", [{"hints": [{"tables": [], "hint": "x"}]}, {"hints": [{"tables": ["loans"]}]}])
def test_bad_yaml_rejected(tmp_path, bad):
    p = tmp_path / "h.yaml"
    p.write_text(yaml.safe_dump(bad), encoding="utf-8")
    with pytest.raises(ValueError):
        th.load_hints(str(p))
