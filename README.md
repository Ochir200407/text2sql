# mn-text2sql — Монгол хэл дээрх Text2SQL (ББСБ, PostgreSQL)

«Монгол хэл дээрх Text2SQL судалгаанд суурилсан бүтээх заавар»-ыг хэрэгжүүлсэн систем. Туршилтын төлөвлөгөө (A1–F10)
бүхэлдээ кодлогдсон. Серверт суулгаж, туршилт бүрийн оноог (EX, TS-EX, TrustSQL, хугацаа ...) төлөвлөгөөний
хүснэгтийн форматаар гаргана. Гол хоёр загвар нь **Snowflake/Arctic-Text2SQL-R1-7B** ба **feyninc/sqrl-9b** (хоёулаа
Apache 2.0). Хоёуланг нь vLLM-ийн OpenAI-compatible API-аар нэг harness-д ажиллуулна.

- Заавар: `docs/guide/GUIDE.md`. Туршилтын төлөвлөгөө (заавал мөрдөх): `docs/guide/EXPERIMENT_PLAN.md`.
- DB нь зөвхөн `text2sql-thesis/database`-ээс авсан хуулбар (`db/`). Засварууд `db/CHANGES.md`-д бичигдсэн.

## Бүтэц

| Хавтас | Агуулга |
|---|---|
| `db/` | Эх ББСБ-ийн схем (migrations, functions, views, seed) — хуулбар, засвартай |
| `db_t2s/` | Text2SQL давхарга: `t2s` схемийн 28 view (PII-гүй), read-only bot role, RLS |
| `src/mn_text2sql/datagen/` | Тогтмол seed-тэй синтетик өгөгдөл үүсгэгч (3000 харилцагч, 2620 зээл, ~650k мөр, өнөөдөр = 2026-06-30) |
| `knowledge/` | Мэдлэгийн давхарга: `semantic_model.yaml` (28 view, 426 багана, монгол/англи тайлбар, синоним, enum), `glossary.yaml` (45 нэр томьёо), `metrics.yaml` (F8 semantic layer), `examples.jsonl` (few-shot сан) |
| `evalset/` | Eval set: `dev.jsonl` (300), `test.jsonl` (200), `MANIFEST.json`, бичих дүрэм `AUTHORING.md`, ноорог `drafts/` |
| `src/mn_text2sql/retrieval/` | 4 индекс (схем, утга — кирилл ба галиг түлхүүр, нэр томьёо, DAIL жишээ), BM25 + embedding (bge-m3) |
| `src/mn_text2sql/pipeline/` | Урсгал: prompt → үүсгэх → шалгах (sqlglot guard) → гүйцэтгэх → засах → санал хураах → татгалзах; A4 орчуулга, F7 intent, F8 router, dialect хөрвүүлэлт |
| `src/mn_text2sql/llm/` | vLLM client, загвар тус бүрийн протокол (OmniSQL, sqrl, XiYan, generic) |
| `src/mn_text2sql/eval/` | Runner (асуулт бүрийн JSONL), хэмжүүр, test-suite, тайлан, алдааны шинжилгээ, LLM judge |
| `src/mn_text2sql/bird/` | F1: BIRD-ийн монгол хувилбар (BIRDTurk арга) ба SQLite дээрх үнэлгээ |
| `src/mn_text2sql/training/` | C/D/F4/F6/F10: BIRD train орчуулга, синтетик өгөгдөл, rejection sampling, CoT, LoRA SFT, GRPO, Belebele |
| `src/mn_text2sql/security/`, `service/` | SQL guard, P2SQL тест, FastAPI (shadow/pilot), шифрлэсэн audit log |
| `config/` | `default.yaml`, `models.yaml` (загвар, vLLM аргумент), `experiments.yaml` (A1–F10), `training.yaml` |
| `scripts/server/` | Сервер дээрх суулгалт, vLLM, туршилт, сургалтын скрипт |
| `docs/` | SERVER, EVAL, EXPERIMENTS, TRAINING, SECURITY, ARCHITECTURE, хянагчдын тайлан (`review/`) |

