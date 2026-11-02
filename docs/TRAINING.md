# Сургалт (C, D, F4, F6, F10)

Сургалт A100 80 GB дээр явагдана: GRPO-д rollout ба сургалт нэг GPU-д зэрэг багтах ёстой. sqrl-9b-ийн RAG-тай SFT
L4-д багтахгүй. Скрипт: `scripts/server/train_a100.sh <model>` — хоёр үе шат: `STEPS="data sample build sft"`, дараа нь L4 дээрх үнэлгээгээр C-шилдгийг сонгоод `STEPS=grpo BEST_SFT=...`. Бүх үе шатад `BEST_A` заавал (сургалтын prompt = A-шилдэг). Тохиргоо: `config/training.yaml`.

## Өгөгдөл

| Эх сурвалж | Лиценз | Ашиглалт |
|---|---|---|
| BIRD train (9.4k асуулт, 69 SQLite DB) | CC BY-SA 4.0 | 5k асуулт сонгоно (DB-ээр давхаргалж), BIRDTurk маягаар монгол руу орчуулна |
| Өөрийн DB-ийн синтетик (C3) | — | Хүний жишээний SQL-ийн тогтмолыг сольж, LLM-ээр монгол асуулт бичүүлнэ, гүйцэтгэлээр шалгана |
| Жишээний сан (`knowledge/examples.jsonl`) | — | 160 асуулт (eval-д ороогүй; eval set-тэй ижил аргаар LLM агентаар ноороглож, gold-ийг гүйцэтгэлээр баталгаажуулсан) |
| Spider (сонголттой) | CC BY-SA 4.0 | `data/train_raw/spider`-д гараар байршуулбал |

Орчуулга ба синтез Apache лицензтэй загвараар (qwen3.5-9b) хийгдэнэ. NLLB-200 нь CC-BY-NC тул хэрэглэхгүй.

- **Галиг.** Сургалтын асуултын 30%-ийг галиг хувилбараар үүсгэнэ.
- **Давхардлын шүүлт (`t2s_train dedup`).** Dev/test асуулт ба тэдгээрийн хувилбартай ойролцоог хасна:
  embedding cosine ≥ 0.9 (bge-m3), эсвэл тэмдэгтийн n-gram ≥ 0.8. Мөн өөрийн DB дээр eval-ийн gold-той ижил үр дүнтэй
  мөрийг хасна.

```bash
t2s_train fetch-bird          # data/train/bird_selected.jsonl
t2s_train translate           # → bird_mn.jsonl (qwen3.5-9b асаалттай)
t2s_train synth               # → own_synth.jsonl
t2s_train dedup               # → bird_mn.jsonl (шүүсэн), own_synth_clean.jsonl, human.jsonl, dedup_report.json
```

## C1/C2 — rejection sampling ба CoT

```bash
t2s_train sample --model arctic-r1-7b --source bird_mn.jsonl         # 8 гаргалт (T 0.8), зөвийг ≤ 4 хадгална
t2s_train cot    --model arctic-r1-7b --source bird_mn.jsonl         # n_correct = 0 асуултад gold-guided reasoning
t2s_train sample --model arctic-r1-7b --source bird_mn.jsonl --variants en     # C4/C7-ийн англи гаргалт
```

- **Sampling.** Prompt нь үнэлгээний A-шилдэг протоколтой ижил. BIRD асуултад evidence-ийг (BIRD-ийн стандарт оролт) оруулна; F1 (A1 аналог) үнэлгээ evidence-гүй хэвээр. sqrl-ийн
  `<sql>`-ээр DB судалсан алхмууд траектор хэлбэрээр хадгалагдана.
- **C2 шүүлт.** «Gold-ыг шууд хуулсан» гаргалтыг хасна: эцсийн хариултын SQL-ээс бусад хэсэгт gold SQL үгчлэн гарсан,
  эсвэл hint-ийг дурдсан («the note», «private», «correct query», «given/provided SQL» г.м., `cot.drop_phrases`).
  Completion бүхэлдээ (reasoning + `<answer>`-ийн хураангуй) шалгагдана — доорх «2026-10-30 засвар» (B2). Сургалтын
  prompt-оос hint-ийг хасна.
