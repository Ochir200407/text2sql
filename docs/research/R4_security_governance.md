# R4. Банкинд LLM Text-to-SQL-ийг production-д нэвтрүүлэх: аюулгүй байдал ба засаглал

> Огноо: 2026-10-30. Хамрах хүрээ: судалгаа + манай кодын аудит. Дараа нь Critical/High-ийн ихэнхийг засав — төлөв §3.7.
> Аудит хийсэн файлууд: `src/mn_text2sql/service/{api,analyst,flow}.py`, `security/sql_guard.py`, `db/executor.py`,
> `db/conn.py`, `db_t2s/110_roles_rls.sql`, `db_t2s/100_t2s_views.sql`, `config/default.yaml`, `config/web.yaml`,
> `scripts/server/run_plan_l4.sh`, `docs/SECURITY.md`, `docs/STUDIO_FEATURES.md`, `docs/STUDIO_NEXT.md`.
> «баталгаагүй» гэж тэмдэглэсэн мэдээллийг анхны эх сурвалжаас шалгаагүй.

## 0. Товч дүгнэлт

Манай систем «defense in depth»-ийн суурийг (sqlglot guard → read-only транзакц → тусдаа DB role → RLS → timeout)
сайн тавьсан бөгөөд P2SQL тест (`security/injection.py`) бичих/файл/системийн халдлагыг хоёр давхаргад хаадаг.
Гэхдээ одоогийн **вэб давхарга нь дипломын демод зориулагдсан** — банкны production-д дараах гурав нь хамгийн том
цоорхой:

1. **Хэрэглэгчийн баталгаажуулалт (authN) байхгүй, нэг нийтлэг түлхүүр URL-д явдаг** — `T2S_WEB_KEY`
   тохируулаагүй бол бүх endpoint нээлттэй; хэрэглэгчийн нэр (`user`) өөрөө зарласан утга (Critical/High).
2. **Эцсийн хэрэглэгчийн таних тэмдэг DB-д дамжихгүй** — бүх хүсэлт нэг `t2s_bot` role-оор (бүх салбар) ажилладаг тул
   бэлдсэн RLS бодлого бодит шинжээчдэд үйлчлэхгүй (High).
3. **Studio-ийн `/flow/run_job` зам shadow горимыг ба audit log-ийг тойрдог** — `service.mode=shadow` үед ч үр дүн
   буцаана, audit-д бичигдэхгүй (High).

## 1. Аюулын загвар (threat model)

| Актор | Зорилго | Суваг |
|---|---|---|
| Гадны халдагч | өгөгдөл хулгайлах, DoS | нийтэд нээлттэй URL (Cloudflare quick tunnel), түлхүүр алдагдах |
| Дотоод шинжээч (зөвшөөрөлтэй) | эрхээсээ илүү мэдээлэл (өөр салбар, PII) харах | асуулт, SQL засвар (`/analyst/rerun`) |
| Шууд prompt injection | загварыг хортой SQL бичүүлэх | асуултын текст |
| Шууд бус prompt injection | загварын зан төлөвийг өөрчлөх | DB-д хадгалсан текст (харилцагчийн нэр, ажил олгогч, тайлбар), few-shot жишээ, орчуулагч |
| Өгөгдлийн хордлого | жишээний санг бохирдуулах | 👍 feedback → candidate examples → knowledge/examples |
| Загварын алдаа (санамсаргүй) | буруу тоо → буруу шийдвэр | семантик алдаа (guard барихгүй) |

Гол зарчим: **LLM-ийн гаралт бол итгэмжгүй оролт** (OWASP LLM05). SQL-ийг загвар биш, guard ба DB-ийн эрх хянадаг байх
ёстой — манай архитектур энэ зарчмыг дагасан.

## 2. Судалгаа: стандарт ба шилдэг практик

### 2.1 OWASP Top 10 for LLM Applications (2025)
- **LLM01 Prompt Injection** — шууд (jailbreak) ба шууд бус (гадны өгөгдлөөр) [owasp_2025_llm_top10, greshake_2023_indirect].
  Text-to-SQL-д энэ нь «P2SQL» болж хувирна: асуултаар загварыг `DROP`/`DELETE` эсвэл хориотой хүснэгт уншуулах
  [pedro_2023_p2sql]. Pedro нар зөвхөн prompt-ын хамгаалалт хангалтгүй, DB-ийн эрх ба гаралтын шалгалт хэрэгтэйг харуулсан.
- **LLM02 Sensitive Information Disclosure** — үр дүн, prompt, лог-оор PII алдагдах.
- **LLM04 Data and Model Poisoning** — feedback-ээр жишээний сан бохирдох.
- **LLM05 Improper Output Handling** (2023 оны «Insecure Output Handling») — гаралтыг SQL/HTML-д шалгалтгүй дамжуулах →
  SQLi, XSS.
- **LLM06 Excessive Agency** — агент (sqrl `<sql>` судлах алхам) хэт их эрхтэй байх.
- **LLM10 Unbounded Consumption** — хязгааргүй хүсэлт, урт асуулга → DoS, зардал.

### 2.2 Generated SQL-ийн хамгаалалт (давхаргаар)
1. **Parse + allow-list** (AST): ганц SELECT, зөвшөөрсөн view/багана, **функцийн allow-list** (deny-list биш).
   sqlglot нь PostgreSQL-ийн parser биш — «parser differential» (sqlglot ба PG өөрөөр задлах) эрсдэл үлдэнэ.
