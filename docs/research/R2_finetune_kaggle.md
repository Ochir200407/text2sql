# R2. Kaggle-ийн үнэгүй GPU дээр Arctic-R1-7B + D2-ийг fine-tune хийх практик жор (2026-10-30)

Эх: `docs/RESEARCH_TECH_2.md` (T7 ExCoT DPO, T8 TTRL), `docs/SYNTH_OWN.md`, `docs/FINETUNE_LIT_REVIEW.md`,
`docs/IMPROVE_80_PLAN.md` (D3-ийн хэмжилт), `docs/WORKLOG.md`, `src/mn_text2sql/training/` (`sft.py`, `grpo.py`, `sampling.py`),
`config/training.yaml`, `requirements*.txt`, `scripts/notebook/bootstrap.sh`, мөн 2026-10-30-нд уншсан arXiv / Unsloth хуудсууд.
BibTeX: `docs/research_refs.bib` (түлхүүрийг `[...]`-д). Хуудсыг бүтнээр нь уншаагүй, эсвэл манай тооцоо болох тоог
**(баталгаагүй)** гэж тэмдэглэв. Энэ баримт зөвхөн судалгаа — код, тохиргоо өөрчлөөгүй.

**Товч дүгнэлт.**
1. **Онлайн GRPO (Arctic-R1, SQL-R1, Reasoning-SQL-ийн жор) Kaggle T4×2 дээр — NO-GO.** Тэдгээр нь 8–64 ширхэг 80 GB GPU
   хэрэглэсэн. Манайд rollout-ийн vLLM болон сургалт нэг 16 GB карт дээр багтахгүй, 12 цагт ~300 бүлэг (асуулт) л
   боловсруулна.
2. **GO (нөхцөлтэй) — R2-A: гүйцэтгэлээр шүүсэн rejection-sampling SFT (RFT)** `synth_own` дээр, D2-ийн дээр шинэ QLoRA.
   Энэ нь «offline / batch RL»-ийн хамгийн хямд хэлбэр. 2 сесс (~16–20 GPU цаг).
3. **GO (нөхцөлтэй, R2-A-ийн дараа) — R2-B: CoT-тэй DPO (+NLL, ExCoT/IRPO маяг), 1 давталт.** Богино prompt (≤6k токен)
   үед л багтана. 2–3 сесс (~20–28 GPU цаг).
4. ORPO / SimPO / KTO нь нөөц хувилбар. Unsloth нь репод алга, суулгахгүй (хувилбарын түгжээтэй зөрчилдөх эрсдэл).

---

## 0. Орчин ба репод аль хэдийн байгаа зүйл

### 0.1 Kaggle-ийн хязгаар
| Нөөц | Утга | Тэмдэглэл |
|---|---|---|
| GPU | T4×2 (16 GB тус бүр, sm75) эсвэл P100 (16 GB, sm60) | **bf16 алга** → fp16 + GradScaler. FlashAttention-2 алга (sm75); SDPA memory-efficient kernel л |
| Сесс | ~12 цаг, долоо хоногт ~30 цаг | Тасарвал `/kaggle/working`-ийн Output-ыг дараагийн version-д input болгоно |
| Диск | `/kaggle/working` ~20 GB **(баталгаагүй)** | Нэгтгэсэн 7B fp16 ≈ 15.2 GB; хоёрыг зэрэг хадгалахгүй |
| CPU RAM | ~29–31 GB **(баталгаагүй)** | fp16 нэгтгэлд (~15 GB) хүрэлцэнэ |

P100: tensor core-гүй, fp16 нь T4-өөс удаан, bitsandbytes-ийн 4 битийн kernel sm60-д дэмжигдэх эсэх **баталгаагүй**.
Жорыг **зөвхөн T4×2**-д төлөвлөнө.

### 0.2 Сан ба хувилбар (суулгахгүй, байгааг л ашиглана)
- `requirements-gpu.txt`: `vllm`, `transformers>=4.56`, `peft>=0.12`, `trl>=0.20`, `datasets`, `accelerate`, `bitsandbytes>=0.45`.
- `requirements-d3.txt` (Kaggle-д D3-ийн үед түгжсэн, `bootstrap.sh`-ийн дараа): **vllm 0.32.0, transformers 5.19.0, trl 1.16.0,
  peft 0.21.2, accelerate 1.15.0, bitsandbytes 0.50.2**. `sft.py` эдгээрийн эх кодоор шалгагдсан.
- `bootstrap.sh` нь `requirements.txt` + `requirements-gpu.txt`-ийг суулгана, `torchaudio`-г устгана. **Unsloth, DeepSpeed, flash-attn байхгүй.**
- TRL-д `DPOTrainer`, `GRPOTrainer` байна. `ORPOTrainer`, `CPOTrainer` (SimPO нь `loss_type="simpo"`), `KTOTrainer` нь TRL 1.x-д
  үндсэн эсвэл `trl.experimental` дотор байгаа эсэх **баталгаагүй** — Kaggle дээр `import`-оор шалгана.

