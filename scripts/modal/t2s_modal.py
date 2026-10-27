"""Modal.com дээр туршилт (L4) ба сургалт (A100 80 GB) — серверийн скриптүүдийг (scripts/server/*.sh) хэвээр нь ажиллуулна.

Нэг удаа:  pip install modal && modal setup        (~/.modal.toml-д token байвал алгасна)

  modal run scripts/modal/t2s_modal.py --action smoke                         # ~$1: PG, DB, индекс, vLLM, A6 × 10 асуулт
  modal run --detach scripts/modal/t2s_modal.py --action eval --stage A       # A1/A4–A7 (L4)
  modal run scripts/modal/t2s_modal.py --action report                        # results/REPORT.md → BEST_A сонгох
  modal run --detach scripts/modal/t2s_modal.py --action train --model arctic-r1-7b --steps data --best-a A6
  modal run --detach scripts/modal/t2s_modal.py --action train --model arctic-r1-7b --steps sample --best-a A6
  modal run --detach scripts/modal/t2s_modal.py --action train --model arctic-r1-7b --steps "build sft" --best-a A6 \
      --recipes "c1 c2 c3"
  modal run --detach scripts/modal/t2s_modal.py --action eval-adapters --model arctic-r1-7b --best-a A6
  modal run --detach scripts/modal/t2s_modal.py --action train --model arctic-r1-7b --steps grpo --best-a A6 --best-sft C3 \
      --grpo-recipes c6
  modal volume get t2s-work results ./results_modal                          # үр дүнг локал руу

Компьютерээс бүрэн хамааралгүй (зөвлөмж): `modal deploy scripts/modal/t2s_modal.py` нэг удаа, дараа нь
`python scripts/modal/launch.py ...` (deploy хийсэн функцийг spawn хийнэ; терминал хаасан ч цуцлагдахгүй).
--detach: локал терминал хаагдсан ч ажил үргэлжилнэ (`modal app logs t2s` эсвэл вэб самбараас харна).
Тасарвал/timeout болвол ижил командыг дахин ажиллуулна: тест дууссан асуултыг, орчуулга ба sampling дууссан мөрийг,
SFT/GRPO дууссан жорыг алгасаж, эхлээгүйг сүүлийн checkpoint-оос үргэлжлүүлнэ.

Хадгалалт: Volume `t2s-work` — results/, adapters/, build/ (индекс, кэш, лог), data/train/. Volume `t2s-hf-cache` — HF
загварууд. PostgreSQL-ийн DB-г контейнер бүрт детерминистээр (seed 42) дахин үүсгэнэ (~1–4 мин).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import threading
from pathlib import Path

import modal

_HERE = Path(__file__).resolve()
# Локал: <repo>/scripts/modal/t2s_modal.py. Контейнер дотор Modal энэ файлыг /root/-д байрлуулдаг (parents[2] алга).
REPO = _HERE.parents[2] if len(_HERE.parents) > 2 else Path("/root/t2s")
ROOT = "/root/t2s"
VOL = "/vol"
HF = "/root/.cache/huggingface"
PG_BIN = "/usr/lib/postgresql/16/bin"
PG_DATA = "/tmp/pgdata"
PG_PASSWORD = "t2s_modal_admin"          # зөвхөн контейнер доторх localhost — гадагш нээгдэхгүй
PERSIST = ["results", "adapters", "build", "data/train", "data/train_raw"]   # train_raw: BIRD DB (sampling-д)
IGNORE = [".venv", ".git", ".claude", "build", "results", "adapters", "data/train", "data/train_raw", "report/out", "**/__pycache__",
          "**/*.pyc", ".pytest_cache", ".ruff_cache"]
GPU_EVAL = "L4"                           # туршилтын төлөвлөгөө: тест L4 (24 GB), sqrl FP8
GPU_TRAIN = "A100-80GB"                   # сургалт A100 80 GB (H100-аар солиж болно: --gpu H100)
DAY = 24 * 3600

ENV = {"T2S_ROOT": ROOT, "PYTHONPATH": f"{ROOT}/src", "PYTHONIOENCODING": "utf-8", "HF_HOME": HF,
       "T2S_PG_PASSWORD": PG_PASSWORD, "T2S_PG_PORT": "5432",
       "TOKENIZERS_PARALLELISM": "false",
       # image-д nvcc алга: FlashInfer sampler JIT эмхэтгэл хийж чадахгүй тул PyTorch-ийн sampler-ийг ашиглана
       "VLLM_USE_FLASHINFER_SAMPLER": "0",
       "T2S_LLM_TIMEOUT_S": "1200"}   # урт reasoning (3k токен) 180 с-ээс хэтэрдэг (smoke-оор ажиглав)

image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("curl", "ca-certificates", "gnupg", "lsb-release", "procps", "build-essential", "git", "locales")
    .run_commands(
        "install -d /usr/share/postgresql-common/pgdg",
        "curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc "
        "https://www.postgresql.org/media/keys/ACCC4CF8.asc",
        'echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] '
        'https://apt.postgresql.org/pub/repos/apt $(lsb_release -cs)-pgdg main" > /etc/apt/sources.list.d/pgdg.list',
        "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y postgresql-16",
    )
    .pip_install_from_requirements(str(REPO / "requirements.txt"))
    .pip_install_from_requirements(str(REPO / "requirements-gpu.txt"))
    .env(ENV)
    .add_local_dir(str(REPO), remote_path=ROOT, ignore=IGNORE)
)

app = modal.App("t2s", image=image)
work = modal.Volume.from_name("t2s-work", create_if_missing=True)
hf_cache = modal.Volume.from_name("t2s-hf-cache", create_if_missing=True)
VOLUMES = {VOL: work, HF: hf_cache}


# ----------------------------------------------------------------------------- контейнер доторх бэлтгэл
def _sh(cmd: str, check: bool = True) -> int:
    """scripts/server/env.sh-тэй bash (t2s, t2s_train, log функцүүд) — гаралт шууд Modal-ийн лог руу."""
    print(f"$ {cmd}", flush=True)
    r = subprocess.run(["bash", "-c", f"source scripts/server/env.sh && {cmd}"], cwd=ROOT)
    if check and r.returncode != 0:
        raise RuntimeError(f"команд амжилтгүй (exit {r.returncode}): {cmd}")
    return r.returncode


def _link_persistent() -> None:
    for rel in PERSIST:
        src, dst = Path(VOL) / rel, Path(ROOT) / rel
        src.mkdir(parents=True, exist_ok=True)
        if dst.is_symlink():
            continue
        if dst.exists():
            shutil.rmtree(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.symlink_to(src)
    for pid in (Path(ROOT) / "build").glob("vllm_*.pid"):      # өмнөх контейнерийн PID — өөр процессыг алахгүй
        pid.unlink()


def _start_postgres() -> None:
    st = subprocess.run(["su", "postgres", "-c", f"{PG_BIN}/pg_ctl -D {PG_DATA} status"], capture_output=True)
    if st.returncode == 0:   # дахин оролдлого (retry) ижил контейнерт — аль хэдийн ажиллаж байна
        return
    if not Path(PG_DATA).exists():
        Path(PG_DATA).mkdir(parents=True)
        Path("/tmp/pgpw").write_text(PG_PASSWORD)
        subprocess.run(["chown", "-R", "postgres:postgres", PG_DATA, "/tmp/pgpw"], check=True)
        subprocess.run(["su", "postgres", "-c", f"{PG_BIN}/initdb -D {PG_DATA} -E UTF8 --locale=C.UTF-8 -U postgres "
                        "--auth-local=trust --auth-host=scram-sha-256 --pwfile=/tmp/pgpw"], check=True)
    opts = "-p 5432 -c max_connections=200 -c shared_buffers=2GB -c work_mem=64MB -c listen_addresses=localhost"
    subprocess.run(["su", "postgres", "-c", f'{PG_BIN}/pg_ctl -D {PG_DATA} -o "{opts}" -l /tmp/pg.log -w -t 120 start'],
                   check=True)


def _knowledge_sha() -> str:
    h = hashlib.sha1()
    for p in sorted((Path(ROOT) / "knowledge").rglob("*")) + sorted((Path(ROOT) / "evalset").glob("*.jsonl")):
        if p.is_file():
            h.update(p.name.encode())
            h.update(p.read_bytes())
    return h.hexdigest()[:12]


def _boot(testsuite: bool, index: bool) -> None:
    """Volume-ийн хавтсуудыг холбох → PostgreSQL → DB (seed 42) → (сонголтоор) test-suite DB, мэдлэгийн индекс."""
    os.chdir(ROOT)
    _link_persistent()
    _start_postgres()
    _sh("t2s db-build")
    _sh("t2s db-validate > build/db_validate.json")
    if testsuite:
        _sh("t2s testsuite-build > build/testsuite_build.json")
    if index:
        marker = Path(ROOT) / "build" / "index" / ".knowledge_sha"
        sha = _knowledge_sha()
        if not marker.exists() or marker.read_text().strip() != sha:
            _sh("T2S_EMBED_DEVICE=auto t2s index-build")       # vLLM асахаас өмнө GPU-г ашиглана
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(sha)
            work.commit()
    _sh("nvidia-smi --query-gpu=name,memory.total --format=csv,noheader || true", check=False)


def _committer(stop: threading.Event, every_s: int = 300) -> None:
    """Урт ажлын явцад Volume-ийг 10 мин тутам хадгална (контейнер унавал checkpoint, кэш алдагдахгүй)."""
    while not stop.wait(every_s):
        try:
            work.commit()
        except Exception as e:  # noqa: BLE001 — commit-ийн алдаа ажлыг зогсоохгүй
            print(f"[volume] commit алгасав: {e}", flush=True)


def _run(cmd: str, env: dict, testsuite: bool, index: bool) -> None:
    os.environ.update({k: str(v) for k, v in env.items() if v not in (None, "")})
    _boot(testsuite=testsuite, index=index)
    stop = threading.Event()
    t = threading.Thread(target=_committer, args=(stop,), daemon=True)
    t.start()
    try:
        _sh(cmd)
    finally:
        stop.set()
        _sh("bash scripts/server/serve_model.sh stop", check=False)
        work.commit()
        hf_cache.commit()


RETRY = modal.Retries(max_retries=1, initial_delay=30.0)   # тасалдал/timeout: нэг удаа checkpoint-оос үргэлжлүүлнэ


@app.function(gpu=GPU_EVAL, volumes=VOLUMES, timeout=DAY, retries=RETRY, cpu=4, memory=16384)
def run_gpu(cmd: str, env: dict, testsuite: bool = True, index: bool = True) -> None:
    _run(cmd, env, testsuite, index)


@app.function(gpu=GPU_TRAIN, volumes=VOLUMES, timeout=DAY, retries=RETRY, cpu=6, memory=49152)
def run_a100(cmd: str, env: dict, testsuite: bool = False, index: bool = True) -> None:
    _run(cmd, env, testsuite, index)


@app.function(volumes=VOLUMES, timeout=2 * 3600, cpu=4, memory=8192)
def run_cpu(cmd: str) -> None:
    os.chdir(ROOT)
    _link_persistent()
    _sh(cmd)
    work.commit()


# ----------------------------------------------------------------------------- локал командууд
def _env(**kw) -> dict:
    keys = {"stage": "STAGE", "models": "MODELS", "best_a": "BEST_A", "best_sft": "BEST_SFT",
            "best_config": "BEST_CONFIG", "steps": "STEPS", "recipes": "RECIPES", "grpo_recipes": "GRPO_RECIPES",
            "test_ids": "TEST_IDS"}
    return {keys[k]: v for k, v in kw.items() if v}


@app.local_entrypoint()
def main(action: str = "smoke", stage: str = "", model: str = "", models: str = "", steps: str = "", best_a: str = "",
         best_sft: str = "", best_config: str = "", recipes: str = "", grpo_recipes: str = "", test_ids: str = "",
         gpu: str = "", cmd: str = "", limit: int = 10):
    env = _env(stage=stage, models=models, best_a=best_a, best_sft=best_sft, best_config=best_config, steps=steps,
               recipes=recipes, grpo_recipes=grpo_recipes, test_ids=test_ids)
    if action == "smoke":
        job = ("python -c \"import torch, vllm, trl, peft; print('torch', torch.__version__, 'cuda', "
               "torch.cuda.is_available(), 'vllm', vllm.__version__, 'trl', trl.__version__)\" && "
               "bash scripts/server/serve_model.sh arctic-r1-7b && "
               f"t2s eval --run A6 --model arctic-r1-7b --split dev --limit {int(limit)} --tag smoke && "
               "t2s report > /dev/null && grep -A3 -m1 'A6' results/REPORT.md || true")
        run_gpu.with_options(gpu=gpu or GPU_EVAL).remote(job, env, testsuite=False, index=True)
    elif action == "eval":
        if not stage:
            raise SystemExit("--stage caches|A|B|F|E|G|thesis|test заавал (scripts/server/run_plan_l4.sh)")
        run_gpu.with_options(gpu=gpu or GPU_EVAL).remote("bash scripts/server/run_plan_l4.sh", env)
    elif action == "eval-adapters":
        if not (model and best_a):
            raise SystemExit("--model ба --best-a заавал")
        run_gpu.with_options(gpu=gpu or GPU_EVAL).remote(f"bash scripts/server/eval_adapters.sh {model}", env)
    elif action == "train":
        if not (model and steps and best_a):
            raise SystemExit("--model, --steps ('data' | 'sample' | 'build sft' | 'grpo'), --best-a заавал")
        (run_a100.with_options(gpu=gpu) if gpu else run_a100).remote(f"bash scripts/server/train_a100.sh {model}", env,
                                                          testsuite=False)
    elif action == "report":
        run_cpu.remote("t2s report && tail -n 60 results/REPORT.md")
    elif action == "shell":
        if not cmd:
            raise SystemExit("--cmd '<bash команд>' заавал")
        run_gpu.with_options(gpu=gpu or GPU_EVAL).remote(cmd, env)
    else:
        raise SystemExit(f"тодорхойгүй --action {action}: smoke | eval | eval-adapters | train | report | shell")
