"""F1: BIRD-ийн монгол хувилбар (туршилтын төлөвлөгөө, 1-р санаа: BIRDTurk-ийн арга).

1. bird-prepare  — BIRD dev (CC BY-SA 4.0) татаж задлах, хүндрэлээр давхаргалсан дэд олонлог (анхдагч 500) сонгох.
2. bird-translate — асуулт ба evidence-ийг орчуулагч LLM-ээр (Apache лицензтэй) англиас монгол кирилл руу орчуулна;
   gold SQL-ийн текст утга, `backtick` нэр, тоо зэргийг placeholder-оор хамгаална. Латин хувилбарыг монгол
   хэрэглэгчийн галигийн хоёр хэв маягаар (v/u/w ба u/o/v) тогтмол seed-ээр үүсгэнэ.
3. bird-review-export / bird-review-import — хүний засвар (BIRDTurk: машин орчуулга + хүний шалгалт); зөвхөн
   шалгагдсан (reviewed) мөрийг нийтлэх/үнэлэхийг зөвлөнө. Үүсмэл dataset-ийг CC BY-SA 4.0-аар нийтэлнэ.
"""

from __future__ import annotations

import csv
import json
import logging
import random
import re
import threading
import zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from ..config import Config, load_models, resolve_path
from ..text.normalize import STYLE_UOV, STYLE_VUW, translit

log = logging.getLogger(__name__)

BIRD_DEV_URL = "https://bird-bench.oss-cn-beijing.aliyuncs.com/dev.zip"
DIFF_MAP = {"simple": "easy", "moderate": "medium", "challenging": "hard"}


def bird_dir(cfg: Config) -> Path:
    return resolve_path((cfg.get("bird") or {}).get("data_dir", "data/bird"))


def prepare(cfg: Config, subset: int | None = None, seed: int | None = None, url: str | None = None) -> dict:
    """BIRD dev-ийг татаж (байхгүй бол), dev_databases-ийг задалж, давхаргалсан дэд олонлог сонгоно."""
    import httpx
    bc = cfg.get("bird") or {}
    subset = int(subset or bc.get("subset", 500))
    seed = int(seed if seed is not None else bc.get("seed", 42))
    root = bird_dir(cfg)
    root.mkdir(parents=True, exist_ok=True)
    zpath = root / "dev.zip"
    if not (root / "dev" / "dev.json").exists():
        if not zpath.exists():
            log.info("BIRD dev татаж байна: %s", url or BIRD_DEV_URL)
            with httpx.stream("GET", url or BIRD_DEV_URL, follow_redirects=True, timeout=None) as r:
                r.raise_for_status()
                with open(zpath, "wb") as fh:
                    for chunk in r.iter_bytes(1 << 20):
                        fh.write(chunk)
        with zipfile.ZipFile(zpath) as z:
            z.extractall(root)
        # архивын бүтэц: dev_YYYYMMDD/ эсвэл dev/
        cand = [p for p in root.iterdir() if p.is_dir() and p.name.startswith("dev") and (p / "dev.json").exists()]
        if cand and cand[0].name != "dev":
            cand[0].rename(root / "dev")
    dev = root / "dev"
    dbzip = dev / "dev_databases.zip"
    if dbzip.exists() and not (dev / "dev_databases").exists():
        with zipfile.ZipFile(dbzip) as z:
            z.extractall(dev)
    data = json.loads((dev / "dev.json").read_text(encoding="utf-8"))
    rng = random.Random(seed)
    by_diff = defaultdict(list)
    for i, d in enumerate(data):
        d.setdefault("question_id", i)
        by_diff[d.get("difficulty", "simple")].append(d)
    chosen = []
    for diff, rows in by_diff.items():
        k = round(subset * len(rows) / len(data))
        chosen += rng.sample(rows, min(k, len(rows)))
    chosen = sorted(chosen, key=lambda d: d["question_id"])[:subset]
    out = root / "subset.jsonl"
    with open(out, "w", encoding="utf-8") as fh:
        for d in chosen:
            fh.write(json.dumps({"question_id": d["question_id"], "db_id": d["db_id"], "question_en": d["question"],
                                 "evidence_en": d.get("evidence", ""), "SQL": d["SQL"],
                                 "difficulty": d.get("difficulty", "simple")}, ensure_ascii=False) + "\n")
    return {"dir": str(root), "dev_total": len(data), "subset": len(chosen), "file": str(out),
            "by_difficulty": {k: sum(1 for d in chosen if d.get("difficulty") == k) for k in by_diff}}


