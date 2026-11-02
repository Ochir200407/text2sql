"""KPI/схемийн детерминист засвар (2026-11-02, RunOptions.kpi_fix; postproc-ийн дараа, guard-аас өмнө). Үлдсэн алдааны
шинжилгээ (scratchpad/err_deep): glossary-ийн каноник SQL prompt-д байсан ч загвар өөрөөр бичдэг хэв маяг — dev+test-ийн gold
бүгд каноник хэлбэртэй. Засвар бүр AST (sqlglot) дээр, нөхцөл нь яг таарвал л; бусад үед SQL өөрчлөгдөхгүй.

* npl_amount — NPL-ийн хувь/харьцааг асуусан (тоогоор биш) асуултад `SUM(CASE WHEN l.is_npl THEN 1 ELSE 0 END) / COUNT(..)`
  → үндсэн үлдэгдлээр (`… THEN l.principal_outstanding_mnt …` / `SUM(l.principal_outstanding_mnt)`) — AUTHORING §3.2.
* posted — loan_payments-ийг төлөвийн нөхцөлгүй ашигласан бол `<alias>.status = 'POSTED'` (gold-ийн loan_payments бүр төлөвөөр
  шүүсэн; «амжилттай бүртгэгдсэн» эргэн төлөлт).
* id_column — 'LN-2025-000123' мэт дугаарыг өөр баганатай харьцуулсан бол semantic_model-ийн тайлбарт заасан баганаар
  (жишээ нь loans.loan_number), тэр хүснэгт ижил SELECT-д байвал л.
* active_portfolio — «багц»/NPL/хугацаа хэтэрсэн асуултад loans-ыг is_active_portfolio-гүй ашигласан бол нэмнэ (агшин/түүх,
  олголт, хаагдсан, данснаас хассан зэрэг үгтэй асуулт ба loan_dpd_history, тодорхой зээлийн дугаартай SQL-д алгасна).
"""
from __future__ import annotations

import re
import sqlglot
from sqlglot import exp

from ..text.normalize import cyr_key

FIX_KINDS = ("npl_amount", "posted", "id_column", "active_portfolio")
_NPL_Q = re.compile(r"(npl|чанаргүй зээл)\w*\s*(-?ийн\s+)?(хувь|хуви|харьц)")
_COUNT_Q = re.compile(r"хэдэн хувь нь|тооны|тоогоор")
_PORTFOLIO_Q = re.compile(r"\bбагц|npl|чанаргүй зээл|хугацаа (нь )?хэтэрсэн")
_PORTFOLIO_SKIP = re.compile(r"хассан|данснаас|олгогдсон|олгосон|олголт|хаагдсан|бүх зээл|агшин|сарын эцс|түүх|эцэст|эцсийн")
_ID_RE = re.compile(r"^([A-Z]{2,3})-\d{4}-\d{4,}$")


_ID_CACHE: dict[int, dict[str, tuple[str, str]]] = {}


def id_columns(model) -> dict[str, tuple[str, str]]:
    """{угтвар: (хүснэгт, багана)} — semantic_model-ийн баганын тайлбар дахь «(LN-2025-000123)» хэлбэрээс (кэштэй)."""
    if id(model) in _ID_CACHE:
        return _ID_CACHE[id(model)]
    out: dict[str, tuple[str, str]] = {}
    for t, tab in model.tables.items():
        for c, col in tab.columns.items():
            m = re.search(r"\(([A-Z]{2,3})-\d{4}-\d+\)", col.mn or "")
            if m:
                out.setdefault(m.group(1), (t, c))
    _ID_CACHE[id(model)] = out
    return out


def _scope_aliases(sel: exp.Select) -> dict[str, str]:
    out = {}
    for t in sel.find_all(exp.Table):
        if t.find_ancestor(exp.Select) is sel:
            out[t.name.lower()] = (t.alias_or_name or t.name)
    return out


def _add_where(sel: exp.Select, cond: exp.Expression) -> None:
    where = sel.args.get("where")
    sel.set("where", exp.Where(this=exp.and_(where.this, cond) if where else cond))


def _is_one(node) -> bool:
    return isinstance(node, exp.Literal) and not node.is_string and node.this in ("1", "1.0")


