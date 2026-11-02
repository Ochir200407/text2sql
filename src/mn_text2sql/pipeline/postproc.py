"""PostgreSQL SQL-ийн детерминист засвар (H бүлэг) — DB-ийн схем ба хадгалагдсан утгаас гаргасан ерөнхий дүрэм.

Асуулт тус бүрийн дүрэм БИШ: зөвхөн схем (баганын төрөл, *_code ↔ *_name хос) ба цөөн утгатай текст баганын
хадгалагдсан утгыг ашиглана (production систем ч мөн адил DB-ээс уншиж чадна).
  R2  код ↔ нэр баганын солилт: `status_name = 'CLOSED'` (утга нь зөвхөн status_code-д байгаа) → `status_code = 'CLOSED'`
  R3  огнооны баганад LIKE: `disbursed_date LIKE '2026-03%'` → `CAST(disbursed_date AS TEXT) LIKE ...`
  R4  ROUND(double, n) PG-д байхгүй: `ROUND(x, 2)` → `ROUND(CAST(x AS NUMERIC), 2)`
  R5  том/жижиг үсгийн зөрүү: `status_code = 'closed'` → хадгалагдсан `'CLOSED'` (баганын бүх утгатай давхцах үед)
  R6  (RunOptions.literal_fix, анхдагч off; apply(sql, hits=…) үед л) DB-д байхгүй текст утга: en_prompt орчуулгаас болж
      загвар хадгалагдсан монгол утгын оронд англи/галиг нэр бичдэг (`branch_name = 'Darkhan'`, `product_name = 'Term
      Deposit'` — prompt-д «Stored value 'Дархан салбар' exists in branches.branch_name» байхад). Тухайн асуултын утгын
      хайлтын олдвороос (hits) ИЖИЛ баганын хадгалагдсан утгаар солино: нэг утга → түүгээр; олон бол оноо нь бусдаасаа
      дээгүүр нэг нь; IN жагсаалтын k буруу утга ↔ яг k олдвор → бүгдийг. Нэг баганад өөр өөр буруу утга олон нөхцөлд
      (CASE WHEN … = 'A' … = 'B') байвал хаана нь юу болохыг мэдэхгүй тул солихгүй.
  R7  (RunOptions.trunc_date, анхдагч off; cast_date_trunc — DB шаардахгүй) SELECT-ийн гаралтын `DATE_TRUNC('month', d)`
      → `CAST(DATE_TRUNC('month', d) AS DATE)` (нэгж day…millennium). PG-д date_trunc(DATE) нь timestamptz буцаадаг тул
      UTC сесст «2026-01-01 00:00+00» → UB-ийн 08:00 болж сарын эхний огноотой (gold-ийн `::date`) таарахгүй. Зөвхөн
      гаралтын баганыг (CTE/дэд асуулгынх ч) — WHERE/GROUP BY/ORDER BY/window хэвээр; DISTINCT ба UNION-ий SELECT-ийг
      хөндөхгүй (ORDER BY-ийн илэрхийлэл/баганын төрөл зөрөхөөс сэргийлж).
"""

from __future__ import annotations

import logging
import threading

import sqlglot
from sqlglot import exp

log = logging.getLogger(__name__)

_DATE_TYPES = ("date", "timestamp with time zone", "timestamp without time zone")
_TEXT_TYPES = ("text", "character varying", "character")
MAX_DISTINCT = 60


