"""E4: санал хураалтын өмнө хувилбар шүүх ба дүрмийн татгалзал/тодруулга (E2 dev алдааны шинжилгээ, 2026-10-18).

* degenerate — үр дүн хоосон, бүх утга NULL, эсвэл ганц мөрийн бүх утга 0/NULL. Eval-ийн gold хэзээ ч ийм биш
  (check-gold хоосон gold-ыг хориглодог), загварын ийм хариулт ихэвчлэн буруу шүүлтээс үүсдэг.
* bad_literals — текст баганатай `=`/`IN`/`LIKE` харьцуулсан утга тухайн баганад огт байхгүй (DB-ээр шалгана):
  буруу хувилбарын 36/124, зөвийн 2/100-д ийм утга байсан.
* REFUSE_RULES / CLARIFY_RULES — evalset/AUTHORING.md-ийн хариулах боломжгүй (missing_data, non_sql,
  external_knowledge, future, write) ба хоёрдмол (бүдэг хугацаа, хэмжүүргүй «хамгийн», бүдэг чанар) ангиллын
  түлхүүр үгс. dev-ийг уншсаны дараа бичсэн тул test дээр бага үр өгч болно (тайланд тэмдэглэнэ).
"""

from __future__ import annotations

import re
import threading
from decimal import Decimal

import sqlglot
from sqlglot import exp

from ..text.normalize import cyr_key

REFUSE_RULES: dict[str, list[str]] = {
    "missing_data": [r"(?<!\S)цалин(?!гийн зээл|тай|г?аас суутгал)", r"түрээс", r"сэтгэл ханамж", r"\broe\b",
                     r"өрсөлдөгч"],
    "non_sql": [r"гэрээ (бич|боловсруул)", r"имэйл (бич|зохио)", r"зөвлөмж (өг|бич|боловсруул)", r"зөвлөгөө өг",
                r"бичиж өг", r"зохиож өг"],
    "external_knowledge": [r"бодлогын хүү", r"инфляц", r"хуул(иар|ийн заалт)", r"иргэний хууль"],
    "future": [r"болох (вэ|бол)", r"\bбол\?", r"таамаг"],
    "write_operation": [r"сунга(ад|ж|х|аарай)", r"хаагаад", r"\bтавь\b"],
}
REFUSE_MN = {
    "missing_data": "Энэ мэдээлэл өгөгдлийн санд байхгүй тул хариулах боломжгүй.",
    "non_sql": "Энэ нь өгөгдлийн сангаас мэдээлэл авах асуулт биш (бичвэр/зөвлөгөө) тул гүйцэтгэхгүй.",
    "external_knowledge": "Гадны мэдээлэл (бодлогын хүү, инфляц, хууль) өгөгдлийн санд байхгүй.",
    "future": "Ирээдүйн таамаглалыг өгөгдлийн сангаас гаргах боломжгүй.",
    "write_operation": "Систем зөвхөн мэдээлэл унших эрхтэй — өөрчлөх хүсэлтийг гүйцэтгэхгүй.",
    "no_table": "Загвар өгөгдлийн сангаас бус тогтмол утга буцаасан — хариулах боломжгүй гэж үзэв.",
}
CLARIFY_RULES = [r"сүүлийн (\d+|хоёр|гурав|гурван|зургаа|зургаан) сар", r"сүүлийн улирал",
                 r"хамгийн (том|сайн|ашигтай|муу) (\d+ )?(байгууллага|харилцагч|салбар|зээл)",
                 r"хамгийн сайн ажилла", r"асуудалтай (зээл|харилцагч)", r"эрсдэлтэй (харилцагч|зээл)"]
# Тодорхой огноо/сар эсвэл «...-аар хамгийн» (хэмжүүр заасан) байвал бүдэг биш
_CLARIFY_RESOLVED = [r"\d+[- ]?р сар", r"\d{4}-\d{2}", r"\w+(аар|ээр|оор|өөр) хамгийн"]
CLARIFY_MN = ("Асуулт тодорхой бус байна (хугацаа эсвэл хэмжүүр). Аль хугацаа (жишээ нь сүүлийн 90 хоног эсвэл "
              "4–6-р сар), аль үзүүлэлтээр (дүн, тоо, үлдэгдэл) харьцуулахыг тодруулна уу.")


def rule_decision(question: str, refuse: bool = True, clarify: bool = True) -> tuple[str, str] | None:
    """(action, reason) — "refuse"/"clarify", эсвэл None. Кирилл асуултыг cyr_key-ээр шалгана."""
    q = cyr_key(question or "")
    if refuse:
        for reason, pats in REFUSE_RULES.items():
            if any(re.search(p, q) for p in pats):
                return "refuse", reason
    if clarify and any(re.search(p, q) for p in CLARIFY_RULES) and not any(re.search(p, q) for p in _CLARIFY_RESOLVED):
        return "clarify", "vague"
    return None


def is_fromless(sql: str | None) -> bool:
    """Хүснэгтгүй SELECT (жишээ нь `SELECT 3`) — загвар DB-ээс бус тогтмол утга таасан."""
    if not sql:
        return False
    try:
        return not list(sqlglot.parse_one(sql, read="postgres").find_all(exp.Table))
    except Exception:
        return False


def _zero(v) -> bool:
    return v is None or (isinstance(v, (int, float, Decimal)) and not isinstance(v, bool) and v == 0)


