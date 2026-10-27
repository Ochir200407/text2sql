# Tester-ийн тайлан (2026-10-16): thesis benchmark, agent (G), compare.py

Read-only tester агентын тайлангийн хураангуй. Агент кодыг өөрчлөөгүй.

Шалгах үеийн төлөв:
- pytest: 48 passed.
- `check-gold --config config/thesis.yaml`: 115 бичлэг, 0 алдаа.
- `knowledge-check`: 0 алдаа. Анхааруулга 2: thesis-ийн YAML-д байгаа боловч өгөгдөлд байхгүй enum утга.

Нийт 1 HIGH, 10 MEDIUM, 17 LOW. Засвар бүрийн шийдвэр: [TESTER_FIXES_2026-10-16.md](TESTER_FIXES_2026-10-16.md).

| ID | Зэрэг | Асуудал |
|---|---|---|
| H1 | HIGH | E1-ийн засвар хийгдсэн бол candidate-ийн `messages`, `prefill`, `raw_turns` алга болдог. Иймээс agent-ийн revise нь system, схем, асуултгүй яриагаар явна. Tester ажиллуулж баталгаажуулсан |
| M1 | MED | Литерал tool `lower(col) = 'mobile'`, boolean `= 't'`, хамгаалалтын `IN (...)` дээр худал «байхгүй» олдвор гаргаж, зөв SQL-ийг засуулна |
| M2 | MED | Литерал tool pii тэмдгийг үл хайхран хадгалсан утгыг (харилцагчийн нэр) prompt ба `agent_log` руу гаргадаг |
| M3 | MED | sqrl revise: загварын сүүлийн хариу ярианд нэмэгддэггүй. `<sql>` судлах асуулгыг эцсийн хариу гэж авдаг |
| M4 | MED | VERDICT мөргүй critic-ийг OK гэж үздэг, энэ нь бичлэгт ялгагддаггүй. Дуусаагүй `<think>`. Токены хязгаар бага (900/600) |
| M5 | MED | Few-shot сан тэгш бус: thesis train + dev, шинэ систем зөвхөн train. Энэ нь COMPARE.md-д заагдаагүй |
| M6 | MED | `compare_report` seed ≠ 0 файлыг алгасдаг. E2/E3 тайланд гардаггүй |
| M7 | MED | STAGE=thesis: adapter-тай BEST_CONFIG үед preflight унаж, скрипт зогсоно |
| M8 | MED | «Диалектын ялгаа оноонд нөлөөлөхгүй» гэсэн өгүүлбэр буруу: зөвхөн gold-ууд тэнцүү гэж баталгаажсан, таамаг өөр хөдөлгүүр дээр ажиллана |
| M9 | MED | `run_sqlite`-д хугацааны хязгаар байхгүй. Декарт join `thesis-score`-ийг гацаана |
| M10 | MED | plan/critic-ийн LLM алдаа асуултыг унагаадаг (infra алдаа гэж тоологдож, бичлэггүй үлдэнэ) |
| L1 | LOW | `db_snapshot` ББСБ-ийн хүснэгтээс хамаардаг. Thesis дээр fingerprint байхгүй |
| L2 | LOW | Скрипт импортын хуучралтыг (эх sha1, PG DB, индекс) шалгадаггүй |
| L3 | LOW | Бүх кейс `order_matters: false`. `gold_empty` нь EX-ийг өсгөдөг. Thesis-ийн өөрийн EX 1000 мөрөөр тасалдаг |
| L4 | LOW | Хоосон == хоосон дүрэм test-suite ба сургалтын reward-д ч нөлөөлнө. Үхмэл код байгаа |
| L5 | LOW | Soft-F1-ийн шинэ хувилбар хуучинтай харьцуулагдахгүй |
| L6 | LOW | `build_record` сонгосон хувилбарыг SQL текстээр хайдаг. `cands.index` нь identity биш `__eq__` ашигладаг |
| L7 | LOW | Засварын дараа confidence хуучин санал хураалтынх хэвээр |
| L8 | LOW | AgentLoop асуулт бүрт шинээр үүсдэг (agent_llm client) |
| L9 | LOW | Сүүлийн тойрогт critic дэмий дуудагддаг |
| L10 | LOW | relevant_tables хоосон үед planner эхний 12 хүснэгтийг л хардаг |
| L11 | LOW | Баримтад «parameterized, guard-аар» гэж бичсэн боловч tool f-string ашиглаж, guard-аар дамждаггүй |
| L12 | LOW | ББСБ-ийн тогтмол (FK whitelist, generic prompt, A4/F7/F8 кэш) thesis config дээр ч үйлчилнэ |
| L13 | LOW | «train (31)» гэсэн хуучирсан тоо. `sources[].model` үргэлж None |
| L14 | LOW | Скрипт `ls -t`-ээр хуучин үр дүнгийн файл авч болзошгүй |
| L15 | LOW | bench.py болон E1+agent, sqrl+agent-д тест байхгүй |
| L16 | LOW | G1 «нэг өөрчлөлт» биш: tool + critic + нэмэлт засвар. Литерал tool-ийг тусад нь хэмжих run байхгүй |
| L17 | LOW | EX баганыг бүтэн n-ээр, Δ-г огтлолцлоор тооцдог. n_pairs харагдахгүй |

Tester баталгаажуулсан зүйлс: ORIGINAL=1-ийн thesis-ийн config түлхүүр, CLI флаг, файлын нэр нь thesis-ийн кодтой таарна. Ижил
кейс, ижил шалгуур ашиглагдана. Few-shot-д leakage байхгүй. Токены бүртгэл зөв.
