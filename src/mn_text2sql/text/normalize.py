"""Монгол текстийн хэвийн болголт (зааврын 2-р хэсэг "Монгол утга ба латин галиг").

* Кирилл түлхүүр: жижиг үсэг, Unicode NFC.
* Сул латин түлхүүр: кириллийг тогтмол схемээр латин болгож хувилбаруудыг нэгтгэнэ
  (kh/h→h, ts/c→c, ө/о→o, ү/у→u, давхар эгшиг→дан, ь/ъ хасах).
* Галиг (латин) асуултыг кирилл рүү: дүрмийн хувиргалт + үгсийн сангаар засах.
* Нөхцөлийн дагавар хасах: «Улаанбаатарт», «Улаанбаатараас» → «Улаанбаатар».
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache

CYR = set("абвгдеёжзийклмноөпрстуүфхцчшщъыьэюя")
_WORD = re.compile(r"[0-9A-Za-zА-Яа-яЁёӨөҮү]+(?:[-'][0-9A-Za-zА-Яа-яЁёӨөҮү]+)*")

# Кирилл → латин (каноник, хэрэглэгчийн "v/u/w" хэв маяг биш, сул түлхүүрийн суурь)
CYR2LAT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "j", "з": "z", "и": "i",
    "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "ө": "o", "п": "p", "р": "r", "с": "s",
    "т": "t", "у": "u", "ү": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "",
    "ы": "i", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}
# Хэрэглэгчийн хоёр галигийн хэв маяг (eval set-ийн question_latin-тай ижил)
STYLE_VUW = dict(CYR2LAT, **{"в": "w", "ө": "u", "ү": "v", "х": "h", "ц": "ts", "ы": "ii", "ь": "i", "е": "e"})
STYLE_UOV = dict(CYR2LAT, **{"в": "v", "ө": "o", "ү": "u", "х": "kh", "ц": "ts", "ы": "y", "ь": "", "е": "e"})


def nfc(text: str) -> str:
    return unicodedata.normalize("NFC", text or "")


def cyr_key(text: str) -> str:
    """Кирилл түлхүүр: NFC + жижиг үсэг + илүү зай хасах."""
    return re.sub(r"\s+", " ", nfc(text).lower()).strip()


def is_cyrillic(text: str) -> bool:
    letters = [c for c in text.lower() if c.isalpha()]
    return bool(letters) and sum(c in CYR for c in letters) / len(letters) > 0.5


def script_of(text: str) -> str:
    letters = [c for c in (text or "").lower() if c.isalpha()]
    if not letters:
        return "none"
    share = sum(c in CYR for c in letters) / len(letters)
    if share > 0.85:
        return "cyrillic"
    if share < 0.15:
        return "latin"
    return "mixed"


def translit(text: str, style: dict | None = None) -> str:
    style = style or CYR2LAT
    out = []
    for ch in nfc(text):
        low = ch.lower()
        if low in style:
            t = style[low]
            out.append(t.upper() if ch.isupper() and t else t)
        else:
            out.append(ch)
    return "".join(out)


def _collapse_latin(s: str) -> str:
    """Галигийн хувилбаруудыг нэгтгэнэ: kh/h, ts/c, w/v, ө/о/ү/у → o (хэрэглэгч ө-г u, ү-г v гэж бичдэг),
    давхар үсэг → дан, ь/ъ/апостроф хасна."""
    s = s.lower()
    s = s.replace("kh", "h").replace("ts", "c").replace("zh", "j").replace("sh", "š").replace("ch", "č")
    s = s.replace("w", "v").replace("q", "o").replace("ö", "o").replace("ü", "o").replace("u", "o")
    s = s.replace("y", "i")
    s = re.sub(r"(.)\1+", r"\1", s)
    s = s.replace("'", "").replace("ʼ", "")
    return s


def weak_latin_key(text: str, v_as: str = "u") -> str:
    """Сул латин түлхүүр. Латин оролтод 'v' нь ү (v_as='u') эсвэл в (v_as='v') байж болох тул хоёр хувилбар."""
    text = nfc(text)
    if any(c in CYR for c in text.lower()):
        base = translit(text.lower(), CYR2LAT)
        return _collapse_latin(base)
    s = text.lower()
    if v_as == "u":
        s = s.replace("v", "o")
    return _collapse_latin(s)


def weak_latin_keys(text: str) -> list[str]:
    """Латин оролтын хоёрдмол 'v'-г хоёуланг нь үүсгэнэ (кирилл бол нэг түлхүүр)."""
    if any(c in CYR for c in nfc(text).lower()):
        return [weak_latin_key(text)]
    a, b = weak_latin_key(text, "u"), weak_latin_key(text, "v")
    return [a] if a == b else [a, b]


def tokens(text: str) -> list[str]:
    return _WORD.findall(nfc(text))


def char_ngrams(text: str, n_min: int = 2, n_max: int = 4) -> list[str]:
    s = f" {text} "
    out = []
    for n in range(n_min, n_max + 1):
        out.extend(s[i:i + n] for i in range(len(s) - n + 1))
    return out


# ---------------------------------------------------------------------------
# Нөхцөлийн дагавар (кирилл ба галиг)
# ---------------------------------------------------------------------------

_CYR_SUFFIXES = sorted([
    "ийнхээ", "ынхаа", "ийнх", "ынх", "аасаа", "ээсээ", "оосоо", "өөсөө", "аараа", "ээрээ", "оороо", "өөрөө",
    "тайгаа", "тэйгээ", "тойгоо", "нуудын", "нүүдийн", "уудын", "үүдийн", "ууд", "үүд", "нууд", "нүүд", "чууд",
    "чүүд", "ийн", "ын", "ий", "ы", "ийг", "ыг", "г", "аас", "ээс", "оос", "өөс", "аар", "ээр", "оор", "өөр",
    "тай", "тэй", "той", "руу", "рүү", "луу", "лүү", "д", "т", "аа", "ээ", "оо", "өө", "н", "ад", "эд", "од", "өд",
    "ыхаа", "ийхээ", "ний", "ны",
], key=len, reverse=True)
_LAT_SUFFIXES = sorted([
    "iinhee", "iinh", "iin", "iig", "ig", "aas", "ees", "oos", "uus", "aar", "eer", "oor", "uur", "tai", "tei",
    "toi", "ruu", "rvv", "luu", "lvv", "uud", "vvd", "nuud", "nvvd", "aa", "ee", "oo", "d", "t", "n", "g", "ii",
    "ni", "nii",
], key=len, reverse=True)


# н-ээр төгссөн нэрийн харьяалах (Дархан + ы = Дарханы, darhan + ii) — н-ийг үндэст үлдээнэ. Нуугдмал н-тэй
# үгийн (ус + ны = усны) хэлбэрийг stem_variants нэмэлт хувилбараар өгнө.
_N_GENITIVE = {"ны": 1, "ний": 2, "nii": 2, "ni": 1}


def strip_suffix(word: str, min_stem: int = 3) -> str:
    w = nfc(word).lower()
    suffixes = _CYR_SUFFIXES if any(c in CYR for c in w) else _LAT_SUFFIXES
    for suf in suffixes:
        if w.endswith(suf) and len(w) - len(suf) >= min_stem:
            if suf in _N_GENITIVE:
                return w[: -_N_GENITIVE[suf]]
            return w[: -len(suf)]
    return w


def stem_variants(word: str) -> list[str]:
    """Үг, түүний дагаваргүй хэлбэр(үүд) (н-тэй ба нуугдмал н-гүй хувилбарыг хоёуланг)."""
    w = nfc(word).lower()
    out = [w]
    s1 = strip_suffix(w)
    if s1 != w:
        out.append(s1)
        s2 = strip_suffix(s1)
        if s2 != s1:
            out.append(s2)
    for suf in _N_GENITIVE:
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            out.append(w[: -len(suf)])
            break
    return list(dict.fromkeys(out))


# ---------------------------------------------------------------------------
# Галиг → кирилл
# ---------------------------------------------------------------------------

_LAT2CYR_RULES = [
    ("kh", "х"), ("sh", "ш"), ("ch", "ч"), ("ts", "ц"), ("ya", "я"), ("yu", "ю"), ("yo", "ё"), ("ye", "е"),
    ("ii", "ий"), ("ai", "ай"), ("ei", "эй"), ("oi", "ой"), ("ui", "уй"),
]
_LAT2CYR_CHAR = {
    "a": "а", "b": "б", "c": "ц", "d": "д", "e": "э", "f": "ф", "g": "г", "h": "х", "i": "и", "j": "ж", "k": "к",
    "l": "л", "m": "м", "n": "н", "o": "о", "p": "п", "q": "ө", "r": "р", "s": "с", "t": "т", "u": "у", "v": "ү",
    "w": "в", "x": "х", "y": "ы", "z": "з", "ö": "ө", "ü": "ү",
}


def latin_to_cyrillic_rule(word: str) -> str:
    """Дүрмийн хувиргалт ("v/u/w" хэв маягт тааруулсан). Үгийн сангаар засахаас өмнөх эхний таамаг."""
    w = word.lower()
    out = []
    i = 0
    while i < len(w):
        for lat, cyr in _LAT2CYR_RULES:
            if w.startswith(lat, i):
                out.append(cyr)
                i += len(lat)
                break
        else:
            out.append(_LAT2CYR_CHAR.get(w[i], w[i]))
            i += 1
    res = "".join(out)
    # эхний "е" нь ихэвчлэн "э" (кирилл "е" нь йе) — тогтмол засвар
    return res


class Transliterator:
    """Галиг үгийг кирилл үгийн сангаас (DB утга, схемийн тайлбар, нэр томьёо, асуултын корпус) сул түлхүүрээр олно."""

    def __init__(self, vocabulary: list[str] | None = None, improved: bool = False):
        """improved=True (MT-ийн латин оролтод; docs/MT_INVARIANT_PLAN.md §5.4): (1) 'v'/'u' хоёрдмол түлхүүрийн АЛЬ Ч нэр дэвшигчээс
        хамгийн их давтамжтайг сонгоно (өмнө нь эхний түлхүүр ялдаг: «we» → «үе»); (2) төгрөлдөг ганц 'i' = зөөлний тэмдэг ь
        (ni → нь, huwi → хувь, ali → аль); (3) «6-r» → «6-р». dev дээр үгийн нарийвчлал 89.1% → 94.8% (eval текстгүй үгийн сангаар).
        Анхдагч нь хуучин зан төлөв — retrieval/туршилтууд өөрчлөгдөхгүй."""
        self.by_key: dict[str, dict[str, int]] = {}
        self.improved = improved
        for w in vocabulary or []:
            self.add(w)

    def add(self, word: str, weight: int = 1) -> None:
        for tok in tokens(word):
            if not any(c in CYR for c in tok.lower()):
                continue
            low = tok.lower()
            key = weak_latin_key(low)
            bucket = self.by_key.setdefault(key, {})
            bucket[low] = bucket.get(low, 0) + weight

    def add_pair(self, lat: str, cyr: str, weight: int = 1000) -> None:
        """F3: лог-оос сурсан хос — галигийн сул түлхүүрт кирилл үгийг өндөр жинтэйгээр бүртгэнэ."""
        for key in weak_latin_keys(lat.lower()):
            bucket = self.by_key.setdefault(key, {})
            bucket[cyr.lower()] = bucket.get(cyr.lower(), 0) + weight

    def _best(self, lat: str) -> tuple[str, int] | None:
        """Бүх сул түлхүүрийн нэр дэвшигчээс хамгийн их жинтэй (үг, жин)."""
        best = None
        for key in weak_latin_keys(lat):
            for w, c in (self.by_key.get(key) or {}).items():
                if best is None or c > best[1]:
                    best = (w, c)
        return best

    def _lookup(self, lat: str) -> str | None:
        if self.improved:
            best = self._best(lat)
            low = lat.lower()
            if low.endswith("i") and not low.endswith("ii") and len(low) >= 2:
                soft = self._best(low[:-1])     # ni → нь, huwi → хувь: 'ь' үсэг латинд 'i' болдог
                if soft and soft[0].endswith("ь") and (best is None or soft[1] >= best[1] * 0.5):
                    return soft[0]
            return best[0] if best else None
        for key in weak_latin_keys(lat):
            cands = self.by_key.get(key)
            if cands:
                return max(cands.items(), key=lambda kv: kv[1])[0]
        return None

    def word(self, lat: str) -> str:
        if any(c in CYR for c in lat.lower()) or not lat.isalpha():
            return lat
        hit = self._lookup(lat)
        if hit:
            return hit
        # Дагавар хасаж үндсийг үгийн сангаас олоод, эгшиг зохицлоор кирилл дагавар залгана
        low = lat.lower()
        for suf in _LAT_SUFFIXES:
            if low.endswith(suf) and len(low) - len(suf) >= 3:
                stem = self._lookup(low[: -len(suf)])
                if stem:
                    return stem + cyr_suffix(stem, suf)
        guess = latin_to_cyrillic_rule(lat)
        rw = getattr(self, "rewriter", None)
        return rw.apply(guess) if rw is not None else guess

    def sentence(self, text: str) -> str:
        out = _WORD.sub(lambda m: self.word(m.group(0)), text)
        return re.sub(r"(\d)-r\b", r"\1-р", out) if self.improved else out


_SUFFIX_FORMS = {
    # латин дагавар: (ар эгшигт, өмнө эгшигт, о-эгшигт)
    "iin": ("ын", "ийн", "ын"), "ii": ("ы", "ий", "ы"), "iig": ("ыг", "ийг", "ыг"), "ig": ("ыг", "ийг", "ыг"),
    "nii": ("ны", "ний", "ны"), "ni": ("ны", "ний", "ны"),
    "aas": ("аас", "ээс", "оос"), "ees": ("аас", "ээс", "оос"), "oos": ("аас", "ээс", "оос"),
    "uus": ("аас", "өөс", "оос"),
    "aar": ("аар", "ээр", "оор"), "eer": ("аар", "ээр", "оор"), "oor": ("аар", "ээр", "оор"),
    "uur": ("аар", "өөр", "оор"),
    "tai": ("тай", "тэй", "той"), "tei": ("тай", "тэй", "той"), "toi": ("тай", "тэй", "той"),
    "ruu": ("руу", "рүү", "руу"), "rvv": ("руу", "рүү", "руу"), "luu": ("луу", "лүү", "луу"),
    "lvv": ("луу", "лүү", "луу"),
    "uud": ("ууд", "үүд", "ууд"), "vvd": ("ууд", "үүд", "ууд"), "nuud": ("нууд", "нүүд", "нууд"),
    "nvvd": ("нууд", "нүүд", "нууд"), "aa": ("аа", "ээ", "оо"), "ee": ("аа", "ээ", "оо"), "oo": ("аа", "ээ", "оо"),
    "d": ("д", "д", "д"), "t": ("т", "т", "т"), "n": ("н", "н", "н"), "g": ("г", "г", "г"),
    "iinh": ("ынх", "ийнх", "ынх"), "iinhee": ("ынхаа", "ийнхээ", "ынхоо"),
}


def cyr_suffix(stem: str, lat_suffix: str) -> str:
    """Эгшиг зохицлоор кирилл дагавар сонгоно (ар: а/у/я/ю, о-эгшиг: зөвхөн о, өмнө: бусад)."""
    forms = _SUFFIX_FORMS.get(lat_suffix)
    if not forms:
        return latin_to_cyrillic_rule(lat_suffix)
    vowels = [c for c in stem.lower() if c in "аэиоуөүяёюы"]
    if any(v in "ауяю" for v in vowels):
        return forms[0]
    if any(v == "о" for v in vowels):
        return forms[2]
    return forms[1]


@lru_cache(maxsize=1)
def default_transliterator() -> Transliterator:
    return Transliterator()