- **stats.jsonl.** Асуулт бүрийн n_correct-ийг хадгална. GRPO-д «заримдаа шийддэг» (1..7/8) асуултыг эндээс сонгоно.

## SFT (LoRA)

```bash
t2s_train build --recipe c3 --model arctic-r1-7b     # data/train/sft/c3__arctic-r1-7b.jsonl ({prompt, completion})
t2s_train sft   --recipe c3 --model arctic-r1-7b     # adapters/c3__arctic-r1-7b/
```

Hyperparameter (`sft_defaults`): LoRA r = 32, α = 64, бүх linear давхарга, LR 2e-4, cosine, 2 epoch, max_length 8192,
bf16, gradient checkpointing, loss зөвхөн completion дээр.

| Жор | Онцлог |
|---|---|
| c1 | Rejection |
| c2 | + cot |
| c3 | + synth + human |
| c4_en30 / c4_en50 | Англи 30% / 50% |
| c5_r64 | r = 64 |
| d2 | RAG-тай prompt: C-ийн гаргалтыг дахин ашиглаж prompt-ыг B5-аар солино |
| f6 | «Restated: …» pivot |
| f10 | sqrl, search_values траектор |

Qwen3.5 (sqrl) нь `...ForConditionalGeneration` архитектуртай тул `AutoModelForImageTextToText`-оор ачаална. Unsloth
нь Qwen3.5-ийн bf16 LoRA-г дэмждэг (сонголттой хувилбар).

## GRPO (C6, C7, D4, F4)

```bash
t2s_train merge --model arctic-r1-7b --adapter adapters/c3__arctic-r1-7b --out adapters/c3__arctic-r1-7b__merged
t2s_train grpo  --recipe c6 --model arctic-r1-7b --init adapters/c3__arctic-r1-7b__merged
```

- **Reward:** 1 (үр дүн таарсан), 0.1 (ажилласан ч буруу), 0 (алдаа).
- **Тохиргоо:** 8 rollout, T 0.8, β 0.001, LR 1e-6, 2000 «заримдаа шийддэг» асуулт. TRL GRPOTrainer, vLLM colocate.
- **C7.** Англи асуултыг 25% replay хийнэ.
- **F4.** Асуулт бүрийн 3 хувилбарыг (mn, latin, en) дараалуулж нэг generation batch-д оруулна. Гурвуулаа ижил үр
  дүн өгвөл +0.2 нэмнэ.
- **sqrl.** RL нь нэг ээлжит (A5 протокол, max_steps = 0): TRL GRPO олон ээлжит tool орчин дэмждэггүй. Тиймээс eval_adapters.sh нь sqrl-ийн GRPO загварыг нэмэлтээр `--set agent_max_steps=0 --tag s0`-оор (сургалттай ижил протоколоор) үнэлнэ.
- **Гаралт.** LoRA-г эхлэлийн загвартай нэгтгэж (`adapters/c6__<model>__merged`) vLLM-д `SERVED=c6`-ээр ажиллуулна.

## Мартагдал ба ерөнхий чадвар

- **Англи dev:** `--variants en`.
- **BIRD-dev (англи):** `eval --run F1 --set adapter=c3 --tag c3 --variants en`.
- **Монгол ерөнхий (Belebele khk_Cyrl, 300 асуулт):**
  `t2s_train general-eval --model arctic-r1-7b --served-name c3`. Суурь загвартай харьцуулна.

`eval_adapters.sh` эдгээрийг автоматаар ажиллуулна.

## 2026-10-30 засвар (сургалтын кодын review: B2–B10, L5)