2. **DB role**: `NOSUPERUSER NOBYPASSRLS`, зөвхөн view-д SELECT, `default_transaction_read_only`, `search_path` тогтмол,
   `REVOKE CREATE/TEMP`, аюултай функцийг PUBLIC-ээс хураах.
3. **Нөөцийн хязгаар**: `statement_timeout`, `lock_timeout`, `idle_in_transaction_session_timeout`, `temp_file_limit`,
   `work_mem`, мөрийн хязгаар, EXPLAIN өртгийн босго; хэрэглэгч тус бүрийн rate limit/квот.
4. **Row-level security** [pg_docs_rls]: эцсийн хэрэглэгч бүр өөрийн DB role (эсвэл `SET ROLE` / session-ий хувьсагч)-тай
   байж, бодлого түүгээр шүүнэ. View-гээр мөр нуух бол `security_barrier` (эсвэл PG15+ `security_invoker` + RLS) [pg_docs_rules_privileges];
   хэрэглэгч функц үүсгэж чадахгүй бол leak-ийн эрсдэл бага.
5. **Багана masking / PII**: view-д PII-г огт оруулахгүй (data minimization), эсвэл `postgresql_anonymizer` маягийн
   dynamic masking, эсвэл багана-түвшний `GRANT SELECT (col, ...)`. Мэдрэмтгий баганад role-оор ялгаатай view.

### 2.3 Audit logging
Банкинд audit нь: хэн (баталгаажсан ID), хэзээ, юу асуусан, ямар SQL ажилласан, ямар хүснэгт/багана, хэдэн мөр,
экспорт хийсэн эсэх, ямар загварын хувилбар — **tamper-evident** (append-only, hash chain эсвэл WORM/SIEM),
хадгалах хугацаа нь зохицуулалтаар тогтоогдоно. PG-ийн талд `pgaudit` (session/object audit) өргөн хэрэглэгддэг.

### 2.4 Data residency / on-prem LLM
Банкны харилцагчийн өгөгдөл (асуулт ба үр дүн, prompt дахь жишээ утга) гадны API руу гарвал хил дамнасан шилжүүлэлт
болно. Монголын Хувь хүний мэдээлэл хамгаалах тухай хууль гадаадад шилжүүлэхэд ерөнхийдөө мэдээллийн субъектын
зөвшөөрөл шаарддаг [pwc_2022_mn_pdp]. Иймд **on-prem / VPC дотор open-weight загвар** (манай Arctic-Text2SQL-R1-7B + vLLM,
`base_url: localhost:8000`) нь давуу тал. Kaggle/Colab + Cloudflare tunnel нь зөвхөн synthetic өгөгдөлтэй демод тохирно.

### 2.5 Монгол Улсын эрх зүй
- **Хувь хүний мэдээлэл хамгаалах тухай хууль** — 2021-12-17 батлагдсан, 2022-05-01-нээс хүчин төгөлдөр; 1995 оны
  Хувь хүний нууцын тухай хуулийг орлосон [pwc_2022_mn_pdp, mn_2021_pdp_law]. Мэдрэмтгий мэдээлэл (генетик, биометр,
  эрүүл мэнд г.м.)-ийг тусад нь зохицуулдаг; цуглуулах/ашиглахад бичгээр (эсвэл цахимаар) зөвшөөрөл, гадаадад
  шилжүүлэхэд тусгай зөвшөөрөл. Зөвшөөрлийн яг хэлбэр, зүйл заалтын дугаар — эх сурвалжуудад зөрүүтэй, **баталгаагүй**.
- **Кибер аюулгүй байдлын тухай хууль** (2021) — онц чухал мэдээллийн дэд бүтэц, эрсдэлийн үнэлгээ, халдлагад хариу
  үзүүлэх үүрэг; банк хамрагдах эсэх ба хэрэгжүүлэх журмууд — **баталгаагүй** [mn_2021_cyber_law].
- **Банкны тухай хууль** — харилцагчийн/банкны нууцыг хамгаалах үүрэг (зүйл заалт **баталгаагүй**).
- **Монголбанкны журам** — мэдээллийн технологийн эрсдэлийн удирдлага, кибер аюулгүй байдлын тусгай журмыг онлайн
  хайлтаар олж баталж чадаагүй (**баталгаагүй**). Хамгаалалтын өмнө Монголбанкны Банкны хяналтын газрын хүчин төгөлдөр
  журмуудыг шалгах шаардлагатай. AI-ийн тусгай хууль/журам Монголд одоогоор байхгүй гэж үзэж байна (**баталгаагүй**).

### 2.6 Загварын эрсдэлийн удирдлага (Model Risk Management)
SR 11-7 [fed_2011_sr117] нь загварын эрсдэлийг (1) загвар буруу, (2) зөв загварыг буруу хэрэглэх гэж тодорхойлж,
**хөгжүүлэлт/хэрэгжүүлэлт**, **бие даасан баталгаажуулалт (effective challenge)**, **засаглал (inventory, бодлого,
баримтжуулалт)** гэсэн гурван тулгуурыг шаарддаг. NIST AI RMF 1.0 [nist_2023_ai_rmf] нь Govern–Map–Measure–Manage
функцээр үүнийг AI-д өргөтгөнө. Text-to-SQL-д хэрэглэвэл:
- **Загварын бүртгэл**: загварын нэр/хувилбар/checkpoint hash, prompt хувилбар, жишээний сангийн хувилбар, semantic model
  хувилбар — хариулт бүрт.
- **Баталгаажуулалт**: dev/test eval (EX), test-suite, shift set (`evalset_shift/`), TrustSQL penalty оноо — манай
  thesis-д байгаа; production-д нэвтрүүлэхээс өмнө бие даасан баг давтах ёстой.
