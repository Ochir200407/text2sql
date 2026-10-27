-- =====================================================================
-- seeds/001_demo_data.sql — DEMO өгөгдөл (development / test орчинд)
-- ---------------------------------------------------------------------
-- ⚠ PRODUCTION-д АЖИЛЛУУЛАХГҮЙ.
-- Агуулга: 20 ажилтан, 21 хэрэглэгч, 50 харилцагч, 60 данс, 20 зээл,
--          хуваарь, төлөлт, журнал, барагдуулалтын кейс.
-- Бүх өгөгдөл хоорондоо УЯЛДААТАЙ: зээл → хуваарь → төлөлт → журнал.
-- Детерминист: setseed(0.42)
-- =====================================================================

\set ON_ERROR_STOP on

SELECT setseed(0.42);

-- ---------------------------------------------------------------------
-- 1. Ажилтан (20) ба хэрэглэгч
-- ---------------------------------------------------------------------
INSERT INTO core.employees (employee_no, first_name, last_name, branch_id, department_id, position_id,
                            email, phone, hire_date, employment_status)
SELECT 'E' || lpad(g::TEXT, 4, '0'),
       (ARRAY['Батбаяр','Оюунчимэг','Ганзориг','Сарантуяа','Мөнхбат','Энхжаргал','Türbat','Наранцэцэг',
              'Дорж','Цэцэгмаа','Ариунболд','Хулан','Ganbold','Сувдаа','Тэмүүлэн','Нарантуяа',
              'Эрдэнэбат','Одончимэг','Батзориг','Уранчимэг'])[g],
       (ARRAY['Дорж','Батсүх','Пүрэв','Цэрэн','Лхагва','Мягмар','Даваа','Ням','Бямба','Сүх',
              'Очир','Жаргал','Ганбат','Мөнх','Баяр','Төмөр','Алтан','Хишиг','Энх','Ундрам'])[g],
       b.id,
       d.id,
       p.id,
       'user' || g || '@nbfi.mn',
       '+976 99' || lpad((100000 + g * 137)::TEXT, 6, '0'),
       DATE '2020-01-01' + (g * 47),
       'ACTIVE'
  FROM generate_series(1, 20) g
  JOIN LATERAL (SELECT id FROM core.branches ORDER BY id LIMIT 1 OFFSET ((g - 1) % 5)) b ON TRUE
  JOIN LATERAL (SELECT id FROM core.departments ORDER BY id LIMIT 1 OFFSET ((g - 1) % 7)) d ON TRUE
  JOIN LATERAL (SELECT id FROM core.positions
                 WHERE position_code = CASE
                     WHEN g = 1 THEN 'CEO'
                     WHEN g <= 6 THEN 'BRANCH_MANAGER'
                     WHEN g <= 13 THEN 'CREDIT_OFFICER'
                     WHEN g <= 16 THEN 'TELLER'
                     WHEN g <= 18 THEN 'ACCOUNTANT'
                     ELSE 'COLLECTOR' END
                 LIMIT 1) p ON TRUE;

-- Менежерийн шатлал: салбарын менежерүүд CEO-д, бусад нь салбарынхаа менежерт
UPDATE core.employees e
   SET manager_id = m.id
  FROM core.employees m
 WHERE m.employee_no = 'E0001' AND e.employee_no <> 'E0001';

-- Хэрэглэгч: админ + ажилтан бүрд
-- Нууц үг: demo орчны "Demo@2026" -ийн argon2id хэш (жишээ утга; production-д апп үүсгэнэ)
INSERT INTO core.users (username, employee_id, password_hash, password_algo, email, status, must_change_password,
                        data_access_level)
SELECT lower(regexp_replace(e.first_name, '[^a-zA-Z]', '', 'g')) || e.employee_no,
       e.id,
       '$argon2id$v=19$m=65536,t=3,p=4$ZGVtb3NhbHQxMjM0NTY3OA$' || md5(e.employee_no),
       'ARGON2ID',
       e.email,
       'ACTIVE',
       TRUE,
       CASE WHEN e.employee_no IN ('E0001', 'E0002') THEN 'RESTRICTED'::system.data_sensitivity
            ELSE 'CONFIDENTIAL'::system.data_sensitivity END
  FROM core.employees e;

