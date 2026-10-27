"""Colab (үнэгүй T4) дээр асуулт-хариултын вэб асаах notebook → notebooks/t2s_web_colab.ipynb; T2 орчуулгын
туршилт (STAGE=t2, I3t2 dev/test) → notebooks/t2s_t2_colab.ipynb.

    python scripts/notebook/make_web_colab.py

Google Drive-ын MyDrive/t2s/ хавтаст: t2s_code.zip (pack_code.py) ба t2s_adapter_d2.zip.part00… + SHA256.txt.
Notebook: Drive холбох → код задлах → bootstrap (PostgreSQL, vLLM, DB) → STAGE=web (D2 ба орчуулагч 4-bit-ээр нэг
T4-д, Cloudflare-ийн үнэгүй tunnel) → «WEB URL:» холбоос. Colab-ийн таб нээлттэй байх хугацаанд ажиллана.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

MD = """# ББСБ асуулт-хариулт — Colab (үнэгүй T4)

1. Google Drive-д **MyDrive/t2s/** хавтас үүсгээд дотор нь хуулна: `t2s_code.zip`, `t2s_adapter_d2.zip.part00`, `part01`, … ба `SHA256.txt`.
2. Colab: **Runtime → Change runtime type → T4 GPU**.
3. **Runtime → Run all**. Drive-д нэвтрэх зөвшөөрөл асууна — зөвшөөрнө.
4. ~25–35 минутын дараа сүүлийн нүдэнд **`WEB URL: https://….trycloudflare.com/?k=…`** гарна. Тэр холбоосыг (бүтнээр нь, `?k=` хэсэгтэй) утас эсвэл компьютерийн браузерт нээгээд асууна. «FLOW BUILDER:» холбоосоор pipeline-ийг node-оор угсарч турших.
5. Энэ Colab-ийн табыг хаавал вэб зогсоно. Үнэгүй Colab хэдэн цагийн дараа өөрөө тасардаг."""

CELL_SETUP = r"""import os, shutil, subprocess, zipfile
from pathlib import Path
from google.colab import drive
drive.mount("/content/drive")
SRC = Path("/content/drive/MyDrive/t2s")
assert (SRC / "t2s_code.zip").exists(), "MyDrive/t2s/t2s_code.zip алга"
assert list(SRC.glob("t2s_adapter_d2.zip.part*")), "MyDrive/t2s/t2s_adapter_d2.zip.part00 … алга"
REPO = Path("/content/t2s")
shutil.rmtree(REPO, ignore_errors=True)
zipfile.ZipFile(SRC / "t2s_code.zip").extractall(REPO)
os.chdir(REPO)
os.environ.update(T2S_PG_PASSWORD="t2s_nb_admin", VLLM_USE_FLASHINFER_SAMPLER="0", ADAPTER_SRC=str(SRC),
                  STAGE="web", WEB_MIN="300", HF_HUB_ENABLE_HF_TRANSFER="0")
print(subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"], capture_output=True, text=True).stdout)

def run(cmd):
    p = subprocess.Popen(["bash", "-c", f"source scripts/server/env.sh && {cmd}"], stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1)
    for line in p.stdout:
        print(line, end="")
    p.wait()
    if p.returncode:
        raise RuntimeError(f"exit {p.returncode}: {cmd}")"""

CELL_BOOT = r"""# PostgreSQL 16, vLLM, DB (seed 42) — ~10–15 мин
run("bash scripts/notebook/bootstrap.sh")"""

CELL_WEB = r"""# D2 нэгтгэх + 2 загвар (4-bit, нэг T4) + вэб + tunnel. Доор «WEB URL:» мөр гарна (~10–20 мин).
run("bash scripts/server/run_plan_l4.sh")"""


def cell(kind, src):
    c = {"cell_type": kind, "metadata": {}, "source": src.splitlines(keepends=True)}
    if kind == "code":
        c.update(execution_count=None, outputs=[])
    return c


MD_T2 = """# T2 орчуулгын туршилт — Colab (үнэгүй T4)

Вэбийн notebook-тэй ижил MyDrive/t2s/ файлууд. **Runtime → Run all.** D2 ба Qwen3.5-9B (4-bit) асаад dev (300), test (200)
асуултыг T2 горимоор орчуулна (маскыг DB-ийн нэрээр шүүх + нэр томьёоны RAG), I3t2-ийг ажиллуулна (~4–5 цаг).
Үр дүн split бүр дуусмагц **MyDrive/t2s/results_t2/**-д хуулагдана — Colab тасарсан ч дууссан хэсэг үлдэнэ.
Табыг нээлттэй, компьютерийг унтуулахгүй байлгана."""


def notebook(md, cells, name):
    nb = {"nbformat": 4, "nbformat_minor": 5,
          "metadata": {"accelerator": "GPU", "colab": {"gpuType": "T4", "provenance": []},
                       "kernelspec": {"name": "python3", "display_name": "Python 3"},
                       "language_info": {"name": "python"}},
          "cells": [cell("markdown", md)] + [cell("code", c) for c in cells]}
    out = ROOT / "notebooks" / name
    out.write_text(json.dumps(nb, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    print(out)


notebook(MD, [CELL_SETUP, CELL_BOOT, CELL_WEB], "t2s_web_colab.ipynb")
notebook(MD_T2, [CELL_SETUP.replace('STAGE="web", WEB_MIN="300"', 'STAGE="t2", RESULTS_DST=str(SRC / "results_t2")'),
                 CELL_BOOT, '# T2 орчуулга → I3t2 dev, test (~4–5 цаг)\nrun("bash scripts/server/run_plan_l4.sh")'],
         "t2s_t2_colab.ipynb")
