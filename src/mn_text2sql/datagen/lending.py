"""Зээлийн хүсэлт → судалгаа/шийдвэр/скоринг → зээл → хуваарь → төлөлт → DPD/ангилал → бүтэц/хасалт."""

from __future__ import annotations

import copy
import heapq
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from .context import Gen
from .loansim import Charge, Item, Ledger, Payment, build_schedule, late_days
from .people import pick_officer
from .refdata import grade_for_dpd, risk_grade_for_score
from .util import ZERO, D, add_months, month_ends_between, ts, ts_rand

ACTIVE_SET = {"ACTIVE", "OVERDUE", "RESTRUCTURED", "LEGAL"}

PRODUCT_TERMS = {
    "CONS_STD": (6, 36), "CONS_FAST": (3, 12), "SAL_STD": (12, 60), "SAL_PREM": (12, 60), "BIZ_WC": (6, 24),
    "BIZ_INV": (12, 60), "MORT_STD": (120, 240), "AUTO_NEW": (24, 60), "PLEDGE_GOLD": (1, 6),
    "CONS_OLD": (6, 30), "AGRO_HERD": (12, 24), "BIZ_USD": (12, 36),
}
BASE_PD = {"AAA": 0.03, "AA": 0.06, "A": 0.10, "BBB": 0.17, "BB": 0.26, "B": 0.36, "C": 0.46}
PRODUCT_PD = {"CONS_FAST": 1.5, "PLEDGE_GOLD": 0.8, "MORT_STD": 0.45, "AGRO_HERD": 1.25, "BIZ_USD": 0.7,
              "BIZ_WC": 1.3, "BIZ_INV": 1.2,
              "CONS_OLD": 1.3, "SAL_PREM": 0.6}
REJECT_REASONS = ["Өр/орлогын харьцаа (DTI) хэт өндөр", "Зээлийн түүх муу", "Барьцаа хөрөнгө хангалтгүй",
                  "Орлого баталгаажаагүй", "Скорингийн оноо хүрэлцээгүй", "Мэдээлэл зөрүүтэй"]
PURPOSES = {
    "CONS_STD": ("CONSUMER", ["Гэр ахуйн хэрэгцээ", "Тавилга худалдан авах", "Аялал", "Засвар"]),
    "CONS_FAST": ("CONSUMER", ["Яаралтай хэрэгцээ", "Сургалтын төлбөр", "Эмчилгээ"]),
    "SAL_STD": ("CONSUMER", ["Хэрэглээ", "Орон сууцны засвар", "Сургалтын төлбөр"]),
    "SAL_PREM": ("CONSUMER", ["Хэрэглээ", "Аялал"]),
    "CONS_OLD": ("CONSUMER", ["Хэрэглээ"]),
    "BIZ_WC": ("BUSINESS", ["Эргэлтийн хөрөнгө", "Бараа таваар", "Түүхий эд"]),
    "BIZ_INV": ("BUSINESS", ["Тоног төхөөрөмж", "Үйлдвэрийн байр", "Өргөтгөл"]),
    "BIZ_USD": ("BUSINESS", ["Импортын бараа", "Тоног төхөөрөмж"]),
    "MORT_STD": ("MORTGAGE", ["Орон сууц худалдан авах"]),
    "AUTO_NEW": ("AUTO", ["Автомашин худалдан авах"]),
    "PLEDGE_GOLD": ("OTHER", ["Богино хугацааны хэрэгцээ"]),
    "AGRO_HERD": ("AGRICULTURE", ["Мал сүрэг өсгөх", "Өвс тэжээл", "Хадлан"]),
}


def choose_product(gen: Gen, rng, cust, on: date) -> SimpleNamespace | None:
    p = gen.ref.products
    rural = cust.branch.province != "Улаанбаатар"
    if cust.is_org:
        weights = {"ORG_SME": {"BIZ_WC": 50, "BIZ_INV": 28, "BIZ_USD": 7, "AUTO_NEW": 7},
                   "ORG_CORP": {"BIZ_INV": 45, "BIZ_WC": 35, "BIZ_USD": 20},
                   "ORG_NGO": {"BIZ_WC": 100}}[cust.ctype].copy()
        if getattr(cust, "industry", "") == "A01":
            weights["AGRO_HERD"] = 40
    elif cust.ctype == "IND_PENSION":
        weights = {"CONS_STD": 40, "CONS_FAST": 18, "PLEDGE_GOLD": 27, "CONS_OLD": 15}
    elif cust.ctype == "IND_BUSINESS":
        weights = {"CONS_STD": 20, "BIZ_WC": 22, "PLEDGE_GOLD": 15, "AUTO_NEW": 13, "CONS_FAST": 10,
                   "CONS_OLD": 8, "MORT_STD": 4}
        if rural:
            weights["AGRO_HERD"] = 30
    else:
        weights = {"SAL_STD": 34, "CONS_STD": 18, "CONS_FAST": 12, "AUTO_NEW": 8, "MORT_STD": 6,
                   "PLEDGE_GOLD": 6, "CONS_OLD": 6}
        if cust.segment in ("AFFLUENT", "PREMIUM", "VIP"):
            weights["SAL_PREM"] = 14
        if rural:
            weights["AGRO_HERD"] = 6
    if cust.branch.branch_type == "DIGITAL":
        weights = {k: v for k, v in weights.items() if k in ("CONS_FAST", "SAL_STD", "CONS_STD")} or {"CONS_FAST": 1}
    valid = {}
    for code, w in weights.items():
        prod = p.get(code)
        if prod is None or prod.launch_date > on:
            continue
        if prod.discontinue_date and prod.discontinue_date < on:
            continue
        valid[code] = w
    if not valid:
        return None
    return p[rng.weighted(valid)]


def _amount(gen: Gen, rng, cust, prod) -> Decimal:
    code = prod.product_code
    lo, hi = float(prod.min_amount or 100000), float(prod.max_amount or 10**9)
    if code == "BIZ_USD":
        val = rng.lognormal(60000 if cust.ctype != "ORG_CORP" else 180000, 0.6)
        step = 1000
    elif cust.is_org or code.startswith("BIZ"):
        base = cust.revenue if cust.is_org else cust.income * 12
        val = base * rng.uniform(0.03, 0.18)
        step = 500_000
    elif code == "MORT_STD":
        val = rng.lognormal(95_000_000, 0.45)
        step = 1_000_000
    elif code == "AUTO_NEW":
        val = rng.lognormal(38_000_000, 0.4)
        step = 500_000
    elif code == "PLEDGE_GOLD":
        val = rng.lognormal(2_200_000, 0.6)
        step = 50_000
    elif code == "AGRO_HERD":
        val = rng.lognormal(9_000_000, 0.6)
        step = 100_000
    else:
        val = cust.income * rng.uniform(2, 10)
        step = 100_000
    val = min(max(val, lo), hi)
    return D(min(max(gen.money_round(val, step), lo), hi))


def _term(rng, prod) -> int:
    lo, hi = PRODUCT_TERMS.get(prod.product_code, (prod.min_term_months or 6, prod.max_term_months or 36))
    if prod.product_code == "MORT_STD":
        return rng.choice([120, 180, 240])
    if prod.product_code == "PLEDGE_GOLD":
        return rng.choice([1, 2, 3, 3, 6])
    options = [t for t in (3, 6, 9, 12, 18, 24, 30, 36, 48, 60) if lo <= t <= hi] or [lo]
    return rng.choice(options)


def _score(rng, cust) -> int:
    base = rng.gauss(660, 95)
    base += {"LOW": 25, "MEDIUM": -10, "HIGH": -70, "CRITICAL": -150}[cust.risk]
    base += {"VIP": 60, "PREMIUM": 40, "AFFLUENT": 15}.get(cust.segment, 0)
    if cust.status in ("BLACKLIST", "SUSPENDED"):
        base -= 120
    return int(min(980, max(280, base)))


