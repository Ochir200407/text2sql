"""Probe-ийн шинж: D2 pipeline-ийн prompt-оор SQL загварын (Arctic-Text2SQL-R1-7B + D2 LoRA) далд төлөв (Kaggle T4/L4).

Үе шат 1 (prompt, GPU-гүй байж болно): Pipeline.prompt_for — үнэлгээний ЦОРЫН ГАНЦ prompt-ын эх сурвалж (D3) — run-ий
  тохиргоогоор (анхдагч M1a = I3 + MADLAD M1 кэш m1_madlad_improved, variant mn; few-shot-оос өөрийн family хасагдана),
  chat template + Arctic prefill → <out>/prompts.jsonl. PG, LLM сервер шаардахгүй (executor/LLM-ийн оронд хуурмаг).
Үе шат 2 (загвар): нэгтгэсэн D2 жин (serve_d2_merged-ийн adapters/d2__arctic-r1-7b__merged16) эсвэл суурь + LoRA-г
  bitsandbytes 4-bit-ээр, batch 1, үүсгэлтгүй НЭГ forward (prompt ~20k токен; T4-д SDPA-ийн GQA засвар заавал): давхарга бүрийн (16, 20, 24 / 28) гаралтын дундаж ба
  сүүлийн токен (hook дотор шууд багасгана — бүх hidden_states-ийг хадгалахгүй), lm_head-ийг ЗӨВХӨН сүүлийн
  токенд (152k толийн logits-ийг 8k токенд гаргахгүй). Сонголтоор (--gen-tokens G) эхний G greedy токены энтропи (KV cache).
  Асуулт бүрийн үр дүн <out>/parts/<id>.npz → тасарвал дахин ажиллуулахад дууссаныг алгасна (resumable).
Үе шат 3: parts → <out>/L<i>_mean.npy, L<i>_last.npy, logit.npy (float16) + ids.json.

dev ЗӨВХӨН; test нь --final-test-тэй (нэг удаа, m0_translate.py-тэй ижил хамгаалалт). Сургалт (probe.py) test-ийн шинжийг
татгалзана.

Жишээ (Kaggle, run_plan_l4.sh STAGE=mt MT_STEPS=probe — docs/PROBE_PLAN.md):
  T2S_EMBED_DEVICE=cpu python scripts/probe/extract_hidden.py --run M1a --var best_a=A7 \
      --model-path adapters/d2__arctic-r1-7b__merged16 --out build/probe/m1a_dev
  python scripts/probe/extract_hidden.py --source knowledge --run M1a --var best_a=A7 \
      --model-path adapters/d2__arctic-r1-7b__merged16 --out build/probe/m1a_knowledge
"""

from __future__ import annotations

import argparse
import gc
import hashlib
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")   # T4: хуваагдсан санах ойгоос OOM

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import numpy as np  # noqa: E402

LOGIT_COLS = ["ent0", "p_top1", "margin12"]           # prefill-ийн дараах эхний токен
GEN_COLS = ["gen_ent_mean", "gen_ent_max", "gen_ptop_min"]   # --gen-tokens > 0 үед


def safe_name(item_id: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in item_id) + "_" + \
        hashlib.sha1(item_id.encode("utf-8")).hexdigest()[:6]


def read_jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


# ---------------------------------------------------------------- асуултын эх
def load_source(source: str, split: str, final_test: bool) -> list[dict]:
    """{id, family, category, question_mn, question_en}. test-ийг зөвхөн --final-test-тэй (category хадгалахгүй)."""
    if source == "dev":
        if split == "test":
            if not final_test:
                sys.exit("test-д зөвхөн эцэст нэг удаа: --final-test өг (WORKLOG: test нь давтаж хэмжигдэхгүй)")
            rows = read_jsonl(ROOT / "evalset/test.jsonl")
            return [{"id": r["id"], "family": r.get("family"), "category": None, "question_mn": r["question_mn"],
                     "question_en": None} for r in rows]
        rows = read_jsonl(ROOT / "evalset/dev.jsonl")
    elif source == "knowledge":
        rows = read_jsonl(ROOT / "knowledge/examples.jsonl")      # 160 жишээ (бүгд answerable)
    elif source.startswith("jsonl:"):
        rows = read_jsonl(Path(source[6:]))                       # синтетик: id, question_mn, category[, family, question_en]
        if "evalset/test" in source.replace("\\", "/"):
            sys.exit("test-ийг jsonl: замаар оруулахгүй")
    else:
        sys.exit(f"--source {source!r}: dev | knowledge | jsonl:<зам>")
    return [{"id": r["id"], "family": r.get("family"), "category": r.get("category"), "question_mn": r["question_mn"],
             "question_en": r.get("question_en")} for r in rows]


