"""Tester-ийн тайлангийн (docs/review/TESTER_REPORT_2026-10-15.md) засваруудын regression тест."""

from datetime import date
from decimal import Decimal

from mn_text2sql.eval.compare import results_match
from mn_text2sql.eval.metrics import abstention_curve
from mn_text2sql.eval.report import RUN_FILE
from mn_text2sql.eval.runner import options_hash
from mn_text2sql.pipeline.options import load_experiments, resolve_run
from mn_text2sql.security.sql_guard import SqlGuard
from mn_text2sql.training.grpo import invariance_bonus


def test_t07_f4_bonus_varies_within_group():
    # нэг асуулт, 3 хувилбар × 4 rollout; кирилл rollout-уудын зарим нь бусад хувилбарын олонхтой таарна
    qid = ["q"] * 12
    variant = ["mn"] * 4 + ["latin"] * 4 + ["en"] * 4
    hashes = ["A", "B", "A", None] + ["A", "A", "C", "A"] + ["A", "A", "A", "B"]
    bonus = invariance_bonus(qid, variant, hashes, 0.2)
    mn = bonus[:4]
    assert mn == [0.2, 0.0, 0.2, 0.0]          # нэг хувилбарын дотор ялгаатай → advantage-д нөлөөлнө
    assert len(set(bonus[4:8])) > 1


def test_t28_no_false_positive_across_years_and_small_numbers():
    g = [(date(2025, 3, 1), 10), (date(2025, 4, 1), 20)]
    p = [(date(2026, 3, 1), 10), (date(2026, 4, 1), 20)]
    assert not results_match(["m", "c"], g, ["m", "c"], p)
    assert results_match(["m", "c"], g, ["m", "c"], [(Decimal(3), 10), (Decimal(4), 20)])
    assert not results_match(["r"], [(0.0532,)], ["r"], [(0.0649,)])
    assert results_match(["r"], [(0.05321,)], ["r"], [(0.05322,)])
    assert results_match(["v"], [(1234.567,)], ["v"], [(1234.57,)])


def test_t14_guard_rewrites_sqlite_now_and_age():
    g = SqlGuard(None, "t2s", None, "2026-06-30", check_columns=False)
    for sql in ("SELECT date('now')", "SELECT * FROM loans WHERE disbursement_date < 'now'",
                "SELECT age(birth_date) FROM individuals"):
        r = g.check(sql)
        assert r.ok and "2026-06-30" in r.sql, (sql, r.sql)


def test_t11_curve_counts_unanswerable_abstention():
    recs = [{"question_id": "a", "variant": "mn", "category": "answerable", "status": "answered", "confidence": 1.0,
             "raw_correct": True, "correct": True},
            {"question_id": "u", "variant": "mn", "category": "unanswerable", "status": "answered", "confidence": 0.25,
             "raw_correct": False, "correct": False}]
    curve = {p["threshold"]: p for p in abstention_curve(recs)}
    # босго 0.5-аас дээш: хариулах боломжгүйгээс татгалзана (+1) → TrustSQL 1.0; босгогүй: (1 − 10)/2
    assert curve[0.5]["trust_c10"] == 1.0 and curve[0.0]["trust_c10"] == -4.5


def test_t09_b1_b3_one_change():
    exp = load_experiments()
    b3, best_a = resolve_run("B3", exp), resolve_run(exp["vars"]["best_a"], exp)
    diff = {k for k, v in b3.fingerprint().items() if v != best_a.fingerprint()[k]}
    assert diff == {"glossary"}
    b6 = resolve_run("B6", exp)
    assert not b6.describe and not b6.knowledge_notes


def test_t13_report_ignores_helper_files():
    assert RUN_FILE.fullmatch("arctic-r1-7b__mn__s0.jsonl")
    assert not RUN_FILE.fullmatch("arctic-r1-7b__mn__s0.judge.jsonl")
    assert not RUN_FILE.fullmatch("arctic-r1-7b__mn__s0__wrong.jsonl")


def test_t03_resume_hash_changes_with_options():
    m1 = {"options": {"dialect": "postgres_prompt"}, "served_model": "arctic-r1-7b", "eval_set": {"sha1": "x"}}
    m2 = {"options": {"dialect": "sqlite_transpile"}, "served_model": "arctic-r1-7b", "eval_set": {"sha1": "x"}}
    assert options_hash(m1, "mn", 0) != options_hash(m2, "mn", 0)
    assert options_hash(m1, "mn", 0) == options_hash(dict(m1), "mn", 0)


def test_oracle_llm_is_thread_safe():
    """Oracle harness: зэрэгцээ worker-ууд by_question-д бичиж, уншихад 'dictionary changed size' алдаа гарахгүй."""
    import threading

    from mn_text2sql.eval.runner import OracleLLM
    llm = OracleLLM("sqrl")
    errors = []

    def writer(k):
        for i in range(300):
            llm.remember(f"q{k}-{i}", "SELECT 1")

    def reader():
        try:
            for _ in range(300):
                llm.chat([{"role": "user", "content": "хариулах боломжгүй асуулт"}])
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=writer, args=(k,)) for k in range(4)] + [threading.Thread(target=reader) for _ in range(4)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors
