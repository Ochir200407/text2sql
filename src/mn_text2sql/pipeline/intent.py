"""F7: монгол хэлэнд хүчтэй жижиг загвар (Gemma 4 E4B / Qwen3.5-4B г.м.) асуултыг англи intent JSON болгоно →
SQL загвар асуулт + intent-ийг evidence болгон авна (A4-ийн орчуулгатай харьцуулна).

Intent нь хэлний ойлголтын давхарга: схем мэдэхгүй, зөвхөн юу асууж байгааг бүтэцлэнэ. DB-ийн утгыг (нэр, код)
асуултад бичигдсэнээр нь хадгална. Кэш: build/intents/<загвар>.jsonl ({id, variant, json, raw, ok}).
"""

from __future__ import annotations

import json
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor

from ..config import Config, load_models, resolve_path
from ..llm.client import make_llm

log = logging.getLogger(__name__)

INTENT_SYSTEM = """You analyse questions that employees of a Mongolian non-bank financial institution ask about their
database (loans, loan applications, repayments, customers, deposits, accounts, collection, collateral, general ledger,
AML alerts, exchange rates). Questions may be in Mongolian Cyrillic, informal Latin-transliterated Mongolian, English, or
a mix. Return ONE JSON object (no prose) with these keys:
  "restated_en": the question as one precise English sentence,
  "task": one of count | sum | average | ratio | list | top_k | trend | compare | lookup | other,
  "measure": what is measured (e.g. "principal outstanding", "number of loans"),
  "entities": main objects (e.g. ["loans"], ["customers", "deposits"]),
  "filters": list of conditions in English, keeping names/codes/values EXACTLY as written in the question,
  "group_by": list of dimensions (e.g. ["branch", "month"]),
  "time": time window in words relative to today (e.g. "2025 calendar year", "last 3 months", "as of today"),
  "order": e.g. "descending by amount" or null,
  "limit": integer or null.
Banking terms: зээл=loan, хадгаламж=deposit, хугацаа хэтэрсэн=overdue (days past due > 0), үлдэгдэл=outstanding
balance, салбар=branch, харилцагч=customer, олголт=disbursement, эргэн төлөлт=repayment, барьцаа=collateral,
торгууль=penalty, хүү=interest, чанаргүй зээл=non-performing loan (NPL), идэвхтэй багц=active portfolio."""

INTENT_EXAMPLE_Q = "Дархан салбарын хугацаа хэтэрсэн зээлийн үндсэн үлдэгдэл хэд вэ?"
INTENT_EXAMPLE_A = json.dumps({
    "restated_en": "What is the total principal outstanding of overdue loans at the Дархан branch?",
    "task": "sum", "measure": "principal outstanding", "entities": ["loans"],
    "filters": ["branch = Дархан", "loan is overdue"], "group_by": [], "time": "as of today", "order": None,
    "limit": None}, ensure_ascii=False)


def intent_messages(question: str) -> list[dict]:
    return [{"role": "system", "content": INTENT_SYSTEM},
            {"role": "user", "content": INTENT_EXAMPLE_Q}, {"role": "assistant", "content": INTENT_EXAMPLE_A},
            {"role": "user", "content": question}]


def parse_intent(text: str) -> tuple[str | None, bool]:
    text = text.split("</think>")[-1]
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None, False
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return m.group(0)[:2000], False
    return json.dumps(obj, ensure_ascii=False), True


def build_intents(cfg: Config, items, variants: list[str], model_name: str | None = None,
                  base_url: str | None = None, workers: int = 8) -> dict:
    registry = load_models()
    model_name = model_name or registry.get("intent_model")
    mspec = registry["models"].get(model_name, {})
    llm_cfg = dict(cfg["llm"], model=mspec.get("served_name", model_name))
    if base_url:
        llm_cfg["base_url"] = base_url
    if mspec.get("extra_body"):
        llm_cfg["extra_body"] = mspec["extra_body"]
    llm = make_llm(llm_cfg)
    path = resolve_path("build/intents") / f"{model_name}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                done.add((d["id"], d["variant"]))
    jobs = [(it, v) for it in items for v in variants if it.question(v) and (it.id, v) not in done]
    lock = threading.Lock()
    stats = {"done": 0, "parse_failed": 0, "skipped_done": len(done)}

    def work(job):
        it, v = job
        resp = llm.chat(intent_messages(it.question(v)), temperature=0.0, max_tokens=512)
        js, ok = parse_intent(resp.texts[0])
        rec = {"id": it.id, "variant": v, "source": it.question(v), "json": js, "raw": resp.texts[0], "ok": ok}
        with lock, open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            stats["done"] += 1
            stats["parse_failed"] += 0 if ok else 1

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(work, jobs))
    stats["file"] = str(path)
    return stats
