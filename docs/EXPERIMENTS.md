# Туршилт бүрийн гарын авлага (A1–F10)

Бүх ID нь `config/experiments.yaml`-д тодорхойлогдсон. Команд нь
`t2s eval --run <ID> --model <arctic-r1-7b|sqrl-9b> [--variants mn,latin,en] [--split dev|test]`. vLLM тухайн загвараар
асаалттай байна (`scripts/server/serve_model.sh`). `results/REPORT.md` нь ID бүрийг `base`-тай нь McNemar-аар
харьцуулна.

## A — суурь загвар, шууд

| ID | Тохиргоо | Тэмдэглэл |
|---|---|---|
| A1 | Кирилл асуулт, тайлбаргүй түүхий DDL, загварын өөрийн протокол (SQLite гэж хэлнэ), гүйцэтгэл PostgreSQL дээр | `--variants mn,latin,en,mixed` гэвэл A2 (en), A3 (latin) нь A1-ийн хувилбарууд болж тайланд автоматаар орно |
| A2 / A3 | Англи / латин хувилбар | Хэлний зөрүү = A2 − A1; галигийн зөрүү = A1 − A3 |
| A4 | Асуултыг англи руу орчуулах, утгыг placeholder-оор хамгаалах | Урьдчилан: `t2s translate --split all` (qwen3.5-9b асаалттай) |
| A5 | sqrl, DB судлах алхамгүй (max_steps = 0) | Зөвхөн sqrl |
| A6 | Зорилтот dialect-ийг (PostgreSQL) prompt-д заана | |
| A7 | SQLite-аар үүсгээд sqlglot-оор хөрвүүлнэ | SQLite-ийн огнооны функц (date('now', …), julianday)-ыг нэмэлт дүрмээр хөрвүүлнэ |

A бүлгийн дараа A1/A6/A7-ийн шилдгийг `vars.best_a`-д бичнэ. B, C, F5, F7 нь түүн дээр суурилна.

## B — RAG ба few-shot

| ID | Нэмэлт |
|---|---|
| B1 | Монгол/англи тайлбар, синоним, enum утга (Arctic-д DDL comment, sqrl-д `<schema>`), мөн мэдлэгийн тэмдэглэл (өнөөдрийн огноо, валютын дүрэм). Токен төсөв 13k. B6 нь хоёуланг хасна |
| B2 | Утгын хайлт (кирилл ба галиг түлхүүр, fuzzy), нийт ≤ 16 утга, evidence-д |
| B3 | Бизнесийн нэр томьёо (glossary, каноник SQL), evidence-д |
| B4 | DAIL маягийн 4 few-shot жишээ: утгыг mask хийсэн асуултын төстэй байдлаар (DAIL-ийн урьдчилсан SQL-ийн skeleton алхмыг хийхгүй — нэмэлт LLM дуудлага шаардана; хазайлт) |
| B5 | B1–B4 бүгд |
| B6–B9 | B5-аас нэгийг хасна (тайлбар, утга, нэр томьёо, few-shot) |
| B10 | B5 + schema linking (top-6 хүснэгт + FK хөрш, хүснэгт бүрт ≤ 14 багана) |

## C — зөвхөн fine-tune (docs/TRAINING.md)

| ID | Сургалтын жор |
|---|---|
| C1 | `c1`: rejection sampling SFT |
| C2 | `c2`: + gold-guided CoT |
| C3 | `c3`: + өөрийн DB синтетик + хүний жишээ |
| C4 / C4b | `c4_en30` / `c4_en50`: англи өгөгдөл 30% / 50% |
| C5 | `c5_r64`: LoRA rank 64 |
| C6 | `c6`: C-шилдэг + GRPO |
| C7 | `c7`: + англи replay |

Үнэлгээг A-шилдэг prompt-оор (RAG-гүй) хийнэ. SFT-ийн LoRA нь `--lora-modules`-оор, GRPO-ийн нэгтгэсэн жин нь тусдаа
served-model-name-ээр ажиллана (`scripts/server/eval_adapters.sh`).

## D — fine-tune + RAG

| ID | Тохиргоо |
|---|---|
| D1 | C-шилдэг adapter (`vars.best_sft_adapter`) + B5-ийн RAG |
| D2 | `d2`: B5-ийн RAG-тай prompt-оор сургасан (C-ийн гаргалтыг дахин ашиглана) |
| D3 | D2 − few-shot |
| D4 | `d4`: D2 + GRPO |

## E — найдвартай байдал (`vars.best_config` дээр)

