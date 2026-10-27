"""SQL хамгаалагч (зааврын 7-р хэсэг, 3-р үе шат): sqlglot parse → ганц SELECT → allow-list → хориотой функц.

sqlglot нь validator биш, зөвхөн эхний шүүлтүүр: DB-ийн role, RLS, timeout, read-only транзакц
(db_t2s/110_roles_rls.sql, db/executor.py) хоёр дахь давхарга болно.
Мөн as-of огнооны дахин бичилт (CURRENT_DATE → DATE '2026-06-30') ба LIMIT хүчлэлтийг хийнэ.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

FORBIDDEN_NODES = (
    exp.Insert, exp.Update, exp.Delete, exp.Merge, exp.Create, exp.Drop, exp.Alter, exp.Command, exp.Copy,
    exp.Set, exp.Transaction, exp.Commit, exp.Rollback, exp.Grant, exp.TruncateTable, exp.Use, exp.Pragma,
    exp.LoadData, exp.Describe,
)
FORBIDDEN_PREFIXES = ("pg_", "lo_", "dblink", "query_to_", "xml", "file_", "txid_")
DEFAULT_FORBIDDEN = {
    "pg_sleep", "pg_sleep_for", "pg_sleep_until", "dblink", "dblink_exec", "lo_import", "lo_export", "lo_get",
    "lo_put", "lo_from_bytea", "lo_unlink", "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file",
    "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf", "pg_rotate_logfile", "set_config",
    "current_setting", "query_to_xml", "query_to_json", "pg_advisory_lock", "pg_advisory_xact_lock",
    "pg_try_advisory_lock", "txid_current", "pg_current_xact_id", "pg_notify", "copy", "version",
    "current_user", "session_user", "inet_server_addr", "inet_client_addr", "pg_backend_pid",
    "current_version",  # sqlglot: version() → CurrentVersion
}
# t2s доторх аюулгүй туслах функцүүд
ALLOWED_QUALIFIED_FUNCS = {"t2s.business_date", "t2s.fx_to_mnt"}
NOW_FUNCS = {"now", "transaction_timestamp", "statement_timestamp", "clock_timestamp"}
# PostgreSQL-ийн тусгай огнооны оролт ('now'::date, DATE 'today' ...) — as-of огноогоор солино (өдрийн зөрүү)
SPECIAL_DATE_LITERALS = {"now": 0, "today": 0, "yesterday": -1, "tomorrow": 1}


@dataclass
class GuardResult:
    ok: bool
    sql: str                          # дахин бичсэн (rewrite) SQL эсвэл эх SQL
    error: str | None = None          # загварт буцаах алдааны мессеж (монгол + англи)
    code: str | None = None           # parse_error | multi_statement | not_select | forbidden_node | forbidden_function |
                                      # forbidden_table | unknown_table | unknown_column | too_long
    tables: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)


class SqlGuard:
    def __init__(self, allowed_tables: dict[str, set[str]] | None = None, schema: str = "t2s",
                 forbidden_functions: set[str] | None = None, as_of_date: str | None = None,
                 enforce_limit: int | None = None, max_length: int = 20000, check_columns: bool = True):
        """allowed_tables: {view нэр: {баганууд}}; as_of_date: CURRENT_DATE/now()-ийг солих огноо (YYYY-MM-DD)."""
        self.allowed = {k.lower(): {c.lower() for c in v} for k, v in (allowed_tables or {}).items()}
        self.schema = schema
        self.forbidden = {f.lower() for f in (forbidden_functions or set())} | DEFAULT_FORBIDDEN
        self.as_of_date = as_of_date
        self.enforce_limit = enforce_limit
        self.max_length = max_length
        self.check_columns = check_columns

    # ------------------------------------------------------------------
    def check(self, sql: str, rewrite: bool = True) -> GuardResult:
        sql = (sql or "").strip().rstrip(";").strip()
        if not sql:
            return GuardResult(False, sql, "Хоосон SQL. Empty SQL.", "parse_error")
        if len(sql) > self.max_length:
            return GuardResult(False, sql, "SQL хэт урт. SQL is too long.", "too_long")
        try:
            statements = [s for s in sqlglot.parse(sql, read="postgres") if s is not None]
        except ParseError as e:
            msg = str(e).splitlines()[0][:300]
            return GuardResult(False, sql, f"SQL синтакс алдаа (PostgreSQL): {msg}", "parse_error")
        except Exception as e:  # sqlglot-ийн бусад алдаа (TokenError г.м.)
            return GuardResult(False, sql, f"SQL задлах боломжгүй: {str(e)[:200]}", "parse_error")
        if len(statements) != 1:
            return GuardResult(False, sql, "Зөвхөн нэг SELECT асуулга зөвшөөрнө. Only a single statement is allowed.",
                               "multi_statement")
        tree = statements[0]
        if not isinstance(tree, exp.Query):
            return GuardResult(False, sql, "Зөвхөн SELECT (WITH ... SELECT) зөвшөөрнө. Only SELECT queries are allowed.",
                               "not_select")
        for node in tree.walk():
            if isinstance(node, FORBIDDEN_NODES):
                return GuardResult(False, sql, f"Хориотой үйлдэл: {type(node).__name__}. Forbidden statement.",
                                   "forbidden_node")
            if isinstance(node, exp.Select) and (node.args.get("into") or node.args.get("locks")):
                return GuardResult(False, sql, "SELECT INTO / FOR UPDATE хориотой. Forbidden clause.", "forbidden_node")
        bad_fn = self._forbidden_function(tree)
        if bad_fn:
            return GuardResult(False, sql, f"Хориотой функц: {bad_fn}. Forbidden function.", "forbidden_function")
        cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
        tables = []
        for t in tree.find_all(exp.Table):
            name = (t.name or "").lower()
            db = (t.db or "").lower()
            if not name:
                continue
            if db and db != self.schema:
                return GuardResult(False, sql, f"Зөвхөн {self.schema} схемийн view-д хандана: {db}.{name} хориотой. "
                                               f"Only {self.schema}.* views are allowed.", "forbidden_table")
            if not db and name in cte_names:
                continue
            if self.allowed and name not in self.allowed:
                return GuardResult(False, sql, f"Үл мэдэгдэх хүснэгт: {name}. Unknown table: {name}. "
                                               f"Ашиглах боломжтой: {', '.join(sorted(self.allowed))[:400]}", "unknown_table")
            tables.append(name)
        columns = []
        if self.check_columns and self.allowed:
            err = self._check_columns(tree, cte_names)
            if err:
                return GuardResult(False, sql, err, "unknown_column", tables=sorted(set(tables)))
            columns = sorted({c.name.lower() for c in tree.find_all(exp.Column) if c.name})
        out_sql = sql
        if rewrite:
            tree = self.rewrite(tree)
            out_sql = tree.sql(dialect="postgres")
        return GuardResult(True, out_sql, None, None, tables=sorted(set(tables)), columns=columns)

    # ------------------------------------------------------------------
    def _forbidden_function(self, tree: exp.Expression) -> str | None:
        for f in tree.find_all(exp.Func):
            if isinstance(f, exp.Anonymous):
                name = (f.name or "").lower()
            else:
                name = (f.sql_name() or type(f).__name__).lower()
            qualified = None
            parent = f.parent
            if isinstance(parent, exp.Dot):
                left = parent.this
                qualified = f"{getattr(left, 'name', '')}.{name}".lower()
                if qualified in ALLOWED_QUALIFIED_FUNCS:
                    continue
                return qualified
            if name in self.forbidden or name.startswith(FORBIDDEN_PREFIXES) and name not in ("xmlagg",):
                return name
        return None

    def _check_columns(self, tree: exp.Expression, cte_names: set[str]) -> str | None:
        """Хүснэгт (alias)-аар тодорхойлсон баганыг allow-list-ээр шалгана. Тодорхойгүй баганыг EXPLAIN шалгана."""
        alias_map: dict[str, str] = {}
        for t in tree.find_all(exp.Table):
            name = (t.name or "").lower()
            if name and name not in cte_names:
                alias_map[(t.alias_or_name or name).lower()] = name
                alias_map.setdefault(name, name)
        for col in tree.find_all(exp.Column):
            qual = (col.table or "").lower()
            cname = (col.name or "").lower()
            if not qual or not cname or isinstance(col.this, exp.Star):
                continue
            base = alias_map.get(qual)
            if base and base in self.allowed and cname not in self.allowed[base]:
                hint = ", ".join(sorted(self.allowed[base]))[:500]
                return (f"'{base}' хүснэгтэд '{cname}' багана байхгүй. Column {qual}.{cname} does not exist "
                        f"in {base}. Боломжит баганууд: {hint}")
        return None

    # ------------------------------------------------------------------
    def rewrite(self, tree: exp.Expression) -> exp.Expression:
        if self.as_of_date:
            d = self.as_of_date

            def _swap(node):
                if isinstance(node, (exp.Date, exp.TsOrDsToDate)) and isinstance(node.this, exp.Literal) \
                        and node.this.is_string and node.this.this.strip().lower() in SPECIAL_DATE_LITERALS \
                        and not node.args.get("zone") and not node.expressions:
                    from datetime import date, timedelta
                    word = node.this.this.strip().lower()
                    day = (date.fromisoformat(d) + timedelta(days=SPECIAL_DATE_LITERALS[word])).isoformat()
                    return exp.cast(exp.Literal.string(day), "date")
                if isinstance(node, exp.Anonymous) and (node.name or "").lower() == "age" and len(node.expressions) == 1:
                    return exp.Anonymous(this="AGE", expressions=[exp.cast(exp.Literal.string(d), "date"),
                                                                 node.expressions[0].copy()])
                if isinstance(node, exp.Literal) and node.is_string and node.this.strip().lower() in ("now", "today") \
                        and isinstance(node.parent, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.Between)):
                    return exp.Literal.string(d)
                to = node.to.sql(dialect="postgres").upper() if isinstance(node, exp.Cast) and node.to else ""
                if to.startswith(("DATE", "TIMESTAMP")) and isinstance(node.this, exp.Literal) \
                        and node.this.is_string and node.this.this.strip().lower() in SPECIAL_DATE_LITERALS:
                    from datetime import date, timedelta
                    word = node.this.this.strip().lower()
                    day = (date.fromisoformat(d) + timedelta(days=SPECIAL_DATE_LITERALS[word])).isoformat()
                    if to.startswith("TIMESTAMP") and word == "now":
                        return exp.cast(exp.Literal.string(f"{d} 23:59:59+08"), "timestamptz")
                    return exp.cast(exp.Literal.string(day), node.to)
                if isinstance(node, exp.CurrentDate):
                    return exp.cast(exp.Literal.string(d), "date")
                if isinstance(node, (exp.CurrentTimestamp, exp.Localtimestamp)):
                    return exp.cast(exp.Literal.string(f"{d} 23:59:59+08"), "timestamptz")
                if isinstance(node, exp.Anonymous) and (node.name or "").lower() in NOW_FUNCS:
                    return exp.cast(exp.Literal.string(f"{d} 23:59:59+08"), "timestamptz")
                if isinstance(node, exp.CurrentTime):
                    return exp.cast(exp.Literal.string("23:59:59"), "time")
                return node

            tree = tree.transform(_swap)
        if self.enforce_limit:
            limit = tree.args.get("limit")
            if limit is None:
                tree = tree.limit(self.enforce_limit)
            else:
                try:
                    current = int(limit.expression.this) if limit.expression is not None else None
                except (AttributeError, TypeError, ValueError):
                    current = None
                if current is None or current > self.enforce_limit:
                    tree = tree.limit(self.enforce_limit)
        return tree


SQL_BLOCK = re.compile(r"```(?:sql|postgresql|postgres|sqlite)?\s*(.*?)```", re.S | re.I)


def extract_sql(text: str) -> str | None:
    """Загварын хариунаас SQL гаргаж авна: <answer> → ```sql``` блок → SELECT/WITH-ээс эхэлсэн хэсэг."""
    if not text:
        return None
    if "</think>" in text:
        text = text.split("</think>")[-1]
    m = re.findall(r"<answer>(.*?)</answer>", text, re.S | re.I)
    if m:
        inner = m[-1]
        blocks = SQL_BLOCK.findall(inner)
        return (blocks[-1] if blocks else inner).strip() or None
    blocks = SQL_BLOCK.findall(text)
    if blocks:
        return blocks[-1].strip() or None
    m2 = re.search(r"((?:WITH|SELECT)\b.*)", text, re.S | re.I)
    if m2:
        return m2.group(1).strip().split("\n\n")[0].strip() or None
    return None


# SQL бичих оролдлогын шинж: «SELECT … FROM», мөрийн эхний SELECT, CTE («WITH x AS (»)
_SQL_HINTS = (re.compile(r"\bselect\b.+?\bfrom\b", re.I | re.S), re.compile(r"^\s*select\b", re.I | re.M),
              re.compile(r"\bwith\s+\w+\s+as\s*\(", re.I))


def is_refusal(text: str | None) -> bool:
    """Загвар эцсийн хариугаа өгсөн боловч SQL бичээгүй эсэх — татгалзалт (TrustSQL-д «хариулаагүй»).

    Эцсийн хариу = </think>-ээс хойших хэсэг, <answer> байвал түүний агуулга. Тасарсан гаралт (<think> хаагдаагүй),
    хоосон хариу эсвэл SQL-ийн шинжтэй хариу нь татгалзалт биш (форматын/SQL-ийн алдаа хэвээр).
    """
    if not text or not text.strip():
        return False
    if "</think>" in text:
        text = text.split("</think>")[-1]
    elif "<think>" in text:
        return False
    m = re.findall(r"<answer>(.*?)</answer>", text, re.S | re.I)
    final = (m[-1] if m else text).strip()
    return bool(final) and not SQL_BLOCK.search(final) and not any(p.search(final) for p in _SQL_HINTS)