Одоо байгаа өгөгдөл, adapter (c1–c3, d2, d3, MADLAD M1) хөндөгдөөгүй. Доорх засвар зөвхөн **дараагийн** ажиллуулалтад
нөлөөлнө. Тест: `tests/test_training_fixes.py`, `tests/test_training_fixes_sft.py`.

### Зан төлөв өөрчлөгдсөн (зөв болгох засвар — дараагийн ажиллуулалтад)

| # | Өмнө | Одоо |
|---|---|---|
| B2 | C2-ийн шүүлт зөвхөн `<answer>`-ийн өмнөх reasoning-ийг харж, «note», «private», «correct query» зэргийг таньдаггүй байсан. Hint нь «Private note … a correct query is …» гэж бичигдсэн | Completion бүхэлдээ шалгагдана (reasoning, `<answer>`-ийн хураангуй, sqrl-ийн бүх ээлж). Gold SQL-ийг эцсийн хариултын SQL-ээс бусад хэсэгт хайна (sqrl-ийн судлах `<sql>` алхам орно). `drop_phrases`-д note, private, correct/provided/given/reference query, тэмдэглэл, өгөгдсөн асуулга нэмэгдсэн. Хэллэг үгийн эхнээс таарна: «denote»-д «note» таарахгүй, «gold_sql»-д «gold» таарна. Hint-гүй prompt-д (асуулт, схем, evidence, sqrl-ийн observation) аль хэдийн байгаа хэллэгийг тухайн гаргалтад тооцохгүй. Жишээ нь асуулт «private schools» гэж агуулсан, эсвэл sqrl prompt «the hints» гэж агуулсан тохиолдол. `cot`-ийн гаралтад `dropped_by_reason` (`gold_verbatim`, `phrase:<хэллэг>`), мөн лог бичигдэнэ. «Note that …» гэх мэт ердийн хэллэг ч хасагдана (алдагдал нь хуурамч «тэмдэглэл» сурахаас бага). `phrase:note` олон гарвал жагсаалтыг «the note»/«private note» болгож нарийсгана |
| B3/F9 | plain/rag горимын prompt `item_id=None`-оор угсрагддаг байсан. Тиймээс RAG-тай (B5: D2/D4) prompt-д human мөр жишээний сангаас **өөрийгөө** few-shot болгон хардаг байв | Бүх горимд (plain/rag/h4s) мөрийн id ба family дамжина (`Sampler._pg_messages`; өмнө нь h4s-д л). Few-shot-оос human мөрийн өөрийн family, own_synth мөрийн эх жишээний family хасагдана. **plain (A7) prompt өөрчлөгдөхгүй**, учир нь retrieval/few-shot байхгүй. rag prompt өөрчлөгдөнө. D2 adapter өөрийгөө харсан prompt-оор сургагдсан гэдгийг харьцуулалтад тэмдэглэх |
| B4 | GRPO-ийн reward өөрийн DB-ийн SQL-ийг шууд PostgreSQL дээр ажиллуулдаг байсан (`sqlite_to_postgres`/postproc-гүй). BEST_A=A7 үед prompt «SQLite» гэж заадаг тул зөв SQLite хариулт (strftime, `= 1` boolean) reward 0 авдаг байв | Reward нь жорын prompt-той ижил RunOptions-той `Pipeline.process_sql`-ээр явна (A7-ийн хөрвүүлэлт → postproc → guard → гүйцэтгэл), үнэлгээ ба sampling-тай ижил. Зөвийн дүрэм sampling-тай нэг (`sampling.pg_correct`). Dataset зөвхөн BIRD мөртэй бол PG-ийн Pipeline үүсгэхгүй |
| L5 | `dedup` сургалтын мөрийн `question_mixed`-ийг шалгадаггүй байсан | Шалгана. `human_items` нь жишээний сангийн `question_mixed`-ийг `extra.question_mixed`-д хадгална (`data.item_questions`) |

### Анхдагч зан төлөв хэвээр (шинэ сонголт)

