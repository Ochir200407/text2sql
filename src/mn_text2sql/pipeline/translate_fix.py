"""A4 орчуулгын кэшийн masking-ийн алдааг (дахин орчуулахгүйгээр) засах — E2 dev шинжилгээ (2026-10-18).

Алдаа: `find_value_spans` нь (1) өгүүлбэрийн эхний том үсэгтэй үгийг (Байвал, Мөрөөр) оноосон нэр гэж, (2) утгын
индексийн бүдэг тааралтыг (байсан → «Баасан», хувьд → «Ховд») хамгаалж [V1] болгосон; орчуулагч [V1]-ийг газар/нэр
гэж ойлгоод «at "байсан"» гэх мэт хуурамч утга үүсгэсэн. Мөн орчуулагч masking хийгээгүй асуултад зохиомол [V1] бичсэн,
хүчинтэй утгыг дагавартай нь («Эрдэнэт салбараас») ишлэлд оруулсан.

Засвар (зөвхөн кэшийн өгөгдлөөр, GPU-гүй):
* span хүчинтэй = regex (бизнес түлхүүр, код, огноо, ишлэл) эсвэл дагаврыг (≤ 5 тэмдэгт) хассан хэлбэр нь DB-ийн
  оноосон нэрийн баганын утгатай яг тэнцүү — тэгвэл англи текстэд DB-д хадгалагдсан хэлбэрээр («Эрдэнэт салбар»);
* хүчингүй span эсвэл зохиомол [Vn] байвал англи текстэд хүчингүйг эх үгээр, зохиомлыг хасч, асуултыг H2 хэлбэрээр
  (монгол асуулт + англи мөр) өгнө — H2 нь dev-д H4-тэй бараг ижил (109 vs 110) тул эрсдэл бага;
* валютын буруу нэр: «Tenge» → «tugrik (MNT)».
"""
from __future__ import annotations

import re

from ..text.normalize import cyr_key
from .translate import _PATTERNS

_PH = re.compile(r"\[V\d+\]")
_TENGE = re.compile(r"\btenge\b", re.I)


def entity_keys(executor, columns: set[str], schema: str = "t2s") -> dict[str, str]:
    """DB-ийн оноосон нэрийн баганын (branch_name, province ...) утгууд: {cyr_key: хадгалагдсан утга}."""
    r = executor.run("SELECT table_name, column_name FROM information_schema.columns "
                     f"WHERE table_schema = '{schema}'")
    keys: dict[str, str] = {}
    for t, c in (r.rows if r.ok else []):
        if str(c).lower() not in columns:
            continue
        v = executor.run(f'SELECT DISTINCT "{c}"::text FROM {schema}."{t}" WHERE "{c}" IS NOT NULL')
        for (x,) in (v.rows if v.ok else []):
            if x:
                keys.setdefault(cyr_key(str(x)), str(x))
    return keys


def span_value(span: str, source: str, keys: dict[str, str]) -> str | bool | None:
    """True = regex/ишлэл (хэвээр); str = DB-д хадгалагдсан утга (дагаврыг хассан); None = хүчингүй (хуурамч)."""
    if any(p.fullmatch(span) for p in _PATTERNS[:3]):
        return True
    if re.search(r"[«\"“']" + re.escape(span) + r"[»\"”']", source):
        return True
    k = cyr_key(span)
    if len(k) < 4:
        return None
    for cut in range(0, 6):
        if len(k) - cut >= 3 and k[:len(k) - cut] in keys:
            return keys[k[:len(k) - cut]]
    return None


def fix_record(rec: dict, keys: dict[str, str]) -> tuple[str, list[str]]:
    """(шинэ англи/хоёр хэлт текст, засварын шалтгаанууд). Засвар хэрэггүй бол (эх text, [])."""
    source, raw, text = rec.get("source") or "", rec.get("raw") or "", rec.get("text") or ""
    spans: dict[str, str] = rec.get("spans") or {}
    reasons: list[str] = []
    vals = {k: span_value(v, source, keys) for k, v in spans.items()}
    bad = {k for k, v in vals.items() if v is None}
    canon = {k: v for k, v in vals.items() if isinstance(v, str) and v != spans[k]}
    invented = [p for p in set(_PH.findall(raw)) if p not in spans]
    if bad:
        reasons.append("spurious_mask")
    if invented:
        reasons.append("invented_placeholder")
    if canon and not (bad or invented):
        reasons.append("canonical_value")
    out = text
    if bad or invented or canon:
        out = raw
        for k, v in spans.items():
            out = out.replace(k, v if k in bad else f'"{canon.get(k, v)}"')
        out = re.sub(r"\s{2,}", " ", _PH.sub("", out)).replace(" ?", "?").strip()
    if _TENGE.search(out):
        out = _TENGE.sub("tugrik (MNT)", out)
        reasons.append("currency_name")
    if bad or invented:
        out = f"{source}\n(English translation: {out})"
    return (out, reasons) if reasons else (text, [])