def build_lending(gen: Gen) -> None:
    rng = gen.rng.child("lending")
    T = _tables(gen)
    managers = {e.branch.id: e for e in gen.employees if e.position == "BRANCH_MANAGER"}
    ceo = next(e for e in gen.employees if e.position == "CEO")
    risk_analysts = [e for e in gen.employees if e.position == "RISK_ANALYST"] or [ceo]

    for cust in gen.customers:
        if cust.status == "PROSPECT":
            continue
        n_apps = rng.weighted({0: 0.14, 1: 0.34, 2: 0.29, 3: 0.15, 4: 0.08}) if not cust.is_org else \
            rng.weighted({0: 0.08, 1: 0.28, 2: 0.30, 3: 0.20, 4: 0.14})
        window_lo = max(cust.onboard, gen.start)
        window_hi = min(gen.as_of, cust.closed - timedelta(days=60) if cust.closed else gen.as_of)
        if window_hi <= window_lo:
            continue
        dates = sorted(rng.date_between(window_lo, window_hi) for _ in range(n_apps))
        last = None
        for d in dates:
            if last and (d - last).days < 45:
                continue
            last = d
            prod = choose_product(gen, rng, cust, d)
            if prod is None:
                continue
            branch = cust.branch if cust.branch.closed_date is None or cust.branch.closed_date > d else \
                gen.ref.branches["UB05"]
            if branch.branch_type == "SUB_BRANCH" and rng.chance(0.3):
                branch = cust.branch
            officer = pick_officer(gen, rng, branch, d)
            _application(gen, rng, T, cust, prod, branch, officer, d, managers, ceo, risk_analysts)

    # Дугаарлалтын дараалал: зээлүүдийг олголтын огноогоор эрэмбэлж бүртгэнэ
    gen.loans.sort(key=lambda ln: (ln.disb or ln.contract, ln.n))
    for ln in gen.loans:
        _simulate_and_write(gen, rng.child(f"loan{ln.n}"), T, ln, managers, ceo)


def _tables(gen: Gen) -> SimpleNamespace:
    st = gen.store
    T = SimpleNamespace()
    T.app = st.table("loan.loan_applications", [
        "id", "application_no", "customer_id", "product_id", "branch_id", "status_id", "channel", "requested_amount",
        "requested_term_months", "currency_code", "loan_purpose", "purpose_category", "repayment_source",
        "declared_income", "declared_expense", "existing_debt_payment", "dti_ratio", "approved_amount",
        "approved_term_months", "approved_rate", "loan_officer_id", "submitted_at", "decided_at", "expires_at",
        "rejection_reason", "withdrawal_reason", "extra_data", "created_at", "updated_at"])
    T.assess = st.table("loan.loan_assessments", [
        "id", "application_id", "assessment_type", "assessed_by", "verified_income", "verified_expense",
        "calculated_dti", "calculated_ltv", "credit_score", "risk_grade_code", "recommended_amount",
        "recommended_term_months", "recommended_rate", "recommendation", "conditions", "findings", "assessed_at",
        "created_at"])
    T.decision = st.table("loan.loan_decisions", [
        "id", "application_id", "decision_level", "decision", "decided_by", "committee_ref", "approved_amount",
        "approved_term_months", "approved_rate", "conditions", "reason", "decided_at", "is_final", "created_at"])
    T.score = st.table("risk.credit_scores", [
        "id", "customer_id", "application_id", "loan_id", "model_id", "risk_grade_id", "score", "pd_estimate",
        "factor_values", "decision_hint", "score_type", "scored_at", "valid_until", "is_current", "created_at"])
    T.bureau = st.table("risk.credit_bureau_reports", [
        "id", "customer_id", "application_id", "bureau_name", "request_ref", "bureau_score", "total_exposure",
        "active_loan_count", "overdue_loan_count", "max_dpd_12m", "worst_classification", "has_writeoff",
        "inquiry_count_3m", "consent_reference", "requested_at", "requested_by", "valid_until", "created_at"])
    T.loan = st.table("loan.loans", [
        "id", "loan_number", "application_id", "customer_id", "product_id", "branch_id", "status_id",
        "loan_officer_id", "currency_code", "principal_amount", "interest_rate", "penalty_rate", "effective_rate",
        "rate_type", "interest_method", "day_count_convention", "amortization_type", "term_months",
        "payment_frequency", "grace_period_days", "contract_date", "disbursement_date", "first_payment_date",
        "maturity_date", "closed_date", "disbursed_amount", "principal_outstanding", "interest_outstanding",
        "penalty_outstanding", "fee_outstanding", "principal_paid", "interest_paid", "penalty_paid", "fee_paid",
        "written_off_amount", "overdue_principal", "overdue_interest", "days_past_due", "max_days_past_due",
        "first_overdue_date", "last_payment_date", "classification_id", "is_npl", "is_restructured",
        "restructure_count", "notes", "created_at", "updated_at"])
    T.borrower = st.table("loan.loan_borrowers", ["id", "loan_id", "customer_id", "borrower_role",
                                                  "liability_share", "is_active", "created_at"])
    T.contract = st.table("loan.loan_contracts", [
        "id", "loan_id", "contract_number", "contract_type", "parent_contract_id", "signed_date", "effective_date",
        "expiry_date", "signed_by_customer", "signed_by_officer_id", "signature_method", "status", "terms_snapshot",
        "created_at", "updated_at"])
    T.disb = st.table("loan.loan_disbursements", [
        "id", "loan_id", "currency_code", "disbursement_no", "amount", "disbursement_date", "value_date", "method",
        "destination_account_id", "internal_account_id", "status", "executed_at", "approved_by", "created_at",
        "updated_at"])
    T.sched = st.table("loan.loan_schedules", [
        "id", "loan_id", "schedule_version", "is_current", "generated_reason", "total_principal", "total_interest",
        "total_fee", "installment_count", "first_due_date", "last_due_date", "generated_at", "generated_by",
        "created_at"])
    T.item = st.table("loan.loan_schedule_items", [
        "id", "schedule_id", "loan_id", "installment_no", "due_date", "period_start_date", "period_end_date",
        "principal_due", "interest_due", "fee_due", "insurance_due", "principal_paid", "interest_paid", "fee_paid",
        "insurance_paid", "penalty_charged", "penalty_paid", "opening_balance", "closing_balance", "status",
        "paid_date", "days_past_due", "created_at", "updated_at"])
    T.pay = st.table("loan.loan_payments", [
        "id", "payment_no", "loan_id", "currency_code", "customer_id", "branch_id", "amount", "payment_date",
        "value_date", "payment_method", "payment_type", "allocated_principal", "allocated_interest",
        "allocated_penalty", "allocated_fee", "unallocated_amount", "transaction_id", "status", "received_by",
        "notes", "created_at", "updated_at"])
    T.alloc = st.table("loan.loan_payment_allocations", ["id", "payment_id", "schedule_item_id", "component",
                                                         "amount", "allocation_order", "created_at"])
    T.fee = st.table("loan.loan_fees", [
        "id", "loan_id", "fee_type_id", "amount", "currency_code", "charged_date", "due_date", "paid_amount",
        "waived_amount", "waived_by", "waive_reason", "status", "created_at", "updated_at"])
    T.pen = st.table("loan.loan_penalties", [
        "id", "loan_id", "schedule_item_id", "currency_code", "penalty_type", "base_amount", "penalty_rate",
        "days_overdue", "amount", "charged_date", "paid_amount", "waived_amount", "waived_by", "waive_reason",
        "status", "created_at", "updated_at"])
    T.accr = st.table("loan.loan_interest_accruals", [
        "id", "loan_id", "accrual_date", "principal_base", "annual_rate", "day_count", "interest_amount",
        "penalty_amount", "is_posted", "journal_entry_id", "is_suspended", "created_at"])
    T.ovd = st.table("loan.loan_overdues", [
        "id", "loan_id", "snapshot_date", "days_past_due", "dpd_bucket", "overdue_principal", "overdue_interest",
        "overdue_penalty", "overdue_fee", "total_outstanding", "oldest_due_date", "classification_id", "created_at"])
    T.cls = st.table("loan.loan_classifications", [
        "id", "loan_id", "old_classification_id", "new_classification_id", "days_past_due", "outstanding_amount",
        "provision_rate", "provision_amount", "classification_date", "is_manual_override", "created_at"])
    T.restr = st.table("loan.loan_restructures", [
        "id", "loan_id", "restructure_no", "restructure_type", "reason", "old_principal", "new_principal",
        "old_rate", "new_rate", "old_term_months", "new_term_months", "old_maturity_date", "new_maturity_date",
        "capitalized_interest", "capitalized_penalty", "waived_amount", "old_schedule_id", "new_schedule_id",
        "new_contract_id", "effective_date", "approved_by", "approval_ref", "status", "created_at", "updated_at"])
    T.wo = st.table("loan.loan_writeoffs", [
        "id", "loan_id", "writeoff_date", "principal_amount", "interest_amount", "penalty_amount", "fee_amount",
        "provision_used", "writeoff_type", "reason", "board_resolution_ref", "approved_by", "is_recoverable",
        "recovered_amount", "created_at", "updated_at"])
    T.hist = st.table("loan.loan_status_history", [
        "id", "loan_id", "old_status_id", "new_status_id", "change_reason", "outstanding_at_change",
        "dpd_at_change", "changed_at", "changed_by"])
    T.rate_hist = st.table("loan.loan_interest_rate_history", [
        "id", "loan_id", "old_rate", "new_rate", "rate_type", "change_reason", "effective_from", "effective_to",
        "approved_by", "changed_at"])
    return T


