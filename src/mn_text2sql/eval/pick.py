"""P (CHASE-SQL маягийн хос харьцуулалт): санал зөрсөн үед үр дүнгийн хамгийн их санал авсан хоёр бүлгийн SQL-ийг
Arctic өөрөө (өөр загваргүй) харьцуулж сонгоно. Байрлалын хазайлтаас сэргийлж A/B, B/A гэж хоёр удаа асууна —
хоёулаа нэгийг сонговол түүнийг, үгүй бол санал хураалтын сонголт хэвээр.

    python -m mn_text2sql reselect --from M1rr --to M1ensp --split dev --no-refuse --no-clarify \
        --extra M1ks M1a M1g8 M1m --pick --pick-model d2

T2 (--pick-diff, анхдагч унтраалттай): diff_clauses() нь sqlglot-оор WHERE/HAVING/GROUP BY/агрегат/хүснэгтийн ялгааг
олж, ≤ MAX_DIFFS төрөл бол Arctic-аас зөвхөн тэр ялгааны тухай асууна (_ask_diff); үгүй бол хуучин _ask.
"""
from __future__ import annotations

import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from ..config import resolve_path

log = logging.getLogger(__name__)
MAX_DIFFS = 2   # T2: ялгаатай clause-ийн төрөл ≤ энэ бол нарийн (diff) асуулт, их бол бүтэн-SQL асуулт

SYSTEM = (
    "You are an expert PostgreSQL reviewer. Two candidate queries answer the same question but return different "
    "results. Decide which one answers the question correctly: check the filters, date ranges, aggregation, joins, "
    "grouping and whether the returned columns and values match what is asked. "
    "Reply inside <answer></answer> with exactly A or B.")


def preview(res, max_rows: int = 5, max_chars: int = 600) -> str:
    """Үр дүнгийн товч: багана + эхний мөрүүд + нийт мөрийн тоо."""
    if res is None or not getattr(res, "ok", False):
        return "(error)"
    head = " | ".join(str(c) for c in res.columns)
    rows = [" | ".join("NULL" if v is None else str(v) for v in r) for r in res.rows[:max_rows]]
    text = "\n".join([head, *rows])
    if len(text) > max_chars:
        text = text[:max_chars] + " …"
    return f"{text}\n({res.rowcount} rows)"


def top_two(cands: list, chosen) -> tuple | None:
    """Санал хураалтын шилдэг (chosen) ба дараагийн хамгийн их санал авсан өөр үр дүнтэй хувилбар."""
    good = [c for c in cands if c.ok and c.result_hash and not getattr(c, "degenerate", False)
            and not getattr(c, "bad_literals", None)]
    counts = Counter(c.result_hash for c in good)
    others = [n for h, n in counts.items() if h != chosen.result_hash]
    if not others:
        return None
    n2 = max(others)   # тэнцвэл эхэнд байгаа (нэмэлт run-ийнх) хувилбар — Pipeline.vote-той ижил
    return chosen, next(c for c in good if c.result_hash != chosen.result_hash and counts[c.result_hash] == n2)


