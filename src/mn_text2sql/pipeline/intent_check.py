"""Асуултын урьдчилсан шалгалт (production; config: pipeline.intent_check, RunOptions.intent_check).

Зааврын шаардлага: хариулах боломжгүй асуултаас татгалзаж, хоёрдмол асуултыг тодруулах. Туршилтын A–F run-д анхдагчаар
унтраалттай (загварын өөрийн зан төлөвийг хэмжинэ); `--set intent_check=true`-ээр TrustSQL-д үзүүлэх нөлөөг хэмжиж болно.

* refuse  — хувийн мэдээлэл (регистр, утас, хаяг ...) эсвэл өгөгдөл өөрчлөх (устга, шинэчил ...) хүсэлт;
* clarify — glossary-д олон утгатай (ambiguous) гэж тэмдэглэсэн нэр томьёог тодотголгүй хэрэглэсэн.
Кирилл ба латин (галиг) хоёуланг шалгана.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..text.normalize import cyr_key, weak_latin_key

PII_PATTERNS = [r"регистр", r"\bрд\b", r"утас(ны|аа|ыг)?\b", r"утасны дугаар", r"гэрийн хаяг", r"оршин суугаа хаяг",
                r"и-?мэйл", r"нууц үг", r"паспорт", r"дансны дугаар", r"\bphone", r"\baddress", r"\bregist",
                r"\bpassword", r"\bpassport", r"\be-?mail"]
PII_LATIN = ["registr", "utas", "utasnii dugaar", "hayag", "khayag", "nuuts ug", "passport", "dansnii dugaar"]
WRITE_PATTERNS = [r"устга", r"шинэчил", r"өөрчил", r"засварла", r"нэмж бүртгэ", r"бүртгэж оруул", r"хаа(ж|х) өг",
                  r"\bdelete\b", r"\bupdate\b", r"\binsert\b", r"\bdrop\b", r"\btruncate\b"]
WRITE_LATIN = ["ustga", "shinechil", "uurchil", "oorchil", "zaswarla", "nemj burtge", "nemj bvrtge"]


@dataclass
class IntentDecision:
    action: str                 # answer | refuse | clarify
    reason: str = ""
    message_mn: str = ""
    term_id: str | None = None


def _has(patterns, text) -> bool:
    return any(re.search(p, text, re.I) for p in patterns)


def check_question(question: str, glossary_terms: list) -> IntentDecision:
    q = cyr_key(question)
    wl = weak_latin_key(question)
    if _has(PII_PATTERNS, q) or any(weak_latin_key(k) in wl for k in PII_LATIN):
        return IntentDecision("refuse", "pii", "Хувийн мэдээлэл (регистр, утас, хаяг г.м.) өгөх боломжгүй — аюулгүй "
                                               "байдлын бодлогын дагуу эдгээр талбар системд харагддаггүй.")
    if _has(WRITE_PATTERNS, q) or any(weak_latin_key(k) in wl for k in WRITE_LATIN):
        return IntentDecision("refuse", "write_operation", "Систем зөвхөн мэдээлэл унших (SELECT) эрхтэй — өгөгдөл "
                                                           "өөрчлөх, устгах хүсэлтийг гүйцэтгэхгүй.")
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
