"""Лавлах өгөгдөл: DB-ээс унших (016_seed) ба нэмэлт лавлах мөр (салбар, бүтээгдэхүүн, ханш...)."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

from .context import IDENTITY_TABLES, Gen
from .util import D, add_months, holiday_name, is_business_day, month_end, ts


def _rows(cur, sql: str) -> list[dict]:
    cur.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def _ns(rows: list[dict], key: str) -> dict:
    return {r[key]: SimpleNamespace(**r) for r in rows}


def load_refs(gen: Gen, conn) -> None:
    with conn.cursor() as cur:
        r = gen.ref
        r.branches = _ns(_rows(cur, "SELECT id, branch_code, branch_name, branch_type, province, district, "
                                    "opened_date, gl_prefix FROM core.branches"), "branch_code")
        r.departments = {x["department_code"]: x["id"] for x in _rows(cur, "SELECT id, department_code FROM core.departments")}
        r.positions = _ns(_rows(cur, "SELECT id, position_code, approval_limit FROM core.positions"), "position_code")
        r.roles = {x["role_code"]: x["id"] for x in _rows(cur, "SELECT id, role_code FROM core.roles")}
        r.customer_types = _ns(_rows(cur, "SELECT id, type_code, party_type::text AS party_type "
                                          "FROM customer.customer_types"), "type_code")
        r.customer_statuses = {x["status_code"]: x["id"] for x in _rows(cur, "SELECT id, status_code FROM customer.customer_statuses")}
        r.segments = _ns(_rows(cur, "SELECT id, segment_code, min_income FROM customer.customer_segments"), "segment_code")
        r.product_types = _ns(_rows(cur, "SELECT id, type_code, category FROM product.product_types"), "type_code")
        r.products = _ns(_rows(cur, """
            SELECT p.id, p.product_code, p.product_name, pt.type_code, pt.category, p.currency_code,
                   p.amortization_type::text AS amortization_type, p.grace_period_days, p.penalty_grace_days,
                   p.requires_collateral, p.requires_guarantor, p.min_credit_score, p.max_dti_ratio,
                   p.max_ltv_ratio, p.launch_date, p.discontinue_date, p.status,
                   pr.annual_rate, pr.min_amount, pr.max_amount, pr.min_term_months, pr.max_term_months
              FROM product.products p
              JOIN product.product_types pt ON pt.id = p.product_type_id
              LEFT JOIN product.product_rates pr ON pr.product_id = p.id"""), "product_code")
        r.fee_types = {x["fee_code"]: x["id"] for x in _rows(cur, "SELECT id, fee_code FROM product.fee_types")}
        r.product_fees = _rows(cur, """
            SELECT p.product_code, ft.fee_code, pf.calc_method, pf.fixed_amount, pf.percent_rate
              FROM product.product_fees pf JOIN product.products p ON p.id = pf.product_id
              JOIN product.fee_types ft ON ft.id = pf.fee_type_id""")
        r.app_statuses = {x["status_code"]: x["id"] for x in _rows(cur, "SELECT id, status_code FROM loan.loan_application_statuses")}
        r.loan_statuses = {x["status_code"]: x["id"] for x in _rows(cur, "SELECT id, status_code FROM loan.loan_statuses")}
        r.grades = [SimpleNamespace(**x) for x in _rows(cur, """
            SELECT id, grade_code, min_dpd, max_dpd, provision_rate, is_npl FROM loan.classification_grades
             ORDER BY min_dpd""")]
        r.transitions = {(x["f"], x["t"]) for x in _rows(cur, """
            SELECT f.status_code AS f, t.status_code AS t FROM loan.loan_status_transitions tr
              JOIN loan.loan_statuses f ON f.id = tr.from_status_id
              JOIN loan.loan_statuses t ON t.id = tr.to_status_id""")}
        r.collateral_types = _ns(_rows(cur, """
            SELECT id, type_code, category, max_ltv_ratio, haircut_pct, revaluation_months,
                   requires_insurance, requires_registration FROM collateral.collateral_types"""), "type_code")
        r.account_types = _ns(_rows(cur, "SELECT id, type_code, account_class FROM account.account_types"), "type_code")
        r.channels = {x["channel_code"]: x["id"] for x in _rows(cur, "SELECT id, channel_code FROM account.transaction_channels")}
        r.txn_statuses = {x["status_code"]: x["id"] for x in _rows(cur, "SELECT id, status_code FROM account.transaction_statuses")}
        r.txn_types = _ns(_rows(cur, "SELECT id, type_code, direction::text AS direction, category "
                                     "FROM account.transaction_types"), "type_code")
        r.payment_methods = {x["method_code"]: x["id"] for x in _rows(cur, "SELECT id, method_code FROM account.payment_methods")}
        r.coa = _ns(_rows(cur, "SELECT id, account_code, account_type::text AS account_type, "
                               "normal_balance::text AS normal_balance FROM accounting.chart_of_accounts"), "account_code")
        r.journals = {x["journal_code"]: x["id"] for x in _rows(cur, "SELECT id, journal_code FROM accounting.journals")}
        r.periods = _ns(_rows(cur, "SELECT id, period_code, start_date, end_date FROM accounting.accounting_periods"), "period_code")
        r.risk_grades = [SimpleNamespace(**x) for x in _rows(cur, """
            SELECT id, grade_code, min_score, max_score, pd_estimate FROM risk.risk_grades ORDER BY min_score DESC""")]
        r.score_models = {x["model_code"]: x["id"] for x in _rows(cur, "SELECT id, model_code FROM risk.credit_score_models")}
        r.fraud_rules = {x["rule_code"]: x["id"] for x in _rows(cur, "SELECT id, rule_code FROM risk.fraud_rules")}
        r.stages = [SimpleNamespace(**x) for x in _rows(cur, """
            SELECT id, stage_code, min_dpd, max_dpd FROM collection.collection_stages ORDER BY min_dpd""")]
        r.action_types = _ns(_rows(cur, "SELECT id, action_code, channel, cost_estimate FROM collection.action_types"), "action_code")
        r.agencies = _ns(_rows(cur, "SELECT id, agency_code, commission_rate FROM collection.collection_agencies"), "agency_code")
        r.aml_rules = _ns(_rows(cur, "SELECT id, rule_code, severity FROM kyc.aml_rules"), "rule_code")
        r.sanction_lists = {x["list_code"]: x["id"] for x in _rows(cur, "SELECT id, list_code FROM kyc.sanction_lists")}
        r.doc_types = {x["type_code"]: x["id"] for x in _rows(cur, "SELECT id, type_code FROM system.document_types")}
        for x in _rows(cur, "SELECT sequence_code, prefix, suffix, padding_length, include_year, reset_period "
                            "FROM system.number_sequences"):
            gen.seq_format[x["sequence_code"]] = SimpleNamespace(**x)
        max_ids = {}
        for table in IDENTITY_TABLES:
            cur.execute(f"SELECT COALESCE(max(id), 0) FROM {table}")
            max_ids[table] = cur.fetchone()[0]
        gen.set_max_ids(max_ids)
        # хуваарийн мөрийн ID-г хэмнэлттэй хадгалахын тулд sequence-тэй хүснэгтүүд 1-ээс эхэлнэ
        for table in ("loan.loan_interest_accruals", "loan.loan_overdues", "account.transactions",
                      "account.account_balances", "accounting.journal_lines"):
            cur.execute(f"SELECT COALESCE(max(id), 0) FROM {table}")
            gen.next_ids[table] = cur.fetchone()[0] + 1


# ---------------------------------------------------------------------------
# Нэмэлт лавлах өгөгдөл
# ---------------------------------------------------------------------------

NEW_BRANCHES = [
    # code, name, type, province, district, opened, parent, closed
    ("UB03", "Хан-Уул салбар", "BRANCH", "Улаанбаатар", "Хан-Уул", date(2018, 3, 12), "HO", None),
    ("UB04", "Чингэлтэй салбар", "BRANCH", "Улаанбаатар", "Чингэлтэй", date(2019, 8, 1), "HO", None),
    ("UB05", "Баянгол салбар", "BRANCH", "Улаанбаатар", "Баянгол", date(2020, 2, 17), "HO", None),
    ("UB06", "Сонгинохайрхан салбар", "BRANCH", "Улаанбаатар", "Сонгинохайрхан", date(2022, 5, 9), "HO", None),
    ("UB07", "Налайх тооцооны төв", "SUB_BRANCH", "Улаанбаатар", "Налайх", date(2023, 1, 16), "UB02", None),
    ("UB08", "Баянгол-2 тооцооны төв", "SUB_BRANCH", "Улаанбаатар", "Баянгол", date(2021, 4, 5), "UB05",
     date(2025, 3, 31)),
    ("KH01", "Ховд салбар", "BRANCH", "Ховд", "Жаргалант", date(2021, 9, 1), "HO", None),
    ("DO01", "Чойбалсан салбар", "BRANCH", "Дорнод", "Хэрлэн", date(2022, 3, 15), "HO", None),
    ("KG01", "Мөрөн салбар", "BRANCH", "Хөвсгөл", "Мөрөн", date(2023, 4, 3), "HO", None),
    ("UV01", "Арвайхээр салбар", "BRANCH", "Өвөрхангай", "Арвайхээр", date(2023, 10, 2), "HO", None),
    ("OG01", "Даланзадгад салбар", "BRANCH", "Өмнөговь", "Даланзадгад", date(2024, 6, 17), "HO", None),
    ("DG01", "Дижитал салбар", "DIGITAL", "Улаанбаатар", "Сүхбаатар", date(2024, 2, 1), "HO", None),
]

# Салбар бүрийн "жин" — харилцагч, зээлийн тархалт
BRANCH_WEIGHTS = {"HO": 0.02, "UB01": 0.13, "UB02": 0.12, "UB03": 0.10, "UB04": 0.07, "UB05": 0.08,
                  "UB06": 0.07, "UB07": 0.03, "UB08": 0.02, "DR01": 0.07, "ER01": 0.07, "KH01": 0.04,
                  "DO01": 0.03, "KG01": 0.03, "UV01": 0.03, "OG01": 0.03, "DG01": 0.06}

NEW_PRODUCT_TYPES = [("AGRI_LOAN", "Хөдөө аж ахуйн зээл", "LOAN")]

NEW_PRODUCTS = [
    # code, name, type, ccy, amort, grace, coll, guar, score, dti, ltv, launch, discontinue, status,
    # rate, min_amt, max_amt, min_t, max_t, early_fee_pct, disb_fee_pct
    ("CONS_OLD", "Хэрэглээний зээл (2023 оны нөхцөл)", "CONSUMER_LOAN", "MNT", "ANNUITY", 0, False, True,
     540, 60.0, None, date(2022, 1, 1), date(2025, 3, 31), "DISCONTINUED", 33.6, 300000, 20000000, 3, 30, None, 1.5),
    ("AGRO_HERD", "Малчны зээл", "AGRI_LOAN", "MNT", "EQUAL_PRINCIPAL", 0, True, False,
     500, 55.0, 50.0, date(2024, 1, 1), None, "ACTIVE", 18.0, 1000000, 60000000, 6, 24, None, 1.0),
    ("BIZ_USD", "Бизнесийн зээл (ам.доллар)", "BUSINESS_LOAN", "USD", "ANNUITY", 30, True, True,
     620, None, 60.0, date(2024, 1, 1), None, "ACTIVE", 14.4, 10000, 500000, 6, 36, None, 1.0),
    ("DEP_DEMAND", "Хугацаагүй хадгаламж", "DEMAND_DEPOSIT", "MNT", None, 0, False, False,
     None, None, None, date(2024, 1, 1), None, "ACTIVE", 6.0, 10000, None, None, None, None, None),
    ("DEP_CHILD", "Хүүхдийн хадгаламж", "TERM_DEPOSIT", "MNT", None, 0, False, False,
     None, None, None, date(2024, 1, 1), None, "ACTIVE", 13.2, 50000, 200000000, 12, 120, None, None),
    ("DEP_TERM_USD", "Ам.долларын хугацаатай хадгаламж", "TERM_DEPOSIT", "USD", None, 0, False, False,
     None, None, None, date(2024, 1, 1), None, "ACTIVE", 4.8, 500, 1000000, 6, 24, None, None),
    ("CUR_MNT", "Харилцах данс (төгрөг)", "CURRENT_ACC", "MNT", None, 0, False, False,
     None, None, None, date(2015, 3, 1), None, "ACTIVE", 0.0, None, None, None, None, None, None),
    ("CUR_USD", "Харилцах данс (ам.доллар)", "CURRENT_ACC", "USD", None, 0, False, False,
     None, None, None, date(2015, 3, 1), None, "ACTIVE", 0.0, None, None, None, None, None, None),
]

FX_START = {"USD": 3410.0, "EUR": 3700.0, "CNY": 472.0, "KRW": 2.58, "JPY": 23.2}
FX_DRIFT = {"USD": 0.00004, "EUR": 0.00003, "CNY": 0.00002, "KRW": -0.00001, "JPY": -0.00004}


def add_reference_rows(gen: Gen) -> None:
    rng = gen.rng.child("refdata")
    st = gen.store
    r = gen.ref

    # --- салбар
    br = st.table("core.branches", ["id", "branch_code", "branch_name", "branch_type", "parent_branch_id",
                                    "country_code", "province", "district", "address_line", "phone", "gl_prefix",
                                    "opened_date", "closed_date", "is_active", "created_at", "updated_at"])
    next_prefix = 5
    for code, name, btype, prov, dist, opened, parent, closed in NEW_BRANCHES:
        bid = gen.next_id("core.branches")
        parent_id = r.branches[parent].id if parent in r.branches else None
        gl = f"{next_prefix:02d}"
        next_prefix += 1
        br.add(id=bid, branch_code=code, branch_name=name, branch_type=btype, parent_branch_id=parent_id,
               country_code="MN", province=prov, district=dist,
               address_line=f"{dist}, {rng.randint(1, 20)}-р хороо", phone=f"+976 7000 {1005 + bid:04d}",
               gl_prefix=gl, opened_date=opened, closed_date=closed, is_active=closed is None,
               created_at=ts(opened), updated_at=ts(closed or opened))
        r.branches[code] = SimpleNamespace(id=bid, branch_code=code, branch_name=name, branch_type=btype,
                                           province=prov, district=dist, opened_date=opened, gl_prefix=gl,
                                           closed_date=closed)
    for b in r.branches.values():
        if not hasattr(b, "closed_date"):
            b.closed_date = None
        b.weight = BRANCH_WEIGHTS.get(b.branch_code, 0.03)
    gen.branches = sorted(r.branches.values(), key=lambda b: b.id)

    # --- бүтээгдэхүүний төрөл ба бүтээгдэхүүн
    ptype = st.table("product.product_types", ["id", "type_code", "type_name", "category", "description"])
    for code, name, cat in NEW_PRODUCT_TYPES:
        pid = gen.next_id("product.product_types")
        ptype.add(id=pid, type_code=code, type_name=name, category=cat, description=name)
        r.product_types[code] = SimpleNamespace(id=pid, type_code=code, category=cat)

    prod = st.table("product.products", [
        "id", "product_code", "product_name", "product_type_id", "currency_code", "amortization_type",
        "interest_method", "day_count_convention", "repayment_period_unit", "grace_period_days", "penalty_grace_days",
        "requires_collateral", "requires_guarantor", "min_credit_score", "max_dti_ratio", "max_ltv_ratio",
        "launch_date", "discontinue_date", "status", "description", "created_at", "updated_at"])
    rates = st.table("product.product_rates", ["id", "product_id", "rate_type", "annual_rate", "min_amount",
                                                "max_amount", "min_term_months", "max_term_months", "valid_from",
                                                "valid_to", "is_active", "created_at", "updated_at"])
    fees = st.table("product.product_fees", ["id", "product_id", "fee_type_id", "calc_method", "fixed_amount",
                                             "percent_rate", "currency_code", "valid_from", "is_active", "created_at"])
    limits = st.table("product.product_limits", ["id", "product_id", "min_amount", "max_amount",
                                                 "min_term_months", "max_term_months", "max_active_loans",
                                                 "valid_from", "created_at"])
    alloc = st.table("product.payment_allocation_rules", ["id", "product_id", "allocation_order", "component",
                                                          "valid_from", "created_at"])
    avail = st.table("product.product_branch_availability", ["product_id", "branch_id", "is_available",
                                                             "valid_from", "created_at"])
    new_product_ids = []
    for (code, name, ptc, ccy, amort, grace, coll, guar, score, dti, ltv, launch, disc, status,
         rate, min_amt, max_amt, min_t, max_t, early_fee, disb_fee) in NEW_PRODUCTS:
        pid = gen.next_id("product.products")
        cat = r.product_types[ptc].category
        prod.add(id=pid, product_code=code, product_name=name, product_type_id=r.product_types[ptc].id,
                 currency_code=ccy, amortization_type=amort, interest_method="SIMPLE" if cat == "LOAN" else None,
                 day_count_convention="ACT_365", repayment_period_unit="MONTH" if cat == "LOAN" else None,
                 grace_period_days=grace, penalty_grace_days=5 if cat == "LOAN" else 0,
                 requires_collateral=coll, requires_guarantor=guar, min_credit_score=score, max_dti_ratio=dti,
                 max_ltv_ratio=ltv, launch_date=launch, discontinue_date=disc, status=status,
                 description=name, created_at=ts(launch), updated_at=ts(disc or launch))
        rid = gen.next_id("product.product_rates")
        rates.add(id=rid, product_id=pid, rate_type="FIXED", annual_rate=rate, min_amount=min_amt,
                  max_amount=max_amt, min_term_months=min_t, max_term_months=max_t, valid_from=launch,
                  valid_to=disc, is_active=disc is None, created_at=ts(launch), updated_at=ts(launch))
        if cat == "LOAN":
            limits.add(id=gen.next_id("product.product_limits"), product_id=pid, min_amount=min_amt,
                       max_amount=max_amt, min_term_months=min_t, max_term_months=max_t, max_active_loans=3,
                       valid_from=launch, created_at=ts(launch))
            for order, comp in enumerate(["PENALTY", "FEE", "OVERDUE_INTEREST", "INTEREST", "PRINCIPAL"], 1):
                alloc.add(id=gen.next_id("product.payment_allocation_rules"), product_id=pid,
                          allocation_order=order, component=comp, valid_from=date(2024, 1, 1),
                          created_at=ts(launch))
            if disb_fee:
                fees.add(id=gen.next_id("product.product_fees"), product_id=pid,
                         fee_type_id=r.fee_types["DISBURSEMENT_FEE"], calc_method="PERCENT_OF_PRINCIPAL",
                         percent_rate=disb_fee, currency_code=ccy, valid_from=launch, is_active=disc is None,
                         created_at=ts(launch))
        r.products[code] = SimpleNamespace(
            id=pid, product_code=code, product_name=name, type_code=ptc, category=cat, currency_code=ccy,
            amortization_type=amort, grace_period_days=grace, penalty_grace_days=5 if cat == "LOAN" else 0,
            requires_collateral=coll, requires_guarantor=guar, min_credit_score=score, max_dti_ratio=dti,
            max_ltv_ratio=ltv, launch_date=launch, discontinue_date=disc, status=status, annual_rate=rate,
            min_amount=min_amt, max_amount=max_amt, min_term_months=min_t, max_term_months=max_t)
        r.product_fees.append({"product_code": code, "fee_code": "DISBURSEMENT_FEE",
                               "calc_method": "PERCENT_OF_PRINCIPAL", "fixed_amount": None,
                               "percent_rate": disb_fee}) if disb_fee else None
        new_product_ids.append(pid)

    old_branch_ids = {b.id for b in gen.branches if b.branch_code in ("HO", "UB01", "UB02", "DR01", "ER01")}
    for p in r.products.values():
        for b in gen.branches:
            if p.id in new_product_ids or b.id not in old_branch_ids:
                avail.add(product_id=p.id, branch_id=b.id, is_available=b.closed_date is None,
                          valid_from=max(b.opened_date, date(2024, 1, 1)), created_at=ts(date(2024, 1, 1)))

    # --- хадгаламжийн хүүгийн шат
    drates = st.table("deposit.deposit_rates", ["id", "product_id", "deposit_type", "currency_code", "min_amount",
                                                "max_amount", "min_term_months", "max_term_months", "annual_rate",
                                                "early_withdrawal_rate", "valid_from", "is_active", "created_at"])
    tiers = [("DEP_TERM", "TERM", "MNT", [(3, 5, 10.8), (6, 11, 12.0), (12, 17, 13.2), (18, 36, 13.8)]),
             ("DEP_CHILD", "CHILD", "MNT", [(12, 120, 13.2)]),
             ("DEP_DEMAND", "DEMAND", "MNT", [(None, None, 6.0)]),
             ("DEP_TERM_USD", "TERM", "USD", [(6, 11, 4.2), (12, 24, 4.8)])]
    for code, dtype, ccy, rows in tiers:
        for min_t, max_t, rate in rows:
            drates.add(id=gen.next_id("deposit.deposit_rates"), product_id=r.products[code].id, deposit_type=dtype,
                       currency_code=ccy, min_amount=0, max_amount=None, min_term_months=min_t,
                       max_term_months=max_t, annual_rate=rate, early_withdrawal_rate=round(rate * 0.25, 2),
                       valid_from=date(2024, 1, 1), is_active=True, created_at=ts(date(2024, 1, 1)))

    # --- 2024 оны ажлын өдрийн хуанли ба тайлант үе
    cal = st.table("system.business_calendar", ["calendar_date", "is_business_day", "is_holiday", "holiday_name",
                                                "country_code"])
    d = date(2024, 1, 1)
    while d <= date(2024, 12, 31):
        hn = holiday_name(d)
        cal.add(calendar_date=d, is_business_day=is_business_day(d), is_holiday=hn is not None, holiday_name=hn,
                country_code="MN")
        d += timedelta(days=1)
    per = st.table("accounting.accounting_periods", ["id", "period_code", "fiscal_year", "period_number",
                                                     "start_date", "end_date", "status", "created_at", "updated_at"])
    for m in range(1, 13):
        start = date(2024, m, 1)
        pid = gen.next_id("accounting.accounting_periods")
        per.add(id=pid, period_code=f"2024-{m:02d}", fiscal_year=2024, period_number=m, start_date=start,
                end_date=month_end(start), status="OPEN", created_at=ts(start), updated_at=ts(start))
        r.periods[f"2024-{m:02d}"] = SimpleNamespace(id=pid, period_code=f"2024-{m:02d}", start_date=start,
                                                     end_date=month_end(start))

    # --- ханшийн түүх (ажлын өдөр бүр, Монголбанк)
    fxt = st.table("system.exchange_rates", ["id", "from_currency", "to_currency", "rate_date", "buy_rate",
                                             "sell_rate", "mid_rate", "official_rate", "source", "is_active",
                                             "created_at"])
    level = dict(FX_START)
    d = date(2024, 1, 1)
    last = {}
    while d <= gen.as_of:
        if is_business_day(d):
            for ccy, value in level.items():
                shock = rng.gauss(FX_DRIFT[ccy], 0.0035)
                value = value * (1 + shock)
                level[ccy] = value
                mid = round(value, 2) if value > 10 else round(value, 4)
                spread = mid * 0.004
                buy = round(mid - spread, 4)
                sell = round(mid + spread, 4)
                fxt.add(id=gen.next_id("system.exchange_rates"), from_currency=ccy, to_currency="MNT",
                        rate_date=d, buy_rate=buy, sell_rate=sell, mid_rate=mid, official_rate=mid,
                        source="CENTRAL_BANK", is_active=True, created_at=ts(d, 11))
                last[ccy] = mid
        for ccy in FX_START:
            if ccy in last:
                gen.fx[(ccy, d)] = last[ccy]
        d += timedelta(days=1)


def fx_rate(gen: Gen, ccy: str, on: date) -> float:
    """ccy → MNT ханш (тухайн өдөр эсвэл түүнээс өмнөх сүүлийн)."""
    if ccy == "MNT":
        return 1.0
    d = on
    for _ in range(40):
        if (ccy, d) in gen.fx:
            return gen.fx[(ccy, d)]
        d -= timedelta(days=1)
    return FX_START[ccy]


def to_mnt(gen: Gen, amount, ccy: str, on: date):
    return D(float(amount) * fx_rate(gen, ccy, on)) if ccy != "MNT" else D(amount)


def period_for(gen: Gen, d: date) -> SimpleNamespace:
    return gen.ref.periods[f"{d.year}-{d.month:02d}"]


def grade_for_dpd(gen: Gen, dpd: int) -> SimpleNamespace:
    chosen = gen.ref.grades[0]
    for g in gen.ref.grades:
        if dpd >= g.min_dpd and (g.max_dpd is None or dpd <= g.max_dpd):
            chosen = g
    return chosen


def risk_grade_for_score(gen: Gen, score: int) -> SimpleNamespace:
    for g in gen.ref.risk_grades:
        if g.min_score <= score <= g.max_score:
            return g
    return gen.ref.risk_grades[-1]


def stage_for_dpd(gen: Gen, dpd: int) -> SimpleNamespace:
    chosen = gen.ref.stages[0]
    for s in gen.ref.stages:
        if dpd >= s.min_dpd and (s.max_dpd is None or dpd <= s.max_dpd):
            chosen = s
    return chosen


def months_back(d: date, n: int) -> date:
    return add_months(d, -n)
