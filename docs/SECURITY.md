# Аюулгүй байдал (зааврын 7-р хэсэг)

## Давхаргууд

1. **SQL guard (`security/sql_guard.py`, sqlglot).**
   - Зөвхөн нэг `SELECT`/`WITH` statement зөвшөөрнө.
   - DDL/DML/COPY/SET/транзакц/PREPARE/DO/`SELECT INTO`/`FOR UPDATE` хориотой.
   - Хориотой функцууд: `pg_*`, `lo_*`, `dblink*`, `query_to_*`, `xml*`, `*_to_xml*`, `file_*`, `txid_*`, `set_config`,
     `current_setting`, `version`, `pg_sleep*`, advisory lock; SQL-ийг текстээр авч гүйцэтгэдэг `ts_stat`, `ts_rewrite`,
     `crosstab*`, `connectby`, `xpath_table` (2026-10-31).
   - Зөвхөн `t2s` схемийн view-д хандана. Хүснэгт/alias-аар тодорхойлсон баганыг allow-list-ээр шалгана.
   - Огноо хамааралтай илэрхийллийг (`CURRENT_DATE`, `now()`, `'today'::date` г.м.) өгөгдлийн «өнөөдөр» болгож дахин
     бичнэ.
   - Үйлчилгээний горимд `LIMIT` хүчилнэ.
2. **Гүйцэтгэгч (`db/executor.py`).** Холболт бүр `set_session(readonly=True)` + транзакц бүрт `SET TRANSACTION READ ONLY`
   (2026-10-31; өмнө нь `BEGIN READ ONLY` psycopg2-ийн transaction дотор нөлөөгүй, зөвхөн role-оор хамгаалагдаж байсан),
   `SET LOCAL statement_timeout` (20 с) ашиглана.
   EXPLAIN-ийн өртгийн дээд хязгаар (`db.max_explain_cost`) ба мөрийн хязгаар тавина.
3. **DB role (`db_t2s/110_roles_rls.sql`).**
   - `t2s_bot`: LOGIN, superuser биш, BYPASSRLS-гүй. `default_transaction_read_only`, statement/lock/idle timeout,
     `search_path = t2s`, `temp_file_limit` тохируулсан.
   - Зөвхөн `t2s` view-д SELECT эрхтэй. Үндсэн схемд (`core`, `loan` ...) эрхгүй.
   - PUBLIC-ээс `CREATE`/`TEMPORARY` болон хортой функцийн EXECUTE эрхийг хассан.
4. **RLS.** Салбарын мөртэй 10 хүснэгтэд `ENABLE + FORCE`. Хэрэглэгчийг `session_user`-ээр тодорхойлно (SET ROLE-оор
   солигдохгүй). `t2s_acl.role_scope`-д role тус бүрийн харах салбарыг тодорхойлно (жишээ: `t2s_branch_demo` → UB01).
5. **PII.** `t2s` view-д регистр, утас, хаяг, дансны дугаар, харьцагч, чөлөөт текст тэмдэглэл ордоггүй.
   Харилцагчийн нэр (`customer_name`) нь semantic model-д `pii: true` тэмдэгтэй тул утгын индексэд ордоггүй.
6. **Audit (`service/api.py`).** Хүсэлт бүрийг бүртгэнэ: хэрэглэгчийн hash, SQL, төлөв, хүснэгт, мөрийн тоо, хугацаа.
   Асуулт ба feedback-ийн текстийг `T2S_AUDIT_KEY` (Fernet) түлхүүрээр шифрлэнэ. Түлхүүргүй үед текстийг хадгалахгүй,
   зөвхөн hash үлдэнэ. Хадгалах хугацаа нь `security.audit_retention_days` (90 хоног).

## Асуултын урьдчилсан шалгалт (`pipeline/intent_check.py`)

`pipeline.intent_check: true` үед (service-д анхдагчаар асаалттай) хоёр шалгалт хийнэ:
- **Татгалзах.** Хувийн мэдээлэл (регистр, утас, хаяг, нууц үг ...) эсвэл өгөгдөл өөрчлөх (устга, шинэчил ...)
  хүсэлтэд LLM дуудахгүй, монгол тайлбартайгаар татгалзана.
