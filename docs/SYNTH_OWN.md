# SYNTH_OWN — өөрийн схемд онилсон синтетик өгөгдөл (GPU-гүй, загвар/дүрэмд суурилсан)

RESEARCH_NEXT.md-ийн 5-р зөвлөмжийн (SING-SQL / OmniSQL / SENSE) «үүсгэх» хэсэг. Үүсгэхэд LLM, GPU, DB ашиглаагүй.
Гүйцэтгэлийн шалгалт ба сургалтыг дараа нь Kaggle дээр хийнэ.

| Файл | Үүрэг |
|---|---|
| `src/mn_text2sql/training/synth_own.py` | Каталог (fact, хэмжигдэхүүн, хэмжээс), 7 хэв маягийн үүсгүүр, guard/parse, eval-тэй ойролцоо давхардлын шүүлт |
| `scripts/synth/generate.py` | Үүсгэх CLI (`python -m mn_text2sql.training.synth_own`-тэй ижил) |
| `scripts/synth/exec_validate.py` | Kaggle (PG) дээр гүйцэтгэж шүүнэ, `result_hash` нэмнэ, TrainItem бичнэ |
| `tests/test_synth_own.py` | DB-гүй 16 тест (загварын олон янз байдлын доод хязгаар орно) |
| `build/synth_own/` | `candidates_raw.jsonl`, `candidates.jsonl`, `neardup_report.json`, `stats.json` |

## 1. Үр дүн (seed 42)

Нийт 2 780 мөр үүсгэсэн. Eval-тэй ойролцоо 1 мөрийг хассан (gold SQL-ийн cosine 0.908), **2 779 мөр** үлдсэн.
(Хэллэгийг олшруулсны дараа дахин үүсгэсэн тоо; seed, quota өмнөхтэй ижил.)
sqlglot parse 100%, SQL хамгаалагч 100% давсан. Эхний ажиллуулалтад guard CTE-ийн alias `t` нь `transactions t`-тэй
давхцаж байгааг илрүүлсэн. Тэр алдааг зассан.

| Хэв маяг | Тоо | Хүнд | Дунд | Хялбар | cross-domain |
|---|---:|---:|---:|---:|---:|
| join_fk — FK замаар холбох (1–3 алхам) | 420 | 272 | 148 | – | 130 |
| cte_multi — олон алхамтай CTE | 420 | 420 | – | – | 180 |
| ratio_nullif — `NULLIF`-тэй харьцаа | 399 | 180 | 219 | – | 65 |
| lag_pop — `LAG`-аар өмнөх үетэй харьцуулах | 360 | 360 | – | – | 52 |
| multi_currency — валютын хөрвүүлэлт | 380 | 165 | 215 | – | 38 |
| topk_ties — тэнцсэнийг оруулсан top-k | 380 | 289 | 91 | – | 107 |
| enum_filter — enum-ийн монгол нэрээр шүүх | 420 | – | 298 | 122 | 43 |
| **Нийт** | **2 779** | 1 686 | 971 | 122 | 615 |

1 104 мөрөнд `t2s.fx_to_mnt`, 1 386 мөрөнд enum код орсон. Домэйноор: cross 615, deposits 534, loans 440,
customers 324, applications 282, gl_trends 231, collection 207, payments 146.

**Хэллэгийн олон янз байдал** (мөрийн `notes`-ийн `phrasing=`; `stats.json → templates_per_pattern`):

| Хэв маяг | Монгол асуултын загвар (өмнө → одоо) | Ашиглагдсан (seed 42) |
|---|---:|---:|
| join_fk | 4 → 10 | 10 |
| cte_multi | 11 → 40 (дэд төрөл бүр 8) | 40 |
| ratio_nullif | 5 → 17 | 17 |
| lag_pop | 6 → 25 | 22 |
| multi_currency | 11 → 48 | 48 |
| topk_ties | 5 → 17 | 17 |
| enum_filter | 7 → 25 | 25 |

Parse 100%, guard 100% хэвээр. Асуултын eval-тэй хамгийн өндөр ойролцоо байдал буурсан: 0.7–0.8 мужид 49 → 22 мөр.

## 2. Загварчлал

**SQL тал.** SQL-ийг санамсаргүй загвараар биш, `semantic_model.yaml`-аас угсарна.

