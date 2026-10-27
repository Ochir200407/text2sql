# Сервер дээр суулгах ба ажиллуулах

## Шаардлага

| | Тест (A, B, E, F, сургасан загварын үнэлгээ) | Сургалт (C, D, F4, F6, F10) |
|---|---|---|
| GPU | NVIDIA L4 24 GB (Arctic-7B — BF16 ~15 GB; sqrl-9b — FP8) | A100 80 GB (GRPO-д заавал; sqrl-ийн RAG-тай SFT L4-д багтахгүй) |
| CPU/RAM | 8+ core, 32 GB RAM | 16+ core, 64+ GB RAM |
| Диск | 150 GB (загварууд ~15–25 GB тус бүр, DB ~2 GB, test-suite 8 DB) | 500 GB (BIRD train ~35 GB, adapter, нэгтгэсэн жин) |
| OS | Ubuntu 22.04/24.04, NVIDIA драйвер + CUDA 12.x | ижил |

PostgreSQL 16-ийг (PGDG) `setup.sh` суулгана. UTF-8 locale-тай (`C.UTF-8`) байх ёстой (кирилл `lower()`, `ILIKE`).
Админ холболт нь superuser байна: role, RLS, `session_replication_role` тохируулахад хэрэгтэй.

## Суулгалт

```bash
git clone <repo> text2sql-new && cd text2sql-new        # эсвэл хавтсыг хуулна
export T2S_PG_PASSWORD='хүчтэй_нууц_үг'
bash scripts/server/setup.sh
```

`setup.sh` дараах алхмуудыг хийнэ:
1. PostgreSQL 16 суулгаж (`SKIP_PG=1` бол алгасна) postgres-ийн нууц үгийг тохируулна.
2. `.venv` үүсгэж хамаарлыг суулгана. Үүнд `requirements.txt`, мөн GPU-тэй үед `requirements-gpu.txt`
   (vllm, transformers, sentence-transformers, peft, trl, datasets) орно.
3. `db-build` — эх схем, синтетик өгөгдөл, `t2s` давхарга, roles, RLS, ANALYZE (~1 мин). Дараа нь `db-validate`.
4. `testsuite-build` — test-suite-ийн 8 хуулбар DB (`nbfi_ts1..8`): NULL, тэнцүү утга, хилийн утга, хоосон хүснэгт (~3 мин).
5. `index-build` — мэдлэгийн индекс. GPU байвал BAAI/bge-m3 embedding-тэй.
6. Шалгалт: `knowledge-check`, `check-gold --testsuite`, `security-test`. Алдаа гарвал скрипт зогсоно.

Орчны хувьсагчийг `scripts/server/env.sh` тодорхойлно: `T2S_ADMIN_DSN`, `T2S_LLM_BASE_URL`, `HF_HOME`,
`T2S_AUDIT_KEY`, `PYTHONPATH`. Shell бүрт `source scripts/server/env.sh` хийснээр `t2s`, `t2s_train` товчлол ажиллана.

## vLLM

```bash
bash scripts/server/serve_model.sh arctic-r1-7b        # config/models.yaml-ийн vllm_args (BF16, 24k context)
bash scripts/server/serve_model.sh sqrl-9b             # L4: FP8; A100: ... sqrl-9b 8000 --80gb
bash scripts/server/serve_model.sh stop
```

- **sqrl.** Загварын картын дагуу `--reasoning-parser` хэрэглэхгүй. Pipeline нь `</think>`-ийн дараах текстээс
  `<sql>`/`<answer>` блокийг авна. Qwen3.5 нь multimodal архитектуртай тул `--limit-mm-per-prompt '{"image":0,"video":0}'`
  ашиглана.
- **Arctic.** Prefill «Let me solve this step by step.\n<think>» нь vLLM-ийн `continue_final_message`-ээр дамжина. Сервер
  дэмжихгүй бол client автоматаар prefill-гүйгээр дахин оролдоно.
- **Хувилбар.** vLLM-ийн хувилбарыг түгжээгүй: Qwen3.5 ба Gemma 4-ийг дэмждэг сүүлийн хувилбар хэрэгтэй. Run бүр
  загварын HF commit-ийг `.meta.json`-д бичнэ. Үүнийг `T2S_MODEL_REVISION`-оор эсвэл HF кэшээс авна.
- **Context.** Prompt context-оос хэтэрвэл client `max_tokens`-ийг автоматаар багасгаж дахин илгээнэ (лог-д анхааруулга).

## Туршилт

- `run_plan_l4.sh` — сургалтгүй туршилт, үе шат бүрийг тусад нь (`STAGE=caches | A | B | F | E | test`).
  Дараалал: caches → A → (BEST_A сонгох) → B, F → (C/D сургалт, eval_adapters.sh) → (BEST_CONFIG сонгох) → E → test.
  B/F нь `BEST_A`, E нь `BEST_CONFIG` заахгүй бол ажиллахгүй (placeholder суурьтай run хийгдэхээс сэргийлнэ).
