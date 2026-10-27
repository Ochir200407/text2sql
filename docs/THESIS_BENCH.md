# Thesis benchmark — text2sql-thesis-ийн 150 кейс дээр хоёр системийг харьцуулах

Зорилго: text2sql-thesis-ийн agent систем болон энэ шинэ системийг **ижил асуулт, ижил өгөгдөл, ижил загвар (vLLM),
ижил шалгуураар** харьцуулах.

## Юуг авсан бэ (text2sql-thesis-д юу ч бичихгүй)

| Эх (text2sql-thesis) | Энд | Тайлбар |
|---|---|---|
| `build/data.db` (цахим худалдаа, 20 хүснэгт, 2023–2025) | `data/thesis/ecom.sqlite` + PostgreSQL `thesis_ecom` (схем `t2s`) | Шинэ систем PostgreSQL дээр ажилладаг тул өгөгдлийг мөр мөрөөр нь хуулсан. BOOLEAN → boolean, REAL → numeric |
| `schema/tables/*.yaml` | `knowledge/thesis/semantic_model.yaml` | Хүснэгт ба баганын монгол/англи тайлбар, enum, FK |
| `resources/glossary.yaml` | semantic model-ийн синоним, enum-ийн англи нэр | Хүснэгт, багана, утгын 128 бичлэг. Цаг хугацаа, агрегатын үгс орохгүй (ерөнхий хэлний бичлэг) |
| `evals/cases/*.json` (150) | `data/thesis/dev.jsonl` (19), `test.jsonl` (96), `knowledge/thesis/examples.jsonl` (train 29), `data/thesis/examples_dev.jsonl` | train = few-shot сан. Thesis-ийн few-shot сан нь train + dev (test-ээс гадуурх бүгд), тиймээс test дээр thesis ~19 жишээгээр давуу. Тэнцүү нөхцөл: `MATCHED_FEWSHOT=1` → test-ийг train + dev сантай (`-fs_traindev` tag) нэмэлтээр ажиллуулна |

**Хассан кейс.** Детерминист бус gold-той 6 кейс (q024, q077, q091, q093, q120, q133; thesis E-33).

**Gold.** SQLite gold-ийг sqlglot-оор PostgreSQL руу хөрвүүлсэн. Тусгай засвар: `julianday`, boolean = 0/1, огноо дээрх
LIKE/substr. Gold бүрийг **хоёр DB дээрх үр дүнгээр нь** тулгасан: 144-өөс 143 нь автоматаар таарсан, 1-ийг
(q147, `strftime('%w')`) `knowledge/thesis/gold_overrides.yaml`-аар гараар зассан.

**Хоосон gold.** `gold_returns_empty` тэмдэгтэй 5 кейс хадгалагдсан, `gold_empty` таг авна. BIRD ба thesis-ийн дүрмээр
хоосон таамаг хоосон gold-той тэнцүү (баганын тоо хамаарахгүй).

**Латин хувилбар.** Thesis-д латин хувилбар байхгүй тул шинэ системийн галигаар (v/u/w хэв маяг) үүсгэсэн.

## Шалгуур

Бүх систем `eval/compare.results_match`-аар (мөрийн multiset, багана солигдохыг зөвшөөрнө, тоон хүлцэлтэй) оноологдоно.

- **Шинэ систем.** PostgreSQL gold-той харьцуулна.
- **Thesis-ийн систем** (`thesis-score`). Таамгийг SQLite дээр, SQLite gold-той харьцуулна.
- **Харьцуулах боломжтой эсэх.** Хоёр gold-ийн үр дүн тэнцүү гэж баталгаажсан. Тиймээс диалектын ялгаа оноонд нөлөөлөхгүй.

Тайланд thesis-ийн өөрийн EX-ийг (`thesis_own_ex`) хамт харуулдаг.

**Таамгийн диалект.** Gold-ууд л хоёр DB дээр тэнцүү гэж баталгаажсан. Таамаг өөр өөр хөдөлгүүр дээр ажиллана:
- SQLite сул: LIKE том жижиг үсэг ялгахгүй, `bool = 1`, `strftime`/`julianday`.
- PostgreSQL хатуу. Шинэ системийн SQLite протоколоор бичсэн SQL (sqlite_native: A1, A3) PostgreSQL дээр ажиллах тул энэ нь
  шинэ системд сул тал болно. Шалгуур ижил боловч «хөдөлгүүрийн нөлөө» бүрэн арилаагүй.

**EX-ийн бусад зөрүү.**
- **Эрэмбэ.** Бүх кейс `order_matters: false`. Thesis-ийн шалгуур нь ORDER BY-тай 19 gold-ийг эрэмбээр харьцуулдаг.
- **Хоосон gold.** `gold_empty` 5 кейс нь ямар ч хоосон таамгийг зөв гэж тооцно. COMPARE.md-ийн «gold хоосон-гүй» багана
  эдгээрийг хассан EX-ийг харуулна.
- **1000 мөрийн хязгаар.** Thesis-ийн өөрийн EX нь 1000 мөрөөр тасалдаг. 8 gold 1000-аас олон мөртэй (q062, q092: 20 000).
  Энэ нь `thesis_own_ex`-ийн зөрүүний нэг шалтгаан.

