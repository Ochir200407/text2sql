"""Сургалтын засвар (2026-10-30), 2-р хэсэг: B6 (fp32-оор нэгтгээд dtype руу), B9 (run_sft --base, validation,
early stopping), B10 (t2s_meta.json-ийн гарал үүсэл), B5 (run_grpo-ийн init), B7 (train_mt.py-ийн batch/warmup).
torch/transformers/trl/peft/datasets локалд алга — хуурамч модулиар (sys.modules) шалгана."""
import copy
import dataclasses
import hashlib
import importlib.util
import json
import math
import os
import struct
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from mn_text2sql.config import ROOT, load_config, load_models
from mn_text2sql.training import provenance as prov
from mn_text2sql.training import sft
from mn_text2sql.training.data import load_training_cfg

MODEL = "arctic-r1-7b"
spec = importlib.util.spec_from_file_location("merge_lora_t", ROOT / "scripts" / "notebook" / "merge_lora.py")
ml = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ml)


# --------------------------------------------------------------------------- B6: fp32 нэгтгэл → dtype

def _bf16_ref(x: float) -> float:
    """fp32 → bf16 (хамгийн ойрын тэгш рүү) — скаляр лавлагаа (merge_lora-ийн векторжуулсанаас тусдаа)."""
    if math.isnan(x):
        return float("nan")
    u = struct.unpack("<I", struct.pack("<f", x))[0]
    lower, rest = u >> 16, u & 0xFFFF
    if rest > 0x8000 or (rest == 0x8000 and lower & 1):
        lower += 1
    return struct.unpack("<f", struct.pack("<I", (lower & 0xFFFF) << 16))[0]


def _write_st(path: Path, tensors: dict[str, tuple[str, np.ndarray]]) -> None:
    header, chunks, off = {}, [], 0
    for name, (dtype, arr) in tensors.items():
        if dtype == "BF16":
            raw = (arr.astype("<f4").view("<u4") >> 16).astype("<u2").tobytes()
        else:
            raw = arr.astype({"F16": "<f2", "F32": "<f4"}[dtype]).tobytes()
        header[name] = {"dtype": dtype, "shape": list(arr.shape), "data_offsets": [off, off + len(raw)]}
        chunks.append(raw)
        off += len(raw)
    hb = json.dumps(header).encode()
    path.write_bytes(struct.pack("<Q", len(hb)) + hb + b"".join(chunks))


def _read_st(path: Path) -> tuple[dict, dict[str, np.ndarray]]:
    header, base = ml.read_header(path)
    return header, {n: ml.load_tensor(path, header, base, n) for n in header if n != "__metadata__"}


def _fixture(tmp_path: Path):
    rng = np.random.default_rng(1)
    base, adapter = tmp_path / "base", tmp_path / "adp"
    base.mkdir()
    adapter.mkdir()
    q = rng.normal(0, 0.02, (8, 6)).astype("<f4")
    # LoRA-гүй тензор: bf16-ийн тэнцүү зай (tie) ба онцгой утгууд
    special = np.array([1.0, 1.0 + 2 ** -8, 1.0 + 3 * 2 ** -8, 1.0 + 2 ** -8 + 2 ** -12, -2.5, np.inf, np.nan],
                       dtype="<f4")
    _write_st(base / "model.safetensors", {"model.layers.0.self_attn.q_proj.weight": ("F32", q),
                                           "model.norm.weight": ("F32", special)})
    (base / "config.json").write_text(json.dumps({"torch_dtype": "bfloat16", "dtype": "bfloat16"}), encoding="utf-8")
    (base / "tokenizer.json").write_text('{"from": "base"}', encoding="utf-8")
    (base / "tokenizer_config.json").write_text('{"from": "base"}', encoding="utf-8")
    r, alpha = 4, 8
    a, b = rng.normal(0, 0.1, (r, 6)).astype("<f4"), rng.normal(0, 0.01, (8, r)).astype("<f4")
    _write_st(adapter / "adapter_model.safetensors", {
        "base_model.model.model.layers.0.self_attn.q_proj.lora_A.weight": ("F32", a),
        "base_model.model.model.layers.0.self_attn.q_proj.lora_B.weight": ("F32", b)})
    (adapter / "adapter_config.json").write_text(json.dumps({"r": r, "lora_alpha": alpha}), encoding="utf-8")
    (adapter / "tokenizer_config.json").write_text('{"from": "adapter"}', encoding="utf-8")
    want_q = q + (alpha / r) * (b @ a)                                  # fp32 нийлбэр
    return base, adapter, want_q, special