INSERT INTO core.users (username, password_hash, password_algo, email, status, must_change_password, data_access_level)
VALUES ('sysadmin', '$argon2id$v=19$m=65536,t=3,p=4$ZGVtb3NhbHQxMjM0NTY3OA$' || md5('admin'),
        'ARGON2ID', 'admin@nbfi.mn', 'ACTIVE', TRUE, 'RESTRICTED');

-- Role хуваарилалт албан тушаалаас
INSERT INTO core.user_roles (user_id, role_id)
SELECT u.id, r.id
  FROM core.users u
  LEFT JOIN core.employees e ON e.id = u.employee_id
  LEFT JOIN core.positions p ON p.id = e.position_id
  JOIN core.roles r ON r.role_code = CASE
        WHEN u.username = 'sysadmin' THEN 'SYS_ADMIN'
        WHEN p.position_code = 'CEO' THEN 'BRANCH_MANAGER'
        WHEN p.position_code = 'BRANCH_MANAGER' THEN 'BRANCH_MANAGER'
        WHEN p.position_code = 'CREDIT_OFFICER' THEN 'CREDIT_OFFICER'
        WHEN p.position_code = 'TELLER' THEN 'TELLER'
        WHEN p.position_code = 'ACCOUNTANT' THEN 'ACCOUNTANT'
        ELSE 'COLLECTOR' END;

INSERT INTO core.user_branch_access (user_id, branch_id, access_type)
SELECT u.id, e.branch_id, 'READ_WRITE' FROM core.users u JOIN core.employees e ON e.id = u.employee_id;

-- ---------------------------------------------------------------------
-- 2. Дотоод (техникийн) данс — салбар бүрд касс + suspense
-- ---------------------------------------------------------------------
INSERT INTO account.accounts (account_number, account_type_id, branch_id, currency_code, account_name,
                              current_balance, available_balance, opened_date, status)
SELECT b.gl_prefix || at.type_code || lpad(b.id::TEXT, 4, '0'),
       at.id, b.id, 'MNT',
       b.branch_name || ' — ' || at.type_name,
       CASE WHEN at.type_code = 'CASH_VAULT' THEN 500000000 ELSE 0 END,
       CASE WHEN at.type_code = 'CASH_VAULT' THEN 500000000 ELSE 0 END,
       b.opened_date, 'ACTIVE'
  FROM core.branches b
  CROSS JOIN account.account_types at
 WHERE at.type_code IN ('CASH_VAULT', 'SUSPENSE');

-- ---------------------------------------------------------------------
-- 3. Харилцагч (40 хувь хүн + 10 байгууллага)
-- ---------------------------------------------------------------------
DO $$
DECLARE
    v_cust_id  UUID;
    v_branch   BIGINT;
    v_officer  BIGINT;
    v_type     SMALLINT;
    v_status   SMALLINT := (SELECT id FROM customer.customer_statuses WHERE status_code = 'ACTIVE');
    v_segment  SMALLINT;
    v_name     TEXT;
    v_first    TEXT;
    v_last     TEXT;
    i          INTEGER;
    first_names TEXT[] := ARRAY['Болд','Сүрэн','Ганаа','Дулмаа','Батаа','Оюунаа','Түвшин','Алтанцэцэг',
                                'Мөнх-Эрдэнэ','Should','Баяраа','Сарнай','Хишигбат','Нандин','Тэмүүжин',
                                'Ичинхорлоо','Ганхуяг','Долгион','Батмөнх','Цэрэндолгор'];
    last_names  TEXT[] := ARRAY['Дорж','Цэрэн','Батаа','Пүрэв','Лхагва','Гомбо','Жамьян','Содном',
                                'Дамдин','Чойжил'];
    org_names   TEXT[] := ARRAY['Түшиг Трейд ХХК','Алтан Говь ХХК','Хөгжил Констракшн ХХК','Ирээдүй Фүүд ХХК',
                                'Сансар Логистик ХХК','Мандал Аутo ХХК','Эрдэнэс Майнинг ХХК','Ногоон Талбай ХХК',
                                'Дижитал Солюшн ХХК','Хүннү Текстайл ХХК'];