- *Fact* (15 хүснэгт): хэмжигдэхүүн, огнооны багана, enum багана, FK зам, суурь нөхцөл (`loan_payments.status =
  'POSTED'`, `transactions.status_code = 'COMPLETED'`), тэмдэглэгээ (`is_npl`, `is_dormant` ...) ба glossary-ийн
  харьцаа (NPL, PAR30/90, зөвшөөрлийн хувь, PTP биелэлт, барагдуулалтын хувь).
- *Dimension* (27): салбар, бүтээгдэхүүн, харилцагч, `individuals`/`organizations` (харилцагчаас дамжсан 2 алхам),
  ажилтан, зээл, кейс, хүсэлт, дансаар бүлэглэх ба шүүх.
- *FK зам*: алхам бүр semantic model-д FK болж зарлагдсан байх ёстой. Үүнийг `check_fk_paths()` шалгадаг ба тест хоосон
  жагсаалт хүлээнэ. Нэг хүснэгт нэг тогтмол alias-тай тул join давхардахгүй.
- *Валютын дүрэм* (`notes_mn` ба glossary-аас): `*_mnt` багана байвал түүнийг ашиглана. Үгүй бол
  `amount * t2s.fx_to_mnt(currency_code, <мөрийн огноо>)` хэрэглэнэ. `loans.interest_paid`-д `fx_rate_to_mnt`,
  `loan_writeoffs`-д зээлийн валют (`l.currency_code`), дансны үлдэгдэлд `t2s.business_date()` ханш авна. Тест
  олон валюттай баганыг хөрвүүлэлтгүйгээр `SUM` хийхийг хориглоно. Ганц үл хамаарал нь `amount_original` багана.
- *Enum*: утга нь зөвхөн semantic model-ийн `values`-аас гарна. Бүтээгдэхүүний төрлийг fact-ийн ангиллаар (зээл эсвэл
  хадгаламж) хязгаарлана. Top-k-ийн k нь enum-ийн утгын тооноос бага байна.
- *Хэв маягийн дэд төрөл*: CTE-д `above_avg`, `share`, `two_fact` (хоёр fact-ийг салбараар), `growth` (жил/хагас жил),
  `per_customer`; LAG-д сар, улирал, жил, `PARTITION BY`, сарын эцсийн агшин (`loan_dpd_history` + `loans`,
  `account_balances_monthly`); top-k-д `RANK`, `DENSE_RANK`, `FETCH FIRST k ROWS WITH TIES`, бүлэг доторх top-k;
  валютад валют бүрээр задлах, нэг валют, гадаад валютын хувь, ам.доллар руу хөрвүүлэх, сарын ханш.

**Асуултын тал.** Монгол болон англи асуултыг зэрэгцээ загвараар үүсгэнэ. Дэд төрөл (branch) бүр 8–10 хэллэгтэй:
үгийн дараалал, тушаах ба асуух хэлбэр (гарга / харуул / жагсаа / хэд вэ / ямар байна / ...өгөөч), ярианы хэлбэр
(«...-аар нь бүлэглээд», «сар бүрийн», «хамгийн их 10», «ихээс нь эрэмбэл»). Мөнгөн дүнг «... (төгрөгөөр)» эсвэл
«төгрөгөөр тооцсон ...» гэж ээлжлэн бичнэ. Хэмжээсийн үйлдэх тийг `mn_instr` гараар бичсэн толгой үгийн
хүснэгтээр (эгшиг гээгдэх: төлөв → төлвөөр, ангилал → ангиллаар), бусдыг `text.normalize.cyr_suffix`-ийн эгшиг
зохицлоор үүсгэнэ. «нийт нийт», «төлөвөөр» зэрэг алдааг тест хориглоно. Хугацаагүй үед үйл үгийг нөхцлийн дараа,
нэр үгийн өмнө тавина («..., тооцсон скорингийн үнэлгээнүүдийн»).
Хэмжигдэхүүнд нэрлэх ба заах тийн хэлбэрийг гараар бичсэн (жишээ нь «үлдэгдэл» / «үлдэгдлийг»). Хугацааны 9 хэллэг
байна: жил, хагас жил, улирал. Enum утгыг монгол нэрээр нь бичнэ: «зээлийн төлөв нь «Хугацаа хэтэрсэн» байгаа».
Латин галигийг `bird.data.latin_variant` хоёр хэв маягаар үүсгэнэ.
Мөр бүрийн бүтэц `knowledge/examples.jsonl`-тэй ижил. Нэмэлт талбарууд: `pattern`, `source: synth_own`,
`needs_exec_check: true`, `enum_codes`, `pool: synth_own`. `family` нь хэв маяг, fact, хэмжээс, хэмжигдэхүүнээс
тогтох загварын hash тул ижил загварын хувилбарууд нэг family-д орно.