def test_merge_lora_bf16_rounds_fp32_sum_once(tmp_path):
    base, adapter, want_q, special = _fixture(tmp_path)
    info = ml.merge(base, adapter, tmp_path / "out", out_dtype="BF16")
    assert info["dtype"] == "BF16" and info["modules"] == 1 and info["clipped"] == 0
    header, got = _read_st(tmp_path / "out" / "model.safetensors")
    assert {header[n]["dtype"] for n in got} == {"BF16"}
    ref = np.vectorize(_bf16_ref, otypes=[np.float32])
    np.testing.assert_array_equal(got["model.layers.0.self_attn.q_proj.weight"], ref(want_q))
    norm = got["model.norm.weight"]
    assert list(norm[:5]) == [1.0, 1.0, 1.015625, 1.0078125, -2.5]          # tie → тэгш, tie-ээс дээш → дээш
    assert np.isinf(norm[5]) and norm[5] > 0 and np.isnan(norm[6])
    cfg = json.loads((tmp_path / "out" / "config.json").read_text(encoding="utf-8"))
    assert cfg["torch_dtype"] == cfg["dtype"] == "bfloat16"
    # анхдагч (F16) хэвээр: config нь float16, хоёр түлхүүр ижил
    ml.merge(base, adapter, tmp_path / "o16")
    h16, g16 = _read_st(tmp_path / "o16" / "model.safetensors")
    assert h16["model.norm.weight"]["dtype"] == "F16"
    np.testing.assert_allclose(g16["model.layers.0.self_attn.q_proj.weight"], want_q, rtol=1e-3, atol=1e-6)
    cfg16 = json.loads((tmp_path / "o16" / "config.json").read_text(encoding="utf-8"))
    assert cfg16["torch_dtype"] == cfg16["dtype"] == "float16"


def test_merge_adapter_uses_fp32_numpy_merge(tmp_path, monkeypatch):
    base, adapter, want_q, _ = _fixture(tmp_path)
    for mod in ("torch", "peft", "transformers"):        # torch/PEFT-ийн bf16 merge-ийг дуудах ёсгүй
        monkeypatch.setitem(sys.modules, mod, None)
    out = tmp_path / "merged"
    assert sft.merge_adapter(MODEL, str(adapter), str(out), base=str(base)) == str(out)
    header, got = _read_st(out / "model.safetensors")
    assert header["model.layers.0.self_attn.q_proj.weight"]["dtype"] == "BF16"       # анхдагч = өмнөх dtype
    ref = np.vectorize(_bf16_ref, otypes=[np.float32])
    np.testing.assert_array_equal(got["model.layers.0.self_attn.q_proj.weight"], ref(want_q))
    # tokenizer: adapter-ийнх (өмнөх merge_adapter шиг), adapter-д байхгүй файл — суурийнх
    assert json.loads((out / "tokenizer_config.json").read_text(encoding="utf-8")) == {"from": "adapter"}
    assert json.loads((out / "tokenizer.json").read_text(encoding="utf-8")) == {"from": "base"}
    assert json.loads((out / "t2s_merge.json").read_text(encoding="utf-8"))["dtype"] == "BF16"
    sft.merge_adapter(MODEL, str(adapter), str(tmp_path / "m16"), base=str(base), dtype="fp16")
    assert _read_st(tmp_path / "m16" / "model.safetensors")[0]["model.norm.weight"]["dtype"] == "F16"
    with pytest.raises(ValueError):
        sft.merge_adapter(MODEL, str(adapter), str(tmp_path / "x"), base=str(base), dtype="int8")


# --------------------------------------------------------------------------- B10: гарал үүсэл