# ---------------------------------------------------------------- үе шат 1: prompt
class _NoDB:
    """prompt угсрахад DB хэрэггүй — санамсаргүй дуудвал шууд алдаа."""

    def __getattr__(self, name):
        raise RuntimeError(f"extract_hidden: prompt угсрахад executor.{name} дуудагдав (DB-гүй горим)")


def build_prompts(args, items: list[dict], out: Path) -> list[dict]:
    """prompts.jsonl (кэш): {id, text, n_chars, sha}. Аль хэдийн байгаа id-г дахин угсрахгүй."""
    path = out / "prompts.jsonl"
    done = {r["id"]: r for r in read_jsonl(path)} if path.exists() else {}
    todo = [it for it in items if it["id"] not in done]
    if not todo:
        return [done[it["id"]] for it in items]
    from transformers import AutoTokenizer

    from mn_text2sql.config import load_config, load_models, resolve_path
    from mn_text2sql.eval.runner import _check_cache_coverage, _load_cache
    from mn_text2sql.llm.client import MockLLM
    from mn_text2sql.pipeline.options import resolve_run
    from mn_text2sql.pipeline.orchestrator import Pipeline

    var_over = dict(v.split("=", 1) for v in args.var)
    opts = resolve_run(args.run, var_overrides=var_over or None)
    if opts.knowledge_select:
        sys.exit(f"{args.run}: knowledge_select нь prompt угсрахад LLM дуудна — энэ скрипт дэмжихгүй")
    cfg = load_config()
    registry = load_models()
    translations = {}
    if opts.translate:
        name = args.translation_cache or opts.translation_cache or registry.get("translator")
        translations = _load_cache(resolve_path("build/translations") / f"{name}.jsonl")
        if args.source == "dev":
            class _It:   # _check_cache_coverage-д хэрэгтэй хэсэг л
                def __init__(self, d):
                    self.id, self._q = d["id"], d["question_mn"]

                def question(self, v):
                    return self._q if v == "mn" else ""
            _check_cache_coverage([_It(d) for d in todo], ["mn"], translations, "A4 орчуулга", "translate")
        else:   # knowledge/синтетик: орчуулгын кэшгүй — мөрийн question_en-ийг (sampling.py-ийн D3 жортой ижил)
            for d in todo:
                if d.get("question_en"):
                    translations[f"{d['id']}:mn"] = d["question_en"]
    print(f"[prompt] run={args.run} орчуулга={len(translations)} асуулт={len(todo)}", flush=True)
    pipe = Pipeline(cfg, opts, args.model_name, MockLLM(lambda m: ""), executor=_NoDB(), translations=translations)
    pipe.item_families = {d["id"]: d["family"] for d in items if d.get("family")}
    tok = AutoTokenizer.from_pretrained(args.tokenizer or args.model_path or args.base, trust_remote_code=True)
    with open(path, "a", encoding="utf-8", newline="\n") as fh:
        for i, d in enumerate(todo):
            msgs, prefill, ctx, info, shown = pipe.prompt_for(d["question_mn"], "mn", d["id"])
            # vLLM-ийн continue_final_message-тэй ижил: template (+ generation prompt) + prefill (training/formats.py)
            text = tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True) + (prefill or "")
            rec = {"id": d["id"], "text": text, "n_chars": len(text),
                   "sha": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], "shown_question": shown,
                   "examples": info.get("examples"), "n_tables": info.get("n_tables")}
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            done[d["id"]] = rec
            if (i + 1) % 25 == 0:
                print(f"  prompt {i + 1}/{len(todo)}", flush=True)
    del pipe
    try:   # BGE-M3 embedder-ийг санах ойгоос чөлөөлнө (үе шат 2-оос өмнө)
        from mn_text2sql.retrieval import retriever as _r
        _r._cached.cache_clear()
    except Exception:  # noqa: BLE001
        pass
    gc.collect()
    return [done[it["id"]] for it in items]


# ---------------------------------------------------------------- үе шат 2: forward
class _Stop(Exception):
    """--no-logit: хэрэгтэй давхаргын дараа forward-ыг тасална."""


