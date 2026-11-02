"""Сургалтын гарал үүсэл (B10): t2s_meta.json-д кодын хувилбар, сангийн хувилбар, өгөгдлийн sha256.

torch/transformers-ийг import хийхгүй (importlib.metadata) — CPU тест ба Kaggle-ийн аль алинд ажиллана.
Kaggle-д код zip-ээр (scripts/notebook/pack_code.py, .git-гүй) очдог тул git commit алга байж болно — тэр үед ч
tree_sha1 (src/*.py + config/*.yaml + knowledge/*.yaml, eval/runner.code_version-тэй ижил дүрэм) хадгалагдана.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
from importlib import metadata
from pathlib import Path

from ..config import ROOT

LIBS = ("torch", "transformers", "trl", "peft", "datasets", "accelerate", "bitsandbytes", "vllm", "numpy",
        "sqlglot")


def file_sha256(path) -> str | None:
    """Файлын sha256 (байхгүй бол None) — хэсэгчлэн уншина."""
    p = Path(path)
    if not p.is_file():
        return None
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rows_sha256(rows: list[dict]) -> str:
    """Санах ойн dataset-ийн (GRPO) тогтмол sha256: мөр бүр sort_keys JSON, дараалал хадгалагдана."""
    h = hashlib.sha256()
    for r in rows:
        h.update(json.dumps(r, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8") + b"\n")
    return h.hexdigest()


def tree_sha1(root: Path = ROOT) -> str:
    """Эх кодын hash (eval/runner.code_version-ийн git-гүй үеийн дүрэмтэй ижил: 12 тэмдэгт)."""
    h = hashlib.sha1()
    for p in sorted(list((root / "src").rglob("*.py")) + list((root / "config").glob("*.yaml"))
                    + list((root / "knowledge").glob("*.yaml"))):
        h.update(p.relative_to(root).as_posix().encode())
        h.update(p.read_bytes())
    return h.hexdigest()[:12]


def code_version(root: Path = ROOT) -> dict:
    """{"commit": git HEAD | None, "dirty": src/config/knowledge/scripts-д commit хийгдээгүй өөрчлөлт | None,
    "tree_sha1": ...}. git алга/алдаа бол commit = None."""
    out: dict = {"commit": None, "dirty": None}
    try:
        r = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10)
        if r.returncode == 0 and r.stdout.strip():
            out["commit"] = r.stdout.strip()
            st = subprocess.run(["git", "-C", str(root), "status", "--porcelain", "--", "src", "config", "knowledge",
                                 "scripts"], capture_output=True, text=True, timeout=10)
            out["dirty"] = bool(st.stdout.strip()) if st.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        out["tree_sha1"] = tree_sha1(root)
    except OSError as e:   # pragma: no cover - эх код уншигдахгүй орчин
        out["tree_sha1"] = None
        out["tree_error"] = str(e)
    return out


def lib_versions(names: tuple[str, ...] = LIBS) -> dict:
    """Суулгасан сангийн хувилбар (import хийхгүй); байхгүй бол None. python, платформ, (torch аль хэдийн
    ачаалагдсан бол) CUDA ба GPU-ийн нэр."""
    out: dict = {}
    for n in names:
        try:
            out[n] = metadata.version(n)
        except metadata.PackageNotFoundError:
            out[n] = None
    out["python"] = platform.python_version()
    out["platform"] = platform.platform()
    torch = sys.modules.get("torch")
    if torch is not None:
        try:
            out["cuda"] = getattr(getattr(torch, "version", None), "cuda", None)
            if torch.cuda.is_available():
                out["gpus"] = [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]
        except Exception:  # noqa: BLE001 — meta бичихэд сургалт унахгүй
            pass
    return out


def model_revision(mspec: dict) -> dict:
    """L2: загварын HF commit — eval/runner.model_revision (T2S_MODEL_REVISION → HF кэшийн snapshot → models.yaml-ийн
    revision; үнэлгээний meta-тай ижил). eval-ийн импорт (psycopg2 г.м.) боломжгүй орчинд env/models.yaml-ийн утга —
    meta бичихэд сургалт унахгүй."""
    try:
        from ..eval.runner import model_revision as _eval_model_revision
        return _eval_model_revision(mspec)
    except Exception as e:  # noqa: BLE001
        return {"hf_id": mspec.get("hf_id"),
                "revision": os.environ.get("T2S_MODEL_REVISION") or mspec.get("revision"),
                "error": f"{type(e).__name__}: {e}"[:200]}


def sampling_settings(tcfg: dict) -> dict:
    """L2: сургалтын өгөгдлийг үүсгэсэн sampling (C1) ба CoT (C2)-ийн тохиргоо (training.yaml) + seed-ийн дүрэм
    (training.sampling.sample_seed)."""
    return {"sampling": tcfg.get("sampling"), "cot": tcfg.get("cot"),
            "seed": "sha256(item_id|variant|free/hint|call#)[:31 бит] — sqrl: + траекторын дугаар"}


def provenance(**files) -> dict:
    """t2s_meta.json-ийн нэмэлт: {"code": ..., "libs": ..., "data_sha256": {нэр: sha256}} (None утгатай файлыг алгасна)."""
    return {"code": code_version(), "libs": lib_versions(),
            "data_sha256": {k: file_sha256(v) for k, v in files.items() if v}}


def effective_batch(per_device: int, grad_acc: int, n_gpu: int = 1, world_size: int = 1) -> int:
    """Нэг optimizer алхмын мөрийн тоо. torchrun-гүй олон GPU-д Trainer DataParallel хэрэглэдэг (n_gpu > 1,
    world_size = 1) — per_device × n_gpu; DDP-д (torchrun) процесс бүр n_gpu = 1, world_size = процессын тоо."""
    return int(per_device) * max(1, int(n_gpu or 1)) * int(grad_acc) * max(1, int(world_size or 1))
