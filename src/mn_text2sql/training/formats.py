"""SFT өгөгдөл угсрах: rejection/CoT гаргалт → {prompt, completion} (загварын chat template-ээр).

* omnisql/generic (нэг ээлж): prompt = template(system+user, add_generation_prompt) + prefill; completion = гаралт.
* sqrl (олон ээлж): assistant ээлж бүр нэг мөр — prompt = template(өмнөх яриа), completion = тухайн ээлжийн түүхий
  гаралт (<think> ... </think> + action).
* D2 (prompt=rag): C-ийн гаргалтыг дахин ашиглаж prompt-ыг B5-ийн RAG-тай prompt-оор солино (төлөвлөгөө:
  "D2-т C1-ийн rejection sampling гаргалтыг дахин ашиглаж, зөвхөн prompt-д RAG нэм").
* D3 (prompt=h4s): өөрийн DB-ийн мөрийн prompt-ыг H4s-ийнхээр (schema linking-тэй богино RAG, англи асуулт, өөрийн
  family-гүй few-shot) солино; drop_truncated бол дуусаагүй гаргалтыг хасаад BIRD-ийг bird_limit-ээр детерминист
  таслана (select_bird_samples); bird_prompt: stored бол BIRD-ийг sampling үеийн prompt-оор нь (BIRD-ийн DB Kaggle-д
  байхгүй) үлдээнэ.
* F6 (pivot): reasoning-ийн эхэнд "Restated: <англи нэг өгүүлбэр>" мөр.
* C4 (english_share): англи асуултын гаргалтын эзлэх хувийг тохируулна.
"""

from __future__ import annotations

import json
import logging
import random
import re
import statistics
from collections import Counter
from pathlib import Path

from ..config import Config, load_models
from .data import load_item_index, train_dir

log = logging.getLogger(__name__)

SOURCE_FILES = {"rejection": ["bird_mn"], "cot": ["bird_mn"], "synth": ["own_synth_clean"], "human": ["human"]}
# select_bird_samples: ойролцоо урт (prompts.est_tokens) нь max_length-ийн энэ хувиас давсан BIRD мөрийг сонгохгүй
# (build-д хасагдах магадлалтай тул хязгаарыг дэмий зарахгүй)
BIRD_EST_SHARE = 0.85


def is_truncated(s: dict) -> bool:
    """Arctic (omnisql) гаргалт </answer>-гүй = ихэвчлэн max_tokens (3072)-д тасарсан: SQL нь reasoning доторх өмнөх
    блокоос зөв гарсан ч completion дуусаагүй — сургавал загвар хариугаа хаахгүй урт бичиж сурна (2026-10-18: plain
    гаргалтын ~19% нь ийм; c1–c3, d2 эдгээрээр сурсан). Бусад family-д тодорхойгүй тул False."""
    if s.get("family") != "omnisql":
        return False
    return "</answer>" not in ((s.get("raw_turns") or [""])[0] or "")


def sample_key(s: dict) -> tuple:
    """Гаргалтын тогтмол эрэмбийн түлхүүр (файлын дараалал, орчноос хамаарахгүй)."""
    sql = re.sub(r"\s+", " ", (s.get("final_sql") or "").strip().rstrip(";")).lower()
    return str(s.get("item_id")), str(s.get("variant")), bool(s.get("cot")), sql


def est_sample_tokens(s: dict) -> int:
    """Гаргалтын (хадгалсан prompt + completion) токены ойролцоо тоо — tokenizer-гүй (chat template ~32 токен)."""
    from ..llm.prompts import est_tokens
    prompt = "".join(m.get("content") or "" for m in (s.get("train_messages") or []))
    raw = (s.get("raw_turns") or [""])[0] or ""
    return est_tokens(prompt) + est_tokens(raw) + 32


def select_bird_samples(samples: list[dict], limit: int | None, seed: int = 42,
                        max_length: int | None = None) -> list[dict]:
    """D3: BIRD-ийн гаргалтаас детерминист дэд олонлог (≤ limit). max_length өгвөл ойролцоо урт нь
    BIRD_EST_SHARE·max_length-аас давсныг эхлээд хасна. Буцаах эрэмбэ = sample_key. Idempotent: сонгосон дэд
    олонлогоо дахин өгвөл яг тэр хэвээр (scripts/notebook/pack_train_d3.py → Kaggle-ийн build ижил мөр гаргана)."""
    pool = sorted(samples, key=sample_key)
    if max_length:
        pool = [s for s in pool if est_sample_tokens(s) <= BIRD_EST_SHARE * int(max_length)]
    if limit is None or len(pool) <= int(limit):
        return pool
    picked = random.Random(seed).sample(range(len(pool)), int(limit))
    return [pool[i] for i in sorted(picked)]


