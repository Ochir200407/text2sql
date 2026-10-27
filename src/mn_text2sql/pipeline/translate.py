"""A4: асуултыг англи руу орчуулах, DB-ийн утгыг placeholder-оор хамгаалах (translate-test).

Урсгал: асуултаас утга агуулсан хэсгийг (нэр, код, тоо, огноо, ишлэл, DB-ийн утгатай таарсан үг) олж [V1], [V2] …
болгоно → орчуулагч LLM (Apache лицензтэй, жишээ нь Qwen3.5-9B; NLLB-200 CC-BY-NC тул хэрэглэхгүй) англи руу
орчуулна → placeholder-ыг анхны (монгол) хэлбэрээр нь буцааж тавина. Ингэснээр SQL загвар утгыг яг асуултад
бичигдсэнээр нь харж, орчуулгад гажихгүй.

Кэш: build/translations/<орчуулагч>.jsonl ({id, variant, source, masked, spans, raw, text, ok}).
"""

from __future__ import annotations

import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..config import Config, load_models, resolve_path
from ..llm.client import make_llm
from ..text.normalize import script_of

log = logging.getLogger(__name__)

TRANSLATE_SYSTEM = (
    "You translate questions about a Mongolian non-bank financial institution's database into natural English. "
    "Input may be Mongolian Cyrillic, Mongolian written in Latin letters (informal transliteration), or mixed with "
    "English words. Keep every placeholder like [V1] exactly as written (they stand for database values). "
    "Translate banking terms precisely (зээл = loan, хадгаламж = deposit, хугацаа хэтэрсэн = overdue, "
    "үлдэгдэл = outstanding balance, салбар = branch, харилцагч = customer, олголт = disbursement, "
    "эргэн төлөлт = repayment, барьцаа = collateral, торгууль = penalty, хүү = interest). "
    "Output only the English question, nothing else.")
TRANSLATE_FEWSHOT = [
    ("[V1] салбарын хугацаа хэтэрсэн зээлийн үлдэгдэл хэд вэ?",
     "What is the outstanding balance of overdue loans at the [V1] branch?"),
    ("2025 onii [V1] sard heden shine zeel olgoson be", "How many new loans were disbursed in [V1] 2025?"),
    ("[V1] бүтээгдэхүүний active loan-уудын average rate хэд вэ?",
     "What is the average interest rate of active loans of the [V1] product?"),
]

# Оноосон нэр агуулдаг баганууд — эдгээрийн утгатай таарсан хэсгийг л хамгаална (төлөв, төрлийн нэрийг орчуулна)
ENTITY_COLUMNS = {"branch_name", "district", "province", "employer_name", "first_name", "last_name", "full_name",
                  "agency_name", "company_name"}

_PATTERNS = [
    re.compile(r"\b[A-Z]{2,4}-\d{4}-\d{3,}\b"),                     # бизнес түлхүүр LN-2025-000123
    re.compile(r"\b[A-Z]{2}\d{2}\b"),                                # салбарын код UB01
    re.compile(r"\d{4}-\d{2}-\d{2}"),                                # огноо
    re.compile(r"[«\"“']([^»\"”']{2,60})[»\"”']"),                   # ишлэл
]


