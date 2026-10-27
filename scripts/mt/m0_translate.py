"""M0 bake-off: нэр дэвшигч орчуулагчийг (zero-shot) dev асуулт дээр ажиллуулж `build/translations/<tag>.jsonl` (одоогийн кэшийн формат)
болгон хадгална. Дараа нь `python scripts/mt/baseline_dev.py --cache build/translations/<tag>.jsonl` (chrF++, нэр томьёо, утга, тоо) ба
Text2SQL (`translation_cache: <tag>`)-ээр хэмжинэ. docs/MT_INVARIANT_PLAN.md §5.2.

Backend:
  mock         — тест (эх текстийг буцаана)
  openai       — vLLM-ийн OpenAI-compatible endpoint (--base-url, --model); Qwen3.5-9B гэх мэт
  hf_causal    — transformers chat загвар (Qwen3.5-9B, Gemma): --load-4bit; T4/Colab-д --cudnn-off (WORKLOG §6.13)
  hf_seq2seq   — MADLAD-400 гэх мэт T5 орчуулгын загвар (`<2en> текст`)

Латин оролт: --translit improved = `Transliterator(improved=True)`-аар кирилл болгоод орчуул (латин замын (b) сонголт, §5.2); `none` = латиныг шууд орчуулна (a).
--hints lexicon = translate_terms.term_hints() (кирилл эхээс; латин бол хөрвүүлсэн кириллээс).
dev ЗӨВХӨН; test нь --final-test-тэй (нэг удаа). Ажиллуулсан тохиргоо `<tag>.meta.json`-д.

Жишээ (Colab/Kaggle):
  python scripts/mt/m0_translate.py --backend hf_seq2seq --model google/madlad400-3b-mt --tag m0_madlad --translit none
  python scripts/mt/m0_translate.py --backend hf_causal --model Qwen/Qwen3.5-9B --load-4bit --cudnn-off --hints lexicon --tag m0_qwen9b
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")   # T4: хуваагдсан санах ойгоос OOM

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from translit_eval import vocabulary  # noqa: E402

from mn_text2sql.pipeline.translate import clean_output, find_value_spans, filter_spans, translate_messages, unmark  # noqa: E402
from mn_text2sql.pipeline.translate_terms import term_hints  # noqa: E402
from mn_text2sql.text.normalize import Transliterator  # noqa: E402


def read_jsonl(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


class Mock:
    def translate(self, jobs: list[dict]) -> list[str]:
        return ["[mock] " + j["sent"] for j in jobs]


class OpenAIBackend:
    def __init__(self, base_url: str, model: str, no_thinking: bool):
        from mn_text2sql.llm.client import OpenAICompatLLM
        extra = {"chat_template_kwargs": {"enable_thinking": False}} if no_thinking else None
        self.llm = OpenAICompatLLM(base_url, model, extra_body=extra)

    def translate(self, jobs: list[dict]) -> list[str]:
        out = []
        for j in jobs:
            r = self.llm.chat(translate_messages(j["sent"], j["hints"] or None), temperature=0.0, max_tokens=256)
            out.append(r.texts[0])
        return out


def sft_messages(j: dict) -> list[dict]:
    """Fine-tune хийсэн загварт сургалтын (render_sft.py) prompt-той яг ижил хэлбэр."""
    from render_sft import HINT_HEAD, SYSTEM
    system = SYSTEM.format(direction=j["direction"]) + (HINT_HEAD + "\n".join(f"- {h}" for h in j["hints"]) if j["hints"] else "")
    return [{"role": "system", "content": system}, {"role": "user", "content": j["sent"]}]


class HFCausal:
    def __init__(self, model: str, load_4bit: bool, cudnn_off: bool, batch: int, adapter: str = "", sft_prompt: bool = False):
        import torch
        from transformers import AutoTokenizer

        from mn_text2sql.training.sft import load_base_model
        if cudnn_off:
            torch.backends.cudnn.enabled = False
        self.torch = torch
        self.tok = AutoTokenizer.from_pretrained(model, trust_remote_code=True, padding_side="left")
        if self.tok.pad_token is None:
            self.tok.pad_token = self.tok.eos_token
        self.model = load_base_model(model, dtype=torch.float16, quantize_4bit=load_4bit,
                                     device_map="auto").eval()
        if adapter:
            from peft import PeftModel
            self.model = PeftModel.from_pretrained(self.model, adapter).eval()
        self.batch, self.sft_prompt = batch, sft_prompt

    def _prompt(self, j: dict) -> str:
        msgs = sft_messages(j) if self.sft_prompt else translate_messages(j["sent"], j["hints"] or None)
        try:
            return self.tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True, enable_thinking=False)
        except TypeError:
            return self.tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)

    def translate(self, jobs: list[dict]) -> list[str]:
        out: list[str] = []
        for i in range(0, len(jobs), self.batch):
            chunk = jobs[i:i + self.batch]
            enc = self.tok([self._prompt(j) for j in chunk], return_tensors="pt", padding=True).to(self.model.device)
            with self.torch.no_grad():
                gen = self.model.generate(**enc, max_new_tokens=128, do_sample=False)
            out += self.tok.batch_decode(gen[:, enc["input_ids"].shape[1]:], skip_special_tokens=True)
            print(f"  {min(i + self.batch, len(jobs))}/{len(jobs)}", flush=True)
        return out


class HFSeq2Seq:
    def __init__(self, model: str, batch: int, beams: int, adapter: str = ""):
        import torch
        from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
        self.torch = torch
        # use_fast=False: Kaggle-ийн transformers 5 + protobuf 7 дээр MADLAD-ын fast tokenizer-ийн хөрвүүлэлт сэжигтэй
        # (Version 13: M0 chrF++ 1.8) — sentencepiece-ийн жишиг (slow) tokenizer-ийг ашиглана
        self.tok = AutoTokenizer.from_pretrained(model, use_fast=False)
        # fp32: T5 (MADLAD) fp16-д overflow хийж хоосон/хог гаргадаг; T4-д bf16 алга. 3B × 4 байт ≈ 12 GB — T4-д багтана
        # adapter (M1): сургалттай ижил NF4 суурь (QLoRA) — fp32 + adapter T4-д batch 1-д ч OOM болсон (Version 16–17)
        q = None
        if adapter:
            from transformers import BitsAndBytesConfig
            q = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_use_double_quant=True,
                                   bnb_4bit_compute_dtype=torch.float32)
        self.model = AutoModelForSeq2SeqLM.from_pretrained(model, torch_dtype=torch.float32, quantization_config=q,
                                                           device_map={"": 0}).eval()
        from madlad_fix import fix_embeddings   # transformers 5: embedding-ийн tie (Version 13–14-ийн хог гаралт)
        fix_embeddings(self.model, self.tok)
        if adapter:
            from peft import PeftModel
            self.model = PeftModel.from_pretrained(self.model, adapter).eval()
        print(f"  [GPU] {torch.cuda.memory_allocated() / 2**30:.2f} GiB ачаалагдсан", flush=True)
        self.batch, self.beams = batch, beams

    def _generate(self, srcs: list[str]) -> list[str]:
        """OOM бол багцыг хувааж дахин оролдоно (урт hint-тэй багц T4-д багтахгүй байж болно)."""
        try:
            enc = self.tok(srcs, return_tensors="pt", padding=True).to(self.model.device)
            with self.torch.no_grad():
                gen = self.model.generate(**enc, max_new_tokens=128, num_beams=self.beams)
            return self.tok.batch_decode(gen, skip_special_tokens=True)
        except self.torch.OutOfMemoryError:
            if len(srcs) == 1:
                raise
            self.torch.cuda.empty_cache()
            h = len(srcs) // 2
            return self._generate(srcs[:h]) + self._generate(srcs[h:])

    def translate(self, jobs: list[dict]) -> list[str]:
        out: list[str] = []
        for i in range(0, len(jobs), self.batch):
            chunk = jobs[i:i + self.batch]
            srcs = ["<2en> " + (f"[T: {'; '.join(j['hints'])}] " if j["hints"] else "") + j["sent"] for j in chunk]
            out += self._generate(srcs)
            if i == 0:   # оношлох: эхний 3 орчуулгыг логт харуулна
                for s_, o_ in list(zip(srcs, out))[:3]:
                    print(f"  [жишээ] {s_[:120]!r} → {o_[:120]!r}", flush=True)
            print(f"  {min(i + self.batch, len(jobs))}/{len(jobs)}", flush=True)
        return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", required=True, choices=["mock", "openai", "hf_causal", "hf_seq2seq"])
    ap.add_argument("--model", default="")
    ap.add_argument("--base-url", default="http://127.0.0.1:8000/v1")
    ap.add_argument("--tag", required=True)
    ap.add_argument("--split", default="dev", choices=["dev", "test"])
    ap.add_argument("--final-test", action="store_true", help="test-д нэг удаа хүрэх зөвшөөрөл")
    ap.add_argument("--variants", default="mn,latin")
    ap.add_argument("--hints", default="none", choices=["none", "lexicon"])
    ap.add_argument("--translit", default="none", choices=["none", "improved"])
    ap.add_argument("--mark-values", default="none", choices=["none", "db"],
                    help="db: DB-ийн оноосон нэр/код/огноог «каноник» болгож тэмдэглээд (M1-ийн сургалтын формат, prepare_v2) "
                         "орчуулсны дараа \"утга\" болгоно (unmark); PG хэрэгтэй (Kaggle bootstrap)")
    ap.add_argument("--load-4bit", action="store_true")
    ap.add_argument("--cudnn-off", action="store_true")
    ap.add_argument("--no-thinking", action="store_true", help="openai: Qwen3.5-ийн thinking-ийг унтраана")
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--beams", type=int, default=4)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--adapter", default="", help="train_mt.py-ийн LoRA хавтас (M1)")
    ap.add_argument("--prompt", default="pipeline", choices=["pipeline", "sft"],
                    help="hf_causal: pipeline = translate_messages (zero-shot), sft = render_sft-ийн формат (fine-tune хийсэн загвар)")
    args = ap.parse_args()
    if args.split == "test" and not args.final_test:
        sys.exit("test-д зөвхөн эцэст нэг удаа: --final-test өг (WORKLOG: test нь давтаж хэмжигдэхгүй)")

    items = read_jsonl(ROOT / f"evalset/{args.split}.jsonl")
    if args.limit:
        items = items[:args.limit]
    translit = Transliterator(vocabulary(), improved=True) if args.translit == "improved" else None
    hint_translit = translit or (Transliterator(vocabulary(), improved=True) if args.hints == "lexicon" else None)
    keys = None
    if args.mark_values == "db":
        from mn_text2sql.config import load_config
        from mn_text2sql.db.conn import bot_dsn
        from mn_text2sql.db.executor import Executor
        from mn_text2sql.pipeline.translate import ENTITY_COLUMNS
        from mn_text2sql.pipeline.translate_fix import entity_keys
        keys = entity_keys(Executor(bot_dsn(load_config()), statement_timeout_ms=20000, max_rows=100000), ENTITY_COLUMNS)
        if not keys:
            sys.exit("--mark-values db: DB-ийн оноосон нэр олдсонгүй (PG ажиллаж байна уу?)")
        print(f"  [утга] DB-ийн оноосон нэр {len(keys)}", flush=True)
    jobs = []
    for d in items:
        for v in args.variants.split(","):
            src = d["question_mn"] if v == "mn" else d.get("question_latin")
            if not src:
                continue
            sent = translit.sentence(src) if (v == "latin" and translit is not None) else src
            # сургалтад (render_sft) латин мөрийн hint-ийг кирилл эхээс гаргадаг — inference-д кирилл рүү хөрвүүлсэн хэлбэрээс
            # hint_translit нь зөвхөн --hints lexicon үед бий (hint хэрэггүй бол cyr ашиглагдахгүй)
            cyr = sent if (v != "latin" or translit is not None) else (hint_translit.sentence(src) if hint_translit else src)
            hints = term_hints(cyr) if args.hints == "lexicon" else []
            direction = "mn(latin)→en" if (v == "latin" and translit is None) else "mn(cyrillic)→en"
            mapping: dict[str, str] = {}
            if keys is not None:   # prepare_v2-тэй ижил: span илрүүлэх → DB-ээр шүүх → «каноник»
                spans, canon = filter_spans(find_value_spans(sent), sent, keys)
                for sp in spans:
                    val = canon.get(sp, sp)
                    quoted = next((f"{o}{sp}{c}" for o, c in ("«»", '""', "“”", "''") if f"{o}{sp}{c}" in sent), None)
                    if quoted or sp in sent:   # ишлэлтэй бол ишлэлийг нь «» болгоно (давхар тэмдэглэхгүй)
                        sent = sent.replace(quoted or sp, f"«{val}»")
                        mapping[f"«{val}»"] = val
            jobs.append({"id": d["id"], "variant": v, "source": src, "sent": sent, "hints": hints, "direction": direction,
                         "mapping": mapping})

    be = {"mock": lambda: Mock(), "openai": lambda: OpenAIBackend(args.base_url, args.model, args.no_thinking),
          "hf_causal": lambda: HFCausal(args.model, args.load_4bit, args.cudnn_off, args.batch, args.adapter,
                                        args.prompt == "sft"),
          "hf_seq2seq": lambda: HFSeq2Seq(args.model, args.batch, args.beams, args.adapter)}[args.backend]()
    t0 = time.time()
    raws = be.translate(jobs)
    out = ROOT / "build/translations" / f"{args.tag}.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        for j, raw in zip(jobs, raws):
            text = clean_output(raw) if args.backend != "hf_seq2seq" else raw.strip()
            ok = True
            if j["mapping"]:   # «утга» → "утга"; хуулаагүй утгыг төгсгөлд нэмнэ
                text, ok = unmark(text, j["mapping"])
            rec = {"id": j["id"], "variant": j["variant"], "source": j["source"], "masked": j["sent"], "spans": j["mapping"],
                   "raw": raw, "text": text, "ok": ok, "hints": j["hints"]}
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    meta = {"args": vars(args), "n": len(jobs), "seconds": round(time.time() - t0, 1)}
    (out.with_suffix(".meta.json")).write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{len(jobs)} орчуулга → {out} ({meta['seconds']} с). Дараа нь:\n  python scripts/mt/baseline_dev.py --cache build/translations/{args.tag}.jsonl")


if __name__ == "__main__":
    main()