class Extractor:
    def __init__(self, args):
        import torch
        from transformers import AutoTokenizer

        from mn_text2sql.training.sft import load_base_model, t4_sdpa_fix
        self.torch = torch
        cap = torch.cuda.get_device_capability()[0] if torch.cuda.is_available() else 0
        dtype = {"fp16": torch.float16, "bf16": torch.bfloat16}.get(args.dtype) or \
            (torch.bfloat16 if cap >= 8 else torch.float16)          # T4 (sm75) bf16-гүй
        if t4_sdpa_fix():
            print("[T4] SDPA GQA засвар (memory-efficient kernel)", flush=True)
        src = args.model_path or args.base
        self.tok = AutoTokenizer.from_pretrained(args.tokenizer or src, trust_remote_code=True)
        model = load_base_model(src, dtype=dtype, quantize_4bit=not args.no_4bit, device_map={"": 0}).eval()
        if args.adapter:   # нэгтгээгүй бол суурь + D2 LoRA (PEFT); нэгтгэсэн жин (--model-path) нь vLLM-тэй ижил
            from peft import PeftModel
            model = PeftModel.from_pretrained(model, args.adapter).eval()
        self.model = model
        core = model.get_base_model() if hasattr(model, "get_base_model") else model
        self.body, self.lm_head = core.model, core.lm_head
        n_layers = len(self.body.layers)
        self.layers = [int(x) for x in args.layers.split(",")]
        bad = [L for L in self.layers if not 1 <= L <= n_layers]
        if bad:
            sys.exit(f"--layers {bad}: 1…{n_layers} (L = hidden_states[L] = L-р decoder давхаргын гаралт)")
        self.max_len, self.gen = args.max_len, (0 if args.no_logit else args.gen_tokens)
        self.no_logit, self.top = args.no_logit, max(self.layers)
        self.dtype = str(dtype).replace("torch.", "")
        self._cap: dict[int, tuple] = {}
        self._on = False
        for L in self.layers:
            self.body.layers[L - 1].register_forward_hook(self._hook(L))
        print(f"[загвар] {src} {'4-bit' if not args.no_4bit else self.dtype} давхарга {n_layers}, "
              f"GPU {torch.cuda.memory_allocated() / 2**30:.2f} GiB", flush=True)

    def _hook(self, L: int):
        def fn(_mod, _inp, out):
            if not self._on:
                return
            h = out[0] if isinstance(out, tuple) else out          # [1, T, H]
            self._cap[L] = (h[0].float().mean(0).cpu().numpy(), h[0, -1].float().cpu().numpy())
            if self.no_logit and L == self.top:
                raise _Stop
        return fn

    @staticmethod
    def _stats(logits) -> tuple[float, float, float]:
        lp = logits.float().log_softmax(-1)
        p = lp.exp()
        ent = float(-(p * lp).sum())
        top = p.topk(2).values
        return ent, float(top[0]), float(top[0] - top[1])

    def run(self, text: str) -> dict:
        torch = self.torch
        ids = self.tok(text, return_tensors="pt", add_special_tokens=False)["input_ids"]
        n = ids.shape[1]
        truncated = n > self.max_len
        if truncated:   # асуулт ба заавар prompt-ын төгсгөлд (Arctic Figure C.1) — зүүн талаас (схемээс) тасална
            ids = ids[:, -self.max_len:]
        ids = ids.to(self.body.device if hasattr(self.body, "device") else "cuda")
        self._cap.clear()
        with torch.inference_mode():
            self._on = True
            try:
                out = self.body(input_ids=ids, use_cache=self.gen > 0)
            except _Stop:
                out = None
            finally:
                self._on = False
            if out is None:
                res = {"n_tokens": int(n), "truncated": bool(truncated)}
                for L in self.layers:
                    res[f"L{L}_mean"], res[f"L{L}_last"] = self._cap[L]
                return res
            logits = self.lm_head(out.last_hidden_state[:, -1])[0]
            feats = list(self._stats(logits))
            if self.gen > 0:
                pkv, ents, ptops = out.past_key_values, [], []
                nxt = logits.argmax(-1).view(1, 1)
                for _ in range(self.gen):
                    o = self.body(input_ids=nxt, past_key_values=pkv, use_cache=True)
                    pkv = o.past_key_values
                    lg = self.lm_head(o.last_hidden_state[:, -1])[0]
                    e, pt, _ = self._stats(lg)
                    ents.append(e)
                    ptops.append(pt)
                    nxt = lg.argmax(-1).view(1, 1)
                feats += [float(np.mean(ents)), float(np.max(ents)), float(np.min(ptops))]
                del pkv
            del out
        res = {"n_tokens": int(n), "truncated": bool(truncated), "logit": np.array(feats, np.float32)}
        for L in self.layers:
            res[f"L{L}_mean"], res[f"L{L}_last"] = self._cap[L]
        return res


