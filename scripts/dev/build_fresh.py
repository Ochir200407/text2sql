"""Шинэ (fresh) үнэлгээний сан: evalset/fresh_parts/*.jsonl → evalset/fresh.jsonl (2026-11-02).

Батчуудыг нэрийн дарааллаар уншина; ижил id дахин гарвал сүүлийнх нь (солих хувилбар) өмнөхийг орлоно. Домэйн
(NA, NC, ND, NP, NL, NK, NG, NX) ба дугаараар эрэмбэлж, давхардсан id/family, ангиллын тархалтыг шалгана.
    python scripts/dev/build_fresh.py [--check]   (--check: файл бичихгүй, зөвхөн тайлан)"""
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PARTS = ROOT / "evalset" / "fresh_parts"
OUT = ROOT / "evalset" / "fresh.jsonl"
ORDER = ("NA", "NC", "ND", "NP", "NL", "NK", "NG", "NX")


def _key(rid: str) -> tuple:
    m = re.fullmatch(r"([A-Z]{2})(\d+)", rid)
    if not m or m.group(1) not in ORDER:
        raise ValueError(f"танигдаагүй id: {rid}")
    return ORDER.index(m.group(1)), int(m.group(2))


# dev/test-ийн адил хариулах бус асуултад ч difficulty байна (eval/dataset.py шаарддаг; check-gold V32-д 96 алдаа):
# боломжгүй — хүсэлтийн төрлөөс нь шууд танигдах (non_sql, write_operation, future, pii) easy, схемийн мэдлэг шаардах
# (missing_data, external_knowledge) medium; хоёрдмол — medium (dev/test-д голдуу medium).
EASY_REASONS = {"non_sql", "write_operation", "future", "pii"}


def default_difficulty(d: dict) -> str | None:
    if d["category"] == "unanswerable":
        return "easy" if d.get("unanswerable_reason") in EASY_REASONS else "medium"
    if d["category"] == "ambiguous":
        return "medium"
    return None


def build() -> list[dict]:
    recs: dict[str, dict] = {}
    for f in sorted(PARTS.glob("*.jsonl")):
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if line.strip():
                d = json.loads(line)
                if d["id"] in recs:
                    print(f"  {f.name}:{n}: {d['id']} — өмнөхийг орлов")
                recs[d["id"]] = d
    for d in recs.values():
        if d.get("difficulty") is None and default_difficulty(d):
            d["difficulty"] = default_difficulty(d)
    return sorted(recs.values(), key=lambda d: _key(d["id"]))


def main() -> None:
    items = build()
    fam = Counter(d["family"] for d in items)
    dup_fam = [k for k, c in fam.items() if c > 1]
    cats = Counter(d["category"] for d in items)
    diff = Counter(d.get("difficulty") for d in items if d["category"] == "answerable")
    diff_other = Counter((d["category"], d.get("difficulty")) for d in items if d["category"] != "answerable")
    dom = Counter(d["id"][:2] for d in items)
    print(f"нийт {len(items)}; ангилал {dict(cats)}; хүндрэл {dict(diff)}; бусад {dict(diff_other)}")
    print("домэйн " + ", ".join(f"{k} {dom.get(k, 0)}" for k in ORDER))
    if dup_fam:
        print("АНХААР: давхардсан family:", dup_fam)
    if "--check" in sys.argv:
        return
    OUT.write_text("".join(json.dumps(d, ensure_ascii=False) + "\n" for d in items), encoding="utf-8", newline="\n")
    print(f"→ {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
