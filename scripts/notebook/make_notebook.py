"""notebooks/t2s_eval.ipynb-ийг үүсгэнэ (эх нь энэ файл — notebook-ийг гараар биш, эндээс засна).

    python scripts/notebook/make_notebook.py
"""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

MD_INTRO = r"""# Монгол Text2SQL — тест (Kaggle / Colab)

Энэ notebook нь `scripts/server/run_plan_l4.sh`-ийн үе шатыг (A, B, F, E, G, thesis, test) ажиллуулж, **бүх үр дүнг нэг
файлд — `t2s_results.zip`** — 15 минут тутам хадгална. Дараагийн сесс тэр файлаас үргэлжилнэ (дууссан асуултыг алгасна).

**Нэг удаагийн бэлтгэл (локал):** `python scripts/notebook/pack_code.py` → `dist/t2s_code.zip`.

**Kaggle:**
1. Datasets → New Dataset → `t2s_code.zip`-ийг байршуулна (нэр нь жишээ нь `t2s-code`).
2. Энэ notebook-ийг импортлоод: Add Input → тэр dataset. Settings → Accelerator = **GPU T4 x2**, Internet = **On**
   (утасны баталгаажуулалт шаардлагатай).
3. Доорх ТОХИРГОО-г засаад **Save Version → Save & Run All**: browser хаасан ч 12 цаг хүртэл ажиллана. Гаралт
   (`t2s_results.zip`) нь version-ий Output-д үлдэнэ.
4. Дараагийн удаа: Add Input → өмнөх version-ий **Output**-ыг нэмнэ → notebook `t2s_results.zip`-ийг олж үргэлжлүүлнэ.

**Colab:** `t2s_code.zip`-ийг Google Drive-ын `MyDrive/t2s/`-д хуулна. Runtime → GPU (**L4 эсвэл A100**; үнэгүй нэг T4-т
7–9B загвар багтахгүй). Архив `MyDrive/t2s/t2s_results.zip`-д хадгалагдана.

**Анхаар:** T4 нь BF16/FP8 дэмждэггүй тул загварыг fp16-аар, хоёр GPU-д хувааж ажиллуулна — тайланд «T4, fp16» гэж
тэмдэглэнэ (төлөвлөгөө: L4, Arctic BF16, sqrl FP8). sqrl-9b (Qwen3.5) T4 дээр ажиллах эсэхийг эхлээд `MODE = "smoke"`-оор
шалгана. Kaggle-ийн долоо хоногийн ~30 GPU цаг, нэг сесс ≤ 12 цагт багтаахын тулд `MODELS`-ийг нэг нэгээр нь ажиллуулна."""

CODE_SETTINGS = r"""# ==== ТОХИРГОО (зөвхөн энэ нүдийг өөрчилнө) ====
MODE = "smoke"           # "smoke" = 10 асуултаар орчин шалгах | "stage" = бүтэн үе шат
STAGE = "A"              # caches | A | B | F | E | G | thesis | test   (scripts/server/run_plan_l4.sh-ийн дараалал)
MODELS = "arctic-r1-7b"  # "arctic-r1-7b" эсвэл "sqrl-9b" (нэг нэгээр нь — сессийн хугацаанд багтаана)
BEST_A = ""              # B, F, E, G, thesis, test-д заавал (A-ийн results/REPORT.md-ээс), жишээ нь "A6"
BEST_CONFIG = ""         # E, G, thesis, test-д заавал, жишээ нь "B5"
BEST_SFT = ""            # сургасан adapter ашиглах үед (Modal-ийн сургалтын дараа)
TEST_IDS = ""            # STAGE="test" үед, жишээ нь "A6,B5,E2"
ARCHIVE = "t2s_results.zip"
ARCHIVE_EVERY_MIN = 15"""