- **B5 — GRPO init.** `--init` өгөөгүй бол жорын `init`-ийг ашиглана: `best_sft` → `adapters/<best_sft>__<model>__merged`
  (c6/c7/f4-ийн өмнөх зам), жорын нэр → `adapters/<нэр>__<model>__merged` (d4 → d2), бусад нь зам/HF id.
  `t2s_meta.json`-д `init_source` бичигдэнэ. `train_a100.sh` `--init`-ийг тодорхой өгдөг тул өөрчлөлтгүй.
- **B6 — merge.** `t2s_train merge` нь W + (α/r)·B·A-г fp32-оор бодоод нэг удаа дугуйлна
  (`scripts/notebook/merge_lora.py`, numpy, тензор нэг нэгээр). PEFT-ийн bf16 merge-ийг ашиглахаа больсон.
  `--dtype bf16` анхдагч бөгөөд өмнөх гаралтын dtype-тай ижил. `fp16` (T4/vLLM) ба `fp32` сонгож болно. Tokenizer-ийг
  adapter-аас авна (өмнөх шиг). `merge_lora.py --dtype` анхдагч нь fp16 хэвээр. Энгийн LoRA-г л нэгтгэнэ (DoRA,
  modules_to_save дэмжихгүй — алдаа өгнө).
- **B9 — R2-A бэлэн байдал** (docs/research/R2_finetune_kaggle.md «R2-A»):
  - `sample --prompt h4s` нь зорилтот H4s prompt-оор шууд түүвэрлэж `__h4s__` файл бичнэ. `--index-dir`-ийг (анхдагч
    H4s = `build/index_plus`, dev-гүй) `cot`-д ч өгнө.
  - Хадгалах дүрэм: `sample --max-keep N --max-keep-all M` эсвэл `training.yaml` `sampling.max_keep` /
    `max_keep_all_correct`. Анхдагч нь 4 / null буюу өмнөх «≤ 4». R2-A-д 2 / 1: 1–7/8 зөв асуултаас ≤ 2, 8/8-аас ≤ 1, 0/8-аас 0.
  - Жорын `sample_prompt: h4s` нь `__h4s__` гаргалтыг цуглуулна. Жорын prompt-той ижил бол хадгалсан prompt-ыг
    дахин угсралгүй шууд ашиглана. Анхдагч: plain гаргалтыг цуглуулж prompt-ыг солих (D2/D3).
  - Validation: жорын `eval_share` (жишээ нь 0.1) тохируулбал `build` нь family-ээр бүлэглэсэн хэсгийг
    `data/train/sft/<recipe>__<model>.eval.jsonl`-д тусгаарлана. Нэг family-ийн мөр (human жишээ, түүний синтетик
    хувилбар, synth_own-ийн skeleton) бүгд нэг талд орно; BIRD мөр асуултаараа бүлэглэгдэнэ. `sft` тэр файлыг
    уншина (эсвэл `--eval-file`). `eval_steps` (анхдагч = save_steps) тутам eval_loss бодно. `early_stopping_patience`
    тохируулбал хамгийн бага eval_loss-тай checkpoint-ийг төгсгөлд ачаална; save_steps нь eval_steps-ийн үржвэр байх ёстой.
    Eval batch нь сургалтын batch-тай ижил (T4: 1). Анхдагч: `eval_share: 0`, eval-гүй.
  - `sft --base <хавтас|HF id>` нь суурийг (жишээ нь D2-ийг нэгтгэсэн fp16 жин) солино; revision-ийг зөвхөн HF суурьт
    хэрэглэнэ. Tokenizer нь `hf_id`-ийнх (build-ийн prompt-той ижил).

  R2-A-ийн жорын жишээ (нэмээгүй — Kaggle-ийн замаар тохируулна):
  ```yaml
  r2a: {kind: sft, data: [synth, human], prompt: h4s, sample_prompt: h4s, drop_truncated: true, eval_share: 0.1,
        early_stopping_patience: 2, eval_steps: 10, save_steps: 10, lora_r: 16, lora_alpha: 32, learning_rate: 5.0e-5,
        epochs: 1, max_length: 9216, per_device_batch_size: 1, gradient_accumulation: 8, quantize_4bit: true,
        fp16: true, bf16: false, gradient_checkpointing: true, completion_logits_only: true}
  ```
  ```bash
  t2s_train sample --model arctic-r1-7b --source own_synth_clean.jsonl --prompt h4s --max-keep 2 --max-keep-all 1
  t2s_train build --recipe r2a --model arctic-r1-7b
  torchrun --standalone --nproc_per_node 2 -m mn_text2sql.training sft --recipe r2a --model arctic-r1-7b \
      --base /kaggle/input/<d2-merged16> --output-dir /kaggle/working/r2a
  ```
  Validation-ийн family-г `data/train/{bird_mn,own_synth_clean,human}.jsonl`-ээс (`load_item_index`) авна.