- **Хэрэглээний хязгаар**: зөвхөн шинжилгээ/дотоод тайлан; зээлийн шийдвэр, зохицуулагчид өгөх тайланд шууд
  ашиглахгүй (хүн баталгаажуулсан SQL-ээр л).

### 2.7 Human-in-the-loop
Үр дүнгийн тоо нь шийдвэрт нөлөөлөх тул: итгэлийн түвшин, татгалзах (abstain/clarify), SQL + монгол тайлбарыг
харуулах, **баталгаажсан асуулгын сан** (verified queries), feedback-ийг хүн хянаж байж жишээнд нэмэх. Shadow → pilot →
live гэсэн үе шаттай нэвтрүүлэлт. Манайд эдгээр бүгд хэсэгчлэн хэрэгжсэн (§4).

### 2.8 Monitoring / drift
- Оролтын drift: асуултын сэдэв, OOV нэр томьёо, шинэ хүснэгт/багана (схемийн өөрчлөлт).
- Гаралтын чанар: татгалзлын хувь, guard-ийн алдааны хувь, exec error хувь, хоосон үр дүнгийн хувь, 👍/👎 харьцаа,
  санал нийлэлт (self-consistency)-ийн тархалт, latency p95.
- Тогтмол «canary» асуултууд (мэдэгдэж буй хариутай) өдөр бүр ажиллуулж EX-ийг хянах.
- Схем/өгөгдөл өөрчлөгдөхөд semantic model, value index, жишээний санг дахин баталгаажуулах (`knowledge/check.py`).

## 3. Манай кодын аудит — сул талууд

Severity: **Critical / High / Medium / Low / Info**. «Production» гэдэг нь бодит банкны өгөгдөлтэй ажиллах нөхцөл.

### 3.1 Нэвтрэлт ба хандалтын хяналт

| # | Байршил | Сул тал | Severity | Санал болгох засвар |
|---|---|---|---|---|
| A1 | `service/api.py:169-173` | `T2S_WEB_KEY` тохируулаагүй бол `check_key` юу ч шалгахгүй — бүх endpoint (`/ask`, `/flow/*`, `/analyst/*`) нээлттэй (fail-open). | Critical (production) | Түлхүүргүй бол серверийг эхлүүлэхгүй (fail-closed); банкны SSO (OIDC/SAML, AD) + session/JWT. |
| A2 | `service/api.py:100`, `:111-115` | `user` нь хүсэлтийн body-д өөрөө зарласан утга — audit-ийн хэрэглэгч хуурамч байж болно. | High | Хэрэглэгчийн ID-г зөвхөн баталгаажсан token-оос авах. |
| A3 | `service/api.py:173`, `analyst.py:1080,1101` г.м. | Түлхүүр URL query (`?k=`, `?admin=`)-д — access log, browser history, Referer, Cloudflare лог-д үлдэнэ. `api.py:173` нь `!=` (constant-time биш; `analyst.py` нь `hmac.compare_digest` ашигладаг). | Medium | `Authorization` header эсвэл HttpOnly cookie; `hmac.compare_digest`. |
| A4 | `service/api.py:276-277` | `/feedback` нь `check_key` огт дууддаггүй — хэн ч feedback файлд бичиж чадна (дискийг дүүргэх, хордлого). | Medium | `check_key` + rate limit. |
| A5 | `service/flow.py:670-676`, `:326` | `/flow/save`, `DELETE /flow/{id}` нь нийтлэг түлхүүртэй хэн бүхэнд `config/flows.yaml`-ийг өөрчлөх эрх өгнө (тохиргооны tampering). | Medium | Админ эрх (`T2S_ADMIN_KEY`/RBAC) шаардах; production-д builder-ийг унтраах. |
| A6 | `service/flow.py:688-698` | `/flow/logs` нь серверийн лог (web/SQL/translator)-ийг буцаана — асуулт, SQL, алдааны мөр агуулж болно. | Medium | Зөвхөн админ; production-д хаах. |
| A7 | `service/api.py:289`, `scripts/server/run_plan_l4.sh:285-287` | Анхдагч `host=0.0.0.0`; Cloudflare quick tunnel нийтэд нээлттэй URL үүсгэнэ; `cloudflared`-г `latest`-ээс checksum-гүй татна (supply chain). | Medium (demo-д OK) | Production-д дотоод сүлжээ + reverse proxy (TLS, WAF); binary-г pin + checksum. |
| A8 | — (CORS, security header) | CSP, `X-Frame-Options`, HSTS байхгүй. | Low | Reverse proxy эсвэл middleware-ээр нэмэх. |

### 3.2 Өгөгдлийн хандалт (DB, RLS, PII)

