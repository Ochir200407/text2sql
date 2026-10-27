"""Test-suite accuracy (зааврын 5-р хэсэг: "5–20 синтетик DB хуулбар — NULL, тэнцүү утга, хил, хоосон").

Үндсэн DB дээр санамсаргүй таарсан буруу SQL-ийг илрүүлэхийн тулд өөр seed, өөр тархалттай хуулбар DB-үүд
(nbfi_ts1..N) үүсгэж, gold ба таамаг SQL-ийг БҮХ хуулбар дээр харьцуулна. TS-EX ≤ EX.

Хувилбарууд: NULL олшруулсан, бүдүүн бөөрөнхийлсөн дүн (тэнцүү утга), DPD/огнооны хил (30/31, 90/91, сарын
эхэн/сүүл), зарим хүснэгт хоосон (хассан зээл, AML, бүтцийн өөрчлөлт, батлан даагч, хугацаанаас өмнөх татан
авалт), энгийн өөр seed.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass

from ..config import Config, resolve_path
from ..datagen.context import Variant
from ..db.conn import admin_dsn, bot_dsn, connection
from ..db.executor import Executor
from .compare import results_match

log = logging.getLogger(__name__)

VARIANTS = [
    (1001, Variant("nulls", null_boost=0.15)),
    (1002, Variant("ties", tie_mode=True)),
    (1003, Variant("boundary", boundary_mode=True)),
    (1004, Variant("sparse_a", sparse_tables=["writeoffs", "aml", "guarantors"])),
    (1005, Variant("plain")),
    (1006, Variant("nulls_ties", null_boost=0.08, tie_mode=True)),
    (1007, Variant("sparse_b", sparse_tables=["restructures", "early_withdrawals"])),
    (1008, Variant("boundary_ties", tie_mode=True, boundary_mode=True)),
]

# Хилийн утгын өөрчлөлт (зөвхөн test-suite хуулбар дээр, admin эрхээр). loans.id нь UUID тул hashtext-ээр сонгоно. Мөр бүр тусдаа savepoint-д —
# CHECK constraint зөрчвөл тухайн өөрчлөлтийг алгасна.
BOUNDARY_SQL = [
    # DPD-ийн босго: 25–35 → 30/31, 85–95 → 90/91, 175–185 → 180/181 (max_days_past_due-г дагуулна)
    """UPDATE loan.loans SET days_past_due = CASE WHEN abs(hashtext(id::text)) % 2 = 0 THEN 30 ELSE 31 END,
              max_days_past_due = GREATEST(max_days_past_due, CASE WHEN abs(hashtext(id::text)) % 2 = 0 THEN 30 ELSE 31 END)
        WHERE days_past_due BETWEEN 25 AND 35""",
    """UPDATE loan.loans SET days_past_due = CASE WHEN abs(hashtext(id::text)) % 2 = 0 THEN 90 ELSE 91 END,
              max_days_past_due = GREATEST(max_days_past_due, CASE WHEN abs(hashtext(id::text)) % 2 = 0 THEN 90 ELSE 91 END)
        WHERE days_past_due BETWEEN 85 AND 95""",
    """UPDATE loan.loans SET days_past_due = CASE WHEN abs(hashtext(id::text)) % 2 = 0 THEN 180 ELSE 181 END,
              max_days_past_due = GREATEST(max_days_past_due, CASE WHEN abs(hashtext(id::text)) % 2 = 0 THEN 180 ELSE 181 END)
        WHERE days_past_due BETWEEN 175 AND 185""",
    # Олголтын огноо сарын эхний өдөр (гэрээний огнооноос өмнө орохгүй)
    """UPDATE loan.loans SET disbursement_date = date_trunc('month', disbursement_date)::date
        WHERE abs(hashtext(id::text)) % 10 = 1 AND disbursement_date IS NOT NULL
          AND (contract_date IS NULL OR contract_date <= date_trunc('month', disbursement_date)::date)""",
    # Олголтын огноо сарын сүүлийн өдөр (эхний төлөлт, хаалтаас хойш орохгүй)
    """UPDATE loan.loans SET disbursement_date = (date_trunc('month', disbursement_date)
                                                  + interval '1 month - 1 day')::date
        WHERE abs(hashtext(id::text)) % 10 = 2 AND disbursement_date IS NOT NULL
          AND (first_payment_date IS NULL OR first_payment_date >= (date_trunc('month', disbursement_date)
                                                                    + interval '1 month - 1 day')::date)
          AND (closed_date IS NULL OR closed_date >= (date_trunc('month', disbursement_date)
                                                      + interval '1 month - 1 day')::date)
          AND (maturity_date IS NULL OR maturity_date > (date_trunc('month', disbursement_date)
                                                         + interval '1 month - 1 day')::date)""",
    # Төлбөрийн огноо сарын эхэн/сүүл
    """UPDATE loan.loan_payments SET payment_date = date_trunc('month', payment_date)::date
        WHERE id % 12 = 1""",
    """UPDATE loan.loan_payments SET payment_date = LEAST((date_trunc('month', payment_date)
                                                          + interval '1 month - 1 day')::date, DATE '{as_of}')
        WHERE id % 12 = 2""",
]


@dataclass
class TsDb:
    name: str
    variant: str
    seed: int


def manifest_path(cfg: Config):
    return resolve_path(cfg["knowledge"]["index_dir"]).parent / "testsuite.json"


def apply_boundary(cfg: Config, dbname: str) -> list[str]:
    skipped = []
    with connection(admin_dsn(cfg, dbname)) as conn, conn.cursor() as cur:
        cur.execute("SET session_replication_role = replica")
        for i, sql in enumerate(BOUNDARY_SQL):
            cur.execute("SAVEPOINT b")
            try:
                cur.execute(sql.format(as_of=cfg["project"]["as_of_date"]))
                cur.execute("RELEASE SAVEPOINT b")
            except Exception as e:  # CHECK constraint г.м. — энэ өөрчлөлтийг алгасна
                cur.execute("ROLLBACK TO SAVEPOINT b")
                skipped.append(f"boundary[{i}]: {str(e).splitlines()[0][:120]}")
        cur.execute("SET session_replication_role = DEFAULT")
        conn.commit()
    with connection(admin_dsn(cfg, dbname), autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("ANALYZE")
    return skipped


def build_testsuite(cfg: Config, count: int | None = None, scale: float | None = None) -> dict:
    from ..datagen.runner import generate_database
    tc = cfg["db"]["testsuite"]
    count = int(count or tc["count"])
    scale = float(scale or tc["scale"])
    dbs, report = [], []
    t0 = time.time()
    for i in range(count):
        seed, variant = VARIANTS[i % len(VARIANTS)]
        seed += 100 * (i // len(VARIANTS))
        name = f"{tc['prefix']}{i + 1}"
        res = generate_database(cfg, dbname=name, seed=seed, scale=scale, variant=variant)
        skipped = apply_boundary(cfg, name) if variant.boundary_mode else []
        dbs.append({"name": name, "variant": variant.name, "seed": seed})
        report.append({"db": name, "variant": variant.name, "seed": seed, "rows": sum(res["rows"].values()),
                       "fk_orphans": len(res["fk_orphans"]), "validation_failed": len(res["validation_failed"]),
                       "boundary_skipped": skipped, "seconds": res["seconds"]})
        log.info("test-suite DB %s (%s) бэлэн", name, variant.name)
    path = manifest_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"scale": scale, "as_of": str(cfg["project"]["as_of_date"]), "dbs": dbs},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    return {"manifest": str(path), "dbs": report, "seconds": round(time.time() - t0, 1)}


def load_testsuite(cfg: Config) -> list[TsDb]:
    path = manifest_path(cfg)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [TsDb(d["name"], d["variant"], d["seed"]) for d in data.get("dbs", [])]


class TestSuite:
    """Таамаг SQL-ийг бүх хуулбар DB дээр gold-той харьцуулна (gold-ийн үр дүнг кэшлэнэ)."""

    def __init__(self, cfg: Config, guard, dbs: list[TsDb] | None = None, value_maps: list[dict] | None = None):
        self.cfg = cfg
        self.guard = guard
        self.value_maps = value_maps
        self.dbs = dbs if dbs is not None else load_testsuite(cfg)
        self.executors = {d.name: Executor(bot_dsn(cfg, d.name), statement_timeout_ms=cfg["db"]["statement_timeout_ms"],
                                           max_rows=200000, maxconn=8) for d in self.dbs}
        self._gold_cache: dict[tuple[str, str], object] = {}

    def close(self) -> None:
        for ex in self.executors.values():
            ex.close()

    def _run(self, db: str, sql: str):
        g = self.guard.check(sql)
        if not g.ok:
            return None
        return self.executors[db].run(g.sql)

    def gold(self, db: str, sql: str):
        key = (db, sql)
        if key not in self._gold_cache:
            self._gold_cache[key] = self._run(db, sql)
        return self._gold_cache[key]

    def check(self, pred_sql: str, gold_sqls: list[str], order_matters: bool = False) -> dict:
        """{"ts_correct": bool, "failed_db": [...]} — DB бүр дээр таамаг нь gold-ын аль нэг хувилбартай таарах ёстой."""
        failed = []
        for d in self.dbs:
            pred = self._run(d.name, pred_sql)
            ok = False
            if pred is not None and pred.ok:
                for gs in gold_sqls:
                    gr = self.gold(d.name, gs)
                    if gr is not None and gr.ok and results_match(gr.columns, gr.rows, pred.columns, pred.rows,
                                                                  order_matters, float(self.cfg["eval"]["float_tolerance"]),
                                                                  float(self.cfg["eval"]["relative_tolerance"]),
                                                                  value_maps=self.value_maps):
                        ok = True
                        break
            if not ok:
                failed.append(d.name)
        return {"ts_correct": not failed and bool(self.dbs), "failed_db": failed}

    def gold_errors(self, gold_sqls: list[str]) -> list[str]:
        errs = []
        for d in self.dbs:
            for gs in gold_sqls:
                r = self.gold(d.name, gs)
                if r is None or not r.ok:
                    errs.append(f"{d.name}: {getattr(r, 'error', 'guard')}")
        return errs