- **Тодруулах.** Glossary-д олон утгатай гэж тэмдэглэсэн нэр томьёог (`ambiguous.triggers`) тодотголгүй
  (`qualifiers`) хэрэглэвэл хувилбаруудыг жагсааж тодруулга асууна.

Кирилл ба галиг хоёуланг шалгана. Туршилтын run-д анхдагчаар унтраалттай (загварын зан төлөвийг хэмжинэ).
`--set intent_check=true`-ээр TrustSQL, хариулах боломжгүй/хоёрдмол асуултын хэмжүүрт үзүүлэх нөлөөг хэмжиж болно.
Татгалзах дүрмийн хувилбар `intent_rules` (анхдагч `legacy`; WEB/WEBA-д `v2`) — доорх «Вэбийн хамгаалалт (2026-10-31)».

## P2SQL тест

`t2s security-test` (`security/injection.py`) нь дараах шалгалтыг хийнэ:
- **SQL халдлага (28).** Guard, гүйцэтгэгч, шууд bot холболт (defense in depth) гэсэн давхарга тус бүрд тусад нь шалгана.
- **Injection асуулт (56).** Монгол кирилл ба латин бичгээр, зааврыг нь дагадаг «хортой» mock LLM-тэй бүрэн
  pipeline-аар ажиллуулна.
- **RLS.** Салбарын role өөр салбар харахгүй. `WHERE true OR …` болон `SET ROLE` оролдлого бүтэлгүйтнэ.

Шалгуур: бүх халдлага дор хаяж нэг давхаргад хаагдсан байна. Бичих, систем, файл, role-ийн халдлага DB давхаргад ч
хаагдсан байна. Одоогийн дүн: бүгд хаагдсан. `setup.sh` нь тестийг автоматаар ажиллуулж, бүтэлгүйтвэл зогсоно.

## Shadow → pilot

`config/default.yaml`-ийн `service.mode` гурван утгатай:
- `shadow`: хариултыг хэрэглэгчид буцаахгүй, зөвхөн audit-д хадгална. Одоогийн процесстой зэрэгцүүлж харьцуулна.
- `pilot` / `live`: хариулт (SQL, үр дүн, ашигласан хүснэгт, монгол тайлбар, итгэл) буцаана.

`POST /feedback` нь шинжээчийн үнэлгээг хадгална. Засварласан SQL-ийг guard-аар шалгана. Хянасны дараа жишээний санд
нэмнэ (feedback loop).

## Вэбийн хамгаалалт (2026-10-31)

