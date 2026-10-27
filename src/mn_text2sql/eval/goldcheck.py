"""Gold SQL-ийн баталгаажуулалт (зааврын 5-р хэсэг: "Gold SQL-ийг гүйцэтгэлээр шалга").

Шалгах зүйлс: бүтэц; SQL хамгаалагчийг давах (зөвхөн t2s, ганц SELECT); bot role-оор алдаагүй гүйцэтгэгдэх;
хариулттай асуулт хоосон биш үр дүнтэй байх; LIMIT-тэй асуулгын тэнцүү утга (tie) — тодорхойгүй эрэмбэ;
хоёрдмол асуултын тайлбарууд өөр өөр үр дүнтэй байх; гүйцэтгэх хугацаа.
"""

from __future__ import annotations

import logging
import time
from collections import Counter

from sqlglot import exp, parse_one

from ..config import Config
from ..db.conn import bot_dsn
from ..db.executor import Executor
from ..knowledge.semantic import cached_model
from ..security.sql_guard import SqlGuard
from .compare import result_hash, results_match
from .dataset import load_items, validate_item

log = logging.getLogger(__name__)


def make_guard(cfg: Config, enforce_limit: int | None = None) -> SqlGuard:
    model = cached_model(cfg["knowledge"]["semantic_model"])
    return SqlGuard(allowed_tables=model.allowed(), schema=model.schema,
                    forbidden_functions=set(cfg["security"]["forbid_functions"]),
                    as_of_date=str(cfg["project"]["as_of_date"]), enforce_limit=enforce_limit)


def _tie_check(executor: Executor, sql: str) -> str | None:
    """LIMIT-тэй асуулга: LIMIT-ийг n+1 болгоход n ба n+1 дэх мөр ORDER BY-ийн гаралтын багана дээр тэнцвэл анхааруулна."""
    try:
        tree = parse_one(sql, read="postgres")
    except Exception:
        return None
    limit = tree.args.get("limit")
    if not isinstance(tree, exp.Select) or limit is None:
        return None
    try:
        n = int(limit.expression.this)
    except (AttributeError, TypeError, ValueError):
        return None
    order = tree.args.get("order")
    if order is None:
        return "LIMIT байгаа боловч ORDER BY байхгүй — үр дүн тодорхойгүй"
    probe = tree.copy()
    probe.set("limit", None)
    probe = probe.limit(n + 1)
    res = executor.run(probe.sql(dialect="postgres"), max_rows=n + 1)
    if not res.ok or res.rowcount <= n:
        return None
    # ORDER BY түлхүүрийг гаралтын баганатай тааруулах (нэр, alias эсвэл байрлалаар)
    out_names = [c.lower() for c in res.columns]
    idx = []
    for o in order.expressions:
        e = o.this
        if isinstance(e, exp.Literal) and e.is_int:
            idx.append(int(e.this) - 1)
        elif isinstance(e, exp.Column) and e.name.lower() in out_names:
            idx.append(out_names.index(e.name.lower()))
        else:
            sql_e = e.sql(dialect="postgres").lower()
            match = [i for i, s in enumerate(tree.expressions) if s.sql(dialect="postgres").lower() == sql_e
                     or (s.alias and s.alias.lower() == sql_e)]
            if match:
                idx.append(match[0])
            else:
                return None
    a, b = res.rows[n - 1], res.rows[n]
    if all(a[i] == b[i] for i in idx):
        return f"{n}-р ба {n + 1}-р мөр ORDER BY түлхүүрээр тэнцүү (tie) — нэмэлт эрэмбийн багана хэрэгтэй"
    return None


