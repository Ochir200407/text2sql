"""Ажилтан, хэрэглэгч, харилцагч (хувь хүн/байгууллага), хаяг, KYC/AML."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

from .context import Gen
from .util import (
    AIMAG_CENTERS, BANKS, EDUCATION, EMPLOYERS, FEMALE_NAMES, INDUSTRIES, LAST_NAMES, LEGAL_FORMS, MALE_NAMES,
    OCCUPATIONS, ORG_PREFIX, UB_DISTRICTS, D, add_months, ts, ts_rand,
)

POSITION_DEPT = {"CEO": None, "BRANCH_MANAGER": "LENDING", "CREDIT_OFFICER": "LENDING", "RISK_ANALYST": "RISK",
                 "ACCOUNTANT": "FINANCE", "TELLER": "OPERATIONS", "COLLECTOR": "COLLECTION",
                 "COMPLIANCE_OFF": "COMPLIANCE"}
POSITION_ROLE = {"CEO": "BRANCH_MANAGER", "BRANCH_MANAGER": "BRANCH_MANAGER", "CREDIT_OFFICER": "CREDIT_OFFICER",
                 "RISK_ANALYST": "RISK_ANALYST", "ACCOUNTANT": "ACCOUNTANT", "TELLER": "TELLER",
                 "COLLECTOR": "COLLECTOR", "COMPLIANCE_OFF": "COMPLIANCE"}


def _staffing(code: str, btype: str, weight: float) -> list[tuple[str, int]]:
    if code == "HO":
        return [("CEO", 1), ("RISK_ANALYST", 4), ("COMPLIANCE_OFF", 3), ("ACCOUNTANT", 4), ("COLLECTOR", 4),
                ("CREDIT_OFFICER", 2), ("TELLER", 1)]
    if btype == "DIGITAL":
        return [("BRANCH_MANAGER", 1), ("CREDIT_OFFICER", 4), ("COLLECTOR", 2)]
    if btype == "SUB_BRANCH":
        return [("CREDIT_OFFICER", 2), ("TELLER", 1)]
    officers = max(3, round(weight * 60))
    return [("BRANCH_MANAGER", 1), ("CREDIT_OFFICER", officers), ("TELLER", 2), ("ACCOUNTANT", 1),
            ("COLLECTOR", 1 if weight < 0.08 else 2)]


def _person_name(rng, gender: str | None = None) -> tuple[str, str, str]:
    g = gender or rng.choice(["MALE", "FEMALE"])
    first = rng.choice(MALE_NAMES if g == "MALE" else FEMALE_NAMES)
    return rng.choice(LAST_NAMES), first, g


def build_employees(gen: Gen) -> None:
    rng = gen.rng.child("employees")
    st = gen.store
    emp = st.table("core.employees", [
        "id", "employee_no", "first_name", "last_name", "register_hash", "branch_id", "department_id",
        "position_id", "manager_id", "email", "phone", "hire_date", "termination_date", "employment_status",
        "created_at", "updated_at"])
    asg = st.table("core.employee_assignments", ["id", "employee_id", "branch_id", "department_id", "position_id",
                                                 "valid_from", "valid_to", "change_reason", "created_at"])
    usr = st.table("core.users", ["id", "username", "employee_id", "password_hash", "password_algo",
                                  "password_changed_at", "must_change_password", "mfa_enabled", "email", "status",
                                  "last_login_at", "data_access_level", "created_at", "updated_at"])
    uroles = st.table("core.user_roles", ["user_id", "role_id", "valid_from", "assigned_at"])
    uaccess = st.table("core.user_branch_access", ["user_id", "branch_id", "access_type", "granted_at"])

    counter = 0
    managers: dict[int, int] = {}
    ceo_id = None
    pending = []
    for b in gen.branches:
        for pos, n in _staffing(b.branch_code, b.branch_type, b.weight):
            for _ in range(n):
                counter += 1
                pending.append((b, pos, counter))

    for b, pos, k in pending:
        last, first, gender = _person_name(rng)
        eid = gen.next_id("core.employees")
        hire_lo = max(b.opened_date, date(2015, 3, 1))
        hire_hi = gen.as_of - timedelta(days=60)
        hire = rng.date_between(hire_lo, hire_hi) if pos not in ("CEO", "BRANCH_MANAGER") else \
            rng.date_between(hire_lo, min(hire_hi, add_months(b.opened_date, 6)))
        status, term = "ACTIVE", None
        if pos not in ("CEO", "BRANCH_MANAGER") and rng.chance(0.06):
            term = rng.date_between(hire + timedelta(days=180), gen.as_of - timedelta(days=5)) \
                if hire + timedelta(days=200) < gen.as_of else None
            status = "TERMINATED" if term else "ACTIVE"
        elif rng.chance(0.03):
            status = "ON_LEAVE"
        if b.closed_date and status == "ACTIVE" and pos not in ("CEO",):
            # хаагдсанаас хойш ажилд орсон бол тэр өдрөө (ck_employees_term). RNG ашиглахгүй тул seed 42-ийн
            # үндсэн DB өөрчлөгдөхгүй (тэнд ийм ажилтан байхгүй) — өөр seed-тэй test-suite DB-д л нөлөөлнө.
            term = max(b.closed_date, hire)
            status = "TERMINATED"
        rec = SimpleNamespace(id=eid, no=f"E{k:04d}", first=first, last=last, gender=gender, branch=b,
                              position=pos, hire=hire, termination=term, status=status, manager=None)
        if pos == "CEO":
            ceo_id = eid
        if pos == "BRANCH_MANAGER":
            managers[b.id] = eid
        gen.employees.append(rec)

    for e in gen.employees:
        if e.position == "CEO":
            e.manager = None
        elif e.position == "BRANCH_MANAGER" or e.branch.branch_code == "HO":
            e.manager = ceo_id
        else:
            parent_mgr = managers.get(e.branch.id)
            e.manager = parent_mgr or ceo_id
        dept = POSITION_DEPT[e.position]
        emp.add(id=e.id, employee_no=e.no, first_name=e.first, last_name=e.last,
                register_hash=gen.hmac_like(f"EMP{e.no}"), branch_id=e.branch.id,
                department_id=gen.ref.departments[dept] if dept else None,
                position_id=gen.ref.positions[e.position].id, manager_id=e.manager,
                email=f"{e.no.lower()}@nbfi.mn", phone=f"+976 99{rng.randint(100000, 999999)}",
                hire_date=e.hire, termination_date=e.termination, employment_status=e.status,
                created_at=ts(e.hire), updated_at=ts(e.termination or e.hire))
        asg.add(id=gen.next_id("core.employee_assignments"), employee_id=e.id, branch_id=e.branch.id,
                department_id=gen.ref.departments[dept] if dept else None,
                position_id=gen.ref.positions[e.position].id, valid_from=e.hire, valid_to=e.termination,
                change_reason="Ажилд авсан", created_at=ts(e.hire))
        uid = gen.next_id("core.users")
        gen.users_by_employee[e.id] = uid
        usr.add(id=uid, username=e.no.lower(), employee_id=e.id,
                password_hash="$argon2id$v=19$m=65536,t=3,p=4$" + gen.hmac_like(e.no).hex()[:22] + "$" +
                              gen.hmac_like("pw" + e.no).hex()[:43],
                password_algo="ARGON2ID", password_changed_at=ts(e.hire), must_change_password=False,
                mfa_enabled=e.position in ("CEO", "BRANCH_MANAGER", "ACCOUNTANT"), email=f"{e.no.lower()}@nbfi.mn",
                status="DISABLED" if e.status == "TERMINATED" else "ACTIVE",
                last_login_at=ts_rand(rng, gen.as_of - timedelta(days=rng.randint(0, 20)))
                if e.status == "ACTIVE" else None,
                data_access_level="RESTRICTED" if e.position in ("CEO", "COMPLIANCE_OFF", "RISK_ANALYST")
                else "CONFIDENTIAL",
                created_at=ts(e.hire), updated_at=ts(e.termination or e.hire))
        uroles.add(user_id=uid, role_id=gen.ref.roles[POSITION_ROLE[e.position]], valid_from=e.hire,
                   assigned_at=ts(e.hire))
        if e.branch.branch_code == "HO":
            for b in gen.branches:
                uaccess.add(user_id=uid, branch_id=b.id, access_type="READ", granted_at=ts(e.hire))
        else:
            uaccess.add(user_id=uid, branch_id=e.branch.id, access_type="READ_WRITE", granted_at=ts(e.hire))

    sys_uid = gen.next_id("core.users")
    gen.sys_user_id = sys_uid
    usr.add(id=sys_uid, username="sysadmin", employee_id=None,
            password_hash="$argon2id$v=19$m=65536,t=3,p=4$" + gen.hmac_like("sys").hex()[:22] + "$" +
                          gen.hmac_like("pwsys").hex()[:43],
            password_algo="ARGON2ID", password_changed_at=ts(date(2024, 1, 1)), must_change_password=False,
            mfa_enabled=True, email="admin@nbfi.mn", status="ACTIVE", last_login_at=ts(gen.as_of, 9),
            data_access_level="RESTRICTED", created_at=ts(date(2015, 3, 1)), updated_at=ts(date(2024, 1, 1)))
    uroles.add(user_id=sys_uid, role_id=gen.ref.roles["SYS_ADMIN"], valid_from=date(2015, 3, 1),
               assigned_at=ts(date(2015, 3, 1)))


def officers_at(gen: Gen, branch_id: int, on: date, position: str = "CREDIT_OFFICER") -> list[SimpleNamespace]:
    out = [e for e in gen.employees if e.branch.id == branch_id and e.position == position
           and e.hire <= on and (e.termination is None or e.termination > on)]
    return out


def pick_officer(gen: Gen, rng, branch, on: date, position: str = "CREDIT_OFFICER") -> SimpleNamespace | None:
    cands = officers_at(gen, branch.id, on, position)
    if not cands:
        cands = [e for e in gen.employees if e.position == position and e.hire <= on
                 and (e.termination is None or e.termination > on)]
    return rng.choice(cands) if cands else None


# ---------------------------------------------------------------------------
# Харилцагч
# ---------------------------------------------------------------------------

def _pick_branch(gen: Gen, rng, on: date):
    open_ = [b for b in gen.branches if b.opened_date <= on and (b.closed_date is None or b.closed_date > on)]
    return rng.weighted([(b, b.weight) for b in open_])


def _address_for(rng, branch) -> tuple[str, str]:
    if branch.province == "Улаанбаатар":
        if rng.chance(0.45):
            return "Улаанбаатар", branch.district
        return "Улаанбаатар", rng.choice(UB_DISTRICTS)
    if rng.chance(0.85):
        return branch.province, branch.district
    prov = rng.choice(list(AIMAG_CENTERS))
    return prov, AIMAG_CENTERS[prov]


def _register(rng, birth: date | None) -> str:
    letters = "АБВГДЕЁЖЗИЙКЛМНОӨПРСТУҮФХЦЧШЩЭЮЯ"
    yy = (birth.year % 100) if birth else rng.randint(50, 99)
    mm = (birth.month + (20 if birth and birth.year >= 2000 else 0)) if birth else rng.randint(1, 12)
    dd = birth.day if birth else rng.randint(1, 28)
    return f"{rng.choice(letters)}{rng.choice(letters)}{yy:02d}{mm:02d}{dd:02d}{rng.randint(10, 99)}"


def _segment_for(gen: Gen, income: float, ctype: str) -> str:
    if ctype.startswith("ORG"):
        return "CORPORATE" if ctype == "ORG_CORP" else "SME"
    if income >= 15_000_000:
        return "VIP"
    if income >= 5_000_000:
        return "PREMIUM"
    if income >= 2_000_000:
        return "AFFLUENT"
    return "MASS"


def build_customers(gen: Gen) -> None:
    rng = gen.rng.child("customers")
    st = gen.store
    ref = gen.ref
    n_total = max(60, round(3000 * gen.scale))
    ind = st.table("customer.customer_individuals", [
        "customer_id", "last_name", "first_name", "middle_name", "birth_date", "gender", "nationality_code",
        "marital_status", "dependants_count", "education_level", "occupation", "employer_name", "employment_type",
        "monthly_income", "income_currency", "id_document_type", "id_issued_date", "id_expiry_date",
        "created_at", "updated_at"])
    org = st.table("customer.customer_organizations", [
        "customer_id", "legal_name", "trade_name", "legal_form", "industry_code", "establishment_date",
        "employee_count", "annual_revenue", "revenue_currency", "tax_registration_no", "is_licensed",
        "license_number", "license_expiry_date", "ultimate_owner_name", "website", "created_at", "updated_at"])
    addr = st.table("customer.customer_addresses", [
        "id", "customer_id", "address_type", "country_code", "province", "district", "khoroo", "street",
        "building", "apartment", "postal_code", "geo_lat", "geo_lon", "is_primary", "valid_from", "created_at",
        "updated_at"])
    cont = st.table("customer.customer_contacts", [
        "id", "customer_id", "contact_type", "contact_value", "is_primary", "is_verified", "verified_at",
        "opt_in_marketing", "created_at", "updated_at"])
    empl = st.table("customer.customer_employments", [
        "id", "customer_id", "employer_name", "position", "income_type", "monthly_income", "currency_code",
        "is_verified", "verification_source", "start_date", "is_primary", "created_at", "updated_at"])
    bank = st.table("customer.customer_bank_accounts", [
        "id", "customer_id", "bank_code", "bank_name", "account_number", "account_name", "currency_code",
        "is_primary", "is_verified", "created_at", "updated_at"])
    seghist = st.table("customer.customer_segment_history", ["id", "customer_id", "old_segment_id",
                                                             "new_segment_id", "change_reason", "valid_from",
                                                             "changed_at", "changed_by"])

    used_org_names: set[str] = set()
    used_regs: set[str] = set()
    for i in range(1, n_total + 1):
        is_org = rng.chance(0.15)
        # Харилцагч болсон огноо: 35% нь 2024-өөс өмнө
        if rng.chance(0.35):
            onboard = rng.date_between(date(2016, 1, 1), gen.start - timedelta(days=1))
        else:
            span = (gen.as_of - gen.start).days
            onboard = gen.start + timedelta(days=int(span * (rng.random() ** 0.8)))
        branch = _pick_branch(gen, rng, onboard)
        if branch is None or branch.opened_date > onboard:
            onboard = max(onboard, branch.opened_date)
        cid = gen.uuid()
        if is_org:
            ctype = rng.weighted({"ORG_SME": 0.8, "ORG_CORP": 0.12, "ORG_NGO": 0.08})
        else:
            ctype = rng.weighted({"IND_SALARY": 0.7, "IND_BUSINESS": 0.2, "IND_PENSION": 0.1})
        status = rng.weighted({"ACTIVE": 0.88, "INACTIVE": 0.05, "PROSPECT": 0.02, "SUSPENDED": 0.01,
                               "BLACKLIST": 0.01, "CLOSED": 0.03})
        if onboard > gen.as_of - timedelta(days=20) and status in ("CLOSED", "INACTIVE"):
            status = "ACTIVE"
        risk = rng.weighted({"LOW": 0.70, "MEDIUM": 0.22, "HIGH": 0.07, "CRITICAL": 0.01})
        is_pep = (not is_org) and rng.chance(0.01)
        if is_pep and risk == "LOW":
            risk = "HIGH"
        kyc = rng.weighted({"VERIFIED": 0.90, "IN_PROGRESS": 0.04, "EXPIRED": 0.04, "REJECTED": 0.01,
                            "NOT_STARTED": 0.01})
        if status == "PROSPECT":
            kyc = "NOT_STARTED" if rng.chance(0.5) else "IN_PROGRESS"
        aml = "BLOCKED" if status == "BLACKLIST" else rng.weighted(
            {"CLEAR": 0.965, "NOT_SCREENED": 0.01, "MATCH_PENDING": 0.025})
        rm = pick_officer(gen, rng, branch, onboard)
        rm_id = gen.maybe_null(rm.id if rm else None, 0.08)

        if is_org:
            industry = rng.weighted({k: v[2] for k, v in INDUSTRIES.items()})
            form = "LLC" if ctype != "ORG_NGO" else "NGO"
            if ctype == "ORG_CORP":
                form = rng.weighted({"JSC": 0.4, "LLC": 0.6})
            elif ctype == "ORG_SME" and rng.chance(0.1):
                form = rng.weighted({"PARTNERSHIP": 0.5, "COOPERATIVE": 0.5})
            for _ in range(50):
                name = f"{rng.choice(ORG_PREFIX)} {rng.choice(INDUSTRIES[industry][1])} {LEGAL_FORMS[form][0]}"
                if name not in used_org_names:
                    break
                name = f"{rng.choice(ORG_PREFIX)} {rng.choice(ORG_PREFIX)} {rng.choice(INDUSTRIES[industry][1])} " \
                       f"{LEGAL_FORMS[form][0]}"
                if name not in used_org_names:
                    break
            used_org_names.add(name)
            display = name
            employees = max(1, int(rng.lognormal(18 if ctype != "ORG_CORP" else 420, 0.8)))
            revenue = gen.money_round(rng.lognormal(900_000_000 if ctype != "ORG_CORP" else 25_000_000_000, 0.9),
                                      1_000_000)
            income = revenue / 12
            reg = f"{rng.randint(2000000, 9999999)}"
            masked = "**" + reg[-4:]
            birth = None
        else:
            last, first, gender = _person_name(rng)
            display = f"{last} {first}"
            if ctype == "IND_PENSION":
                age = rng.randint(55, 76)
            else:
                age = int(min(70, max(19, rng.gauss(37, 10))))
            birth = gen.as_of - timedelta(days=age * 365 + rng.randint(0, 364))
            if birth > onboard - timedelta(days=18 * 366):
                birth = onboard - timedelta(days=18 * 366 + rng.randint(0, 2000))
            etype = {"IND_SALARY": "EMPLOYED", "IND_PENSION": "RETIRED"}.get(
                ctype, rng.weighted({"BUSINESS_OWNER": 0.6, "SELF_EMPLOYED": 0.4}))
            median = {"EMPLOYED": 1_900_000, "RETIRED": 750_000, "BUSINESS_OWNER": 3_400_000,
                      "SELF_EMPLOYED": 2_100_000}[etype]
            income = gen.money_round(rng.lognormal(median, 0.55) * (rng.uniform(4, 10) if rng.chance(0.02) else 1),
                                     10_000)
            for _ in range(20):
                reg = _register(rng, birth)
                if reg not in used_regs:
                    break
            masked = reg[:2] + "**" + reg[-4:]
        used_regs.add(reg)
        segment = _segment_for(gen, income, ctype)
        seg_id = gen.maybe_null(ref.segments[segment].id)
        verified_at = ts_rand(rng, onboard + timedelta(days=rng.randint(0, 3))) \
            if kyc in ("VERIFIED", "EXPIRED") else None
        expires_at = None
        if verified_at:
            expires_at = verified_at + timedelta(days=365 * rng.randint(2, 5))
            if kyc == "EXPIRED":
                expires_at = ts(gen.as_of - timedelta(days=rng.randint(5, 300)))
                verified_at = min(verified_at, expires_at - timedelta(days=400))
        closed_at = close_reason = None
        if status == "CLOSED":
            closed_at = ts_rand(rng, rng.date_between(onboard + timedelta(days=30), gen.as_of))
            close_reason = rng.choice(["Харилцагчийн хүсэлтээр", "Гадаадад шилжсэн", "Бүх үйлчилгээ хаагдсан",
                                       "Нас барсан"])
        country = "MN"
        resident = True
        if not is_org and rng.chance(0.015):
            country = rng.choice(["CN", "RU", "KR"])
            resident = rng.chance(0.4)
        onboard_ts = ts_rand(rng, onboard)
        row = dict(id=cid, party_type="ORGANIZATION" if is_org else "INDIVIDUAL",
                   customer_type_id=ref.customer_types[ctype].id, segment_id=seg_id, branch_id=branch.id,
                   relationship_manager_id=rm_id, display_name=display, registration_no_enc=None,
                   registration_no_hash=gen.hmac_like(reg), registration_no_masked=masked, country_code=country,
                   is_resident=resident, kyc_status=kyc,
                   kyc_verified_at=verified_at if kyc in ("VERIFIED", "EXPIRED") else None,
                   kyc_expires_at=expires_at, aml_status=aml, risk_level=risk, is_pep=is_pep,
                   is_sanctioned=False, onboarded_at=onboard_ts, notes=None,
                   extra_attributes={"acquisition_channel": rng.weighted({"BRANCH": 0.55, "MOBILE": 0.25,
                                                                           "REFERRAL": 0.12, "ONLINE": 0.08}),
                                     "marketing_consent": rng.chance(0.6)},
                   created_at=onboard_ts)
        rec = SimpleNamespace(id=cid, n=i, is_org=is_org, ctype=ctype, status=status, segment=segment,
                              branch=branch, onboard=onboard, closed=closed_at.date() if closed_at else None,
                              income=income, risk=risk, kyc=kyc, aml=aml, is_pep=is_pep, display=display,
                              rm=rm, birth=birth, loans=[], accounts=[], current_account=None, row=row,
                              close_reason=close_reason, onboard_ts=onboard_ts)
        if is_org:
            rec.industry = industry
            rec.employees = employees
            rec.revenue = revenue
            org.add(customer_id=cid, legal_name=display,
                    trade_name=display.split(" ")[0] if rng.chance(0.3) else None, legal_form=form,
                    industry_code=industry,
                    establishment_date=rng.date_between(date(1995, 1, 1), min(onboard, date(2023, 12, 31))),
                    employee_count=employees, annual_revenue=revenue, revenue_currency="MNT",
                    tax_registration_no=reg, is_licensed=industry in ("B05", "B07", "Q86", "P85"),
                    license_number=f"ЛИЦ-{rng.randint(10000, 99999)}" if industry in ("B05", "B07", "Q86", "P85")
                    else None,
                    license_expiry_date=add_months(onboard, 60) if industry in ("B05", "B07", "Q86", "P85") else None,
                    ultimate_owner_name=" ".join(_person_name(rng)[:2]),
                    website=None, created_at=onboard_ts, updated_at=onboard_ts)
        else:
            rec.gender = gender
            rec.etype = etype
            occ = rng.choice(OCCUPATIONS[etype])
            employer = rng.choice(EMPLOYERS) if etype == "EMPLOYED" else None
            issued = rng.date_between(date(2016, 1, 1), min(onboard, gen.as_of))
            ind.add(customer_id=cid, last_name=last, first_name=first, middle_name=None, birth_date=birth,
                    gender=gen.maybe_null(gender, 0.0), nationality_code=country,
                    marital_status=gen.maybe_null(rng.weighted({"MARRIED": 0.55, "SINGLE": 0.33, "DIVORCED": 0.08,
                                                                "WIDOWED": 0.04}), 0.02),
                    dependants_count=gen.maybe_null(rng.weighted({0: 0.25, 1: 0.25, 2: 0.25, 3: 0.17, 4: 0.08}), 0.03),
                    education_level=gen.maybe_null(rng.weighted({e: w for e, w in zip(EDUCATION,
                                                                                       [0.25, 0.15, 0.45, 0.13, 0.02])}),
                                                   0.05),
                    occupation=occ, employer_name=employer, employment_type=etype, monthly_income=income,
                    income_currency="MNT", id_document_type="NATIONAL_ID" if country == "MN" else "PASSPORT",
                    id_issued_date=issued, id_expiry_date=add_months(issued, 120), created_at=onboard_ts,
                    updated_at=onboard_ts)
            if etype in ("EMPLOYED", "RETIRED", "BUSINESS_OWNER", "SELF_EMPLOYED"):
                empl.add(id=gen.next_id("customer.customer_employments"), customer_id=cid,
                         employer_name=employer or ("Нийгмийн даатгалын сан" if etype == "RETIRED" else display),
                         position=occ, income_type={"EMPLOYED": "SALARY", "RETIRED": "PENSION"}.get(etype, "BUSINESS"),
                         monthly_income=income, currency_code="MNT", is_verified=rng.chance(0.7),
                         verification_source=rng.choice(["НДШ", "Банкны хуулга", "Цалингийн тодорхойлолт"]),
                         start_date=rng.date_between(date(2012, 1, 1), onboard), is_primary=True,
                         created_at=onboard_ts, updated_at=onboard_ts)
        prov, dist = _address_for(rng, branch)
        addr.add(id=gen.next_id("customer.customer_addresses"), customer_id=cid,
                 address_type="LEGAL" if is_org else "RESIDENTIAL", country_code="MN", province=prov, district=dist,
                 khoroo=f"{rng.randint(1, 32)}-р хороо" if prov == "Улаанбаатар" else f"{rng.randint(1, 12)}-р баг",
                 street=f"{rng.randint(1, 40)}-р гудамж" if rng.chance(0.6) else None,
                 building=f"{rng.randint(1, 120)}-р байр", apartment=f"{rng.randint(1, 120)} тоот" if not is_org else None,
                 postal_code=None, geo_lat=None, geo_lon=None, is_primary=True, valid_from=onboard,
                 created_at=onboard_ts, updated_at=onboard_ts)
        rec.province, rec.district = prov, dist
        if rng.chance(0.15):
            prov2, dist2 = _address_for(rng, branch)
            addr.add(id=gen.next_id("customer.customer_addresses"), customer_id=cid,
                     address_type="OPERATIONAL" if is_org else "WORK", country_code="MN", province=prov2,
                     district=dist2, khoroo=f"{rng.randint(1, 30)}-р хороо", street=None,
                     building=f"{rng.randint(1, 80)}-р байр", apartment=None, postal_code=None, geo_lat=None,
                     geo_lon=None, is_primary=False, valid_from=onboard, created_at=onboard_ts, updated_at=onboard_ts)
        cont.add(id=gen.next_id("customer.customer_contacts"), customer_id=cid, contact_type="MOBILE",
                 contact_value=f"+976 {rng.choice(['88', '89', '99', '95', '94', '80', '86'])}{rng.randint(100000, 999999)}",
                 is_primary=True, is_verified=True, verified_at=onboard_ts, opt_in_marketing=rng.chance(0.5),
                 created_at=onboard_ts, updated_at=onboard_ts)
        if rng.chance(0.6):
            cont.add(id=gen.next_id("customer.customer_contacts"), customer_id=cid, contact_type="EMAIL",
                     contact_value=f"client{i:05d}@example.mn", is_primary=False, is_verified=False, verified_at=None,
                     opt_in_marketing=rng.chance(0.4), created_at=onboard_ts, updated_at=onboard_ts)
        rec.bank_account_id = None
        if rng.chance(0.4):
            bcode, bname = rng.choice(BANKS)
            rec.bank_account_id = gen.next_id("customer.customer_bank_accounts")
            bank.add(id=rec.bank_account_id, customer_id=cid, bank_code=bcode,
                     bank_name=bname, account_number=f"{rng.randint(10**9, 10**10 - 1)}", account_name=display,
                     currency_code="MNT", is_primary=True, is_verified=rng.chance(0.8), created_at=onboard_ts,
                     updated_at=onboard_ts)
        if not is_org and segment in ("AFFLUENT", "PREMIUM", "VIP") and onboard < gen.as_of - timedelta(days=200) \
                and rng.chance(0.3):
            lower = {"AFFLUENT": "MASS", "PREMIUM": "AFFLUENT", "VIP": "PREMIUM"}[segment]
            when = rng.date_between(onboard + timedelta(days=90), gen.as_of)
            seghist.add(id=gen.next_id("customer.customer_segment_history"), customer_id=cid,
                        old_segment_id=ref.segments[lower].id, new_segment_id=ref.segments[segment].id,
                        change_reason="Орлого өссөн", valid_from=when, changed_at=ts(when), changed_by=gen.sys_user_id)
        gen.customers.append(rec)
    _build_kyc(gen)


def finalize_customer_status(gen: Gen) -> None:
    """Зээл үүссэний дараа: хаагдсан/идэвхгүй харилцагчийн төлөвийг идэвхтэй зээлтэй уялдуулна."""
    rng = gen.rng.child("custfinal")
    for c in gen.customers:
        open_loans = [ln for ln in c.loans if getattr(ln, "status", "ACTIVE") not in ("CLOSED", "WRITTEN_OFF")]
        if c.status in ("CLOSED", "INACTIVE") and open_loans:
            c.status, c.closed, c.close_reason = "ACTIVE", None, None
        elif c.status == "CLOSED":
            last = max([getattr(ln, "closed_on", None) or ln.contract for ln in c.loans] + [c.closed or c.onboard])
            closed = last + timedelta(days=rng.randint(10, 60))
            if closed > gen.as_of:
                c.status, c.closed, c.close_reason = "INACTIVE", None, None
            else:
                c.closed = closed


def write_customer_rows(gen: Gen) -> None:
    """customer.customers + төлөвийн түүх. Дугаарыг (C00000123) нэвтэрсэн огноогоор дараалуулж олгоно."""
    rng = gen.rng.child("custrows")
    ref = gen.ref
    st = gen.store
    cust = st.table("customer.customers", [
        "id", "customer_number", "party_type", "customer_type_id", "status_id", "segment_id", "branch_id",
        "relationship_manager_id", "display_name", "registration_no_enc", "registration_no_hash",
        "registration_no_masked", "country_code", "is_resident", "kyc_status", "kyc_verified_at",
        "kyc_expires_at", "aml_status", "risk_level", "is_pep", "is_sanctioned", "is_blacklisted",
        "onboarded_at", "closed_at", "close_reason", "notes", "extra_attributes", "created_at", "updated_at"])
    shist = st.table("customer.customer_status_history", ["id", "customer_id", "old_status_id", "new_status_id",
                                                          "change_reason", "changed_at", "changed_by"])
    for c in sorted(gen.customers, key=lambda x: (x.onboard_ts, x.n)):
        closed_at = ts(c.closed, 16) if c.status == "CLOSED" and c.closed else None
        number = gen.number("CUSTOMER", c.onboard)
        c.number = number
        cust.add(**c.row, customer_number=number, status_id=ref.customer_statuses[c.status],
                 is_blacklisted=c.status == "BLACKLIST", closed_at=closed_at,
                 close_reason=c.close_reason if c.status == "CLOSED" else None,
                 updated_at=closed_at or c.onboard_ts)
        if c.status not in ("ACTIVE", "PROSPECT"):
            changed = closed_at or ts_rand(rng, rng.date_between(c.onboard, gen.as_of))
            shist.add(id=gen.next_id("customer.customer_status_history"), customer_id=c.id,
                      old_status_id=ref.customer_statuses["ACTIVE"], new_status_id=ref.customer_statuses[c.status],
                      change_reason={"INACTIVE": "12 сараас дээш гүйлгээгүй", "SUSPENDED": "Нийцлийн шалгалт",
                                     "BLACKLIST": "Залилангийн хэрэг", "CLOSED": c.close_reason}.get(c.status),
                      changed_at=changed, changed_by=gen.sys_user_id)


def _build_kyc(gen: Gen) -> None:
    rng = gen.rng.child("kyc")
    st = gen.store
    ref = gen.ref
    prof = st.table("kyc.kyc_profiles", [
        "id", "customer_id", "kyc_level", "status", "identity_verified", "address_verified", "income_verified",
        "source_of_funds", "source_of_wealth", "purpose_of_relation", "expected_monthly_turnover", "verified_at",
        "verified_by", "expires_at", "next_review_date", "rejection_reason", "created_at", "updated_at"])
    docs = st.table("kyc.kyc_documents", [
        "id", "kyc_profile_id", "customer_id", "doc_type", "doc_number_masked", "issued_by", "issued_date",
        "expiry_date", "is_verified", "verified_at", "verified_by", "created_at", "updated_at"])
    ver = st.table("kyc.kyc_verifications", [
        "id", "kyc_profile_id", "verification_type", "provider", "request_ref", "result", "confidence_score",
        "performed_at", "performed_by", "created_at"])
    crp = st.table("kyc.customer_risk_profiles", [
        "id", "customer_id", "risk_level", "risk_score", "factor_scores", "assessment_type", "assessed_at",
        "assessed_by", "valid_from", "valid_to", "next_review_date", "is_current", "created_at"])
    scr = st.table("kyc.aml_screenings", [
        "id", "customer_id", "screening_type", "screened_name", "match_count", "result", "screened_at",
        "screened_by", "reviewed_at", "reviewed_by", "review_notes", "created_at"])
    sent = st.table("kyc.sanction_entries", ["id", "list_id", "external_ref", "entity_name", "aliases", "party_type",
                                             "country_code", "program", "listed_date", "created_at", "updated_at"])
    match = st.table("kyc.aml_screening_matches", [
        "id", "screening_id", "sanction_entry_id", "pep_record_id", "matched_name", "match_score", "decision",
        "decided_at", "decided_by", "decision_reason", "created_at"])
    pep = st.table("kyc.pep_records", ["id", "customer_id", "person_name", "pep_category", "position_held",
                                       "organization", "country_code", "start_date", "source", "is_active",
                                       "created_at", "updated_at"])
    alerts = st.table("kyc.aml_alerts", [
        "id", "alert_no", "rule_id", "customer_id", "entity_type", "alert_amount", "currency_code", "severity",
        "status", "detected_at", "assigned_to", "closed_at", "closed_by", "resolution_notes", "str_reported",
        "str_reference", "details", "created_at", "updated_at"])

    compliance = [e for e in gen.employees if e.position == "COMPLIANCE_OFF"] or gen.employees[:1]
    entries = []
    for k in range(12):
        eid = gen.next_id("kyc.sanction_entries")
        lname, fname, _ = _person_name(rng)
        listed = rng.date_between(date(2015, 1, 1), date(2025, 12, 31))
        sent.add(id=eid, list_id=ref.sanction_lists[rng.choice(["UN", "OFAC", "EU", "MN_FIU"])],
                 external_ref=f"SL-{k + 1:05d}", entity_name=f"{lname} {fname}", aliases=[f"{fname} {lname}"],
                 party_type="INDIVIDUAL", country_code=rng.choice(["KP", "IR", "MN", "RU"]),
                 program=rng.choice(["Терроризм", "Цөмийн зэвсэг", "Мөнгө угаах"]), listed_date=listed,
                 created_at=ts(listed), updated_at=ts(listed))
        entries.append((eid, f"{lname} {fname}"))

    for c in gen.customers:
        officer = c.rm or (gen.employees[0])
        level = "ENHANCED" if c.risk in ("HIGH", "CRITICAL") or c.is_pep else \
            ("SIMPLIFIED" if rng.chance(0.1) and c.risk == "LOW" else "STANDARD")
        status = {"VERIFIED": "VERIFIED", "IN_PROGRESS": "IN_PROGRESS", "EXPIRED": "EXPIRED",
                  "REJECTED": "REJECTED", "NOT_STARTED": "IN_PROGRESS"}[c.kyc]
        verified_at = ts_rand(rng, c.onboard + timedelta(days=rng.randint(0, 3))) \
            if status in ("VERIFIED", "EXPIRED") else None
        pid = gen.next_id("kyc.kyc_profiles")
        review = c.onboard + timedelta(days=365 if level == "ENHANCED" else 3 * 365)
        prof.add(id=pid, customer_id=c.id, kyc_level=level, status=status,
                 identity_verified=status in ("VERIFIED", "EXPIRED"),
                 address_verified=status in ("VERIFIED", "EXPIRED") and rng.chance(0.9),
                 income_verified=status in ("VERIFIED", "EXPIRED") and rng.chance(0.75),
                 source_of_funds="Бизнесийн орлого" if c.is_org else rng.choice(["Цалин", "Бизнесийн орлого",
                                                                                  "Тэтгэвэр", "Түрээсийн орлого"]),
                 source_of_wealth=("Хуримтлал, үл хөдлөх хөрөнгө" if level == "ENHANCED" else None),
                 purpose_of_relation=rng.choice(["Зээл", "Хадгаламж", "Төлбөр тооцоо"]),
                 expected_monthly_turnover=D(c.income * rng.uniform(0.5, 2.0)),
                 verified_at=verified_at if status in ("VERIFIED", "EXPIRED") else None,
                 verified_by=officer.id if status in ("VERIFIED", "EXPIRED") else None,
                 expires_at=verified_at + timedelta(days=730) if verified_at else None,
                 next_review_date=review, rejection_reason="Баримт бичиг хуурамч байж болзошгүй"
                 if status == "REJECTED" else None,
                 created_at=ts(c.onboard), updated_at=verified_at or ts(c.onboard))
        for dt in (["COMPANY_CERTIFICATE", "TAX_CERTIFICATE", "UBO_DECLARATION"] if c.is_org
                   else ["NATIONAL_ID", "ADDRESS_PROOF"] + (["INCOME_PROOF"] if rng.chance(0.6) else [])):
            ok = status in ("VERIFIED", "EXPIRED")
            docs.add(id=gen.next_id("kyc.kyc_documents"), kyc_profile_id=pid, customer_id=c.id, doc_type=dt,
                     doc_number_masked=f"***{rng.randint(1000, 9999)}", issued_by="ХУР" if dt == "NATIONAL_ID" else None,
                     issued_date=c.onboard - timedelta(days=rng.randint(10, 900)), expiry_date=None, is_verified=ok,
                     verified_at=verified_at if ok else None, verified_by=officer.id if ok else None,
                     created_at=ts(c.onboard), updated_at=ts(c.onboard))
        ver.add(id=gen.next_id("kyc.kyc_verifications"), kyc_profile_id=pid,
                verification_type="DATABASE_LOOKUP", provider="ХУР", request_ref=f"KHUR-{rng.randint(10**6, 10**7)}",
                result="PASSED" if status in ("VERIFIED", "EXPIRED") else ("FAILED" if status == "REJECTED"
                                                                          else "MANUAL_REVIEW"),
                confidence_score=round(rng.uniform(80, 100), 2), performed_at=ts_rand(rng, c.onboard),
                performed_by=officer.id, created_at=ts(c.onboard))
        score = {"LOW": rng.uniform(5, 30), "MEDIUM": rng.uniform(31, 60), "HIGH": rng.uniform(61, 85),
                 "CRITICAL": rng.uniform(86, 99)}[c.risk]
        analyst = rng.choice([e for e in gen.employees if e.position in ("RISK_ANALYST", "COMPLIANCE_OFF")]
                             or gen.employees)
        if rng.chance(0.2) and c.onboard < gen.as_of - timedelta(days=400):
            old_level = rng.choice(["LOW", "MEDIUM"])
            valid_to = rng.date_between(c.onboard + timedelta(days=180), gen.as_of - timedelta(days=30))
            crp.add(id=gen.next_id("kyc.customer_risk_profiles"), customer_id=c.id, risk_level=old_level,
                    risk_score=round(rng.uniform(5, 50), 2), factor_scores={"CUSTOMER": round(rng.uniform(0, 30), 1)},
                    assessment_type="ONBOARDING", assessed_at=ts(c.onboard), assessed_by=analyst.id,
                    valid_from=c.onboard, valid_to=valid_to, next_review_date=valid_to, is_current=False,
                    created_at=ts(c.onboard))
            cur_from = valid_to + timedelta(days=1)
            atype = "PERIODIC"
        else:
            cur_from = c.onboard
            atype = "ONBOARDING"
        crp.add(id=gen.next_id("kyc.customer_risk_profiles"), customer_id=c.id, risk_level=c.risk,
                risk_score=round(score, 2), factor_scores={"GEOGRAPHY": round(rng.uniform(0, 20), 1),
                                                           "CUSTOMER": round(score * 0.6, 1)},
                assessment_type=atype, assessed_at=ts(cur_from), assessed_by=analyst.id, valid_from=cur_from,
                valid_to=None, next_review_date=cur_from + timedelta(days=365 if c.risk in ("HIGH", "CRITICAL")
                                                                     else 3 * 365),
                is_current=True, created_at=ts(cur_from))
        # AML шалгалт
        sid = gen.next_id("kyc.aml_screenings")
        pending = c.aml == "MATCH_PENDING"
        fp = (not pending) and rng.chance(0.01)
        result = "POTENTIAL_MATCH" if pending else ("FALSE_POSITIVE" if fp else "CLEAR")
        scr.add(id=sid, customer_id=c.id, screening_type="ONBOARDING", screened_name=c.display,
                match_count=1 if result != "CLEAR" else 0, result=result, screened_at=ts_rand(rng, c.onboard),
                screened_by=rng.choice(compliance).id, reviewed_at=ts(c.onboard + timedelta(days=2)) if fp else None,
                reviewed_by=rng.choice(compliance).id if fp else None,
                review_notes="Нэр давхцсан, өөр хүн" if fp else None, created_at=ts(c.onboard))
        if result != "CLEAR":
            eid, ename = rng.choice(entries)
            match.add(id=gen.next_id("kyc.aml_screening_matches"), screening_id=sid, sanction_entry_id=eid,
                      pep_record_id=None, matched_name=ename, match_score=round(rng.uniform(70, 92), 2),
                      decision="FALSE_POSITIVE" if fp else "PENDING",
                      decided_at=ts(c.onboard + timedelta(days=2)) if fp else None,
                      decided_by=rng.choice(compliance).id if fp else None,
                      decision_reason="Төрсөн огноо зөрүүтэй" if fp else None, created_at=ts(c.onboard))
        if c.is_pep:
            pep.add(id=gen.next_id("kyc.pep_records"), customer_id=c.id, person_name=c.display,
                    pep_category=rng.choice(["DOMESTIC", "FAMILY_MEMBER", "CLOSE_ASSOCIATE"]),
                    position_held=rng.choice(["Улсын Их Хурлын гишүүн", "Яамны газрын дарга", "Аймгийн Засаг дарга",
                                              "Төрийн өмчит компанийн гүйцэтгэх захирал"]),
                    organization="Төрийн байгууллага", country_code="MN",
                    start_date=rng.date_between(date(2012, 1, 1), date(2024, 1, 1)), source="Нийтийн мэдээлэл",
                    is_active=True, created_at=ts(c.onboard), updated_at=ts(c.onboard))
        # Сэжигтэй гүйлгээний сэрэмжлүүлэг
        if rng.chance(0.02 if not c.is_org else 0.05) and c.status != "PROSPECT":
            rule = rng.choice(list(ref.aml_rules.values()))
            detected = rng.date_between(max(c.onboard, gen.start), gen.as_of)
            status_a = rng.weighted({"OPEN": 0.2, "UNDER_REVIEW": 0.15, "ESCALATED": 0.05,
                                     "CLOSED_FALSE_POSITIVE": 0.45, "CLOSED_REPORTED": 0.15})
            closed = status_a.startswith("CLOSED")
            closed_at = ts(min(gen.as_of, detected + timedelta(days=rng.randint(1, 25)))) if closed else None
            reported = status_a == "CLOSED_REPORTED"
            alerts.add(id=gen.next_id("kyc.aml_alerts"), alert_no=gen.number("AML_ALERT", detected),
                       rule_id=rule.id, customer_id=c.id, entity_type="CUSTOMER",
                       alert_amount=D(rng.choice([21_000_000, 25_500_000, 18_900_000, 47_000_000,
                                                  rng.uniform(5_000_000, 90_000_000)])),
                       currency_code="MNT", severity=rule.severity, status=status_a, detected_at=ts_rand(rng, detected),
                       assigned_to=rng.choice(compliance).id, closed_at=closed_at,
                       closed_by=rng.choice(compliance).id if closed else None,
                       resolution_notes="Хэвийн бизнесийн гүйлгээ" if status_a == "CLOSED_FALSE_POSITIVE" else
                       ("СЭГТ-д мэдээлсэн" if reported else None),
                       str_reported=reported, str_reference=f"STR-{detected.year}-{rng.randint(100, 999)}"
                       if reported else None,
                       details={"rule": rule.rule_code}, created_at=ts(detected), updated_at=closed_at or ts(detected))