### 0.3 Репогийн бэлэн дэд бүтэц
| Файл | Юу хийдэг | R2-д |
|---|---|---|
| `training/sft.py` | QLoRA (nf4, double quant), `prepare_qlora`, LoRA-г fp32 болгох, `t4_sdpa_fix()` (sm<80 үед GQA→KV давталт, memory-efficient kernel), completion-only chunked CE (`completion_logits_only`), `deadline_callback` (`T2S_TRAIN_DEADLINE`), `last_checkpoint` → `resume_from_checkpoint`, `merge_adapter` | R2-A шууд ашиглана; R2-B-д бүх хэсгийг дахин ашиглана |
| `training/sampling.py` | vLLM-ээр n хувилбар, reasoning-тай текст хадгална | R2-A/B-ийн өгөгдөл |
| `training/grpo.py` | TRL GRPO, `ExecReward` (1 / 0.1 / 0 — Arctic-ийн reward), vLLM colocate 0.35 | **T4-д ажиллахгүй:** `bf16=True` хатуу бичигдсэн, 4 битийн зам байхгүй, init нь нэгтгэсэн fp16 загвар |
| `scripts/synth/exec_validate.py` | `synth_own`-ийг PG дээр гүйцэтгэж шүүнэ | **Одоогоор ажиллаагүй** (`build/synth_own/validated.jsonl` алга) |
| `training/general_eval.py` | Belebele khk_Cyrl (монгол ерөнхий чадвар) | Мартах эсэхийн хяналт |
| `training/dpo.py` | — | **Байхгүй** (T7-д санал болгосон) |

### 0.4 Бодит хэмжилт — бүх тооцооны суурь (D3, Version 10, 2026-10-19)
- 914 мөр, QLoRA r=32/α=64 all-linear, fp16, gradient checkpointing, completion-only logits, batch 1 × GA 8 × 2 GPU (DDP) = 16 мөр/алхам.
- Алхам бүр ≈ 46k токен, **85–88 с/алхам** → **≈525 токен/с (2×T4), ≈260 токен/с нэг T4** (forward+backward, prompt бүтнээрээ).
- Хамгийн урт мөр 8.3k токен үед **GPU-ийн дээд санах ой 10.1–11.6 GB**. OOM гараагүй (SDPA засвартай).
- 116 алхмын 112-т квотын хязгаарт хүрч checkpoint хадгалаад зогссон → **сесс хоорондын үргэлжлүүлэлт ажилладаг**.

Энэ нь «QLoRA 7B нэг T4 дээр ~200–500 токен/с» гэсэн өмнөх таамгийн доод хэсэгт таарна. Доорх бүх хугацааг **260 токен/с/GPU**-ээр бодов.

---

## 1. Санах ойн тооцоо (нэг T4, 15 GB орчим ашиглах боломжтой)

Qwen2.5-7B: 28 давхарга, hidden 3 584, MLP 18 944, GQA (28 Q / 4 KV head), vocab ~152k, embedding ба lm_head тусдаа.

| Бүрэлдэхүүн | Тооцоо | GB |
|---|---|---|
| Суурь жин nf4 (+ quant тогтмол) | ~6.5B × 0.5 B + ~0.2 | ≈3.5 |
| embedding + lm_head fp16 | 2 × 0.545B × 2 B | ≈2.2 |
| LoRA r=32, all-linear | давхаргад r·Σ(in+out) ≈ 2.88M → ×28 ≈ **80.7M** параметр | — |
| LoRA жин fp32 + grad fp32 + AdamW (2 момент) fp32 | 80.7M × 16 B | ≈1.3 (r=16: 0.65; `paged_adamw_8bit`: −0.5) |
| CUDA context, фрагментац | | ≈0.8–1.0 |
| **Тогтмол дүн** | | **≈7.8–8.0** |
| Activation (GC): давхарга бүрийн оролт L×3 584×2 B ×28 | L=6k: 1.2; L=8.3k: 1.7; L=14.5k: 2.9 | хувьсах |
| Нэг давхаргыг дахин тооцоолох MLP (gate/up/act) ≈ L×18 944×2 B×3 | L=6k: 0.68; L=8.3k: 0.94; L=14.5k: 1.65 | хувьсах |
| Completion-only CE (chunk ≤512 байрлал × 152k × fp32) | | ≤0.3 |

**Шалгалт:** L=8.3k → 7.9 + 1.7 + 0.94 + 0.3 + attention/буфер ~0.5 ≈ **11.3 GB** — хэмжсэн 11.6 GB-тэй таарна.