def check_gold_files(cfg: Config, files: list[str], testsuite: bool = False, dbname: str | None = None) -> dict:
    files = files or [cfg["eval"]["dev_file"], cfg["eval"]["test_file"]]
    items = load_items(files)
    guard = make_guard(cfg)
    executor = Executor(bot_dsn(cfg, dbname), statement_timeout_ms=cfg["db"]["statement_timeout_ms"],
                        max_rows=200000)
    report = {"files": files, "items": len(items), "errors": [], "warnings": [], "stats": {}}
    ids = Counter(it.id for it in items)
    for dup, n in ids.items():
        if n > 1:
            report["errors"].append({"id": dup, "error": f"давхардсан id ({n})"})
    cat = Counter(it.category for it in items)
    diff = Counter(it.difficulty for it in items if it.category == "answerable")
    slow = []
    for it in items:
        for e in validate_item(it):
            report["errors"].append({"id": it.id, "error": e})
        sqls = []
        if it.category == "answerable":
            sqls = [("gold", it.gold_sql)] + [(f"alt{i}", s) for i, s in enumerate(it.alt_sql)]
        elif it.category == "ambiguous":
            sqls = [(f"interp{i}", x.get("sql")) for i, x in enumerate(it.interpretations)]
        results = {}
        for label, sql in sqls:
            if not sql:
                report["errors"].append({"id": it.id, "error": f"{label}: SQL хоосон"})
                continue
            g = guard.check(sql)
            if not g.ok:
                report["errors"].append({"id": it.id, "error": f"{label}: guard — {g.error}"})
                continue
            t0 = time.perf_counter()
            res = executor.run(g.sql)
            ms = (time.perf_counter() - t0) * 1000
            if not res.ok:
                report["errors"].append({"id": it.id, "error": f"{label}: гүйцэтгэх алдаа — {res.error}"})
                continue
            if ms > 3000:
                slow.append((it.id, round(ms)))
            if (res.rowcount == 0 or all(all(v is None for v in r) for r in res.rows)) and "gold_empty" not in it.tags:
                report["errors"].append({"id": it.id, "error": f"{label}: хоосон үр дүн (эсвэл бүгд NULL)"})
            if res.truncated:
                report["warnings"].append({"id": it.id, "warning": f"{label}: үр дүн хэт олон мөртэй ({res.rowcount}+)"})
            tie = _tie_check(executor, g.sql)
            if tie:
                report["warnings"].append({"id": it.id, "warning": f"{label}: {tie}"})
            results[label] = res
        if it.category == "answerable" and "gold" in results:
            for label, res in results.items():
                if label != "gold" and not results_match(results["gold"].columns, results["gold"].rows, res.columns,
                                                         res.rows, it.order_matters):
                    report["warnings"].append({"id": it.id, "warning": f"{label} нь gold-оос өөр үр дүнтэй"})
            it.raw["_result_hash"] = result_hash(results["gold"].columns, results["gold"].rows, it.order_matters)
            it.raw["_rows"] = results["gold"].rowcount
        if it.category == "ambiguous" and len(results) >= 2:
            labels = list(results)
            same = all(results_match(results[labels[0]].columns, results[labels[0]].rows, results[x].columns,
                                     results[x].rows) for x in labels[1:])
            if same:
                report["errors"].append({"id": it.id, "error": "хоёрдмол асуултын тайлбарууд ижил үр дүнтэй — "
                                                               "хоёрдмол биш байна"})
    executor.close()
    if testsuite:
        # gold SQL нь test-suite хуулбар DB бүр дээр алдаагүй ажиллах ёстой (TS-EX-ийн урьдчилсан нөхцөл)
        from .testsuite import TestSuite
        ts = TestSuite(cfg, guard)
        if not ts.dbs:
            report["errors"].append({"id": "-", "error": "test-suite DB алга (testsuite-build)"})
        for it in items:
            for err in ts.gold_errors(it.gold_sqls()):
                report["errors"].append({"id": it.id, "error": f"test-suite: {err}"})
        report["testsuite_dbs"] = [d.name for d in ts.dbs]
        ts.close()
    report["stats"] = {"category": dict(cat), "difficulty_answerable": dict(diff),
                       "families": len({it.family for it in items}), "slow_queries": slow[:20],
                       "error_count": len(report["errors"]), "warning_count": len(report["warnings"])}
    return report
