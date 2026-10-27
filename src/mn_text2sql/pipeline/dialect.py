"""SQLite → PostgreSQL хөрвүүлэлт (A7: "SQLite-аар үүсгээд sqlglot-оор хөрвүүлэх").

sqlglot нь SQLite-ийн огнооны функцуудыг (date('now', '-30 days'), datetime(), julianday()) PostgreSQL руу
хөрвүүлдэггүй тул энд нэмэлт дүрмээр хөрвүүлнэ. 'now' → CURRENT_DATE / CURRENT_TIMESTAMP болох бөгөөд дараа нь
SqlGuard тэдгээрийг өгөгдлийн "өнөөдөр" (as-of огноо) болгон солино.
"""

from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

_MOD = re.compile(r"^([+-]?\d+(?:\.\d+)?)\s*(day|month|year|hour|minute|second)s?$", re.I)
_START = {"start of month": "month", "start of year": "year", "start of day": "day"}
_NOW = {"now", "today"}


def _is_now(node: exp.Expression | None) -> bool:
    return isinstance(node, exp.Literal) and node.is_string and node.this.strip().lower() in _NOW


def _pg(node: exp.Expression) -> str:
    return node.sql(dialect="postgres")


def _base_sql(arg: exp.Expression | None) -> str:
    if arg is None or _is_now(arg):
        return "CAST(CURRENT_DATE AS TIMESTAMP)"
    return f"CAST({_pg(arg)} AS TIMESTAMP)"


def _apply_mods(base: str, mods: list[exp.Expression]) -> str | None:
    out = base
    for m in mods:
        if not (isinstance(m, exp.Literal) and m.is_string):
            return None
        txt = " ".join(m.this.strip().lower().split())
        if txt in _START:
            out = f"DATE_TRUNC('{_START[txt]}', {out})"
        elif txt in ("localtime", "utc"):
            continue
        else:
            mm = _MOD.match(txt)
            if not mm:
                return None
            n, unit = mm.group(1), mm.group(2).lower()
            sign = "-" if n.startswith("-") else "+"
            out = f"({out} {sign} INTERVAL '{n.lstrip('+-')} {unit}')"
    return out


_FMT_TOKEN = re.compile(r"%.|%|[^%]+")


def quarter_format_pg(fmt: str) -> str | None:
    """strftime-ийн формат ('%Y-Q%q') → PG TO_CHAR-ийн загвар ('YYYY-"Q"Q'): '%q' → Q (улирал 1–4), бусад '%X' нь
    sqlglot-ийн буулгалтаар, үсэгтэй литерал текст давхар хашилтанд (PG-д 'Q', 'Y' зэрэг үсэг нь загварын тэмдэг)."""
    out = []
    for tok in _FMT_TOKEN.findall(fmt):
        if tok == "%q":
            out.append("Q")
        elif tok in ("%%", "%"):
            out.append("%")                     # strftime-ийн '%%' (ба төгсгөлийн ганц '%') → PG-д энгийн '%'
        elif tok.startswith("%"):
            sql = exp.TimeToStr(this=exp.column("x"), format=exp.Literal.string(tok)).sql(dialect="postgres")
            m = re.search(r"'((?:[^']|'')*)'\)$", sql)
            if not m:
                return None
            out.append(m.group(1))
        else:
            out.append(f'"{tok}"' if re.search(r"[A-Za-z]", tok) else tok)
    return "".join(out)


def quarter_to_char(node: exp.TimeToStr, this: exp.Expression) -> exp.Expression | None:
    """'%q'-тэй TimeToStr-ийг PG-ийн TO_CHAR(this, '<загвар>') болгоно (Anonymous — sqlglot форматыг дахин буулгахгүй)."""
    fmt = node.args.get("format")
    if not (isinstance(fmt, exp.Literal) and fmt.is_string and "%q" in fmt.this):
        return None
    pg_fmt = quarter_format_pg(fmt.this)
    if pg_fmt is None:
        return None
    return exp.Anonymous(this="TO_CHAR", expressions=[this, exp.Literal.string(pg_fmt)])


