"""MT fine-tune (M1): render_sft.py-ийн SFT мөрөөр орчуулгын загварт LoRA/QLoRA сургана. docs/MT_INVARIANT_PLAN.md §5.5.

Backend:
  seq2seq — MADLAD-400-3B-MT (`{"src": "<2en> [T: …] текст", "tgt": …}`): QLoRA nf4, тооцоолол fp32 (T5-ийн fp16 overflow-оос
            сэргийлнэ; T4-д bf16 алга). LoRA: q, k, v, o, wi_0, wi_1, wo.
  causal  — Qwen3.5-9B гэх мэт chat загвар (`{"messages": [...]}`): мөрийг chat template-ээр {prompt, completion} болгоод
            training/sft.run_sft-ээр (config/training.yaml-ийн `mt_q9b` жор: QLoRA, T4 fp16, checkpoint, хугацааны хязгаар).

Сургалтын дараа val хосоор (pairs.jsonl-ийн split=val; сургалтад ороогүй) chrF++-ийг хэмжиж `<out>/val_report.json`-д бичнэ.
Dev-ийн орчуулгыг `m0_translate.py --adapter <out> --prompt sft`-ээр (сургалтын prompt-той ижил хэлбэр) гаргана.

Жишээ (Kaggle T4):
  python scripts/mt/train_mt.py --backend seq2seq --model google/madlad400-3b-mt --data data/mt/v2/sft/sft_madlad.jsonl \
      --pairs data/mt/v2/pairs.jsonl --out /kaggle/working/mt/madlad_m1
  python scripts/mt/train_mt.py --backend causal --model qwen3.5-9b --data data/mt/v2/sft/sft_qwen.jsonl \
      --pairs data/mt/v2/pairs.jsonl --out /kaggle/working/mt/q9b_m1
Тасарвал ижил командыг дахин ажиллуулахад сүүлийн checkpoint-оос үргэлжилнэ; дууссан бол (`<out>/t2s_meta.json`) алгасна.
"""

from __future__ import annotations

import argparse
import gc
import json
import math
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")   # T4: хуваагдсан санах ойгоос OOM

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from mt_metrics import chrf  # noqa: E402

SEQ2SEQ_TARGETS = ["q", "k", "v", "o", "wi_0", "wi_1", "wo"]


def read_jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def chat_prompt(tok, msgs: list[dict]) -> str:
    try:
        return tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True, enable_thinking=False)
    except TypeError:
        return tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)


def val_jobs(pairs_path: Path, limit: int) -> list[dict]:
    """val хосын кирилл, латин эх → англи (сургалтын формат, hint-гүй)."""
    jobs = []
    for p in read_jsonl(pairs_path):
        if p.get("split") != "val" or p.get("kind", "question") != "question":
            continue
        jobs.append({"id": p["id"], "script": "cyr", "src": p["mn"], "ref": p["en"],
                     "direction": "mn(mixed)→en" if p.get("register") == "R4" else "mn(cyrillic)→en"})
        if p.get("latin"):
            jobs.append({"id": p["id"], "script": "latin", "src": p["latin"], "ref": p["en"], "direction": "mn(latin)→en"})
    return jobs[:limit] if limit else jobs


