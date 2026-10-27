# Санхүүгийн өвөрмөц хэллэгийн толь + жишээ асуулт (орчуулгын загварын fine-tune өгөгдөл, v2 ноорог)

Зорилго: ББСБ/банкны ажилтан, шинжээч, харилцагчийн **өдөр тутам хэрэглэдэг санхүүгийн хэллэг** (үйл үгтэй хэлц, тогтвортой холбоо,
ярианы хэллэг, товчлол)-ийг англи **нягтлан/шинжээчийн каноник** хэлбэртэй нь цуглуулж, хэллэг бүрд ББСБ-ийн өгөгдлийн сангаас асуух
**жишээ асуулт** бичих. Орчуулагч загвар эдгээрийг шууд үгчлэн («зээл хаах» → "close the loan") биш, утгаар нь («зээлээ хаах» → "pay off the loan")
орчуулж сурна. Англи тал нь дараа нь Text2SQL загварын оролт болно → **утга яг, нэр томьёо тогтмол** байх нь хамгийн чухал.

## 1. Юу уншиж болох, юу болохгүй

Уншиж болно: энэ заавар, `docs/mt_authoring/BRIEF.md` (асуултын хэв маяг, register R1–R5, `entities` талбар — **бүгд энд хүчинтэй**),
`knowledge/semantic_model.yaml`, `knowledge/glossary.yaml`, `knowledge/glossary_plus.yaml`, `knowledge/translation_terms.yaml`,
`data/mt/v0/terms.jsonl` (каноник нэр томьёо: `curated` > `schema_label`; `code_derived`-ийг ашиглахгүй), `data/mt/v0/entities.json`.
**Уншиж БОЛОХГҮЙ:** `evalset/`, `data/thesis/`, `results/`, `build/`, `knowledge/examples*.jsonl`, `data/mt/v1*/`, энэ хоёр BRIEF-ээс бусад `docs/`.
Интернэт ашиглахгүй. Дэд агент бүү эхлүүл. Файлыг **Write tool**-оор бич (UTF-8; Git Bash heredoc-д кирилл бүү бич).

## 2. Хэллэг гэж юу вэ (юу цуглуулах)

terms.jsonl-д байгаа **ганц нэр үг** (зээл, хүү, барьцаа …)-ийг давтахгүй. Цуглуулах зүйл:

| Төрөл | Жишээ (монгол → англи каноник) |
|---|---|
| Үйл үгтэй хэлц | зээлээ хаах → pay off the loan; хугацаа сунгах → extend the term; барьцаа чөлөөлөх → release the collateral; зээл олгох → disburse a loan; данснаас хасах → write off |
| Тогтвортой холбоо | хугацаандаа төлөгдсөн → paid on time; эргэн төлөлтийн хуваарь → repayment schedule; үндсэн төлбөр → principal payment; хүүгийн хөнгөлөлт → interest discount |
| Ярианы / өдөр тутмын | зээлээ барих → repay; өр нь хуримтлагдсан → arrears accumulated; хүү нь хүүлэгдэх → interest compounding; тасалдсан → missed (payment); зээл давхардсан → multiple concurrent loans |
| Зохицуулалтын (Монголбанк, СЗХ) | чанаргүй зээл → non-performing loan; хэвийн бус → substandard; эргэлзээтэй → doubtful; муу → loss; зээлийн мэдээллийн сан → credit information bureau; эрсдэлийн сан байгуулах → set aside a loan-loss provision |
| Нягтлан / тайлан | орлогод тусгах → recognize as income; хуримтлуулсан хүү → accrued interest; цэвэр хүүгийн орлого → net interest income; өсөлттэйгээр → cumulatively (year to date) |
| Тоо/хугацааны хэлц | сар гаруй хоцорсон → more than a month overdue; хагас жилийн → semi-annual; оны эхнээс → year to date; өмнөх оны мөн үе → same period last year |
| Хоёрдмол / урхи | «хаах» (зээл = pay off, данс = close the account); «татах» (хадгаламж = withdraw, зээл = take out); «хураах» (барьцаа = repossess, төлбөр = collect); «хувь» (хүүгийн хувь = rate, хувь хүн = individual) |

Хэллэг бүр **ББСБ-ийн өгөгдлийн сангаас асууж болох** утгатай байх (`knowledge/semantic_model.yaml`-ын хүснэгтүүд: зээл, хүсэлт, эргэн төлөлт,
барагдуулалт, харилцагч, хадгаламж, ерөнхий дэвтэр). Ерөнхий эдийн засгийн (инфляц, валютын ханшийн бодлого) хэллэгийг бүү оруул.

