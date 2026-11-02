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


def _strftime_mods(node: exp.Anonymous) -> exp.Expression | None:
    """F5 (transpile_fix): strftime(fmt, x, modifier, …) — sqlglot хувиргадаггүй (PG: «function strftime(unknown, date,
    unknown) does not exist»). Modifier-ийг date()/datetime()-тэй ижил дүрмээр хэрэглээд TO_CHAR; SQLite-д хүчингүй
    modifier («start of quarter», «end of year») бол хөндөхгүй (загварын алдаа хэвээр)."""
    args = list(node.expressions or [])
    if len(args) < 3 or not (isinstance(args[0], exp.Literal) and args[0].is_string):
        return None
    base = "CAST(CURRENT_TIMESTAMP AS TIMESTAMP)" if _is_now(args[1]) else _base_sql(args[1])
    body = _apply_mods(base, args[2:])
    if body is None:
        return None
    inner = sqlglot.parse_one(body, read="postgres")
    tts = exp.TimeToStr(this=inner, format=args[0].copy())
    return quarter_to_char(tts, inner) if "%q" in args[0].this else tts


def _convert(node: exp.Expression, fix: bool = False) -> exp.Expression | None:
    if isinstance(node, exp.TimeToStr):
        return _quarter_format(node)
    if fix and isinstance(node, exp.Anonymous) and (node.name or "").lower() == "strftime":
        return _strftime_mods(node)
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


# ---------------------------------------------------------------- transpile_fix (RunOptions.transpile_fix, анхдагч off)
# SQLite-д хүчинтэй боловч хөрвүүлгийн дараа PostgreSQL-д алдаа өгдөг бүтэц (dev-ийн гүйцэтгэлийн алдаанаас, асуултын
# агуулгад тааруулаагүй ерөнхий дүрэм):
#   F1 WHERE/HAVING дахь SELECT-ийн alias («HAVING npl_ratio > 15» — SQLite alias-ийг таньдаг, PG үгүй) → alias-ийн
#      илэрхийлэл (нэр нь DB-ийн/CTE-ийн багана биш үед л — SQLite ч FROM-ийн баганыг alias-аас түрүүлж таньдаг)
#   F2 strftime (зөвхөн тоон формат) / substr-ийн текстийг арифметик эсвэл тоотой харьцуулалтад → CAST(… AS INT)
#      (SQLite текстийг автоматаар тоо болгодог: «(strftime('%m', d) - 1) / 3 + 1»; PG: «text - integer»)
#   F3 substr(огноо/тоон багана, …) → substr(CAST(… AS TEXT), …) (SQLite-д огноо нь текст; PG: «substring(date, …)»)
#   F4 boolean багана / нөхцөл SUM, AVG, арифметикт → CAST(… AS INT); MAX/MIN(boolean) → BOOL_OR/BOOL_AND (SQLite 0/1)
#   F5 strftime(fmt, x, modifier…) → modifier-ийг хэрэглээд TO_CHAR (_strftime_mods)
_NUM_FMT = re.compile(r"^(?:%[YmdHMSjWwsuyekIlGgV])+$")
_ARITH = (exp.Add, exp.Sub, exp.Mul, exp.Div, exp.Mod, exp.IntDiv)
_CMP = (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)
_PRED = (*_CMP, exp.And, exp.Or, exp.Not, exp.Is, exp.In, exp.Like, exp.ILike, exp.Between)
_NUMERIC_PG = ("smallint", "integer", "bigint", "numeric", "real", "double precision", "decimal")
_TEXT_PG = ("text", "character varying", "character", "varchar", "char", "bpchar")


def column_types(types: dict[str, str] | None) -> dict[str, frozenset[str]]:
    """{"хүснэгт.багана": PG төрөл} (retrieval-ийн column_examples.json __types__) → {багана: {төрлүүд}} (жижиг үсгээр)."""
    out: dict[str, set[str]] = {}
    for key, typ in (types or {}).items():
        col = str(key).split(".")[-1].lower()
        out.setdefault(col, set()).add(str(typ).lower())
    return {k: frozenset(v) for k, v in out.items()}


def _unparen(node: exp.Expression | None) -> exp.Expression | None:
    while isinstance(node, exp.Paren):
        node = node.this
    return node


def _numeric_operand(node: exp.Expression | None, numeric_cols: frozenset[str]) -> bool:
    node = _unparen(node)
    if isinstance(node, exp.Literal):
        return not node.is_string
    if isinstance(node, (*_ARITH, exp.Neg)):
        return True
    if isinstance(node, exp.Cast):
        return node.to is not None and node.to.is_type(*exp.DataType.NUMERIC_TYPES)
    return isinstance(node, exp.Column) and (node.name or "").lower() in numeric_cols


def _numeric_context(node: exp.Expression, numeric_cols: frozenset[str]) -> bool:
    child, parent = node, node.parent
    while isinstance(parent, exp.Paren):
        child, parent = parent, parent.parent
    if isinstance(parent, (*_ARITH, exp.Neg)):
        return True
    if isinstance(parent, _CMP):
        other = parent.expression if parent.this is child else parent.this
        return _numeric_operand(other, numeric_cols)
    if isinstance(parent, exp.Between) and parent.this is child:
        return _numeric_operand(parent.args.get("low"), numeric_cols) or \
            _numeric_operand(parent.args.get("high"), numeric_cols)
    if isinstance(parent, exp.In) and parent.this is child and parent.expressions:
        return all(_numeric_operand(e, numeric_cols) for e in parent.expressions)
    return False


def _texty_number(node: exp.Expression) -> bool:
    if isinstance(node, exp.TimeToStr):
        fmt = node.args.get("format")
        return isinstance(fmt, exp.Literal) and fmt.is_string and bool(_NUM_FMT.match(fmt.this))
    return isinstance(node, exp.Substring)