def db_path(cfg: Config, db_id: str) -> Path:
    return bird_dir(cfg) / "dev" / "dev_databases" / db_id / f"{db_id}.sqlite"


# ---------------------------------------------------------------------------
# Орчуулга (англи → монгол), утга хамгаалах
# ---------------------------------------------------------------------------

MN_SYSTEM = (
    "Translate the English question (or hint) about a database into natural Mongolian Cyrillic, the way a Mongolian "
    "data analyst would write it. Keep every placeholder like [V1] exactly as written. Keep numbers unchanged. "
    "Output only the Mongolian text.")
MN_FEWSHOT = [("How many schools in [V1] have more than 500 students?",
               "[V1]-д 500-аас олон сурагчтай хэдэн сургууль байдаг вэ?"),
              ("List the names of clients whose account was opened in [V1].",
               "[V1]-д данс нээлгэсэн харилцагчдын нэрийг жагсаа.")]


# H3 (2026-10-31): өмнөх _protect нь литерал бүрийг энгийн substring-ээр (урт нь эхэлж, set-ийн эрэмбээр) дараалан
# сольдог байв — богино литерал өөр үгийн дотор ('F' → "Find" → "[V2]ind") болон өмнөх placeholder-ийн дотор ('1' →
# "[V1]" → "[V[V2]]" → сэргээсэн асуултад "[V1]" үлдэнэ) солигдож, дугаар нь PYTHONHASHSEED-ээс хамаардаг байв.
# Одоо: зөвхөн бүтэн токен (үгийн хил), НЭГ удаагийн regex солилт (placeholder дахин маскжихгүй), литералын эрэмбэ
# (-урт, литерал), дугаар нь текст дэх анхны байрлалаар; богино литерал (_is_short) зөвхөн бие даасан токен бол.
_WORD = re.compile(r"\w")
# орчуулагчийн гаралт дахь placeholder: [V1], кирилл В-тэй ([В1]), жижиг үсэг, хаалтын доторх зай — бүгдийг таньна
_PLACEHOLDER = re.compile(r"\[\s*[VvВв]\s*(\d+)\s*\]")
# богино литералын бие даасан токен: өмнө нь мөрийн эхлэл / зай / хашилт / нээх хаалт / =, хойно нь мөрийн төгсгөл /
# зай / хашилт / хаах хаалт / цэг таслал; "F-score", "1.5", "1,000", "F's", "don't"-ын хэсэг биш
_SHORT_PRE = r"(?<![^\s'\"`(\[{=])(?<!\w')"
_SHORT_POST = r"(?![^\s'\"`)\]},;:?!.])(?![.,]\w)(?!'\w)"


def _is_short(s: str) -> bool:
    """Өөр үгийн дотор андуурагдах эрсдэлтэй литерал: 1 тэмдэгт, эсвэл 1–2 оронтой тоо."""
    return len(s) < 2 or (s.isdigit() and len(s) < 3)


def _literal_re(s: str) -> str:
    """Литералын бүтэн токен тааралт (regex). Үсэг/тоогоор эхэлсэн/төгссөн талд үгийн хил (мөн аравтын бутархайн
    хэсэг биш: "12.5"-ийн "12" үгүй); `backtick` ба хашилт/хаалтаар эхэлсэн тал нь өөрөө хил. Богино литерал —
    зөвхөн бие даасан токен (_SHORT_PRE/_SHORT_POST)."""
    body = re.escape(s)
    if _is_short(s):
        return _SHORT_PRE + body + _SHORT_POST
    pre = r"(?<!\w)(?<!\d[.,])" if _WORD.match(s[0]) else ""
    post = r"(?!\w)(?![.,]\d)" if _WORD.match(s[-1]) else ""
    return pre + body + post


