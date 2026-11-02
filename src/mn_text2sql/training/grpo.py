"""GRPO (C6, C7, D4, F4) — TRL GRPOTrainer, vLLM-ээр rollout (colocate), гүйцэтгэлийн reward.

Reward (туршилтын төлөвлөгөө): 1 — үр дүн gold-той таарсан; 0.1 — ажилласан ч буруу; 0 — алдаа/SQL алга.
Асуулт: C1-ийн sampling-д "заримдаа шийддэг" (n-ээс 1..n−1 нь зөв) асуултууд (анхдагч 2000).
C7: англи асуултын replay (english_replay хувь). F4: асуулт бүрийг гурван хувилбараар (кирилл, латин, англи)
дараалуулж нэг batch-д оруулж, "бүх хувилбар ижил үр дүн өгвөл" нэмэлт оноо (script_invariant_bonus).
sqrl-ийн RL нь нэг ээлжит (max_steps = 0, A5 протокол) — TRL-ийн GRPO олон ээлжит tool орчин дэмждэггүй тул.
Эхлэл (init): SFT adapter-ийг суурьт нэгтгэсэн жин (merge_adapter) → шинэ LoRA; үр дүнг нэгтгэж vLLM-д тусдаа
загвар болгон (served-model-name = жорын нэр) ажиллуулна (docs/TRAINING.md). --init өгөөгүй бол жорын init
(best_sft → adapters/<best_sft>__<model>__merged, d2 → adapters/d2__<model>__merged; B5).
Өөрийн DB-ийн reward нь үнэлгээтэй ижил замаар (Pipeline.process_sql: A7-ийн sqlite_transpile, postproc, guard) —
prompt нь SQLite гэж заасан үед зөв SQLite хариулт 0 авахгүй (B4); зөвийн дүрэм нь sampling-тай нэг (pg_correct).
"""

from __future__ import annotations

import json
import os
import logging
import random
import threading
from collections import defaultdict
from pathlib import Path

from ..config import Config, ROOT, load_models
from ..security.sql_guard import extract_sql

log = logging.getLogger(__name__)


class ExecReward:
    """Rollout бүрийн SQL-ийг гүйцэтгэж gold-той харьцуулна (SQLite: BIRD, PostgreSQL: өөрийн DB).
    pipe: prompt-ын RunOptions-той Pipeline (run_grpo нь dataset угсарсан Sampler-ийн pipe-ийг өгнө) — өөрийн DB-ийн SQL
    нь pipe.process_sql-ээр (sqlite_transpile → postproc → guard → гүйцэтгэл), үнэлгээ ба sampling-тай яг ижил.
    pipe=None бол анхны өөрийн DB-ийн мөрөнд plain (A-шилдэг) тохиргооны Pipeline үүсгэнэ."""

    def __init__(self, cfg: Config, family: str, weights: dict, bonus: float = 0.0, pipe=None):
        self.cfg = cfg
        self.family = family
        self.w = weights
        self.bonus = float(bonus)
        self._pipe = pipe
        self._gold_cache: dict = {}     # BIRD: (db_path, gold) → (ok, rows)
        self._gold_pg: dict = {}        # өөрийн DB: gold SQL → ExecResult (pg_correct)
        self._lock = threading.Lock()
        self.__name__ = "exec_reward"

    def _pg_pipe(self):
        with self._lock:
            if self._pipe is None:
                from ..pipeline.orchestrator import Pipeline
                from .sampling import run_options
                self._pipe = Pipeline(self.cfg, run_options("plain"), "generic", llm=_NoLLM())
            return self._pipe

    def _sql(self, completion) -> str | None:
        text = completion if isinstance(completion, str) else (completion[-1].get("content") if completion else "")
        if self.family == "sqrl":
            from ..llm.prompts import parse_sqrl_action
            kind, body = parse_sqrl_action(text)
            return body if kind == "answer" else extract_sql(text)
        return extract_sql(text)

    def _score_one(self, sql, engine, db_path, gold_sql, order_matters: bool = False) -> tuple[float, str | None]:
        if not sql:
            return self.w["error"], None
        if engine == "sqlite":
            from ..bird.runner import bird_correct, run_sqlite
            ok, correct = bird_correct(db_path, sql, gold_sql, self._gold_cache)
            if not ok:
                return self.w["error"], None
            okp, rows = run_sqlite(db_path, sql, 30)
            return (self.w["correct"] if correct else self.w["executable"]), repr(sorted(map(repr, rows)))[:4000]
        from ..eval.compare import result_hash
        from .sampling import pg_correct
        pipe = self._pg_pipe()
        cand = pipe.process_sql(sql)        # үнэлгээтэй ижил: dialect хөрвүүлэлт, postproc, guard, гүйцэтгэл
        if not (cand.ok and cand.result is not None):
            return self.w["error"], None
        correct = pg_correct(pipe, gold_sql, cand, self._gold_pg, self._lock, order_matters)
        res = cand.result
        return (self.w["correct"] if correct else self.w["executable"]), result_hash(res.columns, res.rows)

    def __call__(self, prompts, completions, engine, db_path, gold_sql, qid=None, variant=None, order_matters=None,
                 **kw) -> list[float]:
        """order_matters (L10): dataset-ийн багана (build_grpo_dataset) — үнэлгээ шиг мөр бүрийн дарааллын дүрэм;
        байхгүй (хуучин dataset) бол дараалалгүй (өмнөх дүрэм)."""
        scores, hashes = [], []
        oms = list(order_matters) if order_matters is not None else [False] * len(completions)
        for c, e, p, gsql, om in zip(completions, engine, db_path, gold_sql, oms):
            s, h = self._score_one(self._sql(c), e, p, gsql, bool(om))
            scores.append(s)
            hashes.append(h)
        if self.bonus and qid is not None and variant is not None:
            scores = [s + b for s, b in zip(scores, invariance_bonus(qid, variant, hashes, self.bonus))]
        return scores