| Хувилбар | Нэг алхмын токен (нэг GPU) | Тооцоолсон дээд санах ой | Шийдвэр |
|---|---|---|---|
| SFT, богино prompt (H4s ~5.5k + гаралт ~0.6k ≈ 6.1k) | 6.1k | ≈10 GB | багтана |
| SFT, M1a-ийн урт RAG prompt (~13.5k + 1k ≈ 14.5k) | 14.5k | ≈13.5–14 GB **(баталгаагүй)** | хил дээр; r=16 + 8bit Adam-аар |
| DPO, богино prompt (chosen+rejected = batch 2 × 6.1k) | 12.2k (2 дараалал) | ≈12–13 GB | багтана (reference = adapter унтраасан, нэмэлт жингүй) |
| DPO, урт prompt (2 × 14.5k) | 29k | ≈17 GB | **OOM** |
| GRPO, богино prompt, 8 rollout (8 × 6.1k) | 49k (micro-batch 1–2-оор) | сургалт багтана, гэхдээ rollout-ийн vLLM-д зай үлдэхгүй | §4 |

**vLLM-ийн асуудал.** 7B fp16 жин = 15.2 GB → нэг T4-д KV cache-тэй багтахгүй. Манай inference нь хоёр T4-ийг tensor-parallel (TP=2)
хэрэглэдэг. Тиймээс «нэг GPU rollout, нөгөө нь сургалт» (TRL `vllm_mode="server"`) боломжгүй, «colocate» (нэг GPU дээр
хамт) бүр боломжгүй. vLLM-ийг bitsandbytes 4 бит + LoRA-тай ачаалах нь онолын хувьд боломжтой ч удаан, TRL-ийн жин
синхрончлолтой хамт T4-д туршигдаагүй **(баталгаагүй)**. Иймд **үүсгэх ба сургах үе шатыг ээлжлүүлэх** (offline) нь цорын ганц бодит
загвар: vLLM TP=2-оор түүвэрлэх → vLLM-ийг хаах → QLoRA DDP-ээр сургах.

---

## 2. Аргуудын харьцуулалт (T4×2-т тохирох байдал)

| Арга | Санах ой (D2 дээр) | Нэг жишээний зардал (SFT = 1) | Өгөгдөл | SQL/reasoning дээрх нотолгоо | Манайд |
|---|---|---|---|---|---|
| **RFT / rejection-sampling SFT** | 1 дараалал | 1 | зөв (CoT+SQL) | Arctic-R1-ийн өгөгдөл шүүлт; OmniSQL; T8-ийн хямд хувилбар | **R2-A** |
| **DPO** [`rafailov_2023_dpo`] | 2 дараалал; ref = adapter унтраасан | ≈2.7 (policy 2×, ref 2×⅓) | зөв/буруу хос | ExCoT: CoT-тай DPO ахиц, CoT-гүй DPO ~2%-иар бууруулсан [`zhai_2025_excot`, `liu_2025_cot_dpo_text2sql`] | **R2-B** |
| **DPO + NLL (RPO/IRPO)** [`pang_2024_irpo`] | DPO-той ижил | ≈2.7 | хос | Reasoning-д NLL нэмэх нь чухал гэж IRPO харуулсан (GSM8K Llama-2-70B 55.6 → 81.6 **(баталгаагүй: хураангуй)**) | R2-B-ийн анхдагч loss |
| **KTO** [`ethayarajh_2024_kto`] | 1 дараалал + KL-ийн нэмэлт forward | ≈1.7 **(баталгаагүй)** | хосгүй (зөв эсвэл буруу) | SQL дээр нотолгоо олдоогүй | хос цөөн бол нөөц |
| **ORPO** [`hong_2024_orpo`] | 2 дараалал, ref-гүй | ≈2 | хос | SFT-гүйгээр base-ээс сургах зориулалттай; reasoning/SQL-д нотолгоо олдоогүй | нөөц |
| **SimPO** [`meng_2024_simpo`] | 2 дараалал, ref-гүй | ≈2 | хос | Chat-ийн benchmark дээр DPO-оос илүү; LR/γ-д мэдрэмтгий; SQL-д нотолгоо олдоогүй | эрсдэлтэй |
| **GRPO (онлайн)** [`shao_2024_deepseekmath`] | 8–16 rollout + vLLM | ≫ (§4) | асуулт + gold | Arctic-R1, SQL-R1, Reasoning-SQL | **NO-GO** |

Reference-free аргууд (ORPO/SimPO) манайд санах ой хэмнэхгүй: LoRA-тай DPO-д reference нь adapter-ийг унтраасан
**ижил жин** тул нэмэлт GB шаардахгүй, зөвхөн нэмэлт forward (эсвэл `precompute_ref_log_probs`-оор нэг удаа). Тиймээс
нотолгоо илүүтэй DPO(+NLL)-ийг сонгоно.

---

## 3. Жорууд

### Нийтлэг: D2-той хэрхэн нийлүүлэх вэ
vLLM нэг хүсэлтэд хоёр LoRA давхарлаж чадахгүй тул D2-ийг заавал жинд нэгтгэнэ.