def find_value_spans(question: str, retriever=None, min_score: float = 0.9) -> list[str]:
    """Хамгаалах ёстой хэсгүүд (давхардалгүй, урт нь эхэндээ)."""
    spans: list[str] = []
    for pat in _PATTERNS:
        for m in pat.finditer(question):
            spans.append(m.group(1) if m.groups() else m.group(0))
    # Кирилл томоор эхэлсэн үг (өгүүлбэрийн эхнийхээс бусад) — оноосон нэр
    words = re.findall(r"[A-Za-zА-Яа-яЁёӨөҮү][\w\-]*", question)
    for i, w in enumerate(words):
        if i > 0 and w[:1].isupper() and script_of(w) == "cyrillic" and len(w) > 2:
            spans.append(w)
    if retriever is not None:
        try:
            # basic горимд түлхүүр нь асуултын өөрийн n-gram тул асуултад яг байна. DB-ийн утгыг ОРУУЛАХГҮЙ —
            # зөвхөн асуултын гадаргын хэлбэрийг хамгаална (A4-д RAG байхгүй).
            low = question.lower()
            for h in retriever.values.search(question, per_keyword=3, top_total=8, mode="basic"):
                if h.score < min_score or not h.keyword or h.column not in ENTITY_COLUMNS:
                    continue
                i = low.find(h.keyword.lower())
                if i < 0:
                    continue
                j = i + len(h.keyword)
                while j < len(question) and (question[j].isalnum() or question[j] in "-'"):
                    j += 1   # дагавар хүртэл бүтэн үгээр
                spans.append(question[i:j])
        except Exception as e:  # утгын индексгүй орчинд зөвхөн regex
            log.debug("утгын хайлт алгасав: %s", e)
    out = []
    for s in sorted(set(x.strip() for x in spans if x and x.strip()), key=len, reverse=True):
        if not any(s in o for o in out):
            out.append(s)
    return out


def filter_spans(spans: list[str], question: str, keys: dict[str, str]) -> tuple[list[str], dict[str, str]]:
    """T2: зөвхөн regex/ишлэл эсвэл DB-ийн оноосон нэртэй (дагаврыг хассан) яг таарсан span-ыг хамгаална —
    «байсан» → «Баасан», өгүүлбэрийн эхний том үсэгтэй үг г.м. хуурамч маскыг өмнө нь хийхгүй. Буцаах:
    (хүчинтэй span-ууд, {span: DB-д хадгалагдсан хэлбэр})."""
    from .translate_fix import span_value
    keep, canon = [], {}
    for sp in spans:
        v = span_value(sp, question, keys)
        if v is None:
            continue
        keep.append(sp)
        if isinstance(v, str):
            canon[sp] = v
    return keep, canon


def prepare(question: str, retriever=None, keys: dict[str, str] | None = None,
            hints: bool = False) -> tuple[str, dict[str, str], list[str]]:
    """(masked, {[Vn]: утга}, нэр томьёоны мөрүүд). keys/hints өгөөгүй бол A4-ийн анхны зан төлөв."""
    spans = find_value_spans(question, retriever)
    canon: dict[str, str] = {}
    if keys is not None:
        spans, canon = filter_spans(spans, question, keys)
    masked, mapping = mask(question, spans)
    mapping = {k: canon.get(v, v) for k, v in mapping.items()}
    th: list[str] = []
    if hints:
        from .translate_terms import term_hints
        th = term_hints(question)
    return masked, mapping, th


def mask(question: str, spans: list[str]) -> tuple[str, dict[str, str]]:
    masked, mapping = question, {}
    for i, s in enumerate(spans, 1):
        key = f"[V{i}]"
        if s in masked:
            masked = masked.replace(s, key)
            mapping[key] = s
    return masked, mapping


def unmask(text: str, mapping: dict[str, str]) -> tuple[str, bool]:
    ok = True
    for key, val in mapping.items():
        if key in text:
            text = text.replace(key, f'"{val}"')
        else:
            ok = False
            text = text.rstrip() + f' ("{val}")'
    return text.strip(), ok


def translate_messages(masked: str, hints: list[str] | None = None) -> list[dict]:
    system = TRANSLATE_SYSTEM
    if hints:   # T2: мэдлэгийн сангаас олсон нэр томьёо (translate_terms.term_hints)
        system += ("\nDomain terms that appear in this question (Mongolian = English; use these English terms, "
                   "and keep any quoted database value exactly as quoted). They are vocabulary only: keep which noun "
                   "each word describes exactly as in the question, and do not copy the notes in parentheses into "
                   "the translation:\n" + "\n".join(f"- {h}" for h in hints))
    msgs = [{"role": "system", "content": system}]
    for src, tgt in TRANSLATE_FEWSHOT:
        msgs += [{"role": "user", "content": src}, {"role": "assistant", "content": tgt}]
    msgs.append({"role": "user", "content": masked})
    return msgs


