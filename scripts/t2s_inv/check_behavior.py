"""S4 (зан үйл) ноорог: data/t2s_inv/v1_drafts/behavior_<бүлэг>.jsonl-ийг шалгана. Заавар: docs/mt_authoring/BRIEF_BEHAVIOR.md.

Ажиллуулах:  PYTHONIOENCODING=utf-8 PYTHONPATH=src python scripts/t2s_inv/check_behavior.py data/t2s_inv/v1_drafts/behavior_a.jsonl [...]

Шалгалт: талбар, reason, хариулах мөрийн gold_sql нь t2s схемийн эсрэг sqlglot-оор задарч, бүх хүснэгт/багана нь байгаа эсэх
(schema_variants.validate), near-miss хосын холбоос, давхардал, үнэлгээний олонлогтой төстэй эсэх (зөвхөн id). Алдаа 0 → exit 0.
"""

from __future__ import annotations

import json
import re
import sys
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts" / "mt"))

from build_mt_v0 import EvalGuard, cos, grams, norm, vnorm  # noqa: E402
from schema_variants import validate  # noqa: E402

from mn_text2sql.knowledge.semantic import load_semantic_model  # noqa: E402

REASONS = {"pii", "missing_data", "non_sql", "external_knowledge", "future", "write_operation"}
REQ = ["id", "kind", "domain", "question_mn", "question_en", "reason", "explanation_en", "gold_sql", "pair_of", "notes"]
CYR = re.compile(r"[А-Яа-яЁёӨөҮү]")


def main(paths: list[str]) -> int:
    if not paths:
        print(__doc__)
        return 2
    model = load_semantic_model()
    types = json.loads((ROOT / "build/index/column_examples.json").read_text(encoding="utf-8")).get("__types__", {})
    schema = {t: {c: types.get(f"{t}.{c}", "text") for c in tab.columns} for t, tab in model.tables.items()}
    guard = EvalGuard()
    errors, warns = [], []
    rows = []
    for path in paths:
        for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
            if line.strip():
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError as e:
                    errors.append(f"{Path(path).name}:{n}: JSON алдаа: {e}")
    ids = {r.get("id") for r in rows}
    seen, grm = {}, []
    kinds, reasons = Counter(), Counter()
    for r in rows:
        rid = r.get("id", "?")
        miss = [k for k in REQ if k not in r]
        if miss:
            errors.append(f"{rid}: талбар дутуу {miss}")
            continue
        if not re.fullmatch(r"S4-[a-z]+-\d{3,}", str(rid)):
            errors.append(f"{rid}: id хэлбэр буруу (S4-<бүлэг>-001)")
        kinds[r["kind"]] += 1
        if not CYR.search(r["question_mn"]):
            errors.append(f"{rid}: question_mn кирилл биш")
        if CYR.search(r["question_en"]) or len(r["question_en"]) < 8:
            errors.append(f"{rid}: question_en буруу")
        if r["kind"] == "refuse":
            if r["reason"] not in REASONS:
                errors.append(f"{rid}: reason буруу {r['reason']!r}")
            reasons[r["reason"]] += 1
            if r["gold_sql"]:
                errors.append(f"{rid}: татгалзах мөрөнд gold_sql хоосон байна")
            if not (20 <= len(r["explanation_en"]) <= 400) or CYR.search(r["explanation_en"]):
                errors.append(f"{rid}: explanation_en (англи, 20–400 тэмдэгт) буруу")
            if re.search(r"\bselect\b.+\bfrom\b", r["explanation_en"], re.I | re.S):
                errors.append(f"{rid}: explanation_en-д SQL байна")
        elif r["kind"] == "answer":
            if not r["gold_sql"]:
                errors.append(f"{rid}: хариулах мөрөнд gold_sql алга")
            else:
                err = validate(r["gold_sql"], schema)
                if err:
                    errors.append(f"{rid}: gold_sql — {err[:160]}")
            if r["pair_of"] and r["pair_of"] not in ids:
                errors.append(f"{rid}: pair_of {r['pair_of']!r} олдсонгүй")
        else:
            errors.append(f"{rid}: kind нь refuse | answer")
        key = norm(r["question_en"])
        if key in seen:
            errors.append(f"{rid}: question_en давхардсан ({seen[key]})")
        seen[key] = rid
        g = grams(r["question_mn"])
        ng = vnorm(g)
        for rid2, g2, n2 in grm:
            if cos(g, g2, ng, n2) >= 0.9:
                warns.append(f"{rid}: {rid2}-тэй хэт төстэй")
                break
        grm.append((rid, g, ng))
        if guard.hit("mn", r["question_mn"]) or guard.hit("en", r["question_en"]):
            errors.append(f"{rid}: held-out олонлогтой төстэй — өөр сэдвээр бүхэлд нь шинээр бич")
    pairs = sum(1 for r in rows if r.get("kind") == "answer" and r.get("pair_of"))
    print(f"Нийт {len(rows)}: {dict(kinds)}; reason {dict(reasons)}; near-miss хос {pairs}")
    for k in REASONS:
        if reasons[k] < 20 and len(rows) >= 100:
            warns.append(f"reason {k}: {reasons[k]} < 20")
    print(f"алдаа {len(errors)}, анхааруулга {len(warns)}")
    for e in errors[:60]:
        print("АЛДАА:", e)
    for w in warns[:40]:
        print("анхааруулга:", w)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
