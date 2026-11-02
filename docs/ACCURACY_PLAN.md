# Нарийвчлалыг хэрхэн өсгөх вэ — судалгаа ба дараалал (2026-10-28)

## Одоогийн байдал (dev, хариулах боломжтой 224; бүгд сургасан MADLAD орчуулгатай, Arctic + D2)
| Тохиргоо | Зөв | Тэмдэглэл |
|---|---|---|
| M1a (суурь) | 127 (56.7%) | I3t 105-аас +22, p = 0.005 |
| M1m (утгын тэмдэглэгээ) | 129 | +2/−0, p = 0.5 |
| M1g8 (нэр томьёо 8) | 126 | +13/−14, p = 1.0 |
| M1ks (Arctic хуваарилагч) | **130 (58.0%)** | +15/−12, p = 0.70 |
| **Дөрвийн нэгдэл (oracle)** | **146 (65.2%)** | 104 асуултыг бүгд зөв, 42 асуултыг заримдаа зөв |

## Гол олдвор
Тохиргоо бүр **өөр өөр асуултыг** зөв хариулж байна: 42 «тогтворгүй» асуулт. Зөвийг нь сонгож чадвал +16 асуулт (≈ +7 хувь).
Тиймээс хамгийн их ахиц **олон хувилбар үүсгэж, зөвийг нь сонгох**-оос гарна — шинэ мэдлэг нэмэхээс илүү.

