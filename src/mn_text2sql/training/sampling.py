"""C1: rejection sampling — асуулт бүрд n (8) гаргалт, гүйцэтгэлийн үр дүн gold-той таарсныг SFT-д хадгална
(загварын өөрийн формат хадгалагдана: Arctic — <think>/<answer>, sqrl — <sql>/<answer> траектор).
C2: нэг ч удаа шийдэгдээгүй асуултад gold SQL-ийг "хувийн тэмдэглэл" болгон өгч reasoning үүсгүүлнэ; gold-ыг шууд
хуулсан (gold SQL үгчлэн гарсан, "given SQL"/"the note" гэх мэт дурдсан) гаргалтыг хасна (төлөвлөгөө: C2; B2 —
completion бүхэлдээ шалгагдана, copy_reason).
F10: sqrl-ийн search_values tool-той траектор (prompt_mode=rag, tool=True).
D3 (prompt_mode=h4s): H4s-ийн prompt (H4 + schema linking) — Pipeline.prompt_for-оор (үнэлгээтэй ижил код);
англи асуулт = TrainItem.question_en (en_prompt), few-shot-оос мөрийн өөрийн/эх жишээний family хасагдана.
B3/F9: өөрийн DB-ийн prompt БҮХ горимд (plain/rag/h4s) мөрийн id + family-тэй угсарна (өмнө plain/rag нь item_id=None).
R2-A (B9): h4s-ээр шууд түүвэрлэж болно (`sample --prompt h4s`); хадгалах дүрэм (keep_correct) тохируулагдана.

Гаралт: data/train/samples/<model>__<prompt>[__tool]__<source>[__<variant>].jsonl (зөв гаргалт) + .stats.jsonl (асуулт
бүрийн n, n_correct — C6 GRPO-д "заримдаа шийддэг" асуултыг сонгоход); C2: <...>__<source>__cot.jsonl (sample_tag).
2026-10-31 засвар: L1 — dev/test агуулсан индексийг сургалтын prompt-д хэрэглэхгүй (check_training_index); L2 — хүсэлт
бүрд детерминист seed (sample_seed); L7 — cot-ийн файлын нэр sample-тай ижил (__tool); L10 — зөвийн шалгалт мөрийн
order_matters ба код↔нэрийн map-аар (үнэлгээний _match-тай ижил); H3 — placeholder сэргээгдээгүй BIRD мөрийг хасна.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ..config import Config, load_models, resolve_path
from ..llm.client import make_llm
from ..pipeline.options import load_experiments, resolve_run
from ..security.sql_guard import extract_sql
from .data import TrainItem, read_items, train_dir

log = logging.getLogger(__name__)

HINT_TEMPLATE = ("\n\n(Private note for you only: a correct query is\n```sql\n{sql}\n```\nDo NOT mention or quote this "
                 "note. Reason independently from the schema and the question, then give the answer in the required "
                 "format.)")


def _norm_sql(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip().rstrip(";")).lower()


def reasoning_part(text: str, family: str) -> str:
    """Эцсийн хариултаас өмнөх reasoning (хуучин C2 шүүлт; одоо copy_reason нь completion-ийг бүхэлд нь шалгана)."""
    if family == "sqrl":
        i = text.rfind("<answer>")
        return text[:i] if i >= 0 else text
    i = text.find("<answer>")
    return text[:i] if i >= 0 else text.split("```sql")[0]


# ---------------------------------------------------------------------------
# C2: gold-ыг хуулсан гаргалтын шүүлт (B2)
# ---------------------------------------------------------------------------

_FINAL_SQL_BLOCK = re.compile(r"```sql.*?(?:```|$)", re.S | re.I)
# HINT_TEMPLATE-ийн тэмдэглэл (bird/runner.py-тэй ижил текст): prompt-ын контекстоос хасна — хэрэв хадгалсан ярианд үлдсэн
# бол түүний үгс (note, private, correct query) «prompt-д байгаа» гэж тооцогдож шүүлтийг унтраахгүйн тулд
_HINT_NOTE = re.compile(r"\(Private note for you only:.*?in the required format\.\)", re.S)


def _norm_text(s: str | None) -> str:
    return re.sub(r"\s+", " ", s or "").strip().lower()


def _phrase_re(phrase: str) -> re.Pattern:
    """Үгийн эхнээс таарах хэллэг (жижиг/том үсэг, хоосон зай хамаарахгүй): "note" нь "the note", "notes"-д таарна,
    "denote"/"footnote"-д таарахгүй; "gold" нь "gold_sql"-д таарна, "marigold"-д таарахгүй."""
    return re.compile(r"(?<!\w)" + re.escape(_norm_text(phrase)))


def strip_final_sql(text: str, family: str) -> str:
    """Гаргалтаас эцсийн хариултын SQL-ийг хасна (тэр нь gold-той ижил байх нь зөв — хуулбар биш): sqrl — сүүлийн
    <answer>…</answer>-ийн агуулга; бусад — сүүлийн ```sql блок (хаагдаагүй бол төгсгөл хүртэл). Reasoning, <answer>-ийн
    хураангуй, sqrl-ийн судлах <sql> алхам үлдэнэ."""
    if family == "sqrl":
        low = text.lower()
        i = low.rfind("<answer>")
        if i < 0:
            return text
        j = low.find("</answer>", i)
        return text[:i] + (text[j:] if j >= 0 else "")
    blocks = list(_FINAL_SQL_BLOCK.finditer(text))
    if not blocks:
        return text
    m = blocks[-1]
    return text[:m.start()] + text[m.end():]


def copy_reason(sample: dict, gold_sql: str, family: str, phrases: list[str]) -> str | None:
    """C2 (B2): hint-ээр (HINT_TEMPLATE: "Private note … a correct query is …") үүсгэсэн зөв гаргалтыг хасах шалтгаан,
    эсвэл None. Сургалтын зорилт = completion БҮХЭЛДЭЭ (reasoning + <answer>-ийн хураангуй + sqrl-ийн бүх ээлж) тул:
      * gold_verbatim — gold SQL (хэвийн болгосон) эцсийн хариултын SQL-ээс бусад хэсэгт үгчлэн гарсан;
      * phrase:<p> — drop_phrases-ийн хэллэг completion-д гарсан, ГЭХДЭЭ hint-гүй prompt-д (асуулт, схем, evidence,
        sqrl-ийн observation) байхгүй (тэнд байгаа үг — жишээ нь асуулт дахь "private" — хуулбарын шинж биш)."""
    text = "\n".join(t or "" for t in sample.get("raw_turns") or [])
    gold = _norm_sql(gold_sql)
    if gold and gold in _norm_text(strip_final_sql(text, family)):
        return "gold_verbatim"
    body = _norm_text(text)
    context = _norm_text(_HINT_NOTE.sub(" ", "\n".join(
        [m.get("content") or "" for m in sample.get("train_messages") or []]
        + [m.get("content") or "" for m in sample.get("conversation") or [] if m.get("role") != "assistant"]
        + [sample.get("prefill") or ""])))
    for p in phrases:
        if not _norm_text(p):
            continue
        rx = _phrase_re(p)
        if rx.search(body) and not rx.search(context):
            return f"phrase:{_norm_text(p)}"
    return None


_VALUE_MAPS_KEY = ("__value_maps__",)     # pg_correct-ийн cache дахь код↔нэрийн map (gold SQL-тэй давхцахгүй түлхүүр)


def _value_maps(pipe, cache: dict, lock) -> list[dict]:
    """Код↔нэрийн map (eval/compare.build_code_maps — үнэлгээний init_value_maps-тай ижил), cache-д нэг удаа.
    DB-ээс уншиж чадаагүй бол хоосон (өмнөх дүрэм) — sampling/GRPO унахгүй."""
    with lock:
        if _VALUE_MAPS_KEY in cache:
            return cache[_VALUE_MAPS_KEY]
    try:
        from ..eval.compare import build_code_maps
        maps = build_code_maps(pipe.executor)
    except Exception as e:  # noqa: BLE001 — хуурамч/холбогдоогүй executor
        log.warning("код↔нэрийн map уншигдсангүй (%s) — map-гүй харьцуулна", e)
        maps = []
    with lock:
        return cache.setdefault(_VALUE_MAPS_KEY, maps)


def pg_correct(pipe, gold_sql: str, cand, cache: dict, lock, order_matters: bool = False) -> bool:
    """Өөрийн DB-ийн гүйцэтгэлийн зөв эсэх — sampling (C1/C2) ба GRPO-ийн reward (B4) НЭГ дүрмээр: cand нь
    pipe.process_sql-ийн үр дүн (үнэлгээтэй ижил зам: sqlite_transpile → postproc → guard → гүйцэтгэл); gold-ыг
    pipe-ийн guard + executor-оор гүйцэтгэж cache-д (gold SQL → ExecResult) хадгална.
    L10: харьцуулалт үнэлгээний eval/runner._match-тай ижил — мөрийн order_matters (TrainItem.order_matters; өмнө
    үргэлж дараалалгүй), код↔нэрийн map (_value_maps), cfg-ийн хүлцэл."""
    from ..eval.compare import results_match
    if not (cand.ok and cand.result is not None):
        return False
    with lock:
        hit = gold_sql in cache
        g = cache.get(gold_sql)
    if not hit:
        gg = pipe.guard.check(gold_sql)
        g = pipe.executor.run(gg.sql) if gg.ok else None
        with lock:
            cache[gold_sql] = g
    if g is None or not g.ok:
        return False
    ev = (getattr(pipe, "cfg", None) or {}).get("eval") or {}
    return bool(results_match(g.columns, g.rows, cand.result.columns, cand.result.rows, bool(order_matters),
                              float(ev.get("float_tolerance", 0.01)), float(ev.get("relative_tolerance", 1e-6)),
                              value_maps=_value_maps(pipe, cache, lock)))


def keep_correct(samples: list[dict], max_keep: int = 4, max_keep_all: int | None = None) -> list[dict]:
    """C1-ийн хадгалах дүрэм: зөв гаргалтаас ялгаатай SQL-тэйг (хэвийн болгосон) эхний max_keep хүртэл. max_keep_all
    (R2-A: 0–1) өгвөл БҮХ гаргалт зөв (n/n) асуултад тэр хязгаар. 0/n асуултаас юу ч үлдэхгүй. Анхдагч (4, None) =
    өмнөх дүрэм (≤ 4)."""
    good, seen = [], set()
    for s in samples:
        key = _norm_sql(s["final_sql"])
        if s["correct"] and key not in seen:
            seen.add(key)
            good.append(s)
    cap = int(max_keep)
    if max_keep_all is not None and samples and all(s["correct"] for s in samples):
        cap = int(max_keep_all)
    return good[:max(0, cap)]


PROMPT_MODES = ("plain", "rag", "h4s")
# H4s-ийн (D3) хувьсагч: H4-ийг үнэлсэн утга (A7 = sqlite_transpile; best_sft нь prompt-д нөлөөгүй). T2S_BEST_A дарна.
H4S_VARS = {"best_a": "A7", "best_sft": "C3", "best_sft_adapter": "c3"}


def run_options(prompt_mode: str, pivot: bool = False, tool: bool = False, index_dir: str | None = None):
    """plain = A-бүлгийн шилдэг (vars.best_a), rag = B5, h4s = H4s (D3: H4 + schema linking, vars = H4S_VARS).
    Сургалтын prompt нь үнэлгээнийхтэй яг ижил. index_dir: өөр жишээний сантай индекс (D3-ийн жор: build/index_plus)."""
    import os
    if prompt_mode not in PROMPT_MODES:
        raise ValueError(f"prompt_mode={prompt_mode!r} — {'/'.join(PROMPT_MODES)}-ийн нэг")
    exp = load_experiments()
    env_a = os.environ.get("T2S_BEST_A")
    over = {"pivot": pivot, "search_values_tool": tool, "candidates": 1, "repair_rounds": 0}
    if index_dir:
        over["index_dir"] = index_dir
    if prompt_mode == "h4s":
        h4s_vars = dict(H4S_VARS, **({"best_a": env_a} if env_a else {}))
        return resolve_run("H4s", exp, overrides=over, var_overrides=h4s_vars)
    var_over = {"best_a": env_a} if env_a else None
    best_a = (var_over or exp["vars"])["best_a"]
    rid = best_a if prompt_mode == "plain" else "B5"
    return resolve_run(rid, exp, overrides=over, var_overrides=var_over)


def _eval_ids(cfg: Config) -> set[str]:
    """dev/test-ийн асуултын id (cfg eval.dev_file, eval.test_file; файл алга бол алгасна)."""
    ids: set[str] = set()
    for key in ("dev_file", "test_file"):
        p = resolve_path(cfg["eval"][key])
        if p.exists():
            ids |= {str(json.loads(x).get("id")) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()}
    return ids


def check_training_index(cfg: Config, opts) -> None:
    """L1: сургалтын prompt-ын жишээний сан (few-shot) dev/test асуултыг gold SQL-тэй нь агуулж болохгүй. Индексийн
    meta.json-д include_dev: true (build/index_plus_dev, index_web г.м.), эсвэл examples.json-д dev/test-ийн id байвал
    (include_dev талбаргүй хуучин индекс) ValueError. Retrieval-гүй prompt (plain = A-шилдэг) индекс уншдаггүй тул
    шалгахгүй; индекс алга бол алгасна (Pipeline өөрөө алдаа өгнө)."""
    if not opts.needs_retrieval():
        return
    d = resolve_path(opts.index_dir or cfg["knowledge"]["index_dir"])
    if not (d / "meta.json").exists():
        return
    meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
    reason = "meta.json-д include_dev: true" if meta.get("include_dev") else None
    if reason is None and (d / "examples.json").exists():
        ids = {str(e.get("id")) for e in json.loads((d / "examples.json").read_text(encoding="utf-8"))}
        leaked = sorted(ids & _eval_ids(cfg))
        if leaked:
            reason = f"examples.json-д dev/test-ийн {len(leaked)} асуулт ({', '.join(leaked[:5])}{' …' if len(leaked) > 5 else ''})"
    if reason:
        raise ValueError(f"L1: {d} индексийг сургалтын prompt-д ашиглахгүй — {reason}: dev/test асуулт gold SQL-ийнхээ "
                         "хамт few-shot болж сургалтын өгөгдөлд орно. Dev-гүй индекс өг (жишээ нь build/index, "
                         "build/index_plus; --index-dir / жорын index_dir).")


SEED_MASK = 0x7FFFFFFF


def sample_seed(*parts) -> int:
    """L2: хүсэлтийн детерминист seed (vLLM-ийн seed) — мөрийн id, хувилбар, горим, дуудлагын дугаараас sha256-аар
    (PYTHONHASHSEED, thread-ийн дарааллаас үл хамаарна); 31 бит."""
    return int(hashlib.sha256("|".join(map(str, parts)).encode("utf-8")).hexdigest()[:8], 16) & SEED_MASK


class Sampler:
    def __init__(self, cfg: Config, model_name: str, prompt_mode: str = "plain", pivot: bool = False,
                 tool: bool = False, base_url: str | None = None, llm=None, index_dir: str | None = None):
        from ..pipeline.orchestrator import Pipeline
        self.cfg = cfg
        registry = load_models()
        self.model_name = model_name
        self.mspec = registry["models"][model_name]
        self.family = self.mspec.get("family", "generic")
        self.prompt_mode = prompt_mode
        self.opts = run_options(prompt_mode, pivot, tool, index_dir)
        check_training_index(cfg, self.opts)          # L1: dev/test-тэй индексээр сургалтын prompt угсрахгүй
        self._calls: Counter = Counter()              # L2: (мөр, хувилбар, горим) тус бүрийн дуудлагын дугаар → seed
        self.render_stats: Counter = Counter()      # h4s: англи асуулт, хасагдсан family-ийн тоо (build-ийн тайланд)
        self._pool_families: dict[str, str] | None = None
        if llm is None:
            llm_cfg = dict(cfg["llm"], model=self.mspec.get("served_name", model_name))
            if base_url:
                llm_cfg["base_url"] = base_url
            if self.mspec.get("extra_body"):
                llm_cfg["extra_body"] = self.mspec["extra_body"]
            llm = make_llm(llm_cfg)
        self.llm = llm
        self.max_tokens = int(self.mspec.get("max_tokens", 3072))
        self._pipe = None
        self._pipe_args = (cfg, self.opts, model_name, llm)
        self._Pipeline = Pipeline
        self._gold_pg: dict[str, object] = {}
        self._gold_sqlite: dict = {}
        self._lock = threading.Lock()

    @property
    def pipe(self):
        if self._pipe is None:
            self._pipe = self._Pipeline(*self._pipe_args)
        return self._pipe

    # ------------------------------------------------------------------ prompt
    def messages_for(self, item: TrainItem, variant: str, hint: str | None = None) -> tuple[list[dict], str | None]:
        q = item.question(variant)
        if item.engine == "sqlite":
            from ..bird.runner import bird_prompt
            return bird_prompt(q, item.evidence(variant), item.db_path, self.opts, self.family, hint_sql=hint,
                               include_evidence=True)
        msgs, prefill = self._pg_messages(item, variant, q)
        if hint:
            msgs = list(msgs)
            msgs[-1] = dict(msgs[-1], content=msgs[-1]["content"] + HINT_TEMPLATE.format(sql=hint))
        return msgs, prefill

    def _pg_messages(self, item: TrainItem, variant: str, q: str) -> tuple[list[dict], str | None]:
        """Өөрийн DB-ийн prompt — үнэлгээтэй ижил код (Pipeline.prompt_for), БҮХ горимд (plain/rag/h4s) мөрийн id-тай.
        Few-shot-оос мөрийн өөрийн family-г хасна (human мөр жишээний сандаа өөрөө байгаа тул өөрийгөө жишээ болгон
        харахгүй; own_synth нь эх жишээний family-ийг) — B3/F9: өмнө plain/rag нь item_id=None-оор угсардаг байсан тул
        RAG-тай (B5/D2/D4) prompt-д мөр өөрийнхөө gold SQL-ийг few-shot болгон хардаг байв. plain (A-шилдэг) нь
        retrieval-гүй тул prompt өөрчлөгдөхгүй.
        h4s (D3): үнэлгээнд орчуулгын кэшээс авдаг англи асуултын оронд мөрийн question_en-ийг (жишээний сан/синтезийн
        англи асуулт) pipe.translations-д тухайн id:variant түлхүүрээр оруулна; retrieval монгол асуултаар."""
        pipe = self.pipe
        if self.prompt_mode == "h4s":
            if item.question_en:
                pipe.translations[f"{item.id}:{variant}"] = item.question_en
                self.render_stats["english_question"] += 1
            else:
                self.render_stats["no_english_question"] += 1   # H4-ийн орчуулгагүй асуулт шиг монголоор үлдэнэ
        fam = self.item_family(item)
        if fam:
            pipe.item_families[item.id] = fam
            self.render_stats["family_excluded"] += 1
        else:
            self.render_stats["no_family"] += 1
        msgs, prefill, *_ = pipe.prompt_for(q, variant, item.id)
        return msgs, prefill

    def item_family(self, item: TrainItem) -> str | None:
        """Few-shot-оос хасах family: human — өөрийн (extra.family), own_synth — эх жишээний (extra.template =
        human-<жишээний id> → тухайн индексийн жишээний сангаас family). Олдохгүй бол None."""
        extra = item.extra or {}
        if extra.get("family"):
            return str(extra["family"])
        if self._pool_families is None:
            ret = self.pipe.retriever
            self._pool_families = {e.id: e.family for e in ret.examples.examples if e.family} if ret is not None else {}
        for ref in (extra.get("template") or "", item.id):
            fam = self._pool_families.get(str(ref).removeprefix("human-"))
            if fam:
                return fam
        return None

    # ------------------------------------------------------------------ шалгах
    def _correct_pg(self, item: TrainItem, cand) -> bool:
        return pg_correct(self.pipe, item.gold_sql, cand, self._gold_pg, self._lock, item.order_matters)

    def next_seed(self, item: TrainItem, variant: str, hint: str | None = None) -> int:
        """L2: детерминист seed — (мөр, хувилбар, hint-тэй эсэх) ба тухайн түлхүүрийн хэд дэх дуудлага (C2-ийн оролдлого
        бүр өөр seed; resume-д үлдсэн мөр шинэ ажиллуулалттай ижил seed авна)."""
        key = (item.id, variant, "hint" if hint else "free")
        with self._lock:
            k = self._calls[key]
            self._calls[key] += 1
        return sample_seed(*key, k)

    # ------------------------------------------------------------------ үүсгэх
    def sample(self, item: TrainItem, variant: str, n: int, temperature: float, top_p: float,
               hint: str | None = None) -> list[dict]:
        train_msgs, prefill = self.messages_for(item, variant)
        gen_msgs, _ = self.messages_for(item, variant, hint) if hint else (train_msgs, prefill)
        seed = self.next_seed(item, variant, hint)
        out = []
        if item.engine == "sqlite":
            from ..bird.runner import bird_correct, bird_generate
            for k, g in enumerate(bird_generate(item.db_path, gen_msgs, prefill, self.family, self.llm, self.max_tokens,
                                                self.opts, temperature, top_p, n, seed=seed)):
                ok, correct = bird_correct(item.db_path, g["sql"], item.gold_sql, self._gold_sqlite)
                out.append({"final_sql": g["sql"], "exec_ok": ok, "correct": correct, "raw_turns": g["raw_turns"],
                            "conversation": g["messages"],
                            "seed": (seed + k) & SEED_MASK if self.family == "sqrl" else seed})
        elif self.family == "sqrl":
            dec = {"temperature": temperature, "top_p": top_p}
            for i in range(n):
                s = (seed + i) & SEED_MASK         # өмнө бүх мөрд seed=i (0..n-1) — одоо мөр бүрийн seed + i
                cand = self.pipe._agent(gen_msgs, dec, seed=s)
                out.append({"final_sql": cand.sql or cand.raw_sql, "exec_ok": cand.ok,
                            "correct": self._correct_pg(item, cand), "raw_turns": cand.raw_turns or [],
                            "conversation": cand.messages or [], "seed": s})
        else:
            r = self.llm.chat(gen_msgs, n=n, temperature=temperature, top_p=top_p, max_tokens=self.max_tokens,
                              prefill=prefill, seed=seed)
            for text in r.texts:
                cand = self.pipe.process_sql(extract_sql(text))
                out.append({"final_sql": cand.sql or cand.raw_sql, "exec_ok": cand.ok,
                            "correct": self._correct_pg(item, cand), "raw_turns": [text], "conversation": None,
                            "seed": seed})
        for o in out:
            o.update({"item_id": item.id, "source": item.source, "engine": item.engine, "variant": variant,
                      "model": self.model_name, "family": self.family, "train_messages": train_msgs,
                      "prefill": prefill, "hint": hint, "question_en": item.question_en})
            if hint and o.get("conversation"):
                # сургалтын харилцан яриаг hint-гүй болгоно (загвар gold-ыг хараагүй мэт сурна)
                conv = [dict(m) for m in o["conversation"]]
                note = HINT_TEMPLATE.format(sql=hint)
                for m in conv:
                    if m.get("role") == "user" and note in (m.get("content") or ""):
                        m["content"] = m["content"].replace(note, "")
                        break
                o["conversation"] = conv
        return out


def _variants_for(item: TrainItem, variants: list[str] | None) -> list[str]:
    if variants:
        return [v for v in variants if item.question(v)]
    return [item.extra.get("train_variant", "mn")]


def sample_tag(model_name: str, prompt_mode: str, source_file: str, tool: bool = False,
               variants: list[str] | None = None) -> str:
    """Sample файлын нэр (өргөтгөлгүй): <model>__<prompt>[__tool]__<source>[__<variants>] — sample_file ба cot_file
    (L7: өмнө cot-д __tool алга тул tool-той sampling-ийн stats олдохгүй, гаргалт нь formats.collect_samples(tool=True)-ийн
    ижил дүрмийн glob-д цуглардаггүй байв)."""
    return f"{model_name}__{prompt_mode}{'__tool' if tool else ''}__{Path(source_file).stem}" + \
        (f"__{'-'.join(variants)}" if variants else "")


def sample_file(cfg: Config, tcfg: dict, model_name: str, source_file: str, prompt_mode: str = "plain",
                variants: list[str] | None = None, base_url: str | None = None, limit: int | None = None,
                pivot: bool = False, tool: bool = False, max_keep: int | None = None, llm=None,
                max_keep_all: int | None = None, index_dir: str | None = None) -> dict:
    """C1: source_file (data/train/*.jsonl) асуулт бүрд n гаргалт → зөвийг (давхардалгүй) keep_correct-оор хадгална.
    max_keep / max_keep_all: аргумент → training.yaml sampling.max_keep / max_keep_all_correct → 4 / None (өмнөх
    дүрэм). index_dir: rag/h4s-ийн жишээний сан (анхдагч: тухайн run-ийн, H4s = build/index_plus)."""
    sc = tcfg["sampling"]
    if max_keep is None:
        max_keep = int(sc.get("max_keep", 4))
    if max_keep_all is None and sc.get("max_keep_all_correct") is not None:
        max_keep_all = int(sc["max_keep_all_correct"])
    d = train_dir(tcfg)
    items = read_items(d / source_file)
    # H3: BIRD-ийн орчуулгад placeholder сэргээгдээгүй мөр (асуултад "[V1]" үлдсэн / литерал алга) сургалтад орохгүй
    bad = [it.id for it in items if not it.placeholders_ok]
    if bad:
        log.warning("%s: placeholder сэргээгдээгүй %d мөрийг хасав (жишээ: %s)", source_file, len(bad), bad[:5])
        items = [it for it in items if it.placeholders_ok]
    if limit and len(items) > limit:   # DB-ээр эрэмбэлэгдсэн байж болох тул эхний N биш, детерминист санамсаргүй дээж
        import random
        items = random.Random(42).sample(items, limit)
    sampler = Sampler(cfg, model_name, prompt_mode, pivot, tool, base_url, llm, index_dir=index_dir)
    tag = sample_tag(model_name, prompt_mode, source_file, tool, variants)
    out_dir = d / "samples"
    out_dir.mkdir(parents=True, exist_ok=True)
    out, stats_path = out_dir / f"{tag}.jsonl", out_dir / f"{tag}.stats.jsonl"
    done = set()
    if stats_path.exists():
        done = {(json.loads(x)["item_id"], json.loads(x)["variant"]) for x in stats_path.read_text(encoding="utf-8").splitlines() if x.strip()}
    lock = threading.Lock()
    totals = {"questions": 0, "solved": 0, "kept": 0}

    def work(item: TrainItem, variant: str):
        samples = sampler.sample(item, variant, int(sc["n"]), float(sc["temperature"]), float(sc["top_p"]))
        good = keep_correct(samples, max_keep, max_keep_all)
        n_correct = sum(1 for s in samples if s["correct"])
        with lock:
            with open(out, "a", encoding="utf-8") as fh:
                for s in good:
                    fh.write(json.dumps(s, ensure_ascii=False, default=str) + "\n")
            with open(stats_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"item_id": item.id, "variant": variant, "n": len(samples),
                                     "n_correct": n_correct,
                                     "n_exec": sum(1 for s in samples if s["exec_ok"])}) + "\n")
            totals["questions"] += 1
            totals["solved"] += n_correct > 0
            totals["kept"] += len(good)

    jobs = [(it, v) for it in items for v in _variants_for(it, variants) if (it.id, v) not in done]
    with ThreadPoolExecutor(max_workers=int(sc.get("workers", 16))) as pool:
        for f in as_completed([pool.submit(work, it, v) for it, v in jobs]):
            f.result()
    return {"file": str(out), "stats": str(stats_path), **totals, "dropped_placeholders": len(bad),
            "keep": {"max_keep": max_keep, "max_keep_all_correct": max_keep_all}}


def cot_file(cfg: Config, tcfg: dict, model_name: str, source_file: str, prompt_mode: str = "plain",
             base_url: str | None = None, llm=None, index_dir: str | None = None, tool: bool = False,
             pivot: bool = False) -> dict:
    """C2: stats-д n_correct = 0 асуултад gold-guided reasoning; gold хуулсныг (copy_reason) хасаж, шалтгаанаар тоолно.
    tool/pivot (L7): sample-тай ижил (F10: --prompt rag --tool) — stats ба гаралтын нэр sample_tag-аар
    (<model>__rag__tool__<source>__cot.jsonl → collect_samples(tool=True) цуглуулна)."""
    cc = tcfg["cot"]
    d = train_dir(tcfg)
    tag = sample_tag(model_name, prompt_mode, source_file, tool)
    stats_path = d / "samples" / f"{tag}.stats.jsonl"
    if not stats_path.exists():
        raise FileNotFoundError(f"C2-ийн өмнө C1 sampling хийгдээгүй: {stats_path}"
                                + ("" if tool else " (tool-той sampling бол cot --tool)"))
    unsolved = {(json.loads(x)["item_id"], json.loads(x)["variant"]) for x in stats_path.read_text(encoding="utf-8").splitlines()
                if x.strip() and json.loads(x)["n_correct"] == 0}
    items = {it.id: it for it in read_items(d / source_file) if it.placeholders_ok}     # H3 (sample_file-тэй ижил)
    sampler = Sampler(cfg, model_name, prompt_mode, pivot=pivot, tool=tool, base_url=base_url, llm=llm,
                      index_dir=index_dir)
    out = d / "samples" / f"{tag}__cot.jsonl"
    drop = [str(p) for p in cc.get("drop_phrases", []) if str(p).strip()]
    lock = threading.Lock()
    totals = {"unsolved": len(unsolved), "recovered": 0, "dropped_copy": 0}
    reasons: Counter = Counter()

    def work(key):
        iid, variant = key
        it = items.get(iid)
        if it is None:
            return
        for _ in range(int(cc.get("attempts", 2))):
            for s in sampler.sample(it, variant, 4, float(cc.get("temperature", 0.7)), 0.95, hint=it.gold_sql):
                if not s["correct"]:
                    continue
                why = copy_reason(s, it.gold_sql, sampler.family, drop)
                if why:
                    with lock:
                        totals["dropped_copy"] += 1
                        reasons[why] += 1
                    continue
                with lock, open(out, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(dict(s, cot=True), ensure_ascii=False, default=str) + "\n")
                    totals["recovered"] += 1
                return

    with ThreadPoolExecutor(max_workers=int(tcfg["sampling"].get("workers", 16))) as pool:
        list(pool.map(work, sorted(unsolved)))
    totals["dropped_by_reason"] = dict(reasons.most_common())
    log.info("C2 %s: %d/%d асуулт сэргэв; gold хуулсан гэж хасагдсан %d гаргалт %s", tag, totals["recovered"],
             totals["unsolved"], totals["dropped_copy"], totals["dropped_by_reason"])
    totals["file"] = str(out)
    return totals