def report(jobs: list[dict], hyps: list[str], out: Path, seconds: float) -> dict:
    rep: dict = {"n": len(jobs), "seconds": round(seconds, 1)}
    for s in ("cyr", "latin"):
        sel = [(h, j["ref"]) for j, h in zip(jobs, hyps, strict=True) if j["script"] == s]
        if sel:
            rep[f"chrf_{s}"] = round(sum(chrf(h, r) for h, r in sel) / len(sel), 1)
            rep[f"n_{s}"] = len(sel)
    (out / "val_report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    with open(out / "val_translations.jsonl", "w", encoding="utf-8", newline="\n") as fh:
        for j, h in zip(jobs, hyps, strict=True):
            fh.write(json.dumps({**j, "hyp": h}, ensure_ascii=False) + "\n")
    print("val:", json.dumps(rep, ensure_ascii=False))
    return rep


# ---------------------------------------------------------------- seq2seq (MADLAD)
def train_seq2seq(args) -> None:
    import torch
    from datasets import Dataset
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training
    from transformers import (
        AutoModelForSeq2SeqLM,
        AutoTokenizer,
        BitsAndBytesConfig,
        DataCollatorForSeq2Seq,
        Seq2SeqTrainer,
        Seq2SeqTrainingArguments,
    )
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    tok = AutoTokenizer.from_pretrained(args.model, use_fast=False)   # m0_translate-тэй ижил (slow, sentencepiece)
    rows = read_jsonl(Path(args.data))
    done = (out / "t2s_meta.json").exists()
    if not done:
        q = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True,
                               bnb_4bit_compute_dtype=torch.float32)
        model = AutoModelForSeq2SeqLM.from_pretrained(args.model, quantization_config=q, torch_dtype=torch.float32,
                                                      device_map={"": 0})
        from madlad_fix import fix_embeddings   # transformers 5: embedding-ийн tie (m0_translate-тэй ижил)
        emb_choice = fix_embeddings(model, tok)
        model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)
        model = get_peft_model(model, LoraConfig(r=args.r, lora_alpha=2 * args.r, lora_dropout=0.05,
                                                 target_modules=SEQ2SEQ_TARGETS, task_type="SEQ_2_SEQ_LM"))
        model.print_trainable_parameters()

        def enc(b):
            x = tok(b["src"], max_length=args.max_len, truncation=True)
            x["labels"] = tok(text_target=b["tgt"], max_length=args.max_len, truncation=True)["input_ids"]
            return x
        ds = Dataset.from_list([{"src": r["src"], "tgt": r["tgt"]} for r in rows]).map(
            enc, batched=True, remove_columns=["src", "tgt"])
        steps = math.ceil(len(rows) / (args.batch * args.grad_acc)) * args.epochs
        targs = Seq2SeqTrainingArguments(
            output_dir=str(out), num_train_epochs=args.epochs, per_device_train_batch_size=args.batch,
            gradient_accumulation_steps=args.grad_acc, learning_rate=args.lr, lr_scheduler_type="cosine",
            warmup_steps=max(1, int(0.03 * steps)), logging_steps=10, save_strategy="steps", save_steps=args.save_steps,
            save_total_limit=2, report_to="none", seed=42, gradient_checkpointing=True,
            gradient_checkpointing_kwargs={"use_reentrant": False})
        trainer = Seq2SeqTrainer(model=model, args=targs, train_dataset=ds,
                                 data_collator=DataCollatorForSeq2Seq(tok, model=model))
        from mn_text2sql.training.sft import deadline_callback, last_checkpoint
        stopper = None
        if os.environ.get("T2S_TRAIN_DEADLINE"):      # run_sft-тэй ижил: хязгаарт checkpoint хадгалаад зогсоно
            stopper = deadline_callback(float(os.environ["T2S_TRAIN_DEADLINE"]))
            trainer.add_callback(stopper)
        t0 = time.time()
        res = trainer.train(resume_from_checkpoint=last_checkpoint(out))   # бүрэн (trainer_state.json-той) checkpoint
        if stopper is not None and stopper.stopped and trainer.state.global_step < trainer.state.max_steps:
            print(f"хугацааны хязгаар: checkpoint {last_checkpoint(out)} — дараагийн сесст үргэлжилнэ")
            return
        trainer.save_model(str(out))
        tok.save_pretrained(str(out))
        (out / "t2s_meta.json").write_text(json.dumps(
            {"backend": "seq2seq", "base": args.model, "records": len(rows), "train_loss": res.training_loss,
             "embedding": emb_choice,
             "minutes": round((time.time() - t0) / 60, 1), "args": vars(args)}, ensure_ascii=False, indent=2),
            encoding="utf-8")
        for c in out.glob("checkpoint-*"):
            import shutil
            shutil.rmtree(c, ignore_errors=True)
        del trainer, model
        gc.collect()
        torch.cuda.empty_cache()
    if args.pairs and not (out / "val_report.json").exists():
        from peft import PeftModel
        # сургалттай ижил NF4 суурь (fp32 + adapter T4-д OOM — Version 16–17)
        q = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True,
                               bnb_4bit_compute_dtype=torch.float32)
        base = AutoModelForSeq2SeqLM.from_pretrained(args.model, quantization_config=q, torch_dtype=torch.float32,
                                                     device_map={"": 0})
        from madlad_fix import fix_embeddings
        fix_embeddings(base, tok)
        model = PeftModel.from_pretrained(base, str(out)).eval()
        jobs = val_jobs(Path(args.pairs), args.val_limit)
        t0 = time.time()

        def gen(srcs):   # OOM бол багцыг хувааж дахин оролдоно
            try:
                x = tok(srcs, return_tensors="pt", padding=True).to(base.device)
                with torch.no_grad():
                    g = model.generate(**x, max_new_tokens=128, num_beams=4)
                return tok.batch_decode(g, skip_special_tokens=True)
            except torch.OutOfMemoryError:
                if len(srcs) == 1:
                    raise
                torch.cuda.empty_cache()
                return gen(srcs[:len(srcs) // 2]) + gen(srcs[len(srcs) // 2:])
        hyps = []
        for i in range(0, len(jobs), 8):   # T4: fp32 3B + beam 4 — batch 8
            hyps += gen([f"<2en> {j['src']}" for j in jobs[i:i + 8]])
        report(jobs, [h.strip() for h in hyps], out, time.time() - t0)


# ---------------------------------------------------------------- causal (Qwen3.5-9B …)
def train_causal(args) -> None:
    from transformers import AutoTokenizer

    from mn_text2sql.config import load_config, load_models
    from mn_text2sql.training.data import load_training_cfg
    from mn_text2sql.training.sft import run_sft
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    mspec = load_models()["models"][args.model]
    tok = AutoTokenizer.from_pretrained(mspec["hf_id"], trust_remote_code=True)
    data = out / "records.jsonl"
    if not data.exists():
        eos = tok.eos_token or ""
        with open(data, "w", encoding="utf-8", newline="\n") as fh:
            for r in read_jsonl(Path(args.data)):
                m = r["messages"]
                fh.write(json.dumps({"prompt": chat_prompt(tok, m[:-1]), "completion": m[-1]["content"] + eos},
                                    ensure_ascii=False) + "\n")
    res = run_sft(load_config(None), load_training_cfg("config/training.yaml"), args.recipe, args.model,
                  data_file=str(data), output_dir=str(out))
    print(json.dumps(res, ensure_ascii=False, default=str)[:2000])
    if (res.get("stopped") or not args.pairs or int(os.environ.get("RANK") or 0) > 0
            or (out / "val_report.json").exists()):
        return
    gc.collect()
    import torch
    torch.cuda.empty_cache()
    from peft import PeftModel

    from mn_text2sql.training.sft import load_base_model
    torch.backends.cudnn.enabled = False                       # T4 + Qwen3.5: cuDNN гацалт (WORKLOG §6.13)
    tok.padding_side = "left"
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    base = load_base_model(mspec["hf_id"], dtype=torch.float16, quantize_4bit=True, device_map={"": 0})
    model = PeftModel.from_pretrained(base, str(out)).eval()
    from render_sft import SYSTEM
    jobs = val_jobs(Path(args.pairs), args.val_limit)
    t0 = time.time()
    hyps = []
    for i in range(0, len(jobs), 8):
        chunk = jobs[i:i + 8]
        prompts = [chat_prompt(tok, [{"role": "system", "content": SYSTEM.format(direction=j["direction"])},
                                     {"role": "user", "content": j["src"]}]) for j in chunk]
        x = tok(prompts, return_tensors="pt", padding=True).to(base.device)
        with torch.no_grad():
            g = model.generate(**x, max_new_tokens=128, do_sample=False)
        hyps += tok.batch_decode(g[:, x["input_ids"].shape[1]:], skip_special_tokens=True)
    from mn_text2sql.pipeline.translate import clean_output  # dev кэштэй (m0_translate) ижил цэвэрлэгээ
    report(jobs, [clean_output(h) for h in hyps], out, time.time() - t0)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backend", required=True, choices=["seq2seq", "causal"])
    ap.add_argument("--model", required=True, help="seq2seq: HF id; causal: config/models.yaml-ийн нэр (qwen3.5-9b)")
    ap.add_argument("--recipe", default="mt_q9b", help="causal: config/training.yaml-ийн жор")
    ap.add_argument("--data", required=True)
    ap.add_argument("--pairs", default="", help="val хэмжилтэд pairs.jsonl (split=val)")
    ap.add_argument("--out", required=True)
    # доорх 7 нь зөвхөн seq2seq-д; causal нь config/training.yaml-ийн --recipe-ийг ашиглана
    ap.add_argument("--epochs", type=float, default=3)
    ap.add_argument("--lr", type=float, default=3e-4)
    ap.add_argument("--r", type=int, default=32)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--grad-acc", type=int, default=2)
    ap.add_argument("--max-len", type=int, default=256)
    ap.add_argument("--save-steps", type=int, default=100)
    ap.add_argument("--val-limit", type=int, default=0)
    args = ap.parse_args()
    (train_seq2seq if args.backend == "seq2seq" else train_causal)(args)


if __name__ == "__main__":
    main()
