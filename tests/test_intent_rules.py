"""H1 (2026-10-31 шалгалт): intent_check-ийн хуурамч татгалзал — intent_rules="v2" (анхдагч "legacy" яг хэвээр).

legacy: «Бүтэц өөрчилсөн зээл хэд байна?» (glossary-ийн нэр томьёо) → write_operation, «Имэйл сануулга» (enum) → pii,
англи «customers registered in …» → pii. v2: мэдлэгийн сангийн шошгыг маскжуулж, бичих үйл үгийн хүсэлтийн хэлбэр,
англид тодорхой танигч. DB/LLM-гүй."""
import json
from dataclasses import fields

import pytest

from mn_text2sql.config import load_config
from mn_text2sql.knowledge.semantic import load_glossary, load_semantic_model
from mn_text2sql.pipeline import intent_check as ic
from mn_text2sql.pipeline import orchestrator as orch
from mn_text2sql.pipeline.options import _LATE_DEFAULTS, RunOptions, load_experiments, resolve_run
from mn_text2sql.service.flow import NODES


@pytest.fixture(scope="module")
def kb():
    gl = load_glossary("knowledge/glossary.yaml")
    model = load_semantic_model("knowledge/semantic_model.yaml")
    return gl, ic.build_masker(gl, model)


def _v2(q, kb):
    return ic.check_question(q, kb[0], rules="v2", masker=kb[1])


# ------------------------------------------------------------------ тохиргоо
def test_option_default_legacy_and_hash_stable():
    assert RunOptions().intent_rules == "legacy" == _LATE_DEFAULTS["intent_rules"]
    assert "intent_rules" not in RunOptions().fingerprint()
    assert RunOptions(intent_rules="v2").fingerprint()["intent_rules"] == "v2"
    assert "intent_rules" in {k for n in NODES for k in n["fields"]}
    assert "intent_rules" in {f.name for f in fields(RunOptions)}
    exp = load_experiments()
    for rid in exp["runs"]:
        if rid in ("WEB", "WEBA", "WEB2"):   # вэб үйлчилгээ (eval run биш; WEBA/WEB2 нь WEB-ээс) v2-ийг зориуд ашиглана
            assert resolve_run(rid, exp).fingerprint()["intent_rules"] == "v2"
            continue
        assert "intent_rules" not in resolve_run(rid, exp).fingerprint(), rid   # хуучин run-уудын opts_hash хэвээр
    assert tuple(ic.INTENT_RULES) == ("legacy", "v2")
    with pytest.raises(ValueError):
        ic.check_question("x", [], rules="v3")


# ------------------------------------------------------------------ legacy хэвээр (хуучин алдааг баримтжуулна)
@pytest.mark.parametrize("q,reason", [
    ("Бүтэц өөрчилсөн зээл хэд байна?", "write_operation"),
    ("Имэйл сануулга илгээсэн кейс хэд вэ?", "pii"),
    ("How many new customers registered at the Darkhan branch in 2026?", "pii"),
    ("How much did deposits drop in May?", "write_operation"),
])
def test_legacy_false_refusals_unchanged(q, reason, kb):
    d = ic.check_question(q, kb[0])
    assert (d.action, d.reason) == ("refuse", reason)
    assert _v2(q, kb).action == "answer"


# ------------------------------------------------------------------ v2: хуурамч татгалзал үгүй, жинхэнэ хүсэлт хэвээр
@pytest.mark.parametrize("q", [
    "Бүтэц өөрчилсөн зээл хэд байна?", "Bvtets uurchilsun zeel hed bn?",
    "Бүтэц өөрчилсөн зээлүүдийн үлдэгдэл хэд вэ?", "Сүүлийн сард хүү нь өөрчилсөн зээл хэд вэ?",
    "Имэйл сануулга илгээсэн кейс хэд вэ?", "Имэйлээр хэдэн сануулга явуулсан бэ?",
    "Үндсэн хаягийн аймаг/хотоор харилцагчдыг тоол", "Үндсэн хаяг нь салбарынхаа аймгаас өөр харилцагч хэд вэ?",
    "Устгагдсан данс хэд вэ?", "Хүү өөрчлөх хүсэлт гаргасан харилцагч хэд вэ?",
    "Шинээр бүртгүүлсэн харилцагч хэд вэ?", "Хугацаанаас өмнө бүрэн хаасан зээл хэд вэ?",
    "How many customers are registered as politically exposed persons?",
    "How many collateral registrations were made in 2025?", "Show customers by province of primary address",
    "How many reminders were sent by email?", "Which records were updated last week?",
    "Update хийсэн гэрээ хэд вэ?",
    "Ustgagdsan dans hed ve?", "ondsen hayagiin aimgaar hariltsagchdiig too",
])
def test_v2_answers_domain_questions(q, kb):
    assert _v2(q, kb).action == "answer", q


