"""Асуултын урьдчилсан шалгалт (production; config: pipeline.intent_check, RunOptions.intent_check).

Зааврын шаардлага: хариулах боломжгүй асуултаас татгалзаж, хоёрдмол асуултыг тодруулах. Туршилтын A–F run-д анхдагчаар
унтраалттай (загварын өөрийн зан төлөвийг хэмжинэ); `--set intent_check=true`-ээр TrustSQL-д үзүүлэх нөлөөг хэмжиж болно.

* refuse  — хувийн мэдээлэл (регистр, утас, хаяг ...) эсвэл өгөгдөл өөрчлөх (устга, шинэчил ...) хүсэлт;
* clarify — glossary-д олон утгатай (ambiguous) гэж тэмдэглэсэн нэр томьёог тодотголгүй хэрэглэсэн.
Кирилл ба латин (галиг) хоёуланг шалгана.

Дүрмийн хувилбар (RunOptions.intent_rules): "legacy" (анхдагч, H1–H4/E*/M1* run-уудынх) — үгийн язгуур; "v2" (2026-10-31
шалгалтын H1) — legacy-ийн хуурамч татгалзлыг засна: «Бүтэц өөрчилсөн зээл хэд байна?» (test L058, «өөрчил»), enum-ийн
«Имэйл сануулга» (и-мэйл), «customers registered in …» (англи \\bregist). v2: (1) мэдлэгийн сангийн шошгыг (glossary-ийн
нэр томьёо/синоним, semantic_model-ийн хүснэгт, багана, enum-ийн нэр; pii баганыхаас бусад) маскжуулна; (2) бичих үйл
үгийн ХҮСЭЛТИЙН хэлбэр (тушаах «… -ыг өөрчил», эелдэг «устгаарай», «-ж өг», «-на уу», өгүүлбэрийн төгсгөлийн «-х») —
нөхцөл/өнгөрсөн хэлбэр (өөрчилсөн, устгагдсан) биш; (3) англид зөвхөн тодорхой танигч (registration/phone number, home/
e-mail address, password, passport); «хаяг/hayag» нь хаягийг өөрийг нь асуусан хэлбэрээр (хаягийг, хаягууд, хаяг нь хаана)
эсвэл гэрийн/оршин суугаа/имэйл хаяг — «үндсэн хаягийн аймаг» (DB-д байгаа аймаг/дүүрэг) биш.
v2-ийн үйл үг, танигч нь legacy-ийнхээс шинэ ангилал нэмээгүй (eval set-ээр тааруулаагүй — зөвхөн хэлбэрийг нарийсгасан).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..text.normalize import cyr_key, weak_latin_key

INTENT_RULES = ("legacy", "v2")   # RunOptions.intent_rules

PII_PATTERNS = [r"регистр", r"\bрд\b", r"утас(ны|аа|ыг)?\b", r"утасны дугаар", r"гэрийн хаяг", r"оршин суугаа хаяг",
                r"и-?мэйл", r"нууц үг", r"паспорт", r"дансны дугаар", r"\bphone", r"\baddress", r"\bregist",
                r"\bpassword", r"\bpassport", r"\be-?mail"]
PII_LATIN = ["registr", "utas", "utasnii dugaar", "hayag", "khayag", "nuuts ug", "passport", "dansnii dugaar"]
WRITE_PATTERNS = [r"устга", r"шинэчил", r"өөрчил", r"засварла", r"нэмж бүртгэ", r"бүртгэж оруул", r"хаа(ж|х) өг",
                  r"\bdelete\b", r"\bupdate\b", r"\binsert\b", r"\bdrop\b", r"\btruncate\b"]
WRITE_LATIN = ["ustga", "shinechil", "uurchil", "oorchil", "zaswarla", "nemj burtge", "nemj bvrtge"]

# ------------------------------------------------------------------ v2 (intent_rules="v2")
# Кирилл/англи — маскжуулсан cyr_key текст дээр. Хаяг: хаягийг өөрийг нь асуусан хэлбэр (заах «хаягийг/хаягуудыг», «хаяг нь
# хаана/юу/ямар») — «үндсэн хаягийн аймаг», «үндсэн хаяг нь … аймагт» (DB-д байгаа аймаг/дүүрэг) биш.
PII_PATTERNS_V2 = [r"регистр", r"\bрд\b", r"утас(ны|аа|ыг)?\b", r"утасны дугаар", r"гэрийн хаяг", r"оршин суугаа хаяг",
                   r"хаяг(?:ийг|уудыг|ууд)\b", r"хаяг\s+(?:нь\s+)?(?:юу|хаана|ямар)\b",
                   r"и-?мэйл(?!ээр|ээс)", r"нууц үг", r"паспорт", r"дансны дугаар",
                   r"\bregist(?:er|ered|ry|ration)\s+(?:\([^)]*\)\s*)?(?:id\s+)?(?:no\b|num\b|numbers?\b)",
                   r"\bnational\s+id\b", r"\b(?:tele)?phone\s+(?:no\b|num\b|numbers?\b)",
                   r"\b(?:home|residential|street|mailing|postal)\s+address", r"\be-?mail\s+address",
                   r"\be-?mail-?(?:ийг|ыг|үүдийг|хаяг)", r"\bpassword", r"\bpassport"]
# Галиг (weak_latin_key-ээр, маскжуулсан текстэд): legacy-ийн түлхүүр; ерөнхий «hayag»-ийн оронд кириллийнхтэй ижил хэлбэр.
PII_LATIN_V2 = ["utas", "utasnii dugaar", "geriin hayag", "orshin suugaa hayag", "nuuts ug", "passport", "dansnii dugaar"]
# weak_latin_key-ийн орон зайд: «registr» нь англи registration/registrar/registry-г барихгүй; haiag = хаяг/hayag/khayag
PII_LATIN_V2_RE = [r"\bregistr(?!ation|ar|i\b)", r"\bhaiag(?:ig|odig|od)\b", r"\bhaiag\s+(?:ni?\s+)?(?:io|hana|iamar)\b"]
_W_STEM = r"(?:устга|шинэчил|өөрчил|засварла)"
_EN_WRITE = r"(?:delete|update|insert|drop|truncate)"
WRITE_PATTERNS_V2 = [
    rf"\b{_W_STEM}\b",                                                         # тушаах: «… -ыг өөрчил», «устга»
    r"\b(?:устгаа|засварлаа)(?:рай|ч)\b", r"\bшинэчлээ(?:рэй|ч)\b", r"\bөөрчлөө(?:рэй|ч)\b",   # эелдэг/дотно тушаал
    r"\b(?:устгая|засварлая|шинэчилье|өөрчилье)\b",
    r"\b(?:устгана|засварлана|шинэч(?:ил|лэ)нэ|өөрч(?:ил|лө)нө)\s+(?:уу|үү)\b",          # «-на уу» хүсэлт
    r"\b(?:устга(?:ж|ад)|засварла(?:ж|ад)|шинэчилж|шинэчлээд|өөрчилж|өөрчлөөд)\s+(?:өг|хая|орхи)",   # «-ж/-аад өг»
    r"\b(?:устгах|засварлах|шинэчлэх|өөрчлөх)\s*[.!?]?\s*$",                   # өгүүлбэрийн төгсгөлийн «-х» (тушаал)
    r"нэмж бүртгэ", r"бүртгэж оруул", r"хаа(ж|х) өг",                          # legacy-д аль хэдийн хүсэлтийн хэлбэр
    # англи: өгүүлбэр/хэсгийн эхний тушаал, please/can you/want to-ийн дараа (дараагийн үг кирилл бол холимог өгүүлбэр —
    # «Update хийсэн гэрээ» нь тушаал биш), холимог «delete хий(ж өг)», SQL-ийн бүтэц («deposits drop», «were deleted» биш)
    rf"(?:^|[.;!?]\s*|\b(?:please|kindly)\s+|\b(?:can|could|would|will)\s+you\s+(?:please\s+)?|"
    rf"\b(?:want|need|like)\s+(?:you\s+)?to\s+|\bhelp\s+(?:me\s+)?(?:to\s+)?){_EN_WRITE}\b(?!\s*-?\s*[а-яёөү])",
    rf"\b{_EN_WRITE}\s*-?\s*(?:хий\b|хийж\s+өг|хийгээд|хийгээрэй|хийнэ\s+үү)",
    r"\bdelete\s+from\b", r"\binsert\s+into\b", r"\bupdate\s+\S+\s+set\b", r"\b(?:drop|truncate)\s+table\b"]
_WL_STEM = r"(?:ostga|šinečil|orčil|zas[vo]arla)"
WRITE_LATIN_V2 = [
    rf"\b{_WL_STEM}\b",
    r"\b(?:ostga|zas[vo]arla)(?:rai|č|ia)\b", r"\bšinečle(?:rei|č)\b", r"\borč(?:l|il)o(?:rei|č)\b",
    r"\b(?:šinečil|orčil)e\b",
    r"\b(?:ostgana|zas[vo]arlana|šineč(?:il|le)ne|orč(?:il|lo)no)\s+o\b",
    r"\b(?:ostga[jd]|zas[vo]arla[jd]|šinečilj|šinečled|orčilj|orčlod)\s+(?:og|haia|orhi)",
    r"\b(?:ostgah|zas[vo]arlah|šinečleh|orčloh)\s*[.!?]?\s*$",
    r"nemj bortge", r"bortgej orol"]
# Маскжуулахгүй шошго: эдгээр хэллэгийн ДОТОРХ хэсэг бол (жишээ нь enum-ийн «Имэйл», «дугаар») маск нь хүсэлтийг устгана
_CUE_PHRASES = ["регистрийн дугаар", "утасны дугаар", "гэрийн хаяг", "оршин суугаа хаяг", "имэйл хаяг", "и-мэйл хаяг",
                "нууц үг", "паспорт", "дансны дугаар", "registration number", "register number", "national id",
                "telephone number", "phone number", "home address", "residential address", "email address",
                "e-mail address", "password", "passport", "устгах", "шинэчлэх", "өөрчлөх", "засварлах",
                "нэмж бүртгэх", "бүртгэж оруулах", "хааж өгөх"]
_MASK = " ¤ "


@dataclass
class IntentDecision:
    action: str                 # answer | refuse | clarify
    reason: str = ""
    message_mn: str = ""
    term_id: str | None = None


def _has(patterns, text) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


@dataclass
class Masker:
    """v2: мэдлэгийн сангийн шошгыг (урт нь эхэлж) « ¤ »-ээр солино — cyr_key ба weak_latin_key хоёр орон зайд."""
    cyr: re.Pattern | None = None
    lat: re.Pattern | None = None
    n_labels: int = 0

    def mask(self, q: str) -> str:
        return self.cyr.sub(_MASK, q) if self.cyr is not None else q

    def mask_latin(self, wl: str) -> str:
        return self.lat.sub(_MASK, wl) if self.lat is not None else wl


def domain_labels(glossary_terms: list, model=None) -> list[str]:
    """glossary-ийн нэр томьёо, синоним, ambiguous trigger + semantic_model-ийн хүснэгт/баганын mn, en, syn ба enum-ийн
    нэр. pii баганын шошго орохгүй (хувийн мэдээллийн хүсэлтийг маскжуулахгүй)."""
    out: list[str] = []
    for t in glossary_terms or []:
        out += [t.term, *t.aliases]
        out += [str(x) for x in ((getattr(t, "ambiguous", None) or {}).get("triggers") or [])]
    for tab in (getattr(model, "tables", None) or {}).values():
        out += [tab.mn, tab.en, *tab.syn]
        for c in tab.columns.values():
            if not c.pii:
                out += [c.mn, c.en, *c.syn, *c.values.values()]
    return [str(x) for x in out if x]


def _alternation(keys: set[str]) -> re.Pattern | None:
    # Урт нь эхэлж — давхцсан шошгоос хамгийн урт нь маскжина; зөвхөн үгийн эхнээс (дагавар залгаж болно)
    return re.compile(r"(?<!\w)(?:" + "|".join(re.escape(k) for k in sorted(keys, key=lambda k: (-len(k), k))) + ")") \
        if keys else None


def build_masker(glossary_terms: list, model=None) -> Masker:
    cue_cyr = [cyr_key(p) for p in _CUE_PHRASES]
    cue_lat = [weak_latin_key(p) for p in _CUE_PHRASES]
    cyr, lat = set(), set()
    for lab in domain_labels(glossary_terms, model):
        k, w = cyr_key(lab), weak_latin_key(lab)
        # хэт богино эсвэл хүсэлтийн хэллэгийн хэсэг («Имэйл» ⊂ «имэйл хаяг») бол маскжуулахгүй
        if len(k) >= 4 and not any(k in c for c in cue_cyr):
            cyr.add(k)
        if len(w) >= 4 and not any(w in c for c in cue_lat):
            lat.add(w)
    return Masker(_alternation(cyr), _alternation(lat), len(cyr))


def _refusal_v2(question: str, masker: Masker) -> IntentDecision | None:
    q = masker.mask(cyr_key(question))
    wl = masker.mask_latin(weak_latin_key(question))
    if _has(PII_PATTERNS_V2, q) or any(weak_latin_key(k) in wl for k in PII_LATIN_V2) or _has(PII_LATIN_V2_RE, wl):
        return IntentDecision("refuse", "pii", "Хувийн мэдээлэл (регистр, утас, хаяг г.м.) өгөх боломжгүй — аюулгүй "
                                               "байдлын бодлогын дагуу эдгээр талбар системд харагддаггүй.")
    if _has(WRITE_PATTERNS_V2, q) or _has(WRITE_LATIN_V2, wl):
        return IntentDecision("refuse", "write_operation", "Систем зөвхөн мэдээлэл унших (SELECT) эрхтэй — өгөгдөл "
                                                           "өөрчлөх, устгах хүсэлтийг гүйцэтгэхгүй.")
    return None


def check_question(question: str, glossary_terms: list, rules: str = "legacy",
                   masker: Masker | None = None) -> IntentDecision:
    """rules="legacy" (анхдагч) — хуучин дүрэм яг хэвээр; "v2" — маск + хүсэлтийн хэлбэр (masker өгөөгүй бол зөвхөн
    glossary-ээс угсарна; Pipeline semantic_model-тэй нь нэг удаа угсарч дамжуулна). Тодруулгын алхам хоёуланд ижил."""
    if rules not in INTENT_RULES:
        raise ValueError(f"intent_rules: үл мэдэгдэх утга {rules!r} (боломжит: {INTENT_RULES})")
    q = cyr_key(question)
    wl = weak_latin_key(question)
    if rules == "v2":
        refused = _refusal_v2(question, masker or build_masker(glossary_terms))
        if refused is not None:
            return refused
        return _clarify(q, wl, glossary_terms)
    if _has(PII_PATTERNS, q) or any(weak_latin_key(k) in wl for k in PII_LATIN):
        return IntentDecision("refuse", "pii", "Хувийн мэдээлэл (регистр, утас, хаяг г.м.) өгөх боломжгүй — аюулгүй "
                                               "байдлын бодлогын дагуу эдгээр талбар системд харагддаггүй.")
    if _has(WRITE_PATTERNS, q) or any(weak_latin_key(k) in wl for k in WRITE_LATIN):
        return IntentDecision("refuse", "write_operation", "Систем зөвхөн мэдээлэл унших (SELECT) эрхтэй — өгөгдөл "
                                                           "өөрчлөх, устгах хүсэлтийг гүйцэтгэхгүй.")
    return _clarify(q, wl, glossary_terms)


def _clarify(q: str, wl: str, glossary_terms: list) -> IntentDecision:
    for t in glossary_terms:
        amb = getattr(t, "ambiguous", None)
        if not amb or not amb.get("alternatives"):
            continue
        names = [str(n) for n in (amb.get("triggers") or ([t.term] + list(t.aliases)))]
        hit = any(cyr_key(n) and cyr_key(n) in q for n in names if len(n) > 3) or \
            any(weak_latin_key(n) and len(n) > 3 and weak_latin_key(n) in wl for n in names)
        if not hit:
            continue
        qualifiers = [str(x) for x in (amb.get("qualifiers") or [])]
        if any(cyr_key(x) in q or (len(x) > 3 and weak_latin_key(x) in wl) for x in qualifiers):
            continue
        opts = "; ".join(f"({i + 1}) {a['label_mn']}" for i, a in enumerate(amb["alternatives"]))
        return IntentDecision("clarify", "ambiguous", f"Тодруулна уу: «{t.term}» гэдгийг аль утгаар нь ойлгох вэ? {opts}",
                              term_id=t.id)
    return IntentDecision("answer")
