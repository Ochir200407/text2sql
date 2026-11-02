"""LoRA SFT (C1–C5, D2, D3, F6, F10) — TRL SFTTrainer + PEFT. A100 80 GB дээр bf16 (L4 дээр Arctic-7B-ийн RAG-гүй SFT
ирмэг дээр багтана; sqrl-9b-ийн RAG-тай SFT багтахгүй — туршилтын төлөвлөгөө "Нөөцийн тооцоо").
D3 (Kaggle 2×T4, 16 GB, bf16/FlashAttention-2 алга): QLoRA — суурь жин nf4 (double quant, compute fp16), fp16
mixed precision, gradient checkpointing; хоёр процессын DDP (torchrun --nproc_per_node 2), процесс бүр өөрийн GPU-д
бүтэн 4 битийн загвар. lm_head-ийг зөвхөн completion-ий байрлалд бодно (completion_logits_only).

Өгөгдөл: data/train/sft/<recipe>__<model>.jsonl ({prompt, completion}); loss зөвхөн completion дээр.
Гаралт: adapters/<recipe>__<model>/ (vLLM: --enable-lora --lora-modules <recipe>=adapters/<recipe>__<model>).
R2-A (B9, сонголттой): base — HF суурийн оронд нэгтгэсэн жин (D2-merged); eval_share/eval_file — validation loss,
early_stopping_patience — хамгийн бага eval_loss-тай checkpoint. t2s_meta.json-д код/сан/өгөгдлийн хувилбар (B10).
"""

from __future__ import annotations

import contextlib
import json
import logging
import math
import os
import re
import time
from pathlib import Path

from ..config import ROOT, Config, load_models

log = logging.getLogger(__name__)


def dist_info() -> tuple[int, int]:
    """(world_size, local_rank) — torchrun/accelerate-ийн орчны хувьсагчаас (torch import хийхгүй)."""
    return int(os.environ.get("WORLD_SIZE") or 1), int(os.environ.get("LOCAL_RANK") or 0)


def quant_device_map(world_size: int, local_rank: int):
    """QLoRA-гийн device_map: DDP (олон процесс) бол процесс бүр өөрийн GPU-д бүтэн загвар ({"": local_rank});
    нэг процесс бол "auto" (нэг GPU, эсвэл хэд хэдэн GPU-д давхаргаар хуваана)."""
    return {"": local_rank} if world_size > 1 else "auto"


def load_base_model(hf_id: str, revision: str | None = None, *, dtype=None, quantize_4bit: bool = False,
                    device_map=None):
    """Qwen2.5 (Arctic) — AutoModelForCausalLM; Qwen3.5 (sqrl, ...ForConditionalGeneration) — image-text классаар.
    Анхдагч: bf16, device_map-гүй (өмнөх зан төлөв). quantize_4bit: QLoRA (nf4, double quant, compute = dtype)."""
    import torch
    from transformers import AutoModelForCausalLM
    dtype = dtype or torch.bfloat16
    kw = dict(torch_dtype=dtype, revision=revision, trust_remote_code=True, attn_implementation="sdpa")
    if quantize_4bit:
        from transformers import BitsAndBytesConfig
        kw["quantization_config"] = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4",
                                                       bnb_4bit_compute_dtype=dtype, bnb_4bit_use_double_quant=True)
    if device_map is not None:
        kw["device_map"] = device_map
    try:
        return AutoModelForCausalLM.from_pretrained(hf_id, **kw)
    except (ValueError, KeyError) as e:
        log.info("AutoModelForCausalLM ачаалсангүй (%s) — AutoModelForImageTextToText-оор", e)
        from transformers import AutoModelForImageTextToText
        return AutoModelForImageTextToText.from_pretrained(hf_id, **kw)


def t4_sdpa_fix() -> bool:
    """T4 (sm75): SDPA-ийн flash/cuDNN kernel sm80+ шаарддаг, memory-efficient kernel нь GQA-г (enable_gqa) дэмждэггүй.
    transformers mask-гүй (batch 1, padding-гүй) үед enable_gqa=True өгдөг тул math kernel руу унаж [1, 28, L, L] fp32
    матриц үүсгэнэ (8.3k токенд ~7.7 GB) → OOM. use_gqa_in_sdpa-г False болговол KV head давтагдаж (repeat_kv, +~120 MB)
    memory-efficient kernel ажиллана. sm80+ (A100) дээр өөрчлөхгүй. Буцаах: засвар хэрэглэсэн эсэх."""
    import torch
    if not torch.cuda.is_available() or torch.cuda.get_device_capability()[0] >= 8:
        return False
    import transformers.integrations.sdpa_attention as sd
    sd.use_gqa_in_sdpa = lambda *args, **kwargs: False
    return True