def _application(gen: Gen, rng, T, cust, prod, branch, officer, d: date, managers, ceo, analysts) -> None:
    ref = gen.ref
    app_id = gen.uuid()
    req_amount = _amount(gen, rng, cust, prod)
    term = _term(rng, prod)
    score = _score(rng, cust)
    rgrade = risk_grade_for_score(gen, score)
    rate = round(float(prod.annual_rate) + float(rgrade.pd_estimate or 0) * 10, 1)
    rate = min(rate, float(prod.annual_rate) + 6)
    income = D(cust.income if not cust.is_org else cust.revenue / 12)
    monthly_pay = float(req_amount) * (rate / 1200) / (1 - (1 + rate / 1200) ** (-term)) if term > 1 else float(req_amount)
    if prod.currency_code == "USD":
        monthly_pay *= 3450
    existing = D(float(income) * rng.uniform(0, 0.25)) if rng.chance(0.4) else ZERO
    dti = round(min(99.99, (monthly_pay + float(existing)) / max(float(income), 1) * 100), 2)
    channel = "MOBILE" if branch.branch_type == "DIGITAL" else rng.weighted(
        {"BRANCH": 0.62, "MOBILE": 0.2, "WEB": 0.08, "AGENT": 0.04, "CALL_CENTER": 0.03, "PARTNER": 0.03})
    submitted = ts_rand(rng, d)
    recent = (gen.as_of - d).days
    model = ref.score_models["APP_SCORE_SME" if cust.is_org else "APP_SCORE_IND"]
    cutoff = 500 if cust.is_org else 450
    max_dti = float(prod.max_dti_ratio) if prod.max_dti_ratio else 70.0

    # Шийдвэр
    if recent < 6:
        status = rng.weighted({"SUBMITTED": 0.4, "UNDER_REVIEW": 0.4, "PENDING_DOCS": 0.2})
    elif score < cutoff or cust.status in ("BLACKLIST", "SUSPENDED"):
        status = "REJECTED"
    elif dti > max_dti + 5 and rng.chance(0.7):
        status = "REJECTED"
    else:
        status = rng.weighted({"APPROVED": 0.80, "REJECTED": 0.09, "WITHDRAWN": 0.07, "EXPIRED": 0.04})
    if status == "APPROVED" and recent > 12:
        status = "CONTRACTED"
    elif status == "APPROVED" and rng.chance(0.4):
        status = "CONTRACTED"
    decided = submitted + timedelta(days=rng.randint(1, 9), hours=rng.randint(0, 6)) \
        if status not in ("SUBMITTED", "UNDER_REVIEW", "PENDING_DOCS", "WITHDRAWN") else None
    if decided and decided.date() > gen.as_of:
        decided = ts(gen.as_of, 11)
    approved_amt = approved_term = approved_rate = None
    if status in ("APPROVED", "CONTRACTED", "EXPIRED"):
        approved_amt = req_amount if rng.chance(0.78) else D(gen.money_round(float(req_amount) * rng.uniform(0.6, 0.95),
                                                                             1000 if prod.currency_code == "USD"
                                                                             else 100_000))
        approved_amt = max(approved_amt, D(prod.min_amount or 1))
        approved_term = term
        approved_rate = rate
    purpose_cat, purposes = PURPOSES.get(prod.product_code, ("OTHER", ["Бусад"]))
    T.app.add(id=app_id, application_no=gen.number("LOAN_APPLICATION", d), customer_id=cust.id, product_id=prod.id,
              branch_id=branch.id, status_id=ref.app_statuses[status], channel=channel, requested_amount=req_amount,
              requested_term_months=term, currency_code=prod.currency_code, loan_purpose=rng.choice(purposes),
              purpose_category=gen.maybe_null(purpose_cat, 0.02),
              repayment_source="Цалингийн орлого" if cust.ctype == "IND_SALARY" else
              ("Тэтгэвэр" if cust.ctype == "IND_PENSION" else "Бизнесийн орлого"),
              declared_income=income, declared_expense=D(float(income) * rng.uniform(0.3, 0.6)),
              existing_debt_payment=existing, dti_ratio=dti, approved_amount=approved_amt,
              approved_term_months=approved_term, approved_rate=approved_rate,
              loan_officer_id=officer.id if officer else None, submitted_at=submitted, decided_at=decided,
              expires_at=submitted + timedelta(days=30),
              rejection_reason=rng.choice(REJECT_REASONS) if status == "REJECTED" else None,
              withdrawal_reason="Харилцагч татгалзсан" if status == "WITHDRAWN" else None,
              extra_data={"score": score}, created_at=submitted, updated_at=decided or submitted)
    # Скоринг ба зээлийн мэдээллийн сан
    T.score.add(id=gen.next_id("risk.credit_scores"), customer_id=cust.id, application_id=app_id, loan_id=None,
                model_id=model, risk_grade_id=rgrade.id, score=score, pd_estimate=rgrade.pd_estimate,
                factor_values={"income": float(income), "dti": dti},
                decision_hint="AUTO_REJECT" if score < cutoff else ("MANUAL_REVIEW" if score < 620 else "AUTO_APPROVE"),
                score_type="APPLICATION", scored_at=submitted + timedelta(minutes=5),
                valid_until=d + timedelta(days=90), is_current=False, created_at=submitted)
    cust.last_score = (score, rgrade, app_id, submitted)
    T.bureau.add(id=gen.next_id("risk.credit_bureau_reports"), customer_id=cust.id, application_id=app_id,
                 bureau_name="Зээлийн мэдээллийн сан", request_ref=f"ZMS-{rng.randint(10**7, 10**8 - 1)}",
                 bureau_score=max(300, min(900, score + rng.randint(-60, 60))),
                 total_exposure=D(float(income) * rng.uniform(0, 8)) if rng.chance(0.6) else ZERO,
                 active_loan_count=rng.weighted({0: 0.4, 1: 0.35, 2: 0.17, 3: 0.08}),
                 overdue_loan_count=0 if score > 600 else rng.weighted({0: 0.6, 1: 0.3, 2: 0.1}),
                 max_dpd_12m=0 if score > 650 else rng.choice([0, 0, 5, 15, 35, 95]),
                 worst_classification="Хэвийн" if score > 600 else rng.choice(["Хэвийн", "Хугацаа хэтэрсэн",
                                                                                "Чанаргүй"]),
                 has_writeoff=score < 420 and rng.chance(0.3), inquiry_count_3m=rng.weighted({0: 0.5, 1: 0.3, 2: 0.15,
                                                                                                3: 0.05}),
                 consent_reference=f"CONSENT-{d.year}-{rng.randint(100000, 999999)}", requested_at=submitted,
                 requested_by=officer.id if officer else None, valid_until=d + timedelta(days=30), created_at=submitted)
    if status in ("SUBMITTED",):
        return
    assessor = officer or rng.choice(analysts)
    rec = {"REJECTED": "REJECT", "APPROVED": "APPROVE", "CONTRACTED": "APPROVE", "WITHDRAWN": "APPROVE",
           "EXPIRED": "APPROVE_WITH_CONDITIONS", "UNDER_REVIEW": "NEED_MORE_INFO",
           "PENDING_DOCS": "NEED_MORE_INFO"}[status]
    T.assess.add(id=gen.next_id("loan.loan_assessments"), application_id=app_id, assessment_type="CREDIT",
                 assessed_by=assessor.id, verified_income=D(float(income) * rng.uniform(0.85, 1.0)),
                 verified_expense=D(float(income) * rng.uniform(0.3, 0.55)), calculated_dti=dti,
                 calculated_ltv=None, credit_score=score, risk_grade_code=rgrade.grade_code,
                 recommended_amount=approved_amt or req_amount, recommended_term_months=term,
                 recommended_rate=rate, recommendation=rec,
                 conditions="Батлан даагч шаардлагатай" if prod.requires_guarantor else None,
                 findings="Орлого тогтвортой" if score > 600 else "Эрсдэл өндөр",
                 assessed_at=submitted + timedelta(days=1), created_at=submitted + timedelta(days=1))
    if not decided:
        return
    mnt_amount = float(approved_amt or req_amount) * (3450 if prod.currency_code == "USD" else 1)
    if channel == "MOBILE" and mnt_amount <= 5_000_000 and score >= 650:
        level, maker = "AUTO", None
    elif mnt_amount <= 10_000_000:
        level, maker = "OFFICER", officer
    elif mnt_amount <= 50_000_000:
        level, maker = "BRANCH_MANAGER", managers.get(branch.id) or ceo
    elif mnt_amount <= 500_000_000:
        level, maker = "CREDIT_COMMITTEE", ceo
    else:
        level, maker = "BOARD", ceo
    if level != "AUTO" and maker is None:
        maker = ceo
    decision = "REJECTED" if status == "REJECTED" else "APPROVED"
    if status in ("WITHDRAWN",):
        return
    T.decision.add(id=gen.next_id("loan.loan_decisions"), application_id=app_id, decision_level=level,
                   decision=decision, decided_by=maker.id if maker else None,
                   committee_ref=f"ЗХ-{d.year}/{rng.randint(1, 250):03d}" if level in ("CREDIT_COMMITTEE", "BOARD")
                   else None,
                   approved_amount=approved_amt if decision == "APPROVED" else None,
                   approved_term_months=approved_term if decision == "APPROVED" else None,
                   approved_rate=approved_rate if decision == "APPROVED" else None,
                   conditions=None, reason=rng.choice(REJECT_REASONS) if decision == "REJECTED" else "Шаардлага хангасан",
                   decided_at=decided, is_final=True, created_at=decided)
    if status != "CONTRACTED":
        return
    contract = decided.date() + timedelta(days=rng.randint(1, 5))
    disb = contract + timedelta(days=rng.randint(0, 3))
    if contract > gen.as_of:
        contract = gen.as_of
    pending = disb > gen.as_of or (gen.as_of - disb).days < 2 and rng.chance(0.5)
    ln = SimpleNamespace(
        n=len(gen.loans) + 1, id=gen.uuid(), app_id=app_id, cust=cust, prod=prod, branch=branch, officer=officer,
        currency=prod.currency_code, principal=approved_amt, rate=approved_rate, term=approved_term,
        contract=contract, disb=None if pending else disb, score=score, rgrade=rgrade, dti=dti, channel=channel,
        decided=decided)
    gen.loans.append(ln)
    cust.loans.append(ln)