# ---------------------------------------------------------------------------------------------------------------
# v2 = fine-tune хийсэн орчуулагчийн (M1) формат — scripts/mt/render_sft.py-ийн сургалтын prompt-той ЯГ ижил (нэг эх
# сурвалж; docs/MT_INVARIANT_PLAN.md §5.4): чиглэлийн tag, нэр томьёоны hint, DB-ийн утга «…» (каноник) — загвар үгчлэн хуулна.
SFT_SYSTEM = ("Translate a question about a Mongolian non-bank financial institution's database. Direction: {direction}. "
              "Copy text inside «…» exactly. Output only the translation.")
SFT_HINT_HEAD = "\nTerms (Mongolian = English; vocabulary only):\n"
_MARK = re.compile(r"«([^«»]+)»")


def sft_direction(question: str) -> str:
    s = script_of(question)
    return {"latin": "mn(latin)→en", "mixed": "mn(mixed)→en"}.get(s, "mn(cyrillic)→en")


def translate_messages_v2(text: str, hints: list[str] | None = None, direction: str = "mn(cyrillic)→en") -> list[dict]:
    system = SFT_SYSTEM.format(direction=direction) + (SFT_HINT_HEAD + "\n".join(f"- {h}" for h in hints) if hints else "")
    return [{"role": "system", "content": system}, {"role": "user", "content": text}]


def prepare_v2(question: str, retriever=None, keys: dict[str, str] | None = None
               ) -> tuple[str, dict[str, str], list[str], str]:
    """(«»-тэй асуулт, {«утга»: утга}, hint, чиглэл). Утгын илрүүлэлт prepare()-тэй ижил (T2: keys-ээр шүүнэ); hint-ийг кирилл
    хэлбэрээс (латин бол retriever.to_cyrillic) — сургалтын render_sft-тэй ижил."""
    from .translate_terms import term_hints
    spans = find_value_spans(question, retriever)
    canon: dict[str, str] = {}
    if keys is not None:
        spans, canon = filter_spans(spans, question, keys)
    marked, mapping = question, {}
    for s in spans:
        v = canon.get(s, s)
        if s in marked:
            marked = marked.replace(s, f"«{v}»")
            mapping[f"«{v}»"] = v
    cyr = retriever.to_cyrillic(question) if retriever is not None and hasattr(retriever, "to_cyrillic") else question
    return marked, mapping, term_hints(cyr), sft_direction(question)


def unmark(text: str, mapping: dict[str, str]) -> tuple[str, bool]:
    """«утга» → "утга"; загвар хуулаагүй утгыг төгсгөлд нэмнэ (unmask-тай ижил дүрэм)."""
    ok = all(k in text for k in mapping)
    for k, v in mapping.items():
        if k not in text:
            text = text.rstrip() + f' ("{v}")'
    return _MARK.sub(lambda m: f'"{m.group(1)}"', text).strip(), ok


def translate_v2(question: str, llm, retriever=None, keys: dict[str, str] | None = None) -> dict:
    marked, mapping, th, direction = prepare_v2(question, retriever, keys)
    resp = llm.chat(translate_messages_v2(marked, th, direction), temperature=0.0, max_tokens=256)
    text, ok = unmark(clean_output(resp.texts[0]), mapping)
    return {"text": text, "masked": marked, "spans": mapping, "ok": ok, "hints": th}


def clean_output(text: str) -> str:
    text = text.split("</think>")[-1].strip()
    text = re.sub(r"^(English|Translation)\s*:\s*", "", text, flags=re.I)
    return text.strip().strip('"').splitlines()[0].strip() if text.strip() else ""


