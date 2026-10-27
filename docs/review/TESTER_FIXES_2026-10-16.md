# Tester-ийн тайлангийн засвар (2026-10-16)

Тайлан: [TESTER_REPORT_2026-10-16.md](TESTER_REPORT_2026-10-16.md).

Засварын дараах шалгалт:
- pytest: 57 passed. Шинээр нэмсэн тест: `tests/test_agent.py` (11), `tests/test_thesis_bench.py` (4).
- ruff (E/F/W): цэвэр.
- `bash -n scripts/server/*.sh`: OK.
- CRLF байхгүй.

| ID | Шийдвэр | Хаана |
|---|---|---|
| H1 | Зассан. E1-ийн засвар бүрийн дараа candidate бүрэн яриаг (`conv`), prefill, бүх raw_turns-ийг хадгална. Revise нь үүнийг үргэлжлүүлнэ. Тест: `test_e1_repair_then_agent_keeps_full_conversation` | orchestrator.py `_single_turn` |
| M1 | Зассан. (1) `lower()/upper()/trim()` эсвэл ILIKE бол том жижиг үсэг ялгахгүй шалгана. (2) Зөвхөн текст баганыг шалгана (information_schema), boolean/огноо/тоо алгасна. (3) Үр дүн «утгатай» (хоосон биш, ганц 0/NULL агрегат биш) бол олдвор зөөлөн байна. (4) IN жагсаалтын аль нэг утга байвал зөөлөн | agent.py `check_literals`, `_meaningful_rows` |
| M2 | Зассан. pii баганад хадгалсан утгыг харуулахгүй, зөвхөн «байхгүй» гэж мэдээлнэ. Тест: `test_literal_tool_hides_pii_values` | agent.py |
| M3 | Зассан. sqrl revise загварын хариу бүрийг ярианд нэмнэ, `<sql>` судлах алхмыг (≤ 2) гүйцэтгэнэ. `<answer>`-гүй бол SQL-гүй гэж үзэж засварыг үгүйсгэнэ. Тест: `test_sqrl_revise_runs_exploration_then_answer` | agent.py `_revise` |
| M4 | Зассан. `clean_reasoning` нь `<think>…</think>`, дуусаагүй `<think>`, `<answer>`-ийг зөв салгана. VERDICT байхгүй бол `UNPARSED`, LLM алдаа бол `ERROR` гэж бичлэгт тусад нь тэмдэглэнэ. Токены хязгаар = загварын `max_tokens` (≥ 1024) | agent.py |
| M5 | Зассан. COMPARE.md-ийн толгойд тэгш бус байдлыг бичсэн. Тэнцүү нөхцөл: `data/thesis/examples_dev.jsonl` + `MATCHED_FEWSHOT=1` → test-ийг train + dev сантай (`-fs_traindev`) нэмэлтээр ажиллуулна | bench.py, run_thesis_bench.sh, THESIS_BENCH.md |
| M6 | Зассан. Бүх seed орно. EX нь seed-ийн дундаж, McNemar нь хамгийн бага seed-ээр. Тест: `test_compare_report` | bench.py `compare_report` |
| M7 | Зассан. Adapter-ийг нэрээр нь биш, тохиргооноос (base гинж + vars) задалж шалгана. Adapter-тай ID, түүн дээр суурилсан G1-ийг алгасна (`thesis_stale.py --adapter`). **Мөн илэрсэн:** баримтын `BEST_CONFIG=E1` нь давталттай (E1-ийн суурь = best_config) тул бүх жишээг `D1` болгосон. Adapter-тай үед LoRA-тай, загвар бүрээр тусад нь ажиллуулах тэмдэглэл нэмсэн | run_thesis_bench.sh, thesis_stale.py, README, AGENT.md, run_plan_l4.sh |
| M8 | Зассан. Баримт ба COMPARE.md-ийн толгойг засав: gold л тэнцүү, таамгийн хөдөлгүүрийн нөлөө (SQLite сул / PG хатуу) үлдсэн | THESIS_BENCH.md, bench.py |
| M9 | Зассан. SQLite-д progress handler-ээр 60 с хязгаар тавьсан (thesis-ийн executor-той ижил). Тест: `test_sqlite_timeout` | bench.py `run_sqlite` |
| M10 | Зассан. plan/critic-ийн RuntimeError-ийг барьж: төлөвлөгөөгүй / `ERROR` verdict-тэй үргэлжилнэ | agent.py |
| L1 | Зассан. ББСБ-ийн биш DB дээр t2s-ийн бүх хүснэгтийн мөрийн тооноос fingerprint гаргаж, business_date-ийг бичнэ | runner.py `db_snapshot` |
| L2 | Зассан. Эх data.db ба кейсийн sha1 ≠ MANIFEST, PG DB алга, индекс алга бол дахин импортлоно | thesis_stale.py, run_thesis_bench.sh |
| L3 | Хэсэгчлэн. «gold хоосон-гүй» EX багана нэмсэн. Эрэмбэ ба 1000 мөрийн хязгаарыг баримтжуулсан. order_matters-ийг өөрчлөөгүй: манай эрэмбэтэй харьцуулалтад тэнцүү утгын хүлцэл байхгүй тул худал алдаа гаргана. Хоёр системд ижил үйлчилнэ | bench.py, THESIS_BENCH.md |
| L4 | Зассан. Үхмэл кодыг хассан. Дүрэм BIRD-ийн set дүрэмтэй нийцдэг тул хэвээр үлдээж, нөлөөлөх газрыг (test-suite, BIRD reward) баримтжуулсан | compare.py, EVAL.md |
| L5 | Баримтжуулсан (`code_version`-оор ялгагдана) | EVAL.md |
| L6 | Зассан. `Answer.chosen_index` нь identity-ээр (`is`). `build_record` үүнийг ашиглана | orchestrator.py, runner.py |
| L7 | Зассан. Засварын дараа итгэлийг засварласан SQL-ийн үр дүнг дэмжсэн хувилбаруудын хувиар дахин тооцно (E3) | orchestrator.py |
| L8 | Зассан. `Pipeline.agent_loop` нэг удаа үүснэ | orchestrator.py |
| L9 | Зассан. Сүүлийн тойрогт critic дуудагдахгүй | agent.py `refine` |
| L10 | Зассан. Planner хамаарлын эрэмбэ эсвэл бүх хүснэгтийг (≤ 25) харна | agent.py `plan` |
| L11 | Баримтыг зассан (кодоос бүрдүүлсэн, escape хийсэн, guard-гүй, read-only) | agent.py docstring, AGENT.md |
| L12 | Баримтжуулсан: A4/F7/F8/B10 thesis дээр утгагүй. Анхдагч IDS эдгээрээс хамаарахгүй | THESIS_BENCH.md |
| L13 | Зассан (29; `config.model`) | bench.py |
| L14 | Зассан. Run эхлэхэд marker файл үүсгэж, түүнээс шинэ файлыг л авна | run_thesis_bench.sh |
| L15 | Зассан. `test_thesis_bench.py` (4) ба agent-ийн 6 нэмэлт тест бичсэн | tests/ |
| L16 | Зассан. G5 (G1 − литерал tool) ба G6 (G2 − литерал tool = зөвхөн гүйцэтгэлийн алдаагаар нэмэлт засвар) нэмсэн | experiments.yaml, AGENT.md, run_plan_l4.sh |
| L17 | Зассан. Δ-ийн хажууд n_pairs харагдана | bench.py |
