"""Kaggle логоос хуулсан compact мөрүүдийг (Q|id|category|status|correct|conf|bits) локал run-уудтай харьцуулна.

    python scripts/dev/kaggle_q_stats.py results/runs/test/E2_kaggle_q.txt --split test \
        --vs H4/arctic-r1-7b__mn__s0.jsonl D2/arctic-r1-7b__mn__s0.jsonl

Нийт ба ангиллаар зөв, pass@k, санал нийлэлтийн босгоор тодруулга (E3c) ба McNemar (exact binomial: нийт 300/200
асуулт дээр ба зөвхөн хариулах боломжтой дээр).
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from scipy.stats import binomtest

ROOT = Path(__file__).resolve().parents[2]


def load_q(path: Path) -> dict[str, dict]:
    out = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.startswith("Q|"):
            continue
        _, qid, cat, status, ok, conf, bits = (line.split("|") + [""] * 7)[:7]
        out[qid] = {"category": cat, "status": status, "correct": ok == "1",
                    "confidence": float(conf) if conf else None, "bits": bits}
    return out


def mcnemar(a: dict[str, bool], b: dict[str, bool]) -> str:
    keys = sorted(set(a) & set(b))
    win = sum(1 for k in keys if b[k] and not a[k])
    loss = sum(1 for k in keys if a[k] and not b[k])
    p = binomtest(min(win, loss), win + loss, 0.5).pvalue if win + loss else 1.0
    return f"+{win}/-{loss}, p={p:.3g} (n={len(keys)})"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("qfile")
    ap.add_argument("--split", default="test")
    ap.add_argument("--vs", nargs="*", default=[])
    a = ap.parse_args()
    q = load_q(Path(a.qfile))
    cats = Counter(r["category"] for r in q.values())
    ok = Counter(r["category"] for r in q.values() if r["correct"])
    anyc = Counter(r["category"] for r in q.values() if "1" in r["bits"])
    print(f"{a.qfile}: {sum(ok.values())}/{len(q)} ({100 * sum(ok.values()) / max(1, len(q)):.1f}%)")
    for c in sorted(cats):
        print(f"  {c}: {ok[c]}/{cats[c]} ({100 * ok[c] / cats[c]:.1f}%), pass@k {anyc[c]}")
    mine_all = {k: r["correct"] for k, r in q.items()}
    mine_ans = {k: r["correct"] for k, r in q.items() if r["category"] == "answerable"}
    for rel in a.vs:
        p = ROOT / "results" / "runs" / a.split / rel
        recs = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
        other_all = {r["question_id"]: bool(r["correct"]) for r in recs}
        other_ans = {r["question_id"]: bool(r["correct"]) for r in recs if r["category"] == "answerable"}
        print(f"  vs {rel}: {sum(other_all.values())}/{len(other_all)} | McNemar all {mcnemar(other_all, mine_all)}"
              f" | answerable {mcnemar(other_ans, mine_ans)}")


if __name__ == "__main__":
    sys.exit(main())
