"""Run-ий JSONL-ийг notebook-ийн логт товч хэвлэнэ (Kaggle-аас файл татахгүйгээр локалд дүн, McNemar бодоход).

    python scripts/notebook/compact_results.py results/runs/test/E2/arctic-r1-7b__mn__s1__h_H4.jsonl

Мөр бүр: Q|<id>|<category>|<status>|<correct 0/1>|<confidence>|<candidate бүрийн зөв эсэх 0/1...>
Эцэст нь ангилал бүрийн тоо, pass@k ба санал нийлэлтийн босгоор татгалзах муруй (E3).
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

NO_ANSWER = {"abstained", "refused", "clarify"}


def main() -> None:
    for path in sys.argv[1:]:
        p = Path(path)
        if not p.exists():
            print(f"[compact] алга: {p}")
            continue
        rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
        print(f"[compact] {p} n={len(rows)}")
        for r in rows:
            cand = "".join("1" if c else "0" for c in (r.get("cand_correct") or []))
            conf = r.get("confidence")
            print(f"Q|{r['question_id']}|{r.get('category')}|{r.get('status')}|{int(bool(r.get('correct')))}|"
                  f"{'' if conf is None else round(conf, 3)}|{cand}")
        cats = Counter(r.get("category") for r in rows)
        ok = Counter(r.get("category") for r in rows if r.get("correct"))
        anyc = Counter(r.get("category") for r in rows if any(r.get("cand_correct") or []))
        print("S|total|" + str(sum(ok.values())) + "/" + str(len(rows)))
        for c in sorted(cats):
            print(f"S|{c}|{ok[c]}/{cats[c]}|pass@k={anyc[c]}")
        for t in (0.3, 0.4, 0.5, 0.6):
            tot = 0
            for r in rows:
                low = r.get("status") not in NO_ANSWER and (r.get("confidence") or 0) < t
                if r.get("category") == "answerable":
                    tot += bool(r.get("correct")) and not low
                elif r.get("category") == "unanswerable":
                    tot += r.get("status") in NO_ANSWER or low
                else:   # ambiguous: зөвхөн тодруулга эсвэл зөв тайлбар
                    tot += r.get("status") == "clarify" or low or (bool(r.get("correct")) and not low)
            print(f"S|clarify<{t}|{tot}/{len(rows)}")


if __name__ == "__main__":
    main()
