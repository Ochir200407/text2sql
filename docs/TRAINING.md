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
- **C2 шүүлт.** «Gold-ыг шууд хуулсан» гаргалтыг хасна: reasoning дотор gold SQL үгчлэн гарсан эсвэл «given/provided
  SQL» гэж дурдсан. Сургалтын prompt-оос hint-ийг хасна.
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