**Давхардал.** Хэвийн болгосон SQL болон монгол асуулт хоёулаа давтагдахгүй. Ижил асуулт өөр SQL-тэй гарвал
шошгын шуугиан болох тул хасна.

## 3. Leakage-ийн хяналт

1. **Агуулга.** Загвар, хэллэгийг зөвхөн схем, glossary-аас бичсэн. Dev, test-ийн асуултыг уншаагүй, хуулаагүй,
   өөрчилж бичээгүй. Dev-ээс зөвхөн шошгын нэгтгэсэн тоог авсан (`stats.json → dev_tag_counts`).
2. **Автомат ойролцоо давхардлын шүүлт.** `eval/split.py`-ийн `_vec`/`_cos` (тэмдэгтийн 3-gram cosine)-ийг урвуу
   индексээр хэрэглэнэ. Тест нь энэ аргыг `_cos`-тэй тэнцүү гэж шалгадаг.
   - Асуулт: манай mn/latin/en хувилбарыг dev+test-ийн бүх хувилбартай (mn, latin, en, mixed) харьцуулна.
     Босго ≥ 0.8 нь `dedup_against_eval`, MANIFEST-тэй ижил.
   - Gold SQL: хэвийн болгосон SQL нь яг таарах эсвэл cosine ≥ 0.9 бол хасна (`gold_sql`, `alt_sql`,
     `interpretations`).
   - Үр дүн: **1 мөр хасагдсан** (SQL 0.908; хэллэгийг олшруулахаас өмнө мөн 1 мөр, 0.945). Хамгийн өндөр
     ойролцоо байдлын тархалт: асуултаар 0.3–0.8 (0.7–0.8 мужид 22 мөр; өмнө 49), SQL-ээр ихэнх нь 0.5–0.8.
     `neardup_report.json` нь зөвхөн id болон онооноос бүрдэнэ. Eval-ийн текст тайланд ордоггүй.
3. **Гүйцэтгэлийн түвшин (Kaggle).** `exec_validate.py` dev+test-ийн gold-ийн үр дүнгийн hash-тай таарсан мөрийг
   `same_result_as_eval` шалтгаанаар хасна. Энэ нь `dedup_against_eval`-ийн санаатай ижил.
4. Сургалтын өмнө ердийн `training dedup` (embedding байвал bge-m3 ≥ 0.9) болон `pack_train_d3.py`-ийн eval
   id/асуултын шүүлт дахин ажиллана.

## 4. Kaggle дээр гүйцэтгэлээр шалгах

```bash
# PG + t2s DB (bootstrap нь initdb → t2s db-build)
bash scripts/notebook/bootstrap.sh
# (шаардлагатай бол дахин үүсгэх — DB шаардлагагүй, ~1 мин)
python scripts/synth/generate.py --out build/synth_own
# гүйцэтгэж шүүх; C3-ийн own_synth.jsonl-ийг хадгалан нэмнэ
python scripts/synth/exec_validate.py --in build/synth_own/candidates.jsonl \
    --out build/synth_own/validated.jsonl --train-out data/train/own_synth.jsonl --append --workers 8
```

Хасах шалтгаанууд: `guard_fail`, `exec_error`, `timeout`, `empty`, `all_null`, `single_zero` (1×1 = 0/NULL),
`too_many_rows` (> 2000), `constant_metric` (олон мөртэй ч хэмжигдэхүүн бүгд ижил), `short_series` (LAG-д 2-оос цөөн
үе), `same_result_as_eval`, `dup_result`. Тоог нь `build/synth_own/exec_stats.json`-д бичнэ.

Хоосон үр дүн нилээд гарна гэж тооцож байна. Шалтгаан нь enum утгыг жигд сонгодог явдал юм: жишээ нь улирал
× ховор төлөв × сегмент хослол. Тиймээс 2 779 мөрөөс 50–70% нь үлдэх байх гэж таамаглаж байна (хэмжээгүй).