# ---------------------------------------------------------------------------
# Симуляц
# ---------------------------------------------------------------------------

def _behavior(rng, ln) -> dict:
    pd = BASE_PD.get(ln.rgrade.grade_code, 0.1) * PRODUCT_PD.get(ln.prod.product_code, 1.0)
    if ln.cust.status in ("BLACKLIST",):
        pd = max(pd, 0.7)
    late = 0.16 + (0.1 if ln.rgrade.grade_code in ("BB", "B", "C") else 0.0)
    early = 0.12 if ln.term <= 12 else 0.08
    kind = rng.weighted({"default": pd, "late": late, "early": early, "ontime": max(0.05, 1 - pd - late - early)})
    return {"kind": kind, "late_p": 0.4 if kind == "late" else 0.04,
            "default_from": rng.randint(1, max(1, min(ln.term, 12))),
            "early_at": rng.randint(2, max(2, ln.term - 1)) if ln.term > 2 else ln.term,
            "cure": rng.chance(0.25), "restructure": rng.chance(0.25), "legal": rng.chance(0.65),
            "writeoff": rng.chance(0.55), "recovery": rng.chance(0.45)}


def _simulate_and_write(gen: Gen, rng, T, ln, managers, ceo) -> None:
    ref = gen.ref
    prod = ln.prod
    status_hist = []
    if ln.disb is None:
        _write_loan_row(gen, T, ln, status="PENDING_DISBURSEMENT", ledger=None, final=None)
        status_hist.append((None, "PENDING_DISBURSEMENT", ts(ln.contract, 15), "Гэрээ байгуулсан", ZERO, 0))
        _write_history(gen, T, ln, status_hist)
        return
    first_payment = add_months(ln.disb, 1)
    items = build_schedule(ln.principal, ln.rate, ln.term, prod.amortization_type or "ANNUITY", ln.disb, first_payment)
    penalty_rate = round(ln.rate * 0.2, 2)
    ledger = Ledger(items, penalty_rate, prod.penalty_grace_days or 5)
    ln.ledger = ledger
    ln.penalty_rate = penalty_rate
    ln.maturity = add_months(ln.disb, ln.term)
    ln.schedules = []          # [(version, items, is_current, reason, generated_at)]
    ln.v1_items = None
    beh = _behavior(rng, ln)
    ln.behavior = beh["kind"]
    # Олголтын шимтгэл
    fee_pct = next((f["percent_rate"] for f in ref.product_fees if f["product_code"] == prod.product_code
                    and f["fee_code"] == "DISBURSEMENT_FEE"), None)
    if fee_pct:
        fee = Charge("FEE", "DISBURSEMENT_FEE", ln.disb, D(ln.principal * Decimal(str(fee_pct)) / Decimal(100)))
        if rng.chance(0.85):
            fee.paid = fee.amount
            fee.paid_on = ln.disb
        ledger.charges.append(fee)

    status = "ACTIVE"
    status_hist.append(("PENDING_DISBURSEMENT", "ACTIVE", ts(ln.disb, 11), "Зээл олгогдов", ln.principal, 0))
    snapshots = []
    accruals = []
    classifications = []
    max_dpd = 0
    restructured = None
    written_off = None
    closed_on = None
    prev_grade = None
    events: list = []
    seq = 0

    def push(day, kind, payload=None):
        nonlocal seq
        seq += 1
        heapq.heappush(events, (day, {"month_end": 2, "due": 0, "pay": 1}[kind], seq, kind, payload))

    for it in items:
        if it.due <= gen.as_of:
            push(it.due, "due", it)
    for me in month_ends_between(ln.disb, gen.as_of):
        if me > ln.disb:
            push(me, "month_end", me)
    mode = "ontime" if beh["kind"] in ("ontime", "early") else beh["kind"]
    default_started = None
    cured = False
    last_me = ln.disb
    legal_checked = False

    while events:
        day, _, _, kind, payload = heapq.heappop(events)
        if day > gen.as_of:
            break
        if closed_on or (written_off and kind == "due"):
            if kind != "pay" or not written_off:
                continue
        if kind == "due":
            it: Item = payload
            if it not in ledger.items:
                continue  # бүтэц өөрчлөгдсөний дараах хуучин мөр
            if beh["kind"] == "early" and it.no == beh["early_at"] and restructured is None:
                push(it.due, "pay", ("payoff",))
                continue
            if mode == "default" or (beh["kind"] == "default" and it.no >= beh["default_from"]
                                     and not cured):
                if mode != "default":
                    mode = "default"
                    default_started = it.due
                if beh["cure"] and default_started and (it.due - default_started).days > rng.randint(40, 200):
                    mode = "ontime"
                    cured = True
                    push(it.due, "pay", ("regular",))
                elif rng.chance(0.15):
                    push(it.due + timedelta(days=rng.randint(0, 20)), "pay", ("partial",))
                continue
            if mode == "late" and rng.chance(beh["late_p"]):
                push(it.due + timedelta(days=late_days(rng)), "pay", ("regular",))
            elif mode != "late" and rng.chance(0.03):
                push(it.due + timedelta(days=rng.randint(1, 9)), "pay", ("regular",))
            else:
                push(it.due, "pay", ("regular",))
        elif kind == "pay":
            ptype = payload[0]
            ledger.charge_penalties_until(day)
            due_amt = ledger.amount_due(day)
            if ptype == "payoff":
                remaining = sum((x.unpaid_principal for x in ledger.items if x.due > day), ZERO)
                early_fee = None
                if prod.product_code in ("CONS_STD", "MORT_STD") and remaining > 0:
                    pct = Decimal("2") if prod.product_code == "CONS_STD" else Decimal("1")
                    early_fee = Charge("FEE", "EARLY_REPAY_FEE", day, D(remaining * pct / 100))
                    ledger.charges.append(early_fee)
                    due_amt += early_fee.amount
                amount = due_amt + remaining
                pay = Payment(day, amount, "EARLY_FULL", _method(rng, ln))
                ledger.apply_payment(pay, prepay_all=True)
            elif ptype == "recovery":
                if due_amt <= 0:
                    continue
                amount = D(due_amt * Decimal(str(rng.uniform(0.1, 0.6))))
                pay = Payment(day, amount, "RECOVERY", rng.choice(["BANK_TRANSFER", "CASH", "OFFSET"]))
                ledger.apply_payment(pay)
            else:
                if due_amt <= 0:
                    continue
                if ptype == "partial":
                    amount = D(max(Decimal(1000), due_amt * Decimal(str(rng.uniform(0.2, 0.6)))))
                    amount = min(amount, due_amt)
                else:
                    amount = due_amt
                    if rng.chance(0.03) and ln.currency == "MNT":
                        amount = D((due_amt / 1000).to_integral_value(rounding="ROUND_CEILING") * 1000)
                is_late = any(x.due < day for x in ledger.due_items(day))
                pay = Payment(day, amount, "OVERDUE" if is_late else "REGULAR", _method(rng, ln))
                if ptype == "regular" and rng.chance(0.004):
                    pay.status = "FAILED"
                    pay.unallocated = amount
                    ledger.payments.append(pay)
                    push(day + timedelta(days=rng.randint(2, 6)), "pay", ("regular",))
                    continue
                ledger.apply_payment(pay)
            lateness = max(((day - x.due).days for x in ledger.items if x.paid_on == day and x.due < day),
                           default=0)
            max_dpd = max(max_dpd, lateness)
            if ledger.all_paid() and all(x.is_paid for x in ledger.items):
                closed_on = day
                old = status
                status = "CLOSED"
                status_hist.append((old, "CLOSED", ts(day, 16), "Бүрэн төлөгдөв", ZERO, 0))
        elif kind == "month_end":
            me: date = payload
            state = ledger.state_at(me)
            dpd = state["dpd"]
            max_dpd = max(max_dpd, dpd)
            outstanding_total = state["principal_outstanding"] + state["interest_outstanding"] + \
                state["penalty_outstanding"] + state["fee_outstanding"]
            new_status = status
            reason = None
            if status == "ACTIVE" and dpd > 0:
                new_status, reason = "OVERDUE", "Хугацаа хэтэрлээ"
            elif status == "OVERDUE" and dpd == 0:
                new_status, reason = "ACTIVE", "Хугацаа хэтрэлт барагдав"
            elif status == "RESTRUCTURED" and restructured and (me - restructured["date"]).days > 50:
                new_status, reason = ("ACTIVE", "Шинэ хуваарь идэвхжив") if dpd == 0 else ("OVERDUE", "Дахин хэтэрлээ")
            if new_status == "OVERDUE" and beh["kind"] == "default" and beh["restructure"] and restructured is None \
                    and 45 <= dpd <= 150 and not cured:
                restructured = _restructure(gen, rng, ln, ledger, me, state, push)
                new_status, reason = "RESTRUCTURED", "Бүтцийн өөрчлөлт"
                mode = "ontime" if rng.chance(0.7) else "default_later"
                if mode == "default_later":
                    beh["default_from"] = rng.randint(ledger.items[-1].no - len(restructured["new_items"]) + 2,
                                                      ledger.items[-1].no)
                    beh["kind"] = "default"
                    mode = "ontime"
                    default_started = None
                    beh["cure"] = False
                    beh["restructure"] = False
                else:
                    beh["kind"] = "ontime"
            elif new_status in ("OVERDUE",) and status in ("OVERDUE",) and dpd >= 181 and not legal_checked:
                legal_checked = True
                if beh["legal"]:
                    new_status, reason = "LEGAL", "Хууль зүйн шат руу"
            if status in ("OVERDUE", "LEGAL") and new_status in ("OVERDUE", "LEGAL") and dpd >= 420 \
                    and beh["writeoff"] and written_off is None:
                written_off = {"date": me, "state": state}
                new_status, reason = "WRITTEN_OFF", "Данснаас хасав"
                if beh["recovery"]:
                    rec_day = me + timedelta(days=rng.randint(30, 240))
                    if rec_day <= gen.as_of:
                        push(rec_day, "pay", ("recovery",))
            if new_status != status:
                if (status, new_status) not in ref.transitions:
                    raise RuntimeError(f"буруу шилжилт {status}->{new_status}")
                status_hist.append((status, new_status, ts(me, 18), reason, outstanding_total, dpd))
                status = new_status
            if status in ACTIVE_SET:
                grade = grade_for_dpd(gen, dpd)
                snapshots.append((me, dpd, state, outstanding_total, grade))
                if prev_grade is None or grade.id != prev_grade.id:
                    classifications.append((me, prev_grade, grade, dpd, outstanding_total))
                    prev_grade = grade
                days = (me - max(last_me, ln.disb)).days
                if days > 0 and state["principal_outstanding"] > 0:
                    accruals.append((me, state["principal_outstanding"], days, grade.is_npl))
            last_me = me

    # as_of-ийн эцсийн төлөв
    if not closed_on and not written_off:
        ledger.charge_penalties_until(gen.as_of)
    final = ledger.state_at(gen.as_of)
    final["max_dpd"] = max(max_dpd, final["dpd"])
    if closed_on:
        final["dpd"] = 0
    ln.status = status
    ln.closed_on = closed_on
    ln.written_off = written_off
    ln.restructured = restructured
    ln.final = final
    ln.snapshots = snapshots
    ln.status_hist = status_hist
    _write_loan_row(gen, T, ln, status=status, ledger=ledger, final=final)
    _write_schedule_and_payments(gen, rng, T, ln, managers, ceo)
    _write_history(gen, T, ln, status_hist)
    _write_snapshots(gen, T, ln, snapshots, accruals, classifications)
    if written_off:
        _write_writeoff(gen, rng, T, ln, ceo)


