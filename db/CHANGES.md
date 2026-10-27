# DB-ийн хуулбарт хийсэн өөрчлөлт

`db/` нь `text2sql-thesis/database/`-ийн **хуулбар**. Эх төслийг өөрчлөөгүй. Хуулбарыг ачаалахад гарсан алдааг
энд, хамгийн бага өөрчлөлтөөр зассан. Засвар бүрийн мөрөнд `text2sql-new засвар` гэсэн тайлбар бий.

## Засварласан алдаа

| # | Файл | Асуудал | Засвар |
|---|---|---|---|
| 1 | `migrations/005_product.sql` | `product_fees.calc_method VARCHAR(20)`-д `'PERCENT_OF_OUTSTANDING'` (22 тэмдэгт) багтахгүй | `VARCHAR(30)` |
| 2 | `migrations/006_loan.sql` | `loan_overdues.dpd_bucket VARCHAR(10)`-д `'DPD_180_PLUS'` (12) багтахгүй | `VARCHAR(20)` |
| 3 | `migrations/006_loan.sql` | `loan_assessments.recommendation VARCHAR(20)`-д `'APPROVE_WITH_CONDITIONS'` (23) багтахгүй | `VARCHAR(30)` |
| 4 | `migrations/004_kyc.sql` | `aml_alerts.status VARCHAR(20)`-д `'CLOSED_FALSE_POSITIVE'` (21) багтахгүй | `VARCHAR(30)` |
| 5 | `migrations/012_risk.sql` | `risk_grades.grade_code` нь `system.code_key` домэйнтой; `A`, `B`, `C` гэх мэт 1 тэмдэгттэй код домэйны шалгалтыг давахгүй | `VARCHAR(20) CHECK (grade_code ~ '^[A-Z][A-Z0-9_]{0,19}$')` |
| 6 | `migrations/016_seed.sql` | Нягтлан бодох бүртгэлийн үеийг `CLOSED` төлөвтэй оруулдаг. `ck_period_closed` (`closed_at/closed_by` шаардана) зөрчигдөнө | `OPEN` төлөвтэй оруулна. Хаалтыг generator `_post_sql`-д хийнэ (өнөөдрөөс өмнөх сар `CLOSED`, одоогийн сар `OPEN`, ирээдүйн сар `FUTURE`) |
| 7 | `seeds/001_demo_data.sql` | ENHANCED KYC-д `source_of_wealth` хоосон; `(i - 40)::NUMERIC * 850000000` integer overflow; `loan_payments`-ийн `unallocated_amount` CHECK зөрчигдөнө | Утга нэмсэн, NUMERIC-ээр үржүүлсэн, `unallocated_amount = amount` |
| 8 | `functions/003_accounting_functions.sql` | `fn_post_entry` нь POSTED болгохоос өмнө `total_debit/total_credit`-ийг шинэчилдэггүй. Deferred тэнцлийн trigger ба хамгаалалтын trigger зөрчилдөнө | POSTED болгохоос өмнө мөрүүдээс нийлбэрийг тооцно |

Засварын дараа эх DB, demo seed бүрэн ачаалагдаж, `db/validation` 22/22 давна (`nbfi_raw`). Туршилтын DB (`nbfi`)
нь эх схем дээр синтетик generator-оор (`src/mn_text2sql/datagen`) үүсгэгдэнэ: seed 42, scale 1.0,
"өнөөдөр" = 2026-06-30. Test-suite хуулбаруудын хувьд (seed 1001–1008) generator-т нэг ирмэгийн засвар орсон:
хаагдсан салбарын ажилтан хаагдсанаас хойш ажилд орсон бол чөлөөлөгдсөн огноо = ажилд орсон огноо
(`ck_employees_term`). Энэ засвар RNG ашигладаггүй тул үндсэн DB-ийн агуулга өөрчлөгдөөгүй (hash-аар
баталгаажсан).

## Мэдэгдэж буй, засаагүй асуудал (баримтжуулсан)

- **`CURRENT_DATE` ашигладаг DB функцууд.** `fn_recalculate_balances`, `fn_allocate_payment` гэх мэт функц өнөөдрийн
  огноогоор тооцдог. Туршилтын өгөгдөлд эдгээрийг ашиглаагүй: generator тооцоогоо өөрөө хийж COPY-оор ачаална.
- **`fn_allocate_payment`-ийн INTEREST алхам.** Ирээдүйн хуваарийн хүүг үндсэн төлбөрөөс өмнө төлдөг. Generator-ийн
  хуваарилалт ийм биш: PENALTY → FEE → OVERDUE_INTEREST → INTEREST (хугацаа болсон) → PRINCIPAL.
- **Эх тайлангийн view-үүд.** PAR-ыг `total_outstanding`-ээр тооцдог. Text2SQL-ийн давхарга (`db_t2s/`) ба glossary
  нь PAR-ыг үндсэн үлдэгдлээр (`principal_outstanding_mnt`) тодорхойлдог.

## Text2SQL давхарга (шинэ, эх схемд нөлөөлөхгүй)

- `db_t2s/100_t2s_views.sql`: `t2s` схемд bot-д зориулсан 28 view (хувийн мэдээлэлгүй). Мөн `t2s.business_date()`,
  `t2s.fx_to_mnt()` функцууд.
- `db_t2s/110_roles_rls.sql`: `t2s_reader` (view эзэмшигч), `t2s_bot` (read-only, timeout, search_path=t2s),
  `t2s_branch_demo` (RLS жишээ), RLS `ENABLE + FORCE`, `t2s_acl.role_scope`. PUBLIC-ээс хориотой функцийн эрхийг
  хассан (`pg_sleep*`, `set_config`, `query_to_xml*`, `pg_advisory_lock*`).
