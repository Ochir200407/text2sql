"""Fine-tune-ийн дараах ерөнхий чадварын шалгалт (туршилтын төлөвлөгөө "Хэмжих үзүүлэлт": монгол ерөнхий —
Belebele khk_Cyrl-ийн дэд олонлог). Belebele (CC BY-SA 4.0): уншаад ойлгох, 4 сонголттой асуулт.

Суурь загвар ба adapter-ийг ижил prompt-оор (vLLM OpenAI API) ажиллуулж нарийвчлалыг харьцуулна. Англи dev ба
BIRD-dev дэд олонлогийг eval (variant=en) ба F1 runner-ээр хэмжинэ (docs/TRAINING.md).
"""

from __future__ import annotations

import json
import random
import re
from concurrent.futures import ThreadPoolExecutor

from ..config import Config, load_models, resolve_path
from ..llm.client import make_llm

PROMPT = ("Read the passage and answer the question by choosing A, B, C or D. Reply with the letter only.\n\n"
          "Passage:\n{passage}\n\nQuestion: {question}\nA) {a}\nB) {b}\nC) {c}\nD) {d}\n\nAnswer:")


def belebele_eval(cfg: Config, model: str, n: int = 300, seed: int = 0, base_url: str | None = None,
                  served_name: str | None = None, workers: int = 16) -> dict:
    from datasets import load_dataset
    ds = load_dataset("facebook/belebele", "khk_Cyrl", split="test")
    idx = list(range(len(ds)))
    random.Random(seed).shuffle(idx)
    rows = [ds[i] for i in idx[:n]]
    mspec = load_models()["models"].get(model, {})
    llm_cfg = dict(cfg["llm"], model=served_name or mspec.get("served_name", model))
    if base_url:
        llm_cfg["base_url"] = base_url
    llm = make_llm(llm_cfg)

    def one(r):
        msg = PROMPT.format(passage=r["flores_passage"], question=r["question"], a=r["mc_answer1"], b=r["mc_answer2"],
                            c=r["mc_answer3"], d=r["mc_answer4"])
        out = llm.chat([{"role": "user", "content": msg}], temperature=0.0, max_tokens=512).texts[0]
        out = out.split("</think>")[-1]
        m = re.search(r"\b([ABCD])\b", out.strip())
        pred = "ABCD".index(m.group(1)) + 1 if m else None
        return pred == int(r["correct_answer_num"])

    with ThreadPoolExecutor(max_workers=workers) as pool:
        correct = list(pool.map(one, rows))
    acc = round(100.0 * sum(correct) / len(correct), 2)
    out = resolve_path(cfg["eval"]["results_dir"]) / "general" / f"belebele__{served_name or model}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    res = {"model": model, "served": served_name or mspec.get("served_name", model), "n": len(rows), "accuracy": acc}
    out.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    return res