Эдгээр нь хоёр системд ижил үйлчилнэ.

**Анхааруулга.** E-40-ийн үр дүнг дахин оноолоход thesis-ийн өөрийн оноотой 1 кейсээр зөрсөн (q100). Шалтгаан нь gold-ийн
өөрчлөлт: E-40 үед q100-ийн gold 3 баганатай (`opened_date`-тэй) байсан бөгөөд E-40-ийн дараах gold аудитаар (thesis
E-43) нэг баганыг хассан. Таамаг одоогийн gold-той үгчлэн ижил тул шинэ шалгуураар зөв гарна. E-40-ийн бичлэгт
хадгалагдсан gold нь одоогийнхоос 3 кейсэд (q077, q100, q108) өөр. **Тиймээс хуучин үр дүнг биш, хоёр системийг
одоогийн gold ба өгөгдөл дээр дахин ажиллуулж харьцуулна** (`ORIGINAL=1`).

## Ажиллуулах

```bash
bash scripts/server/serve_model.sh arctic-r1-7b                     # vLLM (өөр терминалд)
ORIGINAL=1 bash scripts/server/run_thesis_bench.sh arctic-r1-7b     # шинэ систем (A6, B5, G1) + thesis-ийн систем
IDS="A6,B5,E1,G1,G2,G3" bash scripts/server/run_thesis_bench.sh sqrl-9b
cat results/thesis/COMPARE.md
```

Скрипт дараах алхмуудыг хийнэ:
1. Анх удаа `thesis-import` → `index-build` → `check-gold` ажиллуулна.
2. Туршилт бүрийг `t2s --config config/thesis.yaml eval ...`-ээр ажиллуулна. ID нь ББСБ-ийн туршилттай ижил, суурь
   тохиргоо нь `config/experiments.yaml`-ийн vars.
3. `ORIGINAL=1` үед thesis-ийн репог `build/thesis_snapshot` руу хуулна (`.venv`, `results`, `thesis`-ийг оруулахгүй). Хуулбарт
   зөвхөн `requirements.txt`-ээр venv үүсгэнэ (vLLM ба torch-гүй), `config/runpod.yaml`-ийг манай vLLM ба
   served name руу чиглүүлнэ. Дараа нь `scripts/run_agent_eval.py --dialect sqlite --split <split> --workers 1`
   ажиллуулна (thesis-ийн E-41: зэрэгцээ ажиллуулалт детерминист биш). Эцэст нь `thesis-score` хийнэ.
4. `thesis-compare` → `results/thesis/COMPARE.md`. Хүснэгтэд систем бүрийн EX-ийг хувилбар бүрээр (mn/en/latin),
   хүндрэлээр, гүйцэтгэгдсэн %-иар харуулна. Мөн ижил (асуулт, хэл) хос дээр thesis-тэй харьцуулсан Δ ба McNemar p-утга бий.

Гараар:

```bash
t2s --config config/thesis.yaml thesis-import --src ../text2sql-thesis --overwrite
t2s --config config/thesis.yaml index-build
t2s --config config/thesis.yaml eval --run G1 --model arctic-r1-7b --split dev --variants mn,en,latin
t2s --config config/thesis.yaml thesis-score --file <thesis/results/...-dev.json> --file <...-test.json> --name thesis-agent --llm arctic-r1-7b
t2s --config config/thesis.yaml thesis-compare
```

## Тайлбарлахдаа анхаарах

- **Жижиг түүвэр.** dev 19, test 96, хоёр хэлтэй бол хосын тоо 38 / 192. dev дээрх 2–3 кейсийн зөрүү статистикийн хувьд ач
  холбогдолгүй. Дүгнэлтийг test дээрх McNemar-аар гаргана.
- **Thesis-ийн систем зөвхөн Arctic-д зориулагдсан** (prompt, `<think>` парсер). sqrl-9b-ийн харьцуулалтыг зөвхөн шинэ
  систем дотор хийнэ.
- **Хэрэглэгдэхгүй хэсгүүд.** B3 (нэр томьёо) нь энэ DB дээр нөлөөгүй: бизнес томьёоны сан байхгүй, `glossary.yaml` хоосон. F8
  (KPI router) ч хамаарахгүй. Test-suite (TS-EX) байхгүй.
- **Thesis дээр утгагүй ID.** A4, F7, F8 нь ББСБ-ийн prompt ба `build/`-ийн нийтлэг кэш ашигладаг. B10-ийн FK-хөршийн
  жагсаалт ба `generic` family-ийн system prompt нь ББСБ-д зориулагдсан. Тиймээс эдгээрийг thesis benchmark дээр ажиллуулахгүй.
  Анхдагч IDS (A6, B5, G1–G3) эдгээрээс хамаарахгүй.
- **Adapter-тай ID.** C*, D*, F4, F6, F10 болон adapter-тай `best_config` дээрх G1 скриптэд алгасагдана.
- **vLLM-ийн хувилбар.** Thesis vLLM 0.8.5 дээр хэмжигдсэн. Энд манай vLLM-ийн хувилбар ашиглагдана. Хоёр систем нэг серверийг
  хуваалцдаг тул харьцуулалт шударга. Гэхдээ thesis-ийн хуучин тоотой (E-40 dev 45.2%) шууд тэнцүүлэхгүй.
