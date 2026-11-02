"""Санал хураалтын өмнөх SQL lint (2026-11-02, RunOptions.vote_lint): шалгалтад унасан хувилбарыг — унаагүй нь байвал —
санал хураалтаас хасна (vote_filter-ийн дараа; бүгд унасан бол хэвээр).

* chasm / fan — Snowflake Cortex Analyst-ийн «granularity graph»-ийн хялбар хувилбар (semantic_model-ийн FK = олон→нэг):
  chasm = нэг эцгийн хоёр хүүхэд хүснэгт нэг SELECT-д join хийгдэж, аль нэгийнх нь хэмжүүрийг SUM/AVG (давхардал);
  fan = эцэг хүснэгтийн хэмжүүрийг SUM/AVG хийхэд түүний хүүхэд хүснэгт join хийгдсэн. DISTINCT-тэй бол алгасна.
* npl_by_count — асуулт NPL-ийн хувь/харьцааг (KPI) асуусан ч SQL нь is_npl-ийг үндсэн үлдэгдэлгүйгээр (тоогоор) тооцсон
  (glossary/AUTHORING §3.2: NPL хувь = үндсэн үлдэгдлээр; «хэдэн хувь нь» = тоогоор — алгасна).
* no_active_portfolio — асуулт «багц» эсвэл NPL-ийг асуусан ч loans-ыг is_active_portfolio-гүй ашигласан.
Офлайн (хадгалсан хувилбарын зөв/буруугаар): dev +3/−0, test +1/−0 (M1ens ба M1ensfx)."""
from __future__ import annotations

import re

import sqlglot
from sqlglot import exp

from ..text.normalize import cyr_key

LINT_KINDS = ("chasm", "fan", "npl_by_count", "no_active_portfolio")
_NPL_Q = re.compile(r"(npl|чанаргүй зээл)\w*\s*(-?ийн\s+)?(хувь|хуви|харьц)")   # «хувийг» гэх мэт хувилсан хэлбэр
_COUNT_Q = re.compile(r"хэдэн хувь нь|тооны|тоогоор")
_PORTFOLIO_Q = re.compile(r"\bбагц")


def child_map(model) -> dict[str, set[str]]:
    """{хүүхэд хүснэгт: {эцэг хүснэгт}} — semantic_model-ийн FK (олон → нэг)."""
    out: dict[str, set[str]] = {}
    for t, tab in model.tables.items():
        for _col, target, _tcol in tab.fks():
            out.setdefault(t, set()).add(target)
    return out


def _scope_tables(sel: exp.Select) -> dict[str, str]:
    """Энэ SELECT-ийн шууд FROM/JOIN хүснэгт {alias: нэр} (дэд query/CTE-ийнхийг тоолохгүй)."""
    out = {}
    for t in sel.find_all(exp.Table):
        if t.find_ancestor(exp.Select) is sel:
            out[(t.alias_or_name or t.name).lower()] = t.name.lower()
    return out


def _join_edges(sel: exp.Select, tabs: dict[str, str]) -> set[tuple[str, str]]:
    """JOIN ON / WHERE-ийн `a.x = b.y` тэнцлээс хүснэгтийн хос (хоёр чиглэлд) — бодит холбоос (FK-ийн боломж биш)."""
    edges = set()
    for eq in sel.find_all(exp.EQ):
        if eq.find_ancestor(exp.Select) is not sel:
            continue
        lc, rc = eq.left, eq.right
        if isinstance(lc, exp.Column) and isinstance(rc, exp.Column) and lc.table and rc.table:
            a, b = tabs.get(lc.table.lower()), tabs.get(rc.table.lower())
            if a and b and a != b:
                edges |= {(a, b), (b, a)}
    return edges


def fan_flags(sql: str, children: dict[str, set[str]]) -> set[str]:
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except Exception:
        return set()
    if tree is None:
        return set()
    flags: set[str] = set()
    for sel in tree.find_all(exp.Select):
        tabs = _scope_tables(sel)
        # anti-join (`LEFT JOIN b … WHERE b.x IS NULL`) нь мөр үржүүлэхгүй — тэр хүснэгтийг тооцохгүй
        where = sel.args.get("where")
        anti = {tabs[c.table.lower()] for isn in (where.find_all(exp.Is) if where else [])
                for c in isn.find_all(exp.Column)
                if isinstance(isn.expression, exp.Null) and not isinstance(isn.parent, exp.Not)
                and c.table and c.table.lower() in tabs}
        names = set(tabs.values()) - anti
        if len(names) < 2 or sel.args.get("distinct"):
            continue
        agg_tabs = set()
        for a in sel.find_all(exp.Sum, exp.Avg):
            if a.find_ancestor(exp.Select) is not sel or isinstance(a.this, exp.Distinct):
                continue
            # нэгтгэж буй хэмжүүр: шууд багана эсвэл CASE-ийн THEN багана (`SUM(h.x * l.rate)` мэт мөр бүрийн үржвэр биш)
            arg = a.this
            cols = [arg] if isinstance(arg, exp.Column) else [
                i.args.get("true") for i in (arg.args.get("ifs") or []) if isinstance(arg, exp.Case)
                and isinstance(i.args.get("true"), exp.Column)]
            for c in cols:
                if c.table and c.table.lower() in tabs:
                    agg_tabs.add(tabs[c.table.lower()])
        if not agg_tabs:
            continue
        edges = _join_edges(sel, tabs)
        # хүүхэд→эцэг холбоос: JOIN-д бодитоор тэнцүүлсэн, хүүхэд нь эцэг рүү FK-тэй (өөрөө өөр лүүгээ биш)
        kids_of = {p: {c for c in names if c != p and (c, p) in edges and p in children.get(c, set())} for p in names}
        for p, kids in kids_of.items():
            if len(kids) >= 2 and agg_tabs & kids:
                flags.add("chasm")
        for p in agg_tabs:
            if kids_of.get(p):
                flags.add("fan")
    return flags