- Тасарвал ижил командыг дахин ажиллуулахад дууссан асуултыг алгасна. Run бүр тохиргооны hash-аа (`opts_hash`)
  хадгалдаг тул vars/`--set`/served model өөрчлөгдсөн бол runner зогсоно — `--overwrite` эсвэл `--tag` ашиглана.
- vLLM тасарвал (холболт, OOM, adapter алга) тухайн асуултыг хариулт болгон хадгалахгүй; 5 дараалсан алдааны дараа
  run зогсоно. Run эхлэхээс өмнө `/v1/models`-д хэрэгтэй served model байгааг шалгана.
- `run_experiments.sh "<ID,...>" <model>` — нэг буюу хэд хэдэн ID. `VARIANTS`, `SPLIT`, `EXTRA="--var best_a=A7"`
  орчны хувьсагчаар тохируулна.
- Бүлэг бүрийн дараа `results/REPORT.md`-ийг үзэж шилдгийг сонгоно (BEST_* орчны хувьсагч эсвэл
  `config/experiments.yaml`-ийн `vars`). Run хийх үеийн суурь `.meta.json`-ийн `baseline`-д бичигдэж, тайлан түүнийг
  ашиглана. Хувьсагч бүр дараах бүлгийн суурийг тодорхойлно:

  | Хувьсагч | Утга | Ашиглах бүлэг |
  |---|---|---|
  | `best_a` | A1/A6/A7-ийн шилдэг | B, C, F5, F7 |
  | `best_config` | A–D-ийн шилдэг | E |
  | `best_sft` / `best_sft_adapter` | C-ийн шилдэг | C6, D1, F6 |

- Test (200 асуулт) нь бүлэг бүрийн эцэст зөвхөн шилдэг тохиргоонд ажиллана:
  `STAGE=test BEST_A=A6 TEST_IDS=A6,B5,E2 bash scripts/server/run_plan_l4.sh` (тайлан: `results/REPORT_test.md`).
- Орлуулах шийдвэр: `t2s compare --a results/runs/test/EXT-current/... --b results/runs/test/E2/...` (McNemar, ΔEX,
  TrustSQL).
- Embedding (bge-m3) нь eval үед CPU дээр (`T2S_EMBED_DEVICE=cpu`, env.sh). Индекс embedding-тэй боловч загвар
  ачаалагдахгүй бол run зогсоно (чимээгүй BM25 руу унахгүй; зориуд бол `T2S_ALLOW_BM25_FALLBACK=1`).

### Хугацаа (туршилтын төлөвлөгөөнөөс)

- Нэг тест (300 асуулт, нэг загвар, нэг хувилбар) L4 дээр 15–30 минут.
- Сургалтгүй бүх тест ~50 GPU-цаг (~3 хоног).
- Сургалт A100 дээр:

  | Ажил | Хугацаа |
  |---|---|
  | Rejection sampling (5k × 8) | 5–9 цаг |
  | SFT RAG-гүй | 10–11 цаг |
  | SFT RAG-тай | 16–19 цаг |
  | GRPO | 10–12 цаг |

## Гадаад систем (одоогийн систем)-тэй харьцуулах

Ижил 300/200 асуулт дээр гадаад системийн SQL-ийг ижил аргаар үнэлнэ:
```bash
t2s score-external --pred current_system_dev.jsonl --name current --split dev    # {question_id, sql, variant?, status?}
t2s report     # EXT-current мөр тайланд гарна; McNemar-аар харьцуулж болно
```

## Түгээмэл асуудал

- **`permission denied for schema core` (bot).** Энэ нь зориулалтын хамгаалалт. Bot зөвхөн `t2s` view-д хандана.
- **Кирилл `lower()` буруу ажиллах.** DB-ийн locale UTF-8 биш байна. Cluster-ийг `C.UTF-8`-аар дахин үүсгэнэ.
- **vLLM OOM (L4).** sqrl-д `--quantization fp8` ашиглана. `--max-model-len`-ийг багасгах эсвэл
  `--gpu-memory-utilization`-ийг 0.88 болгоно.
- **Embedding татагдахгүй.** Индекс embedding-тэй үед run зогсоно. `index-build --no-embed` (зөвхөн BM25)-ээр дахин үүсгэх эсвэл `T2S_ALLOW_BM25_FALLBACK=1`; retrieval-ийн чанарт нөлөөлөх тул run-ийн `.meta.json`-ийн `index` хэсгийг шалгана.
  чанарт л нөлөөлнө.
- **Сургалтын сервер өөр машин бол.** Өөрийн DB-ийн reward ба sampling PostgreSQL-д хандана. `setup.sh`-ийг тэнд бас
  ажиллуулна (эсвэл `T2S_ADMIN_DSN`-ийг тестийн сервер рүү чиглүүлнэ).