def test_provenance_helpers(tmp_path):
    f = tmp_path / "d.jsonl"
    f.write_text('{"a": 1}\n', encoding="utf-8")
    assert prov.file_sha256(f) == hashlib.sha256(f.read_bytes()).hexdigest() and prov.file_sha256(tmp_path / "x") is None
    rows = [{"b": 1, "a": "ө"}, {"c": 2}]
    assert prov.rows_sha256(rows) == prov.rows_sha256([{"a": "ө", "b": 1}, {"c": 2}]) != prov.rows_sha256(rows[::-1])
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / "config").mkdir()
    (root / "knowledge").mkdir()
    (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (root / "config" / "c.yaml").write_text("k: 1\n", encoding="utf-8")
    h1 = prov.tree_sha1(root)
    (root / "src" / "a.py").write_text("x = 2\n", encoding="utf-8")
    assert len(h1) == 12 and prov.tree_sha1(root) != h1
    cv = prov.code_version(root)                     # git-гүй хавтас: commit None, tree hash байна
    assert cv["commit"] is None and cv["tree_sha1"] == prov.tree_sha1(root)
    real = prov.code_version()
    assert set(real) >= {"commit", "dirty", "tree_sha1"} and len(real["tree_sha1"]) == 12
    libs = prov.lib_versions()
    assert set(prov.LIBS) <= set(libs) and libs["numpy"] == np.__version__ and libs["python"]
    p = prov.provenance(train=f, eval=None)
    assert p["data_sha256"] == {"train": prov.file_sha256(f)} and "code" in p and "libs" in p
    assert prov.effective_batch(16, 2, n_gpu=2, world_size=1) == 64 and prov.effective_batch(1, 8, 1, 2) == 16


# --------------------------------------------------------------------------- B9/B10: run_sft (хуурамч сангууд)

def _install_fake_libs(monkeypatch, calls: dict, gpus: int = 1):
    torch = types.ModuleType("torch")
    torch.float16, torch.bfloat16, torch.float32 = "fp16", "bf16", "fp32"
    torch.cuda = SimpleNamespace(is_available=lambda: False, set_device=lambda *a: None, memory_allocated=lambda: 0)
    datasets = types.ModuleType("datasets")

    class Dataset(list):
        @classmethod
        def from_list(cls, rows):
            return cls(rows)
    datasets.Dataset = Dataset
    peft = types.ModuleType("peft")
    peft.LoraConfig = lambda **kw: SimpleNamespace(**kw)
    peft.get_peft_model = lambda model, cfg: model
    tf = types.ModuleType("transformers")

    class Tok:
        pad_token, eos_token = None, "<eos>"

        def save_pretrained(self, path):
            Path(path, "tokenizer_config.json").write_text("{}", encoding="utf-8")

    def from_pretrained(*a, **k):
        calls.setdefault("tokenizer", []).append((a, k))
        return Tok()
    tf.AutoTokenizer = SimpleNamespace(from_pretrained=from_pretrained)
    tf.TrainerCallback = object

    class EarlyStoppingCallback:
        def __init__(self, early_stopping_patience=1, early_stopping_threshold=0.0):
            self.patience = early_stopping_patience
    tf.EarlyStoppingCallback = EarlyStoppingCallback
    trl = types.ModuleType("trl")

    @dataclasses.dataclass
    class SFTConfig:
        output_dir: str = ""
        num_train_epochs: float = 1.0
        per_device_train_batch_size: int = 8
        gradient_accumulation_steps: int = 1
        learning_rate: float = 1e-5
        lr_scheduler_type: str = "linear"
        warmup_steps: int = 0
        bf16: bool = False
        fp16: bool = False
        gradient_checkpointing: bool = False
        gradient_checkpointing_kwargs: dict | None = None
        max_length: int = 1024
        logging_steps: int = 500
        save_strategy: str = "steps"
        save_steps: int = 500
        save_total_limit: int | None = None
        report_to: str = "all"
        completion_only_loss: bool | None = None
        max_steps: int = -1
        seed: int = 0
        ddp_find_unused_parameters: bool | None = None
        optim: str = "adamw_torch"
        eval_strategy: str = "no"
        eval_steps: int | None = None
        per_device_eval_batch_size: int = 8
        load_best_model_at_end: bool = False
        metric_for_best_model: str | None = None
        greater_is_better: bool | None = None
        n_gpu: int = gpus                    # TrainingArguments-ийн property-ийн оронд

    class SFTTrainer:
        def __init__(self, model=None, args=None, train_dataset=None, eval_dataset=None, processing_class=None,
                     peft_config=None):
            calls["trainer"] = {"model": model, "args": args, "train": train_dataset, "eval": eval_dataset,
                                "peft": peft_config}
            self.model, self.args, self.callbacks = model, args, []
            self.state = SimpleNamespace(global_step=0, max_steps=0, best_metric=None, best_model_checkpoint=None)

        def add_callback(self, cb):
            self.callbacks.append(cb)
            calls.setdefault("callbacks", []).append(cb)

        def train(self, resume_from_checkpoint=None):
            self.state.global_step, self.state.max_steps = 6, 10        # early stopping 6/10 алхамд
            if self.args.load_best_model_at_end:
                self.state.best_metric, self.state.best_model_checkpoint = 0.42, "out/checkpoint-4"
            return SimpleNamespace(training_loss=0.5)

        def save_model(self, path):
            Path(path).mkdir(parents=True, exist_ok=True)

        def is_world_process_zero(self):
            return True
    trl.SFTConfig, trl.SFTTrainer = SFTConfig, SFTTrainer
    for name, mod in {"torch": torch, "datasets": datasets, "peft": peft, "transformers": tf, "trl": trl}.items():
        monkeypatch.setitem(sys.modules, name, mod)

    def fake_load(hf_id, revision=None, **kw):
        calls.setdefault("base", []).append((hf_id, revision, kw))
        return "MODEL"
    monkeypatch.setattr(sft, "load_base_model", fake_load)
    monkeypatch.setattr(sft, "t4_sdpa_fix", lambda: False)
    monkeypatch.setattr(sft, "prepare_qlora", lambda model, gc, frozen_fp16: (model, 0))
    monkeypatch.setattr(sft, "_trainable_fp32", lambda model: (0, 0))
    for var in ("T2S_SAVE_STEPS", "T2S_TRAIN_DEADLINE", "WORLD_SIZE", "LOCAL_RANK"):
        monkeypatch.delenv(var, raising=False)


def _rows(path: Path, n: int) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps({"prompt": f"p{i}", "completion": f"c{i}"}) + "\n" for i in range(n)),
                    encoding="utf-8")
    return path