## 3. Гаралт — хоёр файл

Таны бүлэг `<бүлэг>` (даалгавар дээр заасан). Файлууд: `data/mt/v2_drafts/phr_<бүлэг>_expr.jsonl` ба `data/mt/v2_drafts/phr_<бүлэг>.jsonl`.

**(а) Хэллэг** — `phr_<бүлэг>_expr.jsonl`, мөр бүр:
```json
{"id": "PH-<бүлэг>-001", "mn": "зээлээ хаах", "mn_variants": ["зээлээ бүрэн төлөх", "зээлээ дуусгах"],
 "en": "pay off the loan", "en_alt": ["repay the loan in full"], "pos": "verb_phrase",
 "sense": "Зээлийн үлдэгдлийг бүрэн төлж дуусгах. Данс хаахаас ялгаатай.",
 "trap": "close the loan account (үгчлэн) — буруу биш ч Text2SQL-д 'status = CLOSED' гэж андуурагдах эрсдэлтэй",
 "category": "verb_idiom"}
```
- `mn`: үндсэн (толь бичгийн) хэлбэр. `mn_variants`: ижил утгатай 0–4 өөр хэлбэр (ярианы, товч).
- `en`: **каноник** англи (нягтлан/шинжээчийн хэрэглэдэг). `en_alt`: зөвшөөрөгдөх өөр хэлбэр (0–3).
- `pos`: `verb_phrase` | `noun_phrase` | `adjective` | `adverbial` | `abbreviation`.
- `category`: `verb_idiom` | `collocation` | `colloquial` | `regulatory` | `accounting` | `time_quantity` | `ambiguous`.
- `trap`: орчуулагч ихэвчлэн алддаг буруу орчуулга ба яагаад буруу (байхгүй бол `""`).

**(б) Жишээ асуулт** — `phr_<бүлэг>.jsonl`, `BRIEF.md`-ийн §2 схемтэй **яг ижил** + `expr` талбар:
```json
{"id": "MT-phr<бүлэг>-001", "domain": "loans", "register": "R2", "expr": "PH-<бүлэг>-001",
 "mn": "Өнгөрсөн сард хугацаанаасаа өмнө зээлээ хаасан харилцагч хэд вэ?",
 "en": "How many customers paid off their loans early last month?",
 "terms": ["зээлээ хаах=pay off the loan"], "entities": [], "notes": ""}
```
- Хэллэг бүрд **3** асуулт (өөр register, өөр хэмжүүр/задаргаа/хугацаа). Асуултад хэллэг (эсвэл `mn_variants`-ын аль нэг) **дагавартайгаар** орно.
- `ambiguous` хэллэгт 3 асуултын дор хаяж нэг нь **өөр утгаар** (жишээ «данс хаах» → close the account) байна — загвар контекстоор ялгаж сурна;
  тэр асуултын `expr` нь тэр утгад тохирох хэллэгийн id (шаардлагатай бол тусдаа хэллэгийн мөр нэм).
- Register-ийн хуваарилалт, тоо/огноо ≥ 40%, entity ≥ 20% — `BRIEF.md` §4-тэй ижил (`R4` холимогт латин үг заавал).
- Монгол тал нь эх хэлтний байгалийн хэллэг; англи тал нь монголыг нэмэлт/хасалтгүй илэрхийлнэ; нэг ойлголтыг файл дотор нэг л янзаар орчуулна.
- `domain` нь 8 домэйны аль нэг (`applications, loans, payments, collection, customers, deposits, gl_trends, cross`).

## 4. Хэмжээ

Бүлэг бүрд **≥ 50 хэллэг** (давхардалгүй; бусад бүлэгтэй давхацвал зүгээр — нэгтгэхэд шийднэ) ба **≥ 150 асуулт**.

## 5. Өөрийгөө шалгах (заавал)

```bash
cd C:/Users/ochir.b/Desktop/claude/text2sql-new
PYTHONIOENCODING=utf-8 python scripts/mt/check_phrases.py <бүлэг>
```
**Алдаа 0** болтол засна; анхааруулгыг аль болох бууруулна (үнэлгээтэй төстэй мөрийн зөвхөн id-г хэлнэ — тэр мөрийг өөр сэдвээр бүхэлд нь шинээр бич).
Эцсийн мессеж (товч): хэллэг/асуултын тоо, category-ийн хуваарилалт, шалгагчийн дүн, эргэлзсэн 10 хүртэлх хэллэг + шалтгаан.