1. **Сонгосон (давхарлах):** D2-ийг fp16 суурьт нэгтгэсэн жин (`t2s-adapter-d2`-ээс, H4s/M1s2-д аль хэдийн хэрэглэгддэг) → nf4-ээр
   ачаалах → **шинэ LoRA (D5/D6)**. DPO-ийн reference = adapter унтраасан = D2-ийн бодлого. Inference: D5-ийг **fp16
   D2-нэгтгэсэн** жинд нэгтгэх (`sft.merge_adapter(..., base=<d2_merged>)`, D3-ийн `merge_lora.py`-тай ижил), эсвэл vLLM
   `--enable-lora`-оор D2-нэгтгэсэн загвар дээр D5-ийг adapter болгон.
   QLoRA adapter-ийг nf4 биш fp16 жинд нэгтгэхэд бага зэргийн зөрүү гарна — D3-т мөн адил байсан.
2. Альтернатив: D2 adapter-ийг үргэлжлүүлэн сургах (`is_trainable=True`). Гэхдээ D2 bf16 суурь дээр (A100) сургагдсан,
   nf4 дээр үргэлжлүүлэх нь шилжилт үүсгэнэ; DPO-ийн reference-д D2-ийн хуулбар (TRL `ref_adapter_name`) хэрэгтэй —
   TRL 1.16-д дэмжлэг **баталгаагүй**. Санал болгохгүй.

### Нийтлэг: prompt
DPO-д (2 дараалал) M1a-ийн ~13.5k токены prompt багтахгүй (§1). Иймд **R2-д зорилтот inference тохиргоо = богино prompt**
(H4s / I3s3 / M1s3 маяг: schema linking, median ~5.5k, хамгийн урт ~8.3k; `max_length` 9216). Prompt-ыг сургалт ба
inference-д **яг ижил** байлгана (D2-ийн 344 мөр хасагдсан алдааг давтахгүй).
**Урьдчилсан нөхцөл:** M1s2 (богино prompt + D2) dev-ийн дүн M1a-тай ойролцоо байх ёстой. M1s3/M1s2 нь V20+-д төлөвлөгдсөн
боловч WORKLOG-д дүн алга. Хэрэв M1s2 нь M1a-аас ≥5 хувиар доогуур бол R2-ийн ахиц эхлээд тэр зөрүүг нөхөх ёстой —
тэгвэл R2-B-ийг хийх нь утгагүй, R2-A-ийг урт prompt-оор (r=16, хил дээр) л оролдоно.

### R2-A. Гүйцэтгэлээр шүүсэн rejection-sampling SFT (RFT) — эхний алхам
**Өгөгдөл.**
1. `exec_validate.py`-гаар `build/synth_own/candidates.jsonl` (2 779) → `validated.jsonl`. Хүлээгдэж буй үлдэц 50–70% →
   **~1 400–1 950 асуулт (баталгаагүй, хэмжээгүй)**.
2. D2-нэгтгэсэн загвар + зорилтот prompt-оор vLLM TP=2, **n=8, T=0.8, top_p 0.95, max_tokens 2048** (`sampling.py`).
   Гүйцэтгэлээр шалгана (gold-ийн үр дүнгийн hash).
3. Хадгалах: 1–7/8 зөв асуултаас (MaybeKnown [Gekhman, FINETUNE_LIT_REVIEW §2]) **ялгаатай зөв гаралт ≤2** (SQL-ийг
   хэвийн болгоод давхардлыг хасна). 8/8 зөв асуултаас 0–1 (аль хэдийн мэддэг, жин бага). 0/8 асуултыг **хасна**
   (Unknown → hallucination нэмнэ; gold-guided CoT нь энэ жорд орохгүй).
   `</answer>`-гүй (тасарсан) гаралтыг хасна (`drop_truncated`).
4. Replay (мартахаас сэргийлэх): D3-ийн SFT мөрөөс өөрийн DB-ийн human 160 + BIRD-ийн 200–300 (Kaggle-д BIRD DB байхгүй тул
   хадгалсан prompt/гаралтаар). Нийт synth_own-ийн мөр ≤ replay-ийн 3 дахин (SYNTH_OWN §5-ийн дүрэм).
5. Family-ийн 10%-ийг **validation** болгон тусгаарлана (dev биш) — loss-ийн early stopping.

Хүлээгдэх хэмжээ: ~800–1 500 synth мөр + ~400 replay ≈ **1 200–1 900 мөр** (баталгаагүй).