BEGIN
    FOR i IN 1 .. 50 LOOP
        SELECT id INTO v_branch FROM core.branches ORDER BY id LIMIT 1 OFFSET ((i - 1) % 5);
        SELECT e.id INTO v_officer FROM core.employees e
          JOIN core.positions p ON p.id = e.position_id
         WHERE p.position_code = 'CREDIT_OFFICER' ORDER BY e.id LIMIT 1 OFFSET ((i - 1) % 7);

        IF i <= 40 THEN
            v_first := first_names[((i - 1) % 20) + 1];
            v_last  := last_names[((i - 1) % 10) + 1];
            v_name  := v_last || ' ' || v_first;
            SELECT id INTO v_type FROM customer.customer_types
             WHERE type_code = CASE WHEN i % 7 = 0 THEN 'IND_PENSION'
                                    WHEN i % 5 = 0 THEN 'IND_BUSINESS' ELSE 'IND_SALARY' END;
            SELECT id INTO v_segment FROM customer.customer_segments
             WHERE segment_code = CASE WHEN i % 13 = 0 THEN 'VIP'
                                       WHEN i % 5 = 0 THEN 'PREMIUM'
                                       WHEN i % 3 = 0 THEN 'AFFLUENT' ELSE 'MASS' END;
        ELSE
            v_name := org_names[i - 40];
            SELECT id INTO v_type FROM customer.customer_types
             WHERE type_code = CASE WHEN i > 47 THEN 'ORG_CORP' ELSE 'ORG_SME' END;
            SELECT id INTO v_segment FROM customer.customer_segments
             WHERE segment_code = CASE WHEN i > 47 THEN 'CORPORATE' ELSE 'SME' END;
        END IF;

        INSERT INTO customer.customers (
            customer_number, party_type, customer_type_id, status_id, segment_id, branch_id,
            relationship_manager_id, display_name,
            registration_no_hash, registration_no_masked,
            kyc_status, kyc_verified_at, kyc_expires_at, aml_status, risk_level, onboarded_at
        ) VALUES (
            system.fn_next_number('CUSTOMER'),
            CASE WHEN i <= 40 THEN 'INDIVIDUAL'::system.party_type ELSE 'ORGANIZATION'::system.party_type END,
            v_type, v_status, v_segment, v_branch, v_officer, v_name,
            digest('REG' || lpad(i::TEXT, 8, '0'), 'sha256'),
            CASE WHEN i <= 40 THEN 'УБ**' || lpad((1000 + i)::TEXT, 4, '0')
                 ELSE '**' || lpad((5000000 + i)::TEXT, 7, '0') END,
            'VERIFIED', now() - (i || ' days')::INTERVAL, now() + INTERVAL '2 years',
            'CLEAR',
            CASE WHEN i % 17 = 0 THEN 'HIGH' WHEN i % 4 = 0 THEN 'MEDIUM' ELSE 'LOW' END,
            now() - ((300 - i * 5) || ' days')::INTERVAL
        ) RETURNING id INTO v_cust_id;

        IF i <= 40 THEN
            INSERT INTO customer.customer_individuals (
                customer_id, last_name, first_name, birth_date, gender, marital_status,
                occupation, employer_name, employment_type, monthly_income, id_document_type,
                id_issued_date, id_expiry_date
            ) VALUES (
                v_cust_id, v_last, v_first,
                DATE '1995-01-01' - ((i * 137) || ' days')::INTERVAL,
                CASE WHEN i % 2 = 0 THEN 'FEMALE'::system.gender_type ELSE 'MALE'::system.gender_type END,
                CASE WHEN i % 3 = 0 THEN 'SINGLE' ELSE 'MARRIED' END,
                (ARRAY['Инженер','Багш','Нягтлан','Жолооч','Менежер','Худалдагч','Эмч','Программист'])[((i - 1) % 8) + 1],
                (ARRAY['Эрдэнэт Үйлдвэр','Боловсролын газар','Голомт банк','Тээвэр ХХК','АПУ ХК'])[((i - 1) % 5) + 1],
                CASE WHEN i % 7 = 0 THEN 'RETIRED' WHEN i % 5 = 0 THEN 'SELF_EMPLOYED' ELSE 'EMPLOYED' END,
                1200000 + (i % 12) * 450000,
                'NATIONAL_ID', DATE '2020-03-15', DATE '2030-03-15'
            );
        ELSE
            INSERT INTO customer.customer_organizations (
                customer_id, legal_name, legal_form, industry_code, establishment_date,
                employee_count, annual_revenue, tax_registration_no, ultimate_owner_name
            ) VALUES (
                v_cust_id, v_name,
                CASE WHEN i > 47 THEN 'JSC' ELSE 'LLC' END,
                (ARRAY['G47','C10','F41','H49','J62'])[((i - 41) % 5) + 1],
                DATE '2010-01-01' + ((i - 40) * 365),
                (i - 40) * 12,
                (i - 40)::NUMERIC * 850000000,   -- text2sql-new засвар: INTEGER overflow
                lpad((7000000 + i)::TEXT, 7, '0'),
                'Эзэмшигч ' || (i - 40)
            );
        END IF;

        INSERT INTO customer.customer_addresses (customer_id, address_type, province, district, khoroo,
                                                 street, building, is_primary)
        VALUES (v_cust_id, CASE WHEN i <= 40 THEN 'RESIDENTIAL' ELSE 'LEGAL' END,
                (ARRAY['Улаанбаатар','Улаанбаатар','Улаанбаатар','Дархан-Уул','Орхон'])[((i - 1) % 5) + 1],
                (ARRAY['Сүхбаатар','Баянзүрх','Хан-Уул','Дархан','Баян-Өндөр'])[((i - 1) % 5) + 1],
                (((i - 1) % 20) + 1)::TEXT || '-р хороо',
                'Гудамж ' || i, (100 + i)::TEXT || ' тоот', TRUE);

        INSERT INTO customer.customer_contacts (customer_id, contact_type, contact_value, is_primary, is_verified, verified_at)
        VALUES (v_cust_id, 'MOBILE', '+976 88' || lpad((200000 + i * 311)::TEXT, 6, '0'), TRUE, TRUE, now()),
               (v_cust_id, 'EMAIL', 'customer' || i || '@example.mn', FALSE, i % 3 = 0,
                CASE WHEN i % 3 = 0 THEN now() ELSE NULL END);

        -- KYC профайл
        -- text2sql-new засвар: ENHANCED түвшинд source_of_wealth заавал (ck_kyc_enhanced_sow)
        INSERT INTO kyc.kyc_profiles (customer_id, kyc_level, status, identity_verified, address_verified,
                                      income_verified, source_of_funds, source_of_wealth, verified_at, verified_by,
                                      expires_at, next_review_date)
        VALUES (v_cust_id,
                CASE WHEN i % 17 = 0 THEN 'ENHANCED' ELSE 'STANDARD' END,
                'VERIFIED', TRUE, TRUE, i <= 40,
                CASE WHEN i <= 40 THEN 'Цалин' ELSE 'Бизнесийн орлого' END,
                CASE WHEN i % 17 = 0 THEN 'Хуримтлал, үл хөдлөх хөрөнгө' END,
                now() - (i || ' days')::INTERVAL, v_officer,
                now() + INTERVAL '2 years', (CURRENT_DATE + 365)::DATE);

        INSERT INTO kyc.customer_risk_profiles (customer_id, risk_level, risk_score, assessment_type,
                                                assessed_by, is_current, next_review_date)
        VALUES (v_cust_id,
                CASE WHEN i % 17 = 0 THEN 'HIGH' WHEN i % 4 = 0 THEN 'MEDIUM' ELSE 'LOW' END,
                CASE WHEN i % 17 = 0 THEN 78 WHEN i % 4 = 0 THEN 45 ELSE 18 END,
                'ONBOARDING', v_officer, TRUE, (CURRENT_DATE + 365)::DATE);

        -- Харилцах данс
        INSERT INTO account.accounts (account_number, account_type_id, customer_id, branch_id, currency_code,
                                      account_name, current_balance, available_balance, opened_date, status)
        VALUES (system.fn_next_number('ACCOUNT'),
                (SELECT id FROM account.account_types WHERE type_code = 'CURRENT'),
                v_cust_id, v_branch, 'MNT', v_name || ' — харилцах',
                (100000 + i * 25000)::NUMERIC, (100000 + i * 25000)::NUMERIC,
                (CURRENT_DATE - 300 + i * 5), 'ACTIVE');
    END LOOP;
