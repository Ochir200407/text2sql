"""scripts/notebook/merge_lora.py: W + (alpha/r)·B·A-г fp32-оор бодоод fp16 болгодог эсэх (numpy-ийн жижиг загвар)."""
import importlib.util
import json
import struct
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("merge_lora", ROOT / "scripts" / "notebook" / "merge_lora.py")
ml = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ml)


def _bf16(x: np.ndarray) -> bytes:
    return (x.astype("<f4").view("<u4") >> 16).astype("<u2").tobytes()


def _write(path: Path, tensors: dict[str, tuple[str, np.ndarray]]) -> None:
    header, chunks, off = {}, [], 0
    for name, (dtype, arr) in tensors.items():
        raw = _bf16(arr) if dtype == "BF16" else arr.astype({"F16": "<f2", "F32": "<f4"}[dtype]).tobytes()
        header[name] = {"dtype": dtype, "shape": list(arr.shape), "data_offsets": [off, off + len(raw)]}
        chunks.append(raw)
        off += len(raw)
    hb = json.dumps(header).encode()
    with open(path, "wb") as f:
        f.write(struct.pack("<Q", len(hb)) + hb + b"".join(chunks))


def _read(path: Path) -> dict[str, np.ndarray]:
    header, base = ml.read_header(path)
    return {n: ml.load_tensor(path, header, base, n) for n in header if n != "__metadata__"}


def test_merge_matches_fp32_reference(tmp_path):
    rng = np.random.default_rng(0)
    base, adapter, out = tmp_path / "base", tmp_path / "adp", tmp_path / "out"
    base.mkdir()
    adapter.mkdir()
    q = rng.normal(0, 0.02, (8, 6)).astype("<f4")
    down = rng.normal(0, 0.02, (6, 10)).astype("<f4")
    norm = np.ones(6, dtype="<f4")
    _write(base / "model-00001-of-00002.safetensors",
           {"model.layers.0.self_attn.q_proj.weight": ("BF16", q), "model.norm.weight": ("BF16", norm)})
    _write(base / "model-00002-of-00002.safetensors", {"model.layers.0.mlp.down_proj.weight": ("BF16", down)})
    (base / "config.json").write_text(json.dumps({"torch_dtype": "bfloat16"}), encoding="utf-8")
    (base / "model.safetensors.index.json").write_text(json.dumps({"metadata": {}, "weight_map": {}}),
                                                       encoding="utf-8")
    (base / "tokenizer.json").write_text("{}", encoding="utf-8")
    r, alpha = 4, 8
    a_q, b_q = rng.normal(0, 0.1, (r, 6)), rng.normal(0, 0.01, (8, r))
    a_d, b_d = rng.normal(0, 0.1, (r, 10)), rng.normal(0, 0.01, (6, r))
    pre = "base_model.model.model.layers.0"
    _write(adapter / "adapter_model.safetensors", {
        f"{pre}.self_attn.q_proj.lora_A.weight": ("F16", a_q), f"{pre}.self_attn.q_proj.lora_B.weight": ("F16", b_q),
        f"{pre}.mlp.down_proj.lora_A.weight": ("F32", a_d), f"{pre}.mlp.down_proj.lora_B.weight": ("F32", b_d)})
    (adapter / "adapter_config.json").write_text(json.dumps({"r": r, "lora_alpha": alpha}), encoding="utf-8")

    info = ml.merge(base, adapter, out)
    assert info["modules"] == 2 and info["scale"] == 2.0 and info["clipped"] == 0
    got = _read(out / "model-00001-of-00002.safetensors") | _read(out / "model-00002-of-00002.safetensors")
    bq = ml.to_f32(np.frombuffer(_bf16(q), dtype=np.uint8), "BF16").reshape(q.shape)
    bd = ml.to_f32(np.frombuffer(_bf16(down), dtype=np.uint8), "BF16").reshape(down.shape)
    want_q = bq + 2.0 * (b_q.astype("<f2").astype("<f4") @ a_q.astype("<f2").astype("<f4"))
    want_d = bd + 2.0 * (b_d.astype("<f4") @ a_d.astype("<f4"))
    np.testing.assert_allclose(got["model.layers.0.self_attn.q_proj.weight"], want_q, rtol=1e-3, atol=1e-6)
    np.testing.assert_allclose(got["model.layers.0.mlp.down_proj.weight"], want_d, rtol=1e-3, atol=1e-6)
    np.testing.assert_array_equal(got["model.norm.weight"], norm)
    # delta нь bf16-ийн дугуйлалтаас том тул fp16 гаралтад хадгалагдана (bf16 merge-д алдагддаг)
    assert np.abs(got["model.layers.0.self_attn.q_proj.weight"] - bq).max() > 0
    assert json.loads((out / "config.json").read_text(encoding="utf-8"))["torch_dtype"] == "float16"
    assert (out / "tokenizer.json").exists()
    assert json.loads((out / "model.safetensors.index.json").read_text(encoding="utf-8"))["metadata"]["total_size"] \
        == info["bytes"]


def test_merge_rejects_unknown_module(tmp_path):
    base, adapter = tmp_path / "base", tmp_path / "adp"
    base.mkdir()
    adapter.mkdir()
    _write(base / "model.safetensors", {"model.layers.0.self_attn.q_proj.weight": ("BF16", np.zeros((2, 2)))})
    _write(adapter / "adapter_model.safetensors", {
        "base_model.model.model.layers.9.self_attn.q_proj.lora_A.weight": ("F32", np.zeros((1, 2))),
        "base_model.model.model.layers.9.self_attn.q_proj.lora_B.weight": ("F32", np.zeros((2, 1)))})
    (adapter / "adapter_config.json").write_text(json.dumps({"r": 1, "lora_alpha": 1}), encoding="utf-8")
    with pytest.raises(SystemExit):
        ml.merge(base, adapter, tmp_path / "out")