- **B10 — `t2s_meta.json`.** SFT: `code` = {git `commit`, `dirty`, `tree_sha1` (src/*.py + config + knowledge, git-гүй
  Kaggle-д ч)}, `libs` (torch, transformers, trl, peft, datasets, accelerate, bitsandbytes, vllm, …, python, GPU),
  `data_sha256` = {train, eval}, `base`, `batch` = {per_device, grad_acc, n_gpu, world_size, effective}, `eval`
  (best_metric, best_checkpoint, early_stopped). GRPO: `init_source`, `prompt`, `dialect`, `dataset_sha256`, `code`,
  `libs`. Модуль: `training/provenance.py`.
- **B7 — MADLAD M1 (баримт).** `scripts/mt/train_mt.py --backend seq2seq`-ийг 2×T4 дээр torchrun-гүй ажиллуулсан тул
  Trainer DataParallel ашигласан. Алхмын бодит batch = 4 × 2 GPU × GA 8 = **64**, 32 биш. Warmup нь 32-оор тооцсон
  алхмын 3% байсан тул бодит алхмын ~6% болсон. Одоо meta-д `n_gpu`, `world_size`, `effective_batch`, `warmup` бичигдэж,
  DataParallel үед анхааруулга гарна. `--warmup-ratio R` өгвөл warmup-ийг бодит алхмын тооноос бодно
  (`warmup_ratio`, transformers ≥ 5-д `warmup_steps < 1`). Анхдагч нь хуучин томьёо. M1-ийн өгөгдөл, adapter хэвээр.

## 2026-10-31 — хувилбарын түгжээ ба resume-ийн хувилбар (review F6/F7)

**Ажиглагдсан хувилбар (эх сурвалж).** Kaggle-д бодитоор суулгасан нь баримтжсан цорын ганц багц бол D3-ийн түгжээ
`requirements-d3.txt` (`scripts/server/run_plan_l4.sh` STAGE=train_d3, `D3_PINS=1` анхдагч; sft.py-г эдгээрийн эх кодоор
шалгасан — `docs/IMPROVE_80_PLAN.md`, WORKLOG 2026-10-18): **vllm 0.32.0, transformers 5.19.0, trl 1.16.0, peft 0.21.2,
accelerate 1.15.0, bitsandbytes 0.50.2** (vLLM 0.32.0 нь transformers >=5.16.1,<5.20.0 шаарддаг). torch-ийн хувилбар
нь vLLM-ийн хамааралаар тодорхойлогдсон, тэмдэглэгдээгүй. Үнэлгээний сессүүд (bootstrap.sh) `requirements-gpu.txt`-ийг
**түгжээгүй** суулгасан (сессийн үеийн хамгийн сүүлийн vllm/transformers); локал `results/runs/**.meta.json`-д
`serving.vllm_version` байхгүй (тэр талбар R7 #1-ээс хойш нэмэгдсэн) — бодит хувилбарыг шинэ run-ий meta-гаас (`serving`)
ба сургалтын `t2s_meta.json`-ийн `libs`-ээс авна.

**F6 — сонголттой түгжээ (анхдагч зан төлөв хэвээр).** `PIN_VERSIONS=1 bash scripts/notebook/bootstrap.sh` бол
`PIN_FILE`-ийг (анхдагч `requirements-d3.txt`) pip-ийн constraints файл болгож өгнө:
`pip install -r requirements-gpu.txt -c $PIN_FILE` — нэг удаа шийдэж нэг удаа суулгана (хоёр дахь суулгалт биш); `PIN_FILE`-д байхгүй багц (torch,
sentence-transformers, datasets …) requirements-gpu.txt-ээр хэвээр суулгагдана. Суусан хувилбарыг `build/pip_versions.txt`-д
бичнэ. `PIN_FILE` алга бол анхааруулаад түгжээгүй үргэлжилнэ. Тохируулаагүй бол суулгалт өмнөхтэй яг ижил.
Kaggle-д (notebook-ийн нүдийг засах боломжгүй үед) `config/kaggle_overrides.env`-д `PIN_VERSIONS=1` (ба хэрэгтэй бол
`PIN_FILE=…`) бичиж болно: bootstrap.sh нь run_plan-аас өмнө ажилладаг тул файлыг source хийхгүй, зөвхөн эдгээр 2 түлхүүрийг
уншина; `KO_UNTIL` (UTC) өнгөрсөн бол тэдгээрийг тоохгүй (run_plan-тай ижил).

**F7 — resume-ийн prompt/кодын хувилбар.** `options_hash` (файлын нэр, `opts_hash`) **өөрчлөгдөөгүй** — хуучин run
хэвээр үргэлжилнэ. Нэмэлт: meta-д `code_digest` (llm/pipeline/retrieval/security/text/knowledge/db/eval-ийн *.py +
config.py + knowledge/*.yaml-ийн sha1, CRLF-ийг LF болгож), бичлэг бүрт `code_digest`. Resume үед өмнөх бичлэгүүдийн
`prompt_version` (prompts.py-ийн hash, бүх бичлэгт аль хэдийн байдаг) ба `code_digest` (шинэ бичлэгт л) одоогийнхоос
өөр бол анхааруулга, meta-д `resume_version_mismatch`; `T2S_RESUME_STRICT=1` бол зогсооно (`--overwrite`/`--tag`).
Модуль: `eval/runner.py` (`code_digest`, `version_mismatch`, `strict_resume`), тест `tests/test_resume_version.py`.

## 2026-10-31 засвар (сургалт ба BIRD-ийн review: L1, L2, L7, L9, L10, H3)

Одоо байгаа өгөгдөл, adapter хөндөгдөөгүй — **дараагийн** ажиллуулалтад нөлөөлнө. Тест: `tests/test_training_review_fixes.py`.

- **Шинэ сонголт.** `cot --tool` / `--pivot` (L7) нь `sample`-тай ижил: tool-той sampling-ийн stats ба гаргалтын нэр `__tool`
  (`<model>__rag__tool__<source>__cot.jsonl` → `formats.collect_samples(tool=True)` цуглуулна) — `sample --tool` хийсэн бол
  `cot --tool`. `synth --no-embed` (L1) — загварын давхардлын шүүлтийг embedding-гүй (`dedup --no-embed`-тэй ижил).
- **L1 — C3-ийн загвар.** `synth` нь `dedup`-ийн human.jsonl-ээс хасах жишээг (human.jsonl = `dedup`-ийн дараах
  `knowledge/examples.jsonl`; `dedup_against_eval`, ижил босго ба embedding-ийн сонголт) загвар болгохгүй (stats-д
  `templates_excluded`); бусад загварын хувилбар ба эрэмбэ өмнөхтэй яг ижил. Локалд (embedding-гүй, тэмдэгтийн 3-gram)
  human-CX15 нь dev D038-тай ойролцоо давхардал (0.814 ≥ 0.8) гэж тэмдэглэгдсэн.
- **L1 — индекс.** Retrieval-тэй prompt-ын (rag/h4s) sample/cot/GRPO нь `meta.json`-д `include_dev: true` эсвэл
  `examples.json`-д dev/test-ийн id агуулсан индексийг татгалзана (`sampling.check_training_index`, ValueError; жишээ нь
  `build/index_plus_dev`). Dev-гүй индекс өгнө (`build/index`, `build/index_plus`; `--index-dir` / жорын `index_dir`).
  plain (A-шилдэг) prompt индекс уншдаггүй тул шалгахгүй.
- **L2 — давтагдах чадвар.** Хүсэлт бүрийн seed детерминист: sha256(item_id|variant|free/hint|дуудлагын дугаар)-ийн 31 бит
  (PYTHONHASHSEED, thread-ийн эрэмбээс үл хамаарна; sqrl — траектор бүрт seed + k); sample-ийн бичлэг бүрт `seed`
  хадгалагдана. GRPO stats файлуудыг эрэмбэлсэн дарааллаар уншина. `t2s_meta.json`-д `model_revision` (HF commit; SFT ба
  GRPO), SFT-д мөн `sampling` (training.yaml-ийн sampling ба cot тохиргоо + seed-ийн дүрэм).
- **L9 — GRPO-ийн жор.** `grpo_params`: `grpo_defaults`-ийн дээр жорын түвшний утгыг дарна (зөвхөн grpo_defaults-д байгаа
  түлхүүр; reward шиг dict нэг түвшинд нийлнэ). Өмнө жорын утга чимээгүй үл тоомсорлогддог байв; одоогийн c6/c7/d4/f4-д
  ийм түлхүүр алга — үр дүн өөрчлөгдөхгүй.
- **L10 — зөвийн шалгалт.** `sampling.pg_correct` (C1/C2-ийн шүүлт, GRPO-ийн reward) үнэлгээний `_match`-тай ижил харьцуулна:
  мөрийн `order_matters` (`TrainItem.order_matters` = `extra.order_matters`: жишээний сан, түүний C3 хувилбар, synth_own;
  өмнө үргэлж дараалалгүй), код↔нэрийн map (`compare.build_code_maps`), cfg-ийн хүлцэл (`float_tolerance`,
  `relative_tolerance`).
- **H3 — BIRD-ийн литерал маск.** `bird.data._protect` нь нэг удаагийн regex солилт, зөвхөн бүтэн токен (үгийн хил; 1
  тэмдэгт ба 1–2 оронтой тоо — зөвхөн бие даасан токен бол), дугаар нь текст дэх анхны байрлалаар (PYTHONHASHSEED-ээс үл
  хамаарна). Өмнө нь богино литерал өөр үгийн дотор ('F' → "Find" → "[V2]ind") болон өмнөх placeholder-ийн дотор ('1' →
  "[V1]" → "[V[V2]]") солигддог байв. `_restore` нь бүх placeholder сэргээгдэж, танигдаагүй placeholder үлдээгүй үед л ok.
  Сургалтын `translate` мөр бүрт `placeholders_ok` бичнэ; `sample` нь `placeholders_ok=False` мөрийг хасаж тоолно (лог,
  үр дүнгийн `dropped_placeholders`), `cot` ч хасна. F1-ийн `load_bird_mn` хянагдаагүй ийм мөрийг хасаж, тоог `meta.json`-ийн
  `eval_set.dropped_placeholders`-д бичнэ; `bird_mn_review.csv`-д `placeholders_ok` багана («0» — эхэлж засах мөр).
  **Хуучин маскаар үүсгэсэн `data/train/bird_mn.jsonl`**-д энэ талбар алга (сайн гэж тооцогдоно) бөгөөд эвдэрсэн мөр нь
  илрэхгүй байж болно — дараагийн C бүлгийн build-ээс өмнө дахин орчуулна (`translate` нь байгаа мөрийг алгасдаг тул хуучин
  файлыг хойш тавьж).