END;
$$;

-- ---------------------------------------------------------------------
-- 4. Зээл (20) — хүсэлт → шийдвэр → гэрээ → олголт → хуваарь
-- ---------------------------------------------------------------------
DO $$
DECLARE
    v_cust      RECORD;
    v_product   RECORD;
    v_app_id    UUID;
    v_loan_id   UUID;
    v_officer   BIGINT;
    v_amount    NUMERIC;
    v_term      SMALLINT;
    v_disb_date DATE;
    i           INTEGER := 0;
BEGIN
    FOR v_cust IN
        SELECT c.id, c.branch_id, c.relationship_manager_id, c.party_type, c.display_name
          FROM customer.customers c ORDER BY c.customer_number LIMIT 20
    LOOP
        i := i + 1;
        SELECT p.id, p.product_code, pr.annual_rate, p.amortization_type
          INTO v_product
          FROM product.products p
          JOIN product.product_rates pr ON pr.product_id = p.id
          JOIN product.product_types pt ON pt.id = p.product_type_id AND pt.category = 'LOAN'
         WHERE (v_cust.party_type = 'ORGANIZATION') = (p.product_code LIKE 'BIZ%')
         ORDER BY p.id LIMIT 1 OFFSET (i % 3);

        v_officer   := v_cust.relationship_manager_id;
        v_amount    := (3000000 + (i % 8) * 2500000)::NUMERIC;
        v_term      := 12 + (i % 4) * 6;
        v_disb_date := DATE '2025-07-01' + (i * 9);

        -- Хүсэлт
        INSERT INTO loan.loan_applications (
            application_no, customer_id, product_id, branch_id, status_id, channel,
            requested_amount, requested_term_months, currency_code, loan_purpose, purpose_category,
            declared_income, dti_ratio, approved_amount, approved_term_months, approved_rate,
            loan_officer_id, submitted_at, decided_at
        ) VALUES (
            system.fn_next_number('LOAN_APPLICATION'), v_cust.id, v_product.id, v_cust.branch_id,
            (SELECT id FROM loan.loan_application_statuses WHERE status_code = 'CONTRACTED'),
            (ARRAY['BRANCH','MOBILE','WEB'])[(i % 3) + 1],
            v_amount, v_term, 'MNT',
            CASE WHEN v_cust.party_type = 'ORGANIZATION' THEN 'Эргэлтийн хөрөнгө' ELSE 'Хэрэглээний хэрэгцээ' END,
            CASE WHEN v_cust.party_type = 'ORGANIZATION' THEN 'BUSINESS' ELSE 'CONSUMER' END,
            2500000 + (i % 6) * 700000, 35.0 + (i % 20),
            v_amount, v_term, v_product.annual_rate,
            v_officer, (v_disb_date - 14)::TIMESTAMPTZ, (v_disb_date - 5)::TIMESTAMPTZ
        ) RETURNING id INTO v_app_id;

        -- Судалгаа ба шийдвэр
        INSERT INTO loan.loan_assessments (application_id, assessment_type, assessed_by, verified_income,
                                           calculated_dti, credit_score, recommended_amount,
                                           recommended_term_months, recommended_rate, recommendation)
        VALUES (v_app_id, 'CREDIT', v_officer, 2500000 + (i % 6) * 700000, 35.0 + (i % 20),
                520 + (i * 13) % 380, v_amount, v_term, v_product.annual_rate, 'APPROVE');

        INSERT INTO loan.loan_decisions (application_id, decision_level, decision, decided_by,
                                         approved_amount, approved_term_months, approved_rate, decided_at)
        VALUES (v_app_id,
                CASE WHEN v_amount > 20000000 THEN 'CREDIT_COMMITTEE' ELSE 'BRANCH_MANAGER' END,
                'APPROVED', v_officer, v_amount, v_term, v_product.annual_rate,
                (v_disb_date - 5)::TIMESTAMPTZ);

        -- Зээл (шууд ACTIVE — INSERT дээр төлөвийн trigger ажиллахгүй)
        INSERT INTO loan.loans (
            loan_number, application_id, customer_id, product_id, branch_id, status_id, loan_officer_id,
            currency_code, principal_amount, interest_rate, penalty_rate, rate_type, interest_method,
            amortization_type, term_months, contract_date, disbursement_date, first_payment_date,
            maturity_date, disbursed_amount, principal_outstanding
        ) VALUES (
            system.fn_next_number('LOAN'), v_app_id, v_cust.id, v_product.id, v_cust.branch_id,
            (SELECT id FROM loan.loan_statuses WHERE status_code = 'ACTIVE'), v_officer,
            'MNT', v_amount, v_product.annual_rate, v_product.annual_rate * 0.2, 'FIXED', 'SIMPLE',
            v_product.amortization_type, v_term,
            v_disb_date - 2, v_disb_date, (v_disb_date + INTERVAL '1 month')::DATE,
            (v_disb_date + (v_term || ' months')::INTERVAL)::DATE,
            v_amount, v_amount
        ) RETURNING id INTO v_loan_id;

        INSERT INTO loan.loan_contracts (loan_id, contract_number, contract_type, signed_date,
                                         effective_date, signed_by_customer, signed_by_officer_id,
                                         signature_method, status)
        VALUES (v_loan_id, system.fn_next_number('LOAN_CONTRACT'), 'LOAN', v_disb_date - 1,
                v_disb_date, TRUE, v_officer, 'WET_INK', 'ACTIVE');

        INSERT INTO loan.loan_disbursements (loan_id, currency_code, amount, disbursement_date, value_date,
                                             method, status, executed_at, approved_by)
        VALUES (v_loan_id, 'MNT', v_amount, v_disb_date, v_disb_date, 'BANK_TRANSFER', 'EXECUTED',
                v_disb_date::TIMESTAMPTZ, v_officer);

        -- Олголтын шимтгэл
        INSERT INTO loan.loan_fees (loan_id, fee_type_id, amount, currency_code, charged_date, status)
        SELECT v_loan_id, ft.id, round(v_amount * pf.percent_rate / 100, 2), 'MNT', v_disb_date, 'CHARGED'
          FROM product.product_fees pf
          JOIN product.fee_types ft ON ft.id = pf.fee_type_id
         WHERE pf.product_id = v_product.id AND ft.fee_code = 'DISBURSEMENT_FEE'
         LIMIT 1;

        -- Хуваарь үүсгэх (функцээр — бодит логик)
        PERFORM loan.fn_generate_schedule(v_loan_id, 'ORIGINAL', NULL);
    END LOOP;