def is_degenerate(res) -> bool:
    """Хоосон, бүх утга NULL, эсвэл ганц мөрийн бүх утга 0/NULL."""
    if res is None or not res.ok:
        return False
    rows = list(res.rows or [])
    if res.rowcount == 0 or not rows:
        return True
    if all(v is None for row in rows for v in row):
        return True
    return res.rowcount == 1 and all(_zero(v) for v in rows[0])


_TEXTY = ("text", "character varying", "character", "USER-DEFINED")


class LiteralChecker:
    """`col = 'x'`, `col IN ('x', ...)`, `col LIKE 'x'` — текст утга тухайн view-ийн баганад байгаа эсэх (кэштэй)."""

    def __init__(self, executor, schema: str = "t2s", enums: dict | None = None):
        self.ex = executor
        self.schema = schema
        self.enums = enums or {}   # {(view, багана): {хүчинтэй код}} — өгөгдөлгүй ч зөв кодыг буруу гэж үзэхгүй
        self._cols: dict[str, dict[str, str]] | None = None
        self._cache: dict[tuple, bool] = {}
        self._lock = threading.Lock()

    def _columns(self) -> dict[str, dict[str, str]]:
        with self._lock:
            if self._cols is None:
                cols: dict[str, dict[str, str]] = {}
                r = self.ex.run("SELECT table_name, column_name, data_type FROM information_schema.columns "
                                f"WHERE table_schema = '{self.schema}'")
                for t, c, dt in (r.rows if r.ok else []):
                    cols.setdefault(str(t).lower(), {})[str(c).lower()] = str(dt)
                self._cols = cols
            return self._cols

    def _exists(self, table: str, col: str, op: str, lit: str) -> bool:
        key = (table, col, op, lit)
        with self._lock:
            if key in self._cache:
                return self._cache[key]
        val = lit.replace("'", "''")
        cmp = {"like": "LIKE", "ilike": "ILIKE"}.get(op, "=")
        r = self.ex.run(f'SELECT EXISTS (SELECT 1 FROM {self.schema}."{table}" WHERE "{col}"::text {cmp} \'{val}\')')
        ok = bool(r.ok and r.rows and r.rows[0][0])
        if not r.ok:
            ok = True   # шалгаж чадсангүй — буруу гэж тооцохгүй
        with self._lock:
            self._cache[key] = ok
        return ok

    def bad_literals(self, sql: str | None) -> list[tuple[str, str, str]]:
        if not sql:
            return []
        try:
            tree = sqlglot.parse_one(sql, read="postgres")
        except Exception:
            return []
        tc = self._columns()
        ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
        alias, tables = {}, []
        for t in tree.find_all(exp.Table):
            name = (t.name or "").lower()
            if name and name not in ctes and name in tc:
                alias[(t.alias_or_name or t.name).lower()] = name
                tables.append(name)
        out = []
        for node in tree.find_all(exp.EQ, exp.In, exp.Like, exp.ILike):
            col = node.this
            if not isinstance(col, exp.Column):
                continue
            lits = list(node.expressions) if isinstance(node, exp.In) else [node.expression]
            if not lits or not all(isinstance(x, exp.Literal) and x.is_string for x in lits):
                continue
            q = (col.table or "").lower()
            if q:
                t = alias.get(q)
            else:
                cands = [x for x in set(tables) if col.name.lower() in tc.get(x, {})]
                t = cands[0] if len(cands) == 1 else None
            if not t or tc.get(t, {}).get(col.name.lower()) not in _TEXTY:
                continue
            op = "like" if isinstance(node, exp.Like) else ("ilike" if isinstance(node, exp.ILike) else "eq")
            codes = self.enums.get((t, col.name.lower()), set())
            for x in lits:
                if op == "eq" and x.this in codes:
                    continue
                if not self._exists(t, col.name.lower(), op, x.this):
                    out.append((t, col.name.lower(), x.this))
        return out


def fix_quarter_pg(sql: str | None) -> str | None:
    """Хадгалагдсан (PG болсон) SQL-ийн TO_CHAR дахь '%q'-г онлайн хөрвүүлэлттэй ижил дүрмээр засна (reselect-д):
    sqlglot PG-ийн TO_CHAR-ийг strftime хэлбэрт буулгадаг тул dialect.quarter_to_char-ийг дахин ашиглана."""
    if not sql or "%q" not in sql:
        return sql
    from .dialect import quarter_to_char
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except Exception:
        return sql
    changed = False
    for node in list(tree.find_all(exp.TimeToStr)):
        new = quarter_to_char(node, node.this)
        if new is not None:
            node.replace(new)
            changed = True
    return tree.sql(dialect="postgres") if changed else sql


def enum_codes(model) -> dict[tuple[str, str], set[str]]:
    """Semantic model-ийн enum утгууд {(view, багана): {код}} — өгөгдөлд мөр байхгүй ч хүчинтэй код (LiteralChecker)."""
    out: dict[tuple[str, str], set[str]] = {}
    for tname, t in (getattr(model, "tables", None) or {}).items():
        for cname, c in (getattr(t, "columns", None) or {}).items():
            vals = getattr(c, "values", None) or {}
            if vals:
                out[(str(tname).split(".")[-1].lower(), str(cname).lower())] = {str(k) for k in vals}
    return out