def _protect(text: str, sql: str) -> tuple[str, dict[str, str]]:
    """Орчуулахаас өмнө хамгаалах хэсгийг [V1], [V2] ... болгоно: текстийн `backtick` нэр ба "давхар хашилт"-ын
    агуулга, gold SQL-ийн 'текст литерал' ('' escape-ийг тайлна). Буцаах: (маскжсан текст, {placeholder: эх хэсэг})."""
    spans = set(re.findall(r"`[^`]+`", text))
    spans |= {s.replace("''", "'") for s in re.findall(r"'((?:[^']|''){1,80})'", sql or "")}
    spans |= set(re.findall(r'"([^"]{1,80})"', text))
    order = sorted((s for s in spans if s.strip() and s in text), key=lambda s: (-len(s), s))
    if not order:
        return text, {}
    rx = re.compile("|".join(f"(?:{_literal_re(s)})" for s in order))
    keys: dict[str, str] = {}

    def sub(m: re.Match) -> str:
        lit = m.group(0)
        if lit not in keys:
            keys[lit] = f"[V{len(keys) + 1}]"
        return keys[lit]

    masked = rx.sub(sub, text)
    return masked, {k: lit for lit, k in keys.items()}


def _restore(text: str, mapping: dict[str, str]) -> tuple[str, bool]:
    """Placeholder-ийг нэг удаагийн солилтоор буцаана. ok = mapping-ийн бүх placeholder гаралтад байсан БА танигдаагүй
    placeholder үлдээгүй (жишээ нь орчуулагч зохиосон [V9])."""
    found: set[str] = set()

    def sub(m: re.Match) -> str:
        key = f"[V{int(m.group(1))}]"
        if key in mapping:
            found.add(key)
            return mapping[key]
        return m.group(0)

    out = _PLACEHOLDER.sub(sub, text)
    ok = found == set(mapping) and not _PLACEHOLDER.search(out)
    return out.strip(), ok


def latin_variant(text_mn: str, rng: random.Random) -> str:
    """Монгол хэрэглэгчийн галиг: хоёр хэв маягийн аль нэг, заримдаа жижиг үсэг/цэг таслалгүй."""
    style = STYLE_VUW if rng.random() < 0.6 else STYLE_UOV
    out = translit(text_mn, style)
    if rng.random() < 0.3:
        out = out.lower().rstrip("?.")
    return out