_LOAN_TABLES = {"loans", "loan_payments", "loan_schedule", "loan_charges", "loan_applications", "loan_collaterals",
                "loan_guarantors", "loan_writeoffs", "loan_dpd_history", "loan_restructures"}
_DEPOSIT_TABLES = {"deposits", "accounts", "account_balances_monthly"}
_EVENT_DATES = r"(opened_date|open_date|disbursement_date|onboarded_date|submitted_date|hire_date|contract_date)"
_EVENT_Q = re.compile(r"нээгдсэн|нээсэн|олгосон|олгогдсон|шинээр|шинэ |ажилд орсон|ирсэн|бүртгүүлсэн")
_STATE_Q = re.compile(r"идэвхт|одоо|өнөөдр|ажиллаж байгаа|үлдэгдэл|багц|хугацаа нь дуусах|хаагдаагүй|нээлттэй")
_GENERIC_LABELS = {"идэвхтэй", "хаагдсан", "нээлттэй", "бага", "дунд", "өндөр", "салбар", "энгийн", "бусад", "тийм", "үгүй"}


def _domain_mismatch(q: str, tables: set[str]) -> bool:
    """Асуулт хадгаламжийн (зээлгүй) атлаа SQL зөвхөн зээлийн хүснэгттэй — эсвэл эсрэгээрээ."""
    if not tables:
        return False
    if "хадгаламж" in q and "зээл" not in q and tables <= _LOAN_TABLES:
        return True
    return "зээл" in q and not re.search(r"хадгаламж|данс", q) and tables <= _DEPOSIT_TABLES


def label_codes(model) -> dict[tuple[str, str], dict[str, str]]:
    """{(хүснэгт, багана): {нэр (cyr_key): код}} — semantic_model-ийн enum (ерөнхий нэр, ≤5 тэмдэгттэйг хасна)."""
    out: dict[tuple[str, str], dict[str, str]] = {}
    for t, tab in model.tables.items():
        for c, col in tab.columns.items():
            for code, label in (col.values or {}).items():
                k = cyr_key(str(label))
                if len(k) > 5 and k not in _GENERIC_LABELS and not str(code).isdigit():   # GL дансны код биш
                    out.setdefault((t, c), {})[k] = code
    return out


def _label_mismatch(q: str, sql: str, labels: dict) -> bool:
    """Асуултад enum-ийн нэр («Биелээгүй») яг байгаа ч SQL тэр баганыг өөр/илүү кодоор шүүсэн."""
    for (_t, col), names in labels.items():
        hit = {code for name, code in names.items() if name in q}
        if not hit:
            continue
        used = set(re.findall(rf"\b{col}\s*(?:=\s*'([A-Z0-9_]+)'|in\s*\(([^)]*)\))", sql, re.I))
        codes = set()
        for a, b in used:
            codes |= {a} if a else set(re.findall(r"'([A-Z0-9_]+)'", b))
        own = {c for c in codes if c in set(names.values()) | hit}
        if own and not own & hit:   # нэрлэсэн кодыг огт оруулаагүй (илүү кодыг бизнесийн олонлог байж болох тул тооцохгүй)
            return True
    return False


def lint_sql(question: str, sql: str | None, children: dict[str, set[str]], labels: dict | None = None) -> set[str]:
    """Хувилбарын lint шошго (хоосон = шалгалт давсан). labels = label_codes(model) (өгвөл label_mismatch)."""
    if not sql:
        return set()
    q = cyr_key(question or "")
    s = sql.lower()
    flags: set[str] = set()
    if _NPL_Q.search(q) and not _COUNT_Q.search(q) and "is_npl" in s and "principal_outstanding" not in s:
        flags.add("npl_by_count")
    if (_PORTFOLIO_Q.search(q) or _NPL_Q.search(q)) and re.search(r"\bloans\b", s) and "is_active_portfolio" not in s:
        flags.add("no_active_portfolio")
    tables = set(re.findall(r"\b(?:from|join)\s+(?:t2s\.)?([a-z_]+)", s))
    if _domain_mismatch(q, tables):
        flags.add("domain_mismatch")
    if (re.search(r"\bstatus(_code)?\s*=\s*'active'", s) and re.search(_EVENT_DATES, s) and _EVENT_Q.search(q)
            and not _STATE_Q.search(q)):
        flags.add("unrequested_active")
    if labels and _label_mismatch(q, sql, labels):
        flags.add("label_mismatch")
    return flags | fan_flags(sql, children)