def sdpa_efficient_only():
    """T4-д сургалтыг зөвхөн memory-efficient SDPA-аар: kernel тохирохгүй бол math руу чимээгүй унаж OOM болохын
    оронд шууд алдаа өгнө (квот үрэхгүй)."""
    import torch
    import torch.nn.functional as F
    from torch.nn.attention import SDPBackend, sdpa_kernel
    # Урьдчилсан шалгалт (сургалтын хэлбэр: fp16, 28 head, head_dim 128, causal, backward): kernel тохирохгүй бол
    # хүчээр албадахгүй (сургалт эхлэхээс өмнө 40 мин бэлтгэлийг алдахгүйн тулд) — анхааруулаад энгийн горимоор
    try:
        q = torch.randn(1, 28, 64, 128, device="cuda", dtype=torch.float16, requires_grad=True)
        with sdpa_kernel([SDPBackend.EFFICIENT_ATTENTION]):
            F.scaled_dot_product_attention(q, q, q, is_causal=True).sum().backward()
    except RuntimeError as e:
        log.warning("SDPA memory-efficient kernel шалгалт амжилтгүй (%s) — албадахгүй (урт мөрөнд OOM эрсдэлтэй)", e)
        return contextlib.nullcontext()
    return sdpa_kernel([SDPBackend.EFFICIENT_ATTENTION])


def memory_callback():
    """GPU-ийн дээд санах ойг 1, 2 ба 20 алхам тутам логт (T4 15 GB-тэй харьцуулах)."""
    from transformers import TrainerCallback

    class _Mem(TrainerCallback):
        def on_step_end(self, args, state, control, **kwargs):
            import torch
            if torch.cuda.is_available() and (state.global_step in (1, 2) or state.global_step % 20 == 0):
                log.info("SFT алхам %d: GPU-ийн дээд санах ой %.1f GB", state.global_step,
                         torch.cuda.max_memory_allocated() / 1e9)
            return control

    return _Mem()


def prepare_qlora(model, gradient_checkpointing: bool, frozen_fp16: bool):
    """peft.prepare_model_for_kbit_training: суурийг хөлдөөх, gradient checkpointing (reentrant биш), 4 бит биш бүх
    жинг fp32 болгох. frozen_fp16 (T4, fp16 autocast): тэр fp32 жинг (embedding ба lm_head — 152k × 3584, тус бүр
    ~1.1 GB fp16; norm, bias) fp16 болгож буцаана: (1) хоёр толгой fp32-оор 2.2 GB илүү; (2) fp32 norm-ын гаралт
    residual урсгалыг fp32 болгож checkpoint/MLP-ийн activation-ыг 2 дахин томруулна (8k токенд T4-ийн 16 GB-д эрсдэл).
    Эдгээр нь хөлдөөсөн (оновчлогдохгүй) бөгөөд fp16 autocast-д ямар ч байсан fp16-аар бодогдоно — vLLM-ийн fp16
    inference-тэй ижил тооцоо; RMSNorm дотроо fp32-оор бодно. Буцаах: (model, fp16 болгосон тензорын тоо)."""
    import torch
    from peft import prepare_model_for_kbit_training
    model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=gradient_checkpointing,
                                            gradient_checkpointing_kwargs={"use_reentrant": False})
    n = 0
    if frozen_fp16:
        for p in model.parameters():
            if not p.requires_grad and p.dtype == torch.float32 and p.__class__.__name__ != "Params4bit":
                p.data = p.data.to(torch.float16)
                n += 1
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    return model, n


def _trainable_fp32(model) -> tuple[int, int]:
    """Сургагдах (LoRA) жинг fp32 болгоно: fp16 mixed precision-д GradScaler fp32 «мастер» жинтэй ажиллана (fp16
    градиентийг unscale хийж чаддаггүй; bf16 жин бол градиентийн хуримтлал, AdamW-ийн шинэчлэл bf16-ийн 8 битийн
    нарийвчлалаар явна). TRL ≥ 1.x нь 4 битийн загварын LoRA жинг SFTTrainer.__init__ дотор bf16 болгодог (QLoRA-гийн
    өгүүллийн зөвлөмж, bf16 GPU-д) — T4-д тохирохгүй тул trainer үүсгэсний ДАРАА (optimizer train()-д үүснэ) дуудна.
    Буцаах: (сургагдах параметрийн тоо, fp32 болгосон тензорын тоо)."""
    import torch
    n = recast = 0
    for p in model.parameters():
        if p.requires_grad:
            if p.dtype != torch.float32:
                p.data = p.data.float()
                recast += 1
            n += p.numel()
    return n, recast