def _quarter_format(node: exp.TimeToStr) -> exp.Expression | None:
    """strftime('%Y-%q', x): SQLite/sqlglot улирлын '%q'-г мэдэхгүй → PG TO_CHAR(x, 'YYYY-%q') нь '%1' гаргадаг.
    Загварын санаа нь улирал тул '%q' → PG-ийн 'Q' (1–4); 'Q%q' → '"Q"Q' («Q1»)."""
    arg = sqlglot.parse_one(_pg(node.this), read="postgres")   # sqlite-ийн strftime аль хэдийн CAST(... AS TIMESTAMP)
    return quarter_to_char(node, arg)


def _convert(node: exp.Expression) -> exp.Expression | None:
    if isinstance(node, exp.TimeToStr):
        return _quarter_format(node)
    if isinstance(node, exp.Date):
        mods = [x for x in [node.args.get("zone"), *(node.expressions or [])] if x is not None]
        body = _apply_mods(_base_sql(node.this), mods)
        if body is None:
            return None
        if not mods and not _is_now(node.this):
            return sqlglot.parse_one(f"CAST({_pg(node.this)} AS DATE)", read="postgres")
        return sqlglot.parse_one(f"CAST({body} AS DATE)", read="postgres")
    if isinstance(node, exp.Anonymous):
        name = (node.name or "").lower()
        args = list(node.expressions or [])
        if name == "datetime" and args:
            body = _apply_mods(_base_sql(args[0]) if not _is_now(args[0]) else "CAST(CURRENT_TIMESTAMP AS TIMESTAMP)",
                               args[1:])
            return None if body is None else sqlglot.parse_one(f"CAST({body} AS TIMESTAMP)", read="postgres")
        if name == "julianday" and args:
            body = _apply_mods(_base_sql(args[0]), args[1:])
            if body is None:
                return None
            return sqlglot.parse_one(f"(EXTRACT(EPOCH FROM {body}) / 86400.0 + 2440587.5)", read="postgres")
    if isinstance(node, exp.TsOrDsToTimestamp) and _is_now(node.this):
        return sqlglot.parse_one("CAST(CURRENT_DATE AS TIMESTAMP)", read="postgres")
    if isinstance(node, exp.Cast) and _is_now(node.this):
        to = node.to.sql(dialect="postgres").upper() if node.to is not None else ""
        if to.startswith("DATE"):
            return exp.CurrentDate()
        if to.startswith("TIMESTAMP"):
            return exp.CurrentTimestamp()
    return None


def _bool_literal(node: exp.Expression, bool_cols: frozenset[str]) -> exp.Expression | None:
    """SQLite-ийн 0/1 логик утга: boolean баганатай `= 1`, `<> 0`, `IN (0, 1)` → TRUE/FALSE (PG: boolean = integer алдаа)."""
    def lit(x: exp.Expression) -> exp.Expression | None:
        if isinstance(x, exp.Literal) and not x.is_string and x.this in ("0", "1"):
            return exp.true() if x.this == "1" else exp.false()
        return None

    def is_bool(x: exp.Expression) -> bool:
        return isinstance(x, exp.Column) and x.name.lower() in bool_cols

    if isinstance(node, (exp.EQ, exp.NEQ)):
        left, right = node.this, node.expression
        if is_bool(left) and (b := lit(right)) is not None:
            return node.__class__(this=left.copy(), expression=b)
        if is_bool(right) and (b := lit(left)) is not None:
            return node.__class__(this=b, expression=right.copy())
    if isinstance(node, exp.In) and is_bool(node.this) and node.expressions:
        vals = [lit(v) for v in node.expressions]
        if all(v is not None for v in vals):
            return exp.In(this=node.this.copy(), expressions=vals)
    return None


def sqlite_to_postgres(sql: str, bool_cols: frozenset[str] = frozenset()) -> str:
    """SQLite SQL → PostgreSQL. Задлах боломжгүй бол эх SQL-ийг буцаана (guard/DB алдаа өгнө).
    bool_cols: DB-ийн boolean баганын нэрс (жижиг үсгээр) — 0/1-ийг TRUE/FALSE болгоно."""
    try:
        tree = sqlglot.parse_one(sql, read="sqlite")
    except Exception:
        return sql
    if tree is None:
        return sql
    # Доороос дээш: дотоод функцуудыг эхэлж хөрвүүлнэ
    for node in reversed(list(tree.walk())):
        new = _convert(node)
        if new is None and bool_cols:
            new = _bool_literal(node, bool_cols)
        if new is not None:
            if node is tree:
                tree = new
            else:
                node.replace(new)
    try:
        return tree.sql(dialect="postgres")
    except Exception:
        return sql