**Гиперпараметр** (`config/training.yaml`-ийн `d3`-аас; өөрчлөлтийг тодоор):
| Талбар | Утга | Шалтгаан |
|---|---|---|
| quantize_4bit / fp16 / bf16 | true / true / false | T4 |
| lora_r / alpha / dropout | **16 / 32** / 0.05, all-linear | D2-ийн дээр «жижиг засвар»; LoRA бага rank → бага мартах [`biderman_2024_lora_forgets`] |
| learning_rate | **5e-5**, cosine, warmup 3% | D2 аль хэдийн сургагдсан; d3-ийн 2e-4 нь шинэ даалгаварт |
| epochs | **1** | Gekhman: early stopping; синтетик өгөгдөл |
| per_device_batch / GA / GPU | 1 / 8 / 2 → 16 мөр/алхам | D3-тай ижил (хэмжигдсэн) |
| max_length | 9216 | H4s-ийн хамгийн урт мөр 8.3k |
| gradient_checkpointing, completion_logits_only | true, true | D3-ийн санах ойн шийдэл |
| optim | adamw (OOM бол `paged_adamw_8bit`) | |
| save_steps / save_total_limit | **10 / 2** | 12 цагийн хязгаар |
| T2S_TRAIN_DEADLINE | сессийн эхнээс +10.5 ц | бүх процесс хамт checkpoint хадгалж зогсоно |

**Хугацаа.** 1 500 мөр × ~6.1k токен = 9.2M токен ÷ 525 токен/с ≈ **4.9 цаг** (1 эпох). 1 200 мөр бол ≈3.9 ц,
1 900 бол ≈6.1 ц. Алхам: 1 500/16 ≈ 94 алхам × ~98 с (D3-аас урт мөр) — **(баталгаагүй, D3-аас шугаман тооцоо)**.

### R2-B. CoT-тэй DPO + NLL (ExCoT / IRPO маяг), 1 давталт
**Өгөгдөл.** R2-A-ийн 2-р алхмын түүврийг дахин ашиглана (эсвэл R2-A-ийн adapter-аар шинээр түүвэрлэвэл on-policy — 2 дахь давталт).
- Хос: 1–7/8 зөв асуултаас **chosen** = зөв бүлгийн гаралт (хамгийн богино `<think>`-тэйг сонгох — урт хазайлтаас сэргийлэх),
  **rejected** = хамгийн их санал авсан **буруу** бүлгийн гаралт (ExCoT/T7). Асуулт бүрт ≤2 хос.
- Хасах: timeout, гүйцэтгэлийн алдаа ба буруу үр дүнг ялгах — rejected-д «алдаатай SQL»-ээс илүү «ажилласан ч буруу үр дүн»
  давуу (хосын ялгаа нарийн, конвенцийн алдааг онилно: POSTED, MNT, idle filter). Liu нарын «timeout-оос үүдэх шуугиан»-ыг хасна.
- 0/8 ба 8/8 асуулт хос үүсгэхгүй. Хэрэв ийм асуулт олон бөгөөд хос < 400 бол **KTO** (хосгүй) руу шилжинэ.
- Хүлээгдэх тоо: **600–1 200 хос (баталгаагүй)**.

**Гиперпараметр** (TRL `DPOConfig`; T4-д зориулсан):
| Талбар | Утга | Тэмдэглэл |
|---|---|---|
| loss | `sigmoid` + `rpo_alpha=1.0` (chosen дээрх NLL) | IRPO [`pang_2024_irpo`]; TRL 1.16-д `rpo_alpha` байгаа эсэх **баталгаагүй** — байхгүй бол `loss_type=["sigmoid","sft"]` эсвэл custom |
| beta | 0.1 | ExCoT/T7 |
| learning_rate | **5e-6**, cosine, warmup 5% | LoRA-тай DPO-д 5e-7 (бүтэн) – 5e-5-ын хооронд; бага талаас |
| LoRA | r 16 / α 32, all-linear, D2-нэгтгэсэн nf4 дээр | ref = adapter унтраасан |
| per_device_batch / GA / GPU | 1 хос / 8 / 2 → 16 хос/алхам | |
| max_prompt_length / max_length | 8192 / 9216 | урт хосыг хасна (тасалж болохгүй — `</answer>` алдагдана) |
| precompute_ref_log_probs | true | ref-ийн forward-ыг нэг удаа; сесс 2-т дахин тооцохгүйн тулд dataset-д хадгална |
| epochs | 1 | |
| fp16, GC, `t4_sdpa_fix`, LoRA fp32 | true | `sft.py`-гийн функцуудыг дахин ашиглана |

**Хугацаа.** Хос = 2 × 6.1k = 12.2k токен (policy fwd+bwd) + ref-ийн forward (fwd+bwd-ийн ~⅓) ≈ 16.3k «SFT-тэй тэнцэх токен».
800 хос × 16.3k ÷ 525 ≈ **6.9 цаг**; 600 хос ≈ 5.2 ц; 1 200 хос ≈ 10.3 ц (2 сесс). Энэ нь T7-ийн «4–7 цаг»-тай таарна
**(баталгаагүй)**. Ахиц бага бол 2 дахь (on-policy) давталт = +1 sampling сесс + +1 сургалтын сесс.

**Санах ой.** ≈12–13 GB (§1). OOM бол: `max_length` 8192, r=8, `paged_adamw_8bit`.

---

