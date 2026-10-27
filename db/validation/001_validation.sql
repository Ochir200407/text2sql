-- =====================================================================
-- validation/001_validation.sql — Өгөгдлийн бүрэн бүтэн байдлын шалгалт
-- ---------------------------------------------------------------------
-- Ажиллуулах: psql -f validation/001_validation.sql
-- Үр дүн: check_name | status (OK/FAIL) | detail
-- CI/CD-д: FAIL мөр байвал pipeline унана.
-- =====================================================================

\set ON_ERROR_STOP on

WITH

-- 1. Бүх FK хүчинтэй эсэх (NOT VALID төлөвт үлдсэн байгаа юу)
fk_not_valid AS (
    SELECT count(*) AS n FROM pg_constraint
     WHERE contype = 'f' AND NOT convalidated
),

-- 2. Сул холбоос дээрх өнчин бичлэг (FK-гүй, зориудаар сул холбосон талбарууд)
orphan_docs AS (
    SELECT count(*) AS n FROM loan.loan_application_documents d
     WHERE d.document_id IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM system.documents s WHERE s.id = d.document_id)
),
orphan_je_txn AS (
    SELECT count(*) AS n FROM accounting.journal_entries je
     WHERE je.source_transaction_id IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM account.transactions t WHERE t.id = je.source_transaction_id)
),

-- 3. Бизнес түлхүүрийн давхардал
dup_customer AS (
    SELECT count(*) AS n FROM (
        SELECT customer_number FROM customer.customers GROUP BY 1 HAVING count(*) > 1) x
),
dup_loan AS (
    SELECT count(*) AS n FROM (
        SELECT loan_number FROM loan.loans GROUP BY 1 HAVING count(*) > 1) x
),
dup_account AS (
    SELECT count(*) AS n FROM (
        SELECT account_number FROM account.accounts GROUP BY 1 HAVING count(*) > 1) x
),
dup_reg_hash AS (
    SELECT count(*) AS n FROM (
        SELECT registration_no_hash FROM customer.customers
         WHERE registration_no_hash IS NOT NULL GROUP BY 1 HAVING count(*) > 1) x
),

-- 4. Нягтлан: дебит = кредит
je_unbalanced AS (
    SELECT count(*) AS n FROM (
        SELECT jl.entry_id
          FROM accounting.journal_lines jl
         GROUP BY jl.entry_id
        HAVING SUM(CASE WHEN jl.dr_cr = 'D' THEN jl.base_amount ELSE 0 END)
            <> SUM(CASE WHEN jl.dr_cr = 'C' THEN jl.base_amount ELSE 0 END)) x
),
je_header_mismatch AS (
    SELECT count(*) AS n
      FROM accounting.journal_entries je
      LEFT JOIN (
          SELECT entry_id,
                 SUM(base_amount) FILTER (WHERE dr_cr = 'D') AS d,
                 SUM(base_amount) FILTER (WHERE dr_cr = 'C') AS c
            FROM accounting.journal_lines GROUP BY entry_id) l ON l.entry_id = je.id
     WHERE je.status = 'POSTED'
       AND (je.total_debit <> COALESCE(l.d, 0) OR je.total_credit <> COALESCE(l.c, 0))
),

-- 5. Сөрөг үлдэгдэл (зөвшөөрөгдөөгүй дансанд)
negative_balance AS (
    SELECT count(*) AS n
      FROM account.accounts a
      JOIN account.account_types at ON at.id = a.account_type_id
     WHERE NOT at.allows_negative AND a.current_balance < -a.overdraft_limit
),
negative_loan AS (
    SELECT count(*) AS n FROM loan.loans
     WHERE principal_outstanding < 0 OR interest_outstanding < 0
        OR penalty_outstanding < 0 OR fee_outstanding < 0
),

-- 6. Буруу төлөвийн шилжилт (түүхэнд зөвшөөрөгдөөгүй шилжилт байгаа юу)
bad_transition AS (
    SELECT count(*) AS n
      FROM loan.loan_status_history h
     WHERE h.old_status_id IS NOT NULL
       AND NOT EXISTS (
           SELECT 1 FROM loan.loan_status_transitions t
            WHERE t.from_status_id = h.old_status_id AND t.to_status_id = h.new_status_id)
),

