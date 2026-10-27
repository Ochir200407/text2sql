"""Хяналтын файл (data/mt/v1_review/<домэйн>.jsonl)-ийн шалгагч. Заавар: docs/mt_authoring/REVIEW_BRIEF.md.

Ажиллуулах:  PYTHONIOENCODING=utf-8 python scripts/mt/check_review.py gl_trends [cross ...]
Шалгана: ноорогийн id бүр яг нэг удаа; verdict ∈ ok|fix|bad; fix-д mn_fixed/en_fixed-ийн аль нэг; entity-тэй мөрийн mn_fixed нь
entity-ийн mn хэлбэрийг хадгалсан эсвэл entities_fixed өгсөн; reason хоосон биш; blind орчуулга (_blind/<домэйн>_re_en.jsonl) бүх id-тай.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CYR = re.compile(r"[А-Яа-яЁёӨөҮү]")


def rd(p: Path) -> list[dict]:
    return [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]


def main(domains: list[str]) -> int:
    bad = 0
    for dom in domains:
        drafts = {d["id"]: d for d in rd(ROOT / f"data/mt/{'v2' if dom.startswith(('phr_', 'd5_')) else 'v1'}_drafts/{dom}.jsonl")}
        errs: list[str] = []
        blind_p = ROOT / f"data/mt/v1_review/_blind/{dom}_re_en.jsonl"
        if not blind_p.exists():
            errs.append("blind орчуулгын файл алга: _blind/%s_re_en.jsonl" % dom)
        else:
            blind = {r["id"]: r for r in rd(blind_p)}
            miss = set(drafts) - set(blind)
            if miss:
                errs.append(f"blind орчуулга дутуу: {len(miss)} id (жишээ {sorted(miss)[:3]})")
        rev_p = ROOT / f"data/mt/v1_review/{dom}.jsonl"
        if not rev_p.exists():
            errs.append("хяналтын файл алга")
            rev = []
        else:
            rev = rd(rev_p)
        seen = set()
        counts = {"ok": 0, "fix": 0, "bad": 0}
        for r in rev:
            rid = r.get("id")
            if rid not in drafts:
                errs.append(f"{rid}: ноорогт байхгүй id")
                continue
            if rid in seen:
                errs.append(f"{rid}: давхардсан")
            seen.add(rid)
            v = r.get("verdict")
            if v not in counts:
                errs.append(f"{rid}: verdict буруу {v!r}")
                continue
            counts[v] += 1
            if not str(r.get("reason", "")).strip():
                errs.append(f"{rid}: reason хоосон")
            d = drafts[rid]
            if v == "fix":
                if not (r.get("mn_fixed") or r.get("en_fixed")):
                    errs.append(f"{rid}: fix боловч mn_fixed/en_fixed алга")
                if r.get("en_fixed") and CYR.search(re.sub(r"«[^»]*»", "", r["en_fixed"])) and not d.get("entities"):
                    errs.append(f"{rid}: en_fixed-д кирилл үсэг")
                if r.get("mn_fixed") and d.get("entities") and "entities_fixed" not in r:
                    for e in d["entities"]:
                        if e["mn"] not in r["mn_fixed"]:
                            errs.append(f"{rid}: mn_fixed-д entity «{e['mn']}» алга — entities_fixed өг")
                            break
        miss = set(drafts) - seen
        if miss:
            errs.append(f"хяналтгүй id: {len(miss)} (жишээ {sorted(miss)[:3]})")
        print(f"{dom}: ноорог {len(drafts)}, хяналт {len(rev)}; ok {counts['ok']}, fix {counts['fix']}, bad {counts['bad']}; алдаа {len(errs)}")
        for e in errs[:30]:
            print("  АЛДАА:", e)
        bad += len(errs)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