CODE_SETUP = r"""import json, os, shutil, subprocess, sys, threading, time, zipfile
from pathlib import Path

KAGGLE = Path("/kaggle").exists()
COLAB = (not KAGGLE) and Path("/content").exists()
if COLAB:
    from google.colab import drive
    drive.mount("/content/drive")
    OUT = Path("/content/drive/MyDrive/t2s")
elif KAGGLE:
    OUT = Path("/kaggle/working")
else:
    OUT = Path(os.environ.get("T2S_NB_OUT", str(Path.home() / "t2s_out")))   # локал туршилт
OUT.mkdir(parents=True, exist_ok=True)
SEARCH = [OUT] + ([Path("/kaggle/input")] if KAGGLE else [])


def find(name):
    for base in SEARCH:
        hits = sorted(base.rglob(name), key=lambda p: p.stat().st_mtime, reverse=True)
        if hits:
            return hits[0]
    return None


code_zip = find("t2s_code.zip")
assert code_zip, "t2s_code.zip олдсонгүй — Kaggle: dataset болгож Add Input; Colab: MyDrive/t2s/-д хуулна"
REPO = Path("/tmp/t2s")
if REPO.exists():
    os.chdir("/")            # нүдийг дахин ажиллуулахад одоогийн хавтсаа устгахгүй
    shutil.rmtree(REPO)
zipfile.ZipFile(code_zip).extractall(REPO)
os.chdir(REPO)

prev = find(ARCHIVE)
if prev:
    zipfile.ZipFile(prev).extractall(REPO)
    # архивлах үед бичигдэж байсан JSONL-ийн тасархай сүүлийн мөрийг хасна (runner үргэлжлүүлэхэд гацахгүй)
    for f in (REPO / "results").rglob("*.jsonl"):
        good = []
        for line in f.read_text(encoding="utf-8").splitlines():
            try:
                json.loads(line)
                good.append(line)
            except ValueError:
                pass
        f.write_text("".join(x + "\n" for x in good), encoding="utf-8")
    print("Өмнөх үр дүнг сэргээв:", prev)
print("код:", code_zip, "| гаралт:", OUT / ARCHIVE)


def run(cmd, check=True):
    # scripts/server/env.sh-тэй bash; гаралтыг шууд хэвлэнэ
    print("$", cmd, flush=True)
    p = subprocess.Popen(["bash", "-c", f"source scripts/server/env.sh && {cmd}"], stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace", bufsize=1)
    for line in p.stdout:
        print(line, end="")
    p.wait()
    if check and p.returncode:
        raise RuntimeError(f"exit {p.returncode}: {cmd}")
    return p.returncode"""

CODE_GPU = r"""# GPU-г таньж vLLM-ийн тохиргоог тааруулна (torch-ийг энд import хийхгүй: bootstrap нь vLLM-ийн torch-ийг суулгана)
q = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,compute_cap", "--format=csv,noheader,nounits"],
                   capture_output=True, text=True)
assert q.returncode == 0 and q.stdout.strip(), "GPU алга — Kaggle: Accelerator = GPU T4 x2; Colab: Runtime → GPU"
gpus = [[x.strip() for x in line.split(",")] for line in q.stdout.strip().splitlines()]
name, mem_gb, cap = gpus[0][0], float(gpus[0][1]) / 1024, float(gpus[0][2])
n = len(gpus)
print(f"{name} × {n}, {mem_gb:.0f} GB, compute {cap}")
os.environ["T2S_PG_PASSWORD"] = "t2s_nb_admin"
os.environ["VLLM_USE_FLASHINFER_SAMPLER"] = "0"   # nvcc-гүй орчинд FlashInfer JIT унадаг
for k in [k for k in os.environ if k.startswith("T2S_VLLM_ARGS")]:
    del os.environ[k]
if cap < 7.0:
    raise SystemExit("Энэ GPU-г (P100 г.м.) vLLM дэмждэггүй — Kaggle-д «GPU T4 x2» сонго")
if cap < 8.0:          # T4: BF16/FP8 алга → fp16, бүх GPU-д хуваана
    if n * mem_gb < 28:
        raise SystemExit("Нэг T4 (16 GB)-д 7–9B загвар багтахгүй — Kaggle «GPU T4 x2» эсвэл Colab L4/A100")
    base = f"--dtype half --tensor-parallel-size {n} --max-model-len 32768 --gpu-memory-utilization 0.90"
    mm = ' --limit-mm-per-prompt {"image":0,"video":0}'
    os.environ["T2S_VLLM_ARGS"] = base
    os.environ["T2S_VLLM_ARGS__sqrl_9b"] = base + mm
    os.environ["T2S_VLLM_ARGS__qwen3_5_9b"] = base + mm
    os.environ["T2S_LLM_TIMEOUT_S"] = "1200"   # T4 удаан: урт reasoning-д 180 с хүрэлцэхгүй
    print("T4 горим:", base, "— тайланд «T4, fp16» гэж тэмдэглэнэ")
else:
    print("Төлөвлөгөөний тохиргоо (config/models.yaml) хэвээр")"""