COMPLETION_CHUNK = 512    # нэг удаад бодох completion-ий байрлал: [512 × 152k] logits ≈ 0.16 GB fp16 (+ fp32 CE)


def shift_targets(labels: list[int], ignore_index: int = -100) -> list[int]:
    """p байрлалын hidden state нь labels[p + 1]-ийг таамаглана (causal LM-ийн shift); сүүлийн байрлал loss-гүй.
    add_completion_loss_forward-ийн torch хувилбартай ижил дүрэм (CPU тестэд)."""
    return list(labels[1:]) + [ignore_index]


def chunked_completion_ce(hidden, weight, targets, chunk: int = COMPLETION_CHUNK):
    """Σ CE(hidden · weightᵀ, targets) — байрлалаар ≤ chunk хэсэгчилнэ; хэсэг бүрийн logits-ыг хадгалахгүй
    (torch.utils.checkpoint — backward-д дахин бодно). Санах ойд нэг хэсгийн [chunk × vocab] logits л байна —
    completion-ий урт хамаагүй (Qwen2.5: vocab 152k)."""
    import torch
    import torch.nn.functional as F
    from torch.utils.checkpoint import checkpoint

    def part(h, t):
        # autocast-гүй дуудсан ч dtype таарна (fp16 autocast-д ямар ч байсан fp16 matmul); CE нь fp32-оор
        return F.cross_entropy(F.linear(h.to(weight.dtype), weight).float(), t, reduction="sum")

    total = hidden.new_zeros((), dtype=torch.float32)
    for i in range(0, hidden.shape[0], chunk):
        total = total + checkpoint(part, hidden[i:i + chunk], targets[i:i + chunk], use_reentrant=False)
    return total


def add_completion_loss_forward(model, chunk: int = COMPLETION_CHUNK) -> None:
    """Суурь causal LM-ийн (PEFT-ийн доорх Qwen2ForCausalLM) forward-д t2s_shift_labels kwarg нэмнэ: backbone-ийн
    сүүлийн hidden state-ээс зөвхөн label != -100 байрлалыг аваад chunked_completion_ce. Бүтэн [L × vocab] logits
    хэзээ ч үүсэхгүй. Forward дотор хийгддэг тул DDP/PEFT/accelerate-ийн autocast-тай зөв ажиллана (TRL ≥ 1.x-ийн
    fused head-тэй адил арга, харин Triton-гүй энгийн PyTorch — T4/sm75). kwarg-гүй дуудлага хуучнаараа."""
    import functools
    import types

    from transformers.modeling_outputs import CausalLMOutputWithPast
    original = model.forward

    @functools.wraps(type(model).forward)
    def forward(self, *args, t2s_shift_labels=None, **kwargs):
        if t2s_shift_labels is None:
            return original(*args, **kwargs)
        for k in ("labels", "shift_labels", "num_items_in_batch", "logits_to_keep", "fused_lm_head", "return_dict"):
            kwargs.pop(k, None)
        kwargs["use_cache"] = False
        hidden = getattr(self, self.base_model_prefix)(*args, **kwargs).last_hidden_state[0]   # [L, d], batch 1
        weight = self.get_output_embeddings().weight
        tgt = t2s_shift_labels.to(hidden.device)
        mask = tgt != -100
        if not bool(mask.any()):    # DDP: бүх процесс backward хийх ёстой — граф холбоотой 0
            return CausalLMOutputWithPast(loss=hidden.sum() * 0.0)
        hid = hidden[mask].to(weight.device)
        return CausalLMOutputWithPast(loss=chunked_completion_ce(hid, weight, tgt[mask].to(weight.device), chunk))

    model.forward = types.MethodType(forward, model)


