"""Санхүүгийн хэллэгийн толь + жишээ асуултын ноорог (data/mt/v2_drafts/phr_<бүлэг>[_expr].jsonl)-ийг шалгана.
Заавар: docs/mt_authoring/BRIEF_PHRASES.md.

Ажиллуулах:  PYTHONIOENCODING=utf-8 python scripts/mt/check_phrases.py <бүлэг> [...]

Асуултын файлыг check_drafts.py-ийн бүх шалгалтаар (схем, entity, давхардал, eval-тэй төстэй) шалгаад, хэллэгийн файлын схем,
хэллэг бүрд ≥ 3 асуулт, асуултад хэллэг орсон эсэхийг нэмж шалгана. Алдаа 0 → exit code 0.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import check_drafts  # noqa: E402
from build_mt_v0 import ROOT  # noqa: E402

DRAFTS = ROOT / "data/mt/v2_drafts"
REQ = ["id", "mn", "mn_variants", "en", "en_alt", "pos", "sense", "trap", "category"]
POS = {"verb_phrase", "noun_phrase", "adjective", "adverbial", "abbreviation"}
CATS = {"verb_idiom", "collocation", "colloquial", "regulatory", "accounting", "time_quantity", "ambiguous"}
CYR = re.compile(r"[А-Яа-яЁёӨөҮү]")


def words(text: str) -> list[str]:
    return [w for w in re.findall(r"[а-яёөүa-z0-9]+", text.lower()) if len(w) >= 3]


def covered(expr: dict, mn: str) -> bool:
    """Хэллэгийн үгсийн ≥ 60% нь асуултад угаараа (эхний min(4, урт−2) үсэг; дагавар, хувирлыг тэсвэрлэнэ) орсон эсэх."""
    have = words(mn)
    for form in [expr["mn"], *expr.get("mn_variants", [])]:
        need = words(form)
        hit = sum(any(h.startswith(w[:max(3, min(4, len(w) - 2))]) for h in have) for w in need)
        if need and hit / len(need) >= 0.6:
            return True
    return False


def check_group(grp: str, errors: list, warns: list) -> None:
    ep, sp = DRAFTS / f"phr_{grp}_expr.jsonl", DRAFTS / f"phr_{grp}.jsonl"
    missing = [p for p in (ep, sp) if not p.exists()]
    for p in missing:
        errors.append(f"{p.name}: файл алга")
    if missing:
        return
    exprs: dict[str, dict] = {}
    cats = Counter()
    seen_mn: dict[str, str] = {}
    for n, line in enumerate(ep.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError as ex:
            errors.append(f"{ep.name}:{n}: JSON алдаа: {ex}")
            continue
        eid = e.get("id", f"#{n}")
        miss = [k for k in REQ if k not in e]
        if miss:
            errors.append(f"{eid}: талбар дутуу {miss}")
            continue
        if not re.fullmatch(rf"PH-{re.escape(grp)}-\d{{3,}}", eid):
            errors.append(f"{eid}: id хэлбэр буруу (PH-{grp}-001)")
        if eid in exprs:
            errors.append(f"{eid}: id давхардсан")
        if e["pos"] not in POS:
            errors.append(f"{eid}: pos буруу {e['pos']!r}")
        if e["category"] not in CATS:
            errors.append(f"{eid}: category буруу {e['category']!r}")
        if not CYR.search(e["mn"]) and e["pos"] != "abbreviation":
            errors.append(f"{eid}: mn кирилл биш")
        if CYR.search(e["en"]) or not e["en"].strip():
            errors.append(f"{eid}: en хоосон эсвэл кирилл")
        if not isinstance(e["mn_variants"], list) or not isinstance(e["en_alt"], list):
            errors.append(f"{eid}: mn_variants/en_alt жагсаалт биш")
        key = e["mn"].strip().lower()
        if key in seen_mn:
            warns.append(f"{eid}: mn «{e['mn']}» {seen_mn[key]}-тэй давхардсан")
        seen_mn[key] = eid
        cats[e["category"]] += 1
        exprs[eid] = e
    # асуултууд: check_drafts-ийн бүх шалгалт
    print(f"--- {sp.name} (check_drafts)")
    rc = check_drafts.main([str(sp)])
    if rc:
        errors.append(f"{sp.name}: check_drafts алдаатай (дээрх жагсаалт)")
    per = Counter()
    for line in sp.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            r = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid = r.get("id", "?")
        if not re.fullmatch(rf"MT-phr{re.escape(grp)}-\d{{3,}}", str(rid)):
            errors.append(f"{rid}: id хэлбэр буруу (MT-phr{grp}-001)")
        x = r.get("expr")
        if x not in exprs:
            errors.append(f"{rid}: expr {x!r} хэллэгийн файлд алга")
            continue
        per[x] += 1
        if not covered(exprs[x], str(r.get("mn", ""))):
            warns.append(f"{rid}: {x}-ийн хэллэг (эсвэл хувилбар) mn-д олдсонгүй")
    for eid in exprs:
        if per[eid] < 3:
            warns.append(f"{eid}: асуулт {per[eid]} (< 3)")
    print(f"\n{grp}: хэллэг {len(exprs)} (≥ 50), асуулт {sum(per.values())} (≥ 150); category: "
          + ", ".join(f"{k} {v}" for k, v in cats.most_common()))
    if len(exprs) < 50:
        warns.append(f"{grp}: хэллэг {len(exprs)} < 50")
    if sum(per.values()) < 150:
        warns.append(f"{grp}: асуулт {sum(per.values())} < 150")


def main(groups: list[str]) -> int:
    if not groups:
        print(__doc__)
        return 2
    errors: list[str] = []
    warns: list[str] = []
    for g in groups:
        check_group(g, errors, warns)
    print(f"\nХэллэгийн шалгалт: алдаа {len(errors)}, анхааруулга {len(warns)}")
    for e in errors[:60]:
        print("АЛДАА:", e)
    for w in warns[:40]:
        print("анхааруулга:", w)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