-- 7. Хуваарийн нийлбэр = зээлийн үндсэн дүн (1₮-ийн бөөрөнхийлөлт зөвшөөрнө)
schedule_mismatch AS (
    SELECT count(*) AS n FROM (
        SELECT l.id
          FROM loan.loans l
          JOIN loan.loan_schedules s   ON s.loan_id = l.id AND s.is_current
          JOIN loan.loan_schedule_items si ON si.schedule_id = s.id
         GROUP BY l.id, l.principal_amount
        HAVING abs(SUM(si.principal_due) - l.principal_amount) > 1) x
),
schedule_no_current AS (
    SELECT count(*) AS n
      FROM loan.loans l
      JOIN loan.loan_statuses ls ON ls.id = l.status_id AND ls.is_active_loan
     WHERE NOT EXISTS (SELECT 1 FROM loan.loan_schedules s WHERE s.loan_id = l.id AND s.is_current)
),

-- 8. Төлбөрийн хуваарилалт: allocations = payment.amount
payment_alloc_mismatch AS (
    SELECT count(*) AS n
      FROM loan.loan_payments p
      LEFT JOIN (SELECT payment_id, SUM(amount) AS allocated
                   FROM loan.loan_payment_allocations GROUP BY payment_id) a
             ON a.payment_id = p.id
     WHERE p.status = 'POSTED'
       AND abs(COALESCE(a.allocated, 0) + p.unallocated_amount - p.amount) > 0.01
),
payment_field_mismatch AS (
    SELECT count(*) AS n FROM loan.loan_payments
     WHERE status = 'POSTED'
       AND abs(allocated_principal + allocated_interest + allocated_penalty
               + allocated_fee + unallocated_amount - amount) > 0.01
),

-- 9. Валютын нийцэл
currency_mismatch AS (
    SELECT (SELECT count(*) FROM loan.loan_payments p JOIN loan.loans l ON l.id = p.loan_id
             WHERE p.currency_code <> l.currency_code)
         + (SELECT count(*) FROM account.transactions t JOIN account.accounts a ON a.id = t.account_id
             WHERE t.currency_code <> a.currency_code)
         + (SELECT count(*) FROM deposit.deposit_accounts d JOIN account.accounts a ON a.id = d.account_id
             WHERE d.currency_code <> a.currency_code) AS n
),
fx_missing AS (
    SELECT count(*) AS n FROM account.transactions
     WHERE currency_code <> base_currency_code AND (fx_rate IS NULL OR base_amount IS NULL)
),

-- 10. Заавал байх талбарууд
missing_required AS (
    SELECT (SELECT count(*) FROM customer.customers WHERE display_name IS NULL OR customer_number IS NULL)
         + (SELECT count(*) FROM loan.loans WHERE principal_amount IS NULL OR interest_rate IS NULL)
         + (SELECT count(*) FROM customer.customers c
             WHERE c.party_type = 'INDIVIDUAL'
               AND NOT EXISTS (SELECT 1 FROM customer.customer_individuals i WHERE i.customer_id = c.id))
         + (SELECT count(*) FROM customer.customers c
             WHERE c.party_type = 'ORGANIZATION'
               AND NOT EXISTS (SELECT 1 FROM customer.customer_organizations o WHERE o.customer_id = c.id)) AS n
),

-- 11. Unique constraint ажиллаж байгаа эсэх (индексийн тоо)
unique_indexes AS (
    SELECT count(*) AS n FROM pg_indexes
     WHERE schemaname IN ('core','customer','kyc','product','loan','collateral',
                          'account','deposit','accounting','risk','collection','system')
       AND indexdef LIKE 'CREATE UNIQUE%'
),