CODE_BOOT = r"""# PostgreSQL 16, Python хамаарал (vLLM), DB (seed 42), test-suite DB, мэдлэгийн индекс — ~10–15 мин
run("bash scripts/notebook/bootstrap.sh")"""

CODE_ARCHIVE = r"""def save_archive():
    tmp = OUT / (ARCHIVE + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for top in ("results", "build"):
            for p in (REPO / top).rglob("*"):
                if p.is_file() and not p.name.endswith((".pid", ".tmp")):
                    z.write(p, p.relative_to(REPO).as_posix())
    os.replace(tmp, OUT / ARCHIVE)
    print(f"[архив] {OUT / ARCHIVE} ({(OUT / ARCHIVE).stat().st_size / 1e6:.1f} MB) {time.strftime('%H:%M')}",
          flush=True)


_stop = threading.Event()


def _loop():
    while not _stop.wait(ARCHIVE_EVERY_MIN * 60):
        try:
            save_archive()
        except Exception as e:  # архивын алдаа тестийг зогсоохгүй
            print("[архив] алдаа:", e, flush=True)


threading.Thread(target=_loop, daemon=True).start()
print(f"Архив {ARCHIVE_EVERY_MIN} мин тутам: {OUT / ARCHIVE}")"""

CODE_RUN = r"""for k, v in {"STAGE": STAGE, "MODELS": MODELS, "BEST_A": BEST_A, "BEST_CONFIG": BEST_CONFIG,
             "BEST_SFT": BEST_SFT, "TEST_IDS": TEST_IDS}.items():
    if v:
        os.environ[k] = v
    else:
        os.environ.pop(k, None)
try:
    if MODE == "smoke":
        m = MODELS.split()[0]
        run(f"bash scripts/server/serve_model.sh {m}")
        run(f"t2s eval --run A6 --model {m} --split dev --limit 10 --tag smoke")
    else:
        run("bash scripts/server/run_plan_l4.sh")
finally:
    run("bash scripts/server/serve_model.sh stop", check=False)
    run("t2s report > /dev/null", check=False)
    save_archive()"""

CODE_SHOW = r"""rep = REPO / "results" / "REPORT.md"
print(rep.read_text(encoding="utf-8")[:6000] if rep.exists() else "REPORT.md алга")
print("\nҮр дүнгийн файл:", OUT / ARCHIVE)
print("Локал руу: архивыг татаад text2sql-new/ дотор задлаад `python -m mn_text2sql report-latex` → report/build.sh")"""


def cell(kind: str, src: str) -> dict:
    c = {"cell_type": kind, "metadata": {}, "source": src.splitlines(keepends=True)}
    if kind == "code":
        c.update(execution_count=None, outputs=[])
    return c


def main() -> None:
    nb = {"cells": [cell("markdown", MD_INTRO), cell("code", CODE_SETTINGS), cell("code", CODE_SETUP),
                    cell("code", CODE_GPU), cell("code", CODE_BOOT), cell("code", CODE_ARCHIVE), cell("code", CODE_RUN),
                    cell("code", CODE_SHOW)],
          "metadata": {"kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
                       "language_info": {"name": "python"}, "accelerator": "GPU"},
          "nbformat": 4, "nbformat_minor": 5}
    out = ROOT / "notebooks" / "t2s_eval.ipynb"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(nb, ensure_ascii=False, indent=1) + "\n", encoding="utf-8", newline="\n")
    print(out)


if __name__ == "__main__":
    main()