## Сервер дээр ажиллуулах (товч)

```bash
# 1) Суулгалт: PostgreSQL 16, venv, DB, test-suite DB, индекс, шалгалт (~15 мин + загвар татах)
export T2S_PG_PASSWORD='...'
bash scripts/server/setup.sh

# 2) Сургалтгүй тест — нэг L4. Үе шат бүрийг ТУСАД НЬ; дараагийнх нь өмнөхийн шилдгээс хамаарна
STAGE=caches bash scripts/server/run_plan_l4.sh          # A4/F7/F8 кэш
STAGE=A      bash scripts/server/run_plan_l4.sh          # → REPORT.md-ээс A1/A6/A7-ийн шилдэг = BEST_A
STAGE=B BEST_A=A6 bash scripts/server/run_plan_l4.sh
STAGE=F BEST_A=A6 bash scripts/server/run_plan_l4.sh

# 3) Сургалт — A100 80 GB, хоёр үе шат (GRPO нь хэмжсэн C-шилдэг дээр)
STEPS="data sample build sft" BEST_A=A6 bash scripts/server/train_a100.sh arctic-r1-7b
BEST_A=A6 bash scripts/server/eval_adapters.sh arctic-r1-7b          # L4 дээр C1–C5, D2, F6 → BEST_SFT
STEPS=grpo BEST_A=A6 BEST_SFT=C3 bash scripts/server/train_a100.sh arctic-r1-7b
BEST_A=A6 BEST_SFT=C3 bash scripts/server/eval_adapters.sh arctic-r1-7b   # C6, C7, F4, D1, D4
#    (sqrl-9b-д мөн адил)

# 4) E бүлэг (A–D-ийн шилдэг дээр), эцэст нь test
#    BEST_CONFIG adapter-тай (D1 г.м.) бол загвар бүрээр тусад нь, LoRA-тай:
#    MODELS=arctic-r1-7b LORA="c3=adapters/c3__arctic-r1-7b" STAGE=E BEST_A=A6 BEST_CONFIG=D1 bash scripts/server/run_plan_l4.sh
STAGE=E BEST_A=A6 BEST_CONFIG=D1 bash scripts/server/run_plan_l4.sh
STAGE=test BEST_A=A6 BEST_CONFIG=D1 TEST_IDS=A6,B5,D1,E2 bash scripts/server/run_plan_l4.sh
python -m mn_text2sql report            # → results/REPORT.md (+ report.json); test → REPORT_test.md
python -m mn_text2sql compare --a <одоогийн_систем.jsonl> --b <шинэ.jsonl>   # орлуулах шийдвэр (McNemar)

# 5) Agent (G1–G4) ба text2sql-thesis-тэй харьцуулалт
STAGE=G      BEST_A=A6 BEST_CONFIG=D1 bash scripts/server/run_plan_l4.sh
STAGE=thesis BEST_A=A6 BEST_CONFIG=D1 bash scripts/server/run_plan_l4.sh   # → results/thesis/COMPARE.md
```

Нэг туршилтыг гараар ажиллуулах:

```bash
source scripts/server/env.sh
bash scripts/server/serve_model.sh arctic-r1-7b          # vLLM :8000
t2s eval --run A1 --model arctic-r1-7b --variants mn,latin,en
t2s ts-score --run A1                                    # test-suite EX
t2s report
```

Cloud (Kaggle/Colab notebook-оор тест, Modal-оор сургалт): `docs/CLOUD.md`. Дэлгэрэнгүй: `docs/SERVER.md` (суулгалт, хугацаа, алдаа засах), `docs/EXPERIMENTS.md` (ID бүрийн команд ба
хамаарал), `docs/EVAL.md` (eval set, хэмжүүр, JSONL талбар, хүлээн авах дүрэм), `docs/TRAINING.md`, `docs/AGENT.md`
(agentic урсгал), `docs/THESIS_BENCH.md` (text2sql-thesis-ийн 150 кейс дээрх харьцуулалт), `report/` (ШУТИС-ийн загвараар төгсөлтийн ажлын тайлан, `t2s report-latex`).