| # | Байршил | Сул тал | Severity | Санал болгох засвар |
|---|---|---|---|---|
| D1 | `db_t2s/110_roles_rls.sql:71-72`; `service/api.py:136` | Үйлчилгээ бүх хүсэлтийг нэг `t2s_bot` (`all_branches=TRUE`) role-оор ажиллуулна. RLS бодлого (`:148-171`) зөв бичигдсэн ч эцсийн шинжээчийн салбар/эрх DB-д дамжихгүй → **салбарын шинжээч бүх салбарыг харна**. Файлын тайлбар (`:70`) «хэрэглэгч бүрд тусдаа role» гэж бичсэн боловч кодонд хэрэгжээгүй. | High | Хэрэглэгч → DB role mapping (pool per role, эсвэл `SET ROLE` + `session_user`-ийн оронд баталгаатай context, эсвэл `SET LOCAL app.user_id` + `current_setting`-д суурилсан бодлого — гэхдээ guard `set_config`-ийг хориглодог тул executor өөрөө тохируулна). |
| D2 | `db_t2s/100_t2s_views.sql:56-64,74-78` | View-д регистр/утас ороогүй (сайн), гэвч `customer_name`, `org_name`, `employer_name`, `monthly_income`, `is_pep`, `aml_status`, `risk_level` ил. Semantic model-ийн `pii: true` (`knowledge/semantic_model.yaml:76`) нь зөвхөн prompt/value index-д нөлөөлдөг — DB-д masking хийдэггүй. Intent check (`pipeline/intent_check.py:43`) нь regex — тойрч болно. | High | PII/мэдрэмтгий баганыг role-оор ялгах (жишээ нь `t2s_analyst` view-д нэрийг mask, `t2s_compliance`-д бүтэн); багана-түвшний GRANT; экспортод нэмэлт зөвшөөрөл. |
| D3 | `config/default.yaml:16,20`; `110_roles_rls.sql:23,29` | Bot-ийн нууц үг `t2s_bot_local_only` repo-д; `t2s_branch_demo` ижил нууц үгтэй; `admin_dsn` нь нууц үггүй `postgres` superuser. | Medium (production-д High) | Нууцыг vault/env-ээс; role бүрт өөр нууц үг; scram + `pg_hba` IP хязгаар; админ DSN-ийг үйлчилгээний процесст огт өгөхгүй. |
| D4 | `config/default.yaml:25` | `max_explain_cost: 50000000` — маш өндөр, бараг хязгаарлахгүй; EXPLAIN-ийн өртөг найдваргүй тул timeout л үлдэнэ. | Low | Бодит ачааллаар калибрлах (жишээ нь p99 × 10); хэрэглэгчийн квот. |
| D5 | `db_t2s/100_t2s_views.sql` (бүх view) | View-ууд `WHERE deleted_at IS NULL`-ээр мөр нуудаг ч `security_barrier` биш. Bot функц үүсгэж чадахгүй (`REVOKE CREATE`) тул leak-ийн эрсдэл бага. | Low | Мэдрэмтгий шүүлттэй view-д `WITH (security_barrier)` эсвэл RLS-д шилжүүлэх. |
| D6 | `thesis/bench.py:290` | Bench нь `admin_dsn` (superuser, RLS-ийг алгасна)-ээр ажиллана. Үйлчилгээнд хамаарахгүй. | Info | Баримтжуулах; production орчинд bench ажиллуулахгүй. |

### 3.3 SQL guard ба executor

| # | Байршил | Сул тал | Severity | Санал болгох засвар |
|---|---|---|---|---|
| G1 | `security/sql_guard.py:22-31,136` | Функцийг **deny-list**-ээр шалгадаг. `pg_` угтвар ихэнхийг барих ч `generate_series`, `repeat`, `string_agg` (DoS), `current_database`, `current_schema`, `has_*_privilege` (мэдээлэл) зэрэг нэвтэрнэ. DB давхарга + timeout нь хамгаална. | Medium | Зөвшөөрсөн функцийн allow-list (агрегат, огноо, тоо, текст) + `generate_series`-ийн аргументын хязгаар. |
| G2 | `security/sql_guard.py:105,110` | `allowed_tables` хоосон бол хүснэгт/баганын шалгалт бүрэн унтардаг (fail-open). Unqualified `pg_roles`, `pg_stat_activity` нь `search_path`-д `pg_catalog` байдаг тул уншигдаж болно. | Medium | Хоосон allow-list → бүх хүснэгтийг татгалзах; эхлэхэд assert. |
| G3 | `security/sql_guard.py:151` | Хүснэгтээр тодорхойлоогүй (unqualified) багана ба `SELECT *`-ийг шалгадаггүй — багана-түвшний хязгаарлалтыг guard-д найдаж болохгүй. | Low (DB-д найдах тул) | Багана-түвшний хамгаалалтыг зөвхөн DB (D2)-д. sqlglot `qualify`-аар баганыг шийдэх. |
| G4 | `security/sql_guard.py:3,71` | sqlglot ба PostgreSQL-ийн parser ялгаатай (dollar quote, `E''`, nested comment). Файл өөрөө «validator биш» гэж хүлээн зөвшөөрсөн. | Low | Fuzz тест (sqlglot OK ↔ PG-ийн гүйцэтгэл) нэмэх; DB давхаргыг гол хамгаалалт гэж үзэх. |
| G5 | `db/executor.py:359-380` | `Executor.run` өөрөө guard дууддаггүй — дуудагч бүр guard хийх ёстой. Одоо: `orchestrator.py:439,496`, `analyst.py:1048` guard хийнэ; `orchestrator.py:478` нь тогтмол metadata query (хэрэглэгчийн оролтгүй). | Low | `SafeExecutor(guard, executor)` wrapper — guard-гүй замыг бүтцээр нь хаах. |
| G6 | `db/executor.py:409-412`; `service/api.py:238`; `flow.py:709` | PG-ийн алдааны текст (600 тэмдэгт хүртэл) ба Python exception-ийг хэрэглэгчид буцаана — схем/дотоод мэдээлэл алдагдах. | Low | Хэрэглэгчид ерөнхий мессеж, дэлгэрэнгүйг зөвхөн audit/лог-д. |

### 3.4 Shadow горим, audit, feedback