def invariance_bonus(qid, variant, hashes, bonus: float) -> list[float]:
    """F4: completion бүрийн бонус — үр дүнгийн hash нь тухайн асуултын БУСАД хувилбаруудын (кирилл/латин/англи)
    олонхын hash-тай таарвал +bonus. Ингэснээр нэг хувилбарын 8 rollout дотор бонус ялгаатай болж GRPO-ийн
    advantage-д нөлөөлнө (бүх rollout-д тогтмол нэмэх нь advantage-ийг өөрчилдөггүй)."""
    from collections import Counter
    groups = defaultdict(list)
    for i, q in enumerate(qid):
        groups[q].append(i)
    out = [0.0] * len(hashes)
    for idx in groups.values():
        by_var = defaultdict(list)
        for i in idx:
            by_var[variant[i]].append(i)
        if len(by_var) < 2:
            continue
        for v, own in by_var.items():
            others = Counter(hashes[i] for vv, ii in by_var.items() if vv != v for i in ii if hashes[i] is not None)
            if not others:
                continue
            top = others.most_common(1)[0][0]
            for i in own:
                if hashes[i] is not None and hashes[i] == top:
                    out[i] = bonus
    return out


def grpo_params(tcfg: dict, recipe: str) -> dict:
    """L9: grpo_defaults + жорын түвшний дарах утга (run_sft-ийн sft_defaults-тай ижил дүрэм: grpo_defaults-д байгаа
    түлхүүр л; reward шиг dict нь нэг түвшинд нийлнэ). Өмнө жорын утга чимээгүй үл тоомсорлогддог байв (одоогийн c6/c7/
    d4/f4 жорд ийм түлхүүр алга — үр дүн өөрчлөгдөхгүй)."""
    gp = dict(tcfg["grpo_defaults"])
    for k, v in (tcfg["recipes"][recipe] or {}).items():
        if k in gp:
            gp[k] = {**gp[k], **v} if isinstance(gp[k], dict) and isinstance(v, dict) else v
    return gp


def grpo_sampler(cfg: Config, tcfg: dict, recipe: str, model_name: str):
    """Жорын prompt-ын Sampler (LLM-гүй): GRPO-ийн prompt угсралт ба reward-ийн Pipeline (B4) нэг RunOptions-оор."""
    from .sampling import Sampler
    spec = tcfg["recipes"][recipe]
    sampler = Sampler(cfg, model_name, spec.get("prompt", "plain"), llm=_NoLLM(), index_dir=spec.get("index_dir"))
    if load_models()["models"][model_name].get("family") == "sqrl":
        sampler.opts.agent_max_steps = 0            # нэг ээлжит RL (A5 протокол)
    return sampler