def _method(rng, ln) -> str:
    if ln.channel == "MOBILE" or ln.branch.branch_type == "DIGITAL":
        return rng.weighted({"WALLET": 0.45, "BANK_TRANSFER": 0.35, "CARD": 0.2})
    if ln.prod.product_code in ("SAL_STD", "SAL_PREM"):
        return rng.weighted({"SALARY_DEDUCTION": 0.35, "AUTO_DEBIT": 0.25, "BANK_TRANSFER": 0.25, "CASH": 0.15})
    return rng.weighted({"CASH": 0.28, "BANK_TRANSFER": 0.37, "AUTO_DEBIT": 0.18, "WALLET": 0.12, "CARD": 0.05})


def _restructure(gen: Gen, rng, ln, ledger: Ledger, me: date, state: dict, push) -> dict:
    """Хугацаа сунгах/хүү бууруулах бүтцийн өөрчлөлт: хуучин хуваарь хадгалагдаж, шинэ хувилбар үүснэ."""
    old_items = copy.deepcopy(ledger.items)
    for o, it in zip(old_items, ledger.items):
        o.db_id = None
        o._orig = it
    rtype = rng.weighted({"TERM_EXTENSION": 0.6, "RATE_REDUCTION": 0.25, "PAYMENT_HOLIDAY": 0.15})
    remaining_principal = state["principal_outstanding"]
    unpaid_interest = state["overdue_interest"]
    remaining_terms = len([it for it in ledger.items if it.due > me])
    extension = rng.choice([6, 9, 12])
    new_term = max(3, remaining_terms + extension)
    new_rate = ln.rate if rtype != "RATE_REDUCTION" else round(max(10.0, ln.rate - rng.choice([2, 3, 4, 6])), 1)
    hist_items = []
    for it in ledger.items:
        if it.due <= me:
            nit = copy.copy(it)
            nit.history = [(me, it.principal_paid, it.interest_paid)]
            nit.principal_due = it.principal_paid
            nit.interest_due = it.interest_paid
            nit.status_override = "PAID" if (it.principal_paid > 0 or it.interest_paid > 0) else "RESTRUCTURED"
            nit.paid_on = it.paid_on or me
            nit.closing = nit.opening - nit.principal_due
            hist_items.append(nit)
    first = add_months(me, 1) if rtype != "PAYMENT_HOLIDAY" else add_months(me, 3)
    start_no = (hist_items[-1].no + 1) if hist_items else 1
    new_items = build_schedule(remaining_principal, new_rate, new_term, "ANNUITY", me, first, start_no=start_no)
    if new_items and unpaid_interest > 0:
        new_items[0].interest_due += unpaid_interest
    # Шинэ хуваарийн нээлтийн үлдэгдлийг зээлийн нийт үндсэн дүнтэй уялдуулна
    for it in old_items:
        if it.due > me or not it.is_paid:
            it.status_override = "PAID" if it.is_paid else "RESTRUCTURED"
    ledger.items = hist_items + new_items
    # Өмнөх торгууль, хуваарилалт нь хуучин хувилбарын (v1) мөр рүү заана
    orig_to_old = {id(o._orig): o for o in old_items}
    for c in ledger.charges:
        if c.item is not None and id(c.item) in orig_to_old:
            c.item = orig_to_old[id(c.item)]
    for p in ledger.payments:
        for a in p.allocations:
            if a.item is not None and id(a.item) in orig_to_old:
                a.item = orig_to_old[id(a.item)]
    ln.v1_items = old_items
    for it in new_items:
        if it.due <= gen.as_of:
            push(it.due, "due", it)
    new_maturity = new_items[-1].due if new_items else me
    return {"date": me, "type": rtype, "old_principal": remaining_principal, "new_principal": remaining_principal,
            "old_rate": ln.rate, "new_rate": new_rate, "old_term": remaining_terms, "new_term": new_term,
            "old_maturity": ln.maturity, "new_maturity": new_maturity, "new_items": new_items,
            "capitalized_interest": ZERO, "waived": ZERO}


