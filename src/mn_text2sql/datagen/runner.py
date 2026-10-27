"""Өгөгдөл үүсгэх бүрэн дараалал: DB үүсгэх → схем → лавлах → өгөгдөл → COPY → тохируулга → шалгалт."""

from __future__ import annotations

import logging
import time
from datetime import date, datetime

from ..config import Config
from ..db.conn import admin_dsn, connection
from ..db.loader import create_database, load_core_schema, load_t2s_layer, run_validation
from .accounting import build_accounting
from .banking import build_banking
from .collateral import build_collateral
from .collection import build_collection
from .context import IDENTITY_TABLES, SEQUENCE_TABLES, Gen, Variant
from .lending import build_lending
from .people import build_customers, build_employees, finalize_customer_status, write_customer_rows
from .refdata import add_reference_rows, load_refs

log = logging.getLogger(__name__)

# (хүснэгт, дугаарын багана, огнооны багана, number_sequences код)
RENUMBER = [
    ("loan.loan_applications", "application_no", "submitted_at", "LOAN_APPLICATION"),
    ("loan.loan_contracts", "contract_number", "signed_date", "LOAN_CONTRACT"),
    ("loan.loan_payments", "payment_no", "payment_date", "LOAN_PAYMENT"),
    ("account.accounts", "account_number", "opened_date", "ACCOUNT"),
    ("account.transactions", "transaction_no", "value_date", "TRANSACTION"),
    ("deposit.deposit_accounts", "deposit_number", "opened_date", "DEPOSIT"),
    ("deposit.deposit_contracts", "contract_number", "signed_date", "DEPOSIT_CONTRACT"),
    ("collateral.collaterals", "collateral_number", "valuation_date", "COLLATERAL"),
    ("collateral.guarantors", "guarantor_number", "created_at", "GUARANTOR"),
    ("collection.collection_cases", "case_no", "opened_date", "COLLECTION_CASE"),
    ("collection.recovery_cases", "recovery_no", "started_date", "RECOVERY"),
    ("kyc.aml_alerts", "alert_no", "detected_at", "AML_ALERT"),
]

# Test-suite хуулбарт хамт хоослох хүснэгтийн бүлгүүд (FK-ийн уялдаа хадгална)
SPARSE_GROUPS = {
    "writeoffs": ["collection.recovery_cases", "loan.loan_writeoffs"],
    "aml": ["kyc.aml_screening_matches", "kyc.aml_alerts"],
    "restructures": ["loan.loan_restructures", "loan.loan_interest_rate_history"],
    "guarantors": ["collateral.loan_guarantors"],
    "early_withdrawals": ["deposit.deposit_early_withdrawals"],
}