| # | Байршил | Сул тал | Severity | Санал болгох засвар |
|---|---|---|---|---|
| S1 | `service/flow.py:713-738` (`/flow/run_job`), `:553-590` (`FlowRunner._run`) | `service.mode=shadow` үед `/ask` үр дүнг нууна (`api.py:256-257`), харин Studio-ийн `/flow/run_job` бүрэн үр дүн, prompt, RAG-ийг буцаана; **audit log-д огт бичихгүй**. `/flow/inspect` (`:700-711`) prompt доторх DB-ийн жишээ утгыг харуулна. | High | `FlowRunner.run`-д mode шалгалт ба `audit.write`; production-д builder-ийг тусдаа админ-only процесс болгох. |
| S2 | `service/api.py:59-64` | `T2S_AUDIT_KEY` байхгүй бол `question` ба **`sql` хоёулаа хадгалагдахгүй** (зөвхөн hash) → ямар SQL гүйцэтгэгдсэнийг audit-аас сэргээх боломжгүй. | Medium | SQL нь PII биш тул plaintext (эсвэл заавал шифрлэлттэй) хадгалах; түлхүүргүй бол production-д эхлүүлэхгүй. |
| S3 | `service/api.py:63`; `analyst.py:497-498` | Асуулт/SQL-ийн давсгүй SHA-1 (16 тэмдэгт) — богино, таамаглахуйц асуултыг dictionary-гаар сэргээх боломжтой. | Low | HMAC-SHA256 (нууц түлхүүртэй). |
| S4 | `service/api.py:114` | `T2S_USER_PEPPER` байхгүй бол анхдагч `"t2s"` — хэрэглэгчийн hash-ийг сэргээж болно. | Low | Pepper-гүй бол эхлүүлэхгүй. |
| S5 | `service/api.py:66-90` | Audit нь локал JSONL; `purge()` файлыг бүхэлд нь дахин бичдэг — tamper-evident биш, засварласныг илрүүлэхгүй; 90 хоногийн хадгалалт (`config/default.yaml:85`) банкны шаардлагаас богино байж магадгүй (**баталгаагүй**). | Medium | Append-only (hash chain эсвэл SIEM/WORM руу дамжуулах), `pgaudit`; хадгалах хугацааг compliance-тэй тохиролцох. |
| S6 | — | Audit-д загварын хувилбар, prompt/жишээний сангийн хувилбар, экспорт (CSV/XLSX) хийсэн үйлдэл бичигддэггүй. | Medium | MRM (§2.6)-ийн шаардлагаар `model_version`, `prompt_hash`, `index_hash`, `export` event нэмэх. |
| S7 | `service/analyst.py` (FeedbackStore), `analyst.py:1101-1124` | 👍 нь candidate файлд л ордог, хүн хянана (сайн). Verified SQL-ийг админ guard-аар шалгаж нэмнэ (сайн). Эрсдэл: админ түлхүүр URL-д (A3). | Low | Хоёр хүний баталгаа (four-eyes) verified SQL-д. |

### 3.5 Prompt injection ба гаралтын боловсруулалт

| # | Байршил | Сул тал | Severity | Санал болгох засвар |
|---|---|---|---|---|
| P1 | `pipeline/orchestrator.py:492-500` (`_explore`), `llm/prompts.py:74-75` | Шууд бус injection: DB-ийн текст (нэр, ажил олгогч, AML тайлбар), value retrieval, few-shot жишээ prompt-д ордог. Халдагч харилцагчийн нэрэнд заавар бичвэл загварын SQL-д нөлөөлж болно. Guard нь зөвхөн «зөвшөөрөгдсөн» SQL-ийг гаргах тул **нууцлал биш, бүрэн бүтэн байдал (буруу хариу)** эрсдэлтэй. | Medium | Prompt-д DB утгыг тусгаарлагч (delimiter)-тай, «data, not instructions» гэж тэмдэглэх; утгын урт хязгаар; PII баганын утгыг prompt-д оруулахгүй (одоо хэсэгчлэн хийгдсэн, `prompts.py:81,149`). |
| P2 | `pipeline/translate.py` (орчуулагч), `api.py:223-231` | Орчуулагч LLM нь хоёр дахь injection гадаргуу — орчуулга нь SQL загварын prompt болдог; хэрэглэгчид орчуулгыг харуулдаг (сайн). | Low | Орчуулгын гаралтын урт/формат шалгах. |
| P3 | `service/static/index.html:72` | `esc()` нь `'`-ийг escape хийдэггүй (`analyst.js:6`, `builder.html:468` хийдэг). Одоогийн загвар хашилтан доторх attribute-д ашиглаж байгаа тул шууд XSS олдоогүй. | Low | Нэг `esc`-ийг бүх файлд; CSP. |
| P4 | `service/analyst.py:444-457` | CSV formula injection-ээс хамгаалсан (`_safe_cell`) — сайн практик (OWASP LLM05). | Info | XLSX-д ч мөн адил эсэхийг тест-ээр батлах. |

### 3.6 Нөөцийн хэрэглээ (LLM10)

| # | Байршил | Сул тал | Severity | Санал болгох засвар |
|---|---|---|---|---|
| R1 | `service/api.py:188-206`; `flow.py:713-738` | `/ask_job`, `/flow/run_job` хүсэлт бүрт хязгааргүй thread үүсгэнэ, `jobs` dict санах ойд; rate limit/квот байхгүй → GPU/DB DoS. (`/analyst/rerun` нь semaphore-той — `analyst.py:1054`, сайн.) | Medium | Хэрэглэгч тус бүрийн rate limit, queue (max concurrency), job-ийг эзэмшигчтэй холбох. |
| R2 | `service/api.py:208-215` | `/job/{jid}` — түлхүүртэй хэн ч бусдын job-ийн үр дүнг jid-ээр унших боломжтой (jid 48 бит санамсаргүй тул таах хэцүү). | Low | Job-ийг хэрэглэгчийн ID-тэй холбох. |

