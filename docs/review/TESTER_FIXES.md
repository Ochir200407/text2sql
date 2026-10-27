# Tester-ийн тайлангийн засвар (2026-10-15)

Тайлан: `docs/review/TESTER_REPORT_2026-10-15.md` (HIGH 7, MEDIUM 13, LOW 20).

Засварын дараах шалгалт:
- pytest: 43 passed. Үүнд 7 regression тест бий (`tests/test_tester_fixes.py`).
- ruff: E/F/W цэвэр.
- `bash -n scripts/server/*.sh`: OK. Бүх файл LF болсон, `.gitattributes` нэмсэн.
- check-gold `--testsuite`: 500 бичлэг, 0 алдаа.
- knowledge-check: 0 алдаа.
- security-test: бүгд хаагдсан.
- Бүтэн dev дээрх oracle (A6, B5; хоёр загвар): EX 100%.
- vLLM тасалдлын mock: 5 дараалсан алдааны дараа зогссон, 0 бичлэг хадгалсан.

| ID | Шийдвэр | Хаана |
|---|---|---|
| T-01 | Зассан. vllm_args-ийг shlex-ээр задалж bash массиваар дамжуулдаг болгосон (JSON аргумент нэг хашилтад). Мөн Python засвараас үүссэн CRLF-ийг бүх файлаас арилгасан | serve_model.sh, models.yaml |
| T-02 | Зассан. `router_model: qwen3.5-9b` нэмсэн. F8-ийн кэш хоосон бол runner зогсоно | models.yaml, runner.py |
| T-03 | Зассан. (a) run_plan_l4.sh үе шат бүрийг тусад нь ажиллуулна; B/F-д `BEST_A`, E-д `BEST_CONFIG` заавал; E нь C/D-ийн дараа. (b) Бичлэг ба meta-д `opts_hash` хадгална; тохиргоо зөрвөл resume хийхгүй (`--overwrite`/`--tag`). (c) meta-д `baseline` бичигдэж, тайлан түүнийг ашиглана | run_plan_l4.sh, runner.py, report.py |
| T-04 | Зассан. LLM/exception-ийг хариулт болгон хадгалахгүй; 5 дараалсан алдааны дараа зогсоно; `/v1/models`-ийг урьдчилан шалгана; eval_adapters.sh зөвхөн байгаа adapter-ийг үнэлнэ | runner.py, eval_adapters.sh |
| T-05 | Зассан. `load_item_index` зөвхөн sample хийсэн файлуудаас уншина; монгол асуулттай бичлэгийг сонгоно | training/data.py, formats.py, grpo.py |
| T-06 | Зассан. d2/f10-д max_length 16384, GRPO-д max_prompt_length 15000. Хэтэрсэн мөрийг хасаж бүртгэнэ | training.yaml, formats.py, grpo.py |
| T-07 | Зассан. F4 бонус completion бүрээр олгоно: hash нь бусад хувилбарын олонхтой таарвал | grpo.py `invariance_bonus` |
| T-08 | Зассан. GRPO-ийн санд англи stats оруулахгүй (C7 replay тусдаа) | grpo.py |
| T-09 | Зассан. knowledge_notes зөвхөн B1-д; B3 = зөвхөн нэр томьёо; B6 хоёуланг хасна | experiments.yaml |
| T-10 | Баримтжуулсан хазайлт. B4 нь mask хийсэн асуултын төстэй байдлаар сонгоно (урьдчилсан SQL skeleton-гүй) | EXPERIMENTS.md |
| T-11 | Зассан. Татгалзалтын муруйн TrustSQL-ийг бүх ангиллаар тооцно | metrics.py |
| T-12 | Зассан. Хүснэгт/утгын recall, F8 routed % ба EX, GPU-с/асуулт, мартагдлын хэсэг (BIRD-dev, Belebele) нэмсэн | runner.py, metrics.py, report.py |
| T-13 | Зассан. Judge гаралт `results/judge/`-д; тайлан, derive-abstain, ts-score зөвхөн `*__s<N>.jsonl`-ийг уншина | judge.py, report.py, cli.py |
| T-14 | Зассан. `date('now')`, нэг аргументтай `age()`, харьцуулалт дахь `'now'`/`'today'`-г as-of огноогоор солино | sql_guard.py |
| T-15 | Зассан. env.sh-д `set -e` байхгүй болсон | env.sh |
| T-16 | Зассан. train_a100.sh-ийг SFT ба GRPO гэсэн хоёр үе шат болгосон (`BEST_SFT` заавал). d2/f6 нь C-шилдэг жорын өгөгдлийг ашиглана. `T2S_BEST_A` дамжина | train_a100.sh, formats.py, sampling.py |
| T-17 | Зассан. Сургалтын BIRD prompt evidence-тэй; F1 үнэлгээ evidence-гүй хэвээр; баримтыг тааруулсан | bird/runner.py, TRAINING.md |
| T-18 | Зассан. meta-д индекс/embedding/vLLM аргументыг бичнэ. Embedding ачаалагдахгүй бол зогсоно. Eval үед embedding CPU дээр | runner.py, retriever.py, env.sh |
| T-19 | Зассан. F9-ийн санд dev/test асуулт байвал алдаа өгнө | annotate.py |
| T-20 | Зассан. `t2s compare --a --b` (McNemar, ΔEX, TrustSQL) нэмсэн | metrics.py, cli.py |
| T-21 | Зассан. derive-abstain ба pipeline ижил дүрэмтэй боллоо | report.py |
| T-22 | Зассан. executable % бүх оролдлогоор тооцно. Latency-ийн тайлбарыг тайланд нэмсэн | metrics.py, report.py |
| T-23 | Зассан. Ашиглагдаагүй түлхүүрүүдийг хассан. Eval executor-т EXPLAIN max_cost орсон | default.yaml, runner.py |
| T-24 | Зассан. Кэшийн бүрхэлт ба эх асуулт (source) таарч байгааг run-ийн өмнө шалгана | runner.py, intent.py, router.py |
| T-25 | Зассан. SQLite-д хөрвүүлж чадахгүй few-shot SQL-ийг эх хэлбэрээр нь үлдээнэ | orchestrator.py |
| T-26 | Баримтжуулсан. Хоёрдмол асуултыг аль нэг тайлбартай таарвал зөв гэж тооцно (EVAL.md) | — |
| T-27 | Баримтжуулсан хазайлт. Хэрэглэгчийн галиг ө-г "u", ү-г "v"/"u" гэж бичдэг тул бүгдийг нэг түлхүүрт нэгтгэсэн (recall-ийг нэмэх зорилгоор) | normalize.py |
| T-28 | Зассан. Сар/улирлын дугаарын хувиргалтыг нэг талд л хийнэ. 1-ээс бага тоонд 6 оронтой бөөрөнхийлөлт, нарийн хүлцэл | compare.py |
| T-29 | Зассан. scipy-г хамааралд нэмсэн | requirements.txt, pyproject.toml |
| T-30 | Зассан. Audit-д SQL шифрлэгдэнэ; guard-ийн шийдвэр ба retrieval контекстыг бичнэ; сонгосон candidate-ийн хүснэгтийг авна | service/api.py |
| T-31 | Зассан. Хоосон үр дүнд нэг л удаа дахин бодуулна | orchestrator.py |
| T-32 | Зассан. `t2s.collaterals.description`-ийг хассан. t2s давхаргыг бүх DB-д дахин ачаалж, индексийг дахин үүсгэсэн | 100_t2s_views.sql, semantic_model.yaml |
| T-33 | Зассан. setup.sh exit code-ыг тодорхой шалгана | setup.sh |
| T-34 | Зассан. `security-test --live` (жинхэнэ загвар) нэмсэн | injection.py, cli.py |
| T-35 | Зассан. Жишээний сангүй үед index-build алдаа өгнө | build.py |
| T-36 | Зассан. human.jsonl-д ч давхардлын шүүлт хийнэ | training/cli.py |
| T-37 | Зассан. sqrl GRPO-г нэмэлтээр max_steps 0-оор (`tag s0`) үнэлнэ | eval_adapters.sh |
| T-38 | Зассан. F1-д `.meta.json` бичнэ | bird/runner.py |
| T-39 | Зассан. Тайлан split бүрээр тусдаа (`REPORT_<split>.md`); галигийн зөрүү = A1 − A3 | run_experiments.sh, report.py |
| T-40 | Зассан. Arctic-ийн prompt-ыг статьийн (arXiv:2505.20315) Figure C.1-тэй яг тааруулсан: system = "data science expert", user = Engine/Schema/Question/Instructions/Output Format, prefill. sqrl-ийн prompt model card-тай таарч байгааг шалгасан | llm/prompts.py |
