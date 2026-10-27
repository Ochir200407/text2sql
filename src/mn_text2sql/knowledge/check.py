"""Мэдлэгийн давхаргыг DB-тэй тулгах (knowledge-check): semantic_model.yaml, glossary.yaml, metrics.yaml.

* semantic model-ийн хүснэгт/багана t2s-д байгаа эсэх, t2s-ийн багана model-д тайлбартай эсэх;
* enum (values) жагсаалт ба өгөгдөлд байгаа утгын зөрүү (өгөгдөлд байгаа боловч тайлбаргүй утга = алдаа);
* FK заалт зөв эсэх; glossary-ийн бүрэн SQL ажиллах эсэх; metrics.yaml-ийн metric × хэмжээс бүр compile, ажиллах эсэх.
"""

from __future__ import annotations

from ..config import Config
from ..db.conn import admin_dsn, bot_dsn, connection
from ..db.executor import Executor
from .semantic import load_glossary, load_semantic_model


def check_knowledge(cfg: Config) -> dict:
    from ..eval.goldcheck import make_guard
    model = load_semantic_model(cfg["knowledge"]["semantic_model"])
    errors, warnings = [], []
    with connection(admin_dsn(cfg)) as conn, conn.cursor() as cur:
        cur.execute("SELECT table_name, column_name FROM information_schema.columns WHERE table_schema = %s",
                    (model.schema,))
        db_cols: dict[str, set[str]] = {}
        for t, c in cur.fetchall():
            db_cols.setdefault(t, set()).add(c)
        for t, tab in model.tables.items():
            if t not in db_cols:
                errors.append(f"{t}: t2s-д view алга")
                continue
            for c in tab.columns:
                if c not in db_cols[t]:
                    errors.append(f"{t}.{c}: t2s view-д багана алга")
            for c in sorted(db_cols[t] - set(tab.columns)):
                warnings.append(f"{t}.{c}: semantic model-д тайлбаргүй багана")
            for c, target, tcol in tab.fks():
                if target not in model.tables or tcol not in model.tables[target].columns:
                    errors.append(f"{t}.{c}: FK {target}.{tcol} model-д алга")
        for t in sorted(set(db_cols) - set(model.tables)):
            warnings.append(f"{t}: t2s view semantic model-д алга")
        # enum утгууд
        for t, tab in model.tables.items():
            if t not in db_cols:
                continue
            for c, col in tab.columns.items():
                if not col.values or c not in db_cols[t]:
                    continue
                cur.execute(f'SELECT DISTINCT "{c}"::text FROM {model.schema}."{t}" WHERE "{c}" IS NOT NULL LIMIT 200')
                present = {r[0] for r in cur.fetchall()}
                undocumented = present - set(map(str, col.values))
                unused = set(map(str, col.values)) - present
                if undocumented:
                    errors.append(f"{t}.{c}: өгөгдөлд тайлбаргүй утга {sorted(undocumented)[:8]}")
                if unused:
                    warnings.append(f"{t}.{c}: өгөгдөлд байхгүй enum утга {sorted(unused)[:8]}")
    guard = make_guard(cfg)
    ex = Executor(bot_dsn(cfg), statement_timeout_ms=cfg["db"]["statement_timeout_ms"])
    n_gloss = 0
    for term in load_glossary(cfg["knowledge"]["glossary"]):
        for t in term.tables:
            if t not in model.tables:
                errors.append(f"glossary {term.id}: үл мэдэгдэх хүснэгт {t}")
        sql = term.sql.strip()
        if sql.upper().startswith(("SELECT", "WITH")) and ":" not in sql:
            n_gloss += 1
            g = guard.check(sql)
            res = ex.run(g.sql) if g.ok else None
            if res is None or not res.ok:
                errors.append(f"glossary {term.id}: SQL ажиллахгүй — {g.error if not g.ok else res.error}")
    n_metric = 0
    try:
        from ..pipeline.router import SemanticRouter, load_metrics
        router = SemanticRouter(cfg, cache={})
        spec = load_metrics(cfg["knowledge"].get("metrics", "knowledge/metrics.yaml"))
        for mid, m in spec["metrics"].items():
            for dims in [[]] + [[d] for d in m["dims"]]:
                sql, why = router.compile({"metric": mid, "dimensions": dims})
                n_metric += 1
                if sql is None:
                    errors.append(f"metrics {mid} {dims}: compile — {why}")
                    continue
                g = guard.check(sql)
                res = ex.run(g.sql) if g.ok else None
                if res is None or not res.ok:
                    errors.append(f"metrics {mid} {dims}: {g.error if not g.ok else res.error}")
    except FileNotFoundError as e:
        warnings.append(f"metrics шалгалт алгасав: {e}")
    ex.close()
    return {"tables": len(model.tables), "columns": sum(len(t.columns) for t in model.tables.values()),
            "glossary_sql_checked": n_gloss, "metric_combos_checked": n_metric, "errors": errors,
            "warnings": warnings}
