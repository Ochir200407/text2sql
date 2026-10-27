"""Kaggle (2×T4) дээр асуулт-хариултын вэб ба flow builder-ийг ЗАГВАРТАЙ асаах notebook → notebooks/t2s_web_kaggle.ipynb.

    python scripts/notebook/make_web_kaggle.py

Kaggle-д: New Notebook → File → Import Notebook (энэ файл) → Add Input: `text2sql` (код) ба `t2s-adapter-d2` (D2 хэсгүүд)
→ Settings: GPU T4 x2, Internet On → **Run All** (интерактив сесс; Save Version биш — холбоос нүдний гаралтад гарна).
STAGE=web (run_plan_l4.sh): GPU0 = D2 (4-bit), GPU1 = орчуулагч Qwen3.5-9B (4-bit), DB, вэб, Cloudflare tunnel → «WEB URL:»,
«FLOW BUILDER:» холбоос. Кодын dataset-ийн config/kaggle_overrides.env (өөр stage-д зориулсан) энд үйлчлэхгүй.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

MD = """# Text2SQL — загвартай вэб ба flow builder (Kaggle, GPU T4 x2)

1. Баруун талын **Input**: `text2sql` (код) ба `t2s-adapter-d2` (D2 adapter) dataset нэмэгдсэн байна.
2. **Settings → Accelerator: GPU T4 x2**, **Internet: On**.
3. **Run All** (дээд мөр ▶▶). ~30–40 минутын дараа сүүлийн нүдэнд:
   - `FLOW BUILDER: https://….trycloudflare.com/builder?k=…` — алхмуудыг угсарч **▶ Турших**-аар бодит загвараар ажиллуулна
   - `WEB URL: …/?k=…` — энгийн асуулт-хариултын вэб
4. Холбоосыг бүтнээр нь (`?k=` хэсэгтэй) нээнэ. Энэ notebook-ийн таб нээлттэй байх хугацаанд (WEB_MIN, анхдагч 9 цаг) ажиллана.
   Дуусмагц **Stop session** дарж GPU квотоо хэмнэнэ."""

CELL_SETUP = r"""import os, shutil, subprocess, zipfile
from pathlib import Path
IN = Path("/kaggle/input")
# Kaggle zip-ийг задалж mount хийдэг: код = run_plan_l4.sh агуулсан хавтас; задлаагүй бол t2s_code.zip
code = next((p.parents[2] for p in IN.rglob("scripts/server/run_plan_l4.sh")), None)
REPO = Path("/tmp/t2s")
shutil.rmtree(REPO, ignore_errors=True)
if code is not None:
    shutil.copytree(code, REPO)
else:
    z = next(IN.rglob("t2s_code.zip"), None)
    assert z, "text2sql (код) dataset-ийг Add Input хийнэ үү"
    zipfile.ZipFile(z).extractall(REPO)
assert list(IN.rglob("t2s_adapter_d2.zip.part00")), "t2s-adapter-d2 dataset-ийг Add Input хийнэ үү"
os.chdir(REPO)
(REPO / "config/kaggle_overrides.env").unlink(missing_ok=True)   # өөр stage-ийн тохиргоо — вэбд хэрэггүй
os.environ.update(T2S_PG_PASSWORD="t2s_nb_admin", VLLM_USE_FLASHINFER_SAMPLER="0", ADAPTER_SRC=str(IN),
                  STAGE="web", WEB_MIN=os.environ.get("WEB_MIN", "540"), HF_HUB_ENABLE_HF_TRANSFER="0")
print(subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"], capture_output=True, text=True).stdout)

def run(cmd):
    p = subprocess.Popen(["bash", "-c", f"source scripts/server/env.sh && {cmd}"], stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1)
    for line in p.stdout:
        print(line, end="", flush=True)
    p.wait()
    if p.returncode:
        raise RuntimeError(f"exit {p.returncode}: {cmd}")"""

CELL_BOOT = r"""# PostgreSQL 16, Python хамаарал, DB (seed 42), индекс — ~10–15 мин
run("bash scripts/notebook/bootstrap.sh")"""

CELL_WEB = r"""# D2 нэгтгэх + 2 загвар (4-bit, GPU тус бүрт) + вэб + tunnel. «FLOW BUILDER:» ба «WEB URL:» мөр гарна (~15–25 мин).
run("bash scripts/server/run_plan_l4.sh")"""


def cell(kind, src):
    c = {"cell_type": kind, "metadata": {}, "source": src.splitlines(keepends=True)}
    if kind == "code":
        c.update(execution_count=None, outputs=[])
    return c


nb = {"nbformat": 4, "nbformat_minor": 5,
      "metadata": {"kaggle": {"accelerator": "nvidiaTeslaT4", "isInternetEnabled": True, "isGpuEnabled": True},
                   "kernelspec": {"name": "python3", "display_name": "Python 3"}, "language_info": {"name": "python"}},
      "cells": [cell("markdown", MD)] + [cell("code", c) for c in (CELL_SETUP, CELL_BOOT, CELL_WEB)]}
out = ROOT / "notebooks" / "t2s_web_kaggle.ipynb"
out.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
print(out)