## 4. Онлайн GRPO яагаад Kaggle-д тохирохгүй вэ (тоогоор)

| Ажил | Тохиргоо | Тоног төхөөрөмж | BIRD dev 7B |
|---|---|---|---|
| Arctic-Text2SQL-R1 [`yao_2025_arctic_text2sql_r1`] | GRPO онлайн, 16 rollout, batch 256, update batch 128, β=0.001, clip 0.2, T=0.8; reward 1 / 0.1 / 0; OmniSQL SFT checkpoint-оос; BIRD+Spider ~16k + Gretel 11.8k (32B-ээр 10-аас ≥1 зөв гэж шүүсэн) | 8 node × 8 H100-80GB, 7B-д «хэдэн цаг» | 68.9 |
| SQL-R1 [`ma_2025_sql_r1`] | GRPO, 8 rollout, LR 3e-7, max response 2048; reward: формат ±1, гүйцэтгэл ±2, үр дүн ±3, урт; SynSQL-Complex-5K | 8 × 80 GB | 63.1–66.6 (хуудас дотроо зөрүүтэй) |
| Reasoning-SQL [`pourreza_2025_reasoning_sql`] | GRPO, 6 rollout, LR 1e-6 тогтмол, batch 32, 3 эпох, BIRD train 8k; хэсэгчилсэн reward (EX 3, LLM шүүгч 2, syntax/schema/n-gram 1) | 8 × H100 | 64.01 (SFT 61.53, base 58.73) |
| Манай Kaggle | 2 × T4 16 GB, fp16 | — | — |

Arctic-ийн тайлан «batch (offline) RL нь онлайнаас муу» гэсэн. Гэвч онлайн манайд хэрэгжихгүй:
- **Rollout:** vLLM-гүй HF `generate` (nf4, GC-тэй PEFT) T4 дээр batch 8 × 600 токен ≈ 40–80 с/бүлэг **(баталгаагүй)**.
- **Сургалт:** 8 × 6.1k = 49k токен/бүлэг ÷ 260 ≈ **190 с**. TRL нь rollout бүрт prompt-ыг дахин тооцдог (prefix хуваалцахгүй).
- Нийт ≈4–5 мин/асуулт/GPU → 2 GPU-д **~25–30 асуулт/цаг → 12 цагт ~300 бүлэг**. Unsloth-ийн заавар «reward өсөхөд ≥300 алхам,
  ~12 цаг» гэдэг [`unsloth_2025_grpo`] — тэр нь нэг сессийн бүх квот, Arctic-ийн 1 алхам (256 асуулт × 16) -тай бараг тэнцүү.
- M1a-ийн урт prompt-оор (14.5k) бол ×2.4 удаан.
- `grpo.py` нь `bf16=True` хатуу, 4 битгүй, colocate vLLM (0.35) — T4-д код өөрчлөхгүйгээр ажиллахгүй.

Unsloth нь GRPO-ийн санах ойг 8+ дахин бууруулж, vLLM-тэй санах ой хуваалцдаг гэж мэдэгддэг (Llama-3.1-8B, 20k context:
54.3 GB vs TRL+FA2 510.8 GB) ба «QLoRA-д VRAM ≈ параметрийн тоо (GB)» гэсэн дүрэм өгдөг [`unsloth_2025_grpo`]. 7B + vLLM нэг T4-д
багтаж магадгүй ч (а) репод алга, суулгахыг хориглосон, (б) transformers 5.19 / trl 1.16-ийн түгжээтэй нийцэх эсэх **баталгаагүй**,
(в) 12 цаг / ~300 алхмын хурдны хязгаар хэвээр. → **NO-GO** энэ диплом-д.

TTRL [`zuo_2025_ttrl`]: онлайн RL шаардана → мөн NO-GO; олонхын саналаар шүүсэн SFT (T8) = R2-A-ийн gold-гүй хувилбар.

---

## 5. Сесс хоорондын checkpoint

- **Байгаа механизм:** `T2S_TRAIN_DEADLINE` → `deadline_callback` бүх DDP процессыг хамт зогсоож checkpoint хадгална (exit 0);
  дараагийн version-д өмнөх Output-ыг input болгоход `last_checkpoint()` → `resume_from_checkpoint`. D3 (112/116) дээр батлагдсан.
- **Checkpoint-ийн хэмжээ:** LoRA r=16 (40M) → adapter fp32 ~160 MB + Adam 2× ~320 MB + scheduler/RNG → **~0.5 GB**; r=32 бол ~1 GB.
  `save_total_limit 2` → ≤2 GB. `save_steps 10` (≈15–25 мин тутам) — квот дуусахад алдах ажил ≤25 мин.
- **Детерминизм:** seed 42, dataset-ийн дарааллыг файлаар хөлдөөх (shuffle-ийг build үед), SFT/DPO мөрийн jsonl-ийг Output-д хамт хадгалах
  (D3-ийн `d3__arctic-r1-7b.jsonl`-тэй ижил) — дараагийн сесс ижил өгөгдлөөр үргэлжилнэ.