def recipe_max_length(tcfg: dict, recipe: str) -> int:
    return int(tcfg["recipes"][recipe].get("max_length") or tcfg["sft_defaults"]["max_length"])


def bird_subset(samples: list[dict], spec: dict, seed: int = 42, max_length: int | None = None
                ) -> tuple[list[dict], int]:
    """bird_limit-тэй жорын BIRD хэсэг — build_sft ба scripts/notebook/pack_train_d3.py ХОЁУЛАА үүнийг дуудна:
    sqlite гаргалт (english_share-гүй жорд англи хувилбаргүй), drop_truncated бол дуусаагүйг хасаад
    select_bird_samples. Уртын урьдчилсан шүүлт нь bird_max_tokens (байхгүй бол max_length) — жорын max_length-ийг
    (өөрийн DB-ийн урт мөрд) өсгөхөд BIRD-ийн сонголт өөрчлөгдөхгүй. Буцаах: (сонгосон, сонгох боломжтой нийт)."""
    pool = [s for s in samples if s.get("engine") == "sqlite"]
    if spec.get("english_share") is None:
        pool = [s for s in pool if s.get("variant") != "en"]
    if spec.get("drop_truncated"):
        pool = [s for s in pool if not is_truncated(s)]
    limit_len = spec.get("bird_max_tokens") or max_length
    return select_bird_samples(pool, spec.get("bird_limit"), seed, limit_len), len(pool)


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def collect_samples(tcfg: dict, model_name: str, prompt_mode: str, data_keys: list[str], tool: bool = False) -> list[dict]:
    """Жорын data түлхүүрээр (rejection, cot, synth, human) sample файлуудыг цуглуулна."""
    d = train_dir(tcfg) / "samples"
    mode = prompt_mode if prompt_mode == "plain" or tool else "plain"   # D2: plain гаргалтыг дахин ашиглана
    out = []
    for key in data_keys:
        for src in SOURCE_FILES.get(key, []):
            for path in sorted(d.glob(f"{model_name}__{mode}{'__tool' if tool else ''}__{src}*.jsonl")):
                name = path.name
                if name.endswith(".stats.jsonl"):
                    continue
                is_cot = name.endswith("__cot.jsonl")
                if (key == "cot") != is_cot and key in ("rejection", "cot"):
                    continue
                if key in ("synth", "human") and is_cot and "cot" not in data_keys:
                    continue
                out += _read_jsonl(path)
    return out


def _pivot_text(raw: str, question_en: str, family: str) -> str:
    line = f"Restated: {question_en.strip()}\n"
    if not question_en:
        return raw
    if raw.lstrip().startswith("<think>"):
        i = raw.index("<think>") + len("<think>")
        return raw[:i] + "\n" + line + raw[i:].lstrip("\n")
    return ("\n" + line if family == "omnisql" else line) + raw


class Renderer:
    """Сургалтын prompt-ыг (plain / rag / h4s) үнэлгээнийхтэй яг ижлээр дахин угсарна (Pipeline.prompt_for)."""

    def __init__(self, cfg: Config, model_name: str, prompt_mode: str, tool: bool = False, pivot: bool = False,
                 index_dir: str | None = None):
        from .sampling import Sampler
        self.sampler = Sampler(cfg, model_name, prompt_mode, pivot=pivot, tool=tool, llm=_NoLLM(), index_dir=index_dir)
        self.items = {}

    def messages(self, item, variant):
        return self.sampler.messages_for(item, variant)

    @property
    def stats(self) -> dict:
        return dict(self.sampler.render_stats)


class _NoLLM:
    name = "none"

    def chat(self, *a, **k):  # pragma: no cover - зөвхөн prompt угсрахад
        raise RuntimeError("Renderer нь LLM дууддаггүй")


_DDL_TABLE = re.compile(r'CREATE TABLE "?(?:\w+\.)?"?(\w+)"?', re.I)
_SQL_BLOCK = re.compile(r"```sql\s*(.*?)```", re.S | re.I)


