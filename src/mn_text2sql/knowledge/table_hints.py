"""Хүснэгтийн ялгах зөвлөмж (table_hints, M1th): төстэй хүснэгтийн хосыг (loans ↔ loan_applications г.м.) ялгах богино
англи мөрийг prompt-ын evidence-д нэмнэ.

dev-ийн шинжилгээгээр gold хүснэгт prompt-д 97% байсан ч буруу хариултын 13–22% нь төстэй хүснэгтийг андуурсан
(docs/SQL_QUALITY_AND_GAP.md §2b). Зөвлөмжийн агуулга knowledge/table_hints.yaml-д (semantic_model.yaml-ийн тайлбараас).

Сонголтын дүрэм:
  * зөвлөмжийн БҮХ хүснэгт prompt-ийн хүснэгтэд байна (байхгүй хүснэгтийг андуурах боломжгүй);
  * ядаж нэг хүснэгт нь linked_tables-д (retrieval-ийн хамааралтай хүснэгт) байна;
  * linked_tables дахь хамгийн эхний байрлалаар эрэмбэлж (тэнцвэл файлын дарааллаар), ≤ cap мөр.

T1 (M1ip, implied_preds): мөн файлын default_predicates — хүснэгтийн далд нөхцөл (POSTED төлбөр, идэвхтэй багц ...),
dev-ийн gold SQL-ээр баталгаажуулсан (predicate_support); implied_predicates() → «Implied condition: ...» мөрүүд.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

import yaml

from ..config import resolve_path

DEFAULT_PATH = "knowledge/table_hints.yaml"
DEFAULT_CAP = 5


@dataclass(frozen=True)
class TableHint:
    tables: tuple[str, ...]
    hint: str


@lru_cache(maxsize=4)
def load_hints(path: str = DEFAULT_PATH) -> tuple[TableHint, ...]:
    """YAML-аас зөвлөмжүүдийг уншина (кэштэй). Хүснэгтгүй эсвэл хоосон мөрийг ValueError-оор илрүүлнэ."""
    with open(resolve_path(path), encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    out = []
    for i, h in enumerate(data.get("hints") or []):
        tables = tuple(str(t).strip() for t in (h.get("tables") or []))
        text = " ".join(str(h.get("hint") or "").split())
        if not tables or not text:
            raise ValueError(f"{path}: {i}-р зөвлөмжид tables/hint дутуу")
        out.append(TableHint(tables, text))
    return tuple(out)


def _bare(t: str) -> str:
    # "t2s.loans" → "loans" (схемийн угтвартай/угтваргүй нэрийг ижилсүүлнэ)
    return t.rsplit(".", 1)[-1].strip().lower()


def select_hints(linked: list[str] | None, prompt_tables: list[str], hints: tuple[TableHint, ...] | None = None,
                 cap: int = DEFAULT_CAP) -> list[str]:
    """linked (retrieval-ийн эрэмбэтэй хүснэгт) ба prompt-ын хүснэгтэд тохирох зөвлөмжийн мөрүүд (≤ cap).

    linked хоосон/None бол prompt-ын хүснэгтийг linked гэж үзнэ (retrieval-гүй тохиргоо)."""
    hints = load_hints() if hints is None else hints
    in_prompt = {_bare(t) for t in prompt_tables}
    order = [_bare(t) for t in (linked or prompt_tables)]
    rank = {}
    for i, t in enumerate(order):
        rank.setdefault(t, i)
    scored = []
    for j, h in enumerate(hints):
        if not all(t in in_prompt for t in h.tables):
            continue
        r = [rank[t] for t in h.tables if t in rank]
        if not r:
            continue
        # Хосын хоёр хүснэгт хоёулаа linked бол түрүүнд (андуурах магадлал өндөр), дараа нь эхний байрлалаар
        scored.append((len(h.tables) - len(r), min(r), j, h.hint))
    scored.sort()
    return [s[-1] for s in scored[:max(0, cap)]]


# ====================================================================== T1: далд нөхцөл (implied predicates, M1ip)
# Хүснэгтийн «анхдагч» бизнесийн дүрэм (POSTED төлбөр, идэвхтэй хадгаламж ...)-ийг асуултад шууд хэлээгүй ч gold SQL
# тогтмол хэрэглэдэг. Дүрэм бүрийг ЗӨВХӨН dev-ийн gold SQL-ээр баталгаажуулж (predicate_support), тоог YAML-д бичсэн.
# Асуултад эсрэг утгатай үг (skip: failed, closed ...) байвал алгасна; when: байвал зөвхөн тэр үг байхад.
DEFAULT_PRED_CAP = 4


@dataclass(frozen=True)
class ImpliedPredicate:
    table: str
    sql: str                      # жишээ нь "status = 'POSTED'"
    hint: str                     # prompt-д орох англи мөр
    pattern: str                  # gold SQL-д нөхцөл байгааг илрүүлэх regex (dev-ийн дэмжлэг тоолоход)
    skip: tuple[str, ...] = ()    # асуултад (англи) таарвал алгасах regex-үүд
    when: str | None = None       # заавал биш: асуултад таарах үед л идэвхжинэ
    keep: str | None = None       # заавал биш: асуултад таарвал skip-ээс үл хамааран идэвхжинэ («active-portfolio» г.м.)


@lru_cache(maxsize=4)
def load_predicates(path: str = DEFAULT_PATH) -> tuple[ImpliedPredicate, ...]:
    """YAML-ын default_predicates хэсэг (кэштэй). Дутуу талбарыг ValueError-оор илрүүлнэ."""
    with open(resolve_path(path), encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}
    out = []
    for i, p in enumerate(data.get("default_predicates") or []):
        table = str(p.get("table") or "").strip()
        sql = " ".join(str(p.get("sql") or "").split())
        hint = " ".join(str(p.get("hint") or "").split())
        pattern = str(p.get("pattern") or "")
        if not table or not sql or not hint or not pattern:
            raise ValueError(f"{path}: {i}-р default_predicates-д table/sql/hint/pattern дутуу")
        re.compile(pattern)
        skip = tuple(str(s) for s in (p.get("skip") or []))
        for s in skip:
            re.compile(s)
        when, keep = p.get("when") or None, p.get("keep") or None
        for r in (when, keep):
            if r:
                re.compile(r)
        out.append(ImpliedPredicate(table, sql, hint, pattern, skip, when, keep))
    return tuple(out)


def _fires(p: ImpliedPredicate, question: str) -> bool:
    q = question or ""
    if p.when and not re.search(p.when, q, re.I):
        return False
    if p.keep and re.search(p.keep, q, re.I):
        return True
    return not any(re.search(s, q, re.I) for s in p.skip)


def implied_predicates(linked: list[str] | None, question: str, prompt_tables: list[str] | None = None,
                       preds: tuple[ImpliedPredicate, ...] | None = None, cap: int = DEFAULT_PRED_CAP) -> list[str]:
    """linked хүснэгтэд (prompt-д байгаа бол) хамаарах далд нөхцөлийн англи мөрүүд (≤ cap, файлын дарааллаар).

    question нь загварт өгөх англи асуулт (орчуулга); skip үг таарвал тухайн дүрмийг алгасна."""
    preds = load_predicates() if preds is None else preds
    linked_set = {_bare(t) for t in (linked or prompt_tables or [])}
    if prompt_tables is not None:
        linked_set &= {_bare(t) for t in prompt_tables}
    out = [p.hint for p in preds if p.table in linked_set and _fires(p, question)]
    return out[:max(0, cap)]


def gold_tables(sql: str) -> set[str]:
    """SQL-ийн бодит хүснэгтийн нэрс (CTE-ийн нэрийг хасна; sqlglot)."""
    import sqlglot
    from sqlglot import exp
    try:
        tree = sqlglot.parse_one(sql, read="postgres")
    except Exception:
        return set()
    ctes = {c.alias_or_name for c in tree.find_all(exp.CTE)}
    return {t.name for t in tree.find_all(exp.Table) if t.name and t.name not in ctes}


def predicate_support(items: list[dict], preds: tuple[ImpliedPredicate, ...] | None = None) -> list[dict]:
    """Dev-ийн gold SQL дээрх дүрэм бүрийн дэмжлэг (items: {question_en, gold_sql}; ЗӨВХӨН dev-ээр дуудна):
    table_queries — хүснэгтийг хэрэглэсэн gold; with_predicate — тэдгээрээс нөхцөлтэй нь;
    fires — дүрэм идэвхжих (skip/when-ийн дараа) асуулт; fires_with_predicate — идэвхжээд gold-д нөхцөл байгаа нь."""
    preds = load_predicates() if preds is None else preds
    parsed = [(it.get("question_en") or "", it["gold_sql"], gold_tables(it["gold_sql"]))
              for it in items if it.get("gold_sql")]
    out = []
    for p in preds:
        rel = [(q, s) for q, s, ts in parsed if p.table in ts]
        has = [bool(re.search(p.pattern, s, re.I)) for _, s in rel]
        fire = [_fires(p, q) for q, _ in rel]
        out.append({"table": p.table, "sql": p.sql, "table_queries": len(rel), "with_predicate": sum(has),
                    "fires": sum(fire), "fires_with_predicate": sum(f and h for f, h in zip(fire, has, strict=True))})
    return out