# ---------------------------------------------------------------- үе шат 3: нэгтгэх (numpy л — тестлэгдэнэ)
def consolidate(out: Path, items: list[dict], meta: dict) -> dict:
    """parts/<id>.npz → <блок>.npy (float16, items-ийн дараалал) + ids.json. Дутуу part байвал ids-д орохгүй."""
    parts = out / "parts"
    have, blocks, extra = [], {}, []
    for it in items:
        p = parts / f"{safe_name(it['id'])}.npz"
        if not p.exists():
            continue
        z = np.load(p)
        for k in z.files:
            if k in ("n_tokens", "truncated"):
                continue
            blocks.setdefault(k, []).append(z[k].astype(np.float32))
        have.append(it)
        extra.append({"n_tokens": int(z["n_tokens"]), "truncated": bool(z["truncated"])})
    for k in list(blocks):
        if len(blocks[k]) != len(have):   # жишээ нь --no-logit-тэй/гүй part холилдсон — бүрэн биш блокийг хадгалахгүй
            print(f"[анхаар] {k}: {len(blocks[k])}/{len(have)} part-д л байна — алгасав", flush=True)
            del blocks[k]
    for k, v in blocks.items():
        a = np.stack(v)
        a = np.clip(a, -65000, 65000).astype(np.float16)            # fp16 overflow-оос сэргийлнэ
        np.save(out / f"{k}.npy", a)
    split_test = meta.get("split") == "test"
    ids = {"ids": [it["id"] for it in have],
           "items": [{"id": it["id"], "family": it.get("family"),
                      **({} if split_test else {"category": it.get("category")}), **e}
                     for it, e in zip(have, extra, strict=False)],
           "blocks": sorted(blocks), **meta}
    (out / "ids.json").write_text(json.dumps(ids, ensure_ascii=False, indent=1), encoding="utf-8")
    return {"n": len(have), "missing": len(items) - len(have), "blocks": sorted(blocks)}


