"""R1.2 офлайн шалгалт: `translation_check`-ийг DEV орчуулгын кэш дээр ажиллуулж, тэмдэглэгдсэн/тэмдэглэгдээгүй асуултын EX-ийг
хадгалсан dev run-уудын зөв/буруугаар харьцуулна. CPU, LLM/DB-гүй. ЗӨВХӨН dev (evalset/dev.jsonl-ийн id; test-ийн кэш, gold,
run-ыг хэзээ ч уншихгүй).

    PYTHONIOENCODING=utf-8 python scripts/translate/check_report.py
    PYTHONIOENCODING=utf-8 python scripts/translate/check_report.py --json build/translate_check_dev.json

Хос (кэш ↔ run): кэшийн `text` нь run-ийн `question_used`-тай таарч байгааг (en_prompt) шалгаж хэвлэнэ.
  qwen3.5-9b      ↔ H4 (en_prompt, 1 хувилбар), E5h_w3 (чуулга), I3 (Kaggle compact)
  qwen3.5-9b.fix  ↔ I3t (Kaggle compact). I3t-ийг raw кэшийн тэмдэглэгээгээр ч харуулна: I3 → I3t = translate_fix-ийн
  H2 fallback (хуурамч маск → монгол + англи) нь R1.2-ын бодит хэрэгжилттэй ойр.
Fallback-ийн симуляци: H4 (en_prompt) ба H2 (bilingual) нь ижил орчуулга, ижил adapter → «тэмдэглэгдсэнд H2, бусдад H4» = R1.2-ын
`translate_check: bilingual`-ийн GPU-гүй ойролцоо тооцоо (seed/sampling ижил биш тул ±шуугиантай).
M1a-ийн MADLAD кэш (m1_madlad_improved) локалд алга — тооцоогүй.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from mn_text2sql.pipeline.translate_check import keys_from_values, translation_check  # noqa: E402

RUNS = ROOT / "results" / "runs" / "dev"
CACHES = {"qwen3.5-9b": ["H4", "H2", "E5h_w3", "I3", "I3t"], "qwen3.5-9b.fix": ["I3t"]}


def dev_items() -> dict[str, dict]:
    out = {}
    for line in (ROOT / "evalset" / "dev.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            assert d.get("split", "dev") == "dev"
            out[d["id"]] = {"category": d["category"], "difficulty": d.get("difficulty")}
    return out


def load_run(name: str) -> dict[str, dict]:
    """{id: {correct, question_used?}} — run-ийн jsonl эсвэл <name>_kaggle_q.txt (Q|id|cat|status|correct|...)."""
    d = RUNS / name
    if d.is_dir():
        files = sorted(d.glob("*__mn__*.jsonl"))
        out = {}
        for line in files[0].read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip():
                r = json.loads(line)
                out[r["question_id"]] = {"correct": str(r.get("correct")) == "True",
                                         "question_used": r.get("question_used")}
        return out
    q = RUNS / f"{name}_kaggle_q.txt"
    out = {}
    for line in q.read_text(encoding="utf-8").splitlines():
        if line.startswith("Q|"):
            _, qid, _cat, _st, ok, *_ = line.split("|")
            out[qid] = {"correct": ok == "1", "question_used": None}
    return out


def load_cache(name: str, ids: set[str]) -> dict[tuple[str, str], dict]:
    path = ROOT / "build" / "translations" / f"{name}.jsonl"
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            if d["id"] in ids:              # зөвхөн dev
                out[(d["id"], d["variant"])] = d
    return out


def ex(ids, run, items) -> tuple[int, int]:
    ans = [i for i in ids if items[i]["category"] == "answerable" and i in run]
    return sum(run[i]["correct"] for i in ans), len(ans)


def pct(c, n):
    return f"{c}/{n} ({100 * c / n:.1f}%)" if n else "0/0"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-keys", action="store_true", help="build/index/values.json-ийн DB утгагүй (DB-гүй эвристик)")
    ap.add_argument("--json", help="үр дүнг JSON-оор хадгалах")
    a = ap.parse_args(argv)
    items = dev_items()
    keys = None if a.no_keys else keys_from_values(
        json.loads((ROOT / "build" / "index" / "values.json").read_text(encoding="utf-8")))
    report: dict = {}
    for cache, runs in CACHES.items():
        recs = load_cache(cache, set(items))
        print(f"\n=== {cache} (dev) ===")
        per_var = {}
        for var in ("mn", "latin", "mixed"):
            rows = {i: translation_check(r["source"], r["text"], keys=keys) for (i, v), r in recs.items() if v == var}
            if not rows:
                continue
            flagged = {i for i, f in rows.items() if f}
            c = Counter(x for f in rows.values() for x in f)
            bil = sum("(English translation:" in r["text"] for (i, v), r in recs.items() if v == var)
            print(f"  {var}: {len(rows)} асуулт, тэмдэглэсэн {pct(len(flagged), len(rows))}; шалтгаан: {dict(c)}"
                  + (f"; кэшэд аль хэдийн H2: {bil}" if bil else ""))
            per_var[var] = {"n": len(rows), "flagged": len(flagged), "reasons": dict(c), "flags": rows}
        rows = per_var["mn"]["flags"]
        flagged = {i for i, f in rows.items() if f}
        clean = set(rows) - flagged
        rep = {"variants": {k: {kk: vv for kk, vv in v.items() if kk != "flags"} for k, v in per_var.items()},
               "flagged_ids_mn": sorted(flagged), "runs": {}}
        for run_name in runs:
            run = load_run(run_name)
            used = [(i, run[i]["question_used"]) for i in rows if i in run and run[i]["question_used"]]
            match = sum(u == recs[(i, "mn")]["text"] for i, u in used)
            cf, nf = ex(flagged, run, items)
            cc, nc = ex(clean, run, items)
            line = f"  {run_name:7s} EX(answerable) тэмдэглэсэн {pct(cf, nf)} | тэмдэглээгүй {pct(cc, nc)}"
            if used:
                line += f" | question_used == кэш: {match}/{len(used)}"
            print(line)
            per_reason = {}
            for reason in sorted({x for f in rows.values() for x in f}):
                ids = {i for i, f in rows.items() if reason in f}
                per_reason[reason] = ex(ids, run, items)
            print("           шалтгаанаар: " + ", ".join(f"{k} {pct(*v)}" for k, v in per_reason.items()))
            rep["runs"][run_name] = {"flagged": [cf, nf], "clean": [cc, nc], "by_reason": per_reason,
                                     "question_used_match": [match, len(used)]}
        if "H4" in runs and "H2" in runs:
            h4, h2 = load_run("H4"), load_run("H2")
            ans = [i for i in rows if items[i]["category"] == "answerable"]
            base = sum(h4[i]["correct"] for i in ans)
            sim = sum((h2 if i in flagged else h4)[i]["correct"] for i in ans)
            h2all = sum(h2[i]["correct"] for i in ans)
            gain = sum(1 for i in ans if i in flagged and h2[i]["correct"] and not h4[i]["correct"])
            loss = sum(1 for i in ans if i in flagged and h4[i]["correct"] and not h2[i]["correct"])
            print(f"  Fallback-ийн симуляци (answerable {len(ans)}): H4 {base} → H4 + тэмдэглэсэнд H2 {sim} "
                  f"(+{gain}/−{loss}); H2 бүгдэд {h2all}")
            rep["fallback_sim"] = {"answerable": len(ans), "H4": base, "sim": sim, "gain": gain, "loss": loss,
                                   "H2_all": h2all}
        report[cache] = rep
    if a.json:
        Path(a.json).write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
    return report


if __name__ == "__main__":
    main()