def _wrap(node: exp.Expression) -> exp.Expression:
    """Нарийн илэрхийллийг багана/функцийн оронд тавихад үйлдлийн дарааллыг хадгална."""
    if isinstance(node, (exp.Column, exp.Literal, exp.Func, exp.Paren, exp.Cast)):
        return node
    return exp.Paren(this=node)


def _inline_select_aliases(tree: exp.Expression, known_cols: frozenset[str]) -> None:
    """F1: WHERE/HAVING дахь (тодотголгүй) баганын нэр нь тухайн SELECT-ийн alias бөгөөд DB-ийн багана биш, бусад
    SELECT/CTE-ийн гаралтын нэр ч биш бол alias-ийн илэрхийллээр солино."""
    selects = list(tree.find_all(exp.Select))
    outputs = {id(s): {(e.alias_or_name or "").lower() for e in s.expressions} for s in selects}
    for sel in selects:
        aliases = {e.alias.lower(): e.this for e in sel.expressions if isinstance(e, exp.Alias) and e.alias}
        if not aliases:
            continue
        others = set().union(*[v for k, v in outputs.items() if k != id(sel)])
        for clause in ("where", "having"):
            part = sel.args.get(clause)
            if part is None:
                continue
            for col in list(part.find_all(exp.Column)):
                name = (col.name or "").lower()
                if col.table or name not in aliases or name in known_cols or name in others:
                    continue
                if col.find_ancestor(exp.Select) is not sel:
                    continue                     # дэд асуулгын багана
                target = aliases[name]
                if target.find(exp.Window) or (clause == "where" and target.find(exp.AggFunc)):
                    continue                     # PG-д WHERE-д агрегат/window хүчингүй — SQLite ч алдаа өгнө
                if col.find_ancestor(exp.AggFunc) is not None and target.find(exp.AggFunc):
                    continue                     # агрегат дотор агрегат
                col.replace(_wrap(target.copy()))


def _sqlite_semantics(tree: exp.Expression, bool_cols: frozenset[str], col_types: dict[str, frozenset[str]],
                      known_cols: frozenset[str]) -> exp.Expression:
    numeric_cols = frozenset(c for c, ts in col_types.items() if ts and all(t in _NUMERIC_PG for t in ts))
    nontext_cols = frozenset(c for c, ts in col_types.items() if ts and not any(t in _TEXT_PG for t in ts))
    if known_cols:
        _inline_select_aliases(tree, known_cols)                                   # F1
    for sub in list(tree.find_all(exp.Substring)):                                 # F3
        arg = sub.this
        if isinstance(arg, exp.Column) and (arg.name or "").lower() in nontext_cols:
            arg.replace(exp.cast(arg.copy(), "text"))
    for node in [n for n in tree.find_all(exp.TimeToStr, exp.Substring) if _texty_number(n)]:   # F2
        if _numeric_context(node, numeric_cols):
            node.replace(exp.cast(node.copy(), "int"))
    if bool_cols:                                                                   # F4
        for agg in list(tree.find_all(exp.Sum, exp.Avg)):
            arg = agg.this
            if (isinstance(arg, exp.Column) and (arg.name or "").lower() in bool_cols) or isinstance(arg, _PRED):
                arg.replace(exp.cast(arg.copy(), "int"))
        for agg in list(tree.find_all(exp.Max, exp.Min)):
            arg = agg.this
            if isinstance(arg, exp.Column) and (arg.name or "").lower() in bool_cols and not agg.expressions:
                agg.replace(exp.Anonymous(this="BOOL_OR" if isinstance(agg, exp.Max) else "BOOL_AND",
                                          expressions=[arg.copy()]))
        for col in list(tree.find_all(exp.Column)):
            if (col.name or "").lower() in bool_cols and isinstance(col.parent, _ARITH):
                col.replace(exp.cast(col.copy(), "int"))
    return tree


def sqlite_to_postgres(sql: str, bool_cols: frozenset[str] = frozenset(), fix: bool = False,
                       col_types: dict[str, frozenset[str]] | None = None,
                       known_cols: frozenset[str] | None = None) -> str:
    """SQLite SQL → PostgreSQL. Задлах боломжгүй бол эх SQL-ийг буцаана (guard/DB алдаа өгнө).
    bool_cols: DB-ийн boolean баганын нэрс (жижиг үсгээр) — 0/1-ийг TRUE/FALSE болгоно.
    fix (RunOptions.transpile_fix, анхдагч False = хуучин гаралт байт-байтаар): F1–F5 (дээрх тайлбар); col_types —
    column_types()-ийн {багана: {PG төрөл}}, known_cols — DB-ийн бүх баганын нэр (F1-д заавал; байхгүй бол F1 алгасна)."""
    try:
        tree = sqlglot.parse_one(sql, read="sqlite")
    except Exception:
        return sql
    if tree is None:
        return sql
    # Доороос дээш: дотоод функцуудыг эхэлж хөрвүүлнэ
    for node in reversed(list(tree.walk())):
        new = _convert(node, fix)
        if new is None and bool_cols:
            new = _bool_literal(node, bool_cols)
        if new is not None:
            if node is tree:
                tree = new
            else:
                node.replace(new)
    if fix:
        try:
            col_types = col_types or {}
            tree = _sqlite_semantics(tree, bool_cols, col_types, frozenset(known_cols or ()) | frozenset(col_types))
        except Exception:  # noqa: BLE001 — засвар амжилтгүй бол хуучин хөрвүүлэлт (гаралт өөрчлөгдөхгүй)
            return sqlite_to_postgres(sql, bool_cols)
    try:
        return tree.sql(dialect="postgres")
    except Exception:
        return sql