def unseen_tables(prompt: str, completion: str) -> set[str] | None:
    """Зорилтот SQL-ийн (completion-ий сүүлийн ```sql блок) хүснэгтүүдээс prompt-ын CREATE TABLE-д байхгүй нь (schema
    linking хассан). Prompt-д DDL эсвэл completion-д SQL байхгүй / задлагдахгүй бол None (тоолохгүй)."""
    shown = {t.lower() for t in _DDL_TABLE.findall(prompt)}
    blocks = _SQL_BLOCK.findall(completion)
    if not shown or not blocks:
        return None
    try:
        import sqlglot
        from sqlglot import exp
        tree = sqlglot.parse_one(blocks[-1], read="sqlite")
        ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
        used = {t.name.lower() for t in tree.find_all(exp.Table) if t.name} - ctes
    except Exception:
        return None
    return used - shown


def to_records(sample: dict, tok, pivot: bool = False, new_messages: list[dict] | None = None) -> list[dict]:
    family = sample["family"]
    eos = tok.eos_token or ""
    if family != "sqrl" or not sample.get("conversation"):
        msgs = new_messages or sample["train_messages"]
        prefill = sample.get("prefill") or ""
        prompt = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True) + prefill
        raw = sample["raw_turns"][0]
        completion = raw[len(prefill):] if prefill and raw.startswith(prefill) else raw
        if pivot:
            completion = _pivot_text(completion, sample.get("question_en", ""), family)
        return [{"prompt": prompt, "completion": completion + eos}]
    conv = [dict(m) for m in sample["conversation"]]
    if new_messages:
        conv[: len(new_messages)] = new_messages     # RAG-тай system prompt (D2)
    recs, k = [], 0
    for i, m in enumerate(conv):
        if m["role"] != "assistant":
            continue
        raw = sample["raw_turns"][k] if k < len(sample["raw_turns"]) else m["content"]
        if pivot and k == 0:
            raw = _pivot_text(raw, sample.get("question_en", ""), family)
        prompt = tok.apply_chat_template(conv[:i], tokenize=False, add_generation_prompt=True)
        if prompt.rstrip().endswith("<think>") and raw.lstrip().startswith("<think>"):
            raw = raw.lstrip()[len("<think>"):]
        recs.append({"prompt": prompt, "completion": raw + eos})
        k += 1
    return recs


