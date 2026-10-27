"""transformers + bitsandbytes 4-bit загварыг OpenAI-тэй нийцтэй API-аар үйлчлэх жижиг сервер (вэб, T4).

    python scripts/server/hf_openai_server.py --model <hf_id эсвэл хавтас> --served d2 --port 8000

vLLM-ийн шинэ хувилбар bitsandbytes-ийг дэмжихээ больсон тул нэг T4 (15 GB)-д 7B + 9B хоёр загварыг зэрэг багтаах
энэ замыг ашиглана. /v1/models, /v1/chat/completions (n, temperature, top_p, max_tokens, stop, continue_final_message,
add_generation_prompt, chat_template_kwargs). Нэг GPU тул хүсэлтийг дараалуулна (lock).
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

import torch
import uvicorn
from fastapi import FastAPI, Request
from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
# Colab: vLLM-ийн суулгасан torch ба системийн cuDNN зөрчилдөж Qwen3.5-ийн conv1d «SUBLIBRARY_VERSION_MISMATCH» өгдөг
torch.backends.cudnn.enabled = False


def load(model_id: str):
    try:
        from mn_text2sql.training.sft import t4_sdpa_fix  # T4: GQA → math kernel (L²) OOM-оос сэргийлнэ
        t4_sdpa_fix()
    except Exception as e:  # pragma: no cover
        print("t4_sdpa_fix алгасав:", e)
    q = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4", bnb_4bit_compute_dtype=torch.float16,
                           bnb_4bit_use_double_quant=True)
    kw = dict(quantization_config=q, torch_dtype=torch.float16, device_map={"": 0}, attn_implementation="sdpa")
    tok = AutoTokenizer.from_pretrained(model_id)
    try:
        model = AutoModelForCausalLM.from_pretrained(model_id, **kw)
    except (ValueError, KeyError):
        from transformers import AutoModelForImageTextToText
        model = AutoModelForImageTextToText.from_pretrained(model_id, **kw)
    model.eval()
    return tok, model


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True)
    ap.add_argument("--served", required=True)
    ap.add_argument("--port", type=int, default=8000)
    a = ap.parse_args()
    tok, model = load(a.model)
    print(f"[{a.served}] ачааллаа; GPU санах ой {torch.cuda.memory_allocated() / 1e9:.1f} GB", flush=True)
    lock = threading.Lock()
    app = FastAPI()

    @app.get("/v1/models")
    def models():
        return {"object": "list", "data": [{"id": a.served, "object": "model"}]}

    @app.post("/v1/chat/completions")
    async def chat(req: Request):
        b = await req.json()
        kwargs = dict(b.get("chat_template_kwargs") or {})
        cont = bool(b.get("continue_final_message"))
        prompt = tok.apply_chat_template(b["messages"], tokenize=False, add_generation_prompt=not cont and
                                         b.get("add_generation_prompt", True), continue_final_message=cont, **kwargs)
        ids = tok(prompt, return_tensors="pt", add_special_tokens=False).input_ids.to(model.device)
        temp = float(b.get("temperature") or 0.0)
        gen = dict(max_new_tokens=int(b.get("max_tokens") or 1024), do_sample=temp > 0,
                   pad_token_id=tok.pad_token_id or tok.eos_token_id)
        if temp > 0:
            gen.update(temperature=temp, top_p=float(b.get("top_p") or 1.0))
        stop = b.get("stop")
        if stop:
            gen.update(stop_strings=[stop] if isinstance(stop, str) else list(stop), tokenizer=tok)
        choices, comp = [], 0
        t0 = time.perf_counter()
        with lock, torch.inference_mode():
            for i in range(int(b.get("n") or 1)):
                out = model.generate(ids, **gen)[0, ids.shape[1]:]
                text = tok.decode(out, skip_special_tokens=True)
                comp += len(out)
                choices.append({"index": i, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"})
            torch.cuda.empty_cache()
        print(f"[{a.served}] prompt {ids.shape[1]} → {comp} токен, {time.perf_counter() - t0:.1f} с", flush=True)
        return {"id": "chatcmpl", "object": "chat.completion", "model": a.served, "choices": choices,
                "usage": {"prompt_tokens": int(ids.shape[1]), "completion_tokens": comp}}

    uvicorn.run(app, host="127.0.0.1", port=a.port, log_level="warning")


if __name__ == "__main__":
    main()
