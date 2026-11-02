"""Өөрийн схемийн дүрэм/загварт суурилсан синтетик (монгол асуулт, англи асуулт, PostgreSQL) гурвал — GPU-гүй.

SING-SQL / OmniSQL / SENSE маягийн «зорилтот» синтез: сул талын хэв маягийг (docs/SQL_QUALITY_AND_GAP.md)
semantic_model.yaml-ийн FK, enum утга (монгол нэр), *_mnt багана ба t2s.fx_to_mnt дүрмээс угсарна.
Хэв маяг (pattern): join_fk, cte_multi, ratio_nullif, lag_pop, multi_currency, topk_ties, enum_filter.

Шалгалт: sqlglot parse + SQL хамгаалагч (security/sql_guard.py). Гүйцэтгэлийн шалгалтыг Kaggle (PG)-д
scripts/synth/exec_validate.py хийнэ — бүх мөр needs_exec_check: true.
Leakage: dev+test-ийн асуулт (бүх хувилбар) ба gold SQL-тэй тэмдэгтийн 3-gram cosine (eval/split.py-ийн
_vec/_cos) — ойролцоог хасна. Eval-ийн текстийг тайланд бичихгүй (зөвхөн id, оноо).

    python -m mn_text2sql.training.synth_own --out build/synth_own        # эсвэл scripts/synth/generate.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import sqlglot

from ..config import resolve_path

PATTERNS = ("join_fk", "cte_multi", "ratio_nullif", "lag_pop", "multi_currency", "topk_ties", "enum_filter")
QUOTA = {"join_fk": 420, "cte_multi": 420, "ratio_nullif": 400, "lag_pop": 360, "multi_currency": 380,
         "topk_ties": 380, "enum_filter": 420}
AS_OF = "2026-06-30"
Q_THRESHOLD = 0.8        # eval/split.py, training.data.dedup_against_eval-тэй ижил
SQL_THRESHOLD = 0.9      # gold SQL-ийн 3-gram cosine

# хүснэгт бүрийн тогтмол alias (нэг асуулгад давхцахгүй)
ALIAS = {
    "loans": "l", "branches": "b", "products": "p", "customers": "c", "employees": "e", "individuals": "i",
    "organizations": "o", "loan_applications": "la", "loan_payments": "lp", "deposits": "d", "transactions": "t",
    "collection_cases": "cc", "collaterals": "co", "aml_alerts": "aa", "gl_entries": "g", "loan_charges": "lc",
    "loan_writeoffs": "lw", "collection_promises": "cp", "credit_scores": "cs", "accounts": "ac",
    "loan_dpd_history": "h", "account_balances_monthly": "abm", "exchange_rates": "xr",
}
DIM_TABLES = {"branches", "products", "employees", "customers", "individuals", "organizations"}


# ---------------------------------------------------------------------------
# Хугацаа
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Period:
    key: str
    start: str
    end: str
    mn: str
    en: str


PERIODS = [
    Period("2024", "2024-01-01", "2024-12-31", "2024 онд", "in 2024"),
    Period("2025", "2025-01-01", "2025-12-31", "2025 онд", "in 2025"),
    Period("2026h1", "2026-01-01", "2026-06-30", "2026 оны эхний хагаст", "in the first half of 2026"),
    Period("2025q1", "2025-01-01", "2025-03-31", "2025 оны 1-р улиралд", "in Q1 2025"),
    Period("2025q2", "2025-04-01", "2025-06-30", "2025 оны 2-р улиралд", "in Q2 2025"),
    Period("2025q3", "2025-07-01", "2025-09-30", "2025 оны 3-р улиралд", "in Q3 2025"),
    Period("2025q4", "2025-10-01", "2025-12-31", "2025 оны 4-р улиралд", "in Q4 2025"),
    Period("2026q1", "2026-01-01", "2026-03-31", "2026 оны 1-р улиралд", "in Q1 2026"),
    Period("2026q2", "2026-04-01", "2026-06-30", "2026 оны 2-р улиралд", "in Q2 2026"),
]
YEARLY = PERIODS[:3]
# LAG-ийн цуваа: (давтамж, муж)
SERIES_RANGES = [
    ("month", Period("2025", "2025-01-01", "2025-12-31", "2025 онд", "in 2025")),
    ("month", Period("2026h1", "2026-01-01", "2026-06-30", "2026 оны эхний хагаст", "in the first half of 2026")),
    ("month", Period("2024", "2024-01-01", "2024-12-31", "2024 онд", "in 2024")),
    ("month", Period("2425", "2024-07-01", "2025-06-30", "2024 оны 7-р сараас 2025 оны 6-р сар хүртэл",
                     "from July 2024 to June 2025")),
    ("quarter", Period("2426", "2024-01-01", "2026-06-30", "2024 оноос 2026 оны 6-р сар хүртэл",
                       "from 2024 to June 2026")),
    ("quarter", Period("2025", "2025-01-01", "2025-12-31", "2025 онд", "in 2025")),
    ("year", Period("2426", "2024-01-01", "2025-12-31", "2024–2025 онуудад", "over 2024–2025")),
]
@dataclass(frozen=True)
class Grain:
    sql: str
    by: str          # «сар бүрээр»
    prev_com: str    # «өмнөх сартай»
    prev_abl: str    # «өмнөх сараасаа»
    prev_gen: str    # «өмнөх сарын»
    en: str
    pop: str
    prev_en: str
    each: str = ""   # «сар бүрийн»
    by_n: str = ""   # «сараар нь»
    unit_en: str = ""


GRAIN = {
    "month": Grain("date_trunc('month', {d})::date", "сар бүрээр", "өмнөх сартай", "өмнөх сараасаа", "өмнөх сарын",
                   "monthly", "month-over-month", "previous month", "сар бүрийн", "сараар нь", "month"),
    "quarter": Grain("date_trunc('quarter', {d})::date", "улирал бүрээр", "өмнөх улиралтай", "өмнөх улирлаасаа",
                     "өмнөх улирлын", "quarterly", "quarter-over-quarter", "previous quarter", "улирал бүрийн",
                     "улирлаар нь", "quarter"),
    "year": Grain("EXTRACT(YEAR FROM {d})::int", "жил бүрээр", "өмнөх жилтэй", "өмнөх жилээсээ", "өмнөх жилийн",
                  "yearly", "year-over-year", "previous year", "жил бүрийн", "жилээр нь", "year"),
}


def per_gen(p: Period) -> str:
    """«2024 онд» → «2024 оны», «... хагаст» → «... хагасын», «... улиралд» → «... улирлын»."""
    return p.mn.replace("онд", "оны").replace("хагаст", "хагасын").replace("улиралд", "улирлын")


def per_com(p: Period) -> str:
    """Харьцуулах: «2024 оныхтой», «... хагасынхтай»."""
    return p.mn.replace("онд", "оныхтой").replace("хагаст", "хагасынхтай").replace("улиралд", "улирлынхтай")


def per_from(p: Period) -> str:
    """Гарах: «2024 оныхоос», «... хагасынхаас»."""
    return p.mn.replace("онд", "оныхоос").replace("хагаст", "хагасынхаас").replace("улиралд", "улирлынхаас")
# LAG-ийн гаралт: (монгол заах, монгол хамтын, англи)
LAG_WHAT = {"pct": ("өөрчлөлтийн хувийг", "өөрчлөлтийн хувийн", "the percentage change"),
            "diff": ("өөрчлөлтийн хэмжээг", "өөрчлөлтийн хэмжээний", "the absolute change"),
            "both": ("өөрчлөлтийн хэмжээ ба хувийг", "өөрчлөлтийн хэмжээ ба хувийн",
                     "the absolute and percentage change")}
CUR_GEN = {"USD": "ам.долларын", "EUR": "еврогийн", "CNY": "юанийн", "KRW": "воны", "JPY": "иенийн"}


# ---------------------------------------------------------------------------
# Каталог: хэмжигдэхүүн, баримт хүснэгт (fact), хэмжээс (dimension)
# ---------------------------------------------------------------------------

@dataclass
class Measure:
    key: str
    expr: str | None             # мөрийн түвшний илэрхийлэл (COUNT-д None)
    agg: str                     # SUM | AVG | COUNT
    bare: str                    # монгол нэр (нэрлэх тийн ялгал)
    bare_acc: str                # заах тийн ялгал
    en: str
    money: bool = False          # төгрөгөөр (*_mnt эсвэл fx_to_mnt)
    orig: str | None = None      # анхны валютаар (multi_currency)
    needs: tuple = ()            # шаардлагатай FK зам
    where: str | None = None     # хэмжигдэхүүний өөрийн нөхцөл (GL данс г.м.)


def COUNT_M() -> Measure:
    return Measure("cnt", None, "COUNT", "тоо", "тоог", "number")


@dataclass
class Fact:
    table: str
    domain: str
    pl: str                      # олон тоо, нэрлэх
    gen_pl: str                  # олон тоо, харьяалах
    verb: str                    # огнооны үйл үг («олгосон»)
    en_pl: str
    verb_en: str
    date: str
    measures: list[Measure]
    enums: list[str]             # өөрийн enum багана
    fks: dict[str, list[tuple[str, str, str]]]   # зам → [(эх багана, хүснэгт, зорилтот багана)]
    base: list[str] = field(default_factory=list)
    cur: str | None = None       # валютын багана (alias-тай)
    cur_needs: tuple = ()
    flags: list[tuple[str, str, str]] = field(default_factory=list)   # (sql, монгол тодотгол, англи)
    product_cat: str | None = None
    ratios: list[tuple] = field(default_factory=list)  # (тоологч, хуваагч, mn, mn_acc, en, glossary, needs)

    @property
    def a(self) -> str:
        return ALIAS[self.table]


def _fx(a: str, amount: str, date: str, cur: str | None = None) -> str:
    return f"{a}.{amount} * t2s.fx_to_mnt({cur or a + '.currency_code'}, {date if '.' in date or '(' in date else a + '.' + date})"


BD = "t2s.business_date()"

FACTS: dict[str, Fact] = {}


def _add(f: Fact) -> None:
    FACTS[f.table] = f


_add(Fact(
    "loans", "loans", "зээлүүд", "зээлүүдийн", "олгосон", "loans", "disbursed", "disbursement_date",
    [COUNT_M(),
     Measure("disb", "l.principal_amount_mnt", "SUM", "олгосон дүн", "олгосон дүнг", "disbursed principal", True,
             "l.principal_amount"),
     Measure("outst", "l.principal_outstanding_mnt", "SUM", "үндсэн үлдэгдэл", "үндсэн үлдэгдлийг",
             "outstanding principal", True, "l.principal_outstanding"),
     Measure("tot", "l.total_outstanding_mnt", "SUM", "нийт үлдэгдэл", "нийт үлдэгдлийг", "total outstanding", True,
             "l.total_outstanding"),
     Measure("intpaid", "l.interest_paid * l.fx_rate_to_mnt", "SUM", "төлөгдсөн хүү", "төлөгдсөн хүүг",
             "interest paid", True, "l.interest_paid"),
     Measure("rate", "l.interest_rate", "AVG", "жилийн хүү (%)", "жилийн хүүг (%)", "annual interest rate (%)"),
     Measure("term", "l.term_months", "AVG", "хугацаа (сараар)", "хугацааг (сараар)", "term in months"),
     Measure("dpd", "l.days_past_due", "AVG", "хугацаа хэтэрсэн хоног", "хугацаа хэтэрсэн хоногийг",
             "days past due")],
    ["status_code", "dpd_bucket", "classification_code", "amortization_type"],
    {"branch": [("branch_id", "branches", "branch_id")], "product": [("product_id", "products", "product_id")],
     "customer": [("customer_id", "customers", "customer_id")],
     "officer": [("loan_officer_id", "employees", "employee_id")],
     "application": [("application_id", "loan_applications", "application_id")]},
    cur="l.currency_code",
    flags=[("l.is_npl", "чанаргүй (NPL)", "non-performing"), ("l.is_active_portfolio", "идэвхтэй багцын",
                                                               "active-portfolio"),
           ("l.is_restructured", "бүтэц өөрчилсөн", "restructured")],
    product_cat="LOAN",
    ratios=[("SUM(l.principal_outstanding_mnt) FILTER (WHERE l.is_npl)", "SUM(l.principal_outstanding_mnt)",
             "чанаргүй зээлийн (NPL) хувь", "чанаргүй зээлийн (NPL) хувийг", "NPL ratio (%)", "npl_ratio", ()),
            ("SUM(l.principal_outstanding_mnt) FILTER (WHERE l.days_past_due > 30)",
             "SUM(l.principal_outstanding_mnt)", "PAR30 хувь", "PAR30 хувийг", "PAR30 (%)", "par30", ()),
            ("SUM(l.principal_outstanding_mnt) FILTER (WHERE l.days_past_due > 90)",
             "SUM(l.principal_outstanding_mnt)", "PAR90 хувь", "PAR90 хувийг", "PAR90 (%)", "par90", ()),
            ("SUM(l.principal_outstanding_mnt)", "SUM(l.principal_amount_mnt)",
             "үндсэн үлдэгдлийн олгосон дүнд эзлэх хувь", "үндсэн үлдэгдлийн олгосон дүнд эзлэх хувийг",
             "outstanding principal as a share of disbursed principal (%)", None, ())],
))
_add(Fact(
    "loan_applications", "applications", "зээлийн хүсэлтүүд", "зээлийн хүсэлтүүдийн", "ирсэн", "loan applications",
    "submitted", "submitted_date",
    [COUNT_M(),
     Measure("req", _fx("la", "requested_amount", "submitted_date"), "SUM", "хүссэн дүн", "хүссэн дүнг",
             "requested amount", True, "la.requested_amount"),
     Measure("appr", _fx("la", "approved_amount", "decided_date"), "SUM", "батлагдсан дүн", "батлагдсан дүнг",
             "approved amount", True, "la.approved_amount"),
     Measure("score", "la.credit_score", "AVG", "скорингийн оноо", "скорингийн оноог", "credit score"),
     Measure("dti", "la.dti_ratio", "AVG", "өр/орлогын харьцаа (DTI, %)", "өр/орлогын харьцааг (DTI, %)",
             "debt-to-income ratio (%)"),
     Measure("rterm", "la.requested_term_months", "AVG", "хүссэн хугацаа (сараар)", "хүссэн хугацааг (сараар)",
             "requested term in months")],
    ["status_code", "channel", "purpose_category", "decision_level"],
    {"branch": [("branch_id", "branches", "branch_id")], "product": [("product_id", "products", "product_id")],
     "customer": [("customer_id", "customers", "customer_id")],
     "officer": [("loan_officer_id", "employees", "employee_id")]},
    cur="la.currency_code", product_cat="LOAN",
    ratios=[("COUNT(*) FILTER (WHERE la.status_code IN ('APPROVED','CONTRACTED','EXPIRED'))",
             "COUNT(*) FILTER (WHERE la.status_code IN ('APPROVED','CONTRACTED','EXPIRED','REJECTED'))",
             "зөвшөөрлийн хувь", "зөвшөөрлийн хувийг", "approval rate (%)", "approval_rate", ()),
            ("COUNT(*) FILTER (WHERE la.status_code = 'REJECTED')",
             "COUNT(*) FILTER (WHERE la.status_code IN ('APPROVED','CONTRACTED','EXPIRED','REJECTED'))",
             "татгалзсан хувь", "татгалзсан хувийг", "rejection rate (%)", "rejection_rate", ()),
            (f"SUM({_fx('la', 'approved_amount', 'decided_date')})",
             f"SUM({_fx('la', 'requested_amount', 'submitted_date')})",
             "батлагдсан дүнгийн хүссэн дүнд эзлэх хувь", "батлагдсан дүнгийн хүссэн дүнд эзлэх хувийг",
             "approved amount as a share of requested amount (%)", None, ())],
))
_add(Fact(
    "loan_payments", "payments", "бүртгэгдсэн төлбөрүүд", "бүртгэгдсэн төлбөрүүдийн", "төлөгдсөн",
    "posted loan payments", "made", "payment_date",
    [COUNT_M(),
     Measure("amt", _fx("lp", "amount", "payment_date"), "SUM", "төлсөн дүн", "төлсөн дүнг", "payment amount", True,
             "lp.amount"),
     Measure("aint", _fx("lp", "allocated_interest", "payment_date"), "SUM", "хүүнд хуваарилсан дүн",
             "хүүнд хуваарилсан дүнг", "amount allocated to interest", True, "lp.allocated_interest"),
     Measure("apen", _fx("lp", "allocated_penalty", "payment_date"), "SUM", "торгуульд хуваарилсан дүн",
             "торгуульд хуваарилсан дүнг", "amount allocated to penalties", True, "lp.allocated_penalty"),
     Measure("avgamt", _fx("lp", "amount", "payment_date"), "AVG", "төлбөрийн дүн", "төлбөрийн дүнг",
             "payment amount", True, "lp.amount")],
    ["payment_method", "payment_type"],
    {"loan": [("loan_id", "loans", "loan_id")], "branch": [("branch_id", "branches", "branch_id")],
     "customer": [("customer_id", "customers", "customer_id")],
     "product": [("loan_id", "loans", "loan_id"), ("product_id", "products", "product_id")]},
    base=["lp.status = 'POSTED'"], cur="lp.currency_code", product_cat="LOAN",
    ratios=[(f"SUM({_fx('lp', 'allocated_interest', 'payment_date')})", f"SUM({_fx('lp', 'amount', 'payment_date')})",
             "төлбөрийн хүүнд хуваарилагдсан хувь", "төлбөрийн хүүнд хуваарилагдсан хувийг",
             "share of payments allocated to interest (%)", None, ()),
            (f"SUM({_fx('lp', 'allocated_principal', 'payment_date')})", f"SUM({_fx('lp', 'amount', 'payment_date')})",
             "төлбөрийн үндсэн төлбөрт хуваарилагдсан хувь", "төлбөрийн үндсэн төлбөрт хуваарилагдсан хувийг",
             "share of payments allocated to principal (%)", None, ())],
))
_add(Fact(
    "deposits", "deposits", "хадгаламжууд", "хадгаламжуудын", "нээсэн", "deposits", "opened", "opened_date",
    [COUNT_M(),
     Measure("bal", "d.current_balance_mnt", "SUM", "үлдэгдэл", "үлдэгдлийг", "balance", True, "d.current_balance"),
     Measure("princ", _fx("d", "principal_amount", "opened_date"), "SUM", "анх байршуулсан дүн",
             "анх байршуулсан дүнг", "initial principal", True, "d.principal_amount"),
     Measure("rate", "d.interest_rate", "AVG", "жилийн хүү (%)", "жилийн хүүг (%)", "annual interest rate (%)"),
     Measure("avgbal", "d.current_balance_mnt", "AVG", "үлдэгдэл", "үлдэгдлийг", "balance", True,
             "d.current_balance")],
    ["deposit_type", "interest_method", "status"],
    {"branch": [("branch_id", "branches", "branch_id")], "product": [("product_id", "products", "product_id")],
     "customer": [("customer_id", "customers", "customer_id")],
     "account": [("account_id", "accounts", "account_id")]},
    cur="d.currency_code",
    flags=[("d.is_pledged", "барьцаанд тавигдсан", "pledged"), ("d.auto_renew", "автоматаар сунгагддаг",
                                                                  "auto-renewing")],
    product_cat="DEPOSIT",
))
_add(Fact(
    "transactions", "deposits", "амжилттай гүйлгээнүүд", "амжилттай гүйлгээнүүдийн", "хийгдсэн",
    "completed transactions", "made", "transaction_date",
    [COUNT_M(),
     Measure("amt", "t.amount_mnt", "SUM", "гүйлгээний дүн", "гүйлгээний дүнг", "transaction amount", True,
             "t.amount"),
     Measure("fee", _fx("t", "fee_amount", "transaction_date"), "SUM", "шимтгэл", "шимтгэлийг", "fees", True,
             "t.fee_amount"),
     Measure("avgamt", "t.amount_mnt", "AVG", "гүйлгээний дүн", "гүйлгээний дүнг", "transaction amount", True,
             "t.amount")],
    ["transaction_type_code", "category", "channel_code", "direction"],
    {"branch": [("branch_id", "branches", "branch_id")], "customer": [("customer_id", "customers", "customer_id")],
     "account": [("account_id", "accounts", "account_id")]},
    base=["t.status_code = 'COMPLETED'"], cur="t.currency_code",
))
_add(Fact(
    "collection_cases", "collection", "барагдуулалтын кейсүүд", "барагдуулалтын кейсүүдийн", "нээгдсэн",
    "collection cases", "opened", "opened_date",
    [COUNT_M(),
     Measure("coll", _fx("cc", "collected_amount", "opened_date"), "SUM", "цуглуулсан дүн", "цуглуулсан дүнг",
             "collected amount", True, "cc.collected_amount"),
     Measure("oao", _fx("cc", "outstanding_at_open", "opened_date"), "SUM", "нээх үеийн үлдэгдэл",
             "нээх үеийн үлдэгдлийг", "outstanding at opening", True, "cc.outstanding_at_open"),
     Measure("dpd", "cc.dpd_at_open", "AVG", "нээх үеийн хэтэрсэн хоног", "нээх үеийн хэтэрсэн хоногийг",
             "days past due at opening")],
    ["stage_code", "priority", "status"],
    {"loan": [("loan_id", "loans", "loan_id")], "branch": [("branch_id", "branches", "branch_id")],
     "customer": [("customer_id", "customers", "customer_id")],
     "collector": [("assigned_to", "employees", "employee_id")],
     "product": [("loan_id", "loans", "loan_id"), ("product_id", "products", "product_id")]},
    cur="cc.currency_code", product_cat="LOAN",
    ratios=[(f"SUM({_fx('cc', 'collected_amount', 'opened_date')})",
             f"SUM({_fx('cc', 'outstanding_at_open', 'opened_date')})",
             "барагдуулалтын хувь", "барагдуулалтын хувийг", "collection recovery rate (%)",
             "collection_recovery_rate", ())],
))
_add(Fact(
    "collaterals", "loans", "барьцаа хөрөнгүүд", "барьцаа хөрөнгүүдийн", "үнэлгээ хийгдсэн", "collaterals",
    "valued", "valuation_date",
    [COUNT_M(),
     Measure("mkt", _fx("co", "market_value", "valuation_date"), "SUM", "зах зээлийн үнэлгээ",
             "зах зээлийн үнэлгээг", "market value", True, "co.market_value"),
     Measure("acc", _fx("co", "accepted_value", "valuation_date"), "SUM", "хүлээн зөвшөөрөх үнэлгээ",
             "хүлээн зөвшөөрөх үнэлгээг", "accepted value", True, "co.accepted_value")],
    ["collateral_type_code", "collateral_category", "encumbrance_status", "status"],
    {"branch": [("branch_id", "branches", "branch_id")],
     "customer": [("owner_customer_id", "customers", "customer_id")]},
    cur="co.currency_code", flags=[("co.is_insured", "даатгалтай", "insured")],
    ratios=[(f"SUM({_fx('co', 'accepted_value', 'valuation_date')})",
             f"SUM({_fx('co', 'market_value', 'valuation_date')})",
             "хүлээн зөвшөөрөх үнэлгээний зах зээлийн үнэлгээнд эзлэх хувь",
             "хүлээн зөвшөөрөх үнэлгээний зах зээлийн үнэлгээнд эзлэх хувийг",
             "accepted value as a share of market value (%)", None, ())],
))
_add(Fact(
    "aml_alerts", "customers", "AML сэрэмжлүүлгүүд", "AML сэрэмжлүүлгүүдийн", "илэрсэн", "AML alerts", "detected",
    "detected_date",
    [COUNT_M(),
     Measure("amt", _fx("aa", "alert_amount", "detected_date"), "SUM", "сэрэмжлүүлгийн дүн", "сэрэмжлүүлгийн дүнг",
             "alert amount", True, "aa.alert_amount")],
    ["rule_code", "severity", "status"],
    {"customer": [("customer_id", "customers", "customer_id")]},
    cur="aa.currency_code", flags=[("aa.str_reported", "СЭГТ-д мэдээлсэн", "reported to the FIU")],
))
_add(Fact(
    "gl_entries", "gl_trends", "ерөнхий дэвтрийн бичилтүүд", "ерөнхий дэвтрийн бичилтүүдийн", "бичигдсэн",
    "general ledger entries", "posted", "posting_date",
    [Measure("intinc", "g.amount_mnt", "SUM", "хүүгийн орлого", "хүүгийн орлогыг", "interest income", True,
             "g.amount", where="g.account_code = '4100' AND g.dr_cr = 'C'"),
     Measure("feeinc", "g.amount_mnt", "SUM", "шимтгэлийн орлого", "шимтгэлийн орлогыг", "fee income", True,
             "g.amount", where="g.account_code = '4200' AND g.dr_cr = 'C'"),
     Measure("peninc", "g.amount_mnt", "SUM", "торгуулийн орлого", "торгуулийн орлогыг", "penalty income", True,
             "g.amount", where="g.account_code = '4300' AND g.dr_cr = 'C'"),
     Measure("intexp", "g.amount_mnt", "SUM", "хүүгийн зардал", "хүүгийн зардлыг", "interest expense", True,
             "g.amount", where="g.account_code = '5100' AND g.dr_cr = 'D'"),
     Measure("opex", "g.amount_mnt", "SUM", "үйл ажиллагааны зардал", "үйл ажиллагааны зардлыг",
             "operating expense", True, "g.amount", where="g.account_code = '5400' AND g.dr_cr = 'D'")],
    ["journal_code", "source_system"],
    {"branch": [("branch_id", "branches", "branch_id")]},
    cur="g.currency_code",
))
_add(Fact(
    "loan_charges", "loans", "шимтгэл, торгуулийн бичилтүүд", "шимтгэл, торгуулийн бичилтүүдийн", "тооцсон",
    "loan fee and penalty charges", "charged", "charged_date",
    [COUNT_M(),
     Measure("amt", _fx("lc", "amount", "charged_date"), "SUM", "тооцсон дүн", "тооцсон дүнг", "charged amount",
             True, "lc.amount"),
     Measure("paid", _fx("lc", "paid_amount", "charged_date"), "SUM", "төлөгдсөн дүн", "төлөгдсөн дүнг",
             "paid amount", True, "lc.paid_amount"),
     Measure("waived", _fx("lc", "waived_amount", "charged_date"), "SUM", "чөлөөлсөн дүн", "чөлөөлсөн дүнг",
             "waived amount", True, "lc.waived_amount")],
    ["charge_type", "charge_code", "status"],
    {"loan": [("loan_id", "loans", "loan_id")],
     "branch": [("loan_id", "loans", "loan_id"), ("branch_id", "branches", "branch_id")],
     "product": [("loan_id", "loans", "loan_id"), ("product_id", "products", "product_id")],
     "customer": [("loan_id", "loans", "loan_id"), ("customer_id", "customers", "customer_id")]},
    cur="lc.currency_code", product_cat="LOAN",
    ratios=[(f"SUM({_fx('lc', 'paid_amount', 'charged_date')})", f"SUM({_fx('lc', 'amount', 'charged_date')})",
             "төлөгдсөн дүнгийн тооцсон дүнд эзлэх хувь", "төлөгдсөн дүнгийн тооцсон дүнд эзлэх хувийг",
             "paid amount as a share of charged amount (%)", None, ()),
            (f"SUM({_fx('lc', 'waived_amount', 'charged_date')})", f"SUM({_fx('lc', 'amount', 'charged_date')})",
             "чөлөөлсөн дүнгийн тооцсон дүнд эзлэх хувь", "чөлөөлсөн дүнгийн тооцсон дүнд эзлэх хувийг",
             "waived amount as a share of charged amount (%)", None, ())],
))
_WO_FX = "t2s.fx_to_mnt(l.currency_code, lw.writeoff_date)"
_add(Fact(
    "loan_writeoffs", "loans", "зээлийн хасалтууд", "зээлийн хасалтуудын", "хийгдсэн", "loan write-offs", "made",
    "writeoff_date",
    [COUNT_M(),
     Measure("tot", f"lw.total_amount * {_WO_FX}", "SUM", "хассан дүн", "хассан дүнг", "written-off amount", True,
             "lw.total_amount", needs=("loan",)),
     Measure("rec", f"lw.recovered_amount * {_WO_FX}", "SUM", "нөхөн төлөгдсөн дүн", "нөхөн төлөгдсөн дүнг",
             "recovered amount", True, "lw.recovered_amount", needs=("loan",))],
    ["writeoff_type"],
    {"loan": [("loan_id", "loans", "loan_id")],
     "branch": [("loan_id", "loans", "loan_id"), ("branch_id", "branches", "branch_id")],
     "product": [("loan_id", "loans", "loan_id"), ("product_id", "products", "product_id")],
     "customer": [("loan_id", "loans", "loan_id"), ("customer_id", "customers", "customer_id")]},
    cur="l.currency_code", cur_needs=("loan",), product_cat="LOAN",
    ratios=[(f"SUM(lw.recovered_amount * {_WO_FX})", f"SUM(lw.total_amount * {_WO_FX})",
             "нөхөн төлөлтийн хувь", "нөхөн төлөлтийн хувийг", "recovery rate after write-off (%)", "writeoff",
             ("loan",))],
))
_add(Fact(
    "collection_promises", "collection", "төлөх амлалтууд", "төлөх амлалтуудын", "хугацаа нь болох",
    "promises to pay", "due", "promised_date",
    [COUNT_M(),
     Measure("prom", _fx("cp", "promised_amount", "promised_date"), "SUM", "амласан дүн", "амласан дүнг",
             "promised amount", True, "cp.promised_amount"),
     Measure("paid", "COALESCE(cp.paid_amount, 0) * t2s.fx_to_mnt(cp.currency_code, cp.promised_date)", "SUM",
             "амлалтын дагуу төлсөн дүн", "амлалтын дагуу төлсөн дүнг", "amount paid under promises", True,
             "COALESCE(cp.paid_amount, 0)")],
    ["status"],
    {"case": [("case_id", "collection_cases", "case_id")],
     "branch": [("case_id", "collection_cases", "case_id"), ("branch_id", "branches", "branch_id")],
     "customer": [("case_id", "collection_cases", "case_id"), ("customer_id", "customers", "customer_id")],
     "collector": [("case_id", "collection_cases", "case_id"), ("assigned_to", "employees", "employee_id")]},
    cur="cp.currency_code",
    ratios=[("COUNT(*) FILTER (WHERE cp.status = 'KEPT')",
             "COUNT(*) FILTER (WHERE cp.status IN ('KEPT','PARTIALLY_KEPT','BROKEN'))",
             "амлалтын биелэлтийн хувь", "амлалтын биелэлтийн хувийг", "promise-to-pay kept rate (%)",
             "ptp_kept_rate", ()),
            ("SUM(COALESCE(cp.paid_amount, 0) * t2s.fx_to_mnt(cp.currency_code, cp.promised_date))",
             f"SUM({_fx('cp', 'promised_amount', 'promised_date')})",
             "төлсөн дүнгийн амласан дүнд эзлэх хувь", "төлсөн дүнгийн амласан дүнд эзлэх хувийг",
             "paid amount as a share of promised amount (%)", None, ())],
))
_add(Fact(
    "credit_scores", "applications", "скорингийн үнэлгээнүүд", "скорингийн үнэлгээнүүдийн", "тооцсон",
    "credit scores", "computed", "scored_date",
    [COUNT_M(),
     Measure("score", "cs.score", "AVG", "оноо", "оноог", "score"),
     Measure("pd", "cs.pd_estimate", "AVG", "дефолтын магадлал", "дефолтын магадлалыг", "probability of default")],
    ["model_code", "risk_grade_code", "decision_hint", "score_type"],
    {"customer": [("customer_id", "customers", "customer_id")],
     "application": [("application_id", "loan_applications", "application_id")],
     "branch": [("application_id", "loan_applications", "application_id"), ("branch_id", "branches", "branch_id")]},
))
_add(Fact(
    "accounts", "deposits", "дансууд", "дансуудын", "нээсэн", "accounts", "opened", "opened_date",
    [COUNT_M(),
     Measure("bal", _fx("ac", "current_balance", BD), "SUM", "одоогийн үлдэгдэл", "одоогийн үлдэгдлийг",
             "current balance", True, "ac.current_balance"),
     Measure("hold", _fx("ac", "hold_amount", BD), "SUM", "блоклосон дүн", "блоклосон дүнг", "hold amount", True,
             "ac.hold_amount")],
    ["account_type_code", "status"],
    {"branch": [("branch_id", "branches", "branch_id")], "customer": [("customer_id", "customers", "customer_id")],
     "product": [("product_id", "products", "product_id")]},
    cur="ac.currency_code", flags=[("ac.is_dormant", "хөдөлгөөнгүй", "dormant")], product_cat="DEPOSIT",
    ratios=[(f"SUM({_fx('ac', 'hold_amount', BD)})", f"SUM({_fx('ac', 'current_balance', BD)})",
             "блоклосон дүнгийн үлдэгдэлд эзлэх хувь", "блоклосон дүнгийн үлдэгдэлд эзлэх хувийг",
             "hold amount as a share of balance (%)", None, ())],
))
_add(Fact(
    "customers", "customers", "харилцагчид", "харилцагчдын", "бүртгэгдсэн", "customers", "onboarded",
    "onboarded_date",
    [COUNT_M()],
    ["party_type", "customer_type_code", "status_code", "segment_code", "kyc_status", "aml_status", "risk_level"],
    {"branch": [("branch_id", "branches", "branch_id")], "rm": [("relationship_manager_id", "employees",
                                                                  "employee_id")]},
    flags=[("c.is_pep", "улс төрд нөлөө бүхий (PEP)", "politically exposed")],
))


@dataclass
class Dim:
    key: str
    via: str
    table: str
    col: str
    mn_label: str                # «харилцагчийн сегмент»
    mn_by: str                   # «харилцагчийн сегментээр»
    mn_each: str                 # «сегмент бүрийн»
    mn_noun: str                 # «салбар» (top-k)
    en: str
    en_pl: str
    enum: bool = False
    ext: str | None = None       # customer → individuals / organizations
    key_col: str | None = None   # бүлэглэхэд нэмэх ID (давхардах нэртэй үед)
    out: str | None = None       # гаралтын баганын нэр

    @property
    def alias(self) -> str:
        return ALIAS[self.table]

    @property
    def out_name(self) -> str:
        return self.out or self.col


EXT = {"individual": ("customer_id", "individuals", "customer_id"),
       "organization": ("customer_id", "organizations", "customer_id")}

DIMS = [
    Dim("branch_name", "branch", "branches", "branch_name", "салбар", "салбараар", "салбар бүрийн", "салбар",
        "branch", "branches"),
    Dim("branch_province", "branch", "branches", "province", "салбарын байршил (аймаг/хот)",
        "салбарын аймаг, хотоор", "аймаг, хот бүрийн (салбарын байршлаар)", "аймаг/хот", "branch province",
        "provinces", out="branch_province"),
    Dim("branch_type", "branch", "branches", "branch_type", "салбарын төрөл", "салбарын төрлөөр",
        "салбарын төрөл бүрийн", "салбарын төрөл", "branch type", "branch types", enum=True),
    Dim("product_name", "product", "products", "product_name", "бүтээгдэхүүн", "бүтээгдэхүүнээр",
        "бүтээгдэхүүн бүрийн", "бүтээгдэхүүн", "product", "products"),
    Dim("product_type", "product", "products", "product_type_code", "бүтээгдэхүүний төрөл",
        "бүтээгдэхүүний төрлөөр", "бүтээгдэхүүний төрөл бүрийн", "бүтээгдэхүүний төрөл", "product type",
        "product types", enum=True),
    Dim("segment", "customer", "customers", "segment_code", "харилцагчийн сегмент", "харилцагчийн сегментээр",
        "сегмент бүрийн", "сегмент", "customer segment", "segments", enum=True),
    Dim("party_type", "customer", "customers", "party_type", "харилцагчийн хэлбэр (хувь хүн/байгууллага)",
        "хувь хүн, байгууллагаар", "хувь хүн, байгууллага тус бүрийн", "харилцагчийн хэлбэр", "party type",
        "party types", enum=True),
    Dim("customer_type", "customer", "customers", "customer_type_code", "харилцагчийн төрөл",
        "харилцагчийн төрлөөр", "харилцагчийн төрөл бүрийн", "харилцагчийн төрөл", "customer type",
        "customer types", enum=True),
    Dim("risk_level", "customer", "customers", "risk_level", "харилцагчийн эрсдэлийн түвшин",
        "харилцагчийн эрсдэлийн түвшнээр", "эрсдэлийн түвшин бүрийн", "эрсдэлийн түвшин", "customer risk level",
        "risk levels", enum=True),
    Dim("customer_province", "customer", "customers", "province", "харилцагчийн аймаг/хот",
        "харилцагчийн аймаг, хотоор", "харилцагчийн аймаг, хот бүрийн", "аймаг/хот", "customer province",
        "provinces", out="customer_province"),
    Dim("gender", "customer", "individuals", "gender", "харилцагчийн хүйс", "харилцагчийн хүйсээр",
        "хүйс тус бүрийн", "хүйс", "customer gender", "genders", enum=True, ext="individual"),
    Dim("employment_type", "customer", "individuals", "employment_type", "харилцагчийн хөдөлмөр эрхлэлтийн төрөл",
        "хөдөлмөр эрхлэлтийн төрлөөр", "хөдөлмөр эрхлэлтийн төрөл бүрийн", "хөдөлмөр эрхлэлтийн төрөл",
        "employment type", "employment types", enum=True, ext="individual"),
    Dim("marital_status", "customer", "individuals", "marital_status", "харилцагчийн гэрлэлтийн байдал",
        "гэрлэлтийн байдлаар", "гэрлэлтийн байдал бүрийн", "гэрлэлтийн байдал", "marital status",
        "marital statuses", enum=True, ext="individual"),
    Dim("legal_form", "customer", "organizations", "legal_form", "байгууллагын хуулийн хэлбэр",
        "байгууллагын хуулийн хэлбэрээр", "хуулийн хэлбэр бүрийн", "хуулийн хэлбэр", "legal form", "legal forms",
        enum=True, ext="organization"),
    Dim("industry_sector", "customer", "organizations", "industry_sector", "байгууллагын үйл ажиллагааны салбар",
        "байгууллагын үйл ажиллагааны салбараар", "үйл ажиллагааны салбар бүрийн", "үйл ажиллагааны салбар",
        "industry sector", "industry sectors", ext="organization"),
    Dim("officer", "officer", "employees", "full_name", "хариуцсан зээлийн мэргэжилтэн",
        "хариуцсан зээлийн мэргэжилтнээр", "зээлийн мэргэжилтэн бүрийн", "зээлийн мэргэжилтэн", "loan officer",
        "loan officers", key_col="employee_id", out="officer_name"),
    Dim("collector", "collector", "employees", "full_name", "хариуцсан ажилтан", "хариуцсан ажилтнаар",
        "хариуцсан ажилтан бүрийн", "ажилтан", "assigned collector", "collectors", key_col="employee_id",
        out="collector_name"),
    Dim("rm", "rm", "employees", "full_name", "хариуцсан ажилтан", "хариуцсан ажилтнаар",
        "хариуцсан ажилтан бүрийн", "ажилтан", "relationship manager", "relationship managers",
        key_col="employee_id", out="manager_name"),
    Dim("loan_class", "loan", "loans", "classification_code", "зээлийн активын ангилал",
        "зээлийн активын ангиллаар", "активын ангилал бүрийн", "активын ангилал", "loan asset classification",
        "classifications", enum=True),
    Dim("loan_amort", "loan", "loans", "amortization_type", "зээлийн эргэн төлөлтийн арга",
        "зээлийн эргэн төлөлтийн аргаар", "эргэн төлөлтийн арга бүрийн", "эргэн төлөлтийн арга",
        "loan amortization type", "amortization types", enum=True),
    Dim("loan_status", "loan", "loans", "status_code", "зээлийн төлөв", "зээлийн төлвөөр", "зээлийн төлөв бүрийн",
        "зээлийн төлөв", "loan status", "loan statuses", enum=True, out="loan_status"),
    Dim("loan_currency", "loan", "loans", "currency_code", "зээлийн валют", "зээлийн валютаар",
        "зээлийн валют бүрийн", "валют", "loan currency", "currencies", out="loan_currency"),
    Dim("case_stage", "case", "collection_cases", "stage_code", "барагдуулалтын шат", "барагдуулалтын шатаар",
        "барагдуулалтын шат бүрийн", "барагдуулалтын шат", "collection stage", "collection stages", enum=True),
    Dim("case_priority", "case", "collection_cases", "priority", "кейсийн ач холбогдол",
        "кейсийн ач холбогдлоор", "ач холбогдол бүрийн", "ач холбогдол", "case priority", "priorities", enum=True),
    Dim("app_channel", "application", "loan_applications", "channel", "хүсэлт ирсэн суваг",
        "хүсэлт ирсэн сувгаар", "суваг бүрийн", "суваг", "application channel", "channels", enum=True),
    Dim("app_purpose", "application", "loan_applications", "purpose_category", "зээлийн зориулалт",
        "зээлийн зориулалтаар", "зориулалт бүрийн", "зориулалт", "loan purpose", "purposes", enum=True),
    Dim("account_type", "account", "accounts", "account_type_code", "дансны төрөл", "дансны төрлөөр",
        "дансны төрөл бүрийн", "дансны төрөл", "account type", "account types", enum=True),
]
DIM_BY_KEY = {d.key: d for d in DIMS}

# өөрийн enum баганын богино монгол/англи нэр (semantic model-ийн тайлбар урт байвал)
LABEL_OVERRIDE = {
    ("loans", "classification_code"): ("активын ангилал", "asset classification"),
    ("loans", "dpd_bucket"): ("хугацаа хэтрэлтийн бүлэг", "DPD bucket"),
    ("loans", "status_code"): ("зээлийн төлөв", "loan status"),
    ("loan_applications", "status_code"): ("хүсэлтийн төлөв", "application status"),
    ("loan_applications", "channel"): ("хүсэлт ирсэн суваг", "application channel"),
    ("transactions", "direction"): ("гүйлгээний чиглэл", "direction"),
    ("transactions", "category"): ("гүйлгээний ангилал", "transaction category"),
    ("collaterals", "status"): ("барьцааны төлөв", "collateral status"),
    ("collaterals", "collateral_type_code"): ("барьцааны төрөл", "collateral type"),
    ("aml_alerts", "rule_code"): ("сэрэмжлүүлгийн дүрэм", "alert rule"),
    ("aml_alerts", "status"): ("сэрэмжлүүлгийн төлөв", "alert status"),
    ("aml_alerts", "severity"): ("ноцтой байдал", "severity"),
    ("gl_entries", "journal_code"): ("журнал", "journal"),
    ("gl_entries", "source_system"): ("эх систем", "source system"),
    ("loan_charges", "charge_type"): ("бичилтийн төрөл (шимтгэл/торгууль)", "charge type"),
    ("loan_charges", "charge_code"): ("шимтгэл, торгуулийн нэр төрөл", "charge code"),
    ("loan_charges", "status"): ("төлбөрийн төлөв", "charge status"),
    ("collection_promises", "status"): ("амлалтын төлөв", "promise status"),
    ("credit_scores", "model_code"): ("скорингийн модель", "scoring model"),
    ("customers", "party_type"): ("харилцагчийн хэлбэр", "party type"),
    ("customers", "segment_code"): ("сегмент", "segment"),
    ("customers", "kyc_status"): ("KYC төлөв", "KYC status"),
    ("customers", "aml_status"): ("AML шалгалтын төлөв", "AML screening status"),
    ("customers", "risk_level"): ("эрсдэлийн түвшин", "risk level"),
    ("customers", "status_code"): ("харилцагчийн төлөв", "customer status"),
    ("customers", "customer_type_code"): ("харилцагчийн төрөл", "customer type"),
    ("deposits", "status"): ("хадгаламжийн төлөв", "deposit status"),
    ("collection_cases", "status"): ("кейсийн төлөв", "case status"),
    ("collection_cases", "priority"): ("кейсийн ач холбогдол", "case priority"),
    ("collection_cases", "stage_code"): ("барагдуулалтын шат", "collection stage"),
    ("accounts", "status"): ("дансны төлөв", "account status"),
}
CURRENCY = {"MNT": ("төгрөгийн", "MNT-denominated"), "USD": ("ам.долларын", "USD-denominated")}
PRODUCT_TYPES = {"LOAN": ["CONSUMER_LOAN", "SALARY_LOAN", "BUSINESS_LOAN", "MORTGAGE", "AUTO_LOAN", "PLEDGE_LOAN",
                          "AGRI_LOAN"],
                 "DEPOSIT": ["TERM_DEPOSIT", "DEMAND_DEPOSIT", "CURRENT_ACC"]}


# ---------------------------------------------------------------------------
# Semantic model
# ---------------------------------------------------------------------------

_MODEL = None


def model():
    global _MODEL
    if _MODEL is None:
        from ..knowledge.semantic import load_semantic_model
        _MODEL = load_semantic_model("knowledge/semantic_model.yaml")
    return _MODEL


def enum_values(table: str, col: str) -> dict[str, str]:
    return model().tables[table].columns[col].values


def col_label(table: str, col: str) -> tuple[str, str]:
    if (table, col) in LABEL_OVERRIDE:
        return LABEL_OVERRIDE[(table, col)]
    c = model().tables[table].columns[col]
    mn = re.split(r"[(:,]", c.mn)[0].strip()
    en = re.sub(r"\s+code$", "", c.en.strip(), flags=re.I)
    return mn[:1].lower() + mn[1:], en[:1].lower() + en[1:]


# кодоос уншигдахгүй англи нэр
VALUE_EN = {"C": "credit (incoming)", "D": "debit (outgoing)", "IND_SALARY": "salaried individual",
            "IND_BUSINESS": "self-employed individual", "IND_PENSION": "pensioner", "ORG_SME": "SME",
            "ORG_CORP": "large corporate", "ORG_NGO": "NGO", "DPD_1_30": "1-30 days", "DPD_31_60": "31-60 days",
            "DPD_61_90": "61-90 days", "DPD_91_180": "91-180 days", "DPD_180_PLUS": "over 180 days",
            "SUB_BRANCH": "settlement center", "PTP": "promise to pay", "AAA": "AAA", "AA": "AA", "A": "A",
            "BBB": "BBB", "BB": "BB", "B": "B", "LLC": "LLC", "JSC": "JSC", "NGO": "NGO", "VIP": "VIP", "SME": "SME"}


def value_en(code: str) -> str:
    return VALUE_EN.get(code) or code.replace("_", " ").lower()


def check_fk_paths() -> list[str]:
    """Каталогийн зам бүрийн алхам semantic model-ийн зарласан FK (аль нэг чиглэлд) байх ёстой."""
    m = model()
    errs = []

    def ok(src_t, src_c, dst_t, dst_c):
        a = m.tables[src_t].columns.get(src_c)
        b = m.tables[dst_t].columns.get(dst_c)
        return (a is not None and a.fk == f"{dst_t}.{dst_c}") or (b is not None and b.fk == f"{src_t}.{src_c}")

    for f in FACTS.values():
        for via, steps in f.fks.items():
            prev = f.table
            for src, tbl, tgt in steps:
                if not ok(prev, src, tbl, tgt):
                    errs.append(f"{f.table}:{via}: {prev}.{src} → {tbl}.{tgt}")
                prev = tbl
    for src, tbl, tgt in EXT.values():
        if not ok("customers", src, tbl, tgt):
            errs.append(f"ext customers.{src} → {tbl}.{tgt}")
    return errs


# ---------------------------------------------------------------------------
# SQL угсрах туслахууд
# ---------------------------------------------------------------------------

@dataclass
class Cond:
    sql: str
    mn: str
    en: str
    adj: bool = False            # нэр үгийн өмнө шууд (валют, тэмдэг)
    needs: tuple = ()            # (зам, ext)
    codes: tuple = ()


class From:
    def __init__(self, fact: Fact):
        self.fact = fact
        self.joins: dict[str, str] = {}
        self.tables = [fact.table]

    def need(self, via: str, ext: str | None = None) -> str:
        steps = list(self.fact.fks[via]) + ([EXT[ext]] if ext else [])
        prev = self.fact.a
        for src, tbl, tgt in steps:
            al = ALIAS[tbl]
            if al not in self.joins:
                self.joins[al] = f"JOIN t2s.{tbl} {al} ON {al}.{tgt} = {prev}.{src}"
                self.tables.append(tbl)
            prev = al
        return prev

    def sql(self) -> str:
        return " ".join([f"t2s.{self.fact.table} {self.fact.a}", *self.joins.values()])

    @property
    def cross(self) -> bool:
        return any(t not in DIM_TABLES for t in self.tables[1:])


def applicable_dims(fact: Fact, enum_only: bool = False, plain_only: bool = False) -> list[Dim]:
    out = []
    for d in DIMS:
        if d.via not in fact.fks or d.table == fact.table:
            continue
        if enum_only and not d.enum:
            continue
        if plain_only and d.enum:
            continue
        out.append(d)
    return out


# толгой нэр үгийн үйлдэх тийн ялгал (эгшиг гээгдэх, н/г нэмэгдэх үгсийг гараар)
_INSTR = {"төлөв": "төлвөөр", "бүлэг": "бүлгээр", "ангилал": "ангиллаар", "арга": "аргаар", "суваг": "сувгаар",
          "зориулалт": "зориулалтаар", "түвшин": "түвшнээр", "хэрэгсэл": "хэрэгслээр", "төрөл": "төрлөөр",
          "чиглэл": "чиглэлээр", "шат": "шатаар", "холбогдол": "холбогдлоор", "байдал": "байдлаар",
          "дүрэм": "дүрмээр", "журнал": "журналаар", "систем": "системээр", "модель": "моделиор",
          "зэрэглэл": "зэрэглэлээр", "зөвлөмж": "зөвлөмжөөр", "сегмент": "сегментээр", "хэлбэр": "хэлбэрээр"}


def mn_instr(phrase: str) -> str:
    """Нэр үгийн хэллэгийн үйлдэх тийн хэлбэр («зээлийн төлөв» → «зээлийн төлвөөр»); хаалтын тайлбарыг хадгална."""
    from ..text.normalize import cyr_suffix
    head, _, paren = phrase.partition(" (")
    *pre, last = head.split(" ")
    word = _INSTR.get(last) or last + cyr_suffix(last, "aar")
    return " ".join([*pre, word]) + (f" ({paren}" if paren else "")


def own_dim(fact: Fact, col: str) -> Dim:
    mn, en = col_label(fact.table, col)
    return Dim(f"own_{col}", "", fact.table, col, mn, mn_instr(mn), f"{mn} тус бүрийн", mn, en,
               en + ("es" if en.endswith("s") else "s"), enum=True)


def dim_sel(d: Dim, own: bool = False) -> tuple[str, str]:
    """(SELECT хэсэг, GROUP BY хэсэг)."""
    col = f"{d.alias}.{d.col}"
    sel = col if d.out_name == d.col else f"{col} AS {d.out_name}"
    grp = f"{d.alias}.{d.key_col}, {col}" if d.key_col else col
    return sel, grp


def agg_sql(m: Measure, rnd: bool = True) -> str:
    if m.agg == "COUNT":
        return "COUNT(*)"
    inner = f"{m.agg}({m.expr})"
    return f"ROUND({inner}, 2)" if rnd else inner


def _with_word(m: Measure, bare: str) -> str:
    word = "нийт" if m.agg == "SUM" else "дундаж"
    return bare if bare.startswith(word + " ") else f"{word} {bare}"


def m_mn(m: Measure, acc: bool = False) -> str:
    if m.agg == "COUNT":
        return m.bare_acc if acc else m.bare
    s = _with_word(m, m.bare_acc if acc else m.bare)
    return s + (" (төгрөгөөр)" if m.money else "")


def m_pair(m: Measure, rng: random.Random) -> tuple[str, str]:
    """(нэрлэх, заах) хэлбэр — «төгрөгөөр»-ийн байрлал хоёуланд ижил."""
    if m.money and m.agg != "COUNT" and rng.random() < 0.5:
        return f"төгрөгөөр тооцсон {_with_word(m, m.bare)}", f"төгрөгөөр тооцсон {_with_word(m, m.bare_acc)}"
    return m_mn(m), m_mn(m, True)


def m_en(m: Measure) -> str:
    if m.agg == "COUNT":
        return "number"
    word = "total" if m.agg == "SUM" else "average"
    base = m.en if m.en.startswith(word + " ") else f"{word} {m.en}"
    return base + (" in MNT" if m.money else "")


def np_mn(f: Fact, per: Period | None, conds: list[Cond], gen: bool = True, verb: bool = False) -> str:
    """verb=True (хугацаагүй үед): үйл үгийг нэр үгийн өмнө, нөхцлүүдийн дараа («..., олгосон зээлүүдийн»)."""
    rel = ([f"{per.mn} {f.verb}"] if per else []) + [c.mn for c in conds if not c.adj]
    if verb and not per:
        rel.append(f.verb)
    adj = [c.mn for c in conds if c.adj]
    noun = f.gen_pl if gen else f.pl
    return " ".join(x for x in [", ".join(rel), *adj, noun] if x)


def np_en(f: Fact, per: Period | None, conds: list[Cond]) -> str:
    adj = [c.en for c in conds if c.adj]
    s = " ".join([*adj, f.en_pl])
    if per:
        s += f" {f.verb_en} {per.en}"
    rel = [c.en for c in conds if not c.adj]
    if rel:
        s += " with " + " and ".join(rel)
    return s


def where_sql(f: Fact, fr: From, per: Period | None, conds: list[Cond], m: Measure | None = None) -> list[str]:
    w = [x for x in f.base]
    if m is not None and m.where:
        w.append(m.where)
    if per:
        w.append(f"{f.a}.{f.date} BETWEEN '{per.start}' AND '{per.end}'")
    for c in conds:
        for via, ext in c.needs:
            fr.need(via, ext)
        w.append(c.sql)
    return w


def wh(w: list[str]) -> str:
    return (" WHERE " + " AND ".join(w)) if w else ""


def enum_cond(rng: random.Random, f: Fact, d: Dim | None = None, col: str | None = None, mode: str = "eq"
              ) -> Cond | None:
    """Өөрийн (col) эсвэл холбогдсон (d) enum баганын нөхцөл — утгын монгол нэрээр."""
    if d is not None:
        table, column, alias = d.table, d.col, d.alias
        mn_l, en_l = d.mn_label, d.en
        needs = ((d.via, d.ext),)
    else:
        table, column, alias = f.table, col, f.a
        mn_l, en_l = col_label(f.table, col)
        needs = ()
    vals = enum_values(table, column)
    codes = list(vals)
    if column == "product_type_code" and f.product_cat:
        codes = [c for c in codes if c in PRODUCT_TYPES[f.product_cat]]
    if not codes:
        return None
    if mode == "in" and len(codes) >= 3:
        a, b = rng.sample(codes, 2)
        return Cond(f"{alias}.{column} IN ('{a}', '{b}')", f"{mn_l} нь «{vals[a]}» эсвэл «{vals[b]}» байгаа",
                    f"{en_l} '{value_en(a)}' or '{value_en(b)}'", needs=needs, codes=(a, b))
    v = rng.choice(codes)
    if mode == "ne":
        return Cond(f"{alias}.{column} <> '{v}'", f"{mn_l} нь «{vals[v]}» биш", f"{en_l} other than '{value_en(v)}'",
                    needs=needs, codes=(v,))
    mn = rng.choice([f"{mn_l} нь «{vals[v]}» байгаа", f"{mn_l} нь «{vals[v]}» гэсэн"])
    return Cond(f"{alias}.{column} = '{v}'", mn, f"{en_l} '{value_en(v)}'", needs=needs, codes=(v,))


def currency_cond(f: Fact, code: str) -> Cond:
    mn, en = CURRENCY[code]
    return Cond(f"{f.cur} = '{code}'", mn, en, adj=True, needs=tuple((n, None) for n in f.cur_needs), codes=(code,))


def flag_cond(rng: random.Random, f: Fact) -> Cond | None:
    if not f.flags:
        return None
    s, mn, en = rng.choice(f.flags)
    return Cond(s, mn, en, adj=True)


def random_conds(rng: random.Random, f: Fact, p_enum: float = 0.35, p_cur: float = 0.12, p_flag: float = 0.1
                 ) -> list[Cond]:
    out = []
    if f.enums and rng.random() < p_enum:
        c = enum_cond(rng, f, col=rng.choice(f.enums))
        if c:
            out.append(c)
    if f.cur and rng.random() < p_cur:
        out.append(currency_cond(f, rng.choice(["MNT", "USD"])))
    if f.flags and rng.random() < p_flag:
        c = flag_cond(rng, f)
        if c:
            out.append(c)
    return out


# ---------------------------------------------------------------------------
# Мөр
# ---------------------------------------------------------------------------

@dataclass
class Cand:
    pattern: str
    sql: str
    mn: str
    en: str
    fact: str
    skeleton: str
    difficulty: str
    cross: bool = False
    order_matters: bool = False
    codes: tuple = ()
    glossary: tuple = ()
    phrasing: str = ""


def _pick(rng, pairs):
    i = rng.randrange(len(pairs))
    return pairs[i][0], pairs[i][1], f"t{i}"


def _measures(f: Fact, additive: bool = False, money: bool = False) -> list[Measure]:
    ms = f.measures
    if additive:
        ms = [m for m in ms if m.agg in ("SUM", "COUNT")]
    if money:
        ms = [m for m in ms if m.money and m.agg == "SUM"]
    return ms


# ---------------------------------------------------------------------------
# Хэв маяг тус бүрийн үүсгүүр
# ---------------------------------------------------------------------------

def gen_join_fk(rng: random.Random) -> Cand | None:
    f = rng.choice(list(FACTS.values()))
    dims = applicable_dims(f)
    if not dims:
        return None
    d = rng.choice(dims)
    m = rng.choice(f.measures)
    per = rng.choice(PERIODS) if rng.random() < 0.6 else None
    conds = random_conds(rng, f)
    # заримдаа холбогдсон хүснэгтийн enum-аар шүүнэ (FK замаар)
    other = [x for x in applicable_dims(f, enum_only=True) if x.key != d.key]
    if other and rng.random() < 0.3:
        c = enum_cond(rng, f, d=rng.choice(other))
        if c:
            conds.append(c)
    fr = From(f)
    for n in m.needs:
        fr.need(n)
    fr.need(d.via, d.ext)
    w = where_sql(f, fr, per, conds, m)
    sel, grp = dim_sel(d)
    val = "row_count" if m.agg == "COUNT" else f"{m.agg.lower()}_{m.key}"
    sql = f"SELECT {sel}, {agg_sql(m)} AS {val} FROM {fr.sql()}{wh(w)} GROUP BY {grp} ORDER BY {val} DESC"
    np_g, e_np, Me = np_mn(f, per, conds), np_en(f, per, conds), m_en(m)
    M, Ma = m_pair(m, rng)
    mn, en, ph = _pick(rng, [
        (f"{d.mn_each} {np_g} {M} хэд вэ?", f"What is the {Me} of {e_np} for each {d.en}?"),
        (f"{np_g} {Ma} {d.mn_by} гарга.", f"Show the {Me} of {e_np} by {d.en}."),
        (f"{d.mn_by} бүлэглээд {np_g} {Ma} харуул.", f"Group {e_np} by {d.en} and show the {Me}."),
        (f"{np_g} {M} {d.mn_by} ямар байна?", f"How does the {Me} of {e_np} break down by {d.en}?"),
        (f"{np_g} {Ma} {d.mn_by} нь бүлэглээд гарга.", f"Group {e_np} by {d.en} and give the {Me}."),
        (f"{d.mn_by} нь задалж {np_g} {Ma} харуулна уу.", f"Please break down the {Me} of {e_np} by {d.en}."),
        (f"{np_g} {M} {d.mn_each} хувьд ямар байна?", f"What does the {Me} of {e_np} look like for each {d.en}?"),
        (f"{np_g} {Ma} {d.mn_by} тооцоод, ихээс нь бага руу эрэмбэлж жагсаа.",
         f"List the {Me} of {e_np} per {d.en}, sorted from highest to lowest."),
        (f"{d.mn_by} нь харвал {np_g} {M} хэд гарч байна?", f"Looking by {d.en}, what is the {Me} of {e_np}?"),
        (f"{np_g} {Ma} {d.mn_by} нь жагсааж өгөөч.", f"Could you list the {Me} of {e_np} for every {d.en}?"),
    ])
    diff = "hard" if len(fr.joins) >= 2 or fr.cross else "medium"
    return Cand("join_fk", sql, mn, en, f.table, f"join|{f.table}|{d.key}|{m.key}", diff, fr.cross,
                codes=sum((c.codes for c in conds), ()), phrasing=ph)


def _grouped_cte_body(f: Fact, d: Dim, m: Measure, per, conds, own: bool = False) -> tuple[str, From]:
    fr = From(f)
    for n in m.needs:
        fr.need(n)
    if not own:
        fr.need(d.via, d.ext)
    w = where_sql(f, fr, per, conds, m)
    sel, grp = dim_sel(d)
    return f"SELECT {sel}, {agg_sql(m, rnd=False)} AS total FROM {fr.sql()}{wh(w)} GROUP BY {grp}", fr


def gen_cte_multi(rng: random.Random) -> Cand | None:
    kind = rng.choice(["above_avg", "share", "two_fact", "growth", "per_customer"])
    f = rng.choice(list(FACTS.values()))
    if kind == "two_fact":
        return _cte_two_fact(rng)
    if kind == "per_customer":
        return _cte_per_customer(rng, f)
    dims = applicable_dims(f)
    if not dims:
        return None
    d = rng.choice(dims)
    m = rng.choice(_measures(f, additive=(kind != "above_avg")) or f.measures)
    o = d.out_name
    if kind == "growth":
        p_prev, p_cur = rng.choice([(PERIODS[0], PERIODS[1]), (Period("2025h1", "2025-01-01", "2025-06-30",
                                                                      "2025 оны эхний хагаст",
                                                                      "in the first half of 2025"), PERIODS[2])])
        conds = random_conds(rng, f, p_enum=0.25)
        body_prev, fr = _grouped_cte_body(f, d, m, p_prev, conds)
        body_cur, _ = _grouped_cte_body(f, d, m, p_cur, conds)
        sql = (f"WITH prev_p AS ({body_prev}), cur_p AS ({body_cur}) "
               f"SELECT cur_p.{o}, ROUND(prev_p.total, 2) AS prev_total, ROUND(cur_p.total, 2) AS cur_total, "
               f"ROUND(100.0 * (cur_p.total - prev_p.total) / NULLIF(prev_p.total, 0), 2) AS growth_pct "
               f"FROM cur_p JOIN prev_p ON prev_p.{o} = cur_p.{o} ORDER BY growth_pct DESC NULLS LAST")
        if d.key_col:   # нэр давхардаж болох тул ID-аар холбоно
            return None
        np_g = np_mn(f, None, conds, verb=True)
        e_np = np_en(f, None, conds)
        Me = m_en(m)
        M, Ma = m_pair(m, rng)
        pv, cu = p_prev.en[3:], p_cur.en[3:]
        mn, en, ph = _pick(rng, [
            (f"{d.mn_each} {np_g} {M} {per_from(p_prev)} {p_cur.mn} хэдэн хувиар өөрчлөгдсөн бэ?",
             f"For each {d.en}, by what percentage did the {Me} of {e_np} change from {pv} to {cu}?"),
            (f"{p_prev.mn} ба {p_cur.mn} {np_g} {M} {d.mn_by} харьцуулж, өсөлтийн хувийг гарга.",
             f"Compare the {Me} of {e_np} {f.verb_en} {p_prev.en} and {p_cur.en} by {d.en} and show the growth (%)."),
            (f"{d.mn_each} {np_g} {M} {per_gen(p_prev)} болон {per_gen(p_cur)} хооронд хэдэн хувиар "
             f"өссөн эсвэл буурсан бэ?",
             f"For each {d.en}, by how many percent did the {Me} of {e_np} grow or shrink between {pv} and {cu}?"),
            (f"{p_prev.mn} болон {p_cur.mn} {np_g} {Ma} {d.mn_by} нь гаргаад, өсөлтийн хувийг тооцоол.",
             f"Show the {Me} of {e_np} {f.verb_en} {p_prev.en} and {p_cur.en} by {d.en}, and calculate the growth "
             f"rate."),
            (f"{d.mn_by} нь харвал {np_g} {M} {per_from(p_prev)} {p_cur.mn} хэдэн хувиар өссөн бэ?",
             f"Looking by {d.en}, by what percent did the {Me} of {e_np} grow {p_cur.en} compared with {pv}?"),
            (f"{d.mn_each} {p_cur.mn} {np_g} {M} {per_com(p_prev)} харьцуулахад хэдэн хувиар "
             f"өөрчлөгдсөн бэ?",
             f"For each {d.en}, what is the percentage change in the {Me} of {e_np} {f.verb_en} {p_cur.en} "
             f"compared with {pv}?"),
            (f"{np_g} {Ma} {d.mn_by} нь {per_gen(p_prev)} ба {per_gen(p_cur)} утгаар зэрэгцүүлж, "
             f"өсөлтийг хувиар харуул.",
             f"Show the {Me} of {e_np} by {d.en} for {pv} and {cu} side by side, with the growth in percent."),
            (f"{d.mn_each} {np_g} {M} {p_prev.mn} болон {p_cur.mn} ямар байсныг харьцуулж, "
             f"өсөлтийн хувиар нь эрэмбэлж харуул.",
             f"Compare the {Me} of {e_np} {p_prev.en} and {p_cur.en} for each {d.en}, sorted by growth (%)."),
        ])
        return Cand("cte_multi", sql, mn, en, f.table, f"cte_growth|{f.table}|{d.key}|{m.key}", "hard", fr.cross,
                    codes=sum((c.codes for c in conds), ()), phrasing="growth_" + ph)
    per = rng.choice(PERIODS) if rng.random() < 0.7 else None
    conds = random_conds(rng, f, p_enum=0.25)
    body, fr = _grouped_cte_body(f, d, m, per, conds)
    np_g, e_np, Me = np_mn(f, per, conds), np_en(f, per, conds), m_en(m)
    M, Ma = m_pair(m, rng)
    if kind == "above_avg":
        sql = (f"WITH per_dim AS ({body}), avg_t AS (SELECT AVG(total) AS avg_total FROM per_dim) "
               f"SELECT pd.{o}, ROUND(pd.total, 2) AS total, ROUND(av.avg_total, 2) AS avg_total "
               f"FROM per_dim pd CROSS JOIN avg_t av WHERE pd.total > av.avg_total ORDER BY pd.total DESC")
        mn, en, ph = _pick(rng, [
            (f"{d.mn_each} {np_g} {Ma} тооцоод, дунджаас өндөр гарсныг нь жагсаа.",
             f"Compute the {Me} of {e_np} for each {d.en}, then list the {d.en_pl} above the average."),
            (f"{np_g} {M} нь дунджаас их байгаа {d.mn_noun} аль вэ? ({d.mn_by} тооцно)",
             f"Which {d.en_pl} have an above-average {Me} of {e_np}?"),
            (f"Эхлээд {d.mn_each} {np_g} {Ma} ол, дараа нь дунджаас өндөр байгааг нь харуул.",
             f"First find the {Me} of {e_np} per {d.en}, then show only those above the overall average."),
            (f"{np_g} {M} {d.mn_by} тооцоход дунджаас дээгүүр гарсан {d.mn_noun} аль нь вэ?",
             f"When computed by {d.en}, which {d.en_pl} are above the average {Me} of {e_np}?"),
            (f"{d.mn_each} {np_g} {Ma} гаргаад, дундаж утгаас өндөр байгааг нь л үлдээ.",
             f"Show the {Me} of {e_np} per {d.en}, keeping only values above the average."),
            (f"{d.mn_by} нь бүлэглэхэд {np_g} {M} дунджаас их гарч буй {d.mn_noun} аль нь вэ? "
             f"Дундажтай нь хамт харуул.",
             f"Grouping by {d.en}, which {d.en_pl} have an above-average {Me} of {e_np}? Show the average too."),
            (f"{np_g} {Ma} {d.mn_by} нь тооцоод, дунджийг давсныг нь жагсаа.",
             f"Compute the {Me} of {e_np} by {d.en} and list those that exceed the average."),
            (f"{d.mn_each} {np_g} {M} дундажтайгаа харьцуулахад өндөр гарсан нь аль вэ?",
             f"For each {d.en}, compare the {Me} of {e_np} with the average: which ones are above it?"),
        ])
    else:
        sql = (f"WITH per_dim AS ({body}), tot AS (SELECT SUM(total) AS grand_total FROM per_dim) "
               f"SELECT pd.{o}, ROUND(pd.total, 2) AS total, "
               f"ROUND(100.0 * pd.total / NULLIF(tt.grand_total, 0), 2) AS share_pct "
               f"FROM per_dim pd CROSS JOIN tot tt ORDER BY share_pct DESC")
        mn, en, ph = _pick(rng, [
            (f"{np_g} {Ma} {d.mn_by} гаргаж, тус бүрийн нийтэд эзлэх хувийг тооцоол.",
             f"For {e_np}, show the {Me} by {d.en} and each {d.en}'s share of the total (%)."),
            (f"{d.mn_each} {np_g} {M} болон нийтэд эзлэх хувь хэд вэ?",
             f"What is the {Me} of {e_np} per {d.en}, and what percentage of the total does each represent?"),
            (f"{d.mn_by} нь бүлэглээд {np_g} {Ma} болон нийт дүнд эзлэх хувийг харуул.",
             f"Group {e_np} by {d.en} and show the {Me} and its share of the total (%)."),
            (f"{np_g} {M} дотор {d.mn_each} эзлэх хувь ямар байна?",
             f"What share of the {Me} of {e_np} does each {d.en} account for?"),
            (f"{d.mn_each} {np_g} {Ma} гаргаж, нийтэд эзлэх хувиар нь эрэмбэлээд жагсаа.",
             f"List the {Me} of {e_np} per {d.en}, ordered by share of the total."),
            (f"{np_g} {M} {d.mn_by} хэрхэн хуваарилагдаж байна? Хувийг нь гарга.",
             f"How is the {Me} of {e_np} distributed across {d.en_pl}? Give the percentages."),
            (f"{d.mn_by} нь задлаад {np_g} {M} болон тус бүрийн хувь хэмжээг харуулна уу.",
             f"Break the {Me} of {e_np} down by {d.en}, with each one's percentage."),
            (f"{np_g} {Ma} {d.mn_each} хувьд тооцоод, нийт дүнгийн хэдэн хувийг эзэлж байгааг харуул.",
             f"Compute the {Me} of {e_np} for each {d.en} and show what percentage of the total it makes up."),
        ])
    diff = "hard"
    return Cand("cte_multi", sql, mn, en, f.table, f"cte_{kind}|{f.table}|{d.key}|{m.key}", diff, fr.cross,
                codes=sum((c.codes for c in conds), ()), phrasing=f"{kind}_{ph}")


def _cte_two_fact(rng: random.Random) -> Cand | None:
    facts = [f for f in FACTS.values() if "branch" in f.fks and f.table != "customers"]
    fa, fb = rng.sample(facts, 2)
    ma = rng.choice(_measures(fa, additive=True))
    mb = rng.choice(_measures(fb, additive=True))
    d = rng.choice([DIM_BY_KEY["branch_name"], DIM_BY_KEY["branch_province"], DIM_BY_KEY["branch_type"]])
    per = rng.choice(YEARLY)
    body_a, _ = _grouped_cte_body(fa, d, ma, per, [])
    body_b, _ = _grouped_cte_body(fb, d, mb, per, [])
    o = d.out_name
    sql = (f"WITH x_side AS ({body_a}), y_side AS ({body_b}) "
           f"SELECT x_side.{o}, ROUND(x_side.total, 2) AS value_a, ROUND(y_side.total, 2) AS value_b, "
           f"ROUND(x_side.total / NULLIF(y_side.total, 0), 4) AS ratio "
           f"FROM x_side JOIN y_side ON y_side.{o} = x_side.{o} ORDER BY ratio DESC NULLS LAST")
    a_mn = f"{per.mn} {fa.verb} {fa.gen_pl} {m_mn(ma)}"
    a_acc = f"{per.mn} {fa.verb} {fa.gen_pl} {m_mn(ma, True)}"
    b_mn = f"тухайн үед {fb.verb} {fb.gen_pl} {m_mn(mb)}"
    b_acc = f"тухайн үед {fb.verb} {fb.gen_pl} {m_mn(mb, True)}"
    a_en = f"the {m_en(ma)} of {fa.en_pl} {fa.verb_en} {per.en}"
    b_en = f"the {m_en(mb)} of {fb.en_pl} {fb.verb_en} in the same period"
    mn, en, ph = _pick(rng, [
        (f"{d.mn_each} {a_acc} болон {b_acc} гаргаж, хоорондын харьцааг тооцоол.",
         f"For each {d.en}, show {a_en} and {b_en}, and their ratio."),
        (f"{d.mn_by} {a_mn} ба {b_mn} хоёрын харьцаа хэд вэ?",
         f"By {d.en}, what is the ratio of {a_en} to {b_en}?"),
        (f"{d.mn_by} нь {a_acc} болон {b_acc} зэрэгцүүлэн гаргаж, харьцааг нь тооцоол.",
         f"By {d.en}, show {a_en} next to {b_en} and compute the ratio."),
        (f"{d.mn_each} хувьд {a_mn} ба {b_mn} хоорондын харьцаа ямар байна?",
         f"For each {d.en}, what is the ratio between {a_en} and {b_en}?"),
        (f"{a_acc} ба {b_acc} {d.mn_by} нь гаргаад, эхнийхийг нь хоёрдахид нь хуваасан харьцааг нэм.",
         f"Show {a_en} and {b_en} by {d.en}, and add the first divided by the second."),
        (f"{d.mn_by} харьцуулбал {a_mn}, {b_mn} хоёрын харьцаа хэд гарах вэ?",
         f"Comparing by {d.en}, what is the ratio of {a_en} to {b_en}?"),
        (f"{d.mn_each} {a_acc}, {b_acc} хоёуланг нь харуулаад харьцааг нь нэмж гарга.",
         f"For every {d.en}, show both {a_en} and {b_en}, plus their ratio."),
        (f"Харьцаагаар нь эрэмбэлээд {d.mn_each} {a_acc} болон {b_acc} жагсаа.",
         f"List {a_en} and {b_en} for each {d.en}, sorted by their ratio."),
    ])
    return Cand("cte_multi", sql, mn, en, fa.table, f"cte_two|{fa.table}|{fb.table}|{d.key}|{ma.key}|{mb.key}",
                "hard", True, phrasing="two_" + ph)


def _cte_per_customer(rng: random.Random, f: Fact) -> Cand | None:
    if "customer" not in f.fks or len(f.fks["customer"]) != 1 or f.table == "customers":
        return None
    d = rng.choice([x for x in DIMS if x.via == "customer" and x.ext is None])
    per = rng.choice(PERIODS) if rng.random() < 0.6 else None
    conds = random_conds(rng, f, p_enum=0.25, p_flag=0.0)
    if any(c.needs for c in conds):
        return None
    fr = From(f)
    w = where_sql(f, fr, per, conds)
    src = f.fks["customer"][0][0]
    o = d.out_name
    sel = f"c.{d.col}" + ("" if o == d.col else f" AS {o}")
    sql = (f"WITH per_cust AS (SELECT {f.a}.{src} AS customer_id, COUNT(*) AS n FROM {fr.sql()}{wh(w)} "
           f"GROUP BY {f.a}.{src}) SELECT {sel}, COUNT(*) AS customers, ROUND(AVG(pc.n), 2) AS avg_per_customer "
           f"FROM per_cust pc JOIN t2s.customers c ON c.customer_id = pc.customer_id GROUP BY c.{d.col} "
           f"ORDER BY avg_per_customer DESC")
    np_g, e_np = np_mn(f, per, conds), np_en(f, per, conds)
    mn, en, ph = _pick(rng, [
        (f"Нэг харилцагчид ногдох {np_g} дундаж тоог {d.mn_by} гарга.",
         f"Show the average number of {e_np} per customer, by {d.en}."),
        (f"Харилцагч бүрийн {np_g} тоог эхлээд тооцоод, дараа нь {d.mn_by} дундажла.",
         f"First count {e_np} for each customer, then average those counts by {d.en}."),
        (f"{d.mn_each} хувьд нэг харилцагчид дунджаар ногдох {np_g} тоо хэд вэ?",
         f"For each {d.en}, what is the average number of {e_np} per customer?"),
        (f"Харилцагч тус бүрээр {np_g} тоог тоолоод, {d.mn_by} нь дунджийг гарга.",
         f"Count {e_np} per customer, then give the average by {d.en}."),
        (f"{d.mn_by} бүлэглэн нэг харилцагчид ногдох {np_g} дундаж тоог харуул.",
         f"Grouped by {d.en}, show the average number of {e_np} per customer."),
        (f"{d.mn_each} харилцагчийн тоо болон нэг харилцагчид ногдох {np_g} дундаж тоог гарга.",
         f"For each {d.en}, show the number of customers and the average number of {e_np} per customer."),
        (f"Харилцагч бүрт ногдох {np_g} тоо {d.mn_by} дунджаар ямар байна?",
         f"On average, how many {e_np} does a customer have, by {d.en}?"),
        (f"{np_g} тоог харилцагчаар нь тоолж, {d.mn_by} дундажлаад харуул.",
         f"Count {e_np} by customer and show the averages by {d.en}."),
    ])
    return Cand("cte_multi", sql, mn, en, f.table, f"cte_pc|{f.table}|{d.key}", "hard", False,
                codes=sum((c.codes for c in conds), ()), phrasing="pc_" + ph)


def gen_ratio_nullif(rng: random.Random) -> Cand | None:
    f = rng.choice(list(FACTS.values()))
    per = rng.choice(PERIODS) if rng.random() < 0.6 else None
    grouped = rng.random() < 0.5
    d = None
    if grouped:
        dims = applicable_dims(f) + [own_dim(f, c) for c in f.enums]
        d = rng.choice(dims) if dims else None
    if f.ratios and rng.random() < 0.6:
        num, den, r_mn, r_acc, r_en, gl, needs = rng.choice(f.ratios)
        conds = random_conds(rng, f, p_enum=0.2, p_cur=0.1, p_flag=0.0)
        if gl in ("npl_ratio", "par30", "par90"):
            conds = [c for c in conds if "is_active_portfolio" not in c.sql]
            base = Cond("l.is_active_portfolio", "идэвхтэй багцын", "active-portfolio", adj=True)
            conds = [base] + conds
        expr = f"ROUND(100.0 * {num} / NULLIF({den}, 0), 2)"
        glossary = (gl,) if gl else ()
        skel = f"ratio_sum|{f.table}|{r_en}"
    else:
        if not f.enums:
            return None
        col = rng.choice(f.enums)
        vals = enum_values(f.table, col)
        v = rng.choice(list(vals))
        mn_l, en_l = col_label(f.table, col)
        expr = f"ROUND(100.0 * COUNT(*) FILTER (WHERE {f.a}.{col} = '{v}') / NULLIF(COUNT(*), 0), 2)"
        r_mn = f"{mn_l} нь «{vals[v]}» байгаа хувь"
        r_acc = f"{mn_l} нь «{vals[v]}» байгаа хувийг"
        r_en = f"percentage with {en_l} '{value_en(v)}'"
        conds = [c for c in random_conds(rng, f, p_enum=0.0) if c]
        needs, glossary = (), ()
        skel = f"ratio_share|{f.table}|{col}"
        if d is not None and d.table == f.table and d.col == col:
            d = None
    fr = From(f)
    for n in needs:
        fr.need(n)
    w = where_sql(f, fr, per, conds)
    np_g, e_np = np_mn(f, per, conds), np_en(f, per, conds)
    if d is None:
        sql = f"SELECT {expr} AS pct FROM {fr.sql()}{wh(w)}"
        mn, en, ph = _pick(rng, [
            (f"{np_g} {r_mn} хэд вэ?", f"What is the {r_en} of {e_np}?"),
            (f"{np_g} дотор {r_mn} хэд байна?", f"Among {e_np}, what is the {r_en}?"),
            (f"{np_g} {r_acc} тооцоол.", f"Calculate the {r_en} for {e_np}."),
            (f"{np_g} {r_acc} гарга.", f"Show the {r_en} for {e_np}."),
            (f"{np_g} хувьд {r_mn} ямар байна?", f"What does the {r_en} look like for {e_np}?"),
            (f"{r_acc} {np_g} хувьд тооцоод харуул.", f"Compute and show the {r_en} for {e_np}."),
            (f"{np_g} {r_mn} хэдтэй байна?", f"Where does the {r_en} of {e_np} stand?"),
            (f"{np_g} {r_acc} олж өгөөч.", f"Can you find the {r_en} for {e_np}?"),
            (f"{np_g} {r_mn} хэд болж байгааг харуул.", f"Show what the {r_en} of {e_np} comes to."),
        ])
        ph = "flat_" + ph
    else:
        own = d.key.startswith("own_")
        if not own:
            fr.need(d.via, d.ext)
        sel, grp = dim_sel(d)
        sql = f"SELECT {sel}, {expr} AS pct FROM {fr.sql()}{wh(w)} GROUP BY {grp} ORDER BY pct DESC NULLS LAST"
        mn, en, ph = _pick(rng, [
            (f"{d.mn_each} {np_g} {r_mn} хэд вэ?", f"For each {d.en}, what is the {r_en} of {e_np}?"),
            (f"{np_g} {r_acc} {d.mn_by} гарга.", f"Show the {r_en} of {e_np} by {d.en}."),
            (f"{d.mn_by} нь бүлэглээд {np_g} {r_acc} тооцоол.",
             f"Group by {d.en} and calculate the {r_en} of {e_np}."),
            (f"{np_g} {r_mn} {d.mn_each} хувьд ямар байна?", f"How does the {r_en} of {e_np} vary across {d.en_pl}?"),
            (f"{d.mn_each} {r_acc} {np_g} хувьд гаргаж, өндрөөс нь бага руу эрэмбэл.",
             f"Show the {r_en} of {e_np} for each {d.en}, sorted from highest to lowest."),
            (f"{np_g} {r_acc} {d.mn_by} задлаад харуул.", f"Break down the {r_en} of {e_np} by {d.en}."),
            (f"{d.mn_by} харвал {np_g} {r_mn} хэд байна?", f"Looking by {d.en}, what is the {r_en} of {e_np}?"),
            (f"{d.mn_each} хувьд {np_g} {r_acc} жагсаа.", f"List the {r_en} of {e_np} for every {d.en}."),
        ])
        ph = "grp_" + ph
        skel += f"|{d.key}"
    diff = "hard" if (d is not None and fr.joins) or fr.cross else "medium"
    return Cand("ratio_nullif", sql, mn, en, f.table, skel, diff, fr.cross,
                codes=sum((c.codes for c in conds), ()), glossary=glossary, phrasing=ph)


def gen_lag_pop(rng: random.Random) -> Cand | None:
    if rng.random() < 0.15:
        return _lag_special(rng)
    f = rng.choice([x for x in FACTS.values()])
    grain, rng_p = rng.choice(SERIES_RANGES)
    m = rng.choice(f.measures)
    conds = random_conds(rng, f, p_enum=0.3, p_cur=0.15, p_flag=0.05)
    fr = From(f)
    for n in m.needs:
        fr.need(n)
    w = where_sql(f, fr, rng_p, conds, m)
    gr = GRAIN[grain]
    period = gr.sql.format(d=f"{f.a}.{f.date}")
    part = None
    if rng.random() < 0.25:
        cands = [own_dim(f, c) for c in f.enums if not any(f"{f.a}.{c} " in x.sql for x in conds)]
        part = rng.choice(cands) if cands else None
    lag = "LAG(v) OVER (" + (f"PARTITION BY {part.col} " if part else "") + "ORDER BY period)"
    if part:
        inner = (f"SELECT {f.a}.{part.col}, {period} AS period, {agg_sql(m, rnd=False)} AS v FROM {fr.sql()}{wh(w)} "
                 f"GROUP BY {f.a}.{part.col}, 2")
        head = f"{part.col}, "
        order = f"{part.col}, period"
    else:
        inner = f"SELECT {period} AS period, {agg_sql(m, rnd=False)} AS v FROM {fr.sql()}{wh(w)} GROUP BY 1"
        head, order = "", "period"
    variant = rng.choice(["pct", "diff", "both"])
    cols = [f"{head}period", "ROUND(v, 2) AS value", f"ROUND({lag}, 2) AS prev_value"]
    if variant in ("diff", "both"):
        cols.append(f"ROUND(v - {lag}, 2) AS change")
    if variant in ("pct", "both"):
        cols.append(f"ROUND(100.0 * (v - {lag}) / NULLIF({lag}, 0), 2) AS change_pct")
    sql = f"WITH s AS ({inner}) SELECT {', '.join(cols)} FROM s ORDER BY {order}"
    e_np = np_en(f, None, conds)
    Me = m_en(m)
    M, Ma = m_pair(m, rng)
    w_acc, w_gen, w_en = LAG_WHAT[variant]
    p_mn = f"{part.mn_each} " if part else ""
    p_en = f" for each {part.en}" if part else ""
    head_mn = f"{p_mn}{np_mn(f, rng_p, conds)}"
    mn, en, ph = _pick(rng, [
        (f"{head_mn} {M} {gr.by}, {gr.prev_com} харьцуулсан {w_acc} гарга.",
         f"Show the {gr.en} {Me} of {e_np}{p_en} {rng_p.en}, with {w_en} versus the {gr.prev_en}."),
        (f"{head_mn} {M} {gr.by} {gr.prev_abl} хэрхэн өөрчлөгдсөн бэ?",
         f"How did the {gr.en} {Me} of {e_np}{p_en} change versus the {gr.prev_en} {rng_p.en}?"),
        (f"{head_mn} {Ma} {gr.by}, {gr.prev_gen} утга болон {w_gen} хамт харуул.",
         f"List the {gr.en} {Me} of {e_np}{p_en} {rng_p.en} together with the {gr.prev_en}'s value and "
         f"{w_en} ({gr.pop})."),
        (f"{head_mn} {gr.each} {M} {gr.prev_com} харьцуулахад хэр өөрчлөгдсөн бэ?",
         f"{rng_p.en}, how did the {gr.en} {Me} of {e_np}{p_en} change compared with the {gr.prev_en}?"),
        (f"{head_mn} {Ma} {gr.by_n} гаргаад, {gr.prev_com} харьцуулсан {w_acc} нэмж харуул.",
         f"Show the {Me} of {e_np}{p_en} {rng_p.en} by {gr.unit_en}, adding {w_en} versus the {gr.prev_en}."),
        (f"{head_mn} {gr.each} {M}, {gr.prev_gen} утга болон {w_acc} гарга.",
         f"Give the {gr.en} {Me} of {e_np}{p_en} {rng_p.en}, the {gr.prev_en}'s value and {w_en}."),
        (f"{head_mn} {Ma} {gr.by} {gr.prev_com} харьцуулж, {w_acc} тооцоод жагсаа.",
         f"List the {gr.en} {Me} of {e_np}{p_en} {rng_p.en} and compute {w_en} relative to the {gr.prev_en}."),
        (f"{head_mn} {M} {gr.by} яаж өөрчлөгдсөнийг {gr.prev_com} харьцуулж харуул.",
         f"Show how the {gr.en} {Me} of {e_np}{p_en} moved {rng_p.en}, versus the {gr.prev_en}."),
        (f"{gr.prev_com} харьцуулсан {w_acc} оруулаад {head_mn} {Ma} {gr.by} жагсаа.",
         f"Including {w_en} versus the {gr.prev_en}, list the {gr.en} {Me} of {e_np}{p_en} {rng_p.en}."),
    ])
    return Cand("lag_pop", sql, mn, en, f.table, f"lag|{f.table}|{grain}|{m.key}|{bool(part)}", "hard", fr.cross,
                order_matters=True, codes=sum((c.codes for c in conds), ()), phrasing=f"main_{ph}")


def _lag_special(rng: random.Random) -> Cand | None:
    """Сарын эцсийн агшин (loan_dpd_history + loans-ийн валют; account_balances_monthly)."""
    per = rng.choice([SERIES_RANGES[0][1], SERIES_RANGES[1][1], SERIES_RANGES[3][1]])
    kind = rng.choice(["par", "overdue", "bucket", "balance"])
    if kind == "balance":
        at = rng.choice([None, "CURRENT", "SAVINGS"])
        join = " JOIN t2s.accounts ac ON ac.account_id = abm.account_id" if at else ""
        w = f" WHERE abm.balance_date BETWEEN '{per.start}' AND '{per.end}'" + \
            (f" AND ac.account_type_code = '{at}'" if at else "")
        at_mn = {None: "", "CURRENT": "харилцах дансны ", "SAVINGS": "хадгаламжийн дансны "}[at]
        at_en = {None: "", "CURRENT": "current-account ", "SAVINGS": "savings-account "}[at]
        sql = (f"WITH s AS (SELECT abm.balance_date AS period, SUM(abm.closing_balance_mnt) AS v "
               f"FROM t2s.account_balances_monthly abm{join}{w} GROUP BY 1) SELECT period, ROUND(v, 2) AS value, "
               f"ROUND(v - LAG(v) OVER (ORDER BY period), 2) AS change, ROUND(100.0 * (v - LAG(v) OVER (ORDER BY "
               f"period)) / NULLIF(LAG(v) OVER (ORDER BY period), 0), 2) AS change_pct FROM s ORDER BY period")
        mn, en, ph = _pick(rng, [
            (f"{per.mn} сар бүрийн эцсийн {at_mn}нийт үлдэгдэл (төгрөгөөр) өмнөх сараасаа хэдээр, хэдэн хувиар "
             f"өөрчлөгдсөн бэ?",
             f"How much and by what percentage did the total month-end {at_en}balance in MNT change from the "
             f"previous month {per.en}?"),
            (f"{per.mn} {at_mn}сарын эцсийн нийт үлдэгдэл (төгрөгөөр) сараас сард хэрхэн өөрчлөгдсөн бэ? Дүн болон "
             f"хувиар нь харуул.",
             f"How did the total month-end {at_en}balance in MNT change month over month {per.en}? Show it in "
             f"amount and percent."),
            (f"Сар бүрийн эцсийн {at_mn}нийт үлдэгдлийг {per.mn} гаргаад, өмнөх сартай харьцуулсан өөрчлөлтийн "
             f"хэмжээ ба хувийг харуул.",
             f"Show the month-end total {at_en}balance in MNT {per.en}, with the change and percentage change "
             f"versus the previous month."),
            (f"{per.mn} {at_mn}сарын эцсийн үлдэгдлийн нийлбэр (төгрөгөөр) өмнөх сараасаа хэдээр, хэдэн хувиар "
             f"өссөн буюу буурсан бэ?",
             f"{per.en}, by how much and by what percent did the summed month-end {at_en}balance in MNT rise or "
             f"fall versus the previous month?"),
            (f"{per.mn} сар бүрийн эцэст {at_mn}нийт үлдэгдэл хэд байсан, өмнөх сараасаа хэр зөрүүтэй вэ? "
             f"(төгрөгөөр, хувиар)",
             f"{per.en}, what was the total {at_en}balance at each month end and how much did it differ from the "
             f"previous month (MNT and %)?"),
            (f"{at_mn}сарын эцсийн нийт үлдэгдлийн сар хоорондын өөрчлөлтийг {per.mn} дүн, хувиар гарга.",
             f"Give the month-to-month change in total month-end {at_en}balance {per.en}, as amount and percent."),
            (f"{per.mn} {at_mn}сарын эцсийн нийт үлдэгдлийг сараар нь жагсааж, өмнөх сартай харьцуулсан "
             f"өөрчлөлтийг дүн, хувиар нэмж харуул.",
             f"List the total month-end {at_en}balance by month {per.en}, adding the change (amount and %) versus "
             f"the previous month."),
            (f"Сараар нь {per.mn} {at_mn}сарын эцсийн нийт үлдэгдэл ба түүний өмнөх сараас өөрчлөгдсөн хэмжээ, "
             f"хувийг харуулна уу.",
             f"By month, show the total month-end {at_en}balance {per.en} and its change and % change from the "
             f"previous month."),
        ])
        return Cand("lag_pop", sql, mn, en, "account_balances_monthly", f"lag_sp|balance|{at}", "hard", bool(at),
                    order_matters=True, codes=(at,) if at else (), phrasing="spbal_" + ph)
    fx = "t2s.fx_to_mnt(l.currency_code, h.snapshot_date)"
    base = (f"FROM t2s.loan_dpd_history h JOIN t2s.loans l ON l.loan_id = h.loan_id "
            f"WHERE h.snapshot_date BETWEEN '{per.start}' AND '{per.end}'")
    cur = rng.choice([None, "MNT", "USD"])
    if cur:
        base += f" AND l.currency_code = '{cur}'"
    c_mn = f"{CURRENCY[cur][0]} зээлүүдийн " if cur else ""
    c_en = f" for {CURRENCY[cur][1]} loans" if cur else ""
    if kind == "par":
        v = (f"100.0 * SUM(h.total_outstanding * {fx}) FILTER (WHERE h.days_past_due > 30) / "
             f"NULLIF(SUM(h.total_outstanding * {fx}), 0)")
        mn_v, mn_acc = f"{c_mn}сарын эцсийн PAR30 хувь", f"{c_mn}сарын эцсийн PAR30 хувийг"
        en_v, gl = f"month-end PAR30 (%) in MNT{c_en}", ("par30",)
    elif kind == "overdue":
        v = f"SUM(h.overdue_principal * {fx})"
        mn_v = f"{c_mn}сарын эцсийн хугацаа хэтэрсэн үндсэн төлбөр (төгрөгөөр)"
        mn_acc = f"{c_mn}сарын эцсийн хугацаа хэтэрсэн үндсэн төлбөрийг (төгрөгөөр)"
        en_v, gl = f"month-end overdue principal in MNT{c_en}", ()
    else:
        b = rng.choice(["DPD_1_30", "DPD_31_60", "DPD_61_90", "DPD_91_180", "DPD_180_PLUS"])
        bn = enum_values("loan_dpd_history", "dpd_bucket")[b]
        v = f"COUNT(*) FILTER (WHERE h.dpd_bucket = '{b}')"
        mn_v = f"сарын эцэст «{bn}» хоногийн бүлэгт байсан {c_mn or 'зээлүүдийн '}тоо"
        mn_acc = mn_v + "г"
        en_v, gl = f"number of loans in DPD bucket '{value_en(b)}' at month end{c_en}", ("dpd_bucket",)
    sql = (f"WITH s AS (SELECT h.snapshot_date AS period, {v} AS v {base} GROUP BY 1) SELECT period, ROUND(v, 2) AS "
           f"value, ROUND(LAG(v) OVER (ORDER BY period), 2) AS prev_value, ROUND(v - LAG(v) OVER (ORDER BY period), "
           f"2) AS change FROM s ORDER BY period")
    mn, en, ph = _pick(rng, [
        (f"{per.mn} {mn_acc} өмнөх сарынхтай нь харьцуулж гарга.",
         f"Show the {en_v} {per.en}, compared with the previous month."),
        (f"{per.mn} {mn_v} сараас сард хэрхэн өөрчлөгдсөн бэ?",
         f"How did the {en_v} change month over month {per.en}?"),
        (f"{per.mn} {mn_acc} сар бүрээр гаргаад, өмнөх сарынхаас хэдээр өөрчлөгдсөнийг харуул.",
         f"Show the {en_v} by month {per.en} and how much it changed from the previous month."),
        (f"Сар бүрийн хувьд {per.mn} {mn_v} өмнөх сартайгаа харьцуулахад ямар өөрчлөлттэй байна?",
         f"For each month {per.en}, how does the {en_v} compare with the previous month?"),
        (f"{per.mn} {mn_v} болон өмнөх сарын утгыг зэрэгцүүлээд зөрүүг нь гарга.",
         f"Show the {en_v} {per.en} next to the previous month's value and the difference."),
        (f"{per.mn} {mn_v} сараас сард хэдээр нэмэгдэж, буурсан бэ?",
         f"By how much did the {en_v} rise or fall month to month {per.en}?"),
        (f"{per.mn} {mn_acc} сараар нь жагсааж, өмнөх сарын утга, өөрчлөлтийг хамт харуул.",
         f"List the {en_v} by month {per.en}, along with the previous month's value and the change."),
        (f"Өмнөх сартай харьцуулсан өөрчлөлтийн хамт {per.mn} {mn_acc} сар бүрээр харуул.",
         f"Together with the change versus the previous month, show the {en_v} for each month {per.en}."),
    ])
    return Cand("lag_pop", sql, mn, en, "loan_dpd_history", f"lag_sp|{kind}|{cur}", "hard", True,
                order_matters=True, codes=(cur,) if cur else (), glossary=gl, phrasing="sp_" + ph)


def gen_multi_currency(rng: random.Random) -> Cand | None:
    kind = rng.choice(["by_cur", "one_cur", "foreign_share", "to_usd", "fx_rate"])
    if kind == "fx_rate":
        cur = rng.choice(["USD", "EUR", "CNY", "KRW", "JPY"])
        per = rng.choice(PERIODS)
        agg, a_mn, a_en = rng.choice([("AVG(xr.mid_rate)", "дундаж албан ханш", "average official rate"),
                                      ("MAX(xr.mid_rate)", "хамгийн өндөр албан ханш", "highest official rate"),
                                      ("MIN(xr.mid_rate)", "хамгийн бага албан ханш", "lowest official rate")])
        sql = (f"SELECT date_trunc('month', xr.rate_date)::date AS month, ROUND({agg}, 2) AS rate FROM "
               f"t2s.exchange_rates xr WHERE xr.currency_code = '{cur}' AND xr.rate_date BETWEEN '{per.start}' AND "
               f"'{per.end}' GROUP BY 1 ORDER BY 1")
        mn, en, ph = _pick(rng, [
            (f"{per.mn} {CUR_GEN[cur]} сар бүрийн {a_mn} хэд байсан бэ?",
             f"What was the monthly {a_en} of the {cur} {per.en}?"),
            (f"{CUR_GEN[cur].capitalize()} {a_mn}ийг {per.mn} сараар гарга.",
             f"Show the {a_en} of the {cur} by month {per.en}."),
            (f"{per.mn} {CUR_GEN[cur]} {a_mn} сар бүрээр ямар байсан бэ?",
             f"What was the {a_en} of the {cur} by month {per.en}?"),
            (f"{per.mn} {CUR_GEN[cur]} {a_mn}ийг сараар нь жагсаа.",
             f"List the {a_en} of the {cur} month by month {per.en}."),
            (f"Сар бүрийн {CUR_GEN[cur]} {a_mn} {per.mn} хэд байв?",
             f"{per.en}, what was the monthly {a_en} for the {cur}?"),
            (f"{per.mn} {CUR_GEN[cur]} төгрөгтэй харьцах {a_mn}ийг сар бүрээр харуул.",
             f"Show the {a_en} of the {cur} against MNT for each month {per.en}."),
            (f"{per.mn} сараар нь {CUR_GEN[cur]} {a_mn} хэд байсныг гарга.",
             f"Give the {a_en} of the {cur} for each month {per.en}."),
            (f"{CUR_GEN[cur]} {a_mn} {per.mn} сар бүр хэд байсныг харуулна уу.",
             f"Please show what the {a_en} of the {cur} was in each month {per.en}."),
        ])
        return Cand("multi_currency", sql, mn, en, "exchange_rates", f"mc_rate|{cur}|{agg[:3]}", "medium",
                    codes=(cur,), glossary=("fx_rate",), phrasing="rate_" + ph)
    f = rng.choice([x for x in FACTS.values() if x.cur and _measures(x, money=True)])
    m = rng.choice(_measures(f, money=True))
    per = rng.choice(PERIODS) if rng.random() < 0.65 else None
    conds = random_conds(rng, f, p_enum=0.25, p_cur=0.0, p_flag=0.05)
    fr = From(f)
    for n in m.needs + f.cur_needs:
        fr.need(n)
    d = None
    if kind == "one_cur":
        conds.append(currency_cond(f, "USD" if rng.random() < 0.7 else "MNT"))
    w = where_sql(f, fr, per, conds, m)
    np_g, e_np = np_mn(f, per, conds), np_en(f, per, conds)
    bare, bare_acc = m.bare, m.bare_acc
    T, Ta = _with_word(m, bare), _with_word(m, bare_acc)   # «нийт ...» (давхар «нийт»-гүй)
    Te = m.en if m.en.startswith("total ") else f"total {m.en}"
    if kind == "by_cur":
        sql = (f"SELECT {f.cur} AS currency_code, COUNT(*) AS row_count, ROUND(SUM({m.orig}), 2) AS amount_original, "
               f"ROUND(SUM({m.expr}), 2) AS amount_mnt FROM {fr.sql()}{wh(w)} GROUP BY {f.cur} "
               f"ORDER BY amount_mnt DESC")
        mn, en, ph = _pick(rng, [
            (f"{np_g} {Ta} валют тус бүрээр анхны валютаар болон төгрөгөөр гарга.",
             f"Show the {Te} of {e_np} per currency, both in the original currency and in MNT."),
            (f"Валют бүрээр {np_g} тоо, {T} (тухайн валютаар) ба төгрөгт шилжүүлсэн дүн хэд вэ?",
             f"For each currency, what are the count, the {Te} in that currency and its MNT equivalent "
             f"for {e_np}?"),
            (f"Валют бүрээр {np_g} {Ta} анхны валютаар нь болон төгрөгт хөрвүүлж харуул.",
             f"Show the {Te} of {e_np} for each currency, in the original currency and converted to MNT."),
            (f"{np_g} {T} валютаар нь задалбал хэд вэ? Анхны дүн болон төгрөгт шилжүүлсэн дүнг гарга.",
             f"Broken down by currency, what is the {Te} of {e_np}? Give the original and MNT amounts."),
            (f"{np_g} тоо, {Ta} валют тус бүрээр (анхны валют болон төгрөгөөр) гарга.",
             f"Give the count and {Te} of {e_np} per currency (original currency and MNT)."),
            (f"{np_g} {bare_acc} валютаар нь бүлэглээд, анхны валютаар болон төгрөгт шилжүүлсэн нийлбэрийг харуул.",
             f"Group {e_np} by currency and show the {Te} both in the original currency and in MNT."),
            (f"Валют тус бүрийн хувьд {np_g} {T} анхны валютаараа болон төгрөгөөр хэд байна?",
             f"For each currency, what is the {Te} of {e_np} in its own currency and in MNT?"),
            (f"{np_g} {Ta} валютаар нь жагсааж, төгрөгт хөрвүүлсэн дүнгийн хамт харуул.",
             f"List the {Te} of {e_np} by currency, together with the MNT-converted amount."),
        ])
    elif kind == "one_cur":
        sql = f"SELECT ROUND(SUM({m.expr}), 2) AS amount_mnt FROM {fr.sql()}{wh(w)}"
        mn, en, ph = _pick(rng, [
            (f"{np_g} {T} төгрөгөөр хэд вэ?", f"What is the {Te} of {e_np} in MNT?"),
            (f"{np_g} {Ta} төгрөгт хөрвүүлж гарга.", f"Convert the {Te} of {e_np} to MNT."),
            (f"{np_g} {T} төгрөгт хөрвүүлбэл хэд болох вэ?",
             f"How much is the {Te} of {e_np} when converted to MNT?"),
            (f"{np_g} {bare_acc} төгрөгөөр нийлбэрлээд гарга.", f"Sum the {m.en} of {e_np} in MNT."),
            (f"Төгрөгт шилжүүлсэн дүнгээр {np_g} {T} хэд вэ?",
             f"In MNT terms, what is the {Te} of {e_np}?"),
            (f"{np_g} {Ta} төгрөгөөр тооцож харуул.", f"Show the {Te} of {e_np}, calculated in MNT."),
            (f"{np_g} {T} төгрөгөөр хэд байна?", f"What does the {Te} of {e_np} come to in MNT?"),
            (f"Ханшаар нь төгрөгт хөрвүүлээд {np_g} {Ta} гарга.",
             f"Convert at the applicable rate and give the {Te} of {e_np} in MNT."),
        ])
    elif kind == "foreign_share":
        if rng.random() < 0.4:
            dims = [x for x in applicable_dims(f) if not x.key_col]
            d = rng.choice(dims) if dims else None
        expr = (f"ROUND(100.0 * SUM({m.expr}) FILTER (WHERE {f.cur} <> 'MNT') / NULLIF(SUM({m.expr}), 0), 2)")
        if d is None:
            sql = f"SELECT {expr} AS foreign_share_pct FROM {fr.sql()}{wh(w)}"
            mn, en, ph = _pick(rng, [
                (f"{np_g} төгрөгт хөрвүүлсэн {T} дотор гадаад валютынх хэдэн хувь вэ?",
                 f"What percentage of the {Te} (in MNT) of {e_np} is in foreign currency?"),
                (f"{np_g} {bare} дотор гадаад валютын эзлэх хувь хэд вэ (төгрөгт хөрвүүлж тооцно)?",
                 f"What share of the {m.en} of {e_np} is foreign-currency, converted to MNT?"),
                (f"{np_g} {T} дотор гадаад валютынх нь хэдэн хувийг эзэлж байна? Төгрөгт хөрвүүлж "
                 f"тооцно уу.",
                 f"What percentage of the {Te} of {e_np} is in foreign currency? Convert to MNT first."),
                (f"Төгрөгт хөрвүүлсэн дүнгээр {np_g} {bare} дахь гадаад валютын хувийг гарга.",
                 f"Using MNT-converted amounts, give the foreign-currency share of the {m.en} of {e_np}."),
                (f"{np_g} {bare} дотор төгрөгөөс бусад валютын эзлэх хувь хэд вэ?",
                 f"What share of the {m.en} of {e_np} is in currencies other than MNT?"),
                (f"{np_g} {bare_acc} төгрөгт шилжүүлээд, гадаад валютын хэсгийн эзлэх хувийг тооцоол.",
                 f"Convert the {m.en} of {e_np} to MNT and calculate the share held in foreign currency."),
                (f"Гадаад валютын эзлэх хувь {np_g} {bare} дотор хэд байна? (төгрөгт хөрвүүлж тооцно)",
                 f"How large is the foreign-currency share within the {m.en} of {e_np}, converted to MNT?"),
                (f"{np_g} {bare} гадаад валют ба төгрөгт хуваагдахад гадаад валютынх нь хэдэн хувь вэ? "
                 f"(төгрөгт хөрвүүлж)",
                 f"Splitting the {m.en} of {e_np} into foreign currency and MNT, what percentage is foreign "
                 f"(converted to MNT)?"),
            ])
            ph = "flat_" + ph
        else:
            fr.need(d.via, d.ext)
            sel, grp = dim_sel(d)
            sql = (f"SELECT {sel}, {expr} AS foreign_share_pct FROM {fr.sql()}{wh(w)} GROUP BY {grp} "
                   f"ORDER BY foreign_share_pct DESC NULLS LAST")
            mn, en, ph = _pick(rng, [
                (f"{d.mn_each} {np_g} {bare} дотор гадаад валютын эзлэх хувь хэд вэ (төгрөгт хөрвүүлж)?",
                 f"For each {d.en}, what share of the {m.en} of {e_np} is in foreign currency (converted to MNT)?"),
                (f"{np_g} {bare} дахь гадаад валютын хувийг {d.mn_by} гарга (төгрөгт хөрвүүлж тооцно).",
                 f"Show the foreign-currency share of the {m.en} of {e_np} by {d.en}, converted to MNT."),
                (f"{d.mn_by} нь бүлэглээд {np_g} {bare} дотор гадаад валютынх хэдэн хувь болохыг төгрөгт "
                 f"хөрвүүлж тооцоол.",
                 f"Group by {d.en} and compute what percent of the {m.en} of {e_np} is foreign-currency, in MNT."),
                (f"{d.mn_each} хувьд {np_g} {bare} дахь төгрөгөөс бусад валютын эзлэх хувь хэд вэ?",
                 f"For each {d.en}, what share of the {m.en} of {e_np} is in currencies other than MNT?"),
                (f"Гадаад валютын эзлэх хувийг {d.mn_each} {np_g} {bare} дотор тооцож, ихээс нь эрэмбэл.",
                 f"Compute the foreign-currency share of the {m.en} of {e_np} for each {d.en}, highest first."),
                (f"{np_g} {bare_acc} төгрөгт хөрвүүлээд {d.mn_by} нь гадаад валютын хувийг харуул.",
                 f"Convert the {m.en} of {e_np} to MNT and show the foreign-currency share by {d.en}."),
                (f"{d.mn_by} харвал {np_g} {bare} хэдэн хувь нь гадаад валютаар байна вэ? (төгрөгт хөрвүүлж)",
                 f"Looking by {d.en}, what percentage of the {m.en} of {e_np} is in foreign currency (in MNT terms)?"),
                (f"{d.mn_each} {np_g} {bare} дотор гадаад валютын хувийг жагсаа (төгрөгт хөрвүүлж).",
                 f"List the foreign-currency share of the {m.en} of {e_np} per {d.en}, converted to MNT."),
            ])
            ph = "grp_" + ph
    else:
        sql = (f"SELECT ROUND(SUM({m.expr}) / NULLIF(t2s.fx_to_mnt('USD', t2s.business_date()), 0), 2) AS amount_usd "
               f"FROM {fr.sql()}{wh(w)}")
        mn, en, ph = _pick(rng, [
            (f"{np_g} {Ta} төгрөгт хөрвүүлээд ажлын өдрийн ам.долларын ханшаар илэрхийлбэл хэд вэ?",
             f"Converted to MNT and then expressed in USD at the business-date rate, what is the {Te} of "
             f"{e_np}?"),
            (f"{np_g} {T} ам.доллараар (өнөөдрийн ханшаар) хэд болох вэ?",
             f"How much is the {Te} of {e_np} in USD at today's rate?"),
            (f"{np_g} {T} ам.доллараар хэд вэ? Төгрөгт хөрвүүлээд ажлын өдрийн ханшаар бодно уу.",
             f"What is the {Te} of {e_np} in USD? Convert to MNT, then use the business-date USD rate."),
            (f"{np_g} {Ta} ам.долларт шилжүүлж гарга (ажлын өдрийн ханшаар).",
             f"Give the {Te} of {e_np} in US dollars at the business-date rate."),
            (f"Ажлын өдрийн ам.долларын ханшаар {np_g} {T} хэдэн доллар болох вэ?",
             f"At the business-date USD rate, how many dollars is the {Te} of {e_np}?"),
            (f"{np_g} {Ta} эхлээд төгрөгт, дараа нь ам.долларт хөрвүүлж харуул.",
             f"Convert the {Te} of {e_np} first to MNT and then to USD."),
            (f"{np_g} {T} долларын дүнгээр хэд байна? (өнөөдрийн ханшаар)",
             f"What is the {Te} of {e_np} in dollar terms at today's rate?"),
            (f"Өнөөдрийн ханшаар ам.доллараар илэрхийлбэл {np_g} {T} ямар хэмжээтэй вэ?",
             f"Expressed in USD at today's rate, how large is the {Te} of {e_np}?"),
        ])
    diff = "hard" if kind in ("foreign_share", "to_usd") or fr.cross else "medium"
    return Cand("multi_currency", sql, mn, en, f.table, f"mc_{kind}|{f.table}|{m.key}|{d.key if d else ''}", diff,
                fr.cross, codes=sum((c.codes for c in conds), ()), phrasing=f"{kind}_{ph}")


def gen_topk_ties(rng: random.Random) -> Cand | None:
    f = rng.choice(list(FACTS.values()))
    dims = applicable_dims(f)
    if not dims:
        return None
    d = rng.choice(dims)
    m = rng.choice(f.measures)
    # жижиг олонлогт (enum, валют) «эхний k» утгагүй — k нь утгын тооноос бага байх ёстой
    n_vals = len(enum_values(d.table, d.col)) if d.enum else (2 if d.key == "loan_currency" else 10 ** 6)
    ks = [k for k in (3, 5, 10) if k < n_vals]
    if not ks:
        return None
    k = rng.choice(ks)
    desc = rng.random() < 0.75
    per = rng.choice(PERIODS) if rng.random() < 0.6 else None
    conds = random_conds(rng, f, p_enum=0.25)
    fr = From(f)
    for n in m.needs:
        fr.need(n)
    fr.need(d.via, d.ext)
    w = where_sql(f, fr, per, conds, m)
    sel, grp = dim_sel(d)
    o = d.out_name
    agg = agg_sql(m, rnd=False)
    tot = agg if m.agg == "COUNT" else f"ROUND({agg}, 2)"
    dir_sql = "DESC" if desc else "ASC"
    style = rng.choice(["rank", "dense", "fetch", "group"])
    hi_mn, hi_en = ("хамгийн их", "highest") if desc else ("хамгийн бага", "lowest")
    np_g, e_np, Me = np_mn(f, per, conds), np_en(f, per, conds), m_en(m)
    M, Ma = m_pair(m, rng)
    g = None
    if style == "group":
        gs = [x for x in applicable_dims(f, enum_only=True) if x.key != d.key and x.table != d.table] + \
             [own_dim(f, c) for c in f.enums]
        g = rng.choice(gs) if gs else None
        if g is None:
            style = "rank"
    if style == "fetch":
        sql = (f"SELECT {sel}, {tot} AS total FROM {fr.sql()}{wh(w)} GROUP BY {grp} "
               f"ORDER BY total {dir_sql} FETCH FIRST {k} ROWS WITH TIES")
    elif style == "group":
        if not g.key.startswith("own_"):
            fr.need(g.via, g.ext)
        gsel, ggrp = dim_sel(g)
        go = g.out_name
        sql = (f"WITH ranked AS (SELECT {gsel}, {sel}, {tot} AS total, DENSE_RANK() OVER (PARTITION BY "
               f"{g.alias}.{g.col} ORDER BY {agg} {dir_sql}) AS rnk FROM {fr.sql()}{wh(w)} GROUP BY {ggrp}, {grp}) "
               f"SELECT {go}, {o}, total, rnk FROM ranked WHERE rnk <= {k} ORDER BY {go}, rnk, {o}")
    else:
        fn = "RANK" if style == "rank" else "DENSE_RANK"
        sql = (f"WITH ranked AS (SELECT {sel}, {tot} AS total, {fn}() OVER (ORDER BY {agg} {dir_sql}) AS rnk "
               f"FROM {fr.sql()}{wh(w)} GROUP BY {grp}) SELECT {o}, total, rnk FROM ranked WHERE rnk <= {k} "
               f"ORDER BY rnk, {o}")
    if style == "group":
        mn, en, ph = _pick(rng, [
            (f"{g.mn_each} хүрээнд {np_g} {M} {hi_mn} {k} {d.mn_noun} аль вэ? Тэнцсэн утгатайг хамруул.",
             f"Within each {g.en}, which {k} {d.en_pl} have the {hi_en} {Me} of {e_np}? Include ties."),
            (f"{g.mn_each} хувьд {np_g} {M} {hi_mn} эхний {k} байрт орох {d.mn_noun} аль нь вэ? "
             f"Тэнцсэн байрыг оруулна.",
             f"For each {g.en}, rank {d.en_pl} by the {Me} of {e_np} and return the top {k}, keeping ties."),
            (f"{g.mn_each} {np_g} {M} {hi_mn} {k} {d.mn_noun} жагсаа, тэнцсэнийг нь орхилгүй.",
             f"For each {g.en}, list the {k} {d.en_pl} with the {hi_en} {Me} of {e_np}, without dropping ties."),
            (f"{g.mn_by} нь салгаад, тус бүрт {np_g} {M} {hi_mn} эхний {k} {d.mn_noun} харуул (тэнцвэл бүгдийг).",
             f"Split by {g.en} and show the {k} {d.en_pl} with the {hi_en} {Me} of {e_np} in each, all ties "
             f"included."),
            (f"{g.mn_each} дотор {np_g} {Ma} {d.mn_by} эрэмбэлж, эхний {k} байрыг тэнцсэнтэй нь гарга.",
             f"Within each {g.en}, rank the {Me} of {e_np} by {d.en} and return the first {k} places with ties."),
            (f"{np_g} {M} {hi_mn} {k} {d.mn_noun} {g.mn_each} хувьд аль нь вэ? Тэнцсэн байрыг хасахгүй.",
             f"Which {k} {d.en_pl} have the {hi_en} {Me} of {e_np} for each {g.en}? Don't drop ties."),
            (f"Тэнцсэн утгыг оролцуулаад {g.mn_each} хүрээнд {np_g} {M} {hi_mn} {k} {d.mn_noun} харуул.",
             f"Including ties, show the {k} {d.en_pl} with the {hi_en} {Me} of {e_np} within each {g.en}."),
            (f"{g.mn_by} нь бүлэглээд {np_g} {M} {hi_mn} {k} {d.mn_noun} аль болохыг тэнцсэнтэй нь гарга.",
             f"Grouping by {g.en}, give the {k} {d.en_pl} with the {hi_en} {Me} of {e_np}, ties included."),
        ])
        ph = "grp_" + ph
    else:
        mn, en, ph = _pick(rng, [
            (f"{np_g} {M} {hi_mn} {k} {d.mn_noun} аль вэ? Тэнцсэн утгатай бол бүгдийг нь харуул.",
             f"Which {k} {d.en_pl} have the {hi_en} {Me} of {e_np}? Include ties."),
            (f"{d.mn_each} {np_g} {Ma} эрэмбэлж, эхний {k} байрт орсныг тэнцсэнийг нь оролцуулан гарга.",
             f"Rank {d.en_pl} by the {Me} of {e_np} and return the top {k}, keeping ties."),
            (f"Тэнцсэн байрыг хамруулан {np_g} {M} {hi_mn} эхний {k} {d.mn_noun} аль нь вэ?",
             f"List the {k} {d.en_pl} with the {hi_en} {Me} of {e_np}, including any ties."),
            (f"{np_g} {M} {hi_mn} {k} {d.mn_noun} жагсаа. Тэнцсэн бол бүгдийг нь оруул.",
             f"List the {k} {d.en_pl} with the {hi_en} {Me} of {e_np}. If tied, include all."),
            (f"{d.mn_by} эрэмбэлэхэд {np_g} {M} {hi_mn} эхний {k} {d.mn_noun} аль нь вэ? Тэнцсэнийг оруул.",
             f"Ranking by {d.en}, which are the first {k} by {hi_en} {Me} of {e_np}? Include ties."),
            (f"{np_g} {M} {hi_mn} эхний {k} {d.mn_noun} гарга, тэнцсэн байр байвал хамт нь.",
             f"Give the first {k} {d.en_pl} with the {hi_en} {Me} of {e_np}, together with any ties."),
            (f"{d.mn_each} {np_g} {Ma} тооцоод, {hi_mn} эхний {k} байрыг тэнцсэнийг нь хасалгүй харуул.",
             f"Compute the {Me} of {e_np} for each {d.en} and show the {k} {hi_en} places without dropping ties."),
            (f"{np_g} {M} {hi_mn} {k} {d.mn_noun} аль болохыг тэнцсэн утгатай нь харуулна уу.",
             f"Please show which {k} {d.en_pl} have the {hi_en} {Me} of {e_np}, with ties."),
            (f"{np_g} {M} {d.mn_by} эрэмбэлбэл {hi_mn} эхний {k} {d.mn_noun} аль вэ? Тэнцсэн байр орно.",
             f"If we rank {d.en_pl} by the {Me} of {e_np}, which are the {k} {hi_en}? Ties count."),
        ])
        ph = "top_" + ph
    return Cand("topk_ties", sql, mn, en, f.table, f"topk_{style}|{f.table}|{d.key}|{m.key}|{dir_sql}",
                "hard" if style in ("group", "rank", "dense") else "medium", fr.cross,
                order_matters=True, codes=sum((c.codes for c in conds), ()), phrasing=ph)


def gen_enum_filter(rng: random.Random) -> Cand | None:
    f = rng.choice(list(FACTS.values()))
    if not f.enums:
        return None
    conds: list[Cond] = []
    mode = rng.choice(["eq", "eq", "eq", "in", "ne"])
    c1 = enum_cond(rng, f, col=rng.choice(f.enums), mode=mode)
    if c1 is None:
        return None
    conds.append(c1)
    joined = applicable_dims(f, enum_only=True)
    if joined and rng.random() < 0.45:
        c2 = enum_cond(rng, f, d=rng.choice(joined), mode="eq")
        if c2:
            conds.append(c2)
    elif len(f.enums) > 1 and rng.random() < 0.35:
        rest2 = [c for c in f.enums if f"{f.a}.{c} " not in c1.sql]
        c2 = enum_cond(rng, f, col=rng.choice(rest2)) if rest2 else None
        if c2:
            conds.append(c2)
    if rng.random() < 0.1 and f.flags:
        conds.append(flag_cond(rng, f))
    if not conds:
        return None
    per = rng.choice(PERIODS) if rng.random() < 0.45 else None
    ms = f.measures
    m = rng.choice(ms) if rng.random() < 0.5 else ms[0]
    fr = From(f)
    for n in m.needs:
        fr.need(n)
    w = where_sql(f, fr, per, conds, m)
    np_g, np_p, e_np = np_mn(f, per, conds), np_mn(f, per, conds, gen=False), np_en(f, per, conds)
    grp = None
    rest = [c for c in f.enums if not any(f"{f.a}.{c} " in x.sql for x in conds)]
    if rest and rng.random() < 0.25:
        grp = own_dim(f, rng.choice(rest))
    if grp:
        sql = (f"SELECT {f.a}.{grp.col}, {agg_sql(m)} AS value FROM {fr.sql()}{wh(w)} GROUP BY {f.a}.{grp.col} "
               f"ORDER BY value DESC")
        M, Ma, Me = *m_pair(m, rng), m_en(m)
        mn, en, ph = _pick(rng, [
            (f"{np_g} {Ma} {grp.mn_by} гарга.", f"Show the {Me} of {e_np} by {grp.en}."),
            (f"{grp.mn_each} {np_g} {M} хэд вэ?", f"For each {grp.en}, what is the {Me} of {e_np}?"),
            (f"{np_g} {Ma} {grp.mn_by} нь бүлэглээд харуул.", f"Group {e_np} by {grp.en} and show the {Me}."),
            (f"{grp.mn_by} нь задлавал {np_g} {M} ямар байна?",
             f"Broken down by {grp.en}, what is the {Me} of {e_np}?"),
            (f"{np_g} {M} {grp.mn_each} хувьд хэд вэ?", f"What is the {Me} of {e_np} in each {grp.en}?"),
            (f"{grp.mn_by} нь {np_g} {Ma} жагсаа.", f"List the {Me} of {e_np} by {grp.en}."),
            (f"{np_g} {Ma} {grp.mn_by} тооцоод ихээс нь эрэмбэл.",
             f"Compute the {Me} of {e_np} by {grp.en}, highest first."),
            (f"{grp.mn_each} {np_g} {M} хэд байгааг харуулна уу.", f"Please show the {Me} of {e_np} for each {grp.en}."),
        ])
        ph = "grp_" + ph
    elif m.agg == "COUNT":
        sql = f"SELECT COUNT(*) AS row_count FROM {fr.sql()}{wh(w)}"
        mn, en, ph = _pick(rng, [
            (f"{np_p} хэд байна?", f"How many {e_np} are there?"),
            (f"{np_g} тоо хэд вэ?", f"What is the number of {e_np}?"),
            (f"{np_g} тоог гарга.", f"Count the {e_np}."),
            (f"{np_p} нийт хэд вэ?", f"How many {e_np} are there in total?"),
            (f"{np_g} тоог тоолж харуул.", f"Show the count of {e_np}."),
            (f"{np_p} тоогоороо хэд байна?", f"What is the count of {e_np}?"),
            (f"{np_g} тоо хэд байгааг гарга.", f"Give the number of {e_np}."),
            (f"{np_p} хэд байгааг тоолоод хэлээч.", f"Count how many {e_np} there are and tell me."),
            (f"{np_g} нийт тоо хэд гарч байна?", f"What total number of {e_np} do we get?"),
        ])
        ph = "cnt_" + ph
    else:
        sql = f"SELECT {agg_sql(m)} AS value FROM {fr.sql()}{wh(w)}"
        M, Ma, Me = *m_pair(m, rng), m_en(m)
        mn, en, ph = _pick(rng, [
            (f"{np_g} {M} хэд вэ?", f"What is the {Me} of {e_np}?"),
            (f"{np_g} {Ma} гарга.", f"Show the {Me} of {e_np}."),
            (f"{np_g} {M} хэд байна?", f"How much is the {Me} of {e_np}?"),
            (f"{np_g} {Ma} тооцоод харуул.", f"Calculate and show the {Me} of {e_np}."),
            (f"{np_g} хувьд {M} ямар байна?", f"For {e_np}, what does the {Me} come to?"),
            (f"{np_g} {Ma} олж өгнө үү.", f"Please find the {Me} of {e_np}."),
            (f"{np_g} {M} хэд гарч байгааг харуул.", f"Show what the {Me} of {e_np} amounts to."),
            (f"{Ma} {np_g} хувьд гарга.", f"Give the {Me} for {e_np}."),
        ])
        ph = "val_" + ph
    diff = "medium" if fr.joins or len(conds) > 1 or grp else "easy"
    return Cand("enum_filter", sql, mn, en, f.table, f"enum|{f.table}|{m.key}|{bool(grp)}|{len(conds)}", diff,
                fr.cross, codes=sum((c.codes for c in conds), ()), phrasing=ph)


GENERATORS = {"join_fk": gen_join_fk, "cte_multi": gen_cte_multi, "ratio_nullif": gen_ratio_nullif,
              "lag_pop": gen_lag_pop, "multi_currency": gen_multi_currency, "topk_ties": gen_topk_ties,
              "enum_filter": gen_enum_filter}


# ---------------------------------------------------------------------------
# Шалгалт: parse + guard
# ---------------------------------------------------------------------------

def make_guard():
    """eval.goldcheck.make_guard-тэй ижил тохиргоо (DB/psycopg2-гүй)."""
    from ..config import load_config
    from ..security.sql_guard import SqlGuard
    cfg = load_config()
    m = model()
    return SqlGuard(allowed_tables=m.allowed(), schema=m.schema,
                    forbidden_functions=set(cfg["security"]["forbid_functions"]),
                    as_of_date=str(cfg["project"]["as_of_date"]))


def parse_ok(sql: str) -> bool:
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
        return tree is not None and bool(tree.sql(dialect="postgres"))
    except Exception:
        return False


def norm_sql(sql: str) -> str:
    return re.sub(r"\s+", " ", sql.strip().rstrip(";")).lower()


def tags_for(c: Cand) -> list[str]:
    s = c.sql.upper()
    t = []
    if any(x in s for x in ("SUM(", "COUNT(", "AVG(", "MAX(", "MIN(")):
        t.append("aggregation")
    if "GROUP BY" in s:
        t.append("group_by")
    if " JOIN " in s:
        t.append("join")
    if "BETWEEN '" in s:
        t.append("date")
    if "NULLIF(" in s:
        t += ["ratio", "null_handling"]
    if s.startswith("WITH "):
        t.append("cte")
    if " OVER (" in s:
        t.append("window")
    if "LAG(" in s:
        t.append("time_series")
    if "RANK()" in s or "WITH TIES" in s:
        t.append("top_k")
    if "FX_TO_MNT" in s or "_MNT" in s or "CURRENCY_CODE" in s:
        t.append("multi_currency")
    if c.codes:
        t.append("enum")
    if c.cross:
        t.append("cross_domain")
    if c.glossary:
        t.append("glossary")
    return t


def to_row(c: Cand, idx: int, rng: random.Random) -> dict:
    from ..bird.data import latin_variant
    fam = hashlib.sha1(c.skeleton.encode()).hexdigest()[:8]
    rid = f"SO-{c.pattern[:4].upper()}-{idx:05d}"
    domain = "cross" if c.cross else FACTS[c.fact].domain if c.fact in FACTS else \
        {"exchange_rates": "gl_trends", "account_balances_monthly": "deposits", "loan_dpd_history": "loans"}[c.fact]
    return {"id": rid, "family": f"SO-{c.pattern}-{fam}", "domain": domain, "category": "answerable",
            "difficulty": c.difficulty, "question_mn": c.mn, "question_latin": latin_variant(c.mn, rng),
            "question_en": c.en, "question_mixed": None, "gold_sql": c.sql, "alt_sql": [],
            "order_matters": c.order_matters, "tags": tags_for(c), "contains_value": False, "values": [],
            "enum_codes": sorted(set(x for x in c.codes if x)), "glossary_terms": list(c.glossary),
            "notes": f"synth_own phrasing={c.phrasing}", "pool": "synth_own", "pattern": c.pattern,
            "source": "synth_own", "needs_exec_check": True}


def _tidy(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"\s+([,?.])", r"\1", text)
    return text[:1].upper() + text[1:]


def generate(seed: int = 42, quota: dict | None = None, max_tries: int = 60) -> tuple[list[dict], dict]:
    """Хэв маяг бүрээс quota хүртэл давхардалгүй мөр. Буцаах: (мөрүүд, статистик)."""
    errs = check_fk_paths()
    if errs:
        raise ValueError(f"FK зам semantic model-д алга: {errs}")
    quota = quota or QUOTA
    guard = make_guard()
    rng = random.Random(seed)
    seen_sql, seen_q = set(), set()
    rows, stats = [], {"attempts": Counter(), "parse_fail": Counter(), "guard_fail": Counter(),
                       "dup_sql": Counter(), "dup_question": Counter(), "kept": Counter(), "guard_codes": Counter()}
    for pattern in PATTERNS:
        gen = GENERATORS[pattern]
        tries = 0
        while stats["kept"][pattern] < quota[pattern] and tries < quota[pattern] * max_tries:
            tries += 1
            c = gen(rng)
            if c is None:
                continue
            stats["attempts"][pattern] += 1
            c.mn, c.en = _tidy(c.mn), _tidy(c.en)
            if not parse_ok(c.sql):
                stats["parse_fail"][pattern] += 1
                continue
            g = guard.check(c.sql, rewrite=False)
            if not g.ok:
                stats["guard_fail"][pattern] += 1
                stats["guard_codes"][f"{pattern}:{g.code}:{(g.error or '')[:80]}"] += 1
                continue
            ns = norm_sql(c.sql)
            if ns in seen_sql:
                stats["dup_sql"][pattern] += 1
                continue
            qk = c.mn.lower()
            if qk in seen_q:          # ижил асуулт, өөр SQL — шошгын шуугиан тул хасна
                stats["dup_question"][pattern] += 1
                continue
            seen_sql.add(ns)
            seen_q.add(qk)
            rows.append(to_row(c, len(rows) + 1, random.Random(f"{seed}:{len(rows)}")))
            stats["kept"][pattern] += 1
    return rows, {k: dict(v) for k, v in stats.items()}


# ---------------------------------------------------------------------------
# dev+test-тэй ойролцоо давхардал (eval/split.py-ийн _vec/_cos; урвуу индексээр хурдан)
# ---------------------------------------------------------------------------

class NgramIndex:
    """eval.split._cos-тэй ижил cosine-ийг урвуу индексээр (n×m-ийг бүтэн давтахгүй)."""

    def __init__(self, texts: list[tuple[str, str]]):
        from ..eval.split import _vec
        self.ids = [i for i, _ in texts]
        self.norms = []
        self.post: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for j, (_, t) in enumerate(texts):
            v = _vec(t)
            self.norms.append(sum(x * x for x in v.values()) ** 0.5)
            for g, cnt in v.items():
                self.post[g].append((j, cnt))

    def best(self, text: str) -> tuple[str | None, float]:
        from ..eval.split import _vec
        v = _vec(text)
        n = sum(x * x for x in v.values()) ** 0.5
        if not n:
            return None, 0.0
        acc: dict[int, float] = defaultdict(float)
        for g, cnt in v.items():
            for j, c2 in self.post.get(g, ()):
                acc[j] += cnt * c2
        if not acc:
            return None, 0.0
        j, dot = max(((j, d / (n * self.norms[j])) for j, d in acc.items() if self.norms[j]), key=lambda x: x[1])
        return self.ids[j], dot


def load_eval_texts(paths: list[str] | None = None) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """dev+test-ийн асуулт (бүх хувилбар) ба gold SQL (alt, interpretations) — зөвхөн автомат шүүлтэд."""
    from ..eval.dataset import load_items
    paths = paths or ["evalset/dev.jsonl", "evalset/test.jsonl"]
    items = load_items([p for p in paths if resolve_path(p).exists()])
    qs, sqls = [], []
    for it in items:
        for v in ("mn", "latin", "en", "mixed"):
            q = it.question(v)
            if q:
                qs.append((it.id, q))
        for s in it.gold_sqls():
            sqls.append((it.id, norm_sql(s)))
    return qs, sqls


def near_dup_filter(rows: list[dict], eval_qs: list[tuple[str, str]], eval_sqls: list[tuple[str, str]],
                    q_threshold: float = Q_THRESHOLD, sql_threshold: float = SQL_THRESHOLD
                    ) -> tuple[list[dict], list[dict]]:
    qi, si = NgramIndex(eval_qs), NgramIndex(eval_sqls)
    exact = {s for _, s in eval_sqls}
    keep, dropped = [], []
    hist = {"question": Counter(), "gold_sql": Counter()}   # хамгийн өндөр ойролцоо байдлын тархалт (0.1 алхам)
    for r in rows:
        hit = None
        best_q = 0.0
        for fld in ("question_mn", "question_latin", "question_en"):
            eid, s = qi.best(r[fld])
            best_q = max(best_q, s)
            if s >= q_threshold and hit is None:
                hit = {"id": r["id"], "pattern": r["pattern"], "eval_id": eid, "sim": round(s, 3), "field": fld}
        ns = norm_sql(r["gold_sql"])
        eid_s, s_sql = si.best(ns)
        if ns in exact:
            s_sql = 1.0
        hist["question"][f"{min(9, int(best_q * 10)) / 10:.1f}"] += 1
        hist["gold_sql"][f"{min(9, int(s_sql * 10)) / 10:.1f}"] += 1
        if hit is None and s_sql >= sql_threshold:
            hit = {"id": r["id"], "pattern": r["pattern"], "eval_id": eid_s, "sim": round(s_sql, 3),
                   "field": "gold_sql"}
        (dropped if hit else keep).append(hit or r)
    near_dup_filter.last_hist = {k: dict(sorted(v.items())) for k, v in hist.items()}
    return keep, dropped


def dev_tag_counts(path: str = "evalset/dev.jsonl") -> dict:
    """dev-ийн зөвхөн нэгтгэсэн шошгын тоо (асуултын текстийг ашиглахгүй)."""
    p = resolve_path(path)
    c = Counter()
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                c.update(json.loads(line).get("tags") or [])
    return dict(c.most_common())


def template_counts(rows: list[dict]) -> dict[str, int]:
    """Хэв маяг бүрийн ашиглагдсан асуултын загварын (notes-ийн phrasing=) ялгаатай тоо."""
    seen: dict[str, set] = defaultdict(set)
    for r in rows:
        m = re.search(r"phrasing=(\S+)", r.get("notes") or "")
        if m:
            seen[r["pattern"]].add(m.group(1))
    return {p: len(seen[p]) for p in PATTERNS if p in seen}


def write_jsonl(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def run(out_dir: str = "build/synth_own", seed: int = 42, scale: float = 1.0, neardup: bool = True) -> dict:
    quota = {k: max(1, int(v * scale)) for k, v in QUOTA.items()}
    rows, gstats = generate(seed, quota)
    out = resolve_path(out_dir)
    write_jsonl(rows, out / "candidates_raw.jsonl")
    dropped: list[dict] = []
    if neardup:
        qs, sqls = load_eval_texts()
        rows_kept, dropped = near_dup_filter(rows, qs, sqls)
    else:
        rows_kept = rows
    write_jsonl(rows_kept, out / "candidates.jsonl")
    attempts = sum(gstats["attempts"].values())
    stats = {
        "seed": seed, "raw": len(rows), "kept": len(rows_kept), "neardup_removed": len(dropped),
        "neardup_by_field": dict(Counter(d["field"] for d in dropped)),
        "neardup_by_pattern": dict(Counter(d["pattern"] for d in dropped)),
        "pattern_raw": dict(Counter(r["pattern"] for r in rows)),
        "pattern_kept": dict(Counter(r["pattern"] for r in rows_kept)),
        "difficulty": dict(Counter(r["difficulty"] for r in rows_kept)),
        "domain": dict(Counter(r["domain"] for r in rows_kept)),
        "tags": dict(Counter(t for r in rows_kept for t in r["tags"]).most_common()),
        "templates_per_pattern": template_counts(rows_kept),
        "generated_attempts": attempts,
        "parse_pass_rate": round(1 - sum(gstats["parse_fail"].values()) / max(1, attempts), 4),
        "guard_pass_rate": round(1 - sum(gstats["guard_fail"].values()) / max(1, attempts - sum(
            gstats["parse_fail"].values())), 4),
        "gen": gstats, "dev_tag_counts": dev_tag_counts(),
        "thresholds": {"question": Q_THRESHOLD, "sql": SQL_THRESHOLD},
        "max_sim_hist": getattr(near_dup_filter, "last_hist", {}) if neardup else {},
    }
    # eval-ийн текстийг бичихгүй: зөвхөн id, оноо
    (out / "neardup_report.json").write_text(json.dumps(dropped, ensure_ascii=False, indent=1), encoding="utf-8")
    (out / "stats.json").write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    return stats


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="mn_text2sql.training.synth_own", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="build/synth_own")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--scale", type=float, default=1.0, help="QUOTA-г үржүүлэх (жишээ: 0.1 — туршилт)")
    ap.add_argument("--no-neardup", action="store_true")
    a = ap.parse_args(argv)
    s = run(a.out, a.seed, a.scale, not a.no_neardup)
    brief = {k: s[k] for k in ("raw", "kept", "neardup_removed", "neardup_by_field", "pattern_kept", "templates_per_pattern",
                               "parse_pass_rate", "guard_pass_rate", "difficulty", "domain")}
    sys.stdout.write(json.dumps(brief, ensure_ascii=False, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