END;
$$;

-- ---------------------------------------------------------------------
-- 5. Төлөлт — зээл бүрд хугацаа болсон хуваарийн мөрүүдийг төлнө.
--    Гурав дахь зээл бүр нэг төлөлт алгасаж хугацаа хэтрүүлнэ (DPD үүснэ).
-- ---------------------------------------------------------------------
DO $$
DECLARE
    v_loan     RECORD;
    v_item     RECORD;
    v_pay_id   BIGINT;
    v_teller   BIGINT;
    v_skip     BOOLEAN;
    i          INTEGER := 0;
BEGIN
    SELECT e.id INTO v_teller FROM core.employees e
      JOIN core.positions p ON p.id = e.position_id WHERE p.position_code = 'TELLER' LIMIT 1;

    FOR v_loan IN SELECT id, customer_id, branch_id, loan_number FROM loan.loans ORDER BY loan_number
    LOOP
        i := i + 1;
        v_skip := (i % 3 = 0);   -- гуравны нэг нь сүүлийн төлөлтөө алгасна

        FOR v_item IN
            SELECT si.id, si.due_date, si.principal_due + si.interest_due AS due_amount,
                   row_number() OVER (ORDER BY si.due_date) AS rn,
                   count(*) OVER () AS total
              FROM loan.loan_schedule_items si
              JOIN loan.loan_schedules s ON s.id = si.schedule_id AND s.is_current
             WHERE si.loan_id = v_loan.id AND si.due_date <= DATE '2026-01-05'
             ORDER BY si.due_date
        LOOP
            CONTINUE WHEN v_skip AND v_item.rn = v_item.total;

            -- text2sql-new засвар: ck_loan_payment_alloc нь INSERT дээр шууд шалгагддаг тул
            -- эхлээд бүх дүнг unallocated_amount-д тавьж, fn_allocate_payment хуваарилна.
            INSERT INTO loan.loan_payments (
                payment_no, loan_id, currency_code, customer_id, branch_id, amount, unallocated_amount,
                payment_date, value_date, payment_method, payment_type, status, received_by
            ) VALUES (
                system.fn_next_number('LOAN_PAYMENT'), v_loan.id, 'MNT', v_loan.customer_id, v_loan.branch_id,
                round(v_item.due_amount, 2), round(v_item.due_amount, 2), v_item.due_date, v_item.due_date,
                (ARRAY['CASH','BANK_TRANSFER','AUTO_DEBIT'])[(i % 3) + 1], 'REGULAR', 'POSTED', v_teller
            ) RETURNING id INTO v_pay_id;

            PERFORM loan.fn_allocate_payment(v_pay_id);
        END LOOP;
    END LOOP;