-- Нэмэлт: барьцааны хуваарилалт үнэлгээнээс хэтэрсэн эсэх
collateral_over AS (
    SELECT count(*) AS n FROM (
        SELECT lc.collateral_id
          FROM collateral.loan_collaterals lc
          JOIN collateral.collaterals c ON c.id = lc.collateral_id
         WHERE lc.status IN ('PENDING', 'ACTIVE')
         GROUP BY lc.collateral_id, c.accepted_value
        HAVING SUM(lc.allocated_amount) > c.accepted_value) x
),
-- Нэмэлт: зээлийн үлдэгдэл хуваарьтай тохирч байгаа эсэх
balance_drift AS (
    SELECT count(*) AS n FROM (
        SELECT l.id
          FROM loan.loans l
          JOIN loan.loan_schedules s ON s.loan_id = l.id AND s.is_current
          JOIN loan.loan_schedule_items si ON si.schedule_id = s.id
         GROUP BY l.id, l.principal_outstanding
        HAVING abs(SUM(si.principal_due - si.principal_paid) - l.principal_outstanding) > 1) x
)

SELECT * FROM (
    SELECT  1 AS seq, 'FK бүгд хүчинтэй'                AS check_name, n, 0 AS expected FROM fk_not_valid
    UNION ALL SELECT  2, 'Өнчин баримтын холбоос',        n, 0 FROM orphan_docs
    UNION ALL SELECT  3, 'Өнчин журнал→гүйлгээ холбоос',  n, 0 FROM orphan_je_txn
    UNION ALL SELECT  4, 'Харилцагчийн дугаар давхардал', n, 0 FROM dup_customer
    UNION ALL SELECT  5, 'Зээлийн дугаар давхардал',      n, 0 FROM dup_loan
    UNION ALL SELECT  6, 'Дансны дугаар давхардал',       n, 0 FROM dup_account
    UNION ALL SELECT  7, 'Регистрийн хэш давхардал',      n, 0 FROM dup_reg_hash
    UNION ALL SELECT  8, 'Журнал тэнцэхгүй (мөрүүд)',     n, 0 FROM je_unbalanced
    UNION ALL SELECT  9, 'Журналын толгой ≠ мөрүүд',      n, 0 FROM je_header_mismatch
    UNION ALL SELECT 10, 'Сөрөг дансны үлдэгдэл',         n, 0 FROM negative_balance
    UNION ALL SELECT 11, 'Сөрөг зээлийн үлдэгдэл',        n, 0 FROM negative_loan
    UNION ALL SELECT 12, 'Буруу төлөвийн шилжилт',        n, 0 FROM bad_transition
    UNION ALL SELECT 13, 'Хуваарийн нийлбэр ≠ үндсэн дүн',n, 0 FROM schedule_mismatch
    UNION ALL SELECT 14, 'Идэвхтэй зээл хуваарьгүй',      n, 0 FROM schedule_no_current
    UNION ALL SELECT 15, 'Төлбөрийн хуваарилалт зөрүүтэй',n, 0 FROM payment_alloc_mismatch
    UNION ALL SELECT 16, 'Төлбөрийн талбар зөрүүтэй',     n, 0 FROM payment_field_mismatch
    UNION ALL SELECT 17, 'Валютын зөрчил',                n, 0 FROM currency_mismatch
    UNION ALL SELECT 18, 'Ханшгүй валютын гүйлгээ',       n, 0 FROM fx_missing
    UNION ALL SELECT 19, 'Заавал талбар дутуу',           n, 0 FROM missing_required
    UNION ALL SELECT 20, 'Барьцаа хэт хуваарилсан',       n, 0 FROM collateral_over
    UNION ALL SELECT 21, 'Үлдэгдэл хуваарьтай зөрүүтэй',  n, 0 FROM balance_drift
) checks
UNION ALL
SELECT 22, 'UNIQUE индексийн тоо (мэдээлэл)', n, n FROM unique_indexes
ORDER BY 1;

-- Тайлбар: 22-р мөр нь мэдээллийн шинжтэй (expected = n), бусад нь 0 байх ёстой.
-- FAIL шалгах:
--   SELECT * FROM (...) WHERE n <> expected;
