"""Өгөгдөл үүсгэгчийн нийтлэг төлөв: тохиргоо, RNG, ID олгогч, лавлах, бизнес дугаарлалт."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from types import SimpleNamespace

from .buffer import Store
from .util import Rng

# Identity/sequence-тэй хүснэгтүүд (explicit ID өгч COPY хийнэ, дараа нь setval)
IDENTITY_TABLES = [
    "core.branches", "core.employees", "core.employee_assignments", "core.users",
    "customer.customer_addresses", "customer.customer_contacts", "customer.customer_employments",
    "customer.customer_bank_accounts", "customer.customer_relationships", "customer.customer_status_history",
    "customer.customer_segment_history",
    "kyc.kyc_profiles", "kyc.kyc_documents", "kyc.kyc_verifications", "kyc.customer_risk_profiles",
    "kyc.pep_records", "kyc.aml_screenings", "kyc.aml_screening_matches", "kyc.aml_alerts",
    "kyc.sanction_entries",
    "product.product_types", "product.products", "product.product_rates", "product.product_fees",
    "product.product_limits", "product.payment_allocation_rules",
    "loan.loan_application_documents", "loan.loan_assessments", "loan.loan_decisions", "loan.loan_borrowers",
    "loan.loan_disbursements", "loan.loan_schedules", "loan.loan_schedule_items", "loan.loan_payments",
    "loan.loan_payment_allocations", "loan.loan_interest_rate_history", "loan.loan_fees", "loan.loan_penalties",
    "loan.loan_classifications", "loan.loan_restructures", "loan.loan_writeoffs", "loan.loan_status_history",
    "collateral.collateral_valuations", "collateral.collateral_insurances", "collateral.loan_collaterals",
    "collateral.loan_guarantors", "collateral.collateral_documents",
    "account.account_holds", "account.account_status_history",
    "deposit.deposit_rates", "deposit.deposit_interest_accruals", "deposit.deposit_transactions",
    "deposit.deposit_early_withdrawals",
    "accounting.accounting_periods", "accounting.journal_entries", "accounting.ledger_balances",
    "risk.credit_scores", "risk.credit_bureau_reports", "risk.risk_assessments", "risk.fraud_alerts",
    "risk.fraud_cases",
    "collection.collection_actions", "collection.collection_promises", "collection.collection_payments",
    "collection.recovery_cases", "collection.case_assignments",
    "system.exchange_rates",
]

# Партицлагдсан хүснэгтүүд — sequence DEFAULT-тай
SEQUENCE_TABLES = {
    "loan.loan_interest_accruals": "loan.seq_interest_accruals",
    "loan.loan_overdues": "loan.seq_loan_overdues",
    "account.transactions": "account.seq_transactions",
    "account.account_balances": "account.seq_account_balances",
    "accounting.journal_lines": "accounting.seq_journal_lines",
}


@dataclass
class Variant:
    """Test-suite хуулбарын өөрчлөлтийн тохиргоо (eval/testsuite.py)."""
    name: str = "main"
    null_boost: float = 0.0        # сонголттой баганад NULL-ийн магадлалыг нэмэх
    tie_mode: bool = False         # дүнг бүдүүн бөөрөнхийлж тэнцүү утга олшруулах
    boundary_mode: bool = False    # босго утгууд (DPD 30/90, сарын эхэн/сүүл) олшруулах
    sparse_tables: list[str] = field(default_factory=list)  # хоосон үлдээх хүснэгтүүд


class Gen:
    def __init__(self, seed: int, as_of: date, history_start: date, scale: float, variant: Variant | None = None):
        self.seed = seed
        self.rng = Rng(seed)
        self.as_of = as_of
        self.start = history_start
        self.scale = scale
        self.variant = variant or Variant()
        self.store = Store()
        self.next_ids: dict[str, int] = {}
        self.seq_counters: dict[tuple[str, int | None], int] = defaultdict(int)
        self.seq_format: dict[str, SimpleNamespace] = {}
        self.ref = SimpleNamespace()
        # Үүсгэсэн обьектууд (модулиуд хооронд хуваалцана)
        self.branches: list[SimpleNamespace] = []
        self.employees: list[SimpleNamespace] = []
        self.users_by_employee: dict[int, int] = {}
        self.sys_user_id: int | None = None
        self.customers: list[SimpleNamespace] = []
        self.loans: list[SimpleNamespace] = []
        self.accounts: list[SimpleNamespace] = []
        self.deposits: list[SimpleNamespace] = []
        self.fx: dict[tuple[str, date], float] = {}

    # ----------------------------------------------------------------- ID
    def set_max_ids(self, max_ids: dict[str, int]) -> None:
        for table, mx in max_ids.items():
            self.next_ids[table] = (mx or 0) + 1

    def next_id(self, table: str) -> int:
        nid = self.next_ids.get(table, 1)
        self.next_ids[table] = nid + 1
        return nid

    def uuid(self):
        return self.rng.uuid()

    # ------------------------------------------------- бизнес дугаарлалт
    def number(self, code: str, on: date) -> str:
        """system.number_sequences-ийн форматаар (LN-2025-000123). Жилээр тэглэх дүрэмтэй."""
        fmt = self.seq_format[code]
        year = on.year if fmt.include_year else None
        key = (code, year if fmt.reset_period == "YEARLY" else None)
        self.seq_counters[key] += 1
        value = self.seq_counters[key]
        prefix = fmt.prefix + (f"{on.year}-" if fmt.include_year else "")
        return f"{prefix}{value:0{fmt.padding_length}d}{fmt.suffix}"

    # ----------------------------------------------------------- туслах
    @staticmethod
    def hmac_like(text: str) -> bytes:
        return hashlib.sha256(("t2s-pepper:" + text).encode("utf-8")).digest()

    def maybe_null(self, value, base_p: float = 0.0):
        """Variant.null_boost-оор сонголттой багана NULL болох магадлалыг нэмнэ."""
        p = base_p + self.variant.null_boost
        return None if p > 0 and self.rng.chance(p) else value

    def money_round(self, value: float, step: int) -> int:
        if self.variant.tie_mode:
            step = step * 10
        return self.rng.round_to(value, step)

    def sparse(self, table: str) -> bool:
        return table in self.variant.sparse_tables