END;
$$;

-- Үлдэгдэл, DPD, ангиллыг эх сурвалжаас дахин тооцох
DO $$
DECLARE r RECORD;
BEGIN
    FOR r IN SELECT id FROM loan.loans LOOP
        PERFORM loan.fn_recalculate_balances(r.id);
    END LOOP;
END;
$$;

-- Хугацаа хэтэрсэн зээлийн төлөвийг зөвшөөрөгдсөн шилжилтээр солино
SELECT system.fn_set_session_context(
    (SELECT id FROM core.users WHERE username = 'sysadmin'), NULL, NULL, 'Demo: DPD үүссэн');

UPDATE loan.loans
   SET status_id = (SELECT id FROM loan.loan_statuses WHERE status_code = 'OVERDUE')
 WHERE days_past_due > 0
   AND status_id = (SELECT id FROM loan.loan_statuses WHERE status_code = 'ACTIVE');

-- ---------------------------------------------------------------------
-- 6. DPD-ийн агшин ба хүүгийн хуримтлал (batch функцүүд)
-- ---------------------------------------------------------------------
SELECT loan.fn_snapshot_overdues(DATE '2026-01-05');
SELECT loan.fn_accrue_interest(DATE '2026-01-05');

-- ---------------------------------------------------------------------
-- 7. Нягтлангийн бичилт — 2026-01 (нээлттэй тайлант үе)
-- ---------------------------------------------------------------------
DO $$
DECLARE
    v_user   BIGINT := (SELECT id FROM core.users WHERE username = 'sysadmin');
    v_branch BIGINT := (SELECT id FROM core.branches WHERE branch_code = 'HO');
    r        RECORD;
    v_entry  BIGINT;