def build_sft(cfg: Config, tcfg: dict, recipe: str, model_name: str, seed: int = 42, tokenizer=None) -> dict:
    spec = tcfg["recipes"][recipe]
    if spec["kind"] != "sft":
        raise ValueError(f"{recipe} нь SFT жор биш")
    registry = load_models()
    mspec = registry["models"][model_name]
    if tokenizer is None:
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(mspec["hf_id"], revision=mspec.get("revision"), trust_remote_code=True)
    prompt_mode = spec.get("prompt", "plain")
    tool = bool(spec.get("search_values_tool"))
    pivot = bool(spec.get("pivot"))
    data_keys = spec["data"]
    if data_keys == "best_sft":   # D2/F6: C-ийн ШИЛДЭГ жорын өгөгдлийн холимог (training.yaml: best_sft)
        data_keys = tcfg["recipes"][tcfg["best_sft"]]["data"]
    samples = collect_samples(tcfg, model_name, prompt_mode, data_keys, tool)
    if not samples:
        raise FileNotFoundError(f"{recipe}: sample файл алга (data/train/samples/{model_name}__...). Эхлээд sample/cot")
    max_len = recipe_max_length(tcfg, recipe)
    extra: dict = {}
    if spec.get("drop_truncated"):
        # D3: дуусаагүй (тасарсан) гаргалтыг BIRD-ийн хязгаараас ӨМНӨ хасна (pack_train_d3.py мөн ийм дараалалтай)
        cut = Counter(s.get("engine") or "?" for s in samples if is_truncated(s))
        samples = [s for s in samples if not is_truncated(s)]
        extra["dropped_truncated"] = dict(sorted(cut.items()))
        if cut:
            log.warning("%s: дуусаагүй (</answer>-гүй, max_tokens-д тасарсан) гаргалт хасав: %s", recipe, dict(cut))
    if spec.get("bird_limit") is not None:
        # D3: өөрийн DB-ийн бүх мөр + BIRD-ийн детерминист дэд олонлог (pack_train_d3.py-тэй ижил функц)
        own = [s for s in samples if s.get("engine") != "sqlite"]
        chosen, n_bird = bird_subset(samples, spec, seed, max_len)
        samples = own + chosen
        extra.update(bird_available=n_bird, bird_selected=len(chosen))
        log.info("%s: өөрийн DB %d гаргалт + BIRD %d/%d (bird_limit=%s)", recipe, len(own), len(chosen), n_bird,
                 spec["bird_limit"])
    bird_stored = spec.get("bird_prompt", "render") == "stored"
    renderer = None
    if prompt_mode in ("rag", "h4s") and not tool:
        renderer = Renderer(cfg, model_name, prompt_mode, tool, pivot, index_dir=spec.get("index_dir"))
    item_index = load_item_index(tcfg) if renderer is not None else {}
    rng = random.Random(seed)
    en_share = spec.get("english_share")
    if en_share is not None:
        en = [s for s in samples if s["variant"] == "en"]
        other = [s for s in samples if s["variant"] != "en"]
        want_en = int(round(en_share * len(other) / max(1e-9, 1 - en_share)))
        samples = other + rng.sample(en, min(want_en, len(en)))
        if len(en) < want_en:
            log.warning("%s: англи гаргалт хүрэлцэхгүй (%d < %d) — sample --variants en ажиллуул", recipe, len(en),
                        want_en)
    else:
        samples = [s for s in samples if s["variant"] != "en"]
    pairs: list[tuple[str, dict]] = []      # (engine, мөр) — хасалтыг өөрийн DB / BIRD-ээр тайлагнана
    missing = 0
    for s in samples:
        new_msgs = None
        if renderer is not None and not (bird_stored and s.get("engine") == "sqlite"):
            it = item_index.get(s["item_id"])
            if it is None:
                missing += 1
                continue
            new_msgs, _ = renderer.messages(it, s["variant"])
            if s["family"] == "sqrl":
                new_msgs = new_msgs[:1]          # зөвхөн system (schema/evidence) солигдоно
        pairs += [(s.get("engine") or "?", r) for r in to_records(s, tokenizer, pivot, new_msgs)]
    if missing:
        log.warning("%s: %d гаргалтын эх мөр (data/train/*.jsonl) олдсонгүй — алгасав", recipe, missing)
    # T-06: max_length-аас урт мөр (prompt + completion) таслагдаж completion-оо алдахаас сэргийлж хасаж бүртгэнэ
    kept, too_long, lengths, comp_lengths = [], 0, [], []
    by_engine: dict[str, Counter] = {}
    report_all = renderer is not None or bool(extra)
    for eng, r in pairs:
        n_tok = len(tokenizer(r["prompt"] + r["completion"], add_special_tokens=False)["input_ids"])
        c = by_engine.setdefault(eng, Counter())
        c["records"] += 1
        if n_tok > max_len:
            too_long += 1
            c["dropped_too_long"] += 1
            continue
        c["kept"] += 1
        c["tokens"] += n_tok
        if report_all:
            # tester M4: H4s-ийн schema linking хассан хүснэгтийг зорилтот SQL ашигласан мөр — зориуд үлдээнэ (нэг DB тул
            # загвар схемийг сурч, inference-д холболт алдсан ~2% асуултад тус болох магадлалтай); тоог тайлагнана
            miss = unseen_tables(r["prompt"], r["completion"])
            if miss:
                c["target_uses_unseen_table"] += 1
        lengths.append(n_tok)
        if report_all:   # completion-ий урт: CE-ийн санах ой ба сургалтын хугацааны тайланд (D3)
            comp_lengths.append(len(tokenizer(r["completion"], add_special_tokens=False)["input_ids"]))
        kept.append(r)
    if too_long:
        log.warning("%s: %d/%d мөр max_length=%d-аас урт тул хасагдлаа (%s)", recipe, too_long, len(pairs), max_len,
                    {e: c["dropped_too_long"] for e, c in by_engine.items()})
    records = kept
    rng.shuffle(records)
    out = train_dir(tcfg) / "sft" / f"{recipe}__{model_name}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as fh:
        for r in records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    report = {"file": str(out), "samples": len(samples), "records": len(records), "dropped_too_long": too_long,
              "english_samples": sum(1 for s in samples if s["variant"] == "en")}
    if report_all:
        # postgres = өөрийн DB (human + own_synth), sqlite = BIRD; tokens = хадгалсан мөрийн нийт токен (сургалтын хугацаа)
        def _stat(xs: list[int]) -> dict:
            return {"total": sum(xs), "median": int(statistics.median(xs)) if xs else 0, "max": max(xs, default=0)}
        report.update(prompt=prompt_mode, max_length=max_len, missing_items=missing, **extra,
                      by_engine={e: dict(c) for e, c in sorted(by_engine.items())},
                      tokens=_stat(lengths), completion_tokens=_stat(comp_lengths),
                      render=renderer.stats if renderer is not None else {})
        log.info("%s: %d мөр, токен %s, completion %s", recipe, len(records), report["tokens"],
                 report["completion_tokens"])
    return report