# ---------------------------------------------------------------------------
# Бичих
# ---------------------------------------------------------------------------

def _write_loan_row(gen: Gen, T, ln, status: str, ledger: Ledger | None, final: dict | None) -> None:
    ref = gen.ref
    prod = ln.prod
    st_id = ref.loan_statuses[status]
    common = dict(id=ln.id, loan_number=gen.number("LOAN", ln.disb or ln.contract), application_id=ln.app_id,
                  customer_id=ln.cust.id, product_id=prod.id, branch_id=ln.branch.id, status_id=st_id,
                  loan_officer_id=ln.officer.id if ln.officer else None, currency_code=ln.currency,
                  principal_amount=ln.principal, interest_rate=ln.rate,
                  rate_type="FIXED", interest_method="SIMPLE", day_count_convention="ACT_365",
                  amortization_type=prod.amortization_type or "ANNUITY", payment_frequency="MONTH",
                  grace_period_days=prod.grace_period_days or 0, contract_date=ln.contract, notes=None,
                  created_at=ts(ln.contract, 12))
    ln.loan_number = common["loan_number"]
    if ledger is None:
        T.loan.add(**common, penalty_rate=round(ln.rate * 0.2, 2), effective_rate=None, term_months=ln.term,
                   disbursement_date=None, first_payment_date=None, maturity_date=None, closed_date=None,
                   disbursed_amount=ZERO, principal_outstanding=ZERO, interest_outstanding=ZERO,
                   penalty_outstanding=ZERO, fee_outstanding=ZERO, principal_paid=ZERO, interest_paid=ZERO,
                   penalty_paid=ZERO, fee_paid=ZERO, written_off_amount=ZERO, overdue_principal=ZERO,
                   overdue_interest=ZERO, days_past_due=0, max_days_past_due=0, first_overdue_date=None,
                   last_payment_date=None, classification_id=None, is_npl=False, is_restructured=False,
                   restructure_count=0, updated_at=ts(ln.contract, 12))
        ln.status = status
        ln.final = None
        return
    paid_payments = [p for p in ledger.payments if p.status == "POSTED"]
    pen_paid = sum((c.paid for c in ledger.charges if c.kind == "PENALTY"), ZERO)
    fee_paid = sum((c.paid for c in ledger.charges if c.kind == "FEE"), ZERO)
    prin_paid = sum((it.principal_paid for it in ledger.items), ZERO)
    int_paid = sum((it.interest_paid for it in ledger.items), ZERO)
    dpd = final["dpd"]
    grade = grade_for_dpd(gen, dpd) if status in ACTIVE_SET or status == "WRITTEN_OFF" else \
        grade_for_dpd(gen, 0)
    rs = ln.restructured
    maturity = rs["new_maturity"] if rs else ln.maturity
    term_months = ln.term if not rs else max(ln.term, (maturity.year - ln.disb.year) * 12 + maturity.month - ln.disb.month)
    fee_pct = next((f["percent_rate"] for f in ref.product_fees if f["product_code"] == prod.product_code
                    and f["fee_code"] == "DISBURSEMENT_FEE"), None)
    eir = round(ln.rate + (float(fee_pct) * 12 / max(ln.term, 1) if fee_pct else 0), 2)
    wo = ln.written_off
    T.loan.add(**common, penalty_rate=ln.penalty_rate, effective_rate=min(eir, 999.0), term_months=term_months,
               disbursement_date=ln.disb, first_payment_date=add_months(ln.disb, 1), maturity_date=maturity,
               closed_date=ln.closed_on, disbursed_amount=ln.principal,
               principal_outstanding=max(final["principal_outstanding"], ZERO),
               interest_outstanding=max(final["interest_outstanding"], ZERO),
               penalty_outstanding=final["penalty_outstanding"], fee_outstanding=final["fee_outstanding"],
               principal_paid=prin_paid, interest_paid=int_paid, penalty_paid=pen_paid, fee_paid=fee_paid,
               written_off_amount=wo["state"]["principal_outstanding"] if wo else ZERO,
               overdue_principal=final["overdue_principal"] if not ln.closed_on else ZERO,
               overdue_interest=final["overdue_interest"] if not ln.closed_on else ZERO,
               days_past_due=final["dpd"], max_days_past_due=final["max_dpd"],
               first_overdue_date=final["first_overdue"] if final["dpd"] > 0 else None,
               last_payment_date=max((p.date for p in paid_payments), default=None),
               classification_id=grade.id if status != "CLOSED" else ref.grades[0].id,
               is_npl=bool(grade.is_npl) if status != "CLOSED" else False,
               is_restructured=rs is not None, restructure_count=1 if rs else 0,
               updated_at=ts(gen.as_of, 20))
    ln.grade = grade


