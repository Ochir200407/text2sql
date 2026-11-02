"""Хүснэгт мэдэх few-shot сонголт (R5 З1, M1fs): жишээний SQL-ийн хүснэгтийн олонлог асуултын linked хүснэгттэй
таарах тусам жишээг дээш нь эрэмбэлнэ.

docs/research/R5_schema_context.md §2.2/§2.6: манай нэг DB дээр EX-тэй холбоотой нь жишээний skeleton биш, жишээний
хүснэгтийн олонлог gold-ыг «cover» хийх эсэх (cover-тэй 43% / cover-гүй 23% EX, хүндрэлээр давхаргалсан ч хадгалагдсан).
Офлайн симуляцид (BM25 pool 20 → 0.5·оноо + 0.5·Jaccard(жишээний хүснэгт, linked[:3])) cover 67.4 → 78.1%.

Нэмэлт: андуурагддаг хосын (knowledge/table_hints.yaml-ийн 2 хүснэгттэй мөр) хамтрагч нь linked-ийн эхний хүснэгтүүдэд
БАЙХГҮЙ бол түүнийг агуулсан жишээг торгоно — жишээнд байгаа хүснэгтийг загвар ×5–11 илүү ашигладаг (R5 §2.3).

Анхдагч унтраалттай (RunOptions.fewshot_rerank = None) — тэр үед ExampleIndex.search-ийн эрэмбэ огт өөрчлөгдөхгүй.
"""

from __future__ import annotations

from dataclasses import dataclass, field

MODES = ("table_jaccard",)
LINKED_TOP = 3          # prelim хүснэгт = linked_tables[:3] (R5 З1 (а): нэмэлт дуудлагагүй)
WEIGHT = 0.5            # 0.5·асуултын оноо + 0.5·Jaccard (R5 §2.6-тай ижил)
PARTNER_PENALTY = 0.05  # андуурагддаг хосын хамтрагчтай жишээний торгууль (dev cover: 0.2 → −3.5пп cover; 0.05 → −0.9пп, хамтрагч −3.6пп)


@dataclass(frozen=True)
class TableRerank:
    """ExampleIndex.search-д дамжих дахин эрэмбэлэлтийн параметр."""
    tables: frozenset[str]
    avoid: frozenset[str] = field(default_factory=frozenset)
    weight: float = WEIGHT
    penalty: float = PARTNER_PENALTY


def bare(t: str) -> str:
    return t.rsplit(".", 1)[-1].strip().strip('"').lower()


def sql_tables(sql: str) -> frozenset[str]:
    """SQL-ийн бодит хүснэгт (CTE-гүй, жижиг үсгээр). Задлаж чадахгүй бол хоосон."""
    from ..knowledge.table_hints import gold_tables
    try:
        return frozenset(bare(t) for t in gold_tables(sql or ""))
    except Exception:
        return frozenset()


def jaccard(a, b) -> float:
    a, b = set(a), set(b)
    return len(a & b) / len(a | b) if a | b else 0.0


def confusable_pairs() -> tuple[tuple[str, str], ...]:
    """knowledge/table_hints.yaml-ийн яг 2 хүснэгттэй мөрүүд (андуурагддаг хос). Файл алга бол хоосон."""
    try:
        from ..knowledge.table_hints import load_hints
        return tuple((bare(h.tables[0]), bare(h.tables[1])) for h in load_hints() if len(h.tables) == 2)
    except Exception:
        return ()


def partners_to_avoid(linked_top: list[str] | set[str], pairs=None) -> frozenset[str]:
    """linked_top-ийн хүснэгтийн андуурагддаг хамтрагч, гэхдээ өөрөө linked_top-д байхгүй нь."""
    top = {bare(t) for t in linked_top}
    out = set()
    for a, b in (confusable_pairs() if pairs is None else pairs):
        if a in top and b not in top:
            out.add(b)
        if b in top and a not in top:
            out.add(a)
    return frozenset(out)


def make_rerank(mode: str | None, linked: list[str] | None, pairs=None) -> TableRerank | None:
    """RunOptions.fewshot_rerank + linked_tables → TableRerank (None = унтраалттай / linked хоосон)."""
    if not mode:
        return None
    if mode not in MODES:
        raise ValueError(f"fewshot_rerank: үл мэдэгдэх горим {mode!r} (боломжит: {MODES})")
    top = [bare(t) for t in (linked or [])][:LINKED_TOP]
    if not top:
        return None
    return TableRerank(frozenset(top), partners_to_avoid(top, pairs))


def rerank_score(base: float, ex_tables: frozenset[str], rr: TableRerank) -> float:
    s = (1 - rr.weight) * base + rr.weight * jaccard(ex_tables, rr.tables)
    if rr.avoid and ex_tables & rr.avoid:
        s -= rr.penalty
    return s
