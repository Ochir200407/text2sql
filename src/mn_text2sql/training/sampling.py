"""C1: rejection sampling — асуулт бүрд n (8) гаргалт, гүйцэтгэлийн үр дүн gold-той таарсныг SFT-д хадгална
(загварын өөрийн формат хадгалагдана: Arctic — <think>/<answer>, sqrl — <sql>/<answer> траектор).
C2: нэг ч удаа шийдэгдээгүй асуултад gold SQL-ийг "хувийн тэмдэглэл" болгон өгч reasoning үүсгүүлнэ; gold-ыг шууд
хуулсан (reasoning дотор gold SQL үгчлэн гарсан, "given SQL" гэх мэт дурдсан) гаргалтыг хасна (төлөвлөгөө: C2).
F10: sqrl-ийн search_values tool-той траектор (prompt_mode=rag, tool=True).
D3 (prompt_mode=h4s): H4s-ийн prompt (H4 + schema linking) — Pipeline.prompt_for-оор (үнэлгээтэй ижил код);
англи асуулт = TrainItem.question_en (en_prompt), few-shot-оос мөрийн өөрийн/эх жишээний family хасагдана.

Гаралт: data/train/samples/<model>__<prompt>__<source>[__<variant>].jsonl (зөв гаргалт) + .stats.jsonl (асуулт бүрийн
n, n_correct — C6 GRPO-д "заримдаа шийддэг" асуултыг сонгоход).
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from ..config import Config, load_models
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
    """Эцсийн хариултаас өмнөх reasoning (C2-ийн "gold-ыг шууд хуулсан" шалгалтад)."""
    if family == "sqrl":
        i = text.rfind("<answer>")
        return text[:i] if i >= 0 else text
    i = text.find("<answer>")
    return text[:i] if i >= 0 else text.split("```sql")[0]


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
        if self.prompt_mode == "h4s":
            msgs, prefill = self._h4s_messages(item, variant, q)
        else:
            # plain/rag: орчуулгагүй, item_id-гүй (өмнөх build_context + initial_messages-тэй яг ижил)
            msgs, prefill, *_ = self.pipe.prompt_for(q, variant, None)
        if hint:
            msgs = list(msgs)
            msgs[-1] = dict(msgs[-1], content=msgs[-1]["content"] + HINT_TEMPLATE.format(sql=hint))
        return msgs, prefill

    def _h4s_messages(self, item: TrainItem, variant: str, q: str) -> tuple[list[dict], str | None]:
        """D3: үнэлгээний H4s-тэй ижил prompt (Pipeline.prompt_for). Үнэлгээнд орчуулгын кэшээс авдаг англи асуултын
        оронд мөрийн question_en-ийг (жишээний сан/синтезийн англи асуулт) pipe.translations-д тухайн id:variant
        түлхүүрээр оруулна; retrieval монгол асуултаар. Few-shot-оос мөрийн өөрийн family-г хасна (human мөр сандаа
        өөрөө байгаа тул өөрийгөө жишээ болгон харахгүй; own_synth нь эх жишээний family-ийг)."""
        pipe = self.pipe
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
        from ..eval.compare import results_match
        if not (cand.ok and cand.result is not None):
            return False
        with self._lock:
            g = self._gold_pg.get(item.id)
        if g is None:
            gg = self.pipe.guard.check(item.gold_sql)
            g = self.pipe.executor.run(gg.sql) if gg.ok else None
            with self._lock:
                self._gold_pg[item.id] = g
        return bool(g is not None and g.ok and results_match(g.columns, g.rows, cand.result.columns, cand.result.rows))

    # ------------------------------------------------------------------ үүсгэх
    def sample(self, item: TrainItem, variant: str, n: int, temperature: float, top_p: float,
               hint: str | None = None) -> list[dict]:
        train_msgs, prefill = self.messages_for(item, variant)
        gen_msgs, _ = self.messages_for(item, variant, hint) if hint else (train_msgs, prefill)
        out = []
        if item.engine == "sqlite":
            from ..bird.runner import bird_correct, bird_generate
            for g in bird_generate(item.db_path, gen_msgs, prefill, self.family, self.llm, self.max_tokens, self.opts,
                                   temperature, top_p, n):
                ok, correct = bird_correct(item.db_path, g["sql"], item.gold_sql, self._gold_sqlite)
                out.append({"final_sql": g["sql"], "exec_ok": ok, "correct": correct, "raw_turns": g["raw_turns"],
                            "conversation": g["messages"]})
        elif self.family == "sqrl":
            dec = {"temperature": temperature, "top_p": top_p}
            for i in range(n):
                cand = self.pipe._agent(gen_msgs, dec, seed=i)
                out.append({"final_sql": cand.sql or cand.raw_sql, "exec_ok": cand.ok,
                            "correct": self._correct_pg(item, cand), "raw_turns": cand.raw_turns or [],
                            "conversation": cand.messages or []})
        else:
            r = self.llm.chat(gen_msgs, n=n, temperature=temperature, top_p=top_p, max_tokens=self.max_tokens,
                              prefill=prefill)
            for text in r.texts:
                cand = self.pipe.process_sql(extract_sql(text))
                out.append({"final_sql": cand.sql or cand.raw_sql, "exec_ok": cand.ok,
                            "correct": self._correct_pg(item, cand), "raw_turns": [text], "conversation": None})
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


def sample_file(cfg: Config, tcfg: dict, model_name: str, source_file: str, prompt_mode: str = "plain",
                variants: list[str] | None = None, base_url: str | None = None, limit: int | None = None,
                pivot: bool = False, tool: bool = False, max_keep: int = 4, llm=None) -> dict:
    """C1: source_file (data/train/*.jsonl) асуулт бүрд n гаргалт → зөвийг (давхардалгүй, ≤ max_keep) хадгална."""
    sc = tcfg["sampling"]
    d = train_dir(tcfg)
    items = read_items(d / source_file)
    if limit and len(items) > limit:   # DB-ээр эрэмбэлэгдсэн байж болох тул эхний N биш, детерминист санамсаргүй дээж
        import random
        items = random.Random(42).sample(items, limit)
    sampler = Sampler(cfg, model_name, prompt_mode, pivot, tool, base_url, llm)
    tag = f"{model_name}__{prompt_mode}{'__tool' if tool else ''}__{Path(source_file).stem}" + \
        (f"__{'-'.join(variants)}" if variants else "")
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
        good, seen = [], set()
        for s in samples:
            key = _norm_sql(s["final_sql"])
            if s["correct"] and key not in seen:
                seen.add(key)
                good.append(s)
        good = good[:max_keep]
        with lock:
            with open(out, "a", encoding="utf-8") as fh:
                for s in good:
                    fh.write(json.dumps(s, ensure_ascii=False, default=str) + "\n")
            with open(stats_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps({"item_id": item.id, "variant": variant, "n": len(samples),
                                     "n_correct": sum(1 for s in samples if s["correct"]),
                                     "n_exec": sum(1 for s in samples if s["exec_ok"])}) + "\n")
            totals["questions"] += 1
            totals["solved"] += bool(good)
            totals["kept"] += len(good)

    jobs = [(it, v) for it in items for v in _variants_for(it, variants) if (it.id, v) not in done]
    with ThreadPoolExecutor(max_workers=int(sc.get("workers", 16))) as pool:
        for f in as_completed([pool.submit(work, it, v) for it, v in jobs]):
            f.result()
    return {"file": str(out), "stats": str(stats_path), **totals}


def cot_file(cfg: Config, tcfg: dict, model_name: str, source_file: str, prompt_mode: str = "plain",
             base_url: str | None = None, llm=None) -> dict:
    """C2: stats-д n_correct = 0 асуултад gold-guided reasoning; gold хуулсныг хасна."""
    cc = tcfg["cot"]
    d = train_dir(tcfg)
    tag = f"{model_name}__{prompt_mode}__{Path(source_file).stem}"
    stats_path = d / "samples" / f"{tag}.stats.jsonl"
    if not stats_path.exists():
        raise FileNotFoundError(f"C2-ийн өмнө C1 sampling хийгдээгүй: {stats_path}")
    unsolved = {(json.loads(x)["item_id"], json.loads(x)["variant"]) for x in stats_path.read_text(encoding="utf-8").splitlines()
                if x.strip() and json.loads(x)["n_correct"] == 0}
    items = {it.id: it for it in read_items(d / source_file)}
    sampler = Sampler(cfg, model_name, prompt_mode, base_url=base_url, llm=llm)
    out = d / "samples" / f"{tag}__cot.jsonl"
    drop = [p.lower() for p in cc.get("drop_phrases", [])]
    lock = threading.Lock()
    totals = {"unsolved": len(unsolved), "recovered": 0, "dropped_copy": 0}

    def work(key):
        iid, variant = key
        it = items.get(iid)
        if it is None:
            return
        for _ in range(int(cc.get("attempts", 2))):
            for s in sampler.sample(it, variant, 4, float(cc.get("temperature", 0.7)), 0.95, hint=it.gold_sql):
                if not s["correct"]:
                    continue
                reasoning = " ".join(reasoning_part(t, sampler.family) for t in s["raw_turns"]).lower()
                if _norm_sql(it.gold_sql) in re.sub(r"\s+", " ", reasoning) or any(p in reasoning for p in drop):
                    with lock:
                        totals["dropped_copy"] += 1
                    continue
                with lock, open(out, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(dict(s, cot=True), ensure_ascii=False, default=str) + "\n")
                    totals["recovered"] += 1
                return

    with ThreadPoolExecutor(max_workers=int(tcfg["sampling"].get("workers", 16))) as pool:
        list(pool.map(work, sorted(unsolved)))
    totals["file"] = str(out)
    return totals