### 3.7 Засагдсан төлөв (2026-10-30, кодын засвар)

Дээрх хүснэгтүүдийн мөр/байршил нь аудитын үеийнх (засварын дараа мөрийн дугаар шилжсэн). Шинэ модуль:
`service/auth.py`; тест: `tests/test_service_security.py` (PG/LLM-гүй, mock). 2-р аудитын (S1–S8) засвар доор.

| # | Засагдсан | Юу хийсэн / үлдсэн |
|---|---|---|
| A1 | **Засагдсан** (хэсэгчлэн) | `auth.make_check_key`: `service.mode` нь `dev`/`local` биш (shadow/pilot/live/prod) ба `T2S_WEB_KEY` байхгүй бол зөвхөн localhost-оос **шууд** ирсэн хүсэлт (proxy/tunnel толгой `X-Forwarded-For`, `Forwarded`, `CF-Connecting-IP`, `X-Real-IP`, `CF-Ray` байхгүй) зөвшөөрөгдөнө, бусад нь 403. `serve()` нь 0.0.0.0-ийн оронд 127.0.0.1-д холбогдож чанга анхааруулна. Ухамсартай гарц: `T2S_INSECURE_OPEN=1` (зөвхөн synthetic демо). `dev`/`local` горим хуучин шигээ нээлттэй. SSO/OIDC — үлдсэн. |
| A2 | Үгүй | `user` нь өөрөө зарласан хэвээр — SSO хэрэгтэй (кодын хүрээнээс гадуур). |
| A3 | **Засагдсан** | Түлхүүр `X-T2S-Key`, админ `X-T2S-Admin` толгойгоор (router-ийн async dependency → contextvar); `?k=`/`?admin=` нийцтэй байдлын үүднээс хэвээр. Харьцуулалт `hmac.compare_digest`. JS (`index.html`, `builder.html`, `analyst.js`) fetch-д толгой илгээдэг; CSV/XLSX татах `<a href>` холбоос л `?k=`-тэй хэвээр. |
| A4 | **Засагдсан** | `/feedback` нь `check_key` дуудна (толгой эсвэл `?k=`). Rate limit — үлдсэн. |
| A5 | **Засагдсан** | `/flow/save`, `DELETE /flow/{id}` нь dev биш горимд `T2S_ADMIN_KEY` шаардана (тохируулаагүй бол хаалттай). Локал `t2s flow-ui` (`create_builder_app`) хуучин шигээ. |
| A6 | **Засагдсан** | `/flow/logs` — A5-тай адил админ-only. |
| A7 | Хэсэгчлэн | Түлхүүргүй үед 127.0.0.1 (A1). Cloudflare/`cloudflared` checksum — `scripts/` хөндөөгүй, үлдсэн. |
| S1 | **Засагдсан** | `/flow/run_job`: `mode=shadow` үед `FlowRunner.run(shadow=True)` prompt/RAG/үр дүн/экспортын кэшгүй, хэрэглэгчид `{"status": "shadow_logged"}` л буцаана; бүх горимд `audit.write` (`source: flow`, SQL/асуулт AuditLog-ийн бодлогоор шифрлэгдэнэ; алдаа ч `failed` гэж). `/flow/inspect` shadow-д админ-only. |
| R1 | **Засагдсан** (хэсэгчлэн) | Pipeline/SQL ажиллуулдаг бүх endpoint (`/ask`, `/ask_job`, `/flow/run_job`, `/analyst/rerun`, `/analyst/verified/{id}/run`) нэг хуваалцсан хязгаартай (`service.max_jobs` эсвэл `T2S_MAX_JOBS`, анхдагч 4) — хэтэрвэл 429 (доорх 2-р аудитын S1). Хэрэглэгч тус бүрийн квот — үлдсэн. |
| G2 | **Засагдсан** | `SqlGuard(fail_closed=True)` — allow-list хоосон бол аливаа хүснэгт `forbidden_table`. `create_app` нь dev биш горимд `fail_closed`-ийг асаагаад, хоосон allow-list-ийг ERROR-оор лог хийнэ. Анхдагч `False` (тест/eval хуучин шигээ). |
| D1, D2, D5 | Үгүй | DB role/RLS/view-ийн SQL (`db_t2s/*.sql`) хөндөөгүй — хэрэглэгч → DB role mapping, PII masking, `security_barrier` үлдсэн. |
| D3 | Үгүй | `config/default.yaml`-ийн `t2s_bot_local_only` нууц үг, нууц үггүй `admin_dsn`, `t2s_branch_demo` ижил нууц үг — **production-оос өмнө** vault/env-ээр солих; энэ засварт тохиргоог хөндөөгүй. |
| S2–S6, G1, G3–G6, P1–P3, R2, A8 | Үгүй | Хамрах хүрээнээс гадуур (Medium/Low) — §5-ын замаар. |

#### 2-р аудит (2026-10-30): `service/` (auth.py, api.py, flow.py, analyst.py) ба `security/sql_guard.py`

Энэ хүснэгтийн S1–S8 нь 2-р аудитын олдвор (§3.4-ийн S1–S7-оос өөр). Тест: `tests/test_service_security.py`,
`tests/test_guard_dialect.py` (FastAPI TestClient + mock, PostgreSQL/LLM-гүй).