def test_run_sft_base_eval_split_early_stopping_and_meta(tmp_path, monkeypatch):
    calls: dict = {}
    _install_fake_libs(monkeypatch, calls)
    tcfg = copy.deepcopy(load_training_cfg())
    tcfg["recipes"]["r2a_t"] = {"kind": "sft", "data": ["synth"], "prompt": "h4s", "eval_share": 0.1, "eval_steps": 5,
                                "save_steps": 10, "early_stopping_patience": 2, "epochs": 1}
    train = _rows(tmp_path / "sft" / f"r2a_t__{MODEL}.jsonl", 3)
    ev = _rows(tmp_path / "sft" / f"r2a_t__{MODEL}.eval.jsonl", 1)       # build_sft-ийн гаргасан нэрээр
    res = sft.run_sft(load_config(), tcfg, "r2a_t", MODEL, data_file=str(train), output_dir=str(tmp_path / "out"),
                      base="/kaggle/input/d2-merged16")
    assert calls["base"] == [("/kaggle/input/d2-merged16", None, {})]          # revision нь HF суурьт л
    hf_id = load_models()["models"][MODEL]["hf_id"]
    assert calls["tokenizer"][0][0] == (hf_id,)                                  # tokenizer = build_sft-тэй ижил
    args = calls["trainer"]["args"]
    assert (args.eval_strategy, args.eval_steps, args.per_device_eval_batch_size) == ("steps", 5, 2)
    assert args.load_best_model_at_end and args.metric_for_best_model == "eval_loss" and args.greater_is_better is False
    assert args.save_steps == 10 and calls["trainer"]["eval"] == [{"prompt": "p0", "completion": "c0"}]
    assert [cb.patience for cb in calls["callbacks"] if hasattr(cb, "patience")] == [2]
    meta = json.loads((tmp_path / "out" / "t2s_meta.json").read_text(encoding="utf-8"))
    assert meta["base"] == "/kaggle/input/d2-merged16" and meta["hf_id"] == hf_id and res["base"] == meta["base"]
    assert meta["eval"] == {"file": str(ev), "records": 1, "eval_steps": 5, "best_metric": 0.42,
                            "best_checkpoint": "out/checkpoint-4", "global_step": 6, "max_steps": 10,
                            "early_stopped": True}
    assert meta["data_sha256"] == {"train": prov.file_sha256(train), "eval": prov.file_sha256(ev)}
    assert len(meta["code"]["tree_sha1"]) == 12 and "transformers" in meta["libs"]
    assert meta["batch"] == {"per_device": 2, "grad_acc": 8, "n_gpu": 1, "world_size": 1, "effective": 16}