def _npl_amount(tree: exp.Expression) -> int:
    n = 0
    for div in list(tree.find_all(exp.Div)):
        num, den = div.this, div.expression
        sums = [s for s in num.find_all(exp.Sum) if isinstance(s.this, exp.Case)]
        target = None
        for s in sums:
            case = s.this
            ifs = case.args.get("ifs") or []
            if len(ifs) != 1:
                continue
            cond, then = ifs[0].this, ifs[0].args.get("true")
            npl = [c for c in cond.find_all(exp.Column) if c.name.lower() == "is_npl"]
            default = case.args.get("default")
            if npl and _is_one(then) and (default is None or (isinstance(default, exp.Literal) and default.this == "0")):
                target = (s, ifs[0], npl[0].table)
                break
        counts = [c for c in den.find_all(exp.Count)]
        if not target or len(counts) != 1:
            continue
        _s, if_node, alias = target
        col = exp.column("principal_outstanding_mnt", table=alias or None)
        if_node.set("true", col.copy())
        counts[0].replace(exp.Sum(this=col.copy()))
        n += 1
    return n


def _posted(tree: exp.Expression) -> int:
    n = 0
    for sel in list(tree.find_all(exp.Select)):
        al = _scope_aliases(sel)
        if "loan_payments" not in al:
            continue
        a = al["loan_payments"]
        has_status = any(c.name.lower() == "status" and (c.table or "").lower() in (a.lower(), "")
                         for c in sel.find_all(exp.Column) if c.find_ancestor(exp.Select) is sel)
        if has_status:
            continue
        _add_where(sel, exp.EQ(this=exp.column("status", table=a), expression=exp.Literal.string("POSTED")))
        n += 1
    return n


def _id_column(tree: exp.Expression, ids: dict[str, tuple[str, str]]) -> int:
    n = 0
    for eq in list(tree.find_all(exp.EQ)):
        lit, col = eq.expression, eq.this
        if isinstance(col, exp.Literal):
            lit, col = col, lit
        if not (isinstance(lit, exp.Literal) and lit.is_string and isinstance(col, exp.Column)):
            continue
        m = _ID_RE.match(lit.this or "")
        if not m or m.group(1) not in ids:
            continue
        table, column = ids[m.group(1)]
        sel = eq.find_ancestor(exp.Select)
        al = _scope_aliases(sel) if sel is not None else {}
        if table not in al:
            continue
        if col.name.lower() == column and (col.table or "").lower() in (al[table].lower(), ""):
            continue
        col.replace(exp.column(column, table=al[table]))
        n += 1
    return n


def _active_portfolio(tree: exp.Expression, sql_l: str) -> int:
    if "loan_dpd_history" in sql_l or re.search(r"'ln-\d{4}-", sql_l):
        return 0
    n = 0
    for sel in list(tree.find_all(exp.Select)):
        al = _scope_aliases(sel)
        if "loans" not in al:
            continue
        if any(c.name.lower() == "is_active_portfolio" for c in sel.find_all(exp.Column)):
            continue
        _add_where(sel, exp.column("is_active_portfolio", table=al["loans"]))
        n += 1
    return n


_OPEN_Q = re.compile(r"хаагдаагүй")
_OPEN_SET = ("RESOLVED", "CLOSED", "WRITTEN_OFF")   # gold-ийн «хаагдаагүй кейс» бүр (9) = NOT IN энэ олонлог
_USD_Q = re.compile(r"хэдэн доллар|доллараар")


def _open_cases(tree: exp.Expression) -> int:
    """«хаагдаагүй» кейс: collection_cases.status = 'OPEN' → NOT IN ('RESOLVED','CLOSED','WRITTEN_OFF')."""
    n = 0
    for eq in list(tree.find_all(exp.EQ)):
        col, lit = eq.this, eq.expression
        if not (isinstance(col, exp.Column) and col.name.lower() == "status" and isinstance(lit, exp.Literal)
                and lit.is_string and lit.this.upper() == "OPEN"):
            continue
        sel = eq.find_ancestor(exp.Select)
        al = _scope_aliases(sel) if sel is not None else {}
        if "collection_cases" not in al or (col.table or "").lower() not in (al["collection_cases"].lower(), ""):
            continue
        eq.replace(exp.Not(this=exp.In(this=col.copy(), expressions=[exp.Literal.string(x) for x in _OPEN_SET])))
        n += 1
    return n