def translate(cfg: Config, model_name: str | None = None, base_url: str | None = None, workers: int = 8) -> dict:
    from ..llm.client import make_llm
    registry = load_models()
    model_name = model_name or registry.get("translator")
    mspec = registry["models"].get(model_name, {})
    llm_cfg = dict(cfg["llm"], model=mspec.get("served_name", model_name))
    if base_url:
        llm_cfg["base_url"] = base_url
    if mspec.get("extra_body"):
        llm_cfg["extra_body"] = mspec["extra_body"]
    llm = make_llm(llm_cfg)
    root = bird_dir(cfg)
    rows = [json.loads(x) for x in (root / "subset.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    out_path = root / "bird_mn.jsonl"
    done = {}
    if out_path.exists():
        for x in out_path.read_text(encoding="utf-8").splitlines():
            if x.strip():
                d = json.loads(x)
                done[d["question_id"]] = d
    lock = threading.Lock()

    def tr(text: str, sql: str) -> tuple[str, bool]:
        if not text.strip():
            return "", True
        masked, mapping = _protect(text, sql)
        msgs = [{"role": "system", "content": MN_SYSTEM}]
        for s, t in MN_FEWSHOT:
            msgs += [{"role": "user", "content": s}, {"role": "assistant", "content": t}]
        msgs.append({"role": "user", "content": masked})
        resp = llm.chat(msgs, temperature=0.0, max_tokens=512)
        return _restore(resp.texts[0].split("</think>")[-1].strip(), mapping)

    def work(d):
        if d["question_id"] in done:
            return
        q_mn, ok1 = tr(d["question_en"], d["SQL"])
        e_mn, ok2 = tr(d["evidence_en"], d["SQL"])
        rng = random.Random(f"{d['question_id']}")
        rec = dict(d, question_mn=q_mn, evidence_mn=e_mn, question_latin=latin_variant(q_mn, rng),
                   placeholders_ok=ok1 and ok2, reviewed=False, translator=model_name)
        with lock, open(out_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(work, rows))
    recs = [json.loads(x) for x in out_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    bad = sum(1 for d in recs if d.get("placeholders_ok") is False)
    if bad:
        log.warning("F1: %d/%d мөрөнд placeholder сэргээгдээгүй (placeholders_ok=false) — bird-review-export-оор засна; "
                    "засаагүй бол load_bird_mn хасна", bad, len(recs))
    return {"file": str(out_path), "translated": len(recs), "total": len(rows), "placeholders_bad": bad}


def review_export(cfg: Config) -> dict:
    root = bird_dir(cfg)
    rows = [json.loads(x) for x in (root / "bird_mn.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
    path = root / "bird_mn_review.csv"
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        # placeholders_ok (H3): "0" = орчуулгад [V…] сэргээгдээгүй — эхэлж засах мөр (review_import уншихгүй)
        w.writerow(["question_id", "db_id", "question_en", "question_mn", "evidence_en", "evidence_mn", "reviewed",
                    "placeholders_ok"])
        for d in rows:
            w.writerow([d["question_id"], d["db_id"], d["question_en"], d["question_mn"], d["evidence_en"],
                        d["evidence_mn"], "1" if d.get("reviewed") else "",
                        "0" if d.get("placeholders_ok") is False else "1"])
    return {"csv": str(path), "rows": len(rows), "howto": "question_mn/evidence_mn-ийг засаад reviewed=1 тавина"}


def review_import(cfg: Config, csv_path: str | None = None) -> dict:
    root = bird_dir(cfg)
    rows = {json.loads(x)["question_id"]: json.loads(x)
            for x in (root / "bird_mn.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()}
    path = resolve_path(csv_path) if csv_path else root / "bird_mn_review.csv"
    n = 0
    with open(path, encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            qid = int(r["question_id"])
            if qid in rows and (r.get("reviewed") or "").strip() in ("1", "true", "yes"):
                d = rows[qid]
                d["question_mn"], d["evidence_mn"] = r["question_mn"].strip(), r["evidence_mn"].strip()
                d["question_latin"] = latin_variant(d["question_mn"], random.Random(f"{qid}"))
                d["reviewed"] = True
                n += 1
    with open(root / "bird_mn.jsonl", "w", encoding="utf-8") as fh:
        for d in rows.values():
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")
    return {"reviewed_now": n, "reviewed_total": sum(1 for d in rows.values() if d.get("reviewed"))}


def placeholders_bad(d: dict) -> bool:
    """H3: орчуулгад placeholder сэргээгдээгүй (асуулт/evidence-д "[V1]" үлдсэн эсвэл литерал алга), хүн засаагүй мөр.
    Талбаргүй (хуучин) мөр — сайн гэж тооцно."""
    return d.get("placeholders_ok") is False and not d.get("reviewed")


def load_bird_mn(cfg: Config, reviewed_only: bool = False, stats: dict | None = None) -> list[dict]:
    """bird_mn.jsonl-ийн мөрүүд. H3: placeholders_bad мөрийг хасна (тоог log ба stats["dropped_placeholders"]-д —
    run_bird_experiment үүнийг meta.json-ийн eval_set-д бичнэ)."""
    path = bird_dir(cfg) / "bird_mn.jsonl"
    if not path.exists():
        raise FileNotFoundError("F1: data/bird/bird_mn.jsonl алга. Эхлээд: bird-prepare, bird-translate")
    rows = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]
    rows = [d for d in rows if d.get("reviewed") or not reviewed_only]
    bad = sum(1 for d in rows if placeholders_bad(d))
    if bad:
        log.warning("F1: placeholder сэргээгдээгүй, хянагдаагүй %d мөрийг хасав (placeholders_ok=false; "
                    "bird-review-export → засаад reviewed=1)", bad)
    if stats is not None:
        stats["dropped_placeholders"] = bad
    return [d for d in rows if not placeholders_bad(d)]