| # | Severity | Төлөв | Юу хийсэн |
|---|---|---|---|
| S1 | Medium | **Засагдсан** | `auth.WorkSlots` — нэг хуваалцсан `BoundedSemaphore`: `/ask` (синхрон, өмнө нь хязгааргүй), `/ask_job`, `/flow/run_job`, `/analyst/rerun`, `/analyst/verified/{id}/run`; `service.max_jobs`/`T2S_MAX_JOBS` (анхдагч 4); дүүрвэл 429 + `Retry-After`. Синхрон замд `with slots.hold()`, background job-д ажлын thread-ийн `finally`-д чөлөөлнө (thread эхлээгүй бол handler өөрөө). Endpoint бүрийн тусдаа `jobs` тоолол ба analyst-ийн тусдаа semaphore-ыг сольсон. |
| S2 | Medium | **Засагдсан** | `T2S_WEB_KEY`-гүй, dev биш горимд localhost-оос шууд ирсэн хүсэлтийн `Host` толгой ∈ {`localhost`, `127.0.0.1`, `[::1]`} ∪ `service.trusted_hosts` ∪ `T2S_TRUSTED_HOSTS` (порт хасч, жижиг үсгээр) байх ёстой — DNS rebinding, түүхий TCP forwarder (ngrok TCP г.м.) 403. Эхлэхэд ба Host бүрийн анхны татгалзалтад `!!!` WARNING. Ижил машины хөтөч (`http://localhost:8080`, `http://127.0.0.1:8080`) Studio-г shadow горимд хэвээр ашиглана (тестээр баталсан). |
| S3 | Medium | **Засагдсан** | SqlGuard: `table_/query_/cursor_/schema_/database_` × `to_xml`/`to_xmlschema`/`to_xml_and_xmlschema` (15 функц) ба нэрэнд `_to_xml` агуулсан бүх функц `forbidden_function` (SELECT, FROM, дэд асуулга, `"quoted"`, `pg_catalog.`). Хэмжээ (`analyst.cap_rows`): нүд ≤ 10 000 тэмдэгт (тайрч «… [+N тэмдэгт]»), `ResultCache` бичлэг ≤ 8 MB, нийт ≤ 64 MB (хуучныг шахна, хугацаа дууссаныг `put` чөлөөлнө), JSON хариулт (`/ask`, `/analyst/rerun`, verified run, Studio trace) ≤ 2 MB; тайрвал `notices`-д `size`, экспортод `X-T2S-Truncated` толгой ба XLSX-ийн «Анхаар» мөр. `json_agg`/`string_agg` зөвшөөрөгдсөн хэвээр. `db/executor.py` хөндөөгүй. |
| S4 | Low | **Засагдсан** | `enforce_limit` нь `FETCH FIRST n ROWS ONLY \| WITH TIES`-ийн n-ийг уншиж min(n, хязгаар) болгоно; `WITH TIES`, `NEXT`, `OFFSET` хэвээр (өмнө нь `LIMIT 1000` болж, n ба ties алдагддаг байв). n нь илэрхийлэл/PERCENT бол дахин бичихгүй — мөрийг Executor-ийн `max_rows` хязгаарлана. `LIMIT`-ийн зан төлөв өөрчлөгдөөгүй (`LIMIT <илэрхийлэл>` хуучин шигээ `LIMIT enforce_limit` болно). |
| S5 | Low | **Засагдсан** | `auth.norm_mode`/`service_mode`/`is_shadow`: `create_app` горимыг нэг удаа нормчилно (`'Shadow '` → `shadow`; байхгүй/хоосон → `shadow`); api.py, flow.py, `analyst.sql_edit_enabled` бүгд нэг helper (өмнө нь `'Shadow '` үед `/analyst/rerun` нээлттэй байж болох байв). |
| S6 | Low | **Засагдсан** | Shadow горимд `/flow/job/{id}`-ийн `progress[].msg` хоосон (англи орчуулга, төлөв, мөрийн/хүснэгтийн тоо гарахгүй; алхам ба хугацаа UI-д үлдэнэ); алдаанд зөвхөн exception-ийн төрөл — дэлгэрэнгүй нь audit log-д. |
| S7 | Low | **Засагдсан** | dev биш горимд `FastAPI(openapi_url=None, docs_url=None, redoc_url=None)` — `/openapi.json`, `/docs`, `/redoc` → 404. |
| S8 | Low | **Засагдсан** | `/ask_job`, `/flow/run_job`-ийн `jobs` dict-ийг `jobs_lock` дор, хуулбар жагсаалтаар цэвэрлэнэ (зөвхөн дууссан job: 1 цагаас хуучин, эсвэл 200-аас олон үед хамгийн хуучин); ажлын thread өөрийн бичлэгийн reference-ийг шинэчилнэ (ажиллаж буй job-ийг устгахгүй, KeyError-гүй); `GET /job/{id}` түгжээн дор уншина. |

Үнэлгээнд нөлөөгүйг баталсан: `evalset/dev.jsonl` + `evalset/test.jsonl`-ийн бүх gold/alt/interpretation SQL (547)
дээр guard-ийг засварын өмнө ба дараа (`eval.goldcheck.make_guard`, Pipeline-ийн анхдагч guard, үйлчилгээний guard
`enforce_limit=1000, fail_closed=True`; `rewrite` асаалттай/унтраалттай) ажиллуулахад ok/code/error/дахин бичсэн
SQL/хүснэгт/баганын 3 282 харьцуулалтад ялгаа 0. Үнэлгээ/сургалтын бүх guard (`make_guard`, Pipeline-ийн анхдагч)
`enforce_limit=None` тул S4-ийн зам тэнд огт ажиллахгүй; S3-ийн deny-list зөвхөн `*_to_xml*` дуудсан SQL-д нөлөөлнө
(evalset-д байхгүй).

