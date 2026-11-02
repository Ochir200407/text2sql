"""Шинэ (fresh) 400 асуултын дүн — урьдчилан бүртгэсэн үнэлгээ (docs/FRESH_EVAL.md, 2026-11-02).

    python scripts/dev/fresh_report.py [--root results/runs/fresh] [--out docs/RESULTS_FRESH.md]

Run бүрийн (M1a_rs, M1a_fxv2k, M1ens, M1ensfxv2lk; бусад олдвол нэмнэ) нийт EX, ангилал, хариулах асуултын түвшин ба домэйн
(Wilson 95% CI); урьдчилан бүртгэсэн хос: M1ens ↔ M1ensfxv2lk (гол), M1a_rs ↔ M1a_fxv2k — McNemar exact (нийт ба хариулах).
Дутуу run (мөр < 400) бол «дутуу» гэж тэмдэглээд хосын тестэд оруулахгүй. Test/dev-ийн дүн зөвхөн лавлагаа (өөр асуултын сан).
Зөвхөн stdlib; DB, загвар хэрэггүй.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location("_ingest_results", ROOT / "scripts/ingest/ingest_results.py")
_ing = importlib.util.module_from_spec(_spec)
sys.modules["_ingest_results"] = _ing   # dataclass-д модуль бүртгэлтэй байх ёстой
_spec.loader.exec_module(_ing)
mcnemar_exact, wilson = _ing.mcnemar_exact, _ing.wilson

ORDER = ["M1a_rs", "M1a_fxv2k", "M1ens", "M1ensfxv2lk"]
PAIRS = [("M1ens", "M1ensfxv2lk"), ("M1a_rs", "M1a_fxv2k")]
HEADLINE = "M1ensfxv2lk"
REFERENCE = {"test M1ensfxv2lk": (156, 200, 108, 151), "dev M1ensfxv2lk": (241, 300, 170, 224),
             "test M1ens": (110, 200, 90, 151)}
CAT_MN = {"answerable": "хариулах", "unanswerable": "боломжгүй", "ambiguous": "хоёрдмол"}


def load_items(path: Path) -> dict[str, dict]:
    return {d["id"]: d for d in map(json.loads, filter(str.strip, path.read_text(encoding="utf-8").splitlines()))}


def load_run(d: Path) -> dict[str, bool]:
    out = {}
    for f in sorted(d.glob("*__mn__*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    r = json.loads(line)
                except ValueError:
                    continue
                qid = r.get("question_id") or r.get("id")
                if qid:
                    out[qid] = bool(r.get("correct"))
    return out


def kn(k: int, n: int) -> str:
    if not n:
        return "—"
    lo, hi = wilson(k, n)
    return f"{k}/{n} = **{100 * k / n:.1f}%** [{100 * lo:.1f}; {100 * hi:.1f}]"


def mcn(a: dict[str, bool], b: dict[str, bool], ids: list[str]) -> str:
    bb = sum(1 for i in ids if not a[i] and b[i])
    cc = sum(1 for i in ids if a[i] and not b[i])
    return f"+{bb}/−{cc}, p = {mcnemar_exact(bb, cc):.3g}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default="results/runs/fresh")
    ap.add_argument("--items", default="evalset/fresh.jsonl")
    ap.add_argument("--out", default="docs/RESULTS_FRESH.md")
    a = ap.parse_args()
    items = load_items(ROOT / a.items)
    n_all = len(items)
    root = ROOT / a.root
    names = [x for x in ORDER if (root / x).is_dir()] + sorted(
        p.name for p in root.glob("*") if p.is_dir() and p.name not in ORDER) if root.is_dir() else []
    runs = {nm: load_run(root / nm) for nm in names}
    lines = ["# Шинэ (fresh) 400 асуултын дүн", "",
             f"Асуулт: {n_all} ({', '.join(f'{CAT_MN[c]} {sum(1 for d in items.values() if d['category'] == c)}' for c in CAT_MN)}). "
             f"Урьдчилан бүртгэсэн гол дүн: **{HEADLINE}** (docs/FRESH_EVAL.md). CI = Wilson 95%.", ""]
    lines += ["| Run | бүрэн | нийт EX | хариулах EX | боломжгүй | хоёрдмол |", "|---|---|---|---|---|---|"]
    full = {}
    for nm, res in runs.items():
        ids = [i for i in items if i in res]
        full[nm] = len(ids) == n_all
        by = defaultdict(lambda: [0, 0])
        for i in ids:
            by[items[i]["category"]][0] += res[i]
            by[items[i]["category"]][1] += 1
        tot = sum(res[i] for i in ids)
        lines.append(f"| {nm}{' (гол)' if nm == HEADLINE else ''} | {len(ids)}/{n_all}{'' if full[nm] else ' дутуу'} | "
                     f"{kn(tot, len(ids))} | {kn(*by['answerable'])} | {kn(*by['unanswerable'])} | {kn(*by['ambiguous'])} |")
    lines += ["", "Лавлагаа (өөр асуултын сан — шууд харьцуулахгүй, CI-ээр):", ""]
    for k, (t, n, ta, na) in REFERENCE.items():
        lines.append(f"- {k}: нийт {kn(t, n)}, хариулах {kn(ta, na)}")
    lines += ["", "## Урьдчилан бүртгэсэн хос (McNemar exact, асуулт бүрээр)", ""]
    for x, y in PAIRS:
        if x in runs and y in runs and full.get(x) and full.get(y):
            ids = list(items)
            ans = [i for i in ids if items[i]["category"] == "answerable"]
            lines.append(f"- {x} → {y}: нийт {mcn(runs[x], runs[y], ids)}; хариулах {mcn(runs[x], runs[y], ans)}")
        else:
            lines.append(f"- {x} → {y}: run дутуу — тооцоогүй")
    for nm in [HEADLINE] if HEADLINE in runs else []:
        res = runs[nm]
        lines += ["", f"## {nm}: хариулах асуулт түвшин ба домэйнээр", "",
                  "| бүлэг | EX |", "|---|---|"]
        for key in ("difficulty", "domain"):
            grp = defaultdict(lambda: [0, 0])
            for i, d in items.items():
                if d["category"] == "answerable" and i in res:
                    grp[d.get(key) or "—"][0] += res[i]
                    grp[d.get(key) or "—"][1] += 1
            for g in sorted(grp):
                lines.append(f"| {key}: {g} | {kn(*grp[g])} |")
    out = ROOT / a.out
    out.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    print("\n".join(lines))
    print(f"\n→ {out}")


if __name__ == "__main__":
    main()
