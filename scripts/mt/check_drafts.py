"""Агентын бичсэн MT ноорог (data/mt/v1_drafts/<домэйн>.jsonl)-ийг шалгана. Заавар: docs/mt_authoring/BRIEF.md.

Ажиллуулах:  PYTHONIOENCODING=utf-8 python scripts/mt/check_drafts.py data/mt/v1_drafts/loans.jsonl [...]

Алдаа (errors) 0 болтол засна → exit code 0. Үнэлгээний олонлогтой төстэй мөрийн ЗӨВХӨН id-г хэлнэ (текстийг харуулахгүй).
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from build_mt_v0 import ROOT, EvalGuard, cos, grams, norm, read_jsonl, vnorm  # noqa: E402

REQ = ["id", "domain", "register", "mn", "en", "terms", "entities", "notes"]
DOMAINS = {"applications", "loans", "payments", "collection", "customers", "deposits", "gl_trends", "cross"}
REG_TARGET = {"R1": 0.30, "R2": 0.30, "R3": 0.10, "R4": 0.15, "R5": 0.15}
CYR = re.compile(r"[А-Яа-яЁёӨөҮү]")
LAT_TOKEN = re.compile(r"\b[A-Za-z]{2,}\b")
NUMERIC = re.compile(r"\d|сая|мянга|тэрбум|хувь|улирал|хоног|жил")
TRAP_STEMS = ("шимтгэл", "төгрөг", "торгуул", "хугацаа хэтэр", "олголт", "эргэн төлөлт", "барьцаа", "батлан даагч",
              "данснаас хас", "эрсдэлийн сан", "сэрэмжлүүлэг", "хураагдсан", "хүү")


def main(paths: list[str]) -> int:
    if not paths:
        print(__doc__)
        return 2
    guard = EvalGuard()
    v0 = [(grams(p["mn"]), None) for p in read_jsonl(ROOT / "data/mt/v0/pairs.jsonl")]
    v0 = [(g, vnorm(g)) for g, _ in v0]
    ent_path = ROOT / "data/mt/v0/entities.json"
    ent_values = json.loads(ent_path.read_text(encoding="utf-8")) if ent_path.exists() else {}
    all_values = {v for vs in ent_values.values() for v in vs}
    errors, warns = [], []
    total = 0
    for path in paths:
        p = Path(path)
        rows, bad = [], 0
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as e:
                errors.append(f"{p.name}:{n}: JSON алдаа: {e}")
                bad += 1
        total += len(rows)
        seen, norms, grm = set(), {}, []
        reg = Counter()
        n_num = n_ent = n_trap = 0
        for r in rows:
            rid = r.get("id", f"#{rows.index(r) + 1}")
            miss = [k for k in REQ if k not in r]
            if miss:
                errors.append(f"{rid}: талбар дутуу {miss}")
                continue
            if not re.fullmatch(r"MT-[a-z0-9_]+-\d{3,}", str(r["id"])):
                errors.append(f"{rid}: id хэлбэр буруу (MT-<домэйн>-001)")
            if r["id"] in seen:
                errors.append(f"{rid}: id давхардсан")
            seen.add(r["id"])
            if r["domain"] not in DOMAINS:
                errors.append(f"{rid}: domain буруу {r['domain']!r}")
            elif r["domain"] != p.stem and not p.stem.startswith(("phr_", "d5_")):   # хэллэг, D5 файл олон домэйнтэй
                warns.append(f"{rid}: domain ≠ файлын нэр ({p.stem})")
            if r["register"] not in REG_TARGET:
                errors.append(f"{rid}: register буруу {r['register']!r}")
            reg[r["register"]] += 1
            mn, en = str(r["mn"]).strip(), str(r["en"]).strip()
            if not (8 <= len(mn) <= 400) or not (8 <= len(en) <= 500):
                errors.append(f"{rid}: урт хэт богино/урт")
            letters = [c for c in mn if c.isalpha()]
            if letters and sum(1 for c in letters if CYR.match(c)) / len(letters) < 0.5:
                errors.append(f"{rid}: mn нь кирилл биш")
            if r["register"] == "R4" and not LAT_TOKEN.search(mn):
                errors.append(f"{rid}: R4 боловч mn-д англи үг алга")
            if CYR.search(en):
                warns.append(f"{rid}: en-д кирилл үсэг байна")
            if not isinstance(r["terms"], list) or any("=" not in str(t) for t in r["terms"]):
                warns.append(f"{rid}: terms «монгол=англи» хэлбэртэй биш")
            ents = r["entities"]
            if not isinstance(ents, list):
                errors.append(f"{rid}: entities жагсаалт биш")
                ents = []
            for e in ents:
                if not isinstance(e, dict) or not {"mn", "en", "value", "column"} <= set(e):
                    errors.append(f"{rid}: entity-д mn/en/value/column дутуу")
                    continue
                if e["mn"] not in mn:
                    errors.append(f"{rid}: entity mn «{e['mn']}» mn текстэд яг байхгүй")
                if e["en"].lower() not in en.lower():
                    errors.append(f"{rid}: entity en «{e['en']}» en текстэд байхгүй")
                if e["value"] not in all_values:
                    warns.append(f"{rid}: entity value «{e['value']}» entities.json-д алга")
            n_ent += bool(ents)
            n_num += bool(NUMERIC.search(mn))
            n_trap += any(re.search(r"(?<![а-яёөү])" + re.escape(s), mn.lower()) for s in TRAP_STEMS)   # үгийн эхэнд (бүтээгдэхүүн ≠ хүү)
            key = norm(mn)
            if key in norms:
                errors.append(f"{rid}: mn давхардсан ({norms[key]})")
            norms[key] = rid
            g = grams(mn)
            ng = vnorm(g)
            for rid2, g2, n2 in grm:
                if cos(g, g2, ng, n2) >= 0.9:
                    warns.append(f"{rid}: {rid2}-тэй хэт төстэй (давталт)")
                    break
            grm.append((rid, g, ng))
            if any(cos(g, g0, ng, n0) >= 0.85 for g0, n0 in v0):
                warns.append(f"{rid}: v0 хосын аль нэгтэй хэт төстэй")
            if guard.hit("mn", mn) or guard.hit("en", en):
                errors.append(f"{rid}: held-out олонлогтой төстэй — энэ мөрийг өөр сэдвээр бүхэлд нь шинээр бичнэ")
            elif any(h["ratio"] >= 0.5 for h in guard.substring_hits({rid: [mn, en]})):
                errors.append(f"{rid}: held-out асуултыг бараг бүтнээр нь агуулсан — өөр сэдвээр бүхэлд нь шинээр бичнэ")
        n = max(1, len(rows))
        print(f"{p.name}: {len(rows)} мөр; register " + ", ".join(f"{k} {reg[k]} ({reg[k] / n:.0%})" for k in sorted(REG_TARGET))
              + f"; тоо/огноо {n_num / n:.0%} (≥40%), entity {n_ent / n:.0%} (≥20%), урхи {n_trap / n:.0%} (≥15%)")
        for k, tgt in REG_TARGET.items():
            if abs(reg[k] / n - tgt) > 0.08 and len(rows) >= 30:
                warns.append(f"{p.name}: {k} хувь {reg[k] / n:.0%} (зорилт {tgt:.0%})")
        if len(rows) >= 30:
            if n_num / n < 0.40:
                warns.append(f"{p.name}: тоо/огноо агуулсан {n_num / n:.0%} < 40%")
            if n_ent / n < 0.20:
                warns.append(f"{p.name}: entity агуулсан {n_ent / n:.0%} < 20%")
            if n_trap / n < 0.15:
                warns.append(f"{p.name}: урхи агуулсан {n_trap / n:.0%} < 15%")
    print(f"\nНийт {total} мөр, алдаа {len(errors)}, анхааруулга {len(warns)}")
    for e in errors[:60]:
        print("АЛДАА:", e)
    for w in warns[:40]:
        print("анхааруулга:", w)
    if len(errors) > 60 or len(warns) > 40:
        print("… (илүү олон; эхнийхийг засаад дахин ажиллуул)")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