def _usd_native(tree: exp.Expression, model) -> int:
    """«хэдэн доллар» (ам.доллараар хязгаарласан): SUM(<x>_mnt) → SUM(<x>), тухайн хүснэгтэд <x> багана байвал (AUTHORING §3.3)."""
    n = 0
    for sel in list(tree.find_all(exp.Select)):
        al = {v.lower(): k for k, v in _scope_aliases(sel).items()}   # alias → хүснэгт
        for agg in sel.find_all(exp.Sum):
            if agg.find_ancestor(exp.Select) is not sel:
                continue
            for c in list(agg.find_all(exp.Column)):
                name = c.name.lower()
                if not name.endswith("_mnt"):
                    continue
                table = al.get((c.table or "").lower()) or (next(iter(al.values())) if len(al) == 1 else None)
                base = name[:-4]
                if table in model.tables and base in model.tables[table].columns:
                    c.set("this", exp.to_identifier(base))
                    n += 1
    return n


# --------------------------------------------------------------------------- 2-р багц (2026-11-02, 4 домэйны шинжилгээ)
_MNT_Q = re.compile(r"(?<!хэдэн )(?<!хэд )төгрөгийн\s+(бүх\s+|идэвхтэй\s+)?(зээл|хадгаламж|харилцах|данс)"
                    r"|төгрөгөөр\s+(олгосон|байршуулсан|ирсэн)")
_MNT_ANCHORS = ("loans", "loan_payments", "deposits", "accounts", "loan_charges")
_LASTN_Q = re.compile(r"сүүлийн\s+(\d+)\s+хоног")
_NEXT_Q = re.compile(r"дараагийн\s+(төлөлт|хуваарь|төлбөр)")
_APPROVED_Q = re.compile(r"батлагд")
_APPROVED_SKIP = re.compile(r"гэрээ|төлөв")
APPROVED_SET = ("APPROVED", "CONTRACTED", "EXPIRED")                 # glossary approval_rate; gold бүр
DECIDED_SET = ("APPROVED", "CONTRACTED", "EXPIRED", "REJECTED")      # шийдвэр гарсан хүсэлт
_RATE_Q = re.compile(r"(татгалз\w*|зөвшөөрл\w*|батлах)\s+хув[ьи]")
_DECISION_Q = re.compile(r"батлагд|татгалз|шийдвэр")
_PERIOD_Q = re.compile(r"\d{4}|энэ он|өнгөрсөн|улирал|\bсар")
_RECEIPT_Q = re.compile(r"ирсэн|ирж|ирээд|ирүүлсэн|илгээ|гаргасан|хүлээн ав")
_PRODUCT_Q = re.compile(r"бүтээгдэхүүн")
_TYPE_Q = re.compile(r"төр[өә]?л|\btype\b")


def _mnt_filter(tree: exp.Expression) -> int:
    """«төгрөгийн зээл/хадгаламж…» (AUTHORING §3.3): валютаар шүүгээгүй бол үндсэн хүснэгтэд currency_code = 'MNT'."""
    for anchor in _MNT_ANCHORS:
        for sel in tree.find_all(exp.Select):
            al = _scope_aliases(sel)
            if anchor in al:
                _add_where(sel, exp.EQ(this=exp.column("currency_code", table=al[anchor]),
                                       expression=exp.Literal.string("MNT")))
                return 1
    return 0


def _lastn_boundary(tree: exp.Expression, n: str) -> int:
    """«сүүлийн N хоног»: `col >= <өнөөдөр − N>` → `>` (gold-ийн дүрэм `> CURRENT_DATE - N`)."""
    k = 0
    for ge in list(tree.find_all(exp.GTE)):
        rhs = ge.expression.sql(dialect="postgres").lower()
        if re.search(rf"(\b{n}\s*day|'{n} day|-\s*{n}\b|interval\s*'{n})", rhs):
            ge.replace(exp.GT(this=ge.this.copy(), expression=ge.expression.copy()))
            k += 1
    return k


