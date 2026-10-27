"""Давхар бичилтийн нягтлан: зээл олголт, эргэн төлөлт, хүүгийн хуримтлал, шимтгэл, торгууль, эрсдэлийн сан,
NPL ангилал, хасалт, хадгаламж, харилцах дансны хөдөлгөөн, үйл ажиллагааны зардал; дэвтрийн үлдэгдэл."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from .context import Gen
from .refdata import fx_rate, period_for
from .util import ZERO, D, D4, month_end, month_ends_between, ts


class Journal:
    def __init__(self, gen: Gen):
        self.gen = gen
        st = gen.store
        self.entries = st.table("accounting.journal_entries", [
            "id", "entry_no", "journal_id", "period_id", "branch_id", "entry_date", "value_date", "posting_date",
            "description", "reference", "source_system", "source_entity_type", "source_entity_id", "currency_code",
            "total_debit", "total_credit", "status", "posted_at", "posted_by", "approved_by", "is_reversal",
            "created_at", "updated_at"])
        self.lines = st.table("accounting.journal_lines", [
            "id", "entry_id", "posting_date", "line_no", "gl_account_id", "branch_id", "dr_cr", "amount",
            "currency_code", "fx_rate", "base_amount", "subledger_type", "subledger_id", "customer_id", "product_id",
            "description", "created_at"])
        self.pending: list[dict] = []
        self.turnover: dict[tuple, list] = defaultdict(lambda: [ZERO, ZERO, ZERO, ZERO])  # (acc, period, branch, ccy)

    def add(self, day: date, journal: str, branch_id: int, ccy: str, desc: str, credits: list, debits: list,
            source_system: str = "CORE", entity_type: str | None = None, entity_id=None, customer_id=None,
            product_id=None, ref_ids: list | None = None) -> None:
        """credits/debits: [(gl_code, amount, line_desc)]. Нэг талын нийлбэр нөгөө талын нийлбэртэй тэнцэнэ."""
        credits = [(c, D(a), t) for c, a, t in credits if D(a) > 0]
        debits = [(c, D(a), t) for c, a, t in debits if D(a) > 0]
        if not credits or not debits:
            return
        self.pending.append(dict(day=day, journal=journal, branch_id=branch_id, ccy=ccy, desc=desc, credits=credits,
                                 debits=debits, source_system=source_system, entity_type=entity_type,
                                 entity_id=entity_id, customer_id=customer_id, product_id=product_id,
                                 ref_ids=ref_ids or []))

    def flush(self, accountant_user: int) -> dict:
        gen = self.gen
        ref = gen.ref
        entry_ids: dict[int, int] = {}
        for k, e in enumerate(sorted(self.pending, key=lambda x: (x["day"], x["journal"], x["branch_id"] or 0))):
            day = e["day"]
            rate = fx_rate(gen, e["ccy"], day)
            eid = gen.next_id("accounting.journal_entries")
            e["id"] = eid
            for rid in e["ref_ids"]:
                entry_ids[rid] = eid
            per = period_for(gen, day)
            lines = []
            cbase_total = ZERO
            camt_total = ZERO
            for code, amt, text in e["credits"]:
                base = D4(amt * Decimal(str(rate))) if e["ccy"] != "MNT" else amt
                lines.append(("C", code, amt, base, text))
                cbase_total += base
                camt_total += amt
            # Дебит талыг кредитийн нийлбэрээр тэнцүүлнэ (валютын бөөрөнхийлөлтөөс сэргийлнэ)
            dsum = sum((a for _, a, _ in e["debits"]), ZERO)
            d_lines = []
            acc_amt = ZERO
            acc_base = ZERO
            for i, (code, amt, text) in enumerate(e["debits"]):
                if i == len(e["debits"]) - 1:
                    a, b = camt_total - acc_amt, cbase_total - acc_base
                else:
                    a = D(camt_total * amt / dsum)
                    b = D4(a * Decimal(str(rate))) if e["ccy"] != "MNT" else a
                acc_amt += a
                acc_base += b
                d_lines.append(("D", code, a, b, text))
            lines = d_lines + lines
            posted = ts(day, 19)
            self.entries.add(id=eid, entry_no=gen.number("JOURNAL_ENTRY", day), journal_id=ref.journals[e["journal"]],
                             period_id=per.id, branch_id=e["branch_id"], entry_date=day, value_date=day,
                             posting_date=day, description=e["desc"], reference=None,
                             source_system=e["source_system"], source_entity_type=e["entity_type"],
                             source_entity_id=e["entity_id"], currency_code=e["ccy"], total_debit=cbase_total,
                             total_credit=cbase_total, status="POSTED", posted_at=posted, posted_by=accountant_user,
                             approved_by=accountant_user, is_reversal=False, created_at=posted, updated_at=posted)
            for n, (drcr, code, amt, base, text) in enumerate(lines, 1):
                gl = ref.coa[code]
                self.lines.add(id=gen.next_id("accounting.journal_lines"), entry_id=eid, posting_date=day, line_no=n,
                               gl_account_id=gl.id, branch_id=e["branch_id"], dr_cr=drcr, amount=amt,
                               currency_code=e["ccy"], fx_rate=rate if e["ccy"] != "MNT" else None, base_amount=base,
                               subledger_type=e["entity_type"], subledger_id=e["entity_id"],
                               customer_id=e["customer_id"], product_id=e["product_id"], description=text,
                               created_at=posted)
                t = self.turnover[(gl.id, per.id, e["branch_id"], e["ccy"])]
                if drcr == "D":
                    t[0] += amt
                    t[2] += base
                else:
                    t[1] += amt
                    t[3] += base
        return entry_ids


def build_accounting(gen: Gen) -> None:
    rng = gen.rng.child("accounting")
    J = Journal(gen)
    ho = gen.ref.branches["HO"]
    accountants = [e for e in gen.employees if e.position == "ACCOUNTANT"]
    acct_user = gen.users_by_employee[accountants[0].id] if accountants else gen.sys_user_id

    J.add(gen.start, "GJ", ho.id, "MNT", "Хувь нийлүүлсэн хөрөнгө", credits=[("3100", 60_000_000_000, "Дүрмийн сан")],
          debits=[("1120", 60_000_000_000, "Банкин дахь харилцах")])

    monthly = defaultdict(lambda: defaultdict(Decimal))     # (me, branch, ccy) -> {key: amount}
    accrual_refs = defaultdict(list)                        # (me, branch, ccy) -> [accr row index]
    for ln in gen.loans:
        if ln.disb is None:
            continue
        b = ln.branch.id
        credit_acc = "2120" if getattr(ln, "disb_method", "") == "INTERNAL_ACCOUNT" else \
            ("1110" if getattr(ln, "disb_method", "") == "CASH" else "1120")
        J.add(ln.disb, "LOAN_J", b, ln.currency, f"Зээл олголт {ln.loan_number}",
              credits=[(credit_acc, ln.principal, "Зээл олголт")], debits=[("1210", ln.principal, "Зээлийн үндсэн өр")],
              source_system="LOAN", entity_type="LOAN", entity_id=ln.id, customer_id=ln.cust.id, product_id=ln.prod.id)
        for p in ln.ledger.payments:
            if p.status != "POSTED":
                continue
            key = (month_end(p.date), b, ln.currency)
            debit_acc = {"CASH": "1110", "AUTO_DEBIT": "2120", "SALARY_DEDUCTION": "2120", "OFFSET": "2120"}.get(
                p.method, "1120")
            m = monthly[key]
            m["repay_" + debit_acc] += p.amount
            for a in p.allocations:
                comp = "INTEREST" if a.component in ("INTEREST", "OVERDUE_INTEREST") else a.component
                m["alloc_" + comp] += a.amount
            m["alloc_SUSPENSE"] += p.unallocated
        for c in ln.ledger.charges:
            if c.charged <= gen.as_of:
                monthly[(month_end(c.charged), b, ln.currency)]["charge_" + c.kind] += c.amount
        for idx, me, interest, suspended in getattr(ln, "accrual_rows", []):
            if not suspended:
                monthly[(me, b, ln.currency)]["accrual"] += interest
                accrual_refs[(me, b, ln.currency)].append(idx)
        for me, dpd, state, total, grade in ln.snapshots:
            m = monthly[(me, b, ln.currency)]
            m["provision"] += D(max(total, ZERO) * Decimal(str(grade.provision_rate)) / 100)
            if grade.is_npl:
                m["npl_principal"] += state["principal_outstanding"]
        if ln.written_off:
            wo = ln.written_off
            J.add(wo["date"], "ADJ_J", b, ln.currency, f"Зээл данснаас хасав {ln.loan_number}",
                  credits=[("1230", wo["state"]["principal_outstanding"], "Чанаргүй зээлээс хасав")],
                  debits=[("1290", wo["state"]["principal_outstanding"], "Эрсдэлийн сангаас")],
                  source_system="LOAN", entity_type="LOAN", entity_id=ln.id, customer_id=ln.cust.id,
                  product_id=ln.prod.id)
            monthly[(month_end(wo["date"]), b, ln.currency)]["writeoff"] += wo["state"]["principal_outstanding"]
    # Хадгаламж ба харилцах данс (сарын нэгтгэл)
    txn = gen.store["account.transactions"]
    ci = {c: txn.col(c) for c in ("account_id", "amount", "direction", "value_date", "status_id", "transaction_type_id",
                                  "currency_code", "branch_id")}
    acc_type = {a.id: a.atype for a in gen.accounts}
    completed = gen.ref.txn_statuses["COMPLETED"]
    tt_by_id = {t.id: t.type_code for t in gen.ref.txn_types.values()}
    for row in txn.rows:
        if row[ci["status_id"]] != completed or row[ci["value_date"]] < gen.start:
            continue
        atype = acc_type.get(row[ci["account_id"]])
        ttype = tt_by_id[row[ci["transaction_type_id"]]]
        if atype is None or ttype in ("LOAN_DISB", "LOAN_REPAY"):
            continue
        key = (month_end(row[ci["value_date"]]), row[ci["branch_id"]], row[ci["currency_code"]])
        side = "in" if row[ci["direction"]] == "C" else "out"
        if atype == "SAVINGS":
            if ttype == "INTEREST_CREDIT":
                monthly[key]["dep_interest_credit"] += row[ci["amount"]]
            else:
                monthly[key]["dep_" + side] += row[ci["amount"]]
        else:
            monthly[key]["cur_" + side] += row[ci["amount"]]
    dacc = gen.store.tables.get("deposit.deposit_interest_accruals")
    if dacc is not None:
        dep_branch = {d.id: (d.acc.branch.id, d.ccy) for d in gen.deposits}
        cdid, cdate, cint, ctax = dacc.col("deposit_account_id"), dacc.col("accrual_date"), dacc.col("interest_amount"), \
            dacc.col("withholding_tax")
        for row in dacc.rows:
            br, ccy = dep_branch[row[cdid]]
            monthly[(row[cdate], br, ccy)]["dep_accrual"] += row[cint]
            monthly[(row[cdate], br, ccy)]["dep_tax"] += row[ctax]

    prev_prov = defaultdict(Decimal)
    prev_npl = defaultdict(Decimal)
    for key in sorted(monthly, key=lambda k: (k[0], k[1] or 0, k[2])):
        me, b, ccy = key
        m = monthly[key]
        for acc in ("1110", "1120", "2120"):
            amt = m.get("repay_" + acc, ZERO)
            if amt > 0:
                share = amt / sum((m.get("repay_" + x, ZERO) for x in ("1110", "1120", "2120")), ZERO)
                J.add(me, "LOAN_J", b, ccy, f"{me:%Y-%m} зээлийн эргэн төлөлт",
                      credits=[("1210", m["alloc_PRINCIPAL"] * share, "Үндсэн өр"),
                               ("1300", m["alloc_INTEREST"] * share, "Хүү"),
                               ("1310", m["alloc_PENALTY"] * share, "Торгууль"),
                               ("1320", m["alloc_FEE"] * share, "Шимтгэл"),
                               ("2400", m["alloc_SUSPENSE"] * share, "Хуваарилагдаагүй")],
                      debits=[(acc, amt, "Төлбөр хүлээн авав")], source_system="LOAN")
        J.add(me, "ACCR_J", b, ccy, f"{me:%Y-%m} хүүгийн хуримтлал",
              credits=[("4100", m.get("accrual", ZERO), "Хүүгийн орлого")],
              debits=[("1300", m.get("accrual", ZERO), "Хуримтлагдсан хүүгийн авлага")], source_system="BATCH",
              ref_ids=accrual_refs.get(key, []))
        J.add(me, "FEE_J", b, ccy, f"{me:%Y-%m} шимтгэлийн орлого",
              credits=[("4200", m.get("charge_FEE", ZERO), "Шимтгэлийн орлого")],
              debits=[("1320", m.get("charge_FEE", ZERO), "Шимтгэлийн авлага")], source_system="BATCH")
        J.add(me, "ACCR_J", b, ccy, f"{me:%Y-%m} торгуулийн орлого",
              credits=[("4300", m.get("charge_PENALTY", ZERO), "Торгуулийн орлого")],
              debits=[("1310", m.get("charge_PENALTY", ZERO), "Торгуулийн авлага")], source_system="BATCH")
        pk = (b, ccy)
        delta = m.get("provision", ZERO) - prev_prov[pk] - m.get("writeoff", ZERO)
        if delta > 0:
            J.add(me, "ADJ_J", b, ccy, f"{me:%Y-%m} эрсдэлийн сан байгуулав",
                  credits=[("1290", delta, "Эрсдэлийн сан")], debits=[("5200", delta, "Эрсдэлийн сангийн зардал")],
                  source_system="BATCH")
        elif delta < 0:
            J.add(me, "ADJ_J", b, ccy, f"{me:%Y-%m} эрсдэлийн сангийн бууралт",
                  credits=[("5200", -delta, "Эрсдэлийн сангийн буцаалт")], debits=[("1290", -delta, "Эрсдэлийн сан")],
                  source_system="BATCH")
        prev_prov[pk] = m.get("provision", ZERO)
        npl_delta = m.get("npl_principal", ZERO) - prev_npl[pk] + m.get("writeoff", ZERO)
        if npl_delta > 0:
            J.add(me, "ADJ_J", b, ccy, f"{me:%Y-%m} чанаргүй зээлийн ангилал",
                  credits=[("1210", npl_delta, "Хэвийн зээлээс")], debits=[("1230", npl_delta, "Чанаргүй зээл рүү")],
                  source_system="BATCH")
        elif npl_delta < 0:
            J.add(me, "ADJ_J", b, ccy, f"{me:%Y-%m} чанаргүй зээлээс сайжирсан",
                  credits=[("1230", -npl_delta, "Чанаргүй зээлээс")], debits=[("1210", -npl_delta, "Хэвийн зээл рүү")],
                  source_system="BATCH")
        prev_npl[pk] = m.get("npl_principal", ZERO)
        J.add(me, "DEP_J", b, ccy, f"{me:%Y-%m} хадгаламж хүлээн авав",
              credits=[("2110", m.get("dep_in", ZERO), "Хадгаламжийн өглөг")],
              debits=[("1110", m.get("dep_in", ZERO), "Касс")], source_system="DEPOSIT")
        J.add(me, "DEP_J", b, ccy, f"{me:%Y-%m} хадгаламжийн зарлага",
              credits=[("1110", m.get("dep_out", ZERO), "Касс")],
              debits=[("2110", m.get("dep_out", ZERO), "Хадгаламжийн өглөг")], source_system="DEPOSIT")
        J.add(me, "DEP_J", b, ccy, f"{me:%Y-%m} хадгаламжийн хүүгийн зардал",
              credits=[("2200", m.get("dep_accrual", ZERO), "Төлбөл зохих хүү")],
              debits=[("5100", m.get("dep_accrual", ZERO), "Хүүгийн зардал")], source_system="DEPOSIT")
        paid_net = m.get("dep_interest_credit", ZERO)
        if paid_net > 0:
            tax = min(m.get("dep_tax", ZERO), D(paid_net / 9))
            J.add(me, "DEP_J", b, ccy, f"{me:%Y-%m} хадгаламжийн хүү олгов",
                  credits=[("2110", paid_net, "Хадгаламжид нэмэв"), ("2300", tax, "Хүүгийн татвар")],
                  debits=[("2200", paid_net + tax, "Төлбөл зохих хүү")], source_system="DEPOSIT")
        J.add(me, "CASH_J", b, ccy, f"{me:%Y-%m} харилцах дансны орлого",
              credits=[("2120", m.get("cur_in", ZERO), "Харилцах данс")],
              debits=[("1120", m.get("cur_in", ZERO), "Банкин дахь харилцах")], source_system="TELLER")
        J.add(me, "CASH_J", b, ccy, f"{me:%Y-%m} харилцах дансны зарлага",
              credits=[("1120", m.get("cur_out", ZERO), "Банкин дахь харилцах")],
              debits=[("2120", m.get("cur_out", ZERO), "Харилцах данс")], source_system="TELLER")
    # Үйл ажиллагааны зардал (сар бүр, салбар бүр)
    staff = defaultdict(int)
    for e in gen.employees:
        staff[e.branch.id] += 1
    for me in month_ends_between(gen.start, gen.as_of):
        for b in gen.branches:
            if b.opened_date > me or (b.closed_date and b.closed_date < me):
                continue
            cost = D(staff[b.id] * rng.uniform(1_500_000, 1_900_000) + rng.uniform(1_500_000, 6_000_000))
            J.add(me, "GJ", b.id, "MNT", f"{me:%Y-%m} үйл ажиллагааны зардал",
                  credits=[("1120", cost, "Цалин, түрээс, бусад")], debits=[("5400", cost, "Үйл ажиллагааны зардал")])
    entry_ids = J.flush(acct_user)
    # Хүүгийн хуримтлалын мөрийг журналын бичилттэй холбоно
    accr = gen.store["loan.loan_interest_accruals"]
    for idx, eid in entry_ids.items():
        accr.update(idx, journal_entry_id=eid)
    _ledger_balances(gen, J)


def _ledger_balances(gen: Gen, J: Journal) -> None:
    lb = gen.store.table("accounting.ledger_balances", [
        "id", "gl_account_id", "period_id", "branch_id", "currency_code", "opening_debit", "opening_credit",
        "period_debit", "period_credit", "closing_debit", "closing_credit", "base_closing_balance", "last_updated_at"])
    periods = sorted(gen.ref.periods.values(), key=lambda p: p.start_date)
    periods = [p for p in periods if p.start_date <= gen.as_of and p.start_date >= date(gen.start.year, gen.start.month, 1)]
    combos = defaultdict(dict)
    for (gl, per, br, ccy), t in J.turnover.items():
        combos[(gl, br, ccy)][per] = t
    for (gl, br, ccy), per_map in combos.items():
        od = oc = ob = ZERO
        started = False
        for p in periods:
            t = per_map.get(p.id)
            if t is None and not started:
                continue
            started = True
            pdb, pcr, bd, bc = t if t else (ZERO, ZERO, ZERO, ZERO)
            cd, cc = od + pdb, oc + pcr
            ob = ob + bd - bc
            lb.add(id=gen.next_id("accounting.ledger_balances"), gl_account_id=gl, period_id=p.id, branch_id=br,
                   currency_code=ccy, opening_debit=od, opening_credit=oc, period_debit=pdb, period_credit=pcr,
                   closing_debit=cd, closing_credit=cc, base_closing_balance=ob,
                   last_updated_at=ts(min(p.end_date, gen.as_of), 23))
            od, oc = cd, cc