class SqlPostprocessor:
    """Executor-оор схемийн мэдээллийг нэг удаа уншиж кэшилнэ (thread-safe); apply() нь SQL → (SQL, дүрмүүд)."""

    def __init__(self, executor):
        self.executor = executor
        self._lock = threading.Lock()
        self._ready = False
        self.tcols: dict[str, dict[str, str]] = {}            # хүснэгт → {багана: төрөл}
        self.pairs: dict[tuple[str, str], tuple[str, set]] = {}   # (хүснэгт, багана) → (хос багана, хосын утгууд)
        self.distinct: dict[tuple[str, str], set] = {}        # (хүснэгт, багана) → хадгалагдсан утгууд (≤ 60)

    def _q(self, sql: str):
        res = self.executor.run(sql, max_rows=100000)
        return res.rows if res.ok else []

    def _load(self) -> None:
        with self._lock:
            if self._ready:
                return
            rows = self._q("SELECT table_schema, table_name, column_name, data_type FROM information_schema.columns "
                           "WHERE table_schema NOT IN ('pg_catalog', 'information_schema')")
            schema_of: dict[str, str] = {}
            for s, t, c, dt in rows:
                self.tcols.setdefault(t.lower(), {})[c.lower()] = dt
                schema_of[t.lower()] = s
            for t, cols in self.tcols.items():
                s = schema_of[t]
                for c, dt in cols.items():
                    if dt not in _TEXT_TYPES:
                        continue
                    n = self._q(f'SELECT count(DISTINCT "{c}") FROM "{s}"."{t}"')
                    if n and n[0][0] and n[0][0] <= MAX_DISTINCT:
                        self.distinct[(t, c)] = {r[0] for r in self._q(
                            f'SELECT DISTINCT "{c}" FROM "{s}"."{t}" WHERE "{c}" IS NOT NULL')}
                for c in cols:
                    if not c.endswith("_code"):
                        continue
                    for nc in (c[:-5] + "_name", c[:-5] + "_name_mn"):
                        if nc in cols:
                            got = self._q(f'SELECT DISTINCT "{c}"::text, "{nc}"::text FROM "{s}"."{t}" '
                                          f'WHERE "{c}" IS NOT NULL')
                            self.pairs[(t, nc)] = (c, {a for a, _ in got})
                            self.pairs[(t, c)] = (nc, {b for _, b in got})
            self._ready = True
            log.info("postproc: %d хүснэгт, %d код/нэр хос, %d цөөн утгатай багана",
                     len(self.tcols), len(self.pairs) // 2, len(self.distinct))

    def _resolve(self, col: exp.Column, alias_map: dict[str, str], tables: list[str]) -> str | None:
        q = (col.table or "").lower()
        if q:
            return alias_map.get(q)
        cands = [t for t in tables if col.name.lower() in self.tcols.get(t, {})]
        return cands[0] if len(cands) == 1 else None

    def apply(self, sql: str, hits: list | None = None) -> tuple[str, list[str]]:
        """hits: [(хүснэгт, багана, утга, оноо)] — тухайн асуултын утгын хайлтын олдвор (R6; None бол R6 алгасна)."""
        if not sql:
            return sql, []
        self._load()
        try:
            tree = sqlglot.parse_one(sql, read="postgres")
        except Exception:
            return sql, []
        if tree is None:
            return sql, []
        applied: list[str] = []
        alias_map: dict[str, str] = {}
        tables: list[str] = []
        for t in tree.find_all(exp.Table):
            if t.name:
                alias_map[(t.alias_or_name or t.name).lower()] = t.name.lower()
                tables.append(t.name.lower())
        for node in list(tree.find_all(exp.EQ, exp.NEQ, exp.In)):
            col = node.this
            lits = list(node.expressions) if isinstance(node, exp.In) else [node.expression]
            if not isinstance(col, exp.Column) or not lits or \
                    not all(isinstance(x, exp.Literal) and x.is_string for x in lits):
                continue
            t = self._resolve(col, alias_map, tables)
            if not t:
                continue
            cname = col.name.lower()
            vals = [x.this for x in lits]
            dv = self.distinct.get((t, cname))
            if dv is not None and all(v in dv for v in vals):
                continue
            pr = self.pairs.get((t, cname))
            if pr is not None:
                other, other_vals = pr
                odv = self.distinct.get((t, other)) or other_vals
                if all(v in odv for v in vals):
                    col.set("this", exp.to_identifier(other))
                    applied.append("R2")
                    continue
            if dv is not None:
                low = {x.lower(): x for x in dv if isinstance(x, str)}
                if all(v.lower() in low for v in vals):
                    for x in lits:
                        x.set("this", low[x.this.lower()])
                    applied.append("R5")
        for node in list(tree.find_all(exp.Like, exp.ILike)):
            col = node.this
            if isinstance(col, exp.Column):
                t = self._resolve(col, alias_map, tables)
                if t and self.tcols.get(t, {}).get(col.name.lower()) in _DATE_TYPES:
                    node.set("this", exp.cast(col.copy(), "text"))
                    applied.append("R3")
        for node in list(tree.find_all(exp.Round)):
            dec = node.args.get("decimals")
            inner = node.this
            if dec is not None and not (isinstance(inner, exp.Cast) and
                                        inner.to.sql().upper().startswith(("NUMERIC", "DECIMAL"))):
                node.set("this", exp.cast(inner.copy(), "numeric"))
                applied.append("R4")
        if hits:
            applied += self._literal_fix(tree, alias_map, tables, hits)
        if not applied:
            return sql, []
        try:
            return tree.sql(dialect="postgres"), applied
        except Exception:
            return sql, []

    def _literal_fix(self, tree: exp.Expression, alias_map: dict[str, str], tables: list[str],
                     hits: list) -> list[str]:
        """R6 (модулийн docstring). R2/R5-ийн дараа ажиллана — тэдгээрийн зассан утга аль хэдийн хадгалагдсан."""
        preds = []                     # (node, хүснэгт, багана, бүх литерал, хадгалагдаагүй литерал)
        bad_by_col: dict[tuple[str, str], set[str]] = {}
        for node in tree.find_all(exp.EQ, exp.NEQ, exp.In):
            col = node.this
            lits = list(node.expressions) if isinstance(node, exp.In) else [node.expression]
            if not isinstance(col, exp.Column) or not lits or \
                    not all(isinstance(x, exp.Literal) and x.is_string for x in lits):
                continue
            t = self._resolve(col, alias_map, tables)
            dv = self.distinct.get((t, col.name.lower())) if t else None
            if not dv:
                continue
            bad = [x for x in lits if x.this not in dv]
            if bad:
                preds.append((node, t, col.name.lower(), lits, bad))
                bad_by_col.setdefault((t, col.name.lower()), set()).update(x.this for x in bad)
        applied = []
        for _node, t, c, lits, bad in preds:
            dv = self.distinct[(t, c)]
            n_bad = len(bad_by_col[(t, c)])
            if n_bad > len({x.this for x in bad}):
                continue               # өөр нөхцөлд өөр буруу утга — аль нь аль болохыг мэдэхгүй
            present = {x.this for x in lits if x.this in dv}
            cands: dict[str, float] = {}
            for h in hits:
                try:
                    ht, hc, hv, hs = str(h[0]).lower(), str(h[1]).lower(), h[2], float(h[3])
                except (IndexError, TypeError, ValueError):
                    continue
                if (ht, hc) == (t, c) and hv in dv and hv not in present:
                    cands[hv] = max(hs, cands.get(hv, float("-inf")))
            ranked = sorted(cands.items(), key=lambda kv: -kv[1])
            if len(bad) == 1:
                if not ranked or (len(ranked) > 1 and ranked[0][1] <= ranked[1][1]):
                    continue           # олдворгүй эсвэл тэнцүү оноотой хэд хэдэн утга
                bad[0].set("this", ranked[0][0])
            elif len(ranked) == len(bad):
                for x, (v, _) in zip(bad, ranked, strict=True):
                    x.set("this", v)
            else:
                continue
            applied.append("R6")
        return applied


_TRUNC_UNITS = frozenset({"day", "week", "month", "quarter", "year", "decade", "century", "millennium"})


def _is_day_or_coarser_trunc(node: exp.Expression) -> bool:
    """DATE_TRUNC('<нэгж>', x) мөн нэгж нь өдөр ба түүнээс бүдүүн эсэх (цаг/минутыг хөндөхгүй)."""
    if isinstance(node, (exp.TimestampTrunc, exp.DateTrunc)):
        unit = node.args.get("unit")
        return unit is not None and unit.name.strip("'").lower() in _TRUNC_UNITS
    if isinstance(node, exp.Anonymous) and node.name.upper() == "DATE_TRUNC" and node.expressions:
        u = node.expressions[0]
        return isinstance(u, exp.Literal) and u.is_string and u.this.lower() in _TRUNC_UNITS
    return False


def _in_set_operation(sel: exp.Select) -> bool:
    node = sel.parent
    while isinstance(node, (exp.Subquery, exp.Paren)):
        node = node.parent
    return isinstance(node, (exp.Union, exp.Intersect, exp.Except))


def cast_date_trunc(sql: str) -> tuple[str, list[str]]:
    """R7 (модулийн docstring): SELECT-ийн гаралтын DATE_TRUNC-ийг DATE болгоно. Буцаах: (SQL, ["R7"] эсвэл [])."""
    if not sql or "date_trunc" not in sql.lower():
        return sql, []
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except Exception:
        return sql, []
    if tree is None:
        return sql, []
    n = 0
    for sel in list(tree.find_all(exp.Select)):
        if sel.args.get("distinct") is not None or _in_set_operation(sel):
            continue
        for e in sel.expressions:
            inner = e.this if isinstance(e, exp.Alias) else e
            if not _is_day_or_coarser_trunc(inner):
                continue
            cast = exp.cast(inner.copy(), "date")
            if isinstance(e, exp.Alias):
                e.set("this", cast)
            else:
                e.replace(cast)
            n += 1
    if not n:
        return sql, []
    try:
        return tree.sql(dialect="postgres"), ["R7"]
    except Exception:
        return sql, []


__all__ = ["SqlPostprocessor", "cast_date_trunc"]