def check_against_run(prompts: list[dict], n_tokens: dict[str, int], run_file: Path) -> None:
    """Prompt-ын давхцал: run-ий бичлэгийн prompt_tokens (vLLM) ↔ энд токенчилсон урт. Ялгаа том бол prompt зөрсөн."""
    rec = {r["question_id"]: r.get("prompt_tokens") for r in read_jsonl(run_file)}
    diffs = [n_tokens[p["id"]] - rec[p["id"]] for p in prompts
             if p["id"] in n_tokens and isinstance(rec.get(p["id"]), int) and rec[p["id"]] > 0]
    if not diffs:
        print("[шалгалт] run-д prompt_tokens алга — алгасав", flush=True)
        return
    d = np.abs(np.array(diffs))
    print(f"[шалгалт] {len(d)} асуулт: |Δтокен| медиан {np.median(d):.0f}, макс {d.max():.0f}, "
          f"≤5 нь {(d <= 5).mean():.0%} (олон дуудлагын run-д prompt_tokens нийлбэр байж болно)", flush=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", default="M1a", help="experiments.yaml-ийн run (prompt-ын тохиргоо)")
    ap.add_argument("--var", action="append", default=[], help="run-ий хувьсагч, жишээ best_a=A7 (run_plan-ийн mt)")
    ap.add_argument("--translation-cache", default="", help="анхдагч: run-ийх (M1a → m1_madlad_improved)")
    ap.add_argument("--model-name", default="arctic-r1-7b", help="models.yaml-ийн нэр (prompt-ын family)")
    ap.add_argument("--model-path", default="", help="нэгтгэсэн D2 жин (adapters/d2__arctic-r1-7b__merged16)")
    ap.add_argument("--base", default="Snowflake/Arctic-Text2SQL-R1-7B", help="--model-path-гүй үед суурь")
    ap.add_argument("--adapter", default="", help="--base-тэй хамт: D2 LoRA хавтас (adapters/d2__arctic-r1-7b)")
    ap.add_argument("--tokenizer", default="")
    ap.add_argument("--source", default="dev", help="dev | knowledge | jsonl:<зам> (синтетик)")
    ap.add_argument("--split", default="dev", choices=["dev", "test"])
    ap.add_argument("--final-test", action="store_true", help="test-д нэг удаа хүрэх зөвшөөрөл")
    ap.add_argument("--out", default="")
    ap.add_argument("--layers", default="16,20,24")
    ap.add_argument("--max-len", type=int, default=24576,
                    help="токен (models.yaml max_model_len); M1a/H4-ийн prompt ~20k (медиан 20.2k, макс 21k) — "
                         "үүнээс урт бол зүүнээс тасалж ids.json-д truncated=true")
    ap.add_argument("--gen-tokens", type=int, default=0, help="эхний G greedy токены энтропи (0 = зөвхөн нэг forward)")
    ap.add_argument("--no-logit", action="store_true",
                    help="logit шинжгүй: хамгийн дээд --layers давхаргын дараа forward-ыг зогсооно (~15%% хурдан)")
    ap.add_argument("--dtype", default="auto", choices=["auto", "fp16", "bf16"])
    ap.add_argument("--no-4bit", action="store_true", help="L4/A100-д fp16/bf16 бүтэн жин")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--prompts-only", action="store_true", help="зөвхөн үе шат 1 (загвар ачаалахгүй)")
    ap.add_argument("--consolidate-only", action="store_true")
    ap.add_argument("--check-run", default="", help="run-ий jsonl (prompt_tokens-оор prompt-ын давхцал шалгах)")
    args = ap.parse_args(argv)
    if args.split == "test" and not args.final_test:
        sys.exit("test-д зөвхөн эцэст нэг удаа: --final-test өг (WORKLOG: test нь давтаж хэмжигдэхгүй)")
    if args.split == "test" and args.source != "dev":
        sys.exit("--split test нь --source dev-тэй л")
    if not (args.model_path or args.base):
        sys.exit("--model-path эсвэл --base")

    tag = args.source.split(":")[0] if args.source != "dev" else args.split
    out = Path(args.out or ROOT / "build/probe" / f"{args.run.lower()}_{tag}")
    (out / "parts").mkdir(parents=True, exist_ok=True)
    items = load_source(args.source, args.split, args.final_test)
    if args.limit:
        items = items[:args.limit]
    meta = {"run": args.run, "vars": args.var, "source": args.source, "split": args.split,
            "translation_cache": args.translation_cache or None, "model_path": args.model_path or None,
            "base": None if args.model_path else args.base, "adapter": args.adapter or None,
            "layers": [int(x) for x in args.layers.split(",")], "max_len": args.max_len,
            "gen_tokens": 0 if args.no_logit else args.gen_tokens,
            "logit_cols": [] if args.no_logit else LOGIT_COLS + (GEN_COLS if args.gen_tokens else []),
            "pools": ["mean", "last"], "layer_convention": "L = hidden_states[L] = L-р decoder давхаргын гаралт"}
    if args.consolidate_only:
        print(json.dumps(consolidate(out, items, meta), ensure_ascii=False))
        return 0

    prompts = build_prompts(args, items, out)
    print(f"[prompt] {len(prompts)} бэлэн → {out / 'prompts.jsonl'}", flush=True)
    if args.prompts_only:
        return 0

    todo = [p for p in prompts if not (out / "parts" / f"{safe_name(p['id'])}.npz").exists()]
    print(f"[forward] {len(prompts) - len(todo)} дууссан, {len(todo)} үлдсэн", flush=True)
    n_tok: dict[str, int] = {}
    if todo:
        ex = Extractor(args)
        meta["dtype"] = ex.dtype
        t0 = time.time()
        for i, p in enumerate(todo):
            try:
                res = ex.run(p["text"])
            except ex.torch.OutOfMemoryError:
                ex.torch.cuda.empty_cache()
                print(f"  OOM {p['id']} — max_len/2-оор дахин", flush=True)
                ml, ex.max_len = ex.max_len, ex.max_len // 2
                res = ex.run(p["text"])
                ex.max_len = ml
            tmp = out / "parts" / f"{safe_name(p['id'])}.tmp.npz"
            np.savez(tmp, **{k: (np.asarray(v, np.float16) if k not in ("n_tokens", "truncated") else np.asarray(v))
                             for k, v in res.items()})
            tmp.replace(out / "parts" / f"{safe_name(p['id'])}.npz")   # атомар — тасарсан part үлдэхгүй
            n_tok[p["id"]] = res["n_tokens"]
            if (i + 1) % 10 == 0 or i + 1 == len(todo):
                el = time.time() - t0
                print(f"  {i + 1}/{len(todo)}  {el / (i + 1):.1f} с/асуулт, үлдсэн ~{el / (i + 1) * (len(todo) - i - 1) / 60:.0f} мин"
                      f"  (токен {res['n_tokens']}{', ТАСАЛСАН' if res['truncated'] else ''}; "
                      f"GPU оргил {ex.torch.cuda.max_memory_allocated() / 2**30:.1f} GiB)", flush=True)
        del ex
        gc.collect()
    if args.check_run and n_tok:
        check_against_run(prompts, n_tok, Path(args.check_run))
    print(json.dumps(consolidate(out, items, meta), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