def completion_logits_trainer(base_cls):
    """Completion-ий loss-ийг add_completion_loss_forward-оор (хэсэгчилсэн CE) бодох Trainer: T4 (16 GB) дээр бүтэн
    logits (8k токенд fp32 ~5 GB + softmax, градиент) үүсгэхгүй. Loss = completion токены CE-ийн нийлбэр /
    num_items_in_batch (Trainer.compute_loss-ийн нөхцөлөөр) эсвэл дундаж; training_step GA-д хуваах эсэхийг
    loss_is_scaled_for_ga-гаар (transformers ≥ 5.x) яг тааруулна. batch > 1 эсвэл forward нөхөөгүй бол үндсэн рүү."""
    import torch

    class CompletionLogitsTrainer(base_cls):
        _t2s_patched = False

        def compute_loss(self, model, inputs, return_outputs=False, num_items_in_batch=None):
            labels = inputs.get("labels")
            if not self._t2s_patched or labels is None or labels.dim() != 2 or labels.shape[0] != 1:
                self.loss_is_scaled_for_ga = None
                return super().compute_loss(model, inputs, return_outputs=return_outputs,
                                            num_items_in_batch=num_items_in_batch)
            for mask_key in ("completion_mask", "assistant_masks"):
                # TRL dataset/collator labels-ийг өөрөө маскалдаг; batch-д маск үлдсэн хувилбарт энд маскална
                if mask_key in inputs and inputs[mask_key] is not None:
                    labels = labels.masked_fill(inputs[mask_key] == 0, -100)
            shift = torch.cat([labels[0, 1:], labels.new_full((1,), -100)])
            n_tok = int((shift != -100).sum())
            fwd = {k: v for k, v in inputs.items()
                   if k not in ("labels", "use_cache", "logits_to_keep", "completion_mask", "assistant_masks")}
            out = model(**fwd, t2s_shift_labels=shift)
            loss = out.loss
            # Trainer.compute_loss-ийн нөхцөл: num_items_in_batch-ээр хэвийн болгосон бол GA-д дахин хуваахгүй
            scaled = num_items_in_batch is not None and (getattr(self, "model_accepts_loss_kwargs", False)
                                                         or getattr(self, "compute_loss_func", None) is not None)
            if scaled:
                loss = loss / num_items_in_batch
                if getattr(self.args, "average_tokens_across_devices", False) and self.accelerator.num_processes > 1:
                    loss = loss * self.accelerator.num_processes
            else:
                loss = loss / max(1, n_tok)
            self.loss_is_scaled_for_ga = scaled
            return (loss, out) if return_outputs else loss

    return CompletionLogitsTrainer


CRITICAL_CONFIG_KEYS = {"output_dir", "num_train_epochs", "per_device_train_batch_size", "gradient_accumulation_steps",
                        "learning_rate", "max_length", "completion_only_loss", "gradient_checkpointing", "fp16", "bf16",
                        "save_steps", "max_steps",
                        # B9: зөвхөн eval-тэй үед дамжина — алга бол eval/early stopping-гүй чимээгүй сургахгүй
                        "eval_strategy", "evaluation_strategy", "eval_steps", "per_device_eval_batch_size",
                        "load_best_model_at_end", "metric_for_best_model"}


def eval_settings(hp: dict, save_steps: int, config_fields: set[str]) -> dict:
    """B9: eval_dataset-тай үеийн SFTConfig-ийн нэмэлт. eval_steps (анхдагч = save_steps) тутам validation loss;
    per_device_eval_batch_size = сургалтынх (T4: 1 — completion_logits_only нь batch 1-д л хэсэгчилсэн CE).
    early_stopping_patience бол хамгийн бага eval_loss-тай checkpoint-ийг төгсгөлд ачаална (Trainer-ийн шаардлага:
    save_steps нь eval_steps-ийн үржвэр). transformers < 4.41 нь evaluation_strategy нэртэй."""
    eval_steps = int(hp.get("eval_steps") or save_steps)
    key = "evaluation_strategy" if "evaluation_strategy" in config_fields and "eval_strategy" not in config_fields \
        else "eval_strategy"
    kw = {key: "steps", "eval_steps": eval_steps, "per_device_eval_batch_size": int(hp["per_device_batch_size"])}
    if hp.get("early_stopping_patience"):
        if int(save_steps) % eval_steps:
            raise ValueError(f"early stopping: save_steps={save_steps} нь eval_steps={eval_steps}-ийн үржвэр байх ёстой "
                             "(хамгийн сайн checkpoint-ийг хадгалахын тулд)")
        kw.update(load_best_model_at_end=True, metric_for_best_model="eval_loss", greater_is_better=False)
    return kw


def eval_data_path(data_path: Path, hp: dict, eval_file: str | None = None) -> Path | None:
    """Validation файл: eval_file өгвөл тэр; жорын eval_share > 0 бол build_sft-ийн <data>.eval.jsonl; үгүй бол None
    (анхдагч — өмнөх шиг eval-гүй)."""
    if eval_file:
        return Path(eval_file)
    if float(hp.get("eval_share") or 0) > 0:
        return data_path.with_name(f"{data_path.stem}.eval.jsonl")
    return None