- **DPO:** `precompute_ref_log_probs`-ийн үр дүнг dataset-тэй хамт хадгалж, үргэлжлүүлэхэд дахин тооцохгүй.
- **Төсөв:** сесс бүрт bootstrap/PG/индекс ~30–40 мин, загвар ачаалах/нэгтгэх ~20–30 мин → сургалтад 12 цагаас ~10.5 цаг үлдэнэ.
- Нэгтгэсэн fp16 загвар (15.2 GB) ба D2-нэгтгэсэн суурь хоёрыг `/kaggle/working`-д зэрэг бүү хадгал — суурь нь dataset input-ээс уншигдана.

---

## 6. Мартахаас (catastrophic forgetting) сэргийлэх

1. **D2-ийн дээр шинэ, жижиг LoRA** (r 16) + бага LR + 1 эпох. LoRA нь бүтэн fine-tune-оос бага мартдаг (код/математикийн IFT,
   ялгаа кодод их) [`biderman_2024_lora_forgets`].
2. **Arctic-ийн `<think>…</think><answer>` хэлбэрийг хадгалах:** зорилтот гаралт = загварын **өөрийн** зөв гаралт (on-policy),
   gold SQL-ийг гараар бичсэн CoT-оор орлуулахгүй.
3. **NLL-тай DPO (RPO/IRPO)** — DPO нь chosen-ийн магадлалыг ч бууруулж болдог; NLL нэмж тогтворжуулна [`pang_2024_irpo`].
4. **Replay:** human 160 + BIRD 200–300 (хадгалсан prompt).
5. **Хяналт (CPU/GPU хямд):** (а) synth family-ийн 10%-ийн validation loss / DPO reward margin; (б) `general_eval.py` Belebele
   khk_Cyrl 300 — D2-оос ≥3 хувиар буурвал хаях; (в) **dev**-ийн «шинээр алдсан» асуулт ≤ ахицын хагас.
6. **Синтетик тархалтын хазайлт:** `synth_own` нь 7 хэв маягтай (CTE, LAG, валют...) — dev-ийн энгийн асуултууд дээр
   «хэт төвөгтэй SQL» бичих хандлага гарч болно. Dev-ийг хэв маягаар (cte, time_series, multi_currency, ratio, cross, энгийн) задалж харна.

---

## 7. Хуваарь ба нийт GPU цаг (T4×2)

| Сесс | Ажил | Цаг |
|---|---|---|
| S0 (урьдчилсан, бусад run-д багтааж болно) | M1s2 dev (богино prompt + D2) — суурь хяналт, хэрэв хийгдээгүй бол | ~1.5 |
| S1 | bootstrap → `exec_validate` (PG, CPU) → D2-нэгтгэсэн загвар vLLM TP=2: 1 400–1 950 асуулт × n=8 → гүйцэтгэлийн шошго → R2-A/R2-B өгөгдөл Output-д | prefill ~8M токен + decode ~7M токен ≈ **3–5 (баталгаагүй)** |
| S2 | R2-A SFT (1 эпох) → нэгтгэх (D2-нэгтгэсэн fp16 + D6) → dev `M1s6` | 4–6 + 1.5 |
| S3–S4 (R2-A амжилттай эсвэл квот үлдвэл) | R2-B DPO+NLL → нэгтгэх → dev `M1s5` | 5–10 + 1.5 |
| Эцэст | Батлагдсан нэг adapter-ийг **test-д нэг удаа** | ~1.5 |

R2-A: **≈12–15 GPU цаг**; R2-A + R2-B: **≈25–32 GPU цаг** (≈ нэг долоо хоногийн квот). Шинэ `training/dpo.py`, `config/training.yaml`-ийн
жор, `experiments.yaml`-ийн run нь код өөрчлөлт шаардана (энэ баримтад хийгээгүй).

---

## 8. Эрсдэл

