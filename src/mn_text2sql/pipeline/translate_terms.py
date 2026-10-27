"""A4-ийн нэр томьёоны RAG (T2): асуултад орсон монгол нэр томьёог мэдлэгийн сангаас олж орчуулагчид англи хэлбэрийг заана.

Эх сурвалж (test set-ээс огт авахгүй):
* knowledge/translation_terms.yaml — зөвхөн орчуулгад хэрэглэх жижиг толь (давуу эрхтэй; dev-ийн шинжилгээгээр нэмсэн,
  бусад туршилтын prompt-д нөлөөлөхгүй);
* knowledge/semantic_model.yaml — enum утгын монгол нэр → код, хүснэгт.багана («Ажлаас гарсан» → "terminated
  (employees.employment_status …)" — хэнд хамаарахыг заана); хүснэгт/баганын mn → en, syn (баганын шошгоос
  id/name/code үгийг хасна — «Alert id» биш "alert");
* knowledge/glossary.yaml — term, aliases → en.
Нэг монгол хэллэгт олон англи хэлбэр байвал эх сурвалжийн эрэмбээр (толь → утга → glossary → хүснэгт → багана) 2-ыг.
Тааруулалт: монгол үгийн дагаврыг тэвчих угийн (stem) тааралт, урт хэллэг давуу, давхцалгүй, дээд тал нь `limit`.
"""
from __future__ import annotations

import re
from functools import lru_cache

import yaml

from ..config import resolve_path
from ..text.normalize import cyr_key

_WORD = re.compile(r"[a-zа-яёөү0-9]+")
_CYR = re.compile(r"[а-яёөү]")
_COL_TAIL = re.compile(r"(?:\s+(?:id|ids|name|code|flag))+$", re.I)
# эх сурвалжийн эрэмбэ (бага нь давуу)
P_LEXICON, P_VALUE, P_GLOSSARY, P_TABLE, P_COLUMN = range(5)


def _stem(w: str) -> str:
    # монгол дагавар (-ийн, -д, -аас, -гийн, -лүүлгийн …) — урт үгэнд 3 хүртэл үсэг тэвчинэ
    return w if len(w) <= 4 else w[:max(4, len(w) - 3)]


def _en_label(text: str) -> str:
    return re.split(r"\s*[(:;]", str(text))[0].strip()


def _col_label(text: str) -> str:
    """Баганын англи шошгыг орчуулгын үг болгоно: «Alert id» → "alert", «Department name» → "department"."""
    s = _COL_TAIL.sub("", _en_label(text)).strip()
    return " ".join(w if w.isupper() else w.lower() for w in s.split())


@lru_cache(maxsize=4)
def load_lexicon(semantic_path: str = "knowledge/semantic_model.yaml",
                 glossary_path: str = "knowledge/glossary.yaml",
                 lexicon_path: str = "knowledge/translation_terms.yaml") -> tuple[tuple[tuple[str, ...], str], ...]:
    """((stem-үүд, «монгол = англи» мөр), …) — урт хэллэг эхэндээ."""
    raw: dict[str, dict[str, int]] = {}

    def add(mn, en, prio):
        mn, en = cyr_key(_en_label(mn or "")), str(en or "").strip()
        if not mn or not en or not _CYR.search(mn) or len(mn) < 4:
            return
        for key in {mn, " ".join(w for w in _WORD.findall(mn) if _CYR.search(w))}:
            # «AML сэрэмжлүүлэг» г.м. холимог хэллэгийн кирилл хэсгийг ч тусад нь
            if key == mn or len(key) >= 6:
                d = raw.setdefault(key, {})
                d[en] = min(prio, d.get(en, prio))

    lex_file = resolve_path(lexicon_path)
    if lex_file.exists():
        for t in (yaml.safe_load(lex_file.read_text(encoding="utf-8")) or {}).get("terms") or []:
            for mn in [t.get("mn")] + list(t.get("aliases") or []):
                add(mn, t.get("en"), P_LEXICON)
    sem = yaml.safe_load(resolve_path(semantic_path).read_text(encoding="utf-8")) or {}
    for t, spec in (sem.get("tables") or {}).items():
        ten = _en_label(spec.get("en", t))
        add(spec.get("mn", ""), ten, P_TABLE)
        for s in spec.get("syn") or []:
            add(s, ten, P_TABLE)
        for c, cs in (spec.get("columns") or {}).items():
            if not isinstance(cs, dict):
                continue
            cen = _col_label(cs.get("en", c))
            if cen:
                add(cs.get("mn"), cen, P_COLUMN)
                for s in cs.get("syn") or []:
                    add(s, cen, P_COLUMN)
            for code, label in (cs.get("values") or {}).items():
                if not re.search(r"[A-Za-z]", str(code)):
                    continue   # дансны код (4200) г.м. — баганын en хангалттай
                # хүснэгт.багана нь утга хэнд хамаарахыг заана (ажлаас гарсан → ажилтан, харилцагч биш)
                add(label, f'{str(code).lower().replace("_", " ")} ({t}.{c} = {code}, label "{label}")', P_VALUE)
    glo = yaml.safe_load(resolve_path(glossary_path).read_text(encoding="utf-8")) or {}
    for t in glo.get("terms") or []:
        en = _en_label(t.get("en", ""))
        add(t.get("term"), en, P_GLOSSARY)
        for a in t.get("aliases") or []:
            add(a, en, P_GLOSSARY)
    # ижил угтай хэллэгүүдийг (алба / албанд) нэгтгэж эх сурвалжийн эрэмбээр сонгоно
    grouped: dict[tuple[str, ...], tuple[str, dict[str, int]]] = {}
    for mn, ens in raw.items():
        stems = tuple(_stem(w) for w in _WORD.findall(mn))
        if not stems:
            continue
        name, acc = grouped.setdefault(stems, (mn, {}))
        for e, p in ens.items():
            acc[e] = min(p, acc.get(e, p))
        if min(ens.values()) < min(acc.values(), default=99) or len(mn) < len(name):
            grouped[stems] = (mn, acc)
    out = []
    for stems, (mn, ens) in grouped.items():
        best = sorted(ens, key=lambda e: (ens[e], e))[:2]
        out.append((stems, mn, " / ".join(best)))
    out.sort(key=lambda x: (-len(x[0]), -len(x[1])))
    return tuple((s, f"{mn} = {en}") for s, mn, en in out)


def term_hints(question: str, limit: int = 6) -> list[str]:
    """Асуултад орсон нэр томьёоны «монгол = англи» мөрүүд (урт хэллэг эхэндээ, давхцалгүй)."""
    words = _WORD.findall(cyr_key(question))
    used: set[int] = set()
    hints: list[str] = []
    for stems, line in load_lexicon():
        n = len(stems)
        for i in range(len(words) - n + 1):
            if any(i + k in used for k in range(n)):
                continue
            if all(words[i + k].startswith(stems[k]) for k in range(n)):
                used.update(range(i, i + n))
                if line not in hints:
                    hints.append(line)
                break
        if len(hints) >= limit:
            break
    return hints