| ID | Тохиргоо |
|---|---|
| E1 | Алдааны мессежээр 2 удаа засна (хоосон үр дүнд нэг удаа дахин бодуулна) |
| E2 | 8 хувилбар (загварын sampling: Arctic T = 0.8, sqrl T = 0.7 top_p = 0.95), үр дүнгийн hash-аар санал хураана. Seed 1, 2, 3 |
| E3 | Санал нэгдлийн босгоор татгалзана. Шинээр ажиллуулахгүй: E2-ийн бичлэгээс гаргана — `t2s derive-abstain --from E2 --to E3 --threshold <t>`. Босгыг dev дээрх E2-ийн муруйгаас (тайлангийн «Санал хураалт» хэсэг) сонгоно |

## F — шинэлэг санаа

| ID | Хэрхэн |
|---|---|
| F1 / F1b | `t2s bird-prepare` → (qwen3.5-9b асаалттай) `t2s bird-translate` → хүн шалгах (`bird-review-export/import`). Дараа нь `eval --run F1,F1b --variants mn,latin,en`. Үнэлгээ SQLite дээр, BIRD-ийн set тэнцүү байдлаар. Үүсмэл dataset-ийн лиценз CC BY-SA 4.0 |
| F2 | B5 + дүрмийн утга тааруулалт (дагавар хасах, галиг → кирилл) — `value_mode: rule` |
| F3 | Сургасан галиг хувиргагч (`value_mode: learned`). Query log хуримтлагдсаны дараа: service-ийг shadow/pilot горимд ажиллуулж feedback цуглуулна → `t2s translit-learn` (audit + feedback-ээс галиг үг ↔ зөвшөөрсөн SQL-ийн кирилл утгын хос → `build/translit_pairs.jsonl`). Хосуудыг Transliterator-т өндөр жинтэй нэмж, тэмдэгтийн n-gram засварын хүснэгтийг шинэ галиг үгэнд хэрэглэнэ. Хосгүй үед F3 нь F2-той ижил |
| F4 | `f4`: C6 + 3 хувилбарын (кирилл, латин, англи) ижил үр дүнд нэмэлт reward. Галигийн зөрүү (A1 − A3) буурсан эсэхийг харна |
| F5 | A-шилдэг + «Restated: …» англи нэг өгүүлбэр (prompt-оор) |
| F6 | `f6`: сургалтын мөрөнд «Restated: …» (C-ийн жор) |
| F7 | `t2s intent --split all` (gemma-4-e4b) → intent JSON-ийг evidence болгоно. A4-тэй харьцуулна (`compare_to: A4`) |
| F8 | `t2s route --split all` (qwen3.5-9b) → KPI-г `knowledge/metrics.yaml`-аар compile хийнэ, бусад нь LLM рүү. Тайланд `routed` бичлэгийн хувь (хамрах хувь) гарна |
| F9a / F9b | Шошгогүй асуултын сан (shadow лог эсвэл синтетик) дээр E2 маягийн run хийнэ → `t2s select-annotations --pool-run <run.jsonl>` → шинжээч gold бичнэ → `t2s index-build --extra-examples build/annotations/f9_disagreement.jsonl --out build/index_f9a` (f9b мөн адил) → `eval --run F9a,F9b`. «100 тэмдэглэгээ тутмын өсөлт» = F9a − B5, F9b − B5 |
| F10 | `f10` adapter (sqrl, search_values tool-той траектор) + `search_values_tool` |

## G — agentic систем (төлөвлөгөөнөөс гадуурх нэмэлт; docs/AGENT.md)

text2sql-thesis-ийн Planner → Writer → Critic санаа. Детерминист tool (гүйцэтгэл, литерал шалгалт) + LLM critic +
засвар (≤ 2). `STAGE=G BEST_A=… BEST_CONFIG=… bash scripts/server/run_plan_l4.sh` (E-ийн дараа).

| ID | Тохиргоо |
|---|---|
| G1 | `best_config` + `agent: true` (tool + critic, `agent_rounds: 2`) |
| G2 | G1 − critic (`agent_critic: false`) |
| G3 | G1 + planner (`agent_plan: true`) |
| G4 | `best_a` + agent (RAG-гүй) |

## Thesis benchmark (docs/THESIS_BENCH.md)

text2sql-thesis-ийн кейс (цахим худалдааны DB) дээр ижил ID-уудыг `--config config/thesis.yaml`-аар ажиллуулж, thesis-ийн
agent системтэй (ижил vLLM дээр дахин ажиллуулсан) ижил шалгуураар харьцуулна:
`STAGE=thesis BEST_A=… BEST_CONFIG=… bash scripts/server/run_plan_l4.sh` → `results/thesis/COMPARE.md`.

## Test (200)

Бүлэг бүрийн эцэст шилдэг тохиргоог `--split test --variants mn,latin,en`-ээр нэг удаа ажиллуулна. Шийдвэрийг зөвхөн dev
дээр гаргана.