def _write_schedule_and_payments(gen: Gen, rng, T, ln, managers, ceo) -> None:
    ref = gen.ref
    ledger: Ledger = ln.ledger
    as_of = gen.as_of
    # Хуваарийн хувилбарууд
    versions = []
    if ln.v1_items is not None:
        versions.append((1, ln.v1_items, False, "ORIGINAL", ts(ln.disb, 11)))
        versions.append((2, ledger.items, True, "RESTRUCTURE", ts(ln.restructured["date"], 17)))
    else:
        versions.append((1, ledger.items, True, "ORIGINAL", ts(ln.disb, 11)))
    sched_ids = {}
    for ver, items, current, reason, gen_at in versions:
        sid = gen.next_id("loan.loan_schedules")
        sched_ids[ver] = sid
        T.sched.add(id=sid, loan_id=ln.id, schedule_version=ver, is_current=current, generated_reason=reason,
                    total_principal=sum((i.principal_due for i in items), ZERO),
                    total_interest=sum((i.interest_due for i in items), ZERO), total_fee=ZERO,
                    installment_count=len(items), first_due_date=items[0].due, last_due_date=items[-1].due,
                    generated_at=gen_at, generated_by=gen.sys_user_id, created_at=gen_at)
        pen_by_item = {}
        for c in ledger.charges:
            if c.kind == "PENALTY" and c.item is not None:
                k = id(c.item)
                a = pen_by_item.setdefault(k, [ZERO, ZERO])
                a[0] += c.amount
                a[1] += c.paid
        for it in items:
            iid = gen.next_id("loan.loan_schedule_items")
            it.db_id = iid
            if it.status_override and not current:
                status = it.status_override
            elif it.status_override == "RESTRUCTURED":
                status = "RESTRUCTURED"
            elif it.is_paid:
                status = "PAID"
            elif it.principal_paid > 0 or it.interest_paid > 0:
                status = "PARTIALLY_PAID"
            elif it.due < as_of:
                status = "OVERDUE"
            else:
                status = "PENDING"
            paid_date = it.paid_on if status == "PAID" else None
            if status == "PAID" and paid_date is None:
                paid_date = it.due
            if status in ("OVERDUE", "PARTIALLY_PAID") and it.due < as_of and ln.status != "CLOSED":
                item_dpd = (as_of - it.due).days
            elif it.paid_on and it.paid_on > it.due:
                item_dpd = (it.paid_on - it.due).days
            else:
                item_dpd = 0
            pen = pen_by_item.get(id(it), [ZERO, ZERO])
            T.item.add(id=iid, schedule_id=sid, loan_id=ln.id, installment_no=it.no, due_date=it.due,
                       period_start_date=min(it.start, it.due), period_end_date=it.due,
                       principal_due=it.principal_due, interest_due=it.interest_due, fee_due=ZERO, insurance_due=ZERO,
                       principal_paid=it.principal_paid, interest_paid=it.interest_paid, fee_paid=ZERO,
                       insurance_paid=ZERO, penalty_charged=pen[0], penalty_paid=pen[1], opening_balance=it.opening,
                       closing_balance=max(it.closing, ZERO), status=status, paid_date=paid_date,
                       days_past_due=item_dpd, created_at=gen_at, updated_at=ts(min(as_of, it.paid_on or it.due), 18))
    ln.schedule_ids = sched_ids
    # Олголт, гэрээ, хамтран зээлдэгч
    mgr = managers.get(ln.branch.id) or ceo
    method = "INTERNAL_ACCOUNT" if ln.channel in ("MOBILE",) or rng.chance(0.35) else \
        rng.weighted({"BANK_TRANSFER": 0.75, "CASH": 0.2, "WALLET": 0.05})
    ln.disb_method = method
    did = gen.next_id("loan.loan_disbursements")
    dest = getattr(ln.cust, "bank_account_id", None) if method == "BANK_TRANSFER" else None
    T.disb.add(id=did, loan_id=ln.id, currency_code=ln.currency, disbursement_no=1, amount=ln.principal,
               disbursement_date=ln.disb, value_date=ln.disb, method=method, destination_account_id=dest,
               internal_account_id=None, status="EXECUTED", executed_at=ts(ln.disb, 14), approved_by=mgr.id,
               created_at=ts(ln.disb, 11), updated_at=ts(ln.disb, 14))
    ln.disb_row_index = len(T.disb.rows) - 1
    T.borrower.add(id=gen.next_id("loan.loan_borrowers"), loan_id=ln.id, customer_id=ln.cust.id,
                   borrower_role="PRIMARY", liability_share=100, is_active=ln.status != "CLOSED",
                   created_at=ts(ln.contract, 12))
    cid = gen.uuid()
    T.contract.add(id=cid, loan_id=ln.id, contract_number=gen.number("LOAN_CONTRACT", ln.contract), contract_type="LOAN",
                   parent_contract_id=None, signed_date=ln.contract, effective_date=ln.disb, expiry_date=ln.maturity,
                   signed_by_customer=True, signed_by_officer_id=ln.officer.id if ln.officer else None,
                   signature_method="OTP" if ln.channel == "MOBILE" else rng.weighted({"WET_INK": 0.8,
                                                                                        "DIGITAL_ID": 0.2}),
                   status={"CLOSED": "TERMINATED", "WRITTEN_OFF": "TERMINATED"}.get(ln.status, "ACTIVE")
                   if not ln.restructured else "AMENDED",
                   terms_snapshot={"principal": str(ln.principal), "rate": ln.rate, "term": ln.term,
                                   "currency": ln.currency},
                   created_at=ts(ln.contract, 12), updated_at=ts(ln.closed_on or ln.contract, 12))
    ln.contract_id = cid
    # Шимтгэл, торгууль
    for c in ledger.charges:
        status = "PAID" if c.due <= 0 and c.paid > 0 else ("PARTIALLY_PAID" if c.paid > 0 else "CHARGED")
        if c.kind == "FEE":
            waived = ZERO
            if status == "CHARGED" and rng.chance(0.05):
                waived = c.amount
                status = "WAIVED"
            T.fee.add(id=gen.next_id("loan.loan_fees"), loan_id=ln.id, fee_type_id=ref.fee_types[c.code],
                      amount=c.amount, currency_code=ln.currency, charged_date=c.charged, due_date=c.charged,
                      paid_amount=c.paid, waived_amount=waived, waived_by=mgr.id if waived > 0 else None,
                      waive_reason="Урамшуулал" if waived > 0 else None, status=status,
                      created_at=ts(c.charged, 12), updated_at=ts(c.paid_on or c.charged, 12))
        else:
            T.pen.add(id=gen.next_id("loan.loan_penalties"), loan_id=ln.id,
                      schedule_item_id=c.item.db_id if c.item is not None else None, currency_code=ln.currency,
                      penalty_type="LATE_PAYMENT", base_amount=c.base, penalty_rate=ln.penalty_rate,
                      days_overdue=c.days, amount=c.amount, charged_date=c.charged, paid_amount=c.paid,
                      waived_amount=ZERO, waived_by=None, waive_reason=None, status=status,
                      created_at=ts(c.charged, 12), updated_at=ts(c.paid_on or c.charged, 12))
    # Төлбөр ба хуваарилалт
    tellers = [e for e in gen.employees if e.branch.id == ln.branch.id and e.position == "TELLER"]
    for p in sorted(ledger.payments, key=lambda x: x.date):
        pid = gen.next_id("loan.loan_payments")
        p.db_id = pid
        p.payment_no = gen.number("LOAN_PAYMENT", p.date)
        alloc = {"PRINCIPAL": ZERO, "INTEREST": ZERO, "PENALTY": ZERO, "FEE": ZERO}
        for a in p.allocations:
            alloc["INTEREST" if a.component in ("INTEREST", "OVERDUE_INTEREST") else a.component] += a.amount
        if p.status == "FAILED":
            alloc = {k: ZERO for k in alloc}
        T.pay.add(id=pid, payment_no=p.payment_no, loan_id=ln.id, currency_code=ln.currency, customer_id=ln.cust.id,
                  branch_id=ln.branch.id, amount=p.amount, payment_date=p.date, value_date=p.date,
                  payment_method=p.method, payment_type=p.ptype, allocated_principal=alloc["PRINCIPAL"],
                  allocated_interest=alloc["INTEREST"], allocated_penalty=alloc["PENALTY"],
                  allocated_fee=alloc["FEE"], unallocated_amount=p.unallocated if p.status != "FAILED" else p.amount,
                  transaction_id=None, status=p.status,
                  received_by=rng.choice(tellers).id if p.method == "CASH" and tellers else None,
                  notes="Данснаас хасагдсан зээлийн нөхөн төлөлт" if p.ptype == "RECOVERY" else None,
                  created_at=ts_rand(rng, p.date), updated_at=ts(p.date, 18))
        p.row_index = len(T.pay.rows) - 1
        if p.status != "POSTED":
            continue
        for order, a in enumerate(p.allocations, 1):
            T.alloc.add(id=gen.next_id("loan.loan_payment_allocations"), payment_id=pid,
                        schedule_item_id=a.item.db_id if a.item is not None else None, component=a.component,
                        amount=a.amount, allocation_order=order, created_at=ts(p.date, 18))
    # Бүтцийн өөрчлөлт
    rs = ln.restructured
    if rs:
        new_contract = gen.uuid()
        T.contract.add(id=new_contract, loan_id=ln.id, contract_number=gen.number("LOAN_CONTRACT", rs["date"]),
                       contract_type="RESTRUCTURE", parent_contract_id=cid, signed_date=rs["date"],
                       effective_date=rs["date"], expiry_date=rs["new_maturity"], signed_by_customer=True,
                       signed_by_officer_id=ln.officer.id if ln.officer else None, signature_method="WET_INK",
                       status="ACTIVE" if ln.status not in ("CLOSED", "WRITTEN_OFF") else "TERMINATED",
                       terms_snapshot={"rate": rs["new_rate"], "term": rs["new_term"]},
                       created_at=ts(rs["date"], 15), updated_at=ts(rs["date"], 15))
        T.restr.add(id=gen.next_id("loan.loan_restructures"), loan_id=ln.id, restructure_no=1,
                    restructure_type=rs["type"],
                    reason=rng.choice(["Орлого буурсан", "Бизнесийн үйл ажиллагаа саатсан", "Өвчний улмаас",
                                       "Ажлын байраа алдсан"]),
                    old_principal=rs["old_principal"], new_principal=rs["new_principal"], old_rate=rs["old_rate"],
                    new_rate=rs["new_rate"], old_term_months=rs["old_term"], new_term_months=rs["new_term"],
                    old_maturity_date=rs["old_maturity"], new_maturity_date=rs["new_maturity"],
                    capitalized_interest=ZERO, capitalized_penalty=ZERO, waived_amount=ZERO,
                    old_schedule_id=sched_ids[1], new_schedule_id=sched_ids[2], new_contract_id=new_contract,
                    effective_date=rs["date"], approved_by=mgr.id, approval_ref=f"ЗХ-{rs['date'].year}/R{ln.n:04d}",
                    status="EXECUTED", created_at=ts(rs["date"], 15), updated_at=ts(rs["date"], 15))
        if rs["new_rate"] != rs["old_rate"]:
            T.rate_hist.add(id=gen.next_id("loan.loan_interest_rate_history"), loan_id=ln.id,
                            old_rate=rs["old_rate"], new_rate=rs["new_rate"], rate_type="FIXED",
                            change_reason="RESTRUCTURE", effective_from=rs["date"], effective_to=None,
                            approved_by=mgr.id, changed_at=ts(rs["date"], 15))