def config_kwargs(cls, kw: dict):
    """TRL Config-ийг үүсгэнэ; тухайн хувилбарт байхгүй талбарыг (API өөрчлөлт) алгасаж анхааруулна."""
    import dataclasses
    names = {f.name for f in dataclasses.fields(cls)}
    dropped = sorted(k for k in kw if k not in names)
    critical = sorted(set(dropped) & CRITICAL_CONFIG_KEYS)
    if critical:
        raise ValueError(f"{cls.__name__}: энэ хувилбарт чухал тохиргоо алга {critical} (TRL/transformers-ийн API "
                         "өөрчлөгдсөн) — анхдагч утгаар (жишээ нь max_length=1024) чимээгүй сургахгүй")
    if dropped:
        log.warning("%s: энэ хувилбарт байхгүй тохиргоог алгасав: %s", cls.__name__, dropped)
    return cls(**{k: v for k, v in kw.items() if k in names})


def last_checkpoint(out: Path) -> str | None:
    """Тасарсан сургалтыг үргэлжлүүлэх: output_dir дахь хамгийн сүүлийн БҮРЭН checkpoint-N (trainer_state.json нь
    optimizer/scheduler-ийн дараа бичигддэг — Kaggle-ийн 12 цагийн хязгаарт хадгалалт дундаа тасарсан хавтсыг
    алгасна). Байхгүй бол None."""
    if not out.is_dir():
        return None
    found = []
    for p in out.iterdir():
        m = re.fullmatch(r"checkpoint-(\d+)", p.name)
        if m and p.is_dir() and (p / "trainer_state.json").exists():
            found.append((int(m.group(1)), str(p)))
    return max(found)[1] if found else None


def finished(out: Path) -> dict | None:
    """Дууссан сургалт (t2s_meta.json бичигдсэн) — дахин ажиллуулахад алгасна (Modal/сервер тасарсны дараа)."""
    meta = out / "t2s_meta.json"
    return json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else None


def deadline_callback(deadline: float):
    """T2S_TRAIN_DEADLINE (unix сек): хугацаа (Kaggle-ийн долоо хоногийн квот) дуусахаас өмнө алхмын төгсгөлд checkpoint
    хадгалаад зогсоно — дараагийн сесс тэр checkpoint-оос үргэлжилнэ. DDP-д шийдвэрийг бүх процесст MAX-аар нэгтгэнэ
    (нэг нь зогсож нөгөө нь дараагийн алхамд all-reduce хүлээж гацахаас сэргийлнэ)."""
    from transformers import TrainerCallback

    class _Deadline(TrainerCallback):
        stopped = False

        def on_step_end(self, args, state, control, **kwargs):
            import torch
            stop = time.time() >= deadline
            if torch.distributed.is_available() and torch.distributed.is_initialized():
                dev = f"cuda:{torch.cuda.current_device()}" if torch.cuda.is_available() else "cpu"
                t = torch.tensor([1.0 if stop else 0.0], device=dev)
                torch.distributed.all_reduce(t, op=torch.distributed.ReduceOp.MAX)
                stop = bool(t.item() > 0)
            if stop:
                control.should_save = True
                control.should_training_stop = True
                self.stopped = True
                log.warning("SFT: хугацааны хязгаар (T2S_TRAIN_DEADLINE) — алхам %d-д checkpoint хадгалаад зогсоно",
                            state.global_step)
            return control

    return _Deadline()