def _ask(llm, question: str, first, second) -> str | None:
    user = (f"Question: {question}\n\n"
            f"Query A:\n{first.sql}\nResult A:\n{preview(first.result)}\n\n"
            f"Query B:\n{second.sql}\nResult B:\n{preview(second.result)}")
    try:
        resp = llm.chat([{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
                        n=1, max_tokens=2048, temperature=0.0)
    except Exception as e:   # сонголт алгасагдвал санал хураалт хэвээр
        log.warning("pick алгасав: %s", e)
        return None
    return parse_choice(resp.texts[0], first.sql, second.sql)


def parse_choice(text: str, sql_a: str | None = None, sql_b: str | None = None) -> str | None:
    """<answer>A</answer> (эсвэл зөвхөн «A»/«B») — SQL, тайлбар доторх «a»/«b»-г андуурахгүй.
    L4: reasoning (<think>…</think>, prefill-ээр нээгдсэн «…</think>», тасарсан «<think>…») доторх <answer>-ийг
    тооцохгүй; хэд байвал СҮҮЛИЙНХ нь эцсийн шийдвэр. «Query B», «**A**», «A.» хэлбэрийг мөн хүлээн авна.
    sql_a/sql_b өгвөл (V28 dev: 95 дуудлагын 87-д хоёр дарааллын аль алинд нь A/B задлагдаагүй — Arctic нь RL-ийн сургалтын
    хэлбэрээрээ <answer> дотор SQL бичдэг): reasoning-ийн гаднах хаалттай <answer> доторх SQL нь хэвийн болгосноор яг нэг
    хувилбартай ижил бол тэр үсэг (бусад тохиолдолд None — таамаглахгүй)."""
    from ..pipeline.agent import clean_reasoning
    body = re.sub(r"<think>.*?</think>", " ", text or "", flags=re.S | re.I)
    body = re.split(r"</think>", body, flags=re.I)[-1]
    body = re.split(r"<think>", body, flags=re.I)[0]
    found = re.findall(r"<answer>\s*(?:\*\*)?\s*(?:query\s+|option\s+)?([AB])\s*[.)]?\s*(?:\*\*)?\s*</answer>", body, re.I)
    if found:
        return found[-1].upper()
    m = re.fullmatch(r"\s*(?:query\s+)?([AB])[\s.]*", clean_reasoning(text), re.I)
    if m:
        return m.group(1).upper()
    inner = re.findall(r"<answer>(.*?)</answer>", body, re.S | re.I)
    if sql_a and sql_b and inner:
        from ..security.sql_guard import _norm_sql, extract_sql
        got = _norm_sql(extract_sql(f"<answer>{inner[-1]}</answer>"))
        hits = [c for c, s in (("A", sql_a), ("B", sql_b)) if got and _norm_sql(s) == got]
        if len(hits) == 1:
            return hits[0]
    log.info("pick: A/B задлагдсангүй (%d тэмдэгт): …%s", len(text or ""), " ".join((text or "")[-160:].split()))
    return None


def pick_pair(llm, question: str, a, b, diff: bool = False, max_diffs: int = MAX_DIFFS,
              trace: list | None = None) -> tuple[object, str]:
    """(сонгосон хувилбар, шийдвэр): 'keep' — санал хураалт хэвээр, 'swap' — хоёрдахийг сонгосон.

    diff=True (T2, --pick-diff): sqlglot-оор олсон ялгаа 1..max_diffs төрөл бол зөвхөн тэр ялгааг асууна (_ask_diff);
    ялгаа олдоогүй/хэт олон/parse алдаа бол одоогийн бүтэн-SQL асуулт. diff=False үед өмнөх зан төлөв яг хэвээр."""
    ask = _ask
    if diff:
        diffs = diff_clauses(a.sql, b.sql)
        if diffs and len(diffs) <= max_diffs:
            def ask(llm_, q, first, second):
                return _ask_diff(llm_, q, first, second, diffs if first is a else [d.swapped() for d in diffs])
    r1 = ask(llm, question, a, b)           # A = a
    r2 = ask(llm, question, b, a)           # A = b
    if trace is not None:   # оношлогоо: хоёр дарааллын parse-лагдсан хариу («AA» = байрлалын хазайлт/parse, «-» = parse алдаа)
        trace.append(f"{r1 or '-'}{r2 or '-'}")
    if r1 == "B" and r2 == "A":
        return b, "swap"
    return a, "keep"


# ====================================================================== T2: нөхцөлийн түвшний ялгаа (CSC-SQL-ийн хөнгөн хувилбар)

SYSTEM_DIFF = (
    "You are an expert PostgreSQL reviewer. Two candidate queries answer the same question but return different "
    "results. They differ at least in the clauses listed below (other parts may also differ). For each listed difference, "
    "decide which version the question requires (e.g. should the filter be applied, which table holds the requested "
    "records, which aggregation or grouping is asked for). Then choose the query whose differing clauses are correct. "
    "Reply inside <answer></answer> with exactly A or B.")

_KIND_TITLE = {"tables": "Tables used", "where": "WHERE conditions", "group_by": "GROUP BY",
               "aggregates": "Aggregations", "having": "HAVING conditions"}


@dataclass(frozen=True)
class Diff:
    kind: str                 # tables | where | group_by | aggregates | having
    only_a: tuple[str, ...]   # зөвхөн A-д байгаа элемент
    only_b: tuple[str, ...]   # зөвхөн B-д байгаа элемент

    def swapped(self) -> Diff:
        return Diff(self.kind, self.only_b, self.only_a)

    def describe(self) -> str:
        def side(xs):
            return "; ".join(xs) if xs else "(none)"
        return f"{_KIND_TITLE.get(self.kind, self.kind)}: A has {side(self.only_a)} | B has {side(self.only_b)}"


def _norm(node) -> str:
    """Alias/хүснэгтийн угтваргүй, нэг мөрт SQL (ижил нөхцөлийг өөр alias-тай ч ижилсүүлнэ)."""
    from sqlglot import exp
    n = node.copy()
    for c in n.find_all(exp.Column):
        c.set("table", None)
    if isinstance(n, exp.Alias):
        n = n.this
    return " ".join(n.sql(dialect="postgres").split())


def _clauses(sql: str) -> dict[str, set[str]] | None:
    import sqlglot
    from sqlglot import exp
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except Exception:
        return None
    if tree is None:
        return None
    ctes = {c.alias_or_name for c in tree.find_all(exp.CTE)}
    out: dict[str, set[str]] = {k: set() for k in _KIND_TITLE}
    out["tables"] = {t.name for t in tree.find_all(exp.Table) if t.name and t.name not in ctes}
    for kind, cls in (("where", exp.Where), ("having", exp.Having)):
        for w in tree.find_all(cls):
            out[kind] |= {_norm(p) for p in w.this.flatten()} if isinstance(w.this, exp.And) else {_norm(w.this)}
    for g in tree.find_all(exp.Group):
        sel = g.parent if isinstance(g.parent, exp.Select) else None
        for e in g.expressions:
            # GROUP BY 1 → SELECT-ийн 1-р илэрхийлэл (нэрээр/байрлалаар бичсэнийг ижилсүүлнэ)
            if sel is not None and isinstance(e, exp.Literal) and e.is_int and 0 < int(e.this) <= len(sel.expressions):
                e = sel.expressions[int(e.this) - 1]
            out["group_by"].add(_norm(e))
    out["aggregates"] = {_norm(f) for f in tree.find_all(exp.AggFunc)}
    return out


def diff_clauses(sql_a: str, sql_b: str) -> list[Diff] | None:
    """Хоёр SQL-ийн WHERE/HAVING нөхцөл, GROUP BY, агрегат функц, хүснэгтийн олонлогийн ялгаа (sqlglot).

    Буцаах: ялгаатай төрөл бүрт нэг Diff (хоосон жагсаалт = эдгээр clause ижил, ялгаа өөр газар — SELECT/ORDER/JOIN ON);
    None = аль нэгийг parse хийж чадаагүй."""
    ca, cb = _clauses(sql_a), _clauses(sql_b)
    if ca is None or cb is None:
        return None
    out = []
    for kind in ("tables", "where", "group_by", "aggregates", "having"):
        a_only, b_only = ca[kind] - cb[kind], cb[kind] - ca[kind]
        if a_only or b_only:
            out.append(Diff(kind, tuple(sorted(a_only)), tuple(sorted(b_only))))
    return out


def _ask_diff(llm, question: str, first, second, diffs: list[Diff]) -> str | None:
    """Зөвхөн ялгаатай clause-ийн тухай асуух нарийн prompt (A = first). Бүтэн SQL-ийг лавлагаа болгон үзүүлнэ."""
    listed = "\n".join(f"- {d.describe()}" for d in diffs)
    user = (f"Question: {question}\n\nDifferences between the two queries:\n{listed}\n\n"
            f"Query A:\n{first.sql}\nResult A:\n{preview(first.result)}\n\n"
            f"Query B:\n{second.sql}\nResult B:\n{preview(second.result)}\n\n"
            "Which query's version of the differing clauses does the question require?")
    try:
        resp = llm.chat([{"role": "system", "content": SYSTEM_DIFF}, {"role": "user", "content": user}],
                        n=1, max_tokens=2048, temperature=0.0)
    except Exception as e:
        log.warning("pick-diff алгасав: %s", e)
        return None
    return parse_choice(resp.texts[0], first.sql, second.sql)


def load_translations(tag: str | None) -> dict[str, str]:
    """{'<id>:<variant>': англи орчуулга} — run-ий translation_cache-ээс (байхгүй бол хоосон)."""
    if not tag:
        return {}
    p = Path(resolve_path("build/translations")) / f"{tag}.jsonl"
    if not p.exists():
        return {}
    out = {}
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out[f"{r['id']}:{r.get('variant', 'mn')}"] = r["text"]
    return out