def _write_history(gen: Gen, T, ln, hist) -> None:
    ref = gen.ref
    for old, new, when, reason, outstanding, dpd in hist:
        T.hist.add(id=gen.next_id("loan.loan_status_history"), loan_id=ln.id,
                   old_status_id=ref.loan_statuses[old] if old else None, new_status_id=ref.loan_statuses[new],
                   change_reason=reason, outstanding_at_change=outstanding, dpd_at_change=dpd, changed_at=when,
                   changed_by=gen.sys_user_id)


def _bucket(dpd: int) -> str:
    if dpd <= 0:
        return "CURRENT"
    if dpd <= 30:
        return "DPD_1_30"
    if dpd <= 60:
        return "DPD_31_60"
    if dpd <= 90:
        return "DPD_61_90"
    if dpd <= 180:
        return "DPD_91_180"
    return "DPD_180_PLUS"


def _write_snapshots(gen: Gen, T, ln, snapshots, accruals, classifications) -> None:
    ln.accrual_rows = []
    for me, dpd, state, total, grade in snapshots:
        T.ovd.add(id=gen.next_id("loan.loan_overdues"), loan_id=ln.id, snapshot_date=me, days_past_due=dpd,
                  dpd_bucket=_bucket(dpd), overdue_principal=state["overdue_principal"],
                  overdue_interest=state["overdue_interest"], overdue_penalty=state["penalty_outstanding"],
                  overdue_fee=state["fee_outstanding"], total_outstanding=max(total, ZERO),
                  oldest_due_date=state["oldest_due"], classification_id=grade.id, created_at=ts(me, 23))
    for me, base, days, suspended in accruals:
        interest = (base * Decimal(str(ln.rate)) / Decimal(100) * Decimal(days) / Decimal(365)).quantize(
            Decimal("0.0001"))
        idx = len(T.accr.rows)
        T.accr.add(id=gen.next_id("loan.loan_interest_accruals"), loan_id=ln.id, accrual_date=me, principal_base=base,
                   annual_rate=ln.rate, day_count=days, interest_amount=interest, penalty_amount=ZERO, is_posted=True,
                   journal_entry_id=None, is_suspended=bool(suspended), created_at=ts(me, 23))
        ln.accrual_rows.append((idx, me, interest, bool(suspended)))
    for me, old, new, dpd, total in classifications:
        T.cls.add(id=gen.next_id("loan.loan_classifications"), loan_id=ln.id,
                  old_classification_id=old.id if old else None, new_classification_id=new.id, days_past_due=dpd,
                  outstanding_amount=max(total, ZERO), provision_rate=new.provision_rate,
                  provision_amount=D(max(total, ZERO) * Decimal(str(new.provision_rate)) / 100),
                  classification_date=me, is_manual_override=False, created_at=ts(me, 23))


def _write_writeoff(gen: Gen, rng, T, ln, ceo) -> None:
    wo = ln.written_off
    s = wo["state"]
    recovered = sum((p.amount for p in ln.ledger.payments if p.ptype == "RECOVERY" and p.status == "POSTED"), ZERO)
    wid = gen.next_id("loan.loan_writeoffs")
    ln.writeoff_id = wid
    T.wo.add(id=wid, loan_id=ln.id, writeoff_date=wo["date"], principal_amount=s["principal_outstanding"],
             interest_amount=s["overdue_interest"], penalty_amount=s["penalty_outstanding"], fee_amount=ZERO,
             provision_used=s["principal_outstanding"], writeoff_type="FULL",
             reason="360-аас дээш хоног хэтэрсэн, барагдуулах боломж хязгаарлагдмал",
             board_resolution_ref=f"ТУЗ-{wo['date'].year}/{rng.randint(1, 40):02d}", approved_by=ceo.id,
             is_recoverable=True, recovered_amount=recovered, created_at=ts(wo["date"], 17),
             updated_at=ts(gen.as_of, 17))
