"""Notebook (Kaggle/Colab)-д байршуулах кодын архив: dist/t2s_code.zip.

    python scripts/notebook/pack_code.py [--slim] [--out <zip>]

--slim: Chrome-оор байршуулах 10 MB-ын хязгаарт багтаана — thesis-ийн SQLite (STAGE=thesis-д л хэрэгтэй), MT-ийн ноорог,
хяналтын файл, хуучин v1 өгөгдлийг (сургалт v2-ийг хэрэглэнэ) оруулахгүй. Kaggle-д үргэлж --slim.
--out: архивын зам (анхдагч dist/t2s_code.zip; туршилтад scratch хавтас).

Kaggle: Datasets → New Dataset → t2s_code.zip (нэр нь жишээ нь «t2s-code»), notebook-д Add Input.
Colab: Google Drive-ын MyDrive/t2s/ хавтаст хуулна.
Үүсгэгддэг ба том зүйлсийг (venv, results, build, adapters, data/train, data/bird, report*/out) оруулахгүй.
Нууц байж болох файлыг (.env*, *.pem, *.key, kaggle.json, .netrc, id_* — config/kaggle_overrides*.env-ээс бусад) ХЭЗЭЭ Ч
оруулахгүй; илтгэл (*.pptx) ба gitignore-д орсон «diplom ajluud» хавтсыг ч оруулахгүй. scripts/server/env.sh орно (run_plan,
bootstrap, notebook source хийдэг).
"""

from __future__ import annotations

import fnmatch
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKIP_DIRS = {".venv", ".git", ".claude", "__pycache__", ".pytest_cache", ".ruff_cache", "build", "results", "adapters",
             "dist", "htmlcov", "diplom ajluud"}
SKIP_PATHS = {"data/train", "data/bird", "report/out"}
SKIP_SUFFIX = {".pyc", ".log", ".pdf", ".aux", ".bbl", ".blg", ".docx", ".zip", ".pptx"}
# Нууц байж болох файл (нэрээр, том жижиг үсэг ялгахгүй): .env* ба *.env (config/kaggle_overrides*.env-ээс бусад), түлхүүр,
# Kaggle/сүлжээний итгэмжлэл. KEEP_FILES/KEEP_CACHES-аас ч өмнө шалгана.
DENY_NAMES = (".env*", "*.env", "*.pem", "*.key", "kaggle.json", ".netrc", "id_*")
ALLOW_ENV = ("config/kaggle_overrides*.env",)
# build/ дотроос notebook-д хэрэгтэй жижиг кэш (A4 орчуулга, F7 intent, F8 router) — GPU-д дахин бодохгүй
SLIM_PATHS = {"data/thesis/ecom.sqlite", "data/mt/v1", "data/mt/v1_drafts", "data/mt/v1_review", "data/mt/v2_drafts"}
KEEP_CACHES = ("build/translations/", "build/intents/", "build/routes/")
# build/-ээс илгээх оролт. Notebook нь өмнөх t2s_results.zip-ийг кодын дээр задалдаг тул run_plan_l4.sh эхэндээ эдгээрийг
# кодын zip-ээс дахин задална (restore_keep_files; KEEP_CACHES-ийг биш — Kaggle дээр нэмэгддэг).
KEEP_FILES = {"build/synth_own/candidates.jsonl",
              # 2026-11-01: M1ensE2 (run_plan `ense2`) — dev-ийн E2 нь Kaggle-ийн архивт алга (Vast A100-д ажилласан)
              "results/runs/dev/E2/arctic-r1-7b__mn__s1__h_H4.jsonl",
              "results/runs/dev/E2/arctic-r1-7b__mn__s1__h_H4.meta.json",
              "results/runs/dev/E2/arctic-r1-7b__mn__s1__h_H4.summary.json"}   # V28: exec шүүлтийн оролт (docs/SYNTH_OWN.md)


def denied(rel: Path) -> bool:
    """Нууц байж болох файл — хэзээ ч багцлахгүй (config/kaggle_overrides*.env-ийг зөвшөөрнө)."""
    s, name = rel.as_posix(), rel.name.lower()
    if any(fnmatch.fnmatchcase(s, p) for p in ALLOW_ENV):
        return False
    return any(fnmatch.fnmatchcase(name, p) for p in DENY_NAMES)


def keep(rel: Path, slim: bool = False) -> bool:
    if denied(rel):
        return False
    if rel.as_posix() in KEEP_FILES:
        return True
    if rel.as_posix().startswith(KEEP_CACHES):
        return rel.suffix == ".jsonl"
    parts = rel.parts
    if any(p in SKIP_DIRS for p in parts):
        return False
    if len(parts) > 2 and parts[0].startswith("report") and parts[1] == "out":   # report*/out/ (LaTeX-ийн гаралт)
        return False
    s = rel.as_posix()
    if any(s == p or s.startswith(p + "/") for p in SKIP_PATHS | (SLIM_PATHS if slim else set())):
        return False
    return rel.suffix.lower() not in SKIP_SUFFIX


def main(argv: list[str] | None = None) -> None:
    import argparse
    ap = argparse.ArgumentParser(description="Kaggle/Colab-ийн кодын архив")
    ap.add_argument("--slim", action="store_true", help="10 MB-ын хязгаар (Kaggle-д үргэлж)")
    ap.add_argument("--out", type=Path, default=ROOT / "dist" / "t2s_code.zip", help="архивын зам")
    a = ap.parse_args(argv)
    out = a.out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(ROOT.rglob("*")):
            rel = p.relative_to(ROOT)
            if p.is_file() and keep(rel, a.slim) and p.resolve() != out:
                if p.suffix in (".sh", ".env"):   # Windows дээр CRLF орсон бол Linux-д эвдэрнэ (.env-ийг source хийдэг)
                    z.writestr(rel.as_posix(), p.read_bytes().replace(b"\r\n", b"\n"))
                else:
                    z.write(p, rel.as_posix())
                n += 1
    print(f"{out}: {n} files, {out.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