def run_sft(cfg: Config, tcfg: dict, recipe: str, model_name: str, data_file: str | None = None,
            output_dir: str | None = None, max_steps: int | None = None, force: bool = False,
            base: str | None = None, eval_file: str | None = None) -> dict:
    """base: суурь жин (HF id эсвэл хавтас, жишээ нь D2-ийг нэгтгэсэн загвар — R2-A); анхдагч models.yaml-ийн hf_id
    (revision-тэй). Tokenizer нь үргэлж hf_id-ийнх (build_sft-ийн prompt-той ижил). eval_file / жорын eval_share:
    validation (eval_settings) — анхдагч алга."""
    import torch
    from datasets import Dataset
    from peft import LoraConfig
    from transformers import AutoTokenizer
    from trl import SFTConfig, SFTTrainer

    from .provenance import effective_batch, model_revision, provenance, sampling_settings
    spec = tcfg["recipes"][recipe]
    hp = dict(tcfg["sft_defaults"])
    hp.update({k: v for k, v in spec.items() if k in hp})
    fp16, bf16, qlora = bool(hp.get("fp16")), bool(hp["bf16"]), bool(hp.get("quantize_4bit"))
    if fp16 and bf16:
        raise ValueError(f"{recipe}: fp16 ба bf16 зэрэг true байж болохгүй (T4: fp16: true, bf16: false)")
    mspec = load_models()["models"][model_name]
    base_id = base or mspec["hf_id"]
    revision = None if base else mspec.get("revision")
    data_path = Path(data_file) if data_file else ROOT / tcfg["data"]["dir"] / "sft" / f"{recipe}__{model_name}.jsonl"
    rows = [json.loads(x) for x in data_path.read_text(encoding="utf-8").splitlines() if x.strip()]
    ds = Dataset.from_list(rows)
    eval_path = eval_data_path(data_path, hp, eval_file)
    eval_rows: list[dict] = []
    if eval_path is not None:
        if not eval_path.exists():
            raise FileNotFoundError(f"{recipe}: validation файл алга: {eval_path} (build --recipe {recipe} нь "
                                    "eval_share > 0 үед үүсгэнэ, эсвэл --eval-file)")
        eval_rows = [json.loads(x) for x in eval_path.read_text(encoding="utf-8").splitlines() if x.strip()]
        if not eval_rows:
            log.warning("SFT %s: validation файл хоосон (%s) — eval-гүй сургана", recipe, eval_path)
    out = Path(output_dir) if output_dir else ROOT / "adapters" / f"{recipe}__{model_name}"
    if not force and (done := finished(out)):
        log.info("SFT %s аль хэдийн дууссан (%s) — алгасав (--force-оор дахин)", recipe, out)
        return {"adapter": str(out), "skipped": True, **done}
    world, local_rank = dist_info()
    t4_fix = t4_sdpa_fix()
    if t4_fix:
        log.info("SFT %s: GPU sm < 80 (T4) — SDPA-д KV head давтаж memory-efficient kernel (GQA/math-аас зайлсхийнэ)",
                 recipe)
    tok = AutoTokenizer.from_pretrained(mspec["hf_id"], revision=mspec.get("revision"), trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token
    if qlora:
        if world > 1:
            torch.cuda.set_device(local_rank)
        dmap = quant_device_map(world, local_rank)
        log.info("SFT %s: QLoRA nf4 (%s), процесс %d/%d, device_map=%s, суурь %s", recipe, "fp16" if fp16 else "bf16",
                 local_rank + 1, world, dmap, base_id)
        model = load_base_model(base_id, revision, dtype=torch.float16 if fp16 else torch.bfloat16,
                                quantize_4bit=True, device_map=dmap)
        model, n16 = prepare_qlora(model, bool(hp["gradient_checkpointing"]), frozen_fp16=fp16)
        log.info("SFT %s: 4 бит биш хөлдөөсөн %d тензорыг fp16 болгов; GPU санах ой %.1f GB", recipe, n16,
                 torch.cuda.memory_allocated() / 1e9 if torch.cuda.is_available() else 0.0)
    else:
        model = load_base_model(base_id, revision)
    # warmup_ratio-г зарим TRL/transformers хувилбар хассан тул алхмын тоогоор (warmup_steps) өгнө; DDP-д алхам бүр
    # world_size процессын batch-ийг нэгтгэнэ
    steps_per_epoch = math.ceil(len(rows) / (int(hp["per_device_batch_size"]) * int(hp["gradient_accumulation"]) * world))
    total_steps = int(max_steps) if max_steps else steps_per_epoch * math.ceil(float(hp["epochs"]))
    save_steps = int(os.environ.get("T2S_SAVE_STEPS") or hp.get("save_steps", 200))
    kw = dict(
        output_dir=str(out), num_train_epochs=float(hp["epochs"]), per_device_train_batch_size=int(hp["per_device_batch_size"]),
        gradient_accumulation_steps=int(hp["gradient_accumulation"]), learning_rate=float(hp["learning_rate"]),
        lr_scheduler_type=hp["lr_scheduler"], warmup_steps=max(1, int(float(hp["warmup_ratio"]) * total_steps)),
        bf16=bf16, gradient_checkpointing=bool(hp["gradient_checkpointing"]), max_length=int(hp["max_length"]),
        logging_steps=int(hp.get("logging_steps") or 10), save_strategy="steps", save_steps=save_steps, save_total_limit=2,
        report_to="none", completion_only_loss=True,
        max_steps=int(max_steps) if max_steps else -1, seed=42)
    if fp16:
        kw["fp16"] = True
    if qlora:
        kw["gradient_checkpointing_kwargs"] = {"use_reentrant": False}
    if world > 1:
        kw["ddp_find_unused_parameters"] = False
    if hp.get("optim"):
        kw["optim"] = hp["optim"]
    if eval_rows:
        import dataclasses
        kw.update(eval_settings(hp, save_steps, {f.name for f in dataclasses.fields(SFTConfig)}))
    args = config_kwargs(SFTConfig, kw)
    peft_cfg = LoraConfig(r=int(hp["lora_r"]), lora_alpha=int(hp["lora_alpha"]), lora_dropout=float(hp["lora_dropout"]),
                          target_modules=hp["target_modules"], task_type="CAUSAL_LM")
    trainer_cls = completion_logits_trainer(SFTTrainer) if hp.get("completion_logits_only") else SFTTrainer
    data_kw = {"train_dataset": ds}
    if eval_rows:
        data_kw["eval_dataset"] = Dataset.from_list(eval_rows)
    if qlora:
        # PeftModel-ийг өөрсдөө үүсгэнэ: TRL-ийн хувилбараас үл хамааран (хуучин TRL peft_config-оор
        # prepare_model_for_kbit_training-ийг дахин дуудаж fp16 болгосон embedding/lm_head-ийг fp32 болгодог) нэг ижил
        from peft import get_peft_model
        model = get_peft_model(model, peft_cfg)
        trainer = trainer_cls(model=model, args=args, processing_class=tok, **data_kw)
        n_train, recast = _trainable_fp32(trainer.model)
        log.info("SFT %s: сургагдах параметр %.1fM (LoRA r=%s), fp32 болгосон тензор %d (TRL bf16 болгосон бол > 0)",
                 recipe, n_train / 1e6, hp["lora_r"], recast)
    else:
        trainer = trainer_cls(model=model, args=args, processing_class=tok, peft_config=peft_cfg, **data_kw)
    if eval_rows and hp.get("early_stopping_patience"):
        from transformers import EarlyStoppingCallback
        trainer.add_callback(EarlyStoppingCallback(early_stopping_patience=int(hp["early_stopping_patience"])))
        log.info("SFT %s: validation %d мөр, eval_steps %s, early stopping patience %s", recipe, len(eval_rows),
                 kw.get("eval_steps"), hp["early_stopping_patience"])
    if hp.get("completion_logits_only"):
        # trainer үүсгэсний дараа: TRL ≥ 1.x нь __init__ дотор суурь загварын forward-ийг (fused head) нөхдөг — манай
        # нөхөөс түүнийг ороож, t2s_shift_labels-гүй дуудлагыг хэвээр нь дамжуулна
        base = trainer.model.get_base_model() if hasattr(trainer.model, "get_base_model") else trainer.model
        if hasattr(base, "base_model_prefix") and hasattr(base, "get_output_embeddings"):
            add_completion_loss_forward(base)
            trainer._t2s_patched = True
            log.info("SFT %s: completion-ий loss хэсэгчилсэн (≤ %d байрлал, checkpoint) — бүтэн logits үүсэхгүй",
                     recipe, COMPLETION_CHUNK)
        else:
            log.warning("SFT %s: суурь загварт forward нөхөх боломжгүй — үндсэн (бүтэн logits) loss", recipe)
    resume = last_checkpoint(out)
    if resume:
        log.info("SFT %s: %s-ээс үргэлжлүүлнэ", recipe, resume)
    log.info("SFT %s: %d мөр, %d алхам (эпох бүр %d), процесс %d", recipe, len(rows), total_steps, steps_per_epoch,
             world)
    stopper = None
    if os.environ.get("T2S_TRAIN_DEADLINE"):
        deadline = float(os.environ["T2S_TRAIN_DEADLINE"])
        stopper = deadline_callback(deadline)
        trainer.add_callback(stopper)
        log.info("SFT %s: хугацааны хязгаар %s (%.0f мин үлдсэн)", recipe,
                 time.strftime("%H:%M", time.localtime(deadline)), (deadline - time.time()) / 60)
    trainer.add_callback(memory_callback())
    with (sdpa_efficient_only() if t4_fix else contextlib.nullcontext()):
        result = trainer.train(resume_from_checkpoint=resume)
    if stopper is not None and stopper.stopped and trainer.state.global_step < trainer.state.max_steps:
        # дуусаагүй: эцсийн adapter/t2s_meta.json бичихгүй (finished() худал хэвээр) — checkpoint-оос үргэлжилнэ.
        # Сүүлийн алхам дээр хязгаар таарвал сургалт дууссан тул доор хадгална (tester L1)
        return {"adapter": str(out), "stopped": True, "checkpoint": last_checkpoint(out),
                "global_step": trainer.state.global_step}
    trainer.save_model(str(out))
    n_gpu = int(getattr(trainer.args, "n_gpu", 1) or 1)
    if n_gpu > 1 and world == 1:
        log.warning("SFT %s: torchrun-гүй %d GPU — Trainer DataParallel хэрэглэсэн (алхмын batch ×%d)", recipe, n_gpu,
                    n_gpu)
    meta = {"recipe": recipe, "model": model_name, "hf_id": mspec["hf_id"], "base": base_id, "records": len(rows),
            # L2: HF commit (tokenizer = hf_id; base нь хавтас бол түүний гарал "base"-д) ба өгөгдлийн sampling тохиргоо
            "model_revision": model_revision(mspec), "sampling": sampling_settings(tcfg),
            "hparams": hp, "train_loss": getattr(result, "training_loss", None), "data": str(data_path),
            "world_size": world,
            "batch": {"per_device": int(hp["per_device_batch_size"]), "grad_acc": int(hp["gradient_accumulation"]),
                      "n_gpu": n_gpu, "world_size": world,
                      "effective": effective_batch(int(hp["per_device_batch_size"]), int(hp["gradient_accumulation"]),
                                                   n_gpu, world)},
            **provenance(train=data_path, eval=eval_path if eval_rows else None)}
    if eval_rows:
        st = trainer.state
        meta["eval"] = {"file": str(eval_path), "records": len(eval_rows), "eval_steps": kw.get("eval_steps"),
                        "best_metric": getattr(st, "best_metric", None),
                        "best_checkpoint": getattr(st, "best_model_checkpoint", None),
                        "global_step": st.global_step, "max_steps": st.max_steps,
                        "early_stopped": bool(hp.get("early_stopping_patience")) and st.global_step < st.max_steps}
    if trainer.is_world_process_zero():   # DDP: зөвхөн нэг процесс бичнэ (save_model өөрөө үүнийг хийдэг)
        tok.save_pretrained(str(out))
        (out / "t2s_meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"adapter": str(out), **meta}


MERGE_DTYPES = {"bf16": "BF16", "fp16": "F16", "fp32": "F32"}
# adapter хавтсанд run_sft-ийн хадгалсан tokenizer (өмнөх merge_adapter нь tokenizer-ийг adapter-аас авдаг байсан)
TOKENIZER_FILES = ("tokenizer.json", "tokenizer_config.json", "special_tokens_map.json", "added_tokens.json",
                   "vocab.json", "merges.txt", "tokenizer.model", "chat_template.jinja", "chat_template.json")


def merge_lora_module():
    """scripts/notebook/merge_lora.py (numpy-ийн нэгтгэл — torch/peft шаардахгүй)-г модуль болгон ачаална."""
    import importlib.util
    path = ROOT / "scripts" / "notebook" / "merge_lora.py"
    if not path.is_file():
        raise FileNotFoundError(f"merge_lora.py алга: {path}")
    spec = importlib.util.spec_from_file_location("t2s_merge_lora", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def merge_adapter(model_name: str, adapter_dir: str, out_dir: str, base: str | None = None,
                  dtype: str = "bf16") -> str:
    """LoRA-г суурь загварт нэгтгэж бүтэн жин хадгална (GRPO-ийн эхлэл, эсвэл vLLM-д тусдаа загвар болгон).
    base: GRPO-ийн LoRA-г нэгтгэхэд эхлэл болсон (SFT нэгтгэсэн) загварын хавтас (эсвэл HF id); анхдагч models.yaml.
    B6: W + scale·B·A-г fp32-оор бодоод dtype руу НЭГ удаа дугуйлна (scripts/notebook/merge_lora.py, тензор нэг
    нэгээр — санах ой бага); PEFT-ийн bf16 merge шиг delta-г bf16-ийн нарийвчлалд давхар дугуйлахгүй.
    dtype: bf16 (анхдагч — өмнөх гаралтын dtype), fp16 (T4/vLLM; 10 битийн mantissa — delta илүү хадгалагдана), fp32."""
    if dtype not in MERGE_DTYPES:
        raise ValueError(f"dtype={dtype!r} — {'/'.join(MERGE_DTYPES)}-ийн нэг")
    import shutil
    ml = merge_lora_module()
    mspec = load_models()["models"][model_name]
    base_dir = ml.resolve_base(base or mspec["hf_id"], None if base else mspec.get("revision"))
    adapter, out = Path(adapter_dir), Path(out_dir)
    info = ml.merge(Path(base_dir), adapter, out, out_dtype=MERGE_DTYPES[dtype])
    copied = [f for f in TOKENIZER_FILES if (adapter / f).is_file()]
    for f in copied:
        shutil.copy2(adapter / f, out / f)
    log.info("merge %s → %s: %d LoRA модуль, %s, tokenizer adapter-аас %d файл", adapter, out, info["modules"],
             info.get("dtype"), len(copied))
    return out_dir