BEGIN
    -- Хүүгийн хуримтлалыг журналд (нэгтгэсэн бичилт)
    SELECT COALESCE(SUM(interest_amount), 0) AS total INTO r
      FROM loan.loan_interest_accruals WHERE accrual_date = DATE '2026-01-05' AND NOT is_posted;

    IF r.total > 0 THEN
        v_entry := accounting.fn_create_entry_from_rule(
            'AR_INT_ACCRUAL',
            jsonb_build_object('INTEREST', r.total),
            DATE '2026-01-05', v_branch, 'MNT',
            '2026-01-05 өдрийн хүүгийн хуримтлал', NULL, NULL, v_user
        );
        UPDATE loan.loan_interest_accruals
           SET is_posted = TRUE, journal_entry_id = v_entry
         WHERE accrual_date = DATE '2026-01-05';
    END IF;

    -- Шимтгэлийн бичилт
    SELECT COALESCE(SUM(amount), 0) AS total INTO r FROM loan.loan_fees WHERE status = 'CHARGED';
    IF r.total > 0 THEN
        PERFORM accounting.fn_create_entry_from_rule(
            'AR_FEE', jsonb_build_object('FEE', r.total),
            DATE '2026-01-05', v_branch, 'MNT', 'Олголтын шимтгэлийн нэгтгэл', NULL, NULL, v_user
        );
    END IF;
END;
$$;

