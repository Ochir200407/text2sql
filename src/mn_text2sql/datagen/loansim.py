"""Нэг зээлийн хуваарь ба төлөлтийн симуляц (as_of огноо хүртэл).

Хуваарь: loan.fn_generate_schedule-тэй ижил томьёо (сарын хүү = жилийн/12, аннуитет, ажлын өдөр).
Төлбөрийн хуваарилалт: бүтээгдэхүүний дүрмийн дараалал PENALTY → FEE → OVERDUE_INTEREST → INTEREST →
PRINCIPAL. (DB-ийн fn_allocate_payment нь INTEREST шатанд ирээдүйн хуваарийн хүүг ч төлдөг тул энд
зөвхөн хугацаа нь болсон мөрүүдэд хуваарилж, урьдчилсан төлөлтийг тусад нь үндсэн өрөнд тооцно —
db/CHANGES.md-ийн "Мэдэгдэж буй асуудал"-ыг үз.)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal

from .util import ZERO, D, add_months, next_business_day


@dataclass
class Item:
    no: int
    due: date
    start: date
    principal_due: Decimal
    interest_due: Decimal
    opening: Decimal
    closing: Decimal
    fee_due: Decimal = ZERO
    principal_paid: Decimal = ZERO
    interest_paid: Decimal = ZERO
    penalty_charged: Decimal = ZERO
    penalty_paid: Decimal = ZERO
    paid_on: date | None = None           # бүрэн төлөгдсөн огноо
    history: list = field(default_factory=list)  # [(огноо, principal_paid, interest_paid)]
    status_override: str | None = None
    db_id: int | None = None

    @property
    def unpaid_principal(self) -> Decimal:
        return self.principal_due - self.principal_paid

    @property
    def unpaid_interest(self) -> Decimal:
        return self.interest_due - self.interest_paid

    @property
    def is_paid(self) -> bool:
        return self.unpaid_principal <= 0 and self.unpaid_interest <= 0

    def paid_by(self, t: date) -> tuple[Decimal, Decimal]:
        p = i = ZERO
        for d, pp, ii in self.history:
            if d <= t:
                p, i = pp, ii
            else:
                break
        return p, i


def build_schedule(principal: Decimal, annual_rate: float, term: int, amort: str, disb: date,
                   first_payment: date, start_no: int = 1) -> list[Item]:
    items: list[Item] = []
    balance = principal
    monthly = Decimal(str(annual_rate)) / Decimal(100) / Decimal(12)
    payment = None
    if amort == "ANNUITY":
        if monthly == 0:
            payment = balance / term
        else:
            m = float(monthly)
            payment = Decimal(str(float(balance) * m / (1 - (1 + m) ** (-term))))
    prev = disb
    for k in range(1, term + 1):
        due = next_business_day(add_months(first_payment, k - 1))
        interest = D(balance * monthly)
        if amort == "ANNUITY":
            prin = D(payment - interest)
        elif amort == "EQUAL_PRINCIPAL":
            prin = D(principal / term)
        else:  # BULLET, INTEREST_ONLY
            prin = balance if k == term else ZERO
        if k == term or prin > balance:
            prin = balance
        if prin < 0:
            prin = ZERO
        items.append(Item(no=start_no + k - 1, due=due, start=prev, principal_due=prin, interest_due=interest,
                          opening=balance, closing=balance - prin))
        balance -= prin
        prev = due
    return items


@dataclass
class Allocation:
    component: str
    amount: Decimal
    item: Item | None


@dataclass
class Payment:
    date: date
    amount: Decimal
    ptype: str
    method: str
    allocations: list[Allocation] = field(default_factory=list)
    unallocated: Decimal = ZERO
    status: str = "POSTED"
    db_id: int | None = None
    payment_no: str | None = None
    txn_id: int | None = None


@dataclass
class Charge:
    """Торгууль эсвэл шимтгэл."""
    kind: str                 # PENALTY | FEE
    code: str                 # LATE_PAYMENT | DISBURSEMENT_FEE | EARLY_REPAY_FEE | RESTRUCTURE_FEE
    charged: date
    amount: Decimal
    item: Item | None = None
    base: Decimal = ZERO
    days: int = 0
    paid: Decimal = ZERO
    waived: Decimal = ZERO
    paid_on: date | None = None

    @property
    def due(self) -> Decimal:
        return self.amount - self.waived - self.paid


class Ledger:
    """Нэг зээлийн хуваарь + төлбөр + торгууль/шимтгэлийн төлөв."""

    def __init__(self, items: list[Item], penalty_rate: float, grace_days: int):
        self.items = items
        self.penalty_rate = penalty_rate
        self.grace_days = grace_days
        self.charges: list[Charge] = []
        self.payments: list[Payment] = []

    # ------------------------------------------------------------ дүн
    def due_items(self, on: date) -> list[Item]:
        return [it for it in self.items if it.due <= on and not it.is_paid]

    def amount_due(self, on: date) -> Decimal:
        total = sum((c.due for c in self.charges if c.charged <= on and c.due > 0), ZERO)
        for it in self.due_items(on):
            total += it.unpaid_principal + it.unpaid_interest
        return total

    def charge_penalties_until(self, on: date) -> None:
        """on өдөр төлөгдөх мөрүүдийн хоцролтын торгуулийг тооцно (хөнгөлөлтийн хоногоос хэтэрсэн бол)."""
        for it in self.due_items(on):
            late = (on - it.due).days
            if late > self.grace_days and not any(c.item is it and c.kind == "PENALTY" for c in self.charges):
                base = it.unpaid_principal + it.unpaid_interest
                amount = D(base * Decimal(str(self.penalty_rate)) / Decimal(100) * late / Decimal(365))
                if amount > 0:
                    self.charges.append(Charge("PENALTY", "LATE_PAYMENT", on, amount, it, base, late))

    def _touch(self, it: Item, on: date) -> None:
        it.history.append((on, it.principal_paid, it.interest_paid))
        if it.is_paid and it.paid_on is None:
            it.paid_on = on

    def apply_payment(self, pay: Payment, prepay_all: bool = False) -> None:
        """Төлбөрийг хуваарилна. prepay_all=True бол үлдсэн бүх үндсэн өрийг хаана (урьдчилан бүрэн төлөлт)."""
        remaining = pay.amount
        on = pay.date
        for c in sorted((c for c in self.charges if c.kind == "PENALTY" and c.charged <= on and c.due > 0),
                        key=lambda c: c.charged):
            if remaining <= 0:
                break
            x = min(remaining, c.due)
            c.paid += x
            if c.due <= 0:
                c.paid_on = on
            if c.item is not None:
                c.item.penalty_paid += x
            pay.allocations.append(Allocation("PENALTY", x, None))
            remaining -= x
        for c in sorted((c for c in self.charges if c.kind == "FEE" and c.charged <= on and c.due > 0),
                        key=lambda c: c.charged):
            if remaining <= 0:
                break
            x = min(remaining, c.due)
            c.paid += x
            if c.due <= 0:
                c.paid_on = on
            pay.allocations.append(Allocation("FEE", x, None))
            remaining -= x
        touched: set[int] = set()
        for comp, pred in (("OVERDUE_INTEREST", lambda it: it.due < on), ("INTEREST", lambda it: it.due <= on)):
            for it in self.items:
                if remaining <= 0:
                    break
                if pred(it) and it.unpaid_interest > 0:
                    x = min(remaining, it.unpaid_interest)
                    it.interest_paid += x
                    pay.allocations.append(Allocation(comp, x, it))
                    remaining -= x
                    touched.add(id(it))
        for it in self.items:
            if remaining <= 0:
                break
            if (it.due <= on or prepay_all) and it.unpaid_principal > 0:
                x = min(remaining, it.unpaid_principal)
                it.principal_paid += x
                pay.allocations.append(Allocation("PRINCIPAL", x, it))
                remaining -= x
                touched.add(id(it))
        if prepay_all:
            # Урьдчилан бүрэн хаасан: хуваарийг дахин тооцож ирээдүйн мөрийн хүүг тэглэнэ
            # (loan_schedules.total_interest эцсийн мөрүүдээс тооцогдоно).
            for it in self.items:
                if it.due > on and it.unpaid_interest > 0 and it.unpaid_principal <= 0:
                    it.interest_due = it.interest_paid
                    touched.add(id(it))
        for it in self.items:
            if id(it) in touched:
                self._touch(it, on)
        pay.unallocated = remaining
        self.payments.append(pay)

    # ------------------------------------------------------------ агшин
    def state_at(self, t: date) -> dict:
        """t өдрийн эцсийн төлөв: DPD, хугацаа хэтэрсэн дүн, үлдэгдэл."""
        oldest_overdue = None
        ovd_p = ovd_i = ZERO
        prin_paid = int_paid = ZERO
        next_unpaid = None
        for it in self.items:
            p, i = it.paid_by(t)
            prin_paid += p
            int_paid += i
            unpaid = (it.principal_due - p) > 0 or (it.interest_due - i) > 0
            if it.due < t and unpaid:
                ovd_p += it.principal_due - p
                ovd_i += it.interest_due - i
                if oldest_overdue is None:
                    oldest_overdue = it.due
            if unpaid and next_unpaid is None:
                next_unpaid = it.due
        total_p = sum((it.principal_due for it in self.items), ZERO)
        total_i = sum((it.interest_due for it in self.items), ZERO)
        pen_out = ZERO
        fee_out = ZERO
        for c in self.charges:
            if c.charged <= t:
                paid = c.paid if (c.paid_on is None or c.paid_on <= t) else ZERO
                # хэсэгчилсэн төлөлтийн огноог нарийвчлахгүй — ойролцоо утга
                out = c.amount - c.waived - paid
                if c.kind == "PENALTY":
                    pen_out += max(out, ZERO)
                else:
                    fee_out += max(out, ZERO)
        dpd = (t - oldest_overdue).days if oldest_overdue else 0
        return {
            "dpd": dpd, "overdue_principal": ovd_p, "overdue_interest": ovd_i,
            "principal_outstanding": total_p - prin_paid, "interest_outstanding": total_i - int_paid,
            "penalty_outstanding": pen_out, "fee_outstanding": fee_out, "oldest_due": next_unpaid,
            "first_overdue": oldest_overdue,
        }

    def all_paid(self) -> bool:
        return all(it.is_paid for it in self.items) and all(c.due <= 0 for c in self.charges)


def late_days(rng) -> int:
    return rng.weighted([(rng.randint(1, 5), 0.30), (rng.randint(6, 15), 0.30), (rng.randint(16, 35), 0.25),
                         (rng.randint(36, 80), 0.15)])


def shift_back(d: date, days: int, floor: date) -> date:
    x = d - timedelta(days=days)
    return x if x > floor else floor