## Туршилтын ID → тохиргоо (config/experiments.yaml)

| Бүлэг | ID | Юуг өөрчилнө |
|---|---|---|
| A | A1 (кирилл, тайлбаргүй схем), A2 (англи), A3 (латин), A4 (англи руу орчуулах + placeholder), A5 (sqrl, max_steps=0), A6 (PostgreSQL-ийг prompt-д), A7 (SQLite → sqlglot) | Суурь загвар шууд |
| B | B1 (тайлбар), B2 (утгын хайлт), B3 (нэр томьёо), B4 (few-shot), B5 (бүгд), B6–B9 (нэгийг хасах), B10 (schema linking) | RAG, few-shot |
| C | C1 rejection SFT, C2 + CoT, C3 + өөрийн DB, C4/C4b англи 30/50%, C5 rank 64, C6 GRPO, C7 + англи replay | Зөвхөн fine-tune |
| D | D1 (C-шилдэг + RAG), D2 (RAG-тай prompt-оор сургах), D3 (− few-shot), D4 (+ GRPO) | Fine-tune + RAG |
| E | E1 засвар (2 удаа; төлөвлөгөө 1–3), E2 8 хувилбарын санал хураалт (3 seed), E3 татгалзах босго | Найдвартай байдал |
| G | G1 agent (tool + critic + засвар), G2 − critic, G3 + planner, G4 RAG-гүй agent (docs/AGENT.md) | Agentic (нэмэлт) |
| F | F1/F1b монгол BIRD, F2 дүрмийн утга тааруулалт, F3 сургасан галиг, F4 бичгээс үл хамаарах reward, F5 pivot (prompt), F6 pivot (сургалт), F7 intent JSON, F8 semantic router, F9a/F9b идэвхтэй тэмдэглэгээ, F10 search_values tool | Шинэлэг санаа |

Туршилт бүр эх тохиргооноосоо **нэг зүйлээр** ялгаатай (`base`). Тайлан нь McNemar-аар суурьтай нь
харьцуулж «Төлөв» баганыг бөглөнө.

## Үр дүн

- `results/runs/<split>/<ID>/<model>__<variant>__s<seed>.jsonl` — асуулт бүрийн мөр: question_id, config_id, SQL,
  result hash, correct, latency, tokens, error type, итгэл, хувилбаруудын hash, retrieval, eval set/DB/prompt/загвар/кодын
  хувилбар. Мөн `.summary.json` (хэмжүүр) ба `.meta.json` (run-ийн мета) файл.
- `results/REPORT.md` — төлөвлөгөөний хүснэгт (ID | Тохиргоо | Arctic-R1-7B EX % | sqrl-9b EX % | Төлөв | Тэмдэглэл).
  Мөн бичгийн зөрүү, найдвартай байдал, зардал, задаргаа, санал хураалт ба татгалзалтын муруй.
- `results/errors/*.csv` — 50 алдааны түүвэр (NL2SQL-BUGs + монгол шошго), хүн шошголох багана.

## Локал хөгжүүлэлт (Windows/Linux, GPU-гүй)

```bash
export T2S_ADMIN_DSN=postgresql://postgres@localhost:55432/postgres PYTHONPATH=src PYTHONIOENCODING=utf-8
python -m mn_text2sql db-build && python -m mn_text2sql index-build --no-embed
python -m mn_text2sql ask "Дархан салбарын хугацаа хэтэрсэн (1+ хоног) зээл хэд вэ?" --run A6 \
    --mock-sql "SELECT count(*) FROM loans l JOIN branches b USING (branch_id) WHERE b.branch_name = 'Дархан салбар' AND l.is_active_portfolio AND l.days_past_due > 0"
python -m pytest -q                      # unit + DB тест (T2S_ADMIN_DSN-тэй үед)
python -m mn_text2sql eval --run A6 --model arctic-r1-7b --oracle --limit 50    # harness шалгах (gold-оор)
```