def translate_items(cfg: Config, items, variants: list[str], model_name: str | None = None,
                    base_url: str | None = None, workers: int = 8, use_values: bool = True,
                    t2: bool = False, fmt: str | None = None) -> dict:
    """t2=True: маскыг DB-ийн оноосон нэрээр шүүх + нэр томьёоны RAG → кэш <орчуулагч>.t2.jsonl.
    fmt="sft" (анхдагч: models.yaml translator_prompt): fine-tune хийсэн орчуулагч (translate_v2, T2-ийн шүүлттэй) →
    кэш <орчуулагч>.sft.jsonl."""
    registry = load_models()
    model_name = model_name or registry.get("translator")
    mspec = registry["models"].get(model_name, {})
    llm_cfg = dict(cfg["llm"], model=mspec.get("served_name", model_name))
    if base_url:
        llm_cfg["base_url"] = base_url
    if mspec.get("extra_body"):
        llm_cfg["extra_body"] = mspec["extra_body"]
    llm = make_llm(llm_cfg)
    retriever = None
    if use_values:
        try:
            from ..retrieval.retriever import get_retriever
            retriever = get_retriever(cfg)
        except FileNotFoundError:
            log.warning("индекс алга — зөвхөн regex-ээр хамгаална")
    keys = None
    if t2:
        from ..db.conn import bot_dsn
        from ..db.executor import Executor
        from .translate_fix import entity_keys
        keys = entity_keys(Executor(bot_dsn(cfg), statement_timeout_ms=60000), ENTITY_COLUMNS)
    fmt = fmt or registry.get("translator_prompt", "v1")
    if fmt == "sft" and keys is None:
        from ..db.conn import bot_dsn
        from ..db.executor import Executor
        from .translate_fix import entity_keys
        keys = entity_keys(Executor(bot_dsn(cfg), statement_timeout_ms=60000), ENTITY_COLUMNS)
    suffix = ".sft" if fmt == "sft" else (".t2" if t2 else "")
    path = resolve_path("build/translations") / f"{model_name}{suffix}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                done.add((d["id"], d["variant"]))
    lock = threading.Lock()
    jobs = [(it, v) for it in items for v in variants if it.question(v) and (it.id, v) not in done]
    stats = {"translated": 0, "placeholder_lost": 0, "skipped_done": len(done)}

    def work(job):
        it, v = job
        q = it.question(v)
        if fmt == "sft":
            masked, mapping, hints, direction = prepare_v2(q, retriever, keys)
            resp = llm.chat(translate_messages_v2(masked, hints, direction), temperature=0.0, max_tokens=256)
            raw = resp.texts[0]
            text, ok = unmark(clean_output(raw), mapping)
        else:
            masked, mapping, hints = prepare(q, retriever, keys, hints=t2)
            resp = llm.chat(translate_messages(masked, hints), temperature=0.0, max_tokens=256)
            raw = resp.texts[0]
            text, ok = unmask(clean_output(raw), mapping)
        rec = {"id": it.id, "variant": v, "source": q, "masked": masked, "spans": mapping, "raw": raw, "text": text,
               "ok": ok, "hints": hints}
        with lock, open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            stats["translated"] += 1
            stats["placeholder_lost"] += 0 if ok else 1

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(work, jobs))
    stats["file"] = str(path)
    return stats


def load_translations(path: Path) -> dict[str, str]:
    out = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                out[f"{d['id']}:{d['variant']}"] = d["text"]
    return out


def translate_one(question: str, llm, retriever=None, keys: dict[str, str] | None = None,
                  hints: bool = False, fmt: str = "v1") -> dict:
    """Шинэ (кэшгүй) асуултыг шууд орчуулна (вэб үйлчилгээ): утгыг [V] болгож хамгаалаад орчуулагч LLM → буцааж
    тавина. keys/hints = T2 (translate_items(t2=True)-тай ижил). fmt="sft" = fine-tune хийсэн орчуулагч (M1;
    translate_v2). Буцаах: {text, masked, spans, ok, hints}."""
    if fmt == "sft":
        return translate_v2(question, llm, retriever, keys)
    masked, mapping, th = prepare(question, retriever, keys, hints)
    resp = llm.chat(translate_messages(masked, th), temperature=0.0, max_tokens=256)
    text, ok = unmask(clean_output(resp.texts[0]), mapping)
    return {"text": text, "masked": masked, "spans": mapping, "ok": ok, "hints": th}
