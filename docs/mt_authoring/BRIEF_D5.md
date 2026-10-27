# D5 — орчуулгын «хатуу тохиолдол»: минимал хос (orчуулагч алддаг газар)

Зорилго: орчуулагч загварыг **нарийн ялгаа**-нд сургах. Хос бүр нь 2–3 асуулт: үг/дагавар/тоо нь бага зэрэг өөр, харин англи орчуулга
(тиймээс SQL) нь **ялгаатай** байх ёстой. Загвар эдгээрийг андуурвал Text2SQL буруу SQL бичнэ.

## 1. Юу уншиж болох

`docs/mt_authoring/BRIEF.md` (асуултын хэв маяг, `entities` талбар, register R1–R5, урхины жагсаалт — **бүгд хүчинтэй**), энэ заавар,
`knowledge/semantic_model.yaml`, `knowledge/glossary.yaml`, `knowledge/glossary_plus.yaml`, `data/mt/v0/terms.jsonl`, `data/mt/v0/entities.json`.
**Уншиж БОЛОХГҮЙ:** `evalset/`, `data/thesis/`, `results/`, `build/`, `knowledge/examples*.jsonl`, `data/mt/v1*/`, `data/mt/v2*/`, бусад `docs/`.
Интернэт ашиглахгүй. Дэд агент бүү эхлүүл. Файлыг **Write tool**-оор бич (UTF-8; heredoc-д кирилл бүү бич).

## 2. Ангилал (`trap`)

| trap | Юуг ялгах | Жишээ хос (mn → en) |
|---|---|---|
| `term` | ойролцоо нэр томьёо | «шимтгэлийн орлого» → fee income · «хүүгийн орлого» → interest income · «торгуулийн орлого» → penalty income |
| `threshold` | тэнцүү орох/орохгүй, хүрээ | «30-аас дээш хоног» → more than 30 days · «30 ба түүнээс дээш» → 30 days or more · «30 хүртэл» → up to 30 days |
| `period` | хугацааны хэлбэр | «3-р сар» → March · «сүүлийн 3 сар» → the last 3 months · «3 сараас дээш» → more than 3 months · «улирал бүр» vs «2-р улирал» |
| `quantity` | тоо/нэгж | «1.5 сая» → 1.5 million · «1.5 тэрбум» → 1.5 billion · «15 хувь» → 15% · «15 пунктээр» → by 15 percentage points |
| `negation` | үгүйсгэл, эсрэг | «барьцаатай» → with collateral · «барьцаагүй» → without collateral · «төлөөгүй» → unpaid · «бүрэн төлөөгүй» → not fully paid |
| `aggregate` | нэгтгэлийн утга | «хэдэн зээл» → how many loans · «хэдэн төгрөгийн зээл» → total amount of loans · «дундаж» vs «медиан» · «нийт» vs «тус бүр» |
| `scope` | хамрах хүрээ, шүүлтүүрийн байрлал | «идэвхтэй зээлтэй харилцагч» → customers with active loans · «идэвхтэй харилцагчийн зээл» → loans of active customers |
| `entity_trap` | оноосон нэртэй төстэй энгийн үг | «Баасан» (хүний нэр, entities.json-д байвал) vs «байсан» (үйл үг); «Дархан салбар» vs «дархан» (ерөнхий үг) — оноосон нэр биш үгийг `entities`-д бүү оруул |
| `order` | эрэмбэ, дээд/доод | «хамгийн их 5» → top 5 · «хамгийн бага 5» → bottom 5 · «5-аас их» → more than 5 |

## 3. Гаралт

`data/mt/v2_drafts/d5_<бүлэг>.jsonl` (бүлгийг даалгавар дээр заана). Мөр бүр `BRIEF.md` §2-ийн схемтэй **яг ижил** + 2 талбар:
```json
{"id": "MT-d5a-001", "domain": "loans", "register": "R2", "trap": "threshold", "pair": "d5a-p001",
 "mn": "30-аас дээш хоног хэтэрсэн зээл хэд вэ?", "en": "How many loans are more than 30 days past due?",
 "terms": ["хугацаа хэтэрсэн хоног=days past due"], "entities": [], "notes": ""}
```
- `pair`: нэг минимал хосын бүх мөр ижил `pair`-тэй (2–3 мөр). Хосын мөрүүд аль болох **ганц ялгаатай** (үг, дагавар, тоо).
- Хосын мөрүүдийн англи нь ялгааг **тодорхой** илэрхийлнэ (more than vs or more, without vs with …).
- Register-ийн хуваарилалт заавал биш (гэхдээ R1/R2/R4 холих), тоо/огноо ≥ 40%, entity ≥ 10%.

## 4. Хэмжээ ба шалгалт

Бүлэг бүрд **≥ 120 хос (≈ 280 мөр)**, ангилал бүрд ≥ 10 хос. Файлыг 3–4 багцаар бичиж (бүтэн файлыг дахин бичин) шалга:
```bash
cd C:/Users/ochir.b/Desktop/claude/text2sql-new
PYTHONIOENCODING=utf-8 python scripts/mt/check_drafts.py data/mt/v2_drafts/d5_<бүлэг>.jsonl
```
**Алдаа 0** (register-ийн хувийн анхааруулгыг үл тооно). Эцсийн мессеж (монголоор, товч): мөр/хосын тоо, ангилал бүрийн хос, шалгагчийн дүн, эргэлзсэн 10 хүртэлх мөр.