def resolve_init(tcfg: dict, recipe: str, model_name: str, init_model: str | None = None) -> tuple[str, str]:
    """GRPO-ийн эхлэл (B5): --init → жорын init (best_sft → training.yaml best_sft; жорын нэр →
    adapters/<нэр>__<model>__merged; бусад нь зам/HF id) → best_sft. Буцаах: (зам, эх сурвалж)."""
    if init_model:
        return init_model, "cli"
    ref = tcfg["recipes"][recipe].get("init") or "best_sft"
    source = f"recipe:{ref}"
    if ref == "best_sft":
        ref = tcfg.get("best_sft", "c3")
    if ref in tcfg["recipes"]:
        return str(ROOT / "adapters" / f"{ref}__{model_name}__merged"), source
    return str(ref), source


def build_grpo_dataset(cfg: Config, tcfg: dict, recipe: str, model_name: str, seed: int = 42, sampler=None,
                       tokenizer=None) -> list[dict]:
    from .data import load_item_index, train_dir
    spec = tcfg["recipes"][recipe]
    gp = grpo_params(tcfg, recipe)
    mspec = load_models()["models"][model_name]
    if tokenizer is None:
        from transformers import AutoTokenizer
        tokenizer = AutoTokenizer.from_pretrained(mspec["hf_id"], revision=mspec.get("revision"), trust_remote_code=True)
    tok = tokenizer
    d = train_dir(tcfg)
    items = load_item_index(tcfg)
    stats = []
    # L2: файлын эрэмбэ тогтмол (glob-ийн дараалал файлын системээс хамаарч rng.shuffle-ийн оролт өөрчлөгддөг байв)
    for p in sorted((d / "samples").glob(f"{model_name}__plain__*.stats.jsonl")):
        stats += [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    # T-08: англи sampling-ийн статистикийг оруулахгүй (англи replay-ийг C7 тусад нь нэмнэ)
    sometimes = [s for s in stats if 0 < s["n_correct"] < s["n"] and s["item_id"] in items and s["variant"] != "en"]
    rng = random.Random(seed)
    rng.shuffle(sometimes)
    sometimes = sometimes[: int(gp["n_questions"])]
    if sampler is None:
        sampler = grpo_sampler(cfg, tcfg, recipe, model_name)
    variants_spec = spec.get("variants")
    rows = []
    too_long = 0
    for s in sometimes:
        it = items[s["item_id"]]
        variants = variants_spec or [s["variant"]]
        for v in variants:
            if not it.question(v):
                continue
            msgs, prefill = sampler.messages_for(it, v)
            prompt = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True) + (prefill or "")
            if len(tok(prompt, add_special_tokens=False)["input_ids"]) > int(gp["max_prompt_length"]):
                too_long += 1     # T-06: зүүнээс таслагдсан prompt-оор сургахгүй
                continue
            rows.append({"prompt": prompt, "engine": it.engine, "db_path": it.db_path or "", "gold_sql": it.gold_sql,
                         "qid": it.id, "variant": v, "order_matters": it.order_matters})
    replay = float(spec.get("english_replay", 0) or 0)
    if replay:
        extra = [r for r in list(rows) if r["variant"] != "en"]
        rng.shuffle(extra)
        for r in extra[: int(replay * len(rows))]:
            it = items[r["qid"]]
            if it.question_en:
                msgs, prefill = sampler.messages_for(it, "en")
                rows.append(dict(r, prompt=tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
                                 + (prefill or ""), variant="en"))
    if not variants_spec:
        rng.shuffle(rows)        # F4: гурван хувилбар дараалсан хэвээр (shuffle_dataset=False)
    if too_long:
        log.warning("GRPO %s: %d prompt max_prompt_length-аас урт тул хасагдлаа", recipe, too_long)
    return rows


class _NoLLM:
    name = "none"

    def chat(self, *a, **k):  # pragma: no cover
        raise RuntimeError("dataset угсрахад LLM дуудахгүй")