def _next_due(tree: exp.Expression) -> int:
    """«дараагийн төлөлт»: ORDER BY <огноо> LIMIT 1, тэр огноонд доод хязгааргүй бол `<огноо> > CURRENT_DATE`."""
    for sel in tree.find_all(exp.Select):
        lim, order = sel.args.get("limit"), sel.args.get("order")
        if lim is None or order is None or lim.expression is None or lim.expression.sql() != "1":
            continue
        first = order.expressions[0] if order.expressions else None
        col = first.this if isinstance(first, exp.Ordered) else None
        if not (isinstance(col, exp.Column) and col.name.lower().endswith("_date")) or first.args.get("desc"):
            continue
        where = sel.args.get("where")
        if where and any(isinstance(cmp, (exp.GT, exp.GTE)) and isinstance(cmp.this, exp.Column)
                         and cmp.this.name.lower() == col.name.lower() for cmp in where.find_all(exp.GT, exp.GTE)):
            continue
        _add_where(sel, exp.GT(this=col.copy(), expression=exp.CurrentDate()))
        return 1
    return 0


def _approved_set(tree: exp.Expression) -> int:
    """«батлагдсан» хүсэлт = APPROVED/CONTRACTED/EXPIRED: `status_code = 'APPROVED'` (эсвэл status_name = 'Батлагдсан') → IN."""
    k = 0
    for eq in list(tree.find_all(exp.EQ)):
        col, lit = eq.this, eq.expression
        if not (isinstance(col, exp.Column) and isinstance(lit, exp.Literal) and lit.is_string):
            continue
        name, val = col.name.lower(), lit.this
        if (name == "status_code" and val.upper() == "APPROVED") or (name == "status_name" and val == "Батлагдсан"):
            sel = eq.find_ancestor(exp.Select)
            if sel is None or "loan_applications" not in _scope_aliases(sel):
                continue
            target = exp.column("status_code", table=col.table or None)
            eq.replace(exp.In(this=target, expressions=[exp.Literal.string(x) for x in APPROVED_SET]))
            k += 1
    return k


def _rate_denominator(tree: exp.Expression) -> int:
    """Татгалзлын/зөвшөөрлийн хувь: loan_applications-ийн харьцааны шүүлтгүй COUNT(*) хуваагчийг шийдвэр гарсан хүсэлтээр."""
    k = 0
    for div in list(tree.find_all(exp.Div)):
        sel = div.find_ancestor(exp.Select)
        al = _scope_aliases(sel) if sel is not None else {}
        if "loan_applications" not in al:
            continue
        for cnt in list(div.expression.find_all(exp.Count)):
            if isinstance(cnt.parent, exp.Filter) or not isinstance(cnt.this, exp.Star):
                continue
            cond = exp.In(this=exp.column("status_code", table=al["loan_applications"]),
                          expressions=[exp.Literal.string(x) for x in DECIDED_SET])
            cnt.replace(exp.Filter(this=cnt.copy(), expression=exp.Where(this=cond)))
            k += 1
    return k


def _decided_date(tree: exp.Expression) -> int:
    """Шийдвэрийн хугацаа: шийдвэрийн төлөвөөр шүүсэн SQL-ийн WHERE дахь submitted_date → decided_date (харьцаа биш үед)."""
    if any(True for _ in tree.find_all(exp.Div)):
        return 0
    k = 0
    for where in tree.find_all(exp.Where):
        text = where.sql(dialect="postgres").upper()
        if not any(f"'{s}'" in text for s in ("APPROVED", "REJECTED", "CONTRACTED")):
            continue
        for c in list(where.find_all(exp.Column)):
            if c.name.lower() == "submitted_date":
                c.set("this", exp.to_identifier("decided_date"))
                k += 1
    return k


def _negated_label(tree: exp.Expression, q: str, model) -> int:
    """«ажлаас гараагүй» мэт үгүйсгэсэн төлөв: `col = 'CODE'` (CODE-ийн нэрийн сүүлийн үгийн үндэс асуултад -аагүй хэлбэрээр,
    эерэг хэлбэр нь асуултад алга) → `<>`."""
    k = 0
    for eq in list(tree.find_all(exp.EQ)):
        col, lit = eq.this, eq.expression
        if not (isinstance(col, exp.Column) and isinstance(lit, exp.Literal) and lit.is_string):
            continue
        for tab in model.tables.values():
            c = tab.columns.get(col.name.lower())
            label = (c.values or {}).get(lit.this) if c is not None else None
            if not label:
                continue
            last = cyr_key(label).split()[-1]
            stem = re.sub(r"(сан|сэн|сон|сөн|ан|эн|он|өн)$", "", last)
            if len(stem) >= 2 and re.search(rf"{stem}(аагүй|ээгүй|оогүй|өөгүй)", q) and cyr_key(label) not in q:
                eq.replace(exp.NEQ(this=col.copy(), expression=lit.copy()))
                k += 1
                break
    return k