Хойшлуулсан (дараагийн алхам):
- Executor-ийг холболтын түвшинд read-only болгох — psycopg2 `conn.set_session(readonly=True)` ба pool-ийн `minconn`
  тохиргоо (`db/executor.py`). Энэ долоо хоногийн V26 Kaggle үнэлгээ `db/executor.py`-г өөрчлөлтгүй ашиглаж байгаа тул
  тэр ажиллуулалтын **дараа** хийнэ (одоо: `BEGIN READ ONLY` + role-ийн `default_transaction_read_only`).
  → **Хийгдсэн (2026-10-31, үргэлж идэвхтэй):** `set_session(readonly=True)` + `SET TRANSACTION READ ONLY` (өмнө нь
  `BEGIN READ ONLY` psycopg2-ийн transaction дотор нөлөөгүй, зөвхөн role-оор хамгаалагдаж байсан); tests/test_executor_readonly.py.
- Бодит хэрэглэгчийн нэвтрэлт (банкны SSO — OIDC/SAML, RBAC) — A1/A2 хэвээр; одоогийн нэг нийтлэг түлхүүр
  (`T2S_WEB_KEY`) ба localhost-only горим нь pilot-ын өмнөх түр шийдэл.

## 4. Сайн хэрэгжсэн хэсгүүд (хамгаалалтад дурдах)

- Defense in depth: guard (`sql_guard.py`) + `set_session(readonly=True)` + `SET TRANSACTION READ ONLY` (2026-10-31; өмнө нь
  `BEGIN READ ONLY` psycopg2-ийн transaction дотор нөлөөгүй, зөвхөн role-оор хамгаалагдаж байсан) + `SET LOCAL statement_timeout`
  (`executor.py` `_conn`/`_begin`) +
  role-ийн `default_transaction_read_only`, `lock_timeout`, `temp_file_limit`, `search_path=t2s` (`110_roles_rls.sql:36-48`).
- `REVOKE CREATE/TEMP`, аюултай системийн функцийг PUBLIC-ээс хураасан (`110_roles_rls.sql:51-57,178-188`).
- RLS нь `session_user`-д суурилсан, `FORCE ROW LEVEL SECURITY`, SECURITY DEFINER функц `search_path = pg_catalog`.
- View давхарга: регистр, утас, хаягийн дэлгэрэнгүй ороогүй (data minimization).
- P2SQL тест (кирилл + латин) давхарга бүрээр (`security/injection.py`).
- Shadow → pilot → live; итгэл, abstain/clarify, intent check (PII/бичих хүсэлтийг татгалзах).
- Audit шифрлэлт (Fernet), хадгалах хугацаа; feedback → хүн хянах candidate (автоматаар жишээнд нэмэхгүй).
- On-prem open-weight загвар (vLLM, `localhost`) — өгөгдөл гадагш гарахгүй боломжтой.

## 5. Production-д нэвтрүүлэх зам (санал)

1. **Нэвтрэлт**: SSO + RBAC (analyst / branch_analyst / admin / auditor); түлхүүрийг URL-ээс гаргах (A1–A4).
2. **Хэрэглэгч → DB role**: RLS-ийг бодитоор идэвхжүүлэх (D1); PII-г role-оор mask (D2).
3. **Studio-г тусгаарлах**: builder/logs/flows-ийг админ-only, тусдаа порт/процесс (A5, A6, S1).
4. **Audit**: SQL-ийг үргэлж хадгалах, HMAC, append-only + SIEM, `pgaudit`, загвар/prompt хувилбар (S2–S6).
5. **Guard**: функцийн allow-list, хоосон allow-list-д fail-closed, `SafeExecutor` (G1, G2, G5).
6. **Квот**: rate limit, queue, EXPLAIN өртгийн калибровк (R1, D4).
7. **MRM**: загварын бүртгэл (model card), бие даасан баталгаажуулалт, canary асуулт, drift dashboard (§2.6, §2.8).
8. **Эрх зүй**: compliance/хуулийн хэлтэстэй ХХМХ хуулийн зөвшөөрлийн үндэслэл, хадгалах хугацаа, Монголбанкны журмыг
   баталгаажуулах (§2.5 — одоогоор баталгаагүй).

## 6. Ном зүй (docs/research_refs.bib-д нэмсэн түлхүүрүүд)

owasp_2025_llm_top10, pedro_2023_p2sql, greshake_2023_indirect, pg_docs_rls, pg_docs_rules_privileges,
fed_2011_sr117, nist_2023_ai_rmf, mn_2021_pdp_law, pwc_2022_mn_pdp, mn_2021_cyber_law.

Онлайн эх сурвалж (2026-10-30-нд хайсан):
- PwC Mongolia, «Mongolia introduces Personal Data Protection Law effective from 1 May 2022» — https://www.pwc.com/mn/en/tax_alerts/tax_alert_02_2022.html
- DLA Piper, Data Protection in Mongolia — https://www.dlapiperdataprotection.com/?c=MN&t=law
- legalinfo.mn (албан бус англи орчуулга) — https://legalinfo.mn/en/edtl/16532053734461
- PostgreSQL docs, Rules and Privileges (security_barrier) — https://www.postgresql.org/docs/current/rules-privileges.html
- Cybertec, «When is a function leakproof» — https://www.cybertec-postgresql.com/en/when-is-a-function-leakproof/
- OWASP Top 10 for LLM Applications 2025 (хоёрдогч тойм: invicti.com, coralogix.com) — албан PDF-ийг шууд уншаагүй.