`build/synth_own/candidates.jsonl` нь pack_code-ийн `KEEP_FILES`-аар кодын zip-д ордог. Notebook өмнөх `t2s_results.zip`-ийг
(build/ орно) кодын дээр задалдаг тул `run_plan_l4.sh` эхэндээ KEEP_FILES-ийг кодын zip-ээс дахин задална
(`restore_keep_files`, 2026-10-31) — архивын хуучин хуулбар шинэ candidates-ийг дарахгүй.

**`STAGE=synth` — одоогоор хэрэгжээгүй** (`run_plan_l4.sh`-д алга). Хэрэгжүүлэхэд хэрэгтэй:
1. `data/train`-ийн хадгалалт: архивт ч, кодын zip-д ч ордоггүй — гаралтыг `/kaggle/working/synth` эсвэл `build/`-д бичиж,
   stage эхлэхэд сэргээнэ. `--append` нь `data/train/own_synth.jsonl`-ийн C3 мөрийг хадгалдаг; Kaggle-д тэр файл алга бол
   чимээгүй хоосон (D3-ийн өгөгдлийн багцад зөвхөн `own_synth_clean.jsonl`) — C3-ийн мөрийг D3-ийн багцаас авах.
2. `exec_validate`-д хугацааны төсвийн хязгаар; `same_result_as_eval` нь dev+test-ийн gold-ийг гүйцэтгэж үр дүнгийн hash-ийг
   уншдаг — үүнийг зөвшөөрөх эсэхийг шийдэх эсвэл `--no-eval-hash`.
3. `dedup`-ийг vLLM-ээс өмнө GPU дээр (bge-m3) эсвэл `--no-embed`-ээр.
4. `sample`/`cot` нь суурь Arctic-ийг (vLLM, ~15 GB) үйлчлүүлэх ёстой — `serve_d2_merged` нь Kaggle-д суурийн HF кэшийг
   устгадаг тул дараалал чухал; timeout ба resume (`sample` нь stats-аас үргэлжилдэг, `cot`-д resume алга).
5. Сургалт — D3-ийн жороор тусдаа сесст (`STAGE=train_d3`).
6. Dev: D3 synth-тэй ↔ synth-гүй, ижил seed-ээр (~45 GB диск, тооцоо).
7. `SYNTH_STEPS`-ийн шалгалт (`MT_STEPS` шиг үл мэдэгдэх алхмыг анхааруулах).

## 5. D2/D3 LoRA-г дахин сургахад холбох (кодыг өөрчлөхгүй)

Одоо байгаа урсгал (`training/cli.py`, `config/training.yaml`):

```bash
# 1) eval-тэй давхардал (embedding эсвэл 3-gram): own_synth.jsonl → own_synth_clean.jsonl
python -m mn_text2sql.training dedup            # GPU байхгүй бол --no-embed
# 2) C1: Arctic өөрөө шийдэж чадсан мөрийг л хадгална (vLLM serve arctic-r1-7b). Энэ бол «өөрийн загвараар шүүх» алхам.
python -m mn_text2sql.training sample --model arctic-r1-7b --source own_synth_clean.jsonl --prompt plain \
    --base-url http://localhost:8000/v1
# 3) C2: шийдэгдээгүйд gold-guided CoT
python -m mn_text2sql.training cot --model arctic-r1-7b --source own_synth_clean.jsonl --prompt plain \
    --base-url http://localhost:8000/v1
# 4) D3 (Kaggle 2×T4, QLoRA): data: [rejection, cot, synth, human] → synth = own_synth_clean (formats.SOURCE_FILES)
python scripts/notebook/pack_train_d3.py --src data/train          # локалд; эсвэл Kaggle-д шууд:
python -m mn_text2sql.training build --recipe d3 --model arctic-r1-7b
torchrun --standalone --nproc_per_node 2 -m mn_text2sql.training sft --recipe d3 --model arctic-r1-7b \
    --output-dir /kaggle/working/d3_train/d3__arctic-r1-7b
# эсвэл: STAGE=train_d3 BEST_A=A7 D3_EVAL=1 bash scripts/server/run_plan_l4.sh
```