- **Flow-ийн тохиргоо (`service/flow.py`).** Замын төрлийн сонголт (`glossary_file`, `glossary_select_cache`, `index_dir`) —
  зөвхөн `knowledge/` эсвэл `build/` доторх харьцангуй зам (абсолют, `..`, `~`, `\` хориотой; өргөтгөл тогтмол; буруу бол
  400). Сервер ачаалахаас өмнө symlink задалсан бодит зам нь тэдгээр хавтсанд, энгийн файл (FIFO/төхөөрөмж биш), хэмжээ
  ≤ 5 MB (`glossary_file`) / 20 MB (`glossary_select_cache`); `index_dir`-ийн индексийн файлууд мөн энгийн файл, `meta.json`
  ≤ 1 MB.
  Тэнцээгүй бол анхдагч руу буцаж тэмдэглэл гаргана. Нэрийн талбар (`translation_cache`, `adapter`, `agent_llm`) ба enum
  (`fewshot_rerank`, `adaptive_stop`)-ыг шалгана — буруу бол 400.
- **Ажлын хэмжээ.** Хэрэглэгчийн утгыг хумиж анхааруулна: `candidates` ≤ 16, `repair_rounds`/`agent_rounds` ≤ 5,
  `agent_max_steps` ≤ 8, `repair_workers` ≤ 8, `fewshot_k` ≤ 10, `glossary_k` ≤ 20, `select_pool`/`top_tables` ≤ 50,
  `seeds` ≤ 16 (experiments.yaml-ийн утгууд бүгд мужийн дотор). Graph ≤ 50 node, ≤ 100 сум.
- **Хүсэлт.** Биеийн хэмжээ ≤ 256 KB (`service.max_body_bytes`; хэтэрвэл 413). Хурдны хязгаар (процесс даяар): `/flow/compile`
  10/с (burst 30), `/feedback` 2/с (burst 20) — хэтэрвэл 429.
- **Үр дүнгийн хэмжээ (`db/executor.py`).** `service.max_result_bytes` (config/web.yaml-д 5 MB; 0 — унтраах): вэбийн
  Executor server-side cursor-оор мөр мөрөөр татаж, санах ойн ойролцоо хэмжээ хэтэрмэгц `too_large` алдаа буцаана
  (үнэлгээ үүнийг ашигладаггүй). Үлдэх эрсдэл: нэг мөр PostgreSQL-ийн ~1 GB (утга/мөрийн) хязгаар хүртэл том байж болно —
  төсөв нь түүнийг татсаны дараа л шалгагдана.
- **Алдааны мессеж.** Exception-ий текст (DB хаяг, дотоод зам) клиентэд очихгүй — request id-тай ерөнхий мессеж, дэлгэрэнгүй
  нь лог/audit-д. `/health` түлхүүргүй хүсэлтэд зөвхөн `{"ok": true}` (горим, run, загварын нэр — хандах эрхтэйд л).
- **flow-ui (`t2s flow-ui`).** API-тай ижил итгэмжлэгдсэн Host/түлхүүрийн шалгалт; T2S_WEB_KEY-гүй бол 127.0.0.1-д
  холбогдоно. Түлхүүргүй үед (API ба flow-ui) эхлэхэд анхааруулна: портыг өөр машинаас хүрэх боломжтой болгох БҮХ
  тохиолдолд (ssh -R/-L, socat, ngrok tcp, kubectl port-forward г.м.) T2S_WEB_KEY заавал — түүхий TCP forwarder нь
  түлхүүргүй үеийн Host шалгалтыг давдаг.
- **SQL guard.** `ts_stat`, `ts_rewrite`, `crosstab*`, `connectby`, `xpath_table` хориотой (SQL-ийг текстээр авч гүйцэтгэдэг
  тул allow-list, as-of дахин бичилт, EXPLAIN-ийн шалгалтыг тойрно). DB түвшинд (2026-11-01, `db_t2s/110_roles_rls.sql`):
  `ts_stat(text)`, `ts_stat(text, text)`, `ts_rewrite(tsquery, text)` ба `table/schema/database/cursor_to_xml*`-ийг PUBLIC-ээс
  хураана (өмнө нь `query_to_xml*`, `pg_sleep*`, `set_config`, advisory lock); PG-д байхгүй гарын үсгийг `to_regprocedure`-оор
  алгасна. Bot role-д үйлчилж буйг Kaggle-ийн `secdb` алхам (`scripts/dev/db_revoke_check.py`) шалгана;
  tests/test_db_revoke_static.py. **Батлагдсан (2026-11-01, V29, Kaggle-ийн PG):** bot role-д `ts_stat`, `ts_rewrite`,
  `table/query/database_to_xml`, `pg_sleep` бүгд «permission denied»; server-side cursor-ийн хязгаар (ижил мөр, too_large)
  ба READ ONLY-д бичих хориг ажилласан.
- **intent_check v2.** `intent_rules: v2` (WEB/WEBA үйлчилгээний тохиргоонд асаалттай; анхдагч `legacy` хэвээр) нь мэдлэгийн
  сангийн шошгыг (glossary-ийн нэр томьёо, semantic_model-ийн хүснэгт/багана/enum-ийн нэр) маскжуулж, бичих үйл үгийн
  ХҮСЭЛТИЙН хэлбэрийг (тушаах, «-ж өг», «-на уу» г.м.) таньна — нөхцөл/өнгөрсөн хэлбэрийг биш. Иймээс «бүтэц өөрчилсөн
  зээл», «Имэйл сануулга», "registered" зэрэг домэйны нэр томьёо татгалзагдахаа больсон.
- **Read-only гүйцэтгэгч.** `set_session(readonly=True)` + транзакц бүрт `SET TRANSACTION READ ONLY` — үргэлж асаалттай
  («Давхаргууд» 2).