def test_run_sft_defaults_unchanged(tmp_path, monkeypatch):
    """c3 (eval/base-гүй): HF суурь (revision-тэй), eval/early stopping-гүй — өмнөх тохиргоо + meta-д гарал үүсэл."""
    calls: dict = {}
    _install_fake_libs(monkeypatch, calls)
    tcfg = load_training_cfg()
    train = _rows(tmp_path / "c3.jsonl", 4)
    sft.run_sft(load_config(), tcfg, "c3", MODEL, data_file=str(train), output_dir=str(tmp_path / "out"))
    mspec = load_models()["models"][MODEL]
    assert calls["base"] == [(mspec["hf_id"], mspec.get("revision"), {})]
    args = calls["trainer"]["args"]
    assert args.eval_strategy == "no" and not args.load_best_model_at_end and calls["trainer"]["eval"] is None
    assert not any(hasattr(cb, "patience") for cb in calls.get("callbacks", []))
    assert (args.save_steps, args.max_length, args.bf16, args.learning_rate) == (200, 8192, True, 2e-4)
    meta = json.loads((tmp_path / "out" / "t2s_meta.json").read_text(encoding="utf-8"))
    assert "eval" not in meta and meta["base"] == mspec["hf_id"]
    assert meta["data_sha256"] == {"train": prov.file_sha256(train)} and meta["code"]["tree_sha1"]


def test_run_sft_qlora_base_and_data_parallel_warning(tmp_path, monkeypatch, caplog):
    calls: dict = {}
    _install_fake_libs(monkeypatch, calls, gpus=2)
    tcfg = load_training_cfg()
    train = _rows(tmp_path / "d3.jsonl", 2)
    with caplog.at_level("WARNING", logger="mn_text2sql.training.sft"):
        sft.run_sft(load_config(), tcfg, "d3", MODEL, data_file=str(train), output_dir=str(tmp_path / "out"),
                    base="/kaggle/input/d2-merged16")
    hf, rev, kw = calls["base"][0]
    assert (hf, rev) == ("/kaggle/input/d2-merged16", None) and kw["quantize_4bit"] and kw["dtype"] == "fp16"
    meta = json.loads((tmp_path / "out" / "t2s_meta.json").read_text(encoding="utf-8"))
    assert meta["batch"]["n_gpu"] == 2 and meta["batch"]["effective"] == 1 * 2 * 8
    assert "DataParallel" in caplog.text