## Судалгааны нотолгоо
- CHASE-SQL (ICLR 2025): олон өөр аргаар хувилбар үүсгэж (задлах, гүйцэтгэлийн төлөвлөгөө, синтетик жишээ), сургасан
  хос-харьцуулагч сонгогчоор сонгоход өөрийн тогтвортой байдлын (self-consistency) сонголтоос илүү; BIRD 73.0%
  ([arXiv](https://arxiv.org/html/2410.01943v1)).
- Arctic-Text2SQL-R1: утгын хайлт + 8 хувилбарын санал хураалт хоёулаа тус бүр нэмэртэй ([arXiv](https://arxiv.org/pdf/2505.20315)).
- Манай E2: 8 хувилбар +4.3 хувь (dev), дүрэм +12 (dev) / +6.5 (test; dev-д дэмжлэгтэй үгээр +4.5; хариулах +3/−1, p = 0.63).

## Дараалал (хүлээгдэх нөлөө — таамаг)
1. **Чуулга (M1ens)** — GPU-гүй: M1a, M1ks, M1g8 (+M1rr, M1i4)-ийн SQL-ийг үр дүнгээр санал хураах (`run_plan` алхам `ens`). +3…+8.
2. **M1ks + 8 хувилбар + засвар + дүрэм** (хуваарилагч дээр E-стек) — +5…+10 (dev).
3. **Сургасан reranker (M1rr)** — нэр томьёоны сонголт; bge-ийг өөрийн 635 жишээгээр сургана. 0…+4.
4. **Сонгогчийг сайжруулах** — үр дүнгийн олонхоос гадна: хоосон/NULL/алдаатайг хасах (байгаа), Arctic-аар хос харьцуулах
   (CHASE маягаар, нэмэлт загваргүй), итгэл бага бол тодруулах.
5. **Утгын алдаа** — enum-ийн монгол нэр → код (`semantic_model`) prompt-д; M1-ийн утга хадгалалт 13.6%.
6. Хүнд асуултын хэв маяг (LAG-ийн оны зааг, идэвхтэй багц, валют, fan-out) — шинэ жишээгээр (M1i4).

Бүх дүнг test дээр зөвхөн эцэст нэг удаа батална.

## Үр дүн (2026-10-28, dev хариулах 224)
| Тохиргоо | Зөв | M1a-тай |
|---|---|---|
| M1rr (сургасан reranker) | 131 (58.5%) | +18/−14, p = 0.60 |
| **M1ens (5 тохиргооны чуулга)** | **137 (61.2%)** | **+11/−1, p = 0.006** |
| M1ensr (чуулга + дүрэм) | 137; нийт 197/300 (65.7%) | — |

Сургасан reranker нэр томьёоны хамралтыг 77% → 79% (дундаж 4 → 1.4 нэр томьёо) болгосон ч SQL-ийн оноонд ялгаа батлагдаагүй.
**Чуулга — анхны статистикийн хувьд батлагдсан сонголтын ахиц.**

## Дараагийн Kaggle сесс (квот Бямба 10-31-нд шинэчлэгдэнэ)
1. **Эцсийн test (нэг удаа):** M1ens / M1ensr-ийн test — test дээр M1ks, M1g8, M1m, M1rr-ийг ажиллуулж (≈4 × 1.5 цаг), чуулгыг test дээр хэмжинэ. I3t 62/151, M1a 81/151-тэй McNemar.
2. M1i4 (475 жишээ) dev; M1ks + 8 хувилбар + засвар + дүрэм (dev).
3. Reranker-ийн ашиг SQL-д гарахгүй байгаа шалтгааныг шинжлэх (сонгосон нэр томьёо зөв ч загвар хэрэглэхгүй байна уу).

## Зорилт 70% (хэрэглэгч, 2026-10-29) — шударга хэмжүүр: test (200), нийт EX
Одоо: test M1a 101/200 (50.5%; хариулах 81/151, боломжгүй 6/24, хоёрдмол 14/25). Dev M1ensr 197/300 (65.7%, dev-ийн дүрэмтэй).
Таамаг: дүрэм +5…+8, чуулга +3…+4, хос харьцуулалт +2…+4 → test ≈ 60–65%; 70%-д нэмэлт санаа хэрэгтэй.

**Version 26 (квот 10-31-нд):** `config/kaggle_overrides_next.env` — `MT_STEPS="tens ens pick"`.
- `tens` (run_plan mt): M1ks/M1rr/M1g8/M1m-ийг test дээр (M1m-д тэмдэглэгээтэй test орчуулга, M1rr-д reranker-ийн test сонголт),
  дараа нь загваргүй M1r (M1a + дүрэм), M1ens, M1ensr test — бүрэлдэхүүн dev-тэй ЯГ ижил (суурь M1rr + M1ks M1a M1g8 M1m).
- `pick` (шинэ, `eval/pick.py`, `reselect --pick`): санал зөрвөл шилдэг 2 үр дүнгийн бүлгийн SQL-ийг Arctic (d2) A/B ба B/A
  гэж хоёр удаа харьцуулж, хоёулаа нэгийг сонговол түүнийг авна (байрлалын хазайлтаас сэргийлнэ). Эхлээд зөвхөн dev
  (M1ensp/M1enspr); test-д `PICK_TEST=1` нь dev дээр M1ens-ээс ахисны дараа л (test-ийг харж сонгохгүй).

## FK-bridge (schema linking)
`retrieval/fk_bridge.py`, сонголт `fk_bridge` (анхдагч унтраалттай), тохиргоо **`M1fk`** (= M1a + fk_bridge). Retrieval-ийн
шилдэг 3 хүснэгтийн хос шууд FK-гүй бол ≤2 ирмэгтэй замын завсрын хүснэгтийг нэмнэ (хоёуланг нь заадаг холбоос хүснэгт —
loans ← loan_collaterals → collaterals — нийтлэг эцгээс (customers) илүү); мөн шилдэг 2 хүснэгтийн 1 алхмын FK хөршөөс
оноо > 0.5-тай 1-ийг. Нийт ≤2 нэмэлт. Тааруулсан тор: `python -m mn_text2sql.retrieval.fk_bridge_eval <run> --grid`.

**Офлайн, ЗӨВХӨН DEV** (хариулах 224; H4-ийн `retrieval.linked_tables` — M1a-тай ижил монгол retrieval, top_tables 6;
зөв/буруу = `M1a_answerable_correct.txt`; 1 алхмын хөршийн оноо = BM25-only ойролцоо):

| | Бүрэн хамралт (бүгд / буруу / зөв) | Дундаж recall | Нэмэгдсэн хүснэгт (дундаж; суурь 8.7) |
|---|---|---|---|
| Өмнө | 97.3% / 94.8% / 99.2% | 98.9% | 0 |
| Зөвхөн гүүр (hop_top=0) | 98.2% / 95.9% / 100% | 99.3% | +0.03 |
| **fk_bridge (анхдагч)** | **99.1% / 97.9% / 100%** | **99.7%** | **+0.58** |

Англи retrieval-тай H3-д (суурь хамралт муу): 91.1 → 95.5%, +0.70 хүснэгт.
**Анхааруулга:** M1a-д `schema_linking: false` — DDL-д 28 хүснэгт бүгд байдаг; linked_tables нь зөвхөн баганын тайлбарын
эрэмбийг (schema_token_budget) шийддэг тул M1fk-ийн нөлөө бага байх төлөвтэй. H4-ийн буруу хариултын 17.6%-д үүсгэсэн SQL
gold хүснэгтийг алгасдаг ч тэр хүснэгт prompt-д 96.5%-д нь байсан → «хүснэгт дутуу» алдаа голдуу загварын сонголт,
retrieval биш. FK-гүүр schema_linking: true тохиргоонд (I3s3/M1s3 маяг) илүү чухал. GPU run хийгээгүй — EX-ийн нөлөө тодорхойгүй.

## Abstain v2 (хариулах / татгалзах / тодруулах, GPU-гүй)
`pipeline/abstain.py`, сонголт `abstain_v2` (анхдагч унтраалттай), `reselect --abstain-v2`; тохиргоо **`E4a`** (E4f + v2,
дүрэмгүй), **`E4ra`** (E4 дүрэм эхэлж, үлдсэнд v2), **`M1ensa`** (M1ens + v2), **`M1ensra`** (M1ensr + v2).
Гар дүрмийн (`rule_refuse/rule_clarify` — dev-ийн хэллэгээс) оронд 15 ерөнхий шинж: AUTHORING-ийн ангиллын хэллэгийн бүлэг
(хувийн мэдээлэл, бичих, SQL биш, ирээдүй + as_of 2026-06-30-аас хойшхи огноо, гадны мэдлэг, схемд байхгүй HR/төлөвлөгөө),
мэдлэгийн сангийн OOV (semantic_model + glossary + metrics + examples*.jsonl — eval-гүй), glossary `ambiguous` нэр томьёо,
бүдэг хугацаа/«хамгийн»/тэмдэг нэр, тодорхой тоо/огноо, хүснэгтгүй SQL, хоосон үр дүн. 2 логистик регресс (numpy, L2,
ангиллын жин) + босго (сургалтын EX-ээр). Итгэл/тархалтын шинж (confidence, ялгаатай hash, алдааны хувь) dev CV-д
нэмэр өгөөгүй (153.0 vs 154.5) тул анхдагч багцаас хассан.

**Үнэлгээ — ЗӨВХӨН DEV, family-аар 5-fold × 10 давталт** (`python -m mn_text2sql.pipeline.abstain --cv`; суурь = E4f,
«хариулбал зөв» нь E4f-ийн бичлэгээс; татгалзал/тодруулга нь `score_answer`-тэй ижил тооцоо):

| Систем (dev, 300) | Нийт EX | Хариулах (224) | Хариулах боломжгүй (38) | Хоёрдмол (38) |
|---|---|---|---|---|
| E4f (дүрэмгүй) | 135 | 47.8% | 39.5% | 34.2% |
| E4 дүрэм (**in-sample** — dev-ээс бичсэн) | **159** | 47.8% | 78.9% | 57.9% |
| Abstain v2 (CV) | 154.5 ± 1.7 | 45.5% | 78.7% | 59.5% |
| E4 дүрэм + v2 (CV; дүрмийн хэсэг in-sample) | 160.9 ± 1.0 | 45.5% | 89.2% | 65.8% |

Confusion (v2, CV дундаж): answerable → answer 214.8 / clarify 6.8 / refuse 2.4; unanswerable → refuse 27.3 /
clarify 1.4 / answer 9.3; ambiguous → clarify 14.6 / refuse 1.4 / answer 22.0. E4 дүрэм: answerable-д 0 худал дохио,
unanswerable → refuse 28 / answer 10, ambiguous → clarify 10 / answer 28.

**Шударга дүгнэлт:** v2 нь dev CV-д E4 дүрмийг (159, in-sample) **давахгүй** (−4.5): хариулах боломжгүй/хоёрдмолд
адил эсвэл бага зэрэг сайн, гэхдээ хариулах боломжтой асуултын ~5-ыг (2.4%) буруу татгалздаг/тодруулдаг. Дүрмийн 159 нь
dev-ийн хэллэгт тааруулсан тул out-of-sample биш — test дээр ялгаа багасах төлөвтэй; v2-ийн 154.5 нь out-of-family тооцоо.
Хэллэгийн бүлгийг би ч dev-ийг уншсаны дараа бичсэн (dev-ийн өвөрмөц 5 үгийг (АТМ, шүүхийн шийдвэр, гадаад, тэтгэвэр,
бизнесийн) хассаны дараа CV 154 → 150, «short» шинжийг хасаад 154.5) — CV ч бага зэрэг өөдрөг. Санал: test-д E4 (үндсэн)
ба E4ra/M1ensra-г (дүрэм + v2) хоёуланг нь мэдээлж, v2-ийн нэмрийг out-of-sample гэж тайлбарлах. Pipeline-ийн онлайн
горимд (orchestrator) холбоогүй — зөвхөн reselect. PG шаардлагатай тул reselect-ийг ажиллуулаагүй; тоо бүгд офлайн.
**In-sample (2026-10-31):** `reselect --abstain-v2` нь бүх dev (300) дээр сургасан `DEFAULT_WEIGHTS`-ийг хэрэглэдэг тул
M1ensra-ийн **dev** дүн in-sample (өөдрөг) — шийдвэрт хэзээ ч ашиглахгүй (V27 `ensx`-д лавлагаа л;
`ingest_results.py --tag V27`-д «§», McNemar хосгүй). Out-of-sample тооцоо нь зөвхөн дээрх CV ба test.

## M1cte (CTE / дэд алхмын 2-р үүсгэгч, чуулгад)
`llm/prompts.py` `GEN_STYLE_HINTS`, сонголт `gen_style` (анхдагч `"default"` — prompt байт-байтаар өөрчлөгдөхгүй:
`tests/test_gen_style.py` нь git HEAD-ийн кодоор гаргасан prompt-ын sha256-тай тулгана, мөн `gen_style` анхдагч үед run-ийн
fingerprint-д орохгүй). Тохиргоо **`M1cte`** (= M1a + `gen_style: cte`), **`M1dec`** (= M1a + `gen_style: decompose`).
D2 LoRA-ийн сургалтын prompt (Arctic Figure C.1) хэвээр — зөвхөн Instructions-ийн төгсгөлд мөр нэмнэ:
* `cte`: дэд алхмаар төлөвлөх; эцсийн SQL-ийг `WITH step1 AS (...), step2 AS (...)` гинжээр; харьцаа/хувийг тоо ба хуваарийг
  тусад нь CTE/баганаар, `numerator::numeric / NULLIF(denominator, 0)`, хувь = `100.0 * ... / NULLIF(...)` (gold-ийн хэлбэр).
* `decompose`: `cte` + олон домэйны асуултыг хэсэг бүрээр нь join түлхүүрт нэгтгээд дараа нь холбох (fan-out-аас сэргийлэх).

**Таамаг.** Сул тал нь хэцүү (26–33%), CTE (4–7/31), харьцаа (24/54), cross (7–9/28). Одоогийн чуулгын гишүүд (M1a, M1ks, M1g8,
M1m, M1rr) бүгд ижил prompt-ын хэлбэртэй тул ижил алдаа давтана (5 гишүүний oracle 155/224 = 69.2% ↔ чуулга 137; 146 нь M1rr-гүй 4-ийнх). Өөр бүтэцтэй (DIN-SQL / CHASE-SQL
divide-and-conquer) үүсгэгч нь хэцүү асуултад **шинэ зөв SQL** нэмж, oracle-ийг өсгөнө. Ганцаараа M1a-аас бага байж ч болно
(LoRA-ийн тархалтаас бага зэрэг холдоно; энгийн асуултад илүүдэл CTE).
**Хүлээгдэх нөлөө (таамаг):** M1cte ганцаараа M1a ± 4 (−2 … +2 хувь); хэцүү/CTE/ratio дэд бүлэгт +2 … +6 асуулт; oracle
(M1a ∪ … ∪ M1cte) +3 … +8; чуулга (M1ensc − M1ens) 0 … +4 асуулт (≤ +1.8 хувь). Prompt урт: +~133 токен (cte), +~205
(decompose) — 5 dev хэцүү асуултын офлайн render-ээр (RAG-гүй prompt-д +4…+19%; бүтэн M1a prompt-д харьцангуй бага).

**Dev үнэлгээ (GPU, d2 vLLM; test-д хүрэхгүй):**
1. `MT_T2S="M1cte" MT_STEPS="t2s"` (run_plan_l4.sh mt нь `STEPS`-ийг `MT_STEPS`-ээс дахин онооно) — `t2s eval --run M1cte --model $MODEL --split dev --variants mn` (M1a-ийн MADLAD кэш
   `m1_madlad_improved`; run_plan-ийн t2s хүснэгтэд `M1cte:m1_madlad_improved` бүртгэгдсэн). Цаг байвал M1dec ч мөн.
2. Ганцаараа: `t2s compare` (эсвэл report) M1cte ↔ M1a — нийт, difficulty=hard, tag cte/ratio, domain cross; McNemar.
3. Oracle: M1a ∪ M1ks ∪ M1g8 ∪ M1m ∪ M1rr (155/224 = 69.2%, V25) ↔ + M1cte — шинээр зөв болсон асуултын тоо (дээд хязгаарын өсөлт).
4. Чуулга: `t2s reselect --from M1rr --to M1ensc --split dev --no-refuse --no-clarify --extra M1ks M1a M1g8 M1m M1cte`
   (M1ens-тэй ижил дараалал, M1cte СҮҮЛД — санал тэнцвэл `--extra`-ийн дарааллаар, суурь хамгийн сүүлд: M1ks > M1a > M1g8 >
   M1m > M1cte > суурь M1rr); `M1ensc − M1ens`-ийг McNemar-аар.
   Дүрэмтэй хувилбар: `--to M1enscr` (`--no-refuse --no-clarify`-гүй) ↔ M1ensr.
5. Шийдвэр (dev-ээр, урьдчилан тогтоосон): M1ensc ≥ M1ens + 2 бөгөөд алдагдал ≤ ахиц/2 бол чуулгад оруулна; үгүй бол
   хасна. `--extra-weight`-ийг dev дээр тааруулахгүй (хэт тохируулах эрсдэл).
6. **Test — зөвхөн нэг удаа, хамгийн сүүлд:** dev-ээр сонгосон эцсийн чуулгыг (гишүүд, дараалал dev-тэй ЯГ ижил) test дээр
   нэг удаа ажиллуулж мэдээлнэ; test-ийн үр дүнгээр юу ч өөрчлөхгүй.

## M1th (table hints)

**Асуудал.** dev-д gold хүснэгт prompt-д 97% байсан ч буруу хариултын 13–22% нь төстэй хүснэгтийг андуурсан
(docs/SQL_QUALITY_AND_GAP.md §2b). dev-ийн 20 run-ийн буруу хариулт (хариулах боломжтой, parse хийгдсэн 3024 мөр;
CTE-ийн түр нэрийг хассан) дотор gold-д байгаа хүснэгтийг орхиж өөр хүснэгт хэрэглэсэн 609 мөр байна. Хамгийн их
андуурал (gold → таамаг): loan_writeoffs → loan_payments 61, loans → loan_applications 58, loan_schedule → loan_payments 40,
deposits → accounts 39, loans → loan_payments 34, loan_payments → loans 24, loan_writeoffs → loans 19,
deposits → collaterals 18, loans → accounts 18, loans → exchange_rates 17, loan_dpd_history → loans 17.
Хамгийн их орхигдсон шүүлт: loan_payments `status = 'POSTED'` (188), `currency_code = 'MNT'` (143),
`disbursement_date IS NOT NULL` (99), `status = 'ACTIVE'` (93), loan_applications-ийн шийдвэрийн төлөв (49+43).

**Арга.** `knowledge/table_hints.yaml` — 14 андуурагддаг хос + 2 шүүлтийн сануулга (loan_payments POSTED, loans
is_active_portfolio / PENDING_DISBURSEMENT), англиар. Хосыг dev-ийн давтамжаар сонгосон, харин агуулгыг ЗӨВХӨН
semantic_model.yaml-ийн хүснэгт/баганын тайлбар, enum утгаас бичсэн (dev-ийн gold SQL-ээс биш; тест нь snake_case нэр
бүрийг semantic model-оос шалгана). `table_hints: true` үед (`knowledge/table_hints.py`) зөвлөмжийн бүх хүснэгт prompt-д,
ядаж нэг нь linked_tables-д байвал сонгоно; хоёр хүснэгт хоёулаа linked бол түрүүнд, дараа нь linked-ийн эрэмбээр,
≤ 5 мөр evidence-д (утгын мөрийн дараа) нэмнэ. Prompt +~5 мөр (~250 токен). Анхдагч `false` үед код огт дуудагдахгүй,
opts_hash хэвээр (tests/test_table_hints.py). Тохиргоо: `M1th: {base: M1a, table_hints: true}`.

**Офлайн хамралт (dev, зөвхөн coverage — EX-ийн дүгнэлт биш).** Хүснэгт андуурсан 609 буруу мөрийн (20 run):
* андуурсан хос нь файлд бий: 350 (57.5%) — cap-гүй дээд хязгаар;
* яг андуурсан хосын зөвлөмж prompt-д орох байсан: **269 (44.2%)**;
* андуурсан хүснэгтийн аль нэгийг дурдсан зөвлөмж орох байсан: 512 (84.1%).
M1a-тай ижил prompt-той H4 / E5h_w3: 10/15 мөрд яг хосын зөвлөмж орно. Хос сонголт dev-ээс гарсан тул хамралт
өөдрөг — test-ийн үр дүнг энэ дүнгээр урьдчилан таамаглахгүй.

**Dev үнэлгээ (GPU; test-д хүрэхгүй):** `t2s eval --run M1th --model $MODEL --split dev --variants mn`
(`M1th:m1_madlad_improved`, run_plan-д бүртгэгдсэн). M1th ↔ M1a: нийт EX, хүснэгтийн андуурлын тоо (дээрх скриптээр),
McNemar. Ганцаараа ахиц бага бол чуулгын шинэ гишүүн (M1ensx-д M1cte/M1dec-тэй хамт СҮҮЛД).

## M1ip (T1 далд нөхцөл), M1enspd (T2 нөхцөлийн түвшний pick), M1alt1/M1alt8 (T9b) — 2026-10-30, бүгд анхдагч унтраалттай

**M1ip (`implied_preds: true`).** `knowledge/table_hints.yaml`-ийн `default_predicates` — хүснэгтийн анхдагч бизнесийн
дүрэм. Нэр дэвшигчийг ЗӨВХӨН `evalset/dev.jsonl`-ийн gold SQL-ээр шалгасан (test-ийн gold-ыг уншаагүй);
`table_hints.predicate_support()` тоолж, tests/test_implied_preds.py YAML-д бичсэн тоог дахин шалгана:

| Хүснэгт | Нөхцөл | dev gold (хүснэгттэй / нөхцөлтэй) | Дүрэм идэвхжих / үүнээс gold-д нөхцөлтэй |
|---|---|---|---|
| loan_payments | `status = 'POSTED'` | 19 / 18 | 18 / 18 (асуултад «posted/successful» гэж хэлээгүй 16) |
| loans | `is_active_portfolio` | 76 / 47 | 46 / 46 (асуултад «active/portfolio» гэж хэлээгүй 14) |
| loans | «disbursed» → `disbursement_date IS NOT NULL` (эсвэл огнооны муж) | 76 / 12 | 10 / 10 |
| deposits | `status = 'ACTIVE'` | 21 / 16 | 16 / 16 (ихэнх асуулт «active» гэж шууд хэлдэг — далд нь 1) |

Хасагдсан (сул дэмжлэг): loan_payments `currency_code = 'MNT'` 5/19 (асуулт MNT-г шууд хэлдэг), collaterals
`status = 'ACTIVE'` 4/5 (бүгд шууд, n бага), accounts `status = 'ACTIVE'` 2/7, loans `currency_code = 'MNT'` 3/76.
skip/keep үгсийг dev-ийн эсрэг жишээнээс (P022 «failed», D002 «withdrawn», L012 «closed», P027 «active-portfolio … disbursed»)
сонгосон тул dev-ийн 100% нарийвчлал өөдрөг. `implied_preds: true` үед linked (эсвэл prompt-ын) хүснэгтэд хамаарах
«Implied condition: …» мөрийг (≤ 4) evidence-д нэмнэ; skip үгийг англи орчуулгаар (translation_cache) шалгана.
M1th-ийн 2 нэг-хүснэгтийн сануулгатай (POSTED, is_active_portfolio) давхцана — M1ip нь M1a дээр (M1th-гүй) хэмжинэ.
Эрсдэл: loans linked байхад (бараг үргэлж) loans-ийн мөр нэмэгдэнэ — текст нь нөхцөлт («questions about current loans …»).
Dev: `t2s eval --run M1ip --model $MODEL --split dev --variants mn` (кэш `m1_madlad_improved`); M1ip ↔ M1a McNemar,
шинээр алдсан асуулт ≤ ахицын хагас бол л чуулгад.

**M1enspd (`reselect --pick --pick-diff`).** `eval/pick.py::diff_clauses()` нь sqlglot-оор хоёр SQL-ийн WHERE/HAVING
нөхцөл (AND-аар задалж, alias-гүй), GROUP BY (`GROUP BY 1` → SELECT-ийн илэрхийлэл), агрегат функц, бодит хүснэгтийн
(CTE-гүй) олонлогийн ялгааг олно. Ялгаатай төрөл 1–2 бол `SYSTEM_DIFF` prompt нь «Differences between the two queries: …»
жагсаалтаар зөвхөн тэр ялгааг асууна; 0 (ялгаа SELECT/ORDER/JOIN ON-д), > 2 эсвэл parse алдаа бол хуучин бүтэн-SQL асуулт.
A/B, B/A дүрэм хэвээр (B/A асуултад ялгааны тал солигдоно). `--pick-diff`-гүй үед зан төлөв яг хэвээр (tests/test_pick.py).
Dev: `t2s reselect --from M1rr --to M1enspd --split dev --no-refuse --no-clarify --extra M1ks M1a M1g8 M1m --pick --pick-diff --pick-model d2`
↔ M1ensp (ижил гишүүд, `--pick-diff`-гүй).

## T9b — олон орчуулгын чуулга (код өөрчлөхгүй: тохиргоо + translation_cache)

Run бүр `translation_cache: <tag>` → `build/translations/<tag>.jsonl`-ийг уншдаг тул өөр орчуулга = шинэ кэш + шинэ run.
MADLAD (`hf_seq2seq`) нь sampling-гүй, зөвхөн `--beams` (анхдагч 4 = `m1_madlad_improved`) тул өөр хувилбар нь beam 1
(greedy) ба beam 8. Тохиргоо бэлэн: `M1alt1` (`m1_madlad_alt_b1`), `M1alt8` (`m1_madlad_alt_b8`), base M1a. Зөвхөн dev:

```bash
# 1) Орчуулга (GPU, T4 ~20 мин тус бүр; V16-ийн adapter = $W/madlad_m1; m1_madlad_improved-тай ижил тохиргоо, зөвхөн --beams)
python scripts/mt/m0_translate.py --backend hf_seq2seq --model google/madlad400-3b-mt --adapter "$W/madlad_m1" \
  --hints lexicon --translit improved --variants mn --beams 1 --tag m1_madlad_alt_b1 --batch 8
python scripts/mt/m0_translate.py --backend hf_seq2seq --model google/madlad400-3b-mt --adapter "$W/madlad_m1" \
  --hints lexicon --translit improved --variants mn --beams 8 --tag m1_madlad_alt_b8 --batch 8
# 2) Хэр өөр вэ (CPU): m1_madlad_improved-ээс ялгаатай орчуулгын тоо — < ~15% бол SQL run-д GPU зарахгүй
python - <<'PY'
import json
L = lambda t: {(r["id"], r["variant"]): r["text"] for r in map(json.loads, open(f"build/translations/{t}.jsonl", encoding="utf-8"))}
base = L("m1_madlad_improved")
for t in ("m1_madlad_alt_b1", "m1_madlad_alt_b8"):
    alt = L(t); print(t, sum(alt[k] != base.get(k) for k in alt), "/", len(alt))
PY
# 3) SQL run (vLLM, M1a-тай ижил; ~1 цаг тус бүр) → чуулга (офлайн)
t2s eval --run M1alt1 --model $MODEL --split dev --variants mn
t2s eval --run M1alt8 --model $MODEL --split dev --variants mn
t2s reselect --from M1rr --to M1ensalt --split dev --no-refuse --no-clarify --extra M1ks M1a M1g8 M1m M1alt1 M1alt8
```
`M1ensalt` нь `experiments.yaml`-д бүртгэлгүй (M1ensx шиг — тохиргоо нь M1ens-тэй ижил, зөвхөн `--extra` нэмэгдэнэ).
Шинэ гишүүдийг СҮҮЛД нэмнэ (санал тэнцвэл M1ks > M1a > M1g8 > M1m > шинэ гишүүд > суурь M1rr — шинэ гишүүд хуучин нэмэлт
гишүүдэд ялагдана, суурь M1rr-ийг ялна). Test-д: dev дээр M1ens-ээс ахисан бол л `--split test --final-test`-ээр
нэг удаа орчуулж (`*_test` кэшийг tens-ийн `t_merge` шиг нэгтгэнэ). M1alt1/M1alt8 нь run_plan_l4.sh-ийн t2s хүснэгтэд
(`MT_T2S_CACHES`) бүртгэгдсэн ч `m1_madlad_alt_b1/b8` кэшийг үүсгэх алхам (MT_STEPS) одоогоор алга — кэш байхгүй бол t2s
«кэш алга» гэж алгасна; M1ensalt-ийн reselect ч run_plan-д алга.

## Kaggle-ийн дараалал (2026-10-29 шөнийн бэлтгэлийн дараа)
| Сесс | Хэзээ | Юу | Шийдвэрийн дүрэм |
|---|---|---|---|
| V26 (`kaggle_overrides_next.env`) | 10-31-ээс | `tens` (test: M1r, M1ens, M1ensr — НЭГ удаа) → `ens pick` (dev: M1ensp) | Test-ийн дүнг McNemar-аар тайлагнана |
| V27 (`kaggle_overrides_v27.env`) | V26-ийн дараа | dev t2s `MT_T2S`-ийн эрэмбээр (M1ip → M1fs → M1vf → M1rt → M1cte → M1th → M1dec; run бүр `timeout`-тай — төсвийн төгсгөлөөс `MT_TAIL_MIN`=25 мин өмнө зогсож, дараагийн сесст үргэлжилнэ) → `ensx`: M1ensx0/M1ensxr0 (M1ens/M1ensr-ийн жор ижил кодоор — шинэ суурь), M1ensx/M1ensxr (+ шинэ гишүүд), M1ensra (abstain v2 — dev дээр IN-SAMPLE, шийдвэрт ашиглахгүй) | `python scripts/ingest/ingest_results.py --src <V27 Output> --tag V27`: M1ensx > M1ensx0 (хариулах, McNemar p < 0.05) бол л шинэ гишүүдийг V28-ийн test-д |
| V28 (`kaggle_overrides_v28.env`, 2026-11-01) | V27-ийн дараа, ~7 ц | `g1` (gold-ийн валют, DB) → `lex2` (M1 MADLAD dev, lexicon2) → dev t2s M1lx, M1ksf → `ens pick` (засварласан parse_choice, `pick_votes`) → `ensx` (`MT_ENSX_NEW` = V27-ийн гишүүд + M1lx, M1ksf); test-гүй |
| V29+ (төлөвлөгөө; STAGE=synth хэрэгжээгүй) | дараагийн долоо хоног | synth_own: exec шүүлт (`scripts/synth/exec_validate.py`) → rejection sampling → D2 LoRA дахин сургах (docs/SYNTH_OWN.md) | Dev дээр M1a-аас ахисан бол л; test-ийг хамгийн сүүлд нэг удаа |

Test дээр нэг тохиргоог олон удаа сонгож хэмжихгүй: V26-ийн test нь эцсийн тайлангийн гол тоо. V27/V28-ийн ахиц test-д
зөвхөн нэг нэмэлт хэмжилтээр, «dev-ээр урьдчилан сонгосон» гэж тэмдэглэгдэнэ.

- **Хамгаалалт (2026-10-31, `run_plan_l4.sh`):** `tens`/`test` алхам нь override-д `ALLOW_TEST=1` тодорхой заагаагүй бол
  STEPS-ээс хасагдана (V26-ийн эцсийн test-ийг давтан хэмжихгүй; дуусаагүй V26-г үргэлжлүүлэх бол санаатайгаар нэмнэ).
  `config/kaggle_overrides.env`-ийн `KO_UNTIL` (UTC) өнгөрсөн бол notebook-ийн суурилуулсан (хуучин) STAGE-ээр ажиллахгүй —
  exit 2; `FORCE_NOTEBOOK_STAGE=1` бол notebook-ийн STAGE-ээр үргэлжилнэ.
- **V28-д нэмэх (квот үлдвэл):** hidden-state probe (`docs/PROBE_PLAN.md`). Dev-ийн 300 асуултыг T4 дээр ~2–3 цаг. Хүлээн авах нөхцөл: dev CV-д дүрэм+v2 (160.9)-өөс давж, 10 давталтын ≥ 8-д нь ялах.
- **Хугацаа (`docs/ADAPTIVE_SAMPLING.md`):** `beta:0.8:w4` нь хувилбарын тоог 21% бууруулж, EX өөрчлөгдөөгүй. p95-ыг засварыг зэрэг ажиллуулах (`repair_workers`) замаар бууруулна.

## M1rt — тасарсан reasoning-ийг аврах (R7 #5, budget forcing; 2026-10-30, анхдагч унтраалттай, зөвхөн dev)

H4-д 52/300 (17%) гаралт `max_tokens = 3072`-д тасарч, зөв хувь нь 29% (богино гаралтынх 39%). `rescue_truncated: true`
нь `finish_reason = length` бөгөөд SQL задлагдахгүй гаралтыг `</think><answer>```sql` prefill-ээр нэг удаа (t = 0, ≤ 512
токен) үргэлжлүүлнэ. Угтвар prefix cache-д байгаа тул нэмэлт зардал бага. Хүлээгдэх нөлөө +0–3 хувь EX (таамаг). Ажиллуулах:
`t2s eval --run M1rt --model $MODEL --split dev --variants mn` → M1a-тай McNemar; jsonl-ийн `rescued` талбараар аврагдсан мөрийг тоолно.
`max_tokens 1536` (R7 #4)-тай хослуулах бол шинэ `--tag` ашиглана (max_tokens нь hash-д ордоггүй; runner одоо зөрүүг анхааруулна).

## M1fs — хүснэгт мэдэх few-shot сонголт (R5 З1; 2026-10-30, анхдагч унтраалттай, зөвхөн dev)

`fewshot_rerank: table_jaccard` нь жишээний pool 20-ийг `0.5·асуултын оноо + 0.5·Jaccard(жишээний SQL-ийн хүснэгт,
linked_tables[:3])` гэж дахин эрэмбэлнэ. linked-ийн эхний 3-т байхгүй андуурагддаг хамтрагчийг (`knowledge/table_hints.yaml`-ийн
2 хүснэгттэй мөр) агуулсан жишээг 0.05-аар торгоно (`retrieval/fewshot_rerank.py`; `ExampleIndex.search(table_rerank=...)`).
Нэмэлт LLM дуудлагагүй. Офлайн шалгалт (`scripts/rerank/fewshot_cover.py`, BM25, H4-ийн linked, build/index 160, dev 224):

| Сонголт | Cover (утга = run-ийн retrieval) | Cover (утга = R5-тай ижил) | Хамтрагч жишээнд |
|---|---|---|---|
| Бүртгэгдсэн H4 (hybrid) | 67.4% | 67.4% | 42.9% |
| BM25 top-4 | 65.2% | 70.5% | 41.1 / 37.1% |
| + Jaccard (торгуульгүй) | 74.1% | 78.1% | 45.1 / 42.0% |
| **+ Jaccard + торгууль 0.05 (M1fs)** | **74.1%** | **77.2%** | **39.7 / 38.4%** |
| Oracle (gold хүснэгт) | 81.7% | 82.1% | 25.0 / 21.0% |

R5-ын 67 → 78% давтагдсан. Торгууль 0.2 бол cover −3.5 пп тул 0.05 сонгосон (хамтрагч −3.6…−5.4 пп, cover −0…0.9 пп).
Хүлээгдэх нөлөө +2…+5 асуулт (таамаг). V27-д `MT_T2S`-ийн 2-т (M1ip-ийн дараа; доорх «V27»), ensx-д шинэ гишүүн. Шийдвэр: M1a-тай McNemar (dev).

## M1tc — орчуулгын дараах шалгалт → bilingual fallback (R1.2; 2026-10-30, анхдагч унтраалттай, зөвхөн dev)
`pipeline/translate_check.translation_check(source, text, values=None, keys=None)` (цэвэр функц) нь англи орчуулгыг тэмдэглэнэ:
`placeholder_left` ([V1] үлдсэн), `cyrillic_left` (хашилтаас гадуур кирилл), `cyrillic_quoted` (хашилтад орсон кирилл нь эх
асуултын ишлэл / DB-ийн утга биш, эсвэл 2-р өгүүлбэрийн эхний үг — «байсан», «Сарын», «Мөрөөр»), `number_missing` («5 сая» =
5000000, «2025 оны 3-р сар» ↔ March 2025, «1-р улирал» ↔ Q1/first quarter), `number_added`, `value_lost` (бизнес түлхүүр, код,
эх ишлэл; `unmask`-ийн төгсгөлд нэмсэн `("…")`). `translate_check: bilingual` (M1tc = M1a + энэ) нь тэмдэглэсэн асуултад л
`translate_mode`-ийг bilingual болгоно; `retrieval`-д `translate_check` шалтгаан бичигдэнэ. DB-ийн утгыг утгын индексээс авна.

Офлайн (`PYTHONIOENCODING=utf-8 python scripts/translate/check_report.py`, dev, хариулах 224; Qwen кэш — MADLAD M1-ийн кэш локалд алга):

| Кэш / run | Тэмдэглэсэн (mn) | EX тэмдэглэсэн | EX тэмдэглээгүй |
|---|---|---|---|
| qwen3.5-9b ↔ H4 (en_prompt) | 63/300 (21%; cyrillic_quoted 58, placeholder 5) | 11/52 (21.2%) | 71/172 (41.3%) |
| qwen3.5-9b ↔ H2 (bilingual) | ижил | 10/52 (19.2%) | 75/172 (43.6%) |
| qwen3.5-9b ↔ E5h_w3 | ижил | 17/52 (32.7%) | 91/172 (52.9%) |
| qwen3.5-9b ↔ I3 → I3t | ижил | 14/52 → **21/52** | 83/172 → 84/172 |
| qwen3.5-9b.fix | 0/300 (65 нь кэшэд аль хэдийн H2) | — | — |

Latin хувилбар (run-д ашиглаагүй): 70/300 (placeholder 57, number_added 10, number_missing 4 — бүгд бодит гажуудал).

- Шалгалт нь хэцүү/муу орчуулгыг сайн ялгадаг (EX 21% vs 41%), тоо Qwen-ий кирилл орчуулгад алдагдаагүй (0/300).
- **Зөвхөн bilingual fallback ахиц өгөхгүй:** H4 + тэмдэглэсэнд H2-ийн симуляци 82 → 81 (+2/−3). I3 → I3t-ийн +7 нь хуурамч
  маскыг англи текстээс ЗАССАНААС (translate_fix) — монгол мөр нэмснээс биш. Тиймээс Qwen кэшэд M1tc хэрэггүй (I3t аль хэдийн хийсэн).
- MADLAD M1 (M1a) дээр хүчинтэй эсэхийг GPU-гээр л батална: эхлээд m1_madlad_improved кэшийг Kaggle-аас татаж энэ скриптэд
  нэмэх (тэмдэглэсэн хувь < 5% бол SQL run хийхгүй). Шалгалтыг abstain/clarify-ийн шинж болгох нь илүү ирээдүйтэй байж болно.
- R6-ийн «төгрөгийн → MNT», «хаагдаагүй кейс» дүрмийг table_hints.yaml-д НЭМЭЭГҮЙ (dev-ийн дэмжлэгийг тоолоогүй).

### Review (2026-10-30) — нэмэлт тэмдэглэл
- M1ip-ийн дүрэм linked хүснэгт (FK хөрш орно) бүрт идэвхждэг: dev prompt-д `is_active_portfolio` 233/300 (gold-д loans 50),
  `POSTED` 103 (gold 18), `deposits ACTIVE` 43 (gold 16). YAML-ийн dev_support (46/46 г.м.) нь зөвхөн тухайн хүснэгтийг
  ашигласан gold-оор тоологдсон тул бодит precision-ийг хэтрүүлнэ. M1ip-г зөвхөн dev McNemar-аар шүүнэ; муу бол
  `linked_tables[:3]` (FK хөршгүй) хувилбарыг туршина.
- M1rt-ийн өдөөгч `</think>` хүрээгүй тасарсан гаралт болж засагдав (extract_sql reasoning доторх «with/select»-ийг SQL гэж
  барьдаг байсан). M1rt ансамбльд (ensx) орохгүй — M1a-ийн орлуулагч.
- V27-ийн t2s эрэмбэ: M1ip → M1fs → M1rt → M1cte → M1th → M1dec.
- V26-аас өмнө `kaggle_overrides_next.env`-ийг `kaggle_overrides.env` болгож ЗААВАЛ хуулна (одоогийнх V23-ийн агуулгатай).
  → **Хийгдсэн (2026-10-31)**, V26 эхэлсэн (WORKLOG 2026-10-31).

## M1vf — хайлтын засвар (FULL_REVIEW 2026-10-30: F1, F3, F5 + L6, C8; F2 → V28) — 2026-10-30, бүгд анхдагч унтраалттай, зөвхөн dev
V26 (эцсийн test) нь системийг сонгосон dev run-уудтай ижил кодоор явах ёстой тул засвар бүр **шинэ сонголт**, анхдагч нь
хуучин зан төлөв (`_LATE_DEFAULTS` → хуучин fingerprint/opts_hash хэвээр). M1vf = M1a + F1 + F3 + F5 (V27, dev).

| Сонголт | Анхдагч | Засвар | Хаана |
|---|---|---|---|
| `value_match` (F1) | `legacy` | `strict`: кирилл түлхүүрийг ЗӨВХӨН кириллээр — яг (1.0), дагавар хассан хэлбэр = утга (`strip_suffix`/`stem_variants`, 0.95), утгын эхний үг(үүд) (угтвар, хэлбэр нь утгын ≥60%, 0.9), бусад нь кирилл ratio ≥ 0.85; сул латин түлхүүр зөвхөн латин/холимог түлхүүрт, хэсэгчилсэн тааралт зөвхөн угтвараар; linking-ийн өсгөлт (0.7) зөвхөн exact/stem/prefix-д | `retrieval/indexes.strict_match`, `Retriever.retrieve` |
| `mask_mode` (F3) | `legacy` | `values`: few-shot хайлтад зөвхөн утгын тааралт, DB-ийн оноосон нэр (салбар/дүүрэг/аймаг…, дагавар ≤5), regex (бизнес түлхүүр, код, огноо, ишлэл)-ийг маскжуулна; эхний үгийг зөвхөн бодит утга бол; embedding-ийн асуулт ч ижил маскаар (жишээний санг маскжуулсан асуултаар encode хийдэг) | `indexes.mask_values` |
| `enum_codes` (F5) | `first9` | `all`: DDL-ийн тайлбарт enum-ийн бүх код | `prompts.render_ddl(enum_limit=None)` |
| `translit_vocab` (L6) | `pool` | `no_eval`: галиг хувиргагчийн үгийн сангаас жишээний санд орсон dev асуултыг хасна (M1vf-д ОРООГҮЙ) | `Retriever.translits` |
| C8 | — | M1ks-ийн хуваарилагчийн уншсан хариу `retrieval.select_raw` (≤1000 тэмдэгт) бичлэгт; сонголтын дүрэм хэвээр | `orchestrator._select_glossary` |
| F2 | `--hints lexicon` | `--hints lexicon2` (орчуулагч, V28) | `translate_terms.term_hints(mode=)` |

L6-г шууд засаагүй (сонголт болгосон): dev-ийн 300 кирилл асуулт бүгд `script_of = cyrillic` тул prompt-д нөлөөгүй ч test-ийн
холимог бичигтэй асуултыг (test-ийг уншихгүй) шалгах боломжгүй — V26-ийн test prompt өөрчлөгдөх эрсдэлтэй.

**Анхдагч хэвээр байгааг шалгасан:** 36 одоогийн тохиргоо × dev 300 (+ A3/M1a/I3/F2-ийн латин) = 12 000 prompt + retrieval
мэдээллийн hash засварын өмнө/дараа (локал BM25 индекс, Qwen кэш) — prompt **0 зөрүү**; retrieval мэдээлэлд зөвхөн M1ks-ийн шинэ
`select_raw` түлхүүр (түүнийг хасвал 300/300 ижил); `tests/test_d3.py`-ийн тогтмол fingerprint;
хадгалсан 21 `results/runs/*/*/*.meta.json`-оос 13 нь `resolve_run`-ийн fingerprint ба opts_hash-тай яг таарсан, 8 нь (`__tr`
tag, reselect-ээс гаргасан E4/E4f, experiments.yaml-д байхгүй E4c/E5h_*) өмнөх override-оор ялгаатай — шинэ талбар оролцоогүй; 21-ийн
хадгалсан options бүгд `RunOptions(**options).fingerprint()`-оор яг давтагдана. M1vf-ийн fingerprint давхардалгүй (өмнөх давхардал:
M1r = M1ensr). `lexicon` (хуучин) мөр HEAD-тэй ижил PYTHONHASHSEED бүрт яг ижил. Meta-ийн `prompt_version` (prompts.py-ийн sha1,
зөвхөн мэдээлэл, opts_hash-д ордоггүй) өөрчлөгдөнө — gen_style г.м. өмнөх засваруудаар ч аль хэдийн өөрчлөгдсөн.

### Офлайн dev хэмжилт
`PYTHONIOENCODING=utf-8 PYTHONHASHSEED=0 python scripts/rerank/vf_offline.py` — ЗӨВХӨН dev, BM25 (Kaggle дээрх bge-m3-ын хагас
ороогүй), утга/схем `build/index` (D2-ийн бүртгэсэн утгатай 300/300 таардаг), жишээний сан = examples + examples_plus + dev
(579, index_plus_dev-тэй ижил; өөрийн family хасна), орчуулга `qwen3.5-9b.fix` (en_prompt-д хайлт монголоор тул нөлөөгүй).

**F1 — «Stored value '…' exists in t.c» мөр (dev 300):**

| | legacy | strict |
|---|---|---|
| мөртэй асуулт | 208 | 175 |
| нийт мөр | 488 | 391 |
| хуурамч мөр (strict тэр түлхүүр–утгын хосыг огт хүлээн авахгүй) | 113 мөр, 87 асуулт | 0 |
| — үүнээс эхний үг огт өөр (байсан→Баасан, хувьд→Ховд …; шалгалтын «51/300»-тэй ижил төрөл) | 73 мөр, 60 асуулт | 0 |
| gold SQL-ийн литерал (эсвэл dev-ийн `values`)-аар баталгаажсан мөр | 19 (14 асуулт) | 20 (15 асуулт) |

- Хасагдсан хуурамч хос (давтамж): байсан→Баасан 23, зээлийн тоо→«Зээлийн түүх муу» 9, өгөөч→Тогооч 9, салбарын нэр→«Салбарын
  менежер» 8, хадгаламж→«Хадгаламж барьцаа» 7, өөрөө→Мөрөн 6, тоогоор→Тогоо 5, хувьд→Ховд 4, дархан→Дархан-Уул 4, хууль→Хулан 3,
  өнөөдөр→Өндөр 2, хан-уул→Дархан-Уул 2, бүлэгт→BULLET 2, хүссэн→HOUSE, CC-2026-000312→2026-03 …
- Баталгаажсан утга **алдагдсан 0**, нэмэгдсэн 1 (A032 «цалингийн зээлээр» → «Цалингийн зээл — VIP»); strict-ийн шинэ 16 мөр
  (VIP-ийн→VIP, Төвд→Төв, Ховдын→Ховд, Хан-Уулын→Хан-Уул …). Үлдсэн «хадгаламжийн → Хадгаламжийн журнал» г.м. нь үгийн жинхэнэ
  угтвар (лексик давхцал) — string тааралтаар шийдэхгүй.
- linked_tables 49/300 асуултад өөрчлөгдсөн; gold хүснэгт бүгд linked-д: 220/224 → 220/224 (хэвээр). M1a schema linking-гүй тул
  linked нь зөвхөн тайлбарын эрэмбэ/лог — F1 ганцаараа 92/300 prompt-ыг зөвхөн evidence мөрөөр өөрчилнө (DDL ижил).

**F3 — few-shot (хариулах 224, k=4, pool 20, BM25):**

| Маск | Эхний үг маскжсан (300) | gold хүснэгтийн cover | union |
|---|---|---|---|
| legacy (`_quoted`) | 209 | 161 (71.9%) | 179 (79.9%) |
| values (legacy утгын тааралт) | 48 (бодит утга: Батлагдсан, Ховдод …) | 171 (76.3%) | 187 (83.5%) |
| values + strict (M1vf) | 48 | 171 (76.3%) | 187 (83.5%) |

Жишээний багц 106/224 (prompt 144/300) асуултад өөрчлөгдсөн. Kaggle-ийн hybrid (BM25 + bge-m3)-д embedding-ийн маскын нөлөө нэмэгдэнэ.

**F5 — enum:** эхний 9-өөр тасардаг 3 багана: `gl_entries.account_code` (20 код; 2120…5400 хүртэл 11 нь алга), `products.product_type_code`
(CURRENT_ACC), `transactions.transaction_type_code` (REVERSAL). Dev-ийн **8** gold SQL тасарсан дансны кодыг (4100/4200/4300/5100/5200/5400)
ашигладаг. M1a бүх хүснэгтийг тайлбартай өгдөг тул `enum_codes: all` нь **300/300** prompt-ыг яг эдгээр 3 тайлбарын мөрөөр л өөрчилнө
(дундаж ≈ +107 токен, 13 343 → 13 450 тооцоолсон, +0.8%).

**M1a ↔ M1vf prompt:** 300/300 өөрчлөгдсөн (F5); тус тусдаа F1 92, F3 144, F5 300. Хүлээгдэх нөлөө (таамаг): F1 хуурамч утга/хүснэгт
(байсан→Баасан → employees) ба F3 few-shot cover +4.4 пп-ээс +2…+5 асуулт; F5 G-домэйны 8 асуултад. Шийдвэр: M1vf ↔ M1a McNemar (dev,
нийт ба хариулах 224); ялгаа бага бол ensx-ийн шинэ гишүүн. V28-д хугацаа үлдвэл F5-гүй хувилбар (300 prompt-ыг өөрчилдөг тул).

### V27
`run_plan_l4.sh` t2s жагсаалт ба `kaggle_overrides_v27.env` `MT_T2S`: M1ip → M1fs → **M1vf** → M1rt → M1cte → M1th → M1dec; ensx-ийн
шинэ гишүүнд M1vf (сүүлд). `MT_BUDGET_MIN=650`, `MT_RUN_MIN=115` (~110 мин/run) тул M1th/M1dec алгасагдаж магадгүй.
t2s нь run-уудыг `MT_T2S`-ийн эрэмбээр ажиллуулна (≥ `MT_RUN_MIN` мин үлдсэн үед л эхлүүлнэ); run бүр `timeout`-тай — төсвийн
төгсгөлөөс `MT_TAIL_MIN` (25) мин өмнө зогсоно (< 10 мин үлдвэл эхлүүлэхгүй), дууссан асуулт хадгалагдаж дараагийн сесст
үргэлжилнэ. `ensx` (загваргүй, CPU reselect): **M1ensx0 / M1ensxr0** = M1ens / M1ensr-ийн жор (суурь M1rr + M1ks M1a M1g8 M1m)
ЭНЭ сессийн кодоор — шинэ суурь (V26-ийн сэргээсэн dev M1ens/M1ensr-ийг дарж бичихгүй); **M1ensx / M1ensxr** = + шинэ гишүүд
(`--extra`-ийн төгсгөлд, ≥ 300 мөртэй бүрэн run л; M1rt ороогүй); **M1ensra** = M1ensr + Abstain v2 — dev дээр IN-SAMPLE,
шийдвэрт ашиглахгүй. Дүрэмтэй хувилбарууд ба M1ensra нь төсвөөс `MT_ENSX_GRACE` (20) мин-ээс илүү хэтэрсэн бол алгасагдана.
Шийдвэр: `python scripts/ingest/ingest_results.py --src <V27 Output> --tag V27` — M1ensx нь M1ensx0-оос (хариулах, McNemar
p < 0.05) ахисан бол шинэ гишүүдийг V28-ийн test-д нэмнэ; үгүй бол M1ens хэвээр.

**V27-ийн дүн (2026-11-01, Kaggle Version 28, 9.5 ц; docs/RESULTS_V27.md):** M1ip 129, M1fs 124, **M1vf 132**, M1rt 129 /224
(M1a 127; McNemar хариулах: +13/−11, +19/−22, +21/−16, +2/−0 — аль нь ч ач холбогдолгүй). M1cte / M1th / M1dec төсвөөс хоцорч
алгасагдсан (98 мин < `MT_RUN_MIN` 115). **M1ensx** (M1ens + M1ip M1fs M1vf) 142 vs M1ensx0 137 /224: +8/−3, p = 0.227 →
**батлагдаагүй**; урьдчилан тогтоосон дүрмээр шинэ гишүүдийг test-д нэмэхгүй, M1ens хэвээр. M1ensx0 = M1ens (V26) — 0 ялгаа
(кодын хувилбар хоорондын давтагдал). Дүрэмтэй: M1ensxr 201/300 vs M1ensxr0 197/300. M1ensx-ийн нэгдэл (pass@k) 170/224.

**V28-ийн ensx (2026-11-01; docs/RESULTS_V28.md):** M1ensx (+ M1ip M1fs M1vf **M1lx M1afx**) 145 vs M1ensx0 137 /224, +10/−2,
p = 0.0386 (нийт 183 vs 176, +10/−3, p = 0.092). **Test-д аваачихгүй:** ижил дүрмийн 2 дахь харалт (V27 p = 0.227; 2 харалтын
Bonferroni 0.077), M1afx-ийн засвар dev-ийн алдаанаас (in-sample), V28-ийн мөр «test-гүй», 5 шинэ test үүсгэлт (~8–9 ц) квотод
багтахгүй. Засварын хэсгийг V29-ийн `afx` (CPU, урьдчилан бүртгэсэн) шалгана. M1ensE2-ийн dev → test (+14/−0 → +9/−9) нь dev-ээр
сонгосон чуулгын ахиц test-д шилжихгүй байж болохыг харуулсан.

### F2 — орчуулагчийн нэр томьёоны зөвлөмж (`--hints lexicon2`, V28; V27-д ОРОХГҮЙ)
`lexicon` (M1-ийн сургалт ба кэш) нь (1) угийн тааралтдаа «-гүй»-г тооцдоггүй (барьцаагүй → «барьцааны = collateral», Идэвхгүй харилцагч →
«идэвхтэй харилцагч = Active customer»), (2) ижил угтай эсрэг нэр томьёог нэгтгэдэг («хугацаатай = demand … / term …» — «хугацаа»
агуулсан бүх асуултад), (3) хэллэгийн тоог хасдаг («180-аас дээш» → «аас дээш» → «30-аас дээш»-д DPD_180_PLUS). lexicon2: тоо яг
тэнцүү, үгийн туйл (-гүй / -тай,-тэй,-той / үгүй) бүлэглэлт ба тааралтад (туйлгүй нэр томьёо «-тай» үгэнд таарна: барьцаатай → collateral),
тодорхой эрэмбэ. Dev 300 (mn, PYTHONHASHSEED=0):

| | |
|---|---|
| зөвлөмж өөрчлөгдсөн асуулт | 77 (мөрийн олонлог өөр 50; seed бүрт 71) |
| төөрөгдүүлэх зөвлөмж хасагдсан | **38** (эсрэгийг нэгтгэсэн 22, «-гүй»-г үл тоосон 13, тоо хассан 7) |
| `lexicon`-ийн тогтворгүй байдал | 8 асуултын зөвлөмж PYTHONHASHSEED-ээс хамаарна (set-ийн давталт); lexicon2 8 seed-д ижил |

V28 төлөвлөгөө: (1) dev-ийг дахин орчуулах — `python scripts/mt/m0_translate.py --backend hf_seq2seq --model google/madlad400-3b-mt
--adapter "$W/madlad_m1" --hints lexicon2 --translit improved --tag m1_madlad_lex2 --batch 8` (M1a-ийн кэштэй ижил тохиргоо, зөвхөн
hints); `baseline_dev.py`-аар chrF++/нэр томьёо харьцуулах; (2) dev run `M1lx` = M1a + `translation_cache: m1_madlad_lex2`
(experiments.yaml-д бүртгэгдсэн), M1a-тай McNemar. M1 MADLAD-ийг `lexicon`-оор сургасан тул зөвлөмжийн агуулга (хэлбэр биш) л өөрчлөгдөнө.
Kaggle-д (2026-10-31-ээс бэлэн): `STAGE=mt`-ийн `lex2` алхам (1)-ийг `--split dev`-ээр хийж `mt_score`-оор оноолно — зөвхөн
`MT_STEPS`-д `lex2` тодорхой байхад, `m1_madlad_lex2` кэш алга, `madlad_m1` adapter бий, төсвөөс ≥ `MT_LEX2_MIN` (60) мин
үлдсэн үед; MADLAD-ийн OOM-оос сэргийлж эхлээд vLLM-ийг зогсооно. Дараа нь `MT_T2S`-д `M1lx` (кэш `m1_madlad_lex2`).

## M1ensE2 — урьдчилан бүртгэл (2026-11-01, test-ээс ӨМНӨ)

**Эх үүсвэр:** зөвхөн dev-ийн шинжилгээ (300 асуулт; test-ийн файлыг уншаагүй; scratchpad/dev_analysis). M1ens-ийн 5 гишүүний
оракл 155/224 ба M1ens 137/224-ийн зөрүүг судлахад E2 (H4 + 8 хувилбарын санал, h_H4, seed 1) гишүүдтэй ердөө 37–39%-д санал
нийлдэг тул шинэ мэдээлэл өгч оракл 172 (I3t-тэй 175) болгодог.

**Тохиргоо (өөрчлөхгүй):** `t2s reselect --from E2 --tag h_H4 --to M1ensE2 --no-refuse --no-clarify --extra M1ks M1a M1g8 M1m
M1rr I3t --extra-weight 2` (vote_filter анхдагчаар асаалттай; гишүүн бүр ×2, E2-ийн дээж бүр ×1 = ω 0.5). run_plan-ийн `ense2` алхам.

**Dev нотолгоо:** 5-fold-ийн family-group CV (20 давталт): хариулах 149.7/224 (M1ens 137; +13/−0, p = 2.4·10⁻⁴; ×25 Bonferroni ≈ 0.006),
нийт 184.7/300 (+8.7; p = 0.064). Яг загварчлал (in-sample): 185/300, 150/224. Bootstrap 95%: Δхариулах +5.8 хувь [+3.1, +8.9].

**Test-ийн дүрэм:** V28-д test дээр НЭГ удаа (CPU reselect: test-ийн E2, I3t ба V26-ийн гишүүд). Гол дүн (урьдчилан зарласан) нь
M1ens хэвээр; M1ensE2-ийг «dev-ээр сонгож, test-ээс өмнө бүртгэсэн хоёрдогч тохиргоо» гэж тайлагнана; M1ens → M1ensE2 хариулах
асуултын McNemar. Сөрөг/ач холбогдолгүй гарсан ч мэдээлнэ. Анхааруулга: E2-ийн 20 SQL `%q`-тэй тул reselect дахин ажиллуулахад
улирлын засвартай гүйцэтгэгдэнэ (загварчлалаас бага зэрэг зөрж болно).

**V28-ийн дүн (2026-11-01, Kaggle Version 29; docs/RESULTS_V28_test.md): батлагдаагүй.** Dev 186/300, хариулах 151/224 (+14/−0,
p = 1.2·10⁻⁴ — dev-ээр сонгосон тул өөдрөг). **Test 109/200 (54.5%), хариулах 90/151 (59.6%) — M1ens 110/200, 90/151; хариулах
+9/−9, p = 1; нийт +9/−10, p = 1.** Dev-ийн CV-ийн ахиц test-д шилжээгүй; гол дүн M1ens хэвээр.

## M1ensE2r — урьдчилан бүртгэл (2026-11-01 ~14:50 УБ; V28 ажиллаж байх үед, M1ensE2-ийн test-ийн дүнгээс ӨМНӨ)

**Яагаад:** зорилтын хэмжүүр нь test-ийн **нийт** EX (хариулах + боломжгүй + хоёрдмол). M1ensE2 нь `--no-refuse --no-clarify`
тул боломжгүй/хоёрдмол асуултад оноо авахгүй. M1ens → M1ensr-тэй адил дүрмийн давхаргыг (rule_refuse / rule_clarify, `is_fromless`)
нэмэхэд санал хураалт өөрчлөгдөхгүй (dev: M1ensx ба M1ensxr хоёулаа хариулах 142), зөвхөн төлөв өөрчлөгдөнө. V26-ийн test-д
M1ens 110 → M1ensr 120/200 болсон.

**Тохиргоо (өөрчлөхгүй):** `t2s reselect --from E2 --tag h_H4 --to M1ensE2r --extra M1ks M1a M1g8 M1m M1rr I3t --extra-weight 2`
(M1ensE2-тэй ижил, зөвхөн `--no-refuse --no-clarify`-гүй). run_plan-ийн `ense2` алхам (V29-өөс; бүрэн гаралттай M1ensE2-ийг дахин
тооцохгүй). Test дээр НЭГ удаа.

**Тайлагнах дүрэм:** хоёрдогч тохиргоо (гол дүн нь M1ens хэвээр). Харьцуулалт: M1ensr → M1ensE2r (нийт 200, McNemar) ба M1ensE2 →
M1ensE2r (зөвхөн боломжгүй/хоёрдмол асуултад өөрчлөгдөнө). **Анхааруулга:** дүрмийн түлхүүр үгсийг dev дээр боловсруулсан тул dev-ийн
дүрэмтэй тоо in-sample; test дээрх тоо л шударга. Сөрөг/ач холбогдолгүй гарсан ч мэдээлнэ.

**V29-ийн дүн (2026-11-02, Kaggle Version 30; docs/RESULTS_V29.md):** dev 209/300 (хариулах 151); **test 121/200 (60.5%),
хариулах 88/151** — M1ensr → M1ensE2r нийт +9/−8, p = 1 (хариулах +9/−8); M1ensE2 → M1ensE2r нийт +14/−2 (зөвхөн
боломжгүй/хоёрдмол асуултын төлөв). Батлагдаагүй.

**V27-ийн гишүүдийг M1ensE2-д нэмэх үү (2026-11-01, зөвхөн dev, scratchpad/dev_analysis_v27):** үгүй. Төгсгөлд жин 2-оор:
+M1ip 149, +M1fs 151, +M1vf 152 (+3/−1, p 0.63), +M1rt 147, +гурав 149, +бүгд 150 (M1ensE2 150/224, in-sample); сонголтын family
CV 149.70 (−0.30). Шинэ run-ууд M1a-ийн хувилбар тул E2-ийн саналын эсрэг M1a-ийн блокийг л хүчирхэгжүүлнэ. Чуулгын алдагдал
бараг бүгд «зөв хариу цөөнх саналтай» (M1ensx: 28-аас 27, шүүлтээр 0) — сонголтын механизм (pick/verifier) нь дараагийн нөөц.

## R7 — гаралтын DATE_TRUNC → DATE (postproc; урьдчилан бүртгэл 2026-11-01 ~16:00 УБ, V28-ийн test/tzfix-ийн дүнгээс ӨМНӨ)

**Эх үүсвэр (зөвхөн dev, scratchpad/err_v27):** PG-д `DATE_TRUNC('month', d)` нь timestamptz буцаадаг; UTC сесст «…00:00+00» нь
`compare.norm_value`-д УБ-ийн 08:00 болж gold-ийн `::date`-тэй таарахгүй. Dev-ийн бүх run-д гаралтдаа DATE_TRUNC-тэй 115 хариултын
**0** нь зөв. Засвар: `RunOptions.trunc_date` (postproc R7, `cast_date_trunc`; анхдагч off, fingerprint өөрчлөгдөхгүй) — зөвхөн
SELECT-ийн гаралтын баганыг `CAST(… AS DATE)` (WHERE/GROUP BY/ORDER BY хэвээр; DISTINCT/UNION-ийг хөндөхгүй). Хадгалсан хувилбарт
GPU-гүй: `t2s reselect … --trunc-date` (guard-аас өмнө, pipeline-тай ижил дараалал). Хүлээгдэх (dev): M1a/M1afx +3 (K036 K040 P040).
Метрикийн тал (T2S_TZ_NAIVE, V28-ийн `tzfix`) ижил асуултыг эргүүлнэ — R7 нь **системийн** засвар (метрик өөрчлөхгүй).

**Тохиргоо (өөрчлөхгүй), run_plan-ийн `r7` алхам (V29):**
- `M1ens_r7` = `t2s reselect --from M1rr --to M1ens_r7 --no-refuse --no-clarify --extra M1ks M1a M1g8 M1m --trunc-date`
  (гол дүнгийн M1ens-тэй ижил, зөвхөн R7);
- `M1ensE2r_r7` = `t2s reselect --from E2 --tag h_H4 --to M1ensE2r_r7 --extra M1ks M1a M1g8 M1m M1rr I3t --extra-weight 2
  --trunc-date` (M1ensE2r + R7 — «бүрэн систем»-ийн нэр дэвшигч).

**Тайлагнах дүрэм:** dev ба test дээр НЭГ удаа; хоёрдогч тохиргоо (гол дүн нь M1ens хэвээр). McNemar: M1ens → M1ens_r7 (R7-ийн
нөлөө), M1ensE2r → M1ensE2r_r7. Сөрөг/ач холбогдолгүй гарсан ч мэдээлнэ.

**V29-ийн дүн:** dev M1ens → M1ens_r7 ба M1ensE2r → M1ensE2r_r7 тус бүр +3/−0 (p = 0.25; K036 K040 P040); **test 0 өөрчлөлт**
(110 → 110, 121 → 121). R7 test-д нөлөөгүй.

## afx — M1afx-ийн 4 засварыг хадгалсан гаралтад (урьдчилан бүртгэл 2026-11-01 ~19:55 УБ; test-ийн ямар ч afx дүнгээс ӨМНӨ)

**Эх үүсвэр (зөвхөн dev):** V27-ийн dev алдааны шинжилгээний 4 засвар (guard_scope, transpile_fix, extract_fallback, literal_fix;
`tests/test_err_fixes.py`). V28 dev: онлайн M1afx (M1a-тай ижил prompt) хариулах 138/224, M1a 127 (V28-ийн логоос; McNemar-ыг
гаралт татсаны дараа). Засварыг dev-ийн алдаанаас гаргасан тул dev-ийн ахиц in-sample — test-ийнх л шударга.

**Арга (GPU-гүй):** `t2s reselect --refix afx` (`eval/reselect.py` `make_refixer`) — гишүүн бүрийн хадгалсан `raw_output`-оос SQL-ийг
pipeline-ийн замаар (extract → SQLite→PG → postproc → guard → exec, extract_fallback) засвартай дахин гаргана; prompt ба үүсгэлт
өөрчлөгдөхгүй тул онлайн M1afx-тай ижил шийдвэр, vLLM-ийн шуугиангүй. runner `raw_output`-ийн сүүлийн 4000 тэмдэгтийг л хадгалдаг
тул засваргүй дахин гаргалт хадгалсан SQL-ийг яг давтсан бичлэгт л засвар хэрэглэнэ, бусдад хадгалсан SQL (+ scoped guard). Офлайн
(DB-гүй) dev шалгалт: 5 гишүүний тасраагүй бүх бичлэг яг давтагдсан (263/263, 255/255, 252/252, 260/260, 261/261); тасархай нь
10–14%; засвар гишүүн бүрт 15–26 SQL-ийг өөрчилнө (exec/хоосон/guard-ийн алдаатай бичлэг голдуу). Онлайн M1afx-ийн test-ийг
GPU квотын улмаас ажиллуулахгүй.

**Тохиргоо (өөрчлөхгүй), run_plan-ийн `afx` алхам (V29):**
- `M1a_fx` = `t2s reselect --from M1a --to M1a_fx --no-refuse --no-clarify --no-filter --refix afx`; суурь нь `M1a_rs` (ижил,
  `--refix`-гүй — хадгалсан SQL-ийг дахин оноолно);
- `M1ensfx` = `t2s reselect --from M1rr --to M1ensfx --no-refuse --no-clarify --extra M1ks M1a M1g8 M1m --refix afx` (M1ens + засвар);
- `M1ensfxr` = ижил, дүрэмтэй (`--no-refuse --no-clarify`-гүй; M1ensr + засвар);
- `M1ensE2rfx_r7` = `t2s reselect --from E2 --tag h_H4 --to M1ensE2rfx_r7 --extra M1ks M1a M1g8 M1m M1rr I3t --extra-weight 2
  --refix afx --trunc-date` (M1ensE2r + засвар + R7 — бүх сайжруулалттай систем; E2-ийн 8 хувилбар хадгалсан хэвээр).

**Тайлагнах дүрэм:** dev ба test дээр НЭГ удаа; хоёрдогч тохиргоо (гол дүн нь M1ens хэвээр). McNemar (хариулах ба нийт): M1a_rs →
M1a_fx (засварын гишүүн дээрх нөлөө — гол асуулт), M1ens → M1ensfx, M1ensr → M1ensfxr, M1ensE2r → M1ensE2rfx_r7; 4 харьцуулалтын
Holm-оор залруулсан p-г мөн мэдээлнэ. Сөрөг/ач холбогдолгүй гарсан ч мэдээлнэ.

**Нэмэлт (2026-11-01 ~21:45 УБ, V29 эхлэхээс өмнө — afx-ийн dev/test дүн хараахан байхгүй):** test-ийн M1ens/M1ensr нь V26-ийн
кодоор тооцогдсон, M1ensfx/M1ensfxr нь V29-ийн кодоор. Кодын шилжилт засварын нөлөөтэй холилдохгүйн тулд `afx` алхамд ижил
жорыг `--refix`-гүй энэ сессийн кодоор тооцсон **ихэр суурь** нэмэв: `M1ens_rs` (V28-ийн tzfix-ийнхтэй ижил тушаал; байвал дахин
тооцохгүй) ба `M1ensr_rs`. Дээрх 4 бүртгэсэн харьцуулалт хэвээр (Holm-ийн бүлэг өөрчлөгдөхгүй); нэмж M1ens_rs → M1ensfx ба
M1ensr_rs → M1ensfxr-ийг (цэвэр засварын нөлөө) мэдээлж, M1ens → M1ens_rs ба M1ensr → M1ensr_rs-ийн асуулт бүрийн зөрүүг (кодын
шилжилт) тоолно. Хоёр харьцуулалт зөрвөл хоёуланг нь мэдээлж, зөрүүний шалтгааныг (асуулт бүрээр) тайлбарлана.

**V29-ийн дүн (2026-11-02; docs/RESULTS_V29.md).** Хариулах асуултын McNemar (Holm, 4 харьцуулалт):

| | dev | test (хариулах) | test (нийт) |
|---|---|---|---|
| M1a_rs → M1a_fx | 127 → 138, +11/−0 (Holm 0.004) | 81 → 87, **+6/−0, p = 0.031** (Holm 0.125) | 101 → 108, +7/−0, p = 0.016 (Holm 0.063) |
| M1ens → M1ensfx | 137 → 146, +9/−0 (Holm 0.012) | 90 → **95**, +5/−0, p = 0.063 (Holm 0.19) | 110 → **116 (58.0%)**, +6/−0, p = 0.031 (Holm 0.094) |
| M1ensr → M1ensfxr | 137 → 146, +9/−0 | 87 → 92, +5/−0, p = 0.063 | 120 → **125 (62.5%)**, +6/−1, p = 0.125 |
| M1ensE2r → M1ensE2rfx_r7 | 151 → 157, +6/−0 | 88 → 90, +2/−0, p = 0.5 | 121 → 123, +3/−1, p = 0.63 |

Засвар бүх 4 харьцуулалтад нэг чиглэлтэй (хариулах асуултад алдагдал 0), нэрлэсэн p < 0.05 нь M1a_fx (хоёр хүрээ) ба M1ensfx
(нийт); Holm-ийн дараа ач холбогдолгүй (хамгийн бага 0.063). Ихэр суурь (ижил код): M1ens ≡ M1ens_rs, M1ensr ≡ M1ensr_rs,
M1a ≡ M1a_rs (dev ба test, асуулт бүрээр) → кодын шилжилт 0. refix: test-д M1a 11, M1ens-ийн суурь 8 SQL өөрчлөгдсөн (тасархай 17/37).

## V27-ийн алдааны шинжилгээ (2026-11-01, зөвхөн dev; scratchpad/err_v27)
- **M1rt:** аврах механизм 300 асуултын нэгд ч идэвхжээгүй — код зөв, нөхцөл нь тохиолддоггүй: max_tokens (3072)-д хүрсэн 36
  гаралт бүгд `</think>`-ийг хаасны ДАРАА давтагдсан (31 нь ижил SQL блокийг 4–21 удаа, 5 нь тайлбарыг). Өмнөх «12–16% тасардаг»
  дүгнэлт буруу: тасралт бус, хариултын дараах давталт (SQL аль хэдийн бий; сүүлийн тасархай блокийг extract_fallback барина).
  M1rt-ийн 3 ялгаа (C011, A041 +; C055 −) нь vLLM-ийн давтамжийн шуугиан. **M1rt-ийг хасна.**
- **Шуугианы түвшин:** ижил prompt-оор 300 гаралтын 29 нь өөр (хариулах асуултын ~1% эргэнэ); prompt-ийн аливаа өөрчлөлт хариулах
  асуултын 16–19%-ийг хоёр тийш эргүүлнэ. 5 run-ийн аль нэгэнд зөв 162, бүгдэд зөв 91. → Ганц run-ий ±5 ялгаа шуугиан; ахицыг
  чуулга/олон seed-ээр л батална.
- **M1fs:** few-shot 202 асуултад өөрчлөгдсөн (+18/−21), ялалт/ялагдлын хооронд ялгаа алга — хадгалах хувилбаргүй.
- **M1vf:** `enum_codes=all` бүх prompt-ыг өөрчилдөг; утгын олдвор өөрчлөгдсөн 91 асуултад +12/−6, бусдад +9/−10. Бодит засвар:
  A032 (strict тааралт), D012 (төөрөгдүүлэх олдвор хасагдсан); K016/L009-ийг literal_fix ч засна. Хувилбар: M1afx +
  `value_match: strict` (~+2; ерөнхий тааралтын алдааг засдаг тул overfit эрсдэл бага).
- **M1ip:** `loans` бараг үргэлж linked тул 258/300 prompt-д сануулга; сануулсан хүснэгт gold-д байхад +7/−1 (77), үгүй бол
  +6/−10 (123). Асуултад хүснэгтийн нэр гарсан үед л (125 prompt) — тэр дэд олонлогт +8/−2, ~+3 (дүрмийг дүнг харсны дараа
  сонгосон тул overfit эрсдэл дунд).
- Үлдсэн бүлгүүд (run бүрт): зохиосон багана 4–6, NPL харьцааг тоогоор (L035 X009 X020 — бүх 5 run-д; glossary_en ≤ +3),
  «нээлттэй хэрэг» = status 'OPEN' (K015 K031), илүү багана 5–8, далд нөхцөл (L014 X018 X034 P027 P038 P044), 'YYYY-MM' текстийг
  TIMESTAMP (C044, X043). M1afx нь M1rt-ийн 13 exec/guard алдааны 6-г хамарна.