def run_grpo(cfg: Config, tcfg: dict, recipe: str, model_name: str, init_model: str | None = None,
             output_dir: str | None = None, max_steps: int | None = None, force: bool = False) -> dict:
    from datasets import Dataset
    from peft import LoraConfig
    from trl import GRPOConfig, GRPOTrainer

    from .provenance import model_revision, provenance, rows_sha256
    from .sft import config_kwargs, finished, last_checkpoint
    spec = tcfg["recipes"][recipe]
    gp = grpo_params(tcfg, recipe)
    mspec = load_models()["models"][model_name]
    out = Path(output_dir) if output_dir else ROOT / "adapters" / f"{recipe}__{model_name}"
    if not force and (done := finished(out)):
        log.info("GRPO %s аль хэдийн дууссан (%s) — алгасав (--force-оор дахин)", recipe, out)
        return {"adapter": str(out), "skipped": True, **done}
    sampler = grpo_sampler(cfg, tcfg, recipe, model_name)
    rows = build_grpo_dataset(cfg, tcfg, recipe, model_name, sampler=sampler)
    if not rows:
        raise RuntimeError("GRPO-д 'заримдаа шийддэг' асуулт алга — эхлээд sample (C1) ажиллуулна")
    ds = Dataset.from_list(rows)
    init, init_source = resolve_init(tcfg, recipe, model_name, init_model)
    log.info("GRPO %s: эхлэл %s (%s)", recipe, init, init_source)
    f4 = bool(spec.get("script_invariant_bonus"))
    gen_bs = int(gp["per_device_batch_size"]) * int(gp["num_generations"])
    if f4 and gen_bs % (3 * int(gp["num_generations"])):
        gen_bs = 3 * int(gp["num_generations"]) * max(1, gen_bs // (3 * int(gp["num_generations"])))
    args = config_kwargs(GRPOConfig, dict(
        output_dir=str(out), learning_rate=float(gp["learning_rate"]), beta=float(gp["beta"]),
        num_generations=int(gp["num_generations"]), temperature=float(gp["temperature"]),
        max_prompt_length=int(gp["max_prompt_length"]), max_completion_length=int(gp["max_completion_length"]),
        per_device_train_batch_size=int(gp["per_device_batch_size"]),
        gradient_accumulation_steps=int(gp["gradient_accumulation"]), num_train_epochs=float(gp["epochs"]),
        bf16=True, gradient_checkpointing=True, use_vllm=True, vllm_mode=gp.get("vllm_mode", "colocate"),
        vllm_gpu_memory_utilization=float(gp.get("vllm_gpu_memory_utilization", 0.35)), logging_steps=5,
        save_steps=int(os.environ.get("T2S_SAVE_STEPS") or gp.get("save_steps", 50)), save_total_limit=2, report_to="none", seed=42, max_steps=int(max_steps) if max_steps else -1,
        shuffle_dataset=not f4, generation_batch_size=gen_bs if f4 else None))
    own_db = any(r["engine"] != "sqlite" for r in rows)   # BIRD-ийн мөр л бол PG-ийн Pipeline үүсгэхгүй
    reward = ExecReward(cfg, mspec.get("family", "generic"), gp["reward"],
                        bonus=float(spec.get("script_invariant_bonus", 0) or 0),
                        pipe=sampler.pipe if own_db else None)
    peft_cfg = LoraConfig(r=int(tcfg["sft_defaults"]["lora_r"]), lora_alpha=int(tcfg["sft_defaults"]["lora_alpha"]),
                          lora_dropout=0.05, target_modules="all-linear", task_type="CAUSAL_LM")
    trainer = GRPOTrainer(model=init, reward_funcs=[reward], args=args, train_dataset=ds, peft_config=peft_cfg)
    resume = last_checkpoint(out)
    if resume:
        log.info("GRPO %s: %s-ээс үргэлжлүүлнэ", recipe, resume)
    trainer.train(resume_from_checkpoint=resume)
    trainer.save_model(str(out))
    meta = {"recipe": recipe, "model": model_name, "init": init, "init_source": init_source,
            "questions": len({r['qid'] for r in rows}), "rows": len(rows), "f4_bonus": spec.get("script_invariant_bonus"),
            "hparams": gp, "prompt": spec.get("prompt", "plain"), "dialect": sampler.opts.dialect,
            "model_revision": model_revision(mspec),          # L2: tokenizer/суурийн HF commit
            "dataset_sha256": rows_sha256(rows), **provenance()}
    (out / "t2s_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"adapter": str(out), **meta}
