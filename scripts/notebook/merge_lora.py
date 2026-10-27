"""LoRA adapter-ийг суурь загварт fp32-оор нэгтгэж fp16 safetensors болгоно (зөвхөн numpy; torch/peft шаардахгүй).

    python scripts/notebook/merge_lora.py --base <HF id эсвэл хавтас> --adapter adapters/d2__arctic-r1-7b \
        --out adapters/d2__arctic-r1-7b__merged16 [--revision main]

Яагаад: T4 (Kaggle) дээр vLLM-ийн LoRA kernel ~3 дахин удаан (C1: LoRA-тай 22.7 с/асуулт, A1: LoRA-гүй 7.4 с).
Нэгтгэсэн жинтэй бол LoRA-гүй загвар шиг хурдан. PEFT-ийн merge (bf16) нь жижиг delta-г bf16-ийн 8 битийн
нарийвчлалд дугуйлж алддаг тул энд W + scale·B·A-г fp32-оор бодоод fp16 (10 бит) болгоно.
Санах ой: тензор нэг нэгээр (memmap) — 7B загварт ~3 GB-аас бага.
"""
from __future__ import annotations

import argparse
import json
import shutil
import struct
from pathlib import Path

import numpy as np

F16_MAX = 65504.0


def read_header(path: Path) -> tuple[dict, int]:
    with open(path, "rb") as f:
        n = struct.unpack("<Q", f.read(8))[0]
        return json.loads(f.read(n)), 8 + n


def to_f32(raw: np.ndarray, dtype: str) -> np.ndarray:
    if dtype == "BF16":   # bf16 = fp32-ийн дээд 16 бит
        return (raw.view("<u2").astype("<u4") << 16).view("<f4")
    if dtype == "F16":
        return raw.view("<f2").astype("<f4")
    if dtype == "F32":
        return raw.view("<f4").copy()
    raise ValueError(f"дэмжихгүй dtype: {dtype}")


def load_tensor(path: Path, header: dict, base: int, name: str) -> np.ndarray:
    info = header[name]
    a, b = info["data_offsets"]
    raw = np.memmap(path, dtype=np.uint8, mode="r", offset=base + a, shape=(b - a,))
    return to_f32(np.asarray(raw), info["dtype"]).reshape(info["shape"])


def lora_deltas(adapter: Path) -> tuple[dict[str, tuple[np.ndarray, np.ndarray]], float]:
    cfg = json.loads((adapter / "adapter_config.json").read_text(encoding="utf-8"))
    if cfg.get("use_dora") or cfg.get("fan_in_fan_out") or cfg.get("modules_to_save") or cfg.get("rank_pattern") \
            or cfg.get("alpha_pattern"):
        raise SystemExit("энэ скрипт энгийн LoRA-г л нэгтгэнэ (DoRA/fan_in_fan_out/modules_to_save/pattern алга байх)")
    r = int(cfg["r"])
    scale = float(cfg["lora_alpha"]) / (r ** 0.5 if cfg.get("use_rslora") else r)
    path = adapter / "adapter_model.safetensors"
    header, base = read_header(path)
    pairs: dict[str, dict[str, np.ndarray]] = {}
    for name in header:
        if name == "__metadata__":
            continue
        for part in ("lora_A", "lora_B"):
            tag = f".{part}.weight"
            if name.endswith(tag):
                mod = name[: -len(tag)]
                mod = mod.removeprefix("base_model.model.")
                pairs.setdefault(mod, {})[part] = load_tensor(path, header, base, name)
                break
        else:
            raise SystemExit(f"adapter-д танихгүй тензор: {name}")
    out = {}
    for mod, d in pairs.items():
        if set(d) != {"lora_A", "lora_B"}:
            raise SystemExit(f"{mod}: lora_A/lora_B хос дутуу")
        out[mod + ".weight"] = (d["lora_A"], d["lora_B"])
    return out, scale


def resolve_base(base: str, revision: str | None) -> Path:
    p = Path(base)
    if p.is_dir():
        return p
    from huggingface_hub import snapshot_download
    return Path(snapshot_download(base, revision=revision,
                                  allow_patterns=["*.json", "*.safetensors", "*.txt", "*.model", "*.jinja"]))


def merge(base_dir: Path, adapter: Path, out: Path) -> dict:
    deltas, scale = lora_deltas(adapter)
    out.mkdir(parents=True, exist_ok=True)
    shards = sorted(base_dir.glob("*.safetensors"))
    if not shards:
        raise SystemExit(f"{base_dir}: *.safetensors алга")
    used, total, clipped = set(), 0, 0
    for shard in shards:
        header, base = read_header(shard)
        names = [n for n in header if n != "__metadata__"]
        out_header, off = {}, 0
        for n in names:
            size = int(np.prod(header[n]["shape"])) * 2
            out_header[n] = {"dtype": "F16", "shape": header[n]["shape"], "data_offsets": [off, off + size]}
            off += size
        out_header["__metadata__"] = {"format": "pt"}
        hb = json.dumps(out_header, separators=(",", ":")).encode()
        hb += b" " * ((8 - len(hb) % 8) % 8)
        with open(out / shard.name, "wb") as f:
            f.write(struct.pack("<Q", len(hb)))
            f.write(hb)
            for n in names:
                w = load_tensor(shard, header, base, n)
                if n in deltas:
                    a, b = deltas[n]
                    if w.shape != (b.shape[0], a.shape[1]):
                        raise SystemExit(f"{n}: хэмжээс таарахгүй {w.shape} vs {(b.shape[0], a.shape[1])}")
                    w = w + scale * (b @ a)
                    used.add(n)
                big = np.abs(w) > F16_MAX
                if big.any():
                    clipped += int(big.sum())
                    w = np.clip(w, -F16_MAX, F16_MAX)
                f.write(w.astype("<f2").tobytes())
        total += off
    missing = sorted(set(deltas) - used)
    if missing:
        raise SystemExit(f"суурь загварт олдоогүй LoRA модуль {len(missing)}: {missing[:3]}")
    for p in base_dir.iterdir():
        if p.is_file() and p.suffix != ".safetensors" and p.name != "model.safetensors.index.json":
            shutil.copy2(p, out / p.name)
    idx = base_dir / "model.safetensors.index.json"
    if idx.exists():
        j = json.loads(idx.read_text(encoding="utf-8"))
        j.setdefault("metadata", {})["total_size"] = total
        (out / idx.name).write_text(json.dumps(j, indent=2), encoding="utf-8")
    cfg_path = out / "config.json"
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        cfg["torch_dtype"] = "float16"
        cfg_path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    info = {"base": str(base_dir), "adapter": str(adapter), "modules": len(used), "scale": scale,
            "bytes": total, "clipped": clipped}
    (out / "t2s_merge.json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    return info


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--adapter", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--revision")
    a = ap.parse_args()
    print(json.dumps(merge(resolve_base(a.base, a.revision), Path(a.adapter), Path(a.out)), ensure_ascii=False))


if __name__ == "__main__":
    main()