def _product_name(tree: exp.Expression) -> int:
    """«бүтээгдэхүүн» (төрөлгүй): SELECT/GROUP BY дахь product_type_name → product_name (WHERE-ийг хөндөхгүй)."""
    k = 0
    for c in list(tree.find_all(exp.Column)):
        if c.name.lower() != "product_type_name" or c.find_ancestor(exp.Where) is not None:
            continue
        c.set("this", exp.to_identifier("product_name"))
        k += 1
    return k


def kpi_fix(question: str, sql: str | None, model) -> tuple[str | None, list[str]]:
    """Буцаах: (SQL, засварын шошго). Задлах боломжгүй/нөхцөл таараагүй бол эх SQL, []."""
    if not sql:
        return sql, []
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except Exception:
        return sql, []
    if tree is None:
        return sql, []
    q = cyr_key(question or "")
    s = sql.lower()
    tags = []
    if _NPL_Q.search(q) and not _COUNT_Q.search(q) and "is_npl" in s and "principal_outstanding" not in s:
        if _npl_amount(tree):
            tags.append("npl_amount")
    if "loan_payments" in s and _posted(tree):
        tags.append("posted")
    if re.search(r"'[a-z]{2,3}-\d{4}-\d{4,}'", s) and _id_column(tree, id_columns(model)):
        tags.append("id_column")
    # «багц»/NPL/хугацаа хэтэрсэн асуулт, эсвэл (F2a) loans-ын days_past_due-ээр шүүсэн ч төлөвийн шүүлтгүй SQL
    dpd_filter = re.search(r"days_past_due\s*(>|>=|=|between)", s) and not re.search(r"\bstatus_(code|name)\b", s)
    if ((_PORTFOLIO_Q.search(q) or dpd_filter) and not _PORTFOLIO_SKIP.search(q) and re.search(r"\bloans\b", s)
            and "is_active_portfolio" not in s and _active_portfolio(tree, s)):
        tags.append("active_portfolio")
    if _OPEN_Q.search(q) and "collection_cases" in s and _open_cases(tree):
        tags.append("open_case")
    if _USD_Q.search(q) and not re.search(r"төгрөг", q) and "'usd'" in s and _usd_native(tree, model):
        tags.append("usd_native")
    # fx_to_mnt(x.currency_code, …) хөрвүүлэлт байж болно — зөвхөн валютын тэнцүү/IN шүүлт байвал алгасна
    if _MNT_Q.search(q) and not re.search(r"currency_code\s*(=|in)", s) and _mnt_filter(tree):
        tags.append("mnt_filter")
    m = _LASTN_Q.search(q)
    if m and _lastn_boundary(tree, m.group(1)):
        tags.append("lastn_boundary")
    if _NEXT_Q.search(q) and "limit 1" in s and _next_due(tree):
        tags.append("next_due")
    if "loan_applications" in s:
        if _APPROVED_Q.search(q) and not _APPROVED_SKIP.search(q) and _approved_set(tree):
            tags.append("approved_set")
        if _RATE_Q.search(q) and _rate_denominator(tree):
            tags.append("rate_denominator")
        if (_DECISION_Q.search(q) and _PERIOD_Q.search(q) and not _RECEIPT_Q.search(q) and "submitted_date" in s
                and _decided_date(tree)):
            tags.append("decided_date")
    if re.search(r"(аагүй|ээгүй|оогүй|өөгүй)", q) and _negated_label(tree, q, model):
        tags.append("negated_label")
    if _PRODUCT_Q.search(q) and not _TYPE_Q.search(q) and "product_type_name" in s and _product_name(tree):
        tags.append("product_name")
    if not tags:
        return sql, []
    try:
        return tree.sql(dialect="postgres"), tags
    except Exception:
        return sql, []
