"""R1.2: орчуулгын дараах тогтмол шалгалт (GPU-гүй, цэвэр функц) → тэмдэглэгдсэн асуултад bilingual fallback.

`translation_check(source, text)` нь монгол асуулт ба англи орчуулгыг харьцуулж, сэжигтэй бол шалтгаануудыг буцаана:

* ``placeholder_left`` — англид `[V1]` г.м. placeholder үлдсэн (R6: 4 асуулт, бүгд буруу);
* ``cyrillic_left`` — хашилтаас гадуур кирилл үг үлдсэн (орчуулагдаагүй);
* ``cyrillic_quoted`` — хашилтад орсон кирилл хэсэг нь хамгаалсан DB-ийн утга биш («байсан», «Сарын», «Мөрөөр» —
  R6 1b: 38 асуулт, EX 18%). Хүчинтэй утга = эх асуултад хашилттай бичигдсэн, `values`-д байгаа, эсвэл `keys`
  (DB-ийн утга; `translate_fix.span_value`-ийн дагавар хасах дүрэм) -д таарсан. `keys` өгөөгүй бол: том үсгээр
  эхэлсэн, эх асуултад өгүүлбэрийн эхэнд биш байрлалд байгаа (оноосон нэрийн `find_value_spans`-ийн дүрэм);
* ``number_missing`` — эх асуултын тоо (5 сая = 5000000, «2025 оны 3-р сар» → 2025, 3, «1-р улирал» → 1) англид
  алга. Англид «March», «first quarter», «three», «5 million», «5,000,000» г.м. хэлбэрийг тоо болгож харьцуулна;
* ``number_added`` — англид эх асуултад байхгүй цифр гарсан (зохиомол он, тоо);
* ``value_lost`` — хамгаалах ёстой утга (бизнес түлхүүр, салбарын код, эх асуултын ишлэл, `values`) англид алга эсвэл
  зөвхөн `unmask`-ийн төгсгөлд нэмсэн ` ("утга")` хэлбэрээр л байна.

Кэшийн H2 хэлбэрийн мөр (``source\\n(English translation: …)``, translate_fix) аль хэдийн хоёр хэлт тул шалгахгүй.
Офлайн үнэлгээ: scripts/translate/check_report.py (зөвхөн dev кэш). Тохиргоо: RunOptions.translate_check.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

from ..text.normalize import cyr_key

BILINGUAL_MARK = "(English translation:"

_CYR_WORD = re.compile(r"[А-Яа-яЁёӨөҮү][А-Яа-яЁёӨөҮү\-]*")
_PH = re.compile(r"\[V\d+\]")
_QUOTED = re.compile(r"[\"“«]([^\"”»]{1,80})[\"”»]")
_APPENDED = re.compile(r'\s\("([^"]+)"\)\s*$')
# Хамгаалах утгын regex (translate._PATTERNS-ийн эхний 2 — огноог тоогоор шалгана)
_KEY_PATTERNS = [re.compile(r"\b[A-Z]{2,4}-\d{4}-\d{3,}\b"), re.compile(r"\b[A-Z]{2}\d{2}\b")]
_SRC_QUOTE = re.compile(r"[«\"“]([^»\"”]{2,60})[»\"”]")

# Тоо: 5,000,000 | 1.5 | 30 (англи/монгол хоёуланд). Монгол «1,5» ховор тул таслалыг мянгатын тусгаарлагч гэж л үзнэ.
_NUM = re.compile(r"\d{1,3}(?:,\d{3})+(?![\d,])|\d+(?:\.\d+)?")
_MN_MULT = [(re.compile(r"^\s*-?\s*(мянга|myanga|mynga)", re.I), 1000),
            (re.compile(r"^\s*-?\s*(сая|saya|say\b|sya\b)", re.I), 1_000_000),
            (re.compile(r"^\s*-?\s*(тэрбум|terbum)", re.I), 1_000_000_000)]
_EN_MULT = [(re.compile(r"^\s*(thousand|k\b)", re.I), 1000),
            (re.compile(r"^\s*(million|mln\b)", re.I), 1_000_000),
            (re.compile(r"^\s*(billion|bn\b)", re.I), 1_000_000_000)]
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
           "november", "december"]
_MONTH_ABBR = {m[:3]: i for i, m in enumerate(_MONTHS, 1)}
_WORDS = {w: i for i, w in enumerate(
    "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen "
    "eighteen nineteen twenty".split())}
_WORDS.update({"thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
               "hundred": 100, "dozen": 12, "single": 1, "both": 2})
_ORD = {w: i for i, w in enumerate(
    "zeroth first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth".split())}
_EN_WORD = re.compile(r"[A-Za-z]+")


def english_part(text: str) -> str:
    """H2 хэлбэрийн мөрөөс англи хэсгийг (байхгүй бол бүх текстийг)."""
    if BILINGUAL_MARK in text:
        return text.split(BILINGUAL_MARK, 1)[1].rsplit(")", 1)[0].strip()
    return text


def _to_dec(s: str) -> Decimal | None:
    try:
        return Decimal(s.replace(",", ""))
    except InvalidOperation:
        return None


def _numbers(text: str, mults) -> tuple[set[Decimal], set[Decimal]]:
    """(утгууд: үржүүлэгчтэй бол n×mult, цифрээр бичигдсэн «түүхий» утгууд)."""
    vals, raw = set(), set()
    for m in _NUM.finditer(text):
        d = _to_dec(m.group(0))
        if d is None:
            continue
        rest = text[m.end():m.end() + 12]
        for pat, k in mults:
            if pat.match(rest):
                d = d * k
                break
        vals.add(d.normalize())
        raw.add(_to_dec(m.group(0)).normalize())
    return vals, raw


def source_numbers(source: str) -> set[Decimal]:
    """Монгол (кирилл/галиг) асуултын тоонууд: «5 сая» → 5000000, «3-р сар» → 3, «2024–2025» → 2024, 2025."""
    return _numbers(source, _MN_MULT)[0]


def english_numbers(text: str) -> tuple[set[Decimal], set[Decimal]]:
    """(англи текстэд илэрхийлэгдсэн бүх тоо — цифр, сар, үгээр бичсэн тоо/дэс тоо; зөвхөн цифрээр бичсэн тоо)."""
    vals, raw = _numbers(text, _EN_MULT)
    vals |= raw
    for tok in _EN_WORD.findall(text):
        w = tok.lower()
        if w in _MONTHS and tok[0].isupper():          # «May 2026» (сар) ≠ «may» (модаль үйл үг)
            vals.add(Decimal(_MONTHS.index(w) + 1))
        elif w in _MONTH_ABBR and tok[0].isupper() and w != "may":
            vals.add(Decimal(_MONTH_ABBR[w]))
        elif w in _WORDS:
            vals.add(Decimal(_WORDS[w]))
        elif w in _ORD:
            vals.add(Decimal(_ORD[w]))
    # «three million» → 3000000
    for m in re.finditer(r"\b([a-z]+)\s+(thousand|million|billion)\b", text.lower()):
        if m.group(1) in _WORDS:
            vals.add(Decimal(_WORDS[m.group(1)]) * {"thousand": 1000, "million": 10**6, "billion": 10**9}[m.group(2)])
    return {v.normalize() for v in vals}, raw


def _src_words(source: str) -> list[tuple[str, bool]]:
    """[(кирилл үг, 2-р болон дараагийн өгүүлбэрийн эхний үг эсэх)]."""
    out = []
    for k, sent in enumerate(re.split(r"(?<=[.?!:])\s+", source.strip())):
        for i, w in enumerate(_CYR_WORD.findall(sent)):
            # асуултын хамгийн эхний үгийг find_value_spans том үсгээр хамгаалдаггүй (зөвхөн DB-ийн тааралтаар) — тооцохгүй
            out.append((w, i == 0 and k > 0))
    return out


def _quoted_ok(seg: str, source: str, values: set[str], keys: dict[str, str] | None) -> bool:
    k = cyr_key(seg)
    if k in values:
        return True
    if any(cyr_key(q) == k for q in _SRC_QUOTE.findall(source)):
        return True
    first = cyr_key(seg.split()[0])
    stem = first[:max(min(len(first), 4), len(first) - 3)]   # дагаврыг (≤ 3) хасна
    hits = [initial for w, initial in _src_words(source) if cyr_key(w).startswith(stem)]
    if hits and all(hits):
        # эх асуултад зөвхөн өгүүлбэрийн эхэнд том үсгээр («Сарын …», «Мөрөөр биш …») — оноосон нэр биш (R6 1b)
        return False
    if keys is not None:
        from .translate_fix import span_value
        return span_value(seg, source, keys) is not None or k in {cyr_key(v) for v in keys.values()}
    # DB-гүй горим: оноосон нэр = том үсгээр эхэлсэн (find_value_spans-ийн дүрэм)
    return seg[:1].isupper()


def translation_check(source: str, text: str, values: list[str] | set[str] | None = None,
                      keys: dict[str, str] | None = None) -> list[str]:
    """Орчуулгын сэжигтэй шинжүүд (хоосон = асуудалгүй). source = монгол асуулт, text = англи орчуулга (кэшийн
    `text`), values = хамгаалсан утгууд (кэшийн `spans`-ийн утга, заавал биш), keys = {cyr_key: DB-ийн утга}."""
    if not text or not source:
        return []
    if BILINGUAL_MARK in text:          # translate_fix аль хэдийн H2 болгосон
        return []
    vals = {cyr_key(v) for v in (values or []) if v}
    flags: list[str] = []
    en = text
    if _PH.search(en):
        flags.append("placeholder_left")
    # --- хамгаалсан утга алдагдсан эсэх (unmask төгсгөлд нэмсэн бол алдагдсан гэж үзнэ)
    appended = []
    m = _APPENDED.search(en)
    while m:
        appended.append(m.group(1))
        en = en[:m.start()]
        m = _APPENDED.search(en)
    must = [v for v in (values or []) if v]
    for pat in _KEY_PATTERNS:
        must += pat.findall(source)
    must += _SRC_QUOTE.findall(source)
    low = en.lower()
    if appended or any(v.lower() not in low for v in must):
        flags.append("value_lost")
    # --- кирилл үлдэгдэл
    bad_quoted = False
    for seg in _QUOTED.findall(en):
        if _CYR_WORD.search(seg) and not _quoted_ok(seg.strip(), source, vals, keys):
            bad_quoted = True
    if bad_quoted:
        flags.append("cyrillic_quoted")
    outside = _QUOTED.sub(" ", en)
    for v in must:
        outside = outside.replace(v, " ")
    if _CYR_WORD.search(outside):
        flags.append("cyrillic_left")
    # --- тоо
    src_nums = source_numbers(source)
    src_raw = _numbers(source, [])[0]
    en_vals, en_raw = english_numbers(_PH.sub(" ", en))     # [V1]-ийн цифр тоо биш
    if any(n not in en_vals for n in src_nums):
        flags.append("number_missing")
    if any(n not in src_nums and n not in src_raw for n in en_raw):
        flags.append("number_added")
    return flags


def keys_from_values(entries) -> dict[str, str]:
    """Утгын индексийн мөрүүдээс ({value} эсвэл ValueEntry) кирилл утгын {cyr_key: утга} (DB-гүй keys)."""
    out: dict[str, str] = {}
    for e in entries:
        v = e.get("value") if isinstance(e, dict) else getattr(e, "value", None)
        if v and _CYR_WORD.search(str(v)):
            out.setdefault(cyr_key(str(v)), str(v))
    return out
