"""Данс, гүйлгээ, сарын эцсийн үлдэгдэл, блоклолт, хадгаламж."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from types import SimpleNamespace

from .context import Gen
from .refdata import fx_rate
from .util import ZERO, D, add_months, month_end, month_ends_between, month_start, ts

TXN_DESC = {
    "SALARY": "Цалин", "PENSION": "Тэтгэвэр", "BUSINESS_IN": "Борлуулалтын орлого", "CASH_DEPOSIT": "Бэлэн мөнгө тушаав",
    "ATM": "АТМ-аас бэлэн мөнгө авав", "BRANCH_WD": "Бэлэн мөнгө авав", "TRANSFER_OUT": "Шилжүүлэг",
    "TRANSFER_IN": "Шилжүүлэг орлого", "LOAN_DISB": "Зээл олголт", "LOAN_REPAY": "Зээлийн эргэн төлөлт",
    "FEE": "Шилжүүлгийн шимтгэл", "INTEREST": "Хадгаламжийн хүү", "DEP_IN": "Хадгаламж нээв",
    "DEP_OUT": "Хадгаламжаас зарлага",
}


class Accounts:
    def __init__(self, gen: Gen):
        self.gen = gen
        st = gen.store
        self.acc = st.table("account.accounts", [
            "id", "account_number", "account_type_id", "customer_id", "branch_id", "product_id", "currency_code",
            "account_name", "current_balance", "available_balance", "hold_amount", "overdraft_limit",
            "last_transaction_at", "status", "is_dormant", "block_reason", "opened_date", "closed_date",
            "dormant_since", "created_at", "updated_at"])
        self.txn = st.table("account.transactions", [
            "id", "transaction_no", "account_id", "currency_code", "transaction_type_id", "channel_id", "status_id",
            "branch_id", "customer_id", "direction", "amount", "fee_amount", "balance_before", "balance_after",
            "base_currency_code", "base_amount", "fx_rate", "transaction_date", "value_date", "posted_at",
            "source_entity_type", "source_entity_id", "counterparty_name", "counterparty_bank", "description",
            "created_at"])
        self.bal = st.table("account.account_balances", [
            "id", "account_id", "balance_date", "opening_balance", "total_debit", "total_credit", "closing_balance",
            "transaction_count", "hold_amount", "currency_code", "base_closing_balance", "fx_rate", "created_at"])
        self.holds = st.table("account.account_holds", [
            "id", "account_id", "currency_code", "amount", "hold_type", "reference_type", "reason", "placed_at",
            "placed_by", "expires_at", "released_at", "status", "created_at"])
        self.ash = st.table("account.account_status_history", [
            "id", "account_id", "old_status", "new_status", "balance_at_change", "change_reason", "changed_at",
            "changed_by"])

    def new_account(self, cust, atype: str, product_code: str | None, currency: str, opened: date,
                    name_suffix: str, branch=None) -> SimpleNamespace:
        gen = self.gen
        a = SimpleNamespace(id=gen.uuid(), number=gen.number("ACCOUNT", opened), cust=cust, atype=atype,
                            product=gen.ref.products[product_code].id if product_code else None, currency=currency,
                            branch=branch or cust.branch, opened=opened, events=[], balance=ZERO, status="ACTIVE",
                            closed=None, name=f"{cust.display} — {name_suffix}", holds=ZERO, last_txn=None,
                            block_reason=None, dormant_since=None)
        gen.accounts.append(a)
        cust.accounts.append(a)
        return a


def _event(acc, day: date, kind: str, amount: Decimal, direction: str, ttype: str, channel: str, **extra) -> None:
    acc.events.append(SimpleNamespace(day=day, kind=kind, amount=amount, direction=direction, ttype=ttype,
                                      channel=channel, **extra))


def build_banking(gen: Gen) -> None:
    rng = gen.rng.child("banking")
    A = Accounts(gen)
    ref = gen.ref
    # Салбарын дотоод данс
    for b in gen.branches:
        for tcode in ("CASH_VAULT", "SUSPENSE"):
            A.acc.add(id=gen.uuid(), account_number=f"{b.gl_prefix}{tcode}{b.id:04d}",
                      account_type_id=ref.account_types[tcode].id, customer_id=None, branch_id=b.id, product_id=None,
                      currency_code="MNT", account_name=f"{b.branch_name} — {'Касс' if tcode == 'CASH_VAULT' else 'Түдгэлзүүлсэн'}",
                      current_balance=D(500_000_000 if tcode == "CASH_VAULT" else 0),
                      available_balance=D(500_000_000 if tcode == "CASH_VAULT" else 0), hold_amount=ZERO,
                      overdraft_limit=ZERO, last_transaction_at=None, status="ACTIVE" if b.closed_date is None else "ACTIVE",
                      is_dormant=False, block_reason=None, opened_date=b.opened_date, closed_date=None,
                      dormant_since=None, created_at=ts(b.opened_date), updated_at=ts(b.opened_date))
    # Харилцах данс
    for c in gen.customers:
        need = bool(c.loans) or rng.chance(0.8 if c.status != "PROSPECT" else 0.2)
        if not need:
            continue
        opened = c.onboard + timedelta(days=rng.randint(0, 5))
        if opened > gen.as_of:
            opened = c.onboard
        acc = A.new_account(c, "CURRENT", "CUR_MNT", "MNT", opened, "харилцах")
        c.current_account = acc
        if c.is_org and rng.chance(0.12):
            A.new_account(c, "CURRENT", "CUR_USD", "USD", opened + timedelta(days=rng.randint(0, 200)), "харилцах USD")
    _loan_events(gen, rng)
    _activity_events(gen, rng)
    _build_deposits(gen, rng, A)
    _materialize(gen, rng, A)


def _loan_events(gen: Gen, rng) -> None:
    for ln in gen.loans:
        acc = ln.cust.current_account
        if ln.disb is None or acc is None or ln.currency != "MNT":
            continue
        if getattr(ln, "disb_method", None) == "INTERNAL_ACCOUNT":
            _event(acc, ln.disb, "LOAN_DISB", ln.principal, "C", "LOAN_DISB", "BATCH", loan=ln)
            ln.internal_account = acc
            gen.store["loan.loan_disbursements"].update(ln.disb_row_index, internal_account_id=acc.id)
        for p in ln.ledger.payments:
            if p.method in ("AUTO_DEBIT", "SALARY_DEDUCTION") and p.status == "POSTED":
                _event(acc, p.date, "LOAN_REPAY", p.amount, "D", "LOAN_REPAY", "BATCH", payment=p, loan=ln)


def _activity_events(gen: Gen, rng) -> None:
    for acc in gen.accounts:
        c = acc.cust
        if acc.atype != "CURRENT":
            continue
        start = max(acc.opened, gen.start)
        end = gen.as_of if c.closed is None else c.closed
        dormant = c.status == "INACTIVE"
        if dormant:
            end = min(end, gen.as_of - timedelta(days=380))
        if acc.currency == "USD":
            scale = 1 / 3450
        else:
            scale = 1
        monthly_in = (c.revenue / 12 * rng.uniform(0.05, 0.3)) if c.is_org else c.income * rng.uniform(0.5, 1.0)
        for me in month_ends_between(start, end):
            m0 = max(month_start(me), start)
            if m0 > end:
                break
            if c.is_org:
                for _ in range(rng.randint(1, 4)):
                    d = rng.date_between(m0, min(me, end))
                    amt = D(gen.money_round(monthly_in / 2.5 * rng.uniform(0.5, 1.5), 1000) * scale)
                    if amt > 0:
                        _event(acc, d, "BUSINESS_IN", amt, "C", rng.choice(["TRANSFER_IN", "CASH_DEPOSIT"]),
                               rng.choice(["INTERNET", "BRANCH", "API"]))
            else:
                day = 25 if c.ctype == "IND_SALARY" else (15 if c.ctype == "IND_PENSION" else rng.randint(1, 28))
                d = min(date(me.year, me.month, min(day, me.day)), end)
                if d >= m0:
                    kind = {"IND_SALARY": "SALARY", "IND_PENSION": "PENSION"}.get(c.ctype, "BUSINESS_IN")
                    amt = D(gen.money_round(monthly_in * rng.uniform(0.92, 1.05), 1000))
                    _event(acc, d, kind, amt, "C", "TRANSFER_IN" if kind != "BUSINESS_IN" else "CASH_DEPOSIT",
                           "BATCH" if kind != "BUSINESS_IN" else "BRANCH")
            # Зарлага: тоо, хэмжээг дараа нь үлдэгдлээр хязгаарлана
            for _ in range(rng.randint(1, 4)):
                d = rng.date_between(m0, min(me, end))
                share = rng.uniform(0.1, 0.45)
                kind = rng.weighted({"ATM": 0.35, "TRANSFER_OUT": 0.5, "BRANCH_WD": 0.15})
                ttype = "TRANSFER_OUT" if kind == "TRANSFER_OUT" else "CASH_WITHDRAWAL"
                channel = {"ATM": "ATM", "TRANSFER_OUT": rng.choice(["MOBILE", "INTERNET"]), "BRANCH_WD": "BRANCH"}[kind]
                _event(acc, d, kind, Decimal(str(round(share, 4))), "D", ttype, channel, share=True)


def _build_deposits(gen: Gen, rng, A: Accounts) -> None:
    st = gen.store
    ref = gen.ref
    dep = st.table("deposit.deposit_accounts", [
        "id", "deposit_number", "account_id", "customer_id", "product_id", "branch_id", "currency_code",
        "deposit_type", "principal_amount", "current_balance", "interest_rate", "interest_method",
        "day_count_convention", "capitalization_period", "term_months", "accrued_interest", "paid_interest",
        "withholding_tax_paid", "opened_date", "maturity_date", "closed_date", "auto_renew", "renewal_count",
        "payout_account_id", "is_pledged", "status", "created_at", "updated_at"])
    dcon = st.table("deposit.deposit_contracts", [
        "id", "deposit_account_id", "contract_number", "contract_type", "parent_contract_id", "signed_date",
        "effective_date", "expiry_date", "terms_snapshot", "status", "created_at", "updated_at"])
    dacc = st.table("deposit.deposit_interest_accruals", [
        "id", "deposit_account_id", "accrual_date", "balance_base", "annual_rate", "day_count", "interest_amount",
        "withholding_tax", "is_capitalized", "is_posted", "created_at"])
    dtx = st.table("deposit.deposit_transactions", [
        "id", "deposit_account_id", "currency_code", "transaction_id", "transaction_value_date", "txn_type", "amount",
        "balance_after", "value_date", "description", "created_at"])
    early = st.table("deposit.deposit_early_withdrawals", [
        "id", "deposit_account_id", "request_date", "withdrawal_amount", "original_rate", "penalty_rate",
        "interest_forfeited", "penalty_amount", "net_payout", "reason", "approved_by", "status", "executed_at",
        "created_at"])
    managers = {e.branch.id: e for e in gen.employees if e.position == "BRANCH_MANAGER"}
    ceo = next(e for e in gen.employees if e.position == "CEO")
    tiers = {"DEP_TERM": [(3, 5, 10.8), (6, 11, 12.0), (12, 17, 13.2), (18, 36, 13.8)],
             "DEP_CHILD": [(12, 120, 13.2)], "DEP_TERM_USD": [(6, 11, 4.2), (12, 24, 4.8)], "DEP_DEMAND": [(0, 0, 6.0)]}
    for c in gen.customers:
        if c.status in ("PROSPECT", "BLACKLIST"):
            continue
        n = rng.weighted({0: 0.70, 1: 0.22, 2: 0.08}) if not c.is_org else rng.weighted({0: 0.8, 1: 0.2})
        for _ in range(n):
            lo = max(c.onboard, gen.start)
            hi = (c.closed - timedelta(days=40)) if c.closed else gen.as_of - timedelta(days=3)
            if hi <= lo:
                continue
            opened = rng.date_between(lo, hi)
            code = rng.weighted({"DEP_TERM": 0.55, "DEP_DEMAND": 0.25, "DEP_CHILD": 0.1 if not c.is_org else 0,
                                 "DEP_TERM_USD": 0.1})
            prod = ref.products[code]
            ccy = prod.currency_code
            dtype = {"DEP_TERM": "TERM", "DEP_DEMAND": "DEMAND", "DEP_CHILD": "CHILD", "DEP_TERM_USD": "TERM"}[code]
            if code == "DEP_TERM":
                term = rng.choice([3, 6, 12, 12, 12, 18, 24, 36])
            elif code == "DEP_TERM_USD":
                term = rng.choice([6, 12, 12, 24])
            elif code == "DEP_CHILD":
                term = rng.choice([12, 24, 36, 60])
            else:
                term = None
            rate = next(r for lo_t, hi_t, r in tiers[code] if term is None or lo_t <= term <= hi_t)
            base = (c.revenue / 12 * rng.uniform(0.03, 0.4) if c.is_org else c.income * rng.uniform(0.3, 4.0))
            amt = base if ccy == "MNT" else base * rng.uniform(0.3, 1.5) / 3450
            principal = D(gen.money_round(amt, 100 if ccy == "USD" else 50_000))
            principal = max(principal, D(prod.min_amount or 10000))
            compound = rng.chance(0.4) if dtype != "DEMAND" else True
            acc = A.new_account(c, "SAVINGS", code, ccy, opened, "хадгаламж")
            payout = c.current_account if c.current_account and c.current_account.currency == ccy else None
            d = SimpleNamespace(id=gen.uuid(), acc=acc, cust=c, prod=prod, ccy=ccy, dtype=dtype, term=term, rate=rate,
                                principal=principal, compound=compound, opened=opened, auto_renew=rng.chance(0.45),
                                renewals=0, status="ACTIVE", closed=None, accrued=ZERO, paid_int=ZERO, tax=ZERO,
                                balance=ZERO, payout=payout, maturity=add_months(opened, term) if term else None)
            gen.deposits.append(d)
            dep_no = gen.number("DEPOSIT", opened)
            contract_id = gen.uuid()
            dcon.add(id=contract_id, deposit_account_id=d.id, contract_number=gen.number("DEPOSIT_CONTRACT", opened),
                     contract_type="DEPOSIT", parent_contract_id=None, signed_date=opened, effective_date=opened,
                     expiry_date=d.maturity, terms_snapshot={"rate": rate, "term": term, "principal": str(principal)},
                     status="ACTIVE", created_at=ts(opened), updated_at=ts(opened))
            # --- симуляц: сар бүрийн хүү, хугацаа дуусах, сунгах, хугацаанаас өмнө татах
            bal = principal
            _event(acc, opened, "DEP_IN", principal, "C", rng.choice(["CASH_DEPOSIT", "TRANSFER_IN"]), "BRANCH",
                   dep=d, dtype="DEPOSIT")
            early_at = None
            if dtype in ("TERM", "CHILD") and rng.chance(0.06):
                early_at = rng.date_between(opened + timedelta(days=20), min(gen.as_of, d.maturity - timedelta(days=5)))
            last = opened
            for me in month_ends_between(opened, gen.as_of):
                if d.status != "ACTIVE":
                    break
                if early_at and early_at <= me:
                    # хугацаанаас өмнө татах
                    days = (early_at - last).days
                    pen_rate = round(rate * 0.25, 2)
                    forfeited = D(d.accrued)
                    reduced = D(bal * Decimal(str(pen_rate)) / 100 * Decimal(max(days, 0)) / 365)
                    net = D(bal + reduced)
                    early.add(id=gen.next_id("deposit.deposit_early_withdrawals"), deposit_account_id=d.id,
                              request_date=early_at, withdrawal_amount=bal, original_rate=rate, penalty_rate=pen_rate,
                              interest_forfeited=forfeited, penalty_amount=ZERO, net_payout=net,
                              reason=rng.choice(["Яаралтай хэрэгцээ", "Орон сууц худалдан авах", "Эмчилгээ"]),
                              approved_by=(managers.get(c.branch.id) or ceo).id, status="EXECUTED",
                              executed_at=ts(early_at, 15), created_at=ts(early_at, 10))
                    if reduced > 0:
                        bal += reduced
                        _event(acc, early_at, "INTEREST", reduced, "C", "INTEREST_CREDIT", "BATCH", dep=d,
                               dtype="INTEREST_PAYOUT")
                    _event(acc, early_at, "DEP_OUT", bal, "D", "CASH_WITHDRAWAL", "BRANCH", dep=d, dtype="CLOSURE")
                    d.accrued = ZERO
                    bal = ZERO
                    d.status, d.closed = "EARLY_CLOSED", early_at
                    break
                days = (me - last).days
                if days <= 0:
                    continue
                interest = D(bal * Decimal(str(rate)) / 100 * Decimal(days) / 365)
                tax = D(interest * Decimal("0.10"))
                dacc.add(id=gen.next_id("deposit.deposit_interest_accruals"), deposit_account_id=d.id, accrual_date=me,
                         balance_base=bal, annual_rate=rate, day_count=days, interest_amount=interest, withholding_tax=tax,
                         is_capitalized=compound, is_posted=True, created_at=ts(me, 23))
                if compound and interest - tax > 0:
                    bal += interest - tax
                    d.paid_int += interest
                    d.tax += tax
                    _event(acc, me, "INTEREST", interest - tax, "C", "INTEREST_CREDIT", "BATCH", dep=d,
                           dtype="CAPITALIZATION")
                else:
                    d.accrued += interest - tax
                    d.tax += tax
                if dtype == "DEMAND" and rng.chance(0.25) and bal > principal * Decimal("0.3"):
                    w = D(bal * Decimal(str(rng.uniform(0.05, 0.3))))
                    bal -= w
                    _event(acc, me, "DEP_OUT", w, "D", "TRANSFER_OUT", "MOBILE", dep=d, dtype="WITHDRAWAL")
                elif dtype == "DEMAND" and rng.chance(0.3):
                    topup = D(gen.money_round(float(principal) * rng.uniform(0.05, 0.4), 10_000))
                    bal += topup
                    _event(acc, me, "DEP_IN", topup, "C", "TRANSFER_IN", "MOBILE", dep=d, dtype="DEPOSIT")
                last = me
                if d.maturity and d.maturity <= me:
                    if d.accrued > 0:
                        bal += d.accrued
                        d.paid_int += d.accrued
                        _event(acc, d.maturity, "INTEREST", d.accrued, "C", "INTEREST_CREDIT", "BATCH", dep=d,
                               dtype="INTEREST_PAYOUT")
                        d.accrued = ZERO
                    if d.auto_renew and d.renewals < 3:
                        d.renewals += 1
                        old_mat = d.maturity
                        d.maturity = add_months(old_mat, term)
                        dcon.add(id=gen.uuid(), deposit_account_id=d.id,
                                 contract_number=gen.number("DEPOSIT_CONTRACT", old_mat), contract_type="RENEWAL",
                                 parent_contract_id=contract_id, signed_date=old_mat, effective_date=old_mat,
                                 expiry_date=d.maturity, terms_snapshot={"rate": rate, "term": term},
                                 status="ACTIVE", created_at=ts(old_mat), updated_at=ts(old_mat))
                    elif rng.chance(0.6):
                        _event(acc, d.maturity, "DEP_OUT", bal, "D", "TRANSFER_OUT", "BATCH", dep=d, dtype="CLOSURE")
                        bal = ZERO
                        d.status, d.closed = "CLOSED", d.maturity
                        break
                    else:
                        d.status = "MATURED"
                        break
            d.balance = bal
            if d.status in ("CLOSED", "EARLY_CLOSED"):
                acc.status, acc.closed = "CLOSED", d.closed
            pledged = d.status == "ACTIVE" and rng.chance(0.03)
            dep.add(id=d.id, deposit_number=dep_no, account_id=acc.id, customer_id=c.id, product_id=prod.id,
                    branch_id=acc.branch.id, currency_code=ccy, deposit_type=dtype, principal_amount=principal,
                    current_balance=max(bal, ZERO), interest_rate=rate, interest_method="COMPOUND" if compound else "SIMPLE",
                    day_count_convention="ACT_365", capitalization_period="MONTH" if compound else None, term_months=term,
                    accrued_interest=d.accrued, paid_interest=d.paid_int, withholding_tax_paid=d.tax, opened_date=opened,
                    maturity_date=d.maturity, closed_date=d.closed, auto_renew=d.auto_renew, renewal_count=d.renewals,
                    payout_account_id=payout.id if payout else None, is_pledged=pledged,
                    status=d.status if not (d.status == "ACTIVE" and c.status == "SUSPENDED") else "BLOCKED",
                    created_at=ts(opened), updated_at=ts(d.closed or gen.as_of))
            d.dtx = dtx


def _materialize(gen: Gen, rng, A: Accounts) -> None:
    """Дансны үйл явдлуудыг огноогоор нь гүйцэтгэж, үлдэгдэл/гүйлгээ/сарын агшин үүсгэнэ."""
    ref = gen.ref
    for acc in gen.accounts:
        c = acc.cust
        events = sorted(acc.events, key=lambda e: (e.day, 0 if e.direction == "C" else 1))
        # 2024-өөс өмнө нээгдсэн дансны эхний үлдэгдэл
        bal = ZERO
        if acc.opened < gen.start and acc.atype == "CURRENT":
            bal = D(gen.money_round(float(c.income if not c.is_org else c.revenue / 24) * rng.uniform(0.05, 0.8), 1000))
            if acc.currency == "USD":
                bal = D(float(bal) / 3450)
        opening_bal = bal
        txns_by_month: dict[date, list] = defaultdict(list)
        last_txn = None
        for e in events:
            if e.day > gen.as_of:
                continue
            amount = e.amount
            if getattr(e, "share", False):
                amount = D(gen.money_round(float(bal) * float(e.amount), 1000 if acc.currency == "MNT" else 10))
                if amount <= 0 or amount > bal:
                    continue
            if e.direction == "D" and amount > bal:
                if e.kind == "LOAN_REPAY":
                    gap = amount - bal
                    _write_txn(gen, A, acc, e.day, "TRANSFER_IN", "INTERNET", "C", gap, bal, bal + gap,
                               TXN_DESC["TRANSFER_IN"], txns_by_month)
                    bal += gap
                elif getattr(e, "dep", None) is not None:
                    amount = bal
                else:
                    continue
            if amount <= 0:
                continue
            failed = e.kind in ("TRANSFER_OUT", "ATM") and rng.chance(0.015)
            before = bal
            after = bal if failed else (bal + amount if e.direction == "C" else bal - amount)
            desc = TXN_DESC.get(e.kind, e.kind)
            tid = _write_txn(gen, A, acc, e.day, e.ttype, e.channel, e.direction, amount, before, after, desc,
                             txns_by_month, failed=failed, source=getattr(e, "loan", None))
            if not failed:
                bal = after
                last_txn = e.day
            if e.kind == "LOAN_REPAY" and not failed:
                e.payment.txn_id = tid
                gen.store["loan.loan_payments"].update(e.payment.row_index, transaction_id=tid)
            if getattr(e, "dep", None) is not None and not failed:
                d = e.dep
                d.dtx.add(id=gen.next_id("deposit.deposit_transactions"), deposit_account_id=d.id, currency_code=d.ccy,
                          transaction_id=tid, transaction_value_date=e.day, txn_type=e.dtype, amount=amount,
                          balance_after=max(after, ZERO), value_date=e.day, description=desc, created_at=ts(e.day, 12))
        # Төлөв
        status = acc.status
        closed = acc.closed
        if c.status == "CLOSED" and acc.atype == "CURRENT" and c.closed and c.closed < gen.start:
            bal = ZERO
            status, closed = "CLOSED", c.closed
        elif c.status == "CLOSED" and acc.atype == "CURRENT":
            if bal > 0:
                _write_txn(gen, A, acc, c.closed or gen.as_of, "CASH_WITHDRAWAL", "BRANCH", "D", bal, bal, ZERO,
                           "Данс хаах", txns_by_month)
                bal = ZERO
            status, closed = "CLOSED", c.closed or gen.as_of
        elif status == "CLOSED" and bal != 0:
            _write_txn(gen, A, acc, closed, "TRANSFER_OUT", "BATCH", "D", bal, bal, ZERO, "Данс хаах", txns_by_month)
            bal = ZERO
        block_reason = None
        dormant = False
        dormant_since = None
        if status != "CLOSED":
            if c.status in ("BLACKLIST",):
                status, block_reason = "FROZEN", "Хар жагсаалт — залилангийн хэрэг"
            elif c.status == "SUSPENDED":
                status, block_reason = "BLOCKED", "Нийцлийн шалгалт хийгдэж байна"
            elif c.status == "INACTIVE" and acc.atype == "CURRENT":
                status, dormant = "DORMANT", True
                dormant_since = (last_txn or acc.opened) + timedelta(days=365)
                if dormant_since > gen.as_of:
                    dormant_since = gen.as_of
        hold = ZERO
        if status in ("ACTIVE", "BLOCKED") and acc.atype == "CURRENT" and bal > 10_000:
            bad_loans = [ln for ln in c.loans if getattr(ln, "status", "") in ("OVERDUE", "LEGAL")]
            if bad_loans and rng.chance(0.5):
                hold = D(min(bal, sum((ln.final["overdue_principal"] + ln.final["overdue_interest"]
                                       for ln in bad_loans), ZERO)))
                if hold > 0:
                    htype = "COURT_ORDER" if any(ln.status == "LEGAL" for ln in bad_loans) else "LOAN_REPAYMENT"
                    A.holds.add(id=gen.next_id("account.account_holds"), account_id=acc.id, currency_code=acc.currency,
                                amount=hold, hold_type=htype, reference_type="LOAN", reason="Хугацаа хэтэрсэн зээлийн суутгал"
                                if htype == "LOAN_REPAYMENT" else "Шүүхийн шийдвэр", placed_at=ts(gen.as_of - timedelta(days=rng.randint(1, 60))),
                                placed_by=gen.sys_user_id, expires_at=None, released_at=None, status="ACTIVE",
                                created_at=ts(gen.as_of - timedelta(days=60)))
        if status != acc.status or status in ("CLOSED", "DORMANT", "FROZEN", "BLOCKED"):
            A.ash.add(id=gen.next_id("account.account_status_history"), account_id=acc.id, old_status="ACTIVE",
                      new_status=status, balance_at_change=bal, change_reason=block_reason or
                      ("Данс хаагдсан" if status == "CLOSED" else "Идэвхгүй данс"),
                      changed_at=ts(closed or dormant_since or gen.as_of), changed_by=gen.sys_user_id)
        A.acc.add(id=acc.id, account_number=acc.number, account_type_id=ref.account_types[acc.atype].id,
                  customer_id=c.id, branch_id=acc.branch.id, product_id=acc.product, currency_code=acc.currency,
                  account_name=acc.name, current_balance=bal, available_balance=bal - hold, hold_amount=hold,
                  overdraft_limit=ZERO, last_transaction_at=ts(last_txn, 15) if last_txn else None, status=status,
                  is_dormant=dormant, block_reason=block_reason, opened_date=acc.opened, closed_date=closed,
                  dormant_since=dormant_since, created_at=ts(acc.opened), updated_at=ts(closed or gen.as_of))
        acc.balance = bal
        # Сарын эцсийн үлдэгдэл
        start = max(acc.opened, gen.start)
        running = opening_bal
        for me in month_ends_between(start, closed or gen.as_of):
            rows = txns_by_month.get(me, [])
            debit = sum((r[1] for r in rows if r[0] == "D"), ZERO)
            credit = sum((r[1] for r in rows if r[0] == "C"), ZERO)
            closing = running + credit - debit
            rate = fx_rate(gen, acc.currency, me)
            A.bal.add(id=gen.next_id("account.account_balances"), account_id=acc.id, balance_date=me,
                      opening_balance=running, total_debit=debit, total_credit=credit, closing_balance=closing,
                      transaction_count=len(rows), hold_amount=hold if me == gen.as_of else ZERO,
                      currency_code=acc.currency,
                      base_closing_balance=D(float(closing) * rate) if acc.currency != "MNT" else closing,
                      fx_rate=rate if acc.currency != "MNT" else None, created_at=ts(me, 23))
            running = closing


def _write_txn(gen: Gen, A: Accounts, acc, day: date, ttype: str, channel: str, direction: str, amount: Decimal,
               before: Decimal, after: Decimal, desc: str, by_month, failed: bool = False, source=None) -> int:
    ref = gen.ref
    tid = gen.next_id("account.transactions")
    rate = fx_rate(gen, acc.currency, day)
    base = D(float(amount) * rate) if acc.currency != "MNT" else amount
    A.txn.add(id=tid, transaction_no=gen.number("TRANSACTION", day), account_id=acc.id, currency_code=acc.currency,
              transaction_type_id=ref.txn_types[ttype].id, channel_id=ref.channels[channel],
              status_id=ref.txn_statuses["FAILED" if failed else "COMPLETED"], branch_id=acc.branch.id,
              customer_id=acc.cust.id, direction=direction, amount=amount,
              fee_amount=D(100) if ttype == "TRANSFER_OUT" and channel in ("MOBILE", "INTERNET") and not failed else ZERO,
              balance_before=before, balance_after=after, base_currency_code="MNT", base_amount=base,
              fx_rate=rate if acc.currency != "MNT" else None, transaction_date=day, value_date=day,
              posted_at=None if failed else ts(day, 16), source_entity_type="LOAN" if source is not None else None,
              source_entity_id=source.id if source is not None else None, counterparty_name=None,
              counterparty_bank=None, description=desc, created_at=ts(day, 12))
    if not failed:
        by_month[month_end(day)].append((direction, amount))
    return tid
