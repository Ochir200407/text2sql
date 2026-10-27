"""Notebook (Kaggle/Colab)-д байршуулах кодын архив: dist/t2s_code.zip.

    python scripts/notebook/pack_code.py [--slim]

--slim: Chrome-оор байршуулах 10 MB-ын хязгаарт багтаана — thesis-ийн SQLite (STAGE=thesis-д л хэрэгтэй), MT-ийн ноорог,
хяналтын файл, хуучин v1 өгөгдлийг (сургалт v2-ийг хэрэглэнэ) оруулахгүй.

Kaggle: Datasets → New Dataset → t2s_code.zip (нэр нь жишээ нь «t2s-code»), notebook-д Add Input.
Colab: Google Drive-ын MyDrive/t2s/ хавтаст хуулна.
Үүсгэгддэг ба том зүйлсийг (venv, results, build, adapters, data/train, data/bird, report/out) оруулахгүй.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKIP_DIRS = {".venv", ".git", ".claude", "__pycache__", ".pytest_cache", ".ruff_cache", "build", "results", "adapters",
             "dist", "htmlcov"}
SKIP_PATHS = {"data/train", "data/bird", "report/out"}
SKIP_SUFFIX = {".pyc", ".log", ".pdf", ".aux", ".bbl", ".blg", ".docx", ".zip"}
# build/ дотроос notebook-д хэрэгтэй жижиг кэш (A4 орчуулга, F7 intent, F8 router) — GPU-д дахин бодохгүй
SLIM_PATHS = {"data/thesis/ecom.sqlite", "data/mt/v1", "data/mt/v1_drafts", "data/mt/v1_review", "data/mt/v2_drafts"}
KEEP_CACHES = ("build/translations/", "build/intents/", "build/routes/")


def keep(rel: Path, slim: bool = False) -> bool:
    if rel.as_posix().startswith(KEEP_CACHES):
        return rel.suffix == ".jsonl"
    parts = rel.parts
    if any(p in SKIP_DIRS for p in parts):
        return False
    s = rel.as_posix()
    if any(s == p or s.startswith(p + "/") for p in SKIP_PATHS | (SLIM_PATHS if slim else set())):
        return False
    return rel.suffix not in SKIP_SUFFIX


def main() -> None:
    import sys
    slim = "--slim" in sys.argv[1:]
    out = ROOT / "dist" / "t2s_code.zip"
    out.parent.mkdir(exist_ok=True)
    n = 0
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for p in sorted(ROOT.rglob("*")):
            rel = p.relative_to(ROOT)
            if p.is_file() and keep(rel, slim):
                if p.suffix == ".sh":   # Windows дээр CRLF орсон бол Linux-д эвдэрнэ
                    z.writestr(rel.as_posix(), p.read_bytes().replace(b"\r\n", b"\n"))
                else:
                    z.write(p, rel.as_posix())
                n += 1
    print(f"{out}: {n} files, {out.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