def _sort_key(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    return datetime.min


def renumber_business_keys(gen: Gen) -> None:
    """Бизнес дугаарыг огнооны дарааллаар дахин олгоно (LP-2025-00000001 нь 2025 оны хамгийн эхний төлбөр)."""
    for table, col, date_col, code in RENUMBER:
        buf = gen.store.tables.get(table)
        if buf is None or not buf.rows:
            continue
        for key in [k for k in gen.seq_counters if k[0] == code]:
            del gen.seq_counters[key]
        ci, di = buf.col(col), buf.col(date_col)
        order = sorted(range(len(buf.rows)), key=lambda i: (_sort_key(buf.rows[i][di]), i))
        for i in order:
            row = list(buf.rows[i])
            d = row[di]
            row[ci] = gen.number(code, d.date() if isinstance(d, datetime) else d)
            buf.rows[i] = tuple(row)


def apply_sparse(gen: Gen) -> None:
    for group in gen.variant.sparse_tables:
        for table in SPARSE_GROUPS.get(group, [group]):
            if table in gen.store.tables:
                gen.store.tables[table].rows.clear()


def _post_sql(gen: Gen, conn) -> None:
    with conn.cursor() as cur:
        for table in IDENTITY_TABLES:
            cur.execute(f"SELECT setval(pg_get_serial_sequence('{table}', 'id'), "
                        f"GREATEST((SELECT COALESCE(max(id), 0) FROM {table}), 1))")
        for table, seq in SEQUENCE_TABLES.items():
            cur.execute(f"SELECT setval('{seq}', GREATEST((SELECT COALESCE(max(id), 0) FROM {table}), 1))")
        accountant = next((gen.users_by_employee[e.id] for e in gen.employees if e.position == "ACCOUNTANT"),
                          gen.sys_user_id)
        as_of = gen.as_of
        cur.execute("""
            UPDATE accounting.accounting_periods
               SET status = CASE WHEN end_date < date_trunc('month', %s::date) THEN 'CLOSED'
                                 WHEN start_date <= %s::date THEN 'OPEN' ELSE 'FUTURE' END,
                   closed_at = CASE WHEN end_date < date_trunc('month', %s::date)
                                    THEN (end_date + 5)::timestamp + interval '17 hours' END,
                   closed_by = CASE WHEN end_date < date_trunc('month', %s::date) THEN %s END,
                   locked_at = CASE WHEN end_date < date_trunc('year', %s::date)
                                    THEN (end_date + 40)::timestamp + interval '17 hours' END
        """, (as_of, as_of, as_of, as_of, accountant, as_of))
        cur.execute("UPDATE system.system_parameters SET param_value = %s, valid_from = DATE '2024-01-01' "
                    "WHERE param_code = 'CURRENT_BUSINESS_DATE'", (as_of.isoformat(),))
        for (code, year), value in gen.seq_counters.items():
            if year is None or year == as_of.year:
                cur.execute("UPDATE system.number_sequences SET current_value = %s, last_reset_at = %s "
                            "WHERE sequence_code = %s", (value, as_of, code))


FK_ORPHANS_SQL = """
SELECT c.conname, c.conrelid::regclass::text, c.confrelid::regclass::text,
       array_agg(a.attname ORDER BY k.ord), array_agg(af.attname ORDER BY k.ord)
  FROM pg_constraint c
  JOIN LATERAL unnest(c.conkey, c.confkey) WITH ORDINALITY AS k(ck, fk, ord) ON TRUE
  JOIN pg_attribute a  ON a.attrelid = c.conrelid AND a.attnum = k.ck
  JOIN pg_attribute af ON af.attrelid = c.confrelid AND af.attnum = k.fk
 WHERE c.contype = 'f'
   AND NOT EXISTS (SELECT 1 FROM pg_inherits i WHERE i.inhrelid = c.conrelid)
 GROUP BY c.conname, c.conrelid, c.confrelid
"""


def check_fk_orphans(conn) -> list[tuple[str, int]]:
    """session_replication_role=replica үед FK шалгагдахгүй тул ачаалсны дараа өнчин мөрийг тоолно."""
    bad = []
    with conn.cursor() as cur:
        cur.execute(FK_ORPHANS_SQL)
        for name, child, parent, ccols, pcols in cur.fetchall():
            not_null = " AND ".join(f"c.{col} IS NOT NULL" for col in ccols)
            join = " AND ".join(f"p.{pc} = c.{cc}" for cc, pc in zip(ccols, pcols))
            cur.execute(f"SELECT count(*) FROM {child} c WHERE {not_null} "
                        f"AND NOT EXISTS (SELECT 1 FROM {parent} p WHERE {join})")
            n = cur.fetchone()[0]
            if n:
                bad.append((f"{name} ({child} → {parent})", n))
    return bad


def generate_database(cfg: Config, dbname: str | None = None, seed: int | None = None, scale: float | None = None,
                      variant: Variant | None = None, drop: bool = True, with_t2s: bool = True) -> dict:
    dbname = dbname or cfg["db"]["name"]
    seed = cfg["project"]["seed"] if seed is None else seed
    scale = cfg["data"]["scale"] if scale is None else scale
    as_of = date.fromisoformat(str(cfg["project"]["as_of_date"]))
    start = date.fromisoformat(str(cfg["data"]["history_start"]))
    t0 = time.time()
    create_database(cfg, dbname, drop=drop)
    load_core_schema(cfg, dbname)
    log.info("схем ачаалагдлаа (%.1f с)", time.time() - t0)
    gen = Gen(seed=seed, as_of=as_of, history_start=start, scale=scale, variant=variant)
    with connection(admin_dsn(cfg, dbname)) as conn:
        load_refs(gen, conn)
        add_reference_rows(gen)
        build_employees(gen)
        build_customers(gen)
        build_lending(gen)
        finalize_customer_status(gen)
        build_collateral(gen)
        build_banking(gen)
        build_collection(gen)
        build_accounting(gen)
        write_customer_rows(gen)
        renumber_business_keys(gen)
        apply_sparse(gen)
        log.info("өгөгдөл бэлэн (%.1f с), COPY эхэлж байна", time.time() - t0)
        with conn.cursor() as cur:
            cur.execute("SET session_replication_role = replica")
        counts = gen.store.copy_all(conn)
        with conn.cursor() as cur:
            cur.execute("SET session_replication_role = DEFAULT")
        _post_sql(gen, conn)
        conn.commit()
        orphans = check_fk_orphans(conn)
    if with_t2s:
        load_t2s_layer(cfg, dbname)
    with connection(admin_dsn(cfg, dbname), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("ANALYZE")
    validation = run_validation(cfg, dbname)
    failed = [r for r in validation if r[2] != r[3]]
    log.info("нийт %.1f с", time.time() - t0)
    return {"db": dbname, "seed": seed, "scale": scale, "variant": gen.variant.name, "rows": counts,
            "fk_orphans": orphans, "validation_failed": failed, "seconds": round(time.time() - t0, 1)}
