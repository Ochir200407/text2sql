"""Kaggle логоос JS-ээр авсан товч кодыг (id + төлөв + зөв, хоёр run-ийг нэг токенд) Q| мөр болгоно.

    python scripts/dev/decode_kaggle_tok.py <tok.txt> --split dev --runs I3 I3r [--expect 123 148]

Токен: <id><S1><c1>[<S2><c2>...] — S: A answered, B abstained, R refused, C clarify, E error; c: 0/1. Ангиллыг
evalset-ээс сэргээнэ (логийн компакт кодонд ангиллын эхний үсэг давхцдаг). Нэг хувилбартай run тул conf=1.0, bits=c.
Гаралт: results/runs/<split>/<RUN>_kaggle_q.txt (kaggle_q_stats.py-ийн формат).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATUS = {"A": "answered", "B": "abstained", "R": "refused", "C": "clarify", "E": "error"}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("tokfile")
    ap.add_argument("--split", default="dev")
    ap.add_argument("--runs", nargs="+", required=True)
    ap.add_argument("--expect", nargs="*", type=int, default=[])
    a = ap.parse_args()
    ev = ROOT / "evalset" / f"{a.split}.jsonl"
    cat = {d["id"]: d["category"] for d in map(json.loads, ev.read_text(encoding="utf-8").splitlines()) if d}
    toks = [t for t in re.split(r"[,\s]+", Path(a.tokfile).read_text(encoding="utf-8")) if t]
    pat = re.compile(r"([A-Z]\d{3})" + r"([ABRCE])([01])" * len(a.runs))
    rows: dict[str, list[tuple[str, str]]] = {}
    for t in toks:
        m = pat.fullmatch(t)
        if not m:
            raise SystemExit(f"танигдахгүй токен: {t!r}")
        g = m.groups()
        rows[g[0]] = [(STATUS[g[1 + 2 * i]], g[2 + 2 * i]) for i in range(len(a.runs))]
    missing = set(cat) - set(rows)
    extra = set(rows) - set(cat)
    if missing or extra:
        raise SystemExit(f"id таарахгүй: дутуу {sorted(missing)[:5]}… ({len(missing)}), илүү {sorted(extra)[:5]}")
    for i, run in enumerate(a.runs):
        out = ROOT / "results" / "runs" / a.split / f"{run}_kaggle_q.txt"
        lines = [f"Q|{qid}|{cat[qid]}|{st[i][0]}|{st[i][1]}|1.0|{st[i][1]}" for qid, st in rows.items()]
        out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
        ok = Counter(cat[q] for q, st in rows.items() if st[i][1] == "1")
        n = Counter(cat.values())
        tot = sum(ok.values())
        print(f"{run}: {tot}/{len(rows)} " + " ".join(f"{c} {ok[c]}/{n[c]}" for c in sorted(n)) + f" → {out}")
        if i < len(a.expect) and a.expect[i] != tot:
            raise SystemExit(f"{run}: логийн нийт {a.expect[i]} ≠ задалсан {tot}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