- `TrainItem`: `id = synth_own-<SO-...>`, `source = own_synth`, `extra = {origin, pattern, family, result_hash}`
  (+ `order_matters: true` мөр нь дараалалтай бол — 2026-10-31, L10: sampling/GRPO-ийн зөвийн шалгалт үнэлгээний дүрмээр).
  `sampling.item_family` нь `extra.family` (`SO-...`)-г буцаана. Жишээний санд ийм family байхгүй тул few-shot-оос
  юу ч хасагдахгүй.
  `pack_train_d3.py`-ийн eval id-ийн шүүлт `synth_own-` угтварыг eval id гэж андуурахгүй.
- D2 нь `data: best_sft` болон RAG prompt (16k) ашигладаг. Тэнд өөрийн DB-ийн мөр урт prompt-д хасагдаж байсан тул
  энэ өгөгдөлд D3 (H4s prompt, max_length 9216) илүү тохирно.
- Холих харьцааг хязгаарлана: synth_own-ийн C1-ээс шүүгдсэн мөрийг human (160) болон C3-ийн synth-ээс 2–3 дахин
  ихгүй байлгах. Одоогийн жор тусад нь хязгаар тавьдаггүй. Хэрэгтэй бол `--limit` эсвэл файлын түвшинд sample хийнэ.
- Шалгах туршилт: D3 (synth_own-гүй) ба D3+synth_own-ийг ижил seed-ээр сургаж dev дээр харьцуулна. Шошгоор (cte,
  time_series, multi_currency, ratio, cross) задалж харна. Test-ийг зөвхөн эцэст нь ажиллуулна.

## 6. Эрсдэл (шударга үнэлгээ)

- **Загварт суурилсан өгөгдөл хор хийж болно.** Arctic-Text2SQL-R1-д шүүгээгүй синтетик өгөгдөл 14B загварын EX-ийг
  64.9 → 64.6 болгож бууруулсан. Өөрийн загвараар шүүсэн өгөгдөл 66.5 болгож өсгөсөн [`yao_2025_arctic_text2sql_r1`].
  Тиймээс эндээс гарсан мөрийг шууд SFT-д оруулахгүй. Гүйцэтгэлийн шүүлт ба C1 rejection sampling хоёулаа заавал.
- **Хэллэгийн олон янз байдал хязгаартай.** Дэд төрөл бүр 8–10 хэллэгтэй болсон ч загварт суурилсан хэвээр. Загвар
  асуултын хэлбэрт хэт тааруулж, хүний бичсэн асуултад (dev) ерөнхийлөх чадвар буурч магадгүй. Хүнд, урт нийлмэл нэр үг ихтэй асуулт («...-ын ...-ын дундаж ...»)
  байгалийн биш сонсогдоно. Сайжруулах арга: MADLAD/LLM-ээр дахин найруулах (дахин найруулсан асуултыг ойролцоо
  давхардлын шүүлтээр дахин шалгана).
- **Утгын алдаа.** SQL нь схемийн хувьд зөв, гэхдээ бизнесийн утгаараа маргаантай байж болно. Жишээ нь кейсийн
  цуглуулсан дүнг нээсэн өдрийн ханшаар хөрвүүлдэг. Барагдуулалтын хувийн glossary-ийн тодорхойлолтод огноо
  заагаагүй.
- **Тархалтын зөрүү.** Хүнд/CTE мөр синтетик өгөгдлийн 59%-ийг эзэлдэг. Dev-д `cte` 31, `time_series` 22,
  `multi_currency` 18 шошготой. Сургалтад хэт их жин өгвөл энгийн асуултын гүйцэтгэл буурч магадгүй.
- **Rejection sampling-ийн хязгаар.** C1 нь Arctic аль хэдийн шийдэж чаддаг мөрийг л үлдээнэ. Хамгийн хэцүү хэв
  маягийн ахиц нь C2 (gold-guided CoT)-ийн чанараас хамаарна.
- **Ойролцоо давхардлын шүүлт гадаргуугийн шинжтэй.** Тэмдэгтийн 3-gram cosine нь утгын давхардлыг (өөр үгээр
  илэрхийлсэн ижил асуулт) бүрэн барихгүй. Энэ дутагдлыг үр дүнгийн hash шүүлт (Kaggle) ба `training dedup`-ийн
  embedding нөхнө.
- Хүлээгдэж буй ахиц (RESEARCH_NEXT §5): +3…+8 асуулт. Энэ нь тодорхой бус бөгөөд хэмжээгүй.