def test_eval_settings_and_paths():
    hp = {"per_device_batch_size": 1, "eval_steps": None, "early_stopping_patience": None}
    assert sft.eval_settings(hp, 10, {"eval_strategy"}) == {"eval_strategy": "steps", "eval_steps": 10,
                                                          "per_device_eval_batch_size": 1}
    old = sft.eval_settings(dict(hp, eval_steps=5), 10, {"evaluation_strategy"})          # transformers < 4.41
    assert old["evaluation_strategy"] == "steps" and "eval_strategy" not in old
    with pytest.raises(ValueError, match="үржвэр"):
        sft.eval_settings(dict(hp, eval_steps=4, early_stopping_patience=2), 10, {"eval_strategy"})
    p = Path("data/train/sft/r__m.jsonl")
    assert sft.eval_data_path(p, {"eval_share": 0}) is None
    assert sft.eval_data_path(p, {"eval_share": 0.1}) == Path("data/train/sft/r__m.eval.jsonl")
    assert sft.eval_data_path(p, {"eval_share": 0}, "x.jsonl") == Path("x.jsonl")
    # анхдагч sft_defaults: eval-гүй
    d = load_training_cfg()["sft_defaults"]
    assert d["eval_share"] == 0 and d["eval_steps"] is None and d["early_stopping_patience"] is None


# --------------------------------------------------------------------------- B5/B4/B10: run_grpo (хуурамч TRL)

def test_run_grpo_uses_recipe_init_reward_pipe_and_meta(tmp_path, monkeypatch):
    from mn_text2sql.training import grpo
    calls: dict = {}
    datasets = types.ModuleType("datasets")
    datasets.Dataset = SimpleNamespace(from_list=lambda rows: list(rows))
    peft = types.ModuleType("peft")
    peft.LoraConfig = lambda **kw: SimpleNamespace(**kw)
    trl = types.ModuleType("trl")

    @dataclasses.dataclass
    class GRPOConfig:
        output_dir: str = ""
        learning_rate: float = 1e-6
        beta: float = 0.0
        num_generations: int = 8
        temperature: float = 1.0
        max_prompt_length: int = 512
        max_completion_length: int = 256
        per_device_train_batch_size: int = 8
        gradient_accumulation_steps: int = 1
        num_train_epochs: float = 1.0
        bf16: bool = False
        gradient_checkpointing: bool = False
        use_vllm: bool = False
        vllm_mode: str = "server"
        vllm_gpu_memory_utilization: float = 0.3
        logging_steps: int = 10
        save_steps: int = 500
        save_total_limit: int | None = None
        report_to: str = "all"
        seed: int = 0
        max_steps: int = -1
        shuffle_dataset: bool = True
        generation_batch_size: int | None = None

    class GRPOTrainer:
        def __init__(self, model=None, reward_funcs=None, args=None, train_dataset=None, peft_config=None):
            calls.update(model=model, reward=reward_funcs[0], args=args)

        def train(self, resume_from_checkpoint=None):
            pass

        def save_model(self, path):
            Path(path).mkdir(parents=True, exist_ok=True)
    trl.GRPOConfig, trl.GRPOTrainer = GRPOConfig, GRPOTrainer
    for name, mod in {"datasets": datasets, "peft": peft, "trl": trl}.items():
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.delenv("T2S_SAVE_STEPS", raising=False)
    pipe = object()
    fake_sampler = SimpleNamespace(pipe=pipe, opts=SimpleNamespace(dialect="sqlite_transpile"))
    monkeypatch.setattr(grpo, "grpo_sampler", lambda *a: fake_sampler)
    rows = [{"prompt": "p", "engine": "postgres", "db_path": "", "gold_sql": "SELECT 1", "qid": "q1", "variant": "mn"}]
    monkeypatch.setattr(grpo, "build_grpo_dataset", lambda *a, sampler=None, **k: (calls.update(sampler=sampler), rows)[1])
    tcfg = load_training_cfg()
    res = grpo.run_grpo(load_config(), tcfg, "d4", MODEL, output_dir=str(tmp_path / "d4"))
    assert calls["model"] == str(ROOT / "adapters" / f"d2__{MODEL}__merged")      # жорын init: d2 (B5)
    assert calls["sampler"] is fake_sampler and calls["reward"]._pipe is pipe       # reward = prompt-ын Pipeline (B4)
    meta = json.loads((tmp_path / "d4" / "t2s_meta.json").read_text(encoding="utf-8"))
    assert meta["init_source"] == "recipe:d2" and meta["dialect"] == "sqlite_transpile" and meta["prompt"] == "rag"
    assert meta["dataset_sha256"] == prov.rows_sha256(rows) and meta["code"]["tree_sha1"] and "trl" in meta["libs"]
    assert res["init"] == calls["model"]
    grpo.run_grpo(load_config(), tcfg, "d4", MODEL, init_model="/x/merged", output_dir=str(tmp_path / "d4b"))
    assert calls["model"] == "/x/merged"
    # BIRD-ийн мөр л бол reward нь PG-ийн Pipeline-гүй (DB холболт шаардахгүй)
    rows[0]["engine"] = "sqlite"
    grpo.run_grpo(load_config(), tcfg, "c6", MODEL, output_dir=str(tmp_path / "c6"))
    assert calls["reward"]._pipe is None
    assert calls["model"] == str(ROOT / "adapters" / f"{tcfg['best_sft']}__{MODEL}__merged")


