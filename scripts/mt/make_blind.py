"""Хяналтын агентад зориулсан «сохор» файл: ноорогийн ЗӨВХӨН id ба mn (англи орчуулгыг оруулахгүй).

Ажиллуулах:  python scripts/mt/make_blind.py gl_trends cross
Гаралт:     data/mt/v1_review/_blind/<домэйн>_mn.jsonl
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main(domains: list[str]) -> None:
    out = ROOT / "data/mt/v1_review/_blind"
    out.mkdir(parents=True, exist_ok=True)
    for dom in domains:
        rows = [json.loads(x) for x in (ROOT / f"data/mt/{'v2' if dom.startswith(('phr_', 'd5_')) else 'v1'}_drafts/{dom}.jsonl").read_text(encoding="utf-8").splitlines() if x.strip()]
        with open(out / f"{dom}_mn.jsonl", "w", encoding="utf-8", newline="\n") as fh:
            for r in rows:
                fh.write(json.dumps({"id": r["id"], "mn": r["mn"]}, ensure_ascii=False) + "\n")
        print(dom, len(rows))


if __name__ == "__main__":
    main(sys.argv[1:])