| Эрсдэл | Магадлал | Нөлөө | Бууруулах |
|---|---|---|---|
| `exec_validate`-ийн үлдэц бага (хоосон үр дүн олон) | дунд | өгөгдөл < 800 → ахиц ~0 | хос < 400 бол KTO; хэв маягаар тэнцвэржүүлэх |
| Богино prompt-ын суурь (M1s2) M1a-аас доогуур | **тодорхойгүй** (хэмжээгүй) | R2 ахиц нь зөрүүг нөхөхгүй | S0-ийг эхэлж; зөрүү ≥5 хувь бол R2-B-гүй |
| Синтетик → бодит асуултын шилжилт (MADLAD орчуулга, хэллэг) | өндөр | dev ахиц 0 | dev-ийн хэв маягийн задаргаа; synth-ийг replay-тэй холих |
| D2-ийн чадварыг эвдэх | дунд | dev буурах | §6; dev < M1s2 бол хаях |
| T4 fp16-ийн тогтворгүй байдал (DPO logit-ийн ялгаа, overflow) | дунд | NaN loss | LoRA fp32, GradScaler, `max_grad_norm 1.0`, LR бага |
| TRL 1.16-ийн API (`rpo_alpha`, KTO/ORPO-ийн байршил) | дунд | код засах цаг | Kaggle-д `import`-оор урьдчилан шалгах |
| Kaggle квот (V26/V27-ийн ажилтай өрсөлдөнө) | өндөр | R2-B хийгдэхгүй | R2-A-г эхэлж, R2-B-г «квот үлдвэл» |
| Хэмжилтийн шуугиан (dev 224, ±6–7 хувь) | өндөр | хуурамч «ахиц» | McNemar, урьдчилан тогтоосон дүрэм (≥+2, алдагдал ≤ ахиц/2) |
| Leakage | бага | дүн хүчингүй | `synth_own` нь dev/test-тэй 3-gram/SQL/үр дүнгийн hash-аар шүүгдсэн; **test-ийн gold-ийг сургалт, сонголтод хэзээ ч хэрэглэхгүй** |

---

## 9. Go / No-go

| Сонголт | Шийдвэр | Нөхцөл |
|---|---|---|
| Онлайн GRPO (Arctic/SQL-R1/Reasoning-SQL-ийн жор) | **NO-GO** | 8–64 × 80 GB-ийн жор; Kaggle-д 12 цагт ~300 бүлэг, vLLM багтахгүй, `grpo.py` T4-д ажиллахгүй |
| Unsloth руу шилжих | **NO-GO** (одоо) | репод алга, суулгахыг хориглосон, хувилбарын түгжээ |
| **R2-A RFT (synth_own, D2 дээр r16)** | **GO (нөхцөлтэй)** | (1) `exec_validate` ≥ 800 асуулт; (2) M1s2 dev нь M1a-аас ≤5 хувь доогуур; (3) ≥15 GPU цаг үлдсэн (T1/T2/T9-ийн дараа) |
| **R2-B DPO+NLL** | **GO (R2-A-ийн дараа)** | R2-A dev дээр M1s2-оос доогуур биш ба хос ≥ 400; ≥ 12 GPU цаг үлдсэн |
| KTO / ORPO / SimPO | нөөц | хос цөөн (KTO) эсвэл DPO NaN/тогтворгүй үед |

**Шийдвэрийн дүрэм (dev, урьдчилан тогтоосон):** шинэ adapter-тай run нь **ижил prompt + D2** (M1s2)-оос McNemar p < 0.05, эсвэл
≥ +2 асуулт ба шинээр алдсан ≤ ахиц/2, Belebele-ийн уналт < 3 хувь. Давбал л чуулгад гишүүн болгон нэмж, test-ийг эцэст нь нэг удаа.

**Хүлээгдэх ахиц (таамаг):** R2-A 0…+4 асуулт (dev), R2-B нэмж 0…+6. ExCoT/Reasoning-SQL-ийн +2.5…+11 хувь нь
том өгөгдөл (8k–16k асуулт) ба онлайн/давталттай; манайд 1 давталт, ~1k хос тул доод талдаа. **Дипломын хувьд** R2-A нь
«өөрийн DB-ийн гүйцэтгэлээр шалгасан синтетик өгөгдлөөр T4 дээр 7B-ийг дасгах» гэсэн хэрэгжих, давтагдах үр дүн —
ахиц 0 гарсан ч Ovadia / Gekhman-ий олдвортой (FINETUNE_LIT_REVIEW) уялдуулж тайлбарлаж болно.

---

## Эх сурвалж (2026-10-30)
- Arctic-Text2SQL-R1: https://arxiv.org/html/2505.20315 (жорын хэсгийг уншсан; LR, max length тайланд байхгүй)
- SQL-R1: https://arxiv.org/html/2504.08600 (жорын хэсгийг уншсан; 7B BIRD dev 63.1 / 66.6 зөрүүтэй)
- Reasoning-SQL: https://arxiv.org/html/2503.23157 (Appendix A.1, Хүснэгт 2)
- Unsloth GRPO: https://unsloth.ai/docs/basics/reasoning-grpo-and-rl , https://unsloth.ai/blog/grpo (хайлтын хураангуй; blog/r1-reasoning 403)
- LoRA Learns Less and Forgets Less: https://arxiv.org/abs/2405.09673 (хайлтын хураангуй)
- DPO, ORPO, SimPO, KTO, IRPO: доорх bib-ийн arXiv холбоос (агуулгыг ерөнхий мэдлэгээс; энэ сессэд бүтнээр уншаагүй — тоонууд **баталгаагүй**)
- Дотоод: `docs/IMPROVE_80_PLAN.md` (D3: 85–88 с/алхам, 10.1–11.6 GB), `docs/WORKLOG.md` §6.10, `config/training.yaml`