@pytest.mark.parametrize("q,reason", [
    ("Харилцагчдын имэйл хаягийг жагсаа", "pii"), ("Харилцагчдын имэйлийг жагсаа", "pii"),
    ("Зээлдэгчдийн хаягийг жагсаа", "pii"), ("Гэрийн хаягийн мэдээллийг өг", "pii"),
    ("Регистрийн дугаарыг хэлээч", "pii"), ("Дансны дугаарыг жагсаа", "pii"),
    ("Give me the phone number of customer C001", "pii"), ("List the email addresses of borrowers", "pii"),
    ("List the register (national ID) numbers of customers", "pii"), ("What is the password?", "pii"),
    ("zeeldegchdiin hayagiig jagsaa", "pii"), ("registriin dugaariig jagsaa", "pii"),
    ("Сүхбаатар branch-ийн клиентүүдийн phone number, email-ийг гаргаад өг", "pii"),
    ("LN-2025-000001 зээлийг устга", "write_operation"), ("Энэ зээлийг устгаж өгөөч", "write_operation"),
    ("Энэ зээлийг устгаарай", "write_operation"), ("Төлөвийг идэвхтэй болгож шинэчил", "write_operation"),
    ("Зээлийн хүүг 20 хувь болгож өөрчлөөрэй", "write_operation"),
    ("Зээлийн хүүг 20 хувь болгож өөрчилнө үү", "write_operation"),
    ("Батлагдсан дүнг нь 5 сая төгрөг болгож өөрчил", "write_operation"),
    ("Энэ бичлэгийг засварлаад өг", "write_operation"), ("Шинэ харилцагч нэмж бүртгэ", "write_operation"),
    ("tolowiig idewhtei bolgoj uurchil", "write_operation"), ("LN-001 zeeliig ustga", "write_operation"),
    ("Delete all closed loans.", "write_operation"), ("Please update the status of loan LN-1", "write_operation"),
    ("Can you delete the old records?", "write_operation"), ("DELETE FROM t2s.loans", "write_operation"),
    ("I want to update the loan status", "write_operation"), ("Энэ loan-ийг delete хийж өгөөч", "write_operation"),
    ("Status-ийг update хий", "write_operation"),
])
def test_v2_still_refuses_requests(q, reason, kb):
    d = _v2(q, kb)
    assert (d.action, d.reason) == ("refuse", reason), q


def test_masker_keeps_pii_and_cue_labels(kb):
    gl, m = kb
    model = load_semantic_model("knowledge/semantic_model.yaml")
    labels = ic.domain_labels(gl, model)
    assert "бүтэц өөрчилсөн зээл" in labels and "Имэйл сануулга" in labels
    pii = [c for t in model.tables.values() for c in t.columns.values() if c.pii]
    assert pii and not any(c.mn in labels for c in pii)            # pii баганын шошгыг маскжуулахгүй
    assert "¤" in m.mask("имэйл сануулга") and "имэйл" in m.mask("имэйл хаяг")   # «Имэйл» ⊂ «имэйл хаяг» — үлдэнэ
    assert "дансны" in m.mask("дансны дугаар")
    assert ic.build_masker([]).mask("бүтэц өөрчилсөн зээл") == "бүтэц өөрчилсөн зээл"


def test_dev_set_v2_keeps_every_genuine_refusal(kb):
    """dev-ийн бүх хувилбар (mn/latin/en/mixed): v2-ийн татгалзал ⊆ legacy; legacy-ийн хариулах боломжгүй асуултын
    татгалзал v2-д бүгд хэвээр; өөрчлөгдөх нь зөвхөн англи «registered» (C003, K033, X016 — хариулах боломжтой)."""
    dev = [json.loads(x) for x in open(load_config()["eval"]["dev_file"], encoding="utf-8") if x.strip()]
    changed = []
    for r in dev:
        for v in ("mn", "latin", "en", "mixed"):
            q = r.get(f"question_{v}")
            if not q:
                continue
            a, b = ic.check_question(q, kb[0]), _v2(q, kb)
            if b.action == "refuse":
                assert a.action == "refuse", (r["id"], v)
            if a.action == "refuse" and r["category"] == "unanswerable":
                assert (b.action, b.reason) == (a.action, a.reason), (r["id"], v)
            if (a.action, a.reason) != (b.action, b.reason):
                changed.append((r["id"], v))
    assert sorted(changed) == [("C003", "en"), ("K033", "en"), ("X016", "en")]


# ------------------------------------------------------------------ orchestrator
class _Passed(Exception):
    pass


def _pipe(**opt):
    p = orch.Pipeline.__new__(orch.Pipeline)
    p.opts = RunOptions(intent_check=True, intent_refuse_only=True, **opt)
    p.cfg = {"knowledge": {"glossary": "knowledge/glossary.yaml"}}
    p.model_name, p.translations = "m", {}
    p.model = load_semantic_model("knowledge/semantic_model.yaml")

    def stop(*a, **k):
        raise _Passed()
    p.prompt_for = stop        # intent шалгалтыг давбал SQL үүсгэлт рүү орно
    return p


def test_pipeline_wiring_legacy_vs_v2():
    q = "Бүтэц өөрчилсөн зээл хэд байна?"
    assert _pipe().ask(q).status == "refused"
    with pytest.raises(_Passed):
        _pipe(intent_rules="v2").ask(q)
    p = _pipe(intent_rules="v2")
    assert p.ask("LN-2025-000001 зээлийг устга").status == "refused"
    assert p._intent_masker.n_labels > 100          # semantic_model-ийн шошготой нэг удаа угсарсан