# --------------------------------------------------------------------------- B7: train_mt.py

@pytest.fixture()
def train_mt():
    env, path = os.environ.get("PYTORCH_CUDA_ALLOC_CONF"), list(sys.path)
    spec_t = importlib.util.spec_from_file_location("train_mt_t", ROOT / "scripts" / "mt" / "train_mt.py")
    mod = importlib.util.module_from_spec(spec_t)
    spec_t.loader.exec_module(mod)
    yield mod
    sys.path[:] = path
    if env is None:
        os.environ.pop("PYTORCH_CUDA_ALLOC_CONF", None)
    else:
        os.environ["PYTORCH_CUDA_ALLOC_CONF"] = env


def test_train_mt_warmup_default_unchanged_and_ratio(train_mt):
    # анхдагч: хуучин томьёо (M1: 0.03 × ceil(n / (batch × GA)) × epochs)
    for n, b, ga, ep in ((5000, 16, 2, 3.0), (100, 16, 2, 1.0), (12345, 8, 4, 2.5)):
        old = max(1, int(0.03 * (math.ceil(n / (b * ga)) * ep)))
        assert train_mt.warmup_kwargs({"warmup_ratio", "warmup_steps"}, n, b, ga, ep) == {"warmup_steps": old}
    assert train_mt.warmup_kwargs({"warmup_ratio", "warmup_steps"}, 5000, 16, 2, 3.0, 0.05) == \
        {"warmup_ratio": 0.05, "warmup_steps": 0}
    assert train_mt.warmup_kwargs({"warmup_steps"}, 5000, 16, 2, 3.0, 0.05) == {"warmup_steps": 0.05}
    with pytest.raises(ValueError):
        train_mt.warmup_kwargs({"warmup_ratio"}, 10, 1, 1, 1.0, 1.5)


def test_train_mt_batch_info_records_data_parallel(train_mt):
    dp = train_mt.batch_info(SimpleNamespace(n_gpu=2, world_size=1), 16, 2)       # M1: 2×T4, torchrun-гүй
    assert dp == {"n_gpu": 2, "world_size": 1, "per_device_batch": 16, "grad_acc": 2, "data_parallel": True,
                  "effective_batch": 64}
    ddp = train_mt.batch_info(SimpleNamespace(n_gpu=1, world_size=2), 16, 2)
    assert ddp["effective_batch"] == 64 and not ddp["data_parallel"]
    assert train_mt.batch_info(SimpleNamespace(n_gpu=1, world_size=1), 16, 2)["effective_batch"] == 32


def test_train_mt_cli_flag_default_none(train_mt, monkeypatch):
    got = []
    monkeypatch.setattr(train_mt, "train_seq2seq", got.append)
    monkeypatch.setattr(sys, "argv", ["train_mt.py", "--backend", "seq2seq", "--model", "m", "--data", "d", "--out", "o"])
    train_mt.main()
    assert got[0].warmup_ratio is None and got[0].batch == 16 and got[0].grad_acc == 2
    monkeypatch.setattr(sys, "argv", ["train_mt.py", "--backend", "seq2seq", "--model", "m", "--data", "d", "--out", "o",
                                      "--warmup-ratio", "0.03"])
    train_mt.main()
    assert got[1].warmup_ratio == 0.03
