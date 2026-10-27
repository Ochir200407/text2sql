"""LLM judge — туслах хэмжүүр (зааврын 5-р хэсэг: "LLM judge-ийг дор хаяж 200 хүний шошготой тулгаж баталгаажуулсны
дараа л ашиглана"). Гол үзүүлэлт EX хэвээр; judge нь EX-ийн "худал сөрөг" (дүрслэлийн зөрүү, нэмэлт багана г.м.)-ийг
тоймлоход л хэрэглэгдэнэ.

  judge          — run-ийн EX-ээр буруу (эсвэл бүх) бичлэгийг judge загвараар (жишээ нь Qwen3.5-9B) үнэлүүлнэ
  judge-validate — хүний шошготой CSV (question_id, human_label ∈ {1,0}) ба judge-ийн шийдвэрийн тохирол, Cohen's κ
"""

from __future__ import annotations

import csv
import json
import re
from concurrent.futures import ThreadPoolExecutor

from ..config import Config, load_models, resolve_path
from ..llm.client import make_llm
from .dataset import load_all_eval_items
from .metrics import load_records

JUDGE_PROMPT = """You are grading a text-to-SQL system for a Mongolian non-bank financial institution.
Decide whether the PREDICTED query answers the user's question correctly (same information as the reference answer;
column order, aliases, extra formatting and equivalent date/period encodings do not matter; missing or extra rows,
wrong filters, wrong aggregation or wrong numbers DO matter).

Question (Mongolian): {question}
Reference SQL: {gold_sql}
Reference result (first rows): {gold_rows}
Predicted SQL: {pred_sql}
Predicted result (first rows): {pred_rows}

Answer with one line: "VERDICT: CORRECT" or "VERDICT: INCORRECT", then one short reason."""


def _preview(executor, guard, sql: str | None, n: int = 8) -> str:
    if not sql:
        return "(no SQL)"
    g = guard.check(sql)
    if not g.ok:
        return f"(rejected: {g.error})"
    r = executor.run(g.sql, max_rows=n)
    return r.preview(n=n, width=40)


def run_judge(cfg: Config, run_file: str, model_name: str | None = None, base_url: str | None = None,
              only_wrong: bool = True, workers: int = 8) -> dict:
    from ..db.conn import bot_dsn
    from ..db.executor import Executor
    from .goldcheck import make_guard
    registry = load_models()
    model_name = model_name or registry.get("translator")
    mspec = registry["models"].get(model_name, {})
    llm_cfg = dict(cfg["llm"], model=mspec.get("served_name", model_name))
    if base_url:
        llm_cfg["base_url"] = base_url
    if mspec.get("extra_body"):
        llm_cfg["extra_body"] = mspec["extra_body"]
    llm = make_llm(llm_cfg)
    items = {it.id: it for it in load_all_eval_items(cfg)}
    recs = [r for r in load_records(resolve_path(run_file)) if r.get("category") == "answerable"
            and (not only_wrong or not r.get("correct"))]
    guard, ex = make_guard(cfg), Executor(bot_dsn(cfg), statement_timeout_ms=cfg["db"]["statement_timeout_ms"])

    def one(r):
        it = items.get(r["question_id"])
        if it is None:
            return None
        prompt = JUDGE_PROMPT.format(question=it.question_mn, gold_sql=it.gold_sql,
                                     gold_rows=_preview(ex, guard, it.gold_sql), pred_sql=r.get("sql") or "(none)",
                                     pred_rows=_preview(ex, guard, r.get("sql")))
        text = llm.chat([{"role": "user", "content": prompt}], temperature=0.0, max_tokens=300).texts[0]
        m = re.search(r"VERDICT:\s*(CORRECT|INCORRECT)", text.split("</think>")[-1], re.I)
        return {"question_id": r["question_id"], "variant": r.get("variant"), "ex_correct": bool(r.get("correct")),
                "judge_correct": (m.group(1).upper() == "CORRECT") if m else None, "judge_text": text[-500:]}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        out = [x for x in pool.map(one, recs) if x]
    ex.close()
    rp = resolve_path(run_file)
    path = resolve_path(cfg["eval"]["results_dir"]) / "judge" / f"{rp.parent.name}__{rp.stem}.judge.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for x in out:
            fh.write(json.dumps(x, ensure_ascii=False) + "\n")
    flips = sum(1 for x in out if x["judge_correct"] and not x["ex_correct"])
    return {"judged": len(out), "judge_says_correct_but_ex_wrong": flips, "file": str(path),
            "note": "judge нь зөвхөн judge-validate-ээр (≥200 хүний шошго, κ ≥ 0.8) баталгаажсаны дараа тайланд орно"}


def validate_judge(judge_file: str, human_csv: str) -> dict:
    """human_csv: question_id, variant(optional), human_label (1 = зөв, 0 = буруу). Дор хаяж 200 мөр."""
    judge = {}
    for line in resolve_path(judge_file).read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            judge[(d["question_id"], d.get("variant"))] = d["judge_correct"]
    pairs = []
    with open(resolve_path(human_csv), encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            key = (r["question_id"], r.get("variant") or None)
            j = judge.get(key, judge.get((r["question_id"], "mn")))
            if j is not None and r.get("human_label", "").strip() in ("0", "1"):
                pairs.append((bool(int(r["human_label"])), bool(j)))
    n = len(pairs)
    if not n:
        return {"n": 0, "error": "тохирох мөр алга"}
    agree = sum(1 for h, j in pairs if h == j) / n
    ph = sum(1 for h, _ in pairs if h) / n
    pj = sum(1 for _, j in pairs if j) / n
    pe = ph * pj + (1 - ph) * (1 - pj)
    kappa = (agree - pe) / (1 - pe) if pe < 1 else 1.0
    return {"n": n, "agreement": round(agree, 4), "cohen_kappa": round(kappa, 4), "enough_labels": n >= 200,
            "usable": n >= 200 and kappa >= 0.8}