-- ---------------------------------------------------------------------
-- 8. Барагдуулалтын кейс — хугацаа хэтэрсэн зээлүүдэд
-- ---------------------------------------------------------------------
INSERT INTO collection.collection_cases (
    case_no, loan_id, customer_id, branch_id, stage_id, currency_code,
    opened_date, dpd_at_open, current_dpd, outstanding_at_open, current_outstanding,
    overdue_amount, assigned_to, priority, status, next_action_date
)
SELECT system.fn_next_number('COLLECTION_CASE'), l.id, l.customer_id, l.branch_id,
       (SELECT cs.id FROM collection.collection_stages cs
         WHERE l.days_past_due >= cs.min_dpd AND (cs.max_dpd IS NULL OR l.days_past_due <= cs.max_dpd)
         ORDER BY cs.min_dpd DESC LIMIT 1),
       l.currency_code, CURRENT_DATE, l.days_past_due, l.days_past_due,
       l.total_outstanding, l.total_outstanding, l.overdue_principal + l.overdue_interest,
       (SELECT e.id FROM core.employees e JOIN core.positions p ON p.id = e.position_id
         WHERE p.position_code = 'COLLECTOR' LIMIT 1),
       CASE WHEN l.days_past_due > 60 THEN 'HIGH' ELSE 'MEDIUM' END,
       'OPEN', CURRENT_DATE + 3
  FROM loan.loans l
 WHERE l.days_past_due > 0;

INSERT INTO collection.collection_actions (case_id, action_type_id, action_date, performed_by,
                                           contact_result, outcome, cost_amount)
SELECT cc.id, at.id, cc.opened_date, cc.assigned_to, 'CONTACTED',
       'Харилцагчтай холбогдож төлөлтийн талаар мэдэгдэв', at.cost_estimate
  FROM collection.collection_cases cc
  JOIN collection.action_types at ON at.action_code = 'SMS_REMINDER';

-- ---------------------------------------------------------------------
-- 9. Хадгаламж (5 ширхэг)
-- ---------------------------------------------------------------------
DO $$
DECLARE
    v_cust   RECORD;
    v_acc_id UUID;
    v_prod   BIGINT := (SELECT id FROM product.products WHERE product_code = 'DEP_TERM');
    i        INTEGER := 0;
BEGIN
    FOR v_cust IN SELECT id, branch_id, display_name FROM customer.customers
                   ORDER BY customer_number DESC LIMIT 5
    LOOP
        i := i + 1;
        INSERT INTO account.accounts (account_number, account_type_id, customer_id, branch_id, currency_code,
                                      account_name, current_balance, available_balance, opened_date, status)
        VALUES (system.fn_next_number('ACCOUNT'),
                (SELECT id FROM account.account_types WHERE type_code = 'SAVINGS'),
                v_cust.id, v_cust.branch_id, 'MNT', v_cust.display_name || ' — хадгаламж',
                (10000000 * i)::NUMERIC, (10000000 * i)::NUMERIC, DATE '2025-10-01', 'ACTIVE')
        RETURNING id INTO v_acc_id;

        INSERT INTO deposit.deposit_accounts (
            deposit_number, account_id, customer_id, product_id, branch_id, currency_code,
            deposit_type, principal_amount, current_balance, interest_rate, term_months,
            opened_date, maturity_date, auto_renew, status
        ) VALUES (
            system.fn_next_number('DEPOSIT'), v_acc_id, v_cust.id, v_prod, v_cust.branch_id, 'MNT',
            'TERM', (10000000 * i)::NUMERIC, (10000000 * i)::NUMERIC, 12.0, 12,
            DATE '2025-10-01', DATE '2026-10-01', TRUE, 'ACTIVE'
        );
    END LOOP;
END;
$$;

-- ---------------------------------------------------------------------
-- 10. Товч дүн
-- ---------------------------------------------------------------------
DO $$
DECLARE v RECORD;
BEGIN
    SELECT (SELECT count(*) FROM customer.customers)      AS customers,
           (SELECT count(*) FROM loan.loans)              AS loans,
           (SELECT count(*) FROM loan.loan_schedule_items) AS schedule_items,
           (SELECT count(*) FROM loan.loan_payments)      AS payments,
           (SELECT count(*) FROM account.accounts)        AS accounts,
           (SELECT count(*) FROM accounting.journal_entries) AS entries,
           (SELECT count(*) FROM collection.collection_cases) AS cases
      INTO v;
    RAISE NOTICE 'DEMO: % харилцагч, % зээл, % хуваарийн мөр, % төлөлт, % данс, % журнал, % кейс',
        v.customers, v.loans, v.schedule_items, v.payments, v.accounts, v.entries, v.cases;
END;
$$;
