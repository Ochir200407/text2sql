-- =====================================================================
-- 016_seed.sql — ЛАВЛАХ өгөгдөл (reference data)
-- ---------------------------------------------------------------------
-- Энэ нь demo өгөгдөл БИШ — production-д ч энэ лавлахууд заавал байна.
-- Demo харилцагч/зээл/гүйлгээ: seeds/001_demo_data.sql
-- =====================================================================

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------------
-- Улс, валют
-- ---------------------------------------------------------------------
INSERT INTO system.countries (code, name_mn, name_en, iso3, phone_prefix, is_high_risk) VALUES
    ('MN', 'Монгол',          'Mongolia',      'MNG', '+976', FALSE),
    ('CN', 'Хятад',           'China',         'CHN', '+86',  FALSE),
    ('RU', 'Орос',            'Russia',        'RUS', '+7',   FALSE),
    ('US', 'АНУ',             'United States', 'USA', '+1',   FALSE),
    ('KR', 'Солонгос',        'South Korea',   'KOR', '+82',  FALSE),
    ('JP', 'Япон',            'Japan',         'JPN', '+81',  FALSE),
    ('DE', 'Герман',          'Germany',       'DEU', '+49',  FALSE),
    ('KP', 'Хойд Солонгос',   'North Korea',   'PRK', '+850', TRUE),
    ('IR', 'Иран',            'Iran',          'IRN', '+98',  TRUE),
    ('MM', 'Мьянмар',         'Myanmar',       'MMR', '+95',  TRUE);

INSERT INTO system.currencies (code, name_mn, name_en, symbol, decimal_places, is_base) VALUES
    ('MNT', 'Төгрөг',      'Mongolian Tugrik', '₮', 2, TRUE),
    ('USD', 'Ам. доллар',  'US Dollar',        '$', 2, FALSE),
    ('EUR', 'Евро',        'Euro',             '€', 2, FALSE),
    ('CNY', 'Юань',        'Chinese Yuan',     '¥', 2, FALSE),
    ('KRW', 'Вон',         'Korean Won',       '₩', 0, FALSE),
    ('JPY', 'Иен',         'Japanese Yen',     '¥', 0, FALSE);

INSERT INTO system.exchange_rates (from_currency, to_currency, rate_date, buy_rate, sell_rate, mid_rate, source) VALUES
    ('USD', 'MNT', DATE '2026-01-01', 3420.00, 3450.00, 3435.00, 'CENTRAL_BANK'),
    ('EUR', 'MNT', DATE '2026-01-01', 3690.00, 3730.00, 3710.00, 'CENTRAL_BANK'),
    ('CNY', 'MNT', DATE '2026-01-01',  470.00,  478.00,  474.00, 'CENTRAL_BANK'),
    ('KRW', 'MNT', DATE '2026-01-01',    2.45,    2.55,    2.50, 'CENTRAL_BANK'),
    ('JPY', 'MNT', DATE '2026-01-01',   22.80,   23.40,   23.10, 'CENTRAL_BANK');

-- Ажлын өдрийн хуулиа (2025–2027). Тогтмол огноотой баярууд;
-- Цагаан сар зэрэг билгийн тооллын баярыг жил бүр нэмнэ.
INSERT INTO system.business_calendar (calendar_date, is_business_day, is_holiday, holiday_name)
SELECT d::DATE,
       EXTRACT(ISODOW FROM d) < 6 AND h.name IS NULL,
       h.name IS NOT NULL,
       h.name
  FROM generate_series(DATE '2025-01-01', DATE '2027-12-31', INTERVAL '1 day') d
  LEFT JOIN (VALUES
        ('01-01', 'Шинэ жил'),
        ('03-08', 'Эмэгтэйчүүдийн баяр'),
        ('06-01', 'Хүүхдийн баяр'),
        ('07-11', 'Үндэсний их баяр наадам'),
        ('07-12', 'Үндэсний их баяр наадам'),
        ('07-13', 'Үндэсний их баяр наадам'),
        ('07-14', 'Үндэсний их баяр наадам'),
        ('07-15', 'Үндэсний их баяр наадам'),
        ('11-26', 'Тусгаар тогтнолын өдөр'),
        ('12-29', 'Тусгаар тогтнолоо зарласан өдөр')
  ) AS h(md, name) ON to_char(d, 'MM-DD') = h.md;

-- ---------------------------------------------------------------------
-- Дугаарлалт
-- ---------------------------------------------------------------------
INSERT INTO system.number_sequences (sequence_code, prefix, padding_length, include_year, reset_period, description) VALUES
    ('CUSTOMER',        'C',    8, FALSE, 'NEVER',  'Харилцагчийн дугаар'),
    ('LOAN_APPLICATION','LA-',  6, TRUE,  'YEARLY', 'Зээлийн хүсэлт'),
    ('LOAN',            'LN-',  6, TRUE,  'YEARLY', 'Зээлийн дугаар'),
    ('LOAN_CONTRACT',   'LC-',  6, TRUE,  'YEARLY', 'Зээлийн гэрээ'),
    ('LOAN_PAYMENT',    'LP-',  8, TRUE,  'YEARLY', 'Зээлийн төлөлт'),
    ('ACCOUNT',         '',    12, FALSE, 'NEVER',  'Дансны дугаар'),
    ('TRANSACTION',     'TX',  10, FALSE, 'NEVER',  'Гүйлгээний дугаар'),
    ('JOURNAL_ENTRY',   'JE-',  8, TRUE,  'YEARLY', 'Журналын бичилт'),
    ('DEPOSIT',         'DP-',  6, TRUE,  'YEARLY', 'Хадгаламж'),
    ('DEPOSIT_CONTRACT','DC-',  6, TRUE,  'YEARLY', 'Хадгаламжийн гэрээ'),
    ('COLLATERAL',      'CL-',  6, TRUE,  'YEARLY', 'Барьцаа хөрөнгө'),
    ('GUARANTOR',       'GR-',  6, TRUE,  'YEARLY', 'Батлан даагч'),
    ('DOCUMENT',        'DOC-', 8, TRUE,  'YEARLY', 'Баримт бичиг'),
    ('COLLECTION_CASE', 'CC-',  6, TRUE,  'YEARLY', 'Барагдуулалтын кейс'),
    ('RECOVERY',        'RC-',  6, TRUE,  'YEARLY', 'Нөхөн төлүүлэлт'),
    ('AML_ALERT',       'AML-', 8, TRUE,  'YEARLY', 'AML сэрэмжлүүлэг'),
    ('FRAUD_ALERT',     'FA-',  8, TRUE,  'YEARLY', 'Залилангийн сэрэмжлүүлэг'),
    ('FRAUD_CASE',      'FC-',  6, TRUE,  'YEARLY', 'Залилангийн кейс'),
    ('PAYMENT_ORDER',   'PO-',  8, TRUE,  'YEARLY', 'Төлбөрийн даалгавар');

INSERT INTO system.system_parameters (param_code, param_group, param_value, value_type, description) VALUES
    ('CURRENT_BUSINESS_DATE', 'CORE',    '2026-01-05', 'DATE',    'Системийн ажлын өдөр'),
    ('BASE_CURRENCY',         'CORE',    'MNT',        'STRING',  'Суурь валют'),
    ('PENALTY_GRACE_DAYS',    'LOAN',    '5',          'NUMBER',  'Торгууль эхлэх хүртэлх хөнгөлөлтийн хоног'),
    ('NPL_DPD_THRESHOLD',     'LOAN',    '90',         'NUMBER',  'NPL болох DPD босго'),
    ('MAX_DTI_RATIO',         'LOAN',    '60',         'NUMBER',  'Өрх/орлогын харьцааны дээд хязгаар (%)'),
    ('DORMANT_ACCOUNT_DAYS',  'ACCOUNT', '365',        'NUMBER',  'Идэвхгүй данс болох хоног'),
    ('AML_CASH_THRESHOLD',    'AML',     '20000000',   'NUMBER',  'Бэлэн мөнгөний мэдэгдэх босго (MNT)'),
    ('KYC_REVIEW_MONTHS_HIGH','KYC',     '12',         'NUMBER',  'Өндөр эрсдэлт харилцагчийн дахин үнэлгээ (сар)'),
    ('SESSION_TIMEOUT_MIN',   'SECURITY','20',         'NUMBER',  'Session хугацаа (минут)'),
    ('PASSWORD_EXPIRY_DAYS',  'SECURITY','90',         'NUMBER',  'Нууц үгийн хүчинтэй хугацаа');

-- ---------------------------------------------------------------------
-- Салбар, хэлтэс, албан тушаал
-- ---------------------------------------------------------------------
INSERT INTO core.branches (branch_code, branch_name, branch_type, country_code, province, district, phone, gl_prefix, opened_date) VALUES
    ('HO',   'Төв оффис',            'HEAD_OFFICE', 'MN', 'Улаанбаатар', 'Сүхбаатар',   '+976 7000 1000', '00', DATE '2015-03-01'),
    ('UB01', 'Сүхбаатар салбар',     'BRANCH',      'MN', 'Улаанбаатар', 'Сүхбаатар',   '+976 7000 1001', '01', DATE '2016-05-10'),
    ('UB02', 'Баянзүрх салбар',      'BRANCH',      'MN', 'Улаанбаатар', 'Баянзүрх',    '+976 7000 1002', '02', DATE '2017-09-15'),
    ('DR01', 'Дархан салбар',        'BRANCH',      'MN', 'Дархан-Уул',  'Дархан',      '+976 7000 1003', '03', DATE '2019-04-01'),
    ('ER01', 'Эрдэнэт салбар',       'BRANCH',      'MN', 'Орхон',       'Баян-Өндөр',  '+976 7000 1004', '04', DATE '2021-06-20');

UPDATE core.branches SET parent_branch_id = (SELECT id FROM core.branches WHERE branch_code = 'HO')
 WHERE branch_code <> 'HO';

INSERT INTO core.departments (department_code, department_name) VALUES
    ('LENDING',    'Зээлийн газар'),
    ('RISK',       'Эрсдэлийн удирдлагын газар'),
    ('FINANCE',    'Санхүү, нягтлан бодох бүртгэл'),
    ('COMPLIANCE', 'Нийцлийн алба'),
    ('COLLECTION', 'Эргэн төлөлтийн алба'),
    ('IT',         'Мэдээллийн технологи'),
    ('OPERATIONS', 'Үйл ажиллагааны газар');

INSERT INTO core.positions (position_code, position_name, grade_level, approval_limit) VALUES
    ('CEO',            'Гүйцэтгэх захирал',        12, 500000000),
    ('BRANCH_MANAGER', 'Салбарын менежер',          9,  50000000),
    ('CREDIT_OFFICER', 'Зээлийн мэргэжилтэн',       6,  10000000),
    ('RISK_ANALYST',   'Эрсдэлийн шинжээч',         7,          0),
    ('ACCOUNTANT',     'Нягтлан бодогч',            6,          0),
    ('TELLER',         'Кассч',                     4,   1000000),
    ('COLLECTOR',      'Эргэн төлөлтийн ажилтан',   5,          0),
    ('COMPLIANCE_OFF', 'Нийцлийн ажилтан',          7,          0);

-- ---------------------------------------------------------------------
-- Эрх (role / permission)
-- ---------------------------------------------------------------------
INSERT INTO core.roles (role_code, role_name, is_system, description) VALUES
    ('SYS_ADMIN',      'Системийн администратор', TRUE,  'Бүх эрх — зөвхөн IT'),
    ('BRANCH_MANAGER', 'Салбарын менежер',        FALSE, 'Салбарын зээл батлах, тайлан'),
    ('CREDIT_OFFICER', 'Зээлийн мэргэжилтэн',     FALSE, 'Хүсэлт бүртгэх, судалгаа'),
    ('TELLER',         'Кассч',                   FALSE, 'Гүйлгээ хийх'),
    ('ACCOUNTANT',     'Нягтлан бодогч',          FALSE, 'Журнал батлах, тайлан'),
    ('RISK_ANALYST',   'Эрсдэлийн шинжээч',       FALSE, 'Скоринг, эрсдэлийн үнэлгээ'),
    ('COMPLIANCE',     'Нийцлийн ажилтан',        FALSE, 'KYC/AML шалгалт'),
    ('COLLECTOR',      'Эргэн төлөлтийн ажилтан', FALSE, 'Барагдуулалтын кейс'),
    ('AUDITOR',        'Аудитор',                 FALSE, 'Зөвхөн унших + аудитын лог');

INSERT INTO core.permissions (permission_code, module, action, sensitivity) VALUES
    ('CUSTOMER_VIEW',    'CUSTOMER',   'VIEW',    'CONFIDENTIAL'),
    ('CUSTOMER_CREATE',  'CUSTOMER',   'CREATE',  'CONFIDENTIAL'),
    ('CUSTOMER_UPDATE',  'CUSTOMER',   'UPDATE',  'CONFIDENTIAL'),
    ('LOAN_VIEW',        'LOAN',       'VIEW',    'CONFIDENTIAL'),
    ('LOAN_CREATE',      'LOAN',       'CREATE',  'CONFIDENTIAL'),
    ('LOAN_APPROVE',     'LOAN',       'APPROVE', 'RESTRICTED'),
    ('LOAN_UPDATE',      'LOAN',       'UPDATE',  'CONFIDENTIAL'),
    ('TXN_CREATE',       'TRANSACTION','CREATE',  'CONFIDENTIAL'),
    ('TXN_VIEW',         'TRANSACTION','VIEW',    'CONFIDENTIAL'),
    ('GL_POST',          'ACCOUNTING', 'POST',    'RESTRICTED'),
    ('GL_VIEW',          'ACCOUNTING', 'VIEW',    'INTERNAL'),
    ('KYC_APPROVE',      'KYC',        'APPROVE', 'RESTRICTED'),
    ('KYC_VIEW',         'KYC',        'VIEW',    'CONFIDENTIAL'),
    ('COLLECTION_UPDATE','COLLECTION', 'UPDATE',  'CONFIDENTIAL'),
    ('COLLECTION_VIEW',  'COLLECTION', 'VIEW',    'CONFIDENTIAL'),
    ('REPORT_EXPORT',    'REPORTING',  'EXPORT',  'CONFIDENTIAL'),
    ('SYSTEM_ADMIN',     'SYSTEM',     'ADMIN',   'RESTRICTED'),
    ('AUDIT_VIEW',       'AUDIT',      'VIEW',    'RESTRICTED');

INSERT INTO core.role_permissions (role_id, permission_id)
SELECT r.id, p.id FROM core.roles r, core.permissions p
 WHERE (r.role_code = 'SYS_ADMIN')
    OR (r.role_code = 'BRANCH_MANAGER' AND p.permission_code IN
        ('CUSTOMER_VIEW','CUSTOMER_CREATE','CUSTOMER_UPDATE','LOAN_VIEW','LOAN_CREATE','LOAN_APPROVE',
         'LOAN_UPDATE','TXN_VIEW','GL_VIEW','COLLECTION_VIEW','REPORT_EXPORT'))
    OR (r.role_code = 'CREDIT_OFFICER' AND p.permission_code IN
        ('CUSTOMER_VIEW','CUSTOMER_CREATE','CUSTOMER_UPDATE','LOAN_VIEW','LOAN_CREATE','LOAN_UPDATE','KYC_VIEW'))
    OR (r.role_code = 'TELLER' AND p.permission_code IN ('CUSTOMER_VIEW','TXN_CREATE','TXN_VIEW','LOAN_VIEW'))
    OR (r.role_code = 'ACCOUNTANT' AND p.permission_code IN ('GL_POST','GL_VIEW','TXN_VIEW','REPORT_EXPORT'))
    OR (r.role_code = 'RISK_ANALYST' AND p.permission_code IN ('CUSTOMER_VIEW','LOAN_VIEW','GL_VIEW','REPORT_EXPORT'))
    OR (r.role_code = 'COMPLIANCE' AND p.permission_code IN ('CUSTOMER_VIEW','KYC_VIEW','KYC_APPROVE','AUDIT_VIEW'))
    OR (r.role_code = 'COLLECTOR' AND p.permission_code IN ('CUSTOMER_VIEW','LOAN_VIEW','COLLECTION_VIEW','COLLECTION_UPDATE'))
    OR (r.role_code = 'AUDITOR' AND p.permission_code IN
        ('CUSTOMER_VIEW','LOAN_VIEW','TXN_VIEW','GL_VIEW','AUDIT_VIEW','COLLECTION_VIEW','KYC_VIEW'));

-- ---------------------------------------------------------------------
-- Харилцагчийн лавлах
-- ---------------------------------------------------------------------
INSERT INTO customer.customer_types (type_code, type_name, party_type) VALUES
    ('IND_SALARY',   'Цалинтай хувь хүн',        'INDIVIDUAL'),
    ('IND_BUSINESS', 'Бизнес эрхлэгч хувь хүн',  'INDIVIDUAL'),
    ('IND_PENSION',  'Тэтгэвэр авагч',           'INDIVIDUAL'),
    ('ORG_SME',      'Жижиг дунд аж ахуйн нэгж', 'ORGANIZATION'),
    ('ORG_CORP',     'Томоохон байгууллага',     'ORGANIZATION'),
    ('ORG_NGO',      'Төрийн бус байгууллага',   'ORGANIZATION');

INSERT INTO customer.customer_statuses (status_code, status_name, is_active_state, blocks_new_loan, sort_order) VALUES
    ('PROSPECT',  'Боломжит харилцагч', TRUE,  TRUE,  1),
    ('ACTIVE',    'Идэвхтэй',           TRUE,  FALSE, 2),
    ('INACTIVE',  'Идэвхгүй',           FALSE, TRUE,  3),
    ('SUSPENDED', 'Түдгэлзүүлсэн',      FALSE, TRUE,  4),
    ('BLACKLIST', 'Хар жагсаалт',       FALSE, TRUE,  5),
    ('CLOSED',    'Хаагдсан',           FALSE, TRUE,  6);

INSERT INTO customer.customer_segments (segment_code, segment_name, min_income, priority_rank) VALUES
    ('MASS',     'Энгийн',   0,        1),
    ('AFFLUENT', 'Дунд',     2000000,  2),
    ('PREMIUM',  'Дээд',     5000000,  3),
    ('VIP',      'VIP',      15000000, 4),
    ('SME',      'ЖДҮ',      0,        3),
    ('CORPORATE','Корпорэйт',0,        4);

-- ---------------------------------------------------------------------
-- KYC / AML лавлах
-- ---------------------------------------------------------------------
INSERT INTO kyc.risk_factors (factor_code, factor_name, category, weight) VALUES
    ('GEO_HIGH_RISK',   'Өндөр эрсдэлт улстай холбоотой', 'GEOGRAPHY',   3.0),
    ('PEP_STATUS',      'Улс төрд нөлөө бүхий этгээд',    'CUSTOMER',    4.0),
    ('CASH_INTENSIVE',  'Бэлэн мөнгө эрчимтэй бизнес',    'CUSTOMER',    2.5),
    ('NON_RESIDENT',    'Резидент бус',                   'CUSTOMER',    2.0),
    ('HIGH_VALUE_TXN',  'Их дүнтэй гүйлгээ',              'TRANSACTION', 2.0),
    ('DIGITAL_ONLY',    'Зөвхөн цахим суваг',             'CHANNEL',     1.5),
    ('COMPLEX_OWNERSHIP','Нарийн өмчлөлийн бүтэц',        'CUSTOMER',    3.0);

INSERT INTO kyc.sanction_lists (list_code, list_name, jurisdiction) VALUES
    ('UN',     'НҮБ-ын Аюулгүйн зөвлөлийн жагсаалт', NULL),
    ('OFAC',   'OFAC SDN жагсаалт',                  'US'),
    ('EU',     'ЕХ-ны санкцын жагсаалт',             NULL),
    ('MN_FIU', 'СЭГТ-ийн жагсаалт',                  'MN');

INSERT INTO kyc.aml_rules (rule_code, rule_name, rule_type, parameters, severity) VALUES
    ('CASH_THRESHOLD',   'Бэлэн мөнгөний босго давсан',  'THRESHOLD',
     '{"amount": 20000000, "currency": "MNT"}'::JSONB, 'HIGH'),
    ('STRUCTURING',      'Босго тойрсон хуваасан гүйлгээ','STRUCTURING',
     '{"amount": 20000000, "window_days": 7, "min_count": 3}'::JSONB, 'CRITICAL'),
    ('VELOCITY_DAILY',   'Өдрийн гүйлгээний тоо хэвийн бус','VELOCITY',
     '{"max_count": 15, "window_hours": 24}'::JSONB, 'MEDIUM'),
    ('HIGH_RISK_GEO',    'Өндөр эрсдэлт улс руу шилжүүлэг','GEOGRAPHY',
     '{"countries": ["KP", "IR", "MM"]}'::JSONB, 'CRITICAL'),
    ('WATCHLIST_HIT',    'Хяналтын жагсаалттай тааралт',  'WATCHLIST',
     '{"min_score": 85}'::JSONB, 'CRITICAL');

-- ---------------------------------------------------------------------
-- Бүтээгдэхүүн
-- ---------------------------------------------------------------------
INSERT INTO product.product_types (type_code, type_name, category) VALUES
    ('CONSUMER_LOAN',  'Хэрэглээний зээл',       'LOAN'),
    ('SALARY_LOAN',    'Цалингийн зээл',         'LOAN'),
    ('BUSINESS_LOAN',  'Бизнесийн зээл',         'LOAN'),
    ('MORTGAGE',       'Орон сууцны зээл',       'LOAN'),
    ('AUTO_LOAN',      'Автомашины зээл',        'LOAN'),
    ('PLEDGE_LOAN',    'Барьцаат хурдан зээл',   'LOAN'),
    ('TERM_DEPOSIT',   'Хугацаатай хадгаламж',   'DEPOSIT'),
    ('DEMAND_DEPOSIT', 'Хугацаагүй хадгаламж',   'DEPOSIT'),
    ('CURRENT_ACC',    'Харилцах данс',          'CURRENT_ACCOUNT');

INSERT INTO product.fee_types (fee_code, fee_name, charge_event, is_taxable) VALUES
    ('APPLICATION_FEE',  'Хүсэлтийн шимтгэл',        'APPLICATION',    TRUE),
    ('DISBURSEMENT_FEE', 'Зээл олголтын шимтгэл',    'DISBURSEMENT',   TRUE),
    ('SERVICE_FEE',      'Сарын үйлчилгээний хураамж','MONTHLY',       TRUE),
    ('EARLY_REPAY_FEE',  'Хугацаанаас өмнө төлөх',   'EARLY_REPAYMENT',TRUE),
    ('LATE_FEE',         'Хугацаа хэтрүүлсэн хураамж','LATE_PAYMENT',  TRUE),
    ('RESTRUCTURE_FEE',  'Бүтцийн өөрчлөлтийн шимтгэл','RESTRUCTURE',  TRUE),
    ('STATEMENT_FEE',    'Хуулга гаргах',            'STATEMENT',      TRUE),
    ('TRANSFER_FEE',     'Шилжүүлгийн шимтгэл',      'TRANSACTION',    TRUE);

INSERT INTO product.products (
    product_code, product_name, product_type_id, currency_code, amortization_type, interest_method,
    repayment_period_unit, grace_period_days, penalty_grace_days, requires_collateral, requires_guarantor,
    min_credit_score, max_dti_ratio, max_ltv_ratio, launch_date, status
)
SELECT v.code, v.name, pt.id, 'MNT', v.amort::system.amortization_type, 'SIMPLE'::system.interest_method,
       'MONTH'::system.period_unit, v.grace, 5, v.coll, v.guar, v.score, v.dti, v.ltv, DATE '2024-01-01', 'ACTIVE'
  FROM (VALUES
    ('CONS_STD',   'Хэрэглээний зээл — стандарт',  'CONSUMER_LOAN', 'ANNUITY',         0, FALSE, TRUE,  550, 60.0, NULL),
    ('CONS_FAST',  'Хурдан хэрэглээний зээл',      'CONSUMER_LOAN', 'ANNUITY',         0, FALSE, FALSE, 600, 50.0, NULL),
    ('SAL_STD',    'Цалингийн зээл',               'SALARY_LOAN',   'ANNUITY',         0, FALSE, FALSE, 500, 65.0, NULL),
    ('SAL_PREM',   'Цалингийн зээл — VIP',         'SALARY_LOAN',   'ANNUITY',         0, FALSE, FALSE, 700, 70.0, NULL),
    ('BIZ_WC',     'Бизнесийн эргэлтийн хөрөнгө',  'BUSINESS_LOAN', 'EQUAL_PRINCIPAL', 30, TRUE, TRUE,  550, NULL, 70.0),
    ('BIZ_INV',    'Бизнесийн хөрөнгө оруулалт',   'BUSINESS_LOAN', 'ANNUITY',         60, TRUE, TRUE,  600, NULL, 65.0),
    ('MORT_STD',   'Орон сууцны зээл',             'MORTGAGE',      'ANNUITY',         0, TRUE,  FALSE, 620, 45.0, 80.0),
    ('AUTO_NEW',   'Шинэ автомашины зээл',         'AUTO_LOAN',     'ANNUITY',         0, TRUE,  FALSE, 580, 55.0, 70.0),
    ('PLEDGE_GOLD','Алт барьцаалсан зээл',         'PLEDGE_LOAN',   'BULLET',          0, TRUE,  FALSE, 0,   NULL, 60.0),
    ('DEP_TERM',   'Хугацаатай хадгаламж',         'TERM_DEPOSIT',  NULL,              0, FALSE, FALSE, NULL, NULL, NULL)
  ) AS v(code, name, ptype, amort, grace, coll, guar, score, dti, ltv)
  JOIN product.product_types pt ON pt.type_code = v.ptype;

INSERT INTO product.product_rates (product_id, rate_type, annual_rate, min_amount, max_amount,
                                   min_term_months, max_term_months, valid_from)
SELECT p.id, 'FIXED', v.rate, v.min_amt, v.max_amt, v.min_t, v.max_t, DATE '2024-01-01'
  FROM (VALUES
    ('CONS_STD',   30.0,  500000,   30000000, 3,  36),
    ('CONS_FAST',  42.0,  100000,    5000000, 1,  12),
    ('SAL_STD',    24.0,  500000,   50000000, 6,  60),
    ('SAL_PREM',   19.2, 5000000,  100000000, 6,  60),
    ('BIZ_WC',     27.6, 5000000,  300000000, 3,  24),
    ('BIZ_INV',    25.2,10000000,  500000000, 12, 60),
    ('MORT_STD',   16.8,20000000, 1000000000, 60, 240),
    ('AUTO_NEW',   21.6, 5000000,  150000000, 12, 60),
    ('PLEDGE_GOLD',36.0,  100000,   20000000, 1,   6),
    ('DEP_TERM',   12.0,  100000, 1000000000, 3,  36)
  ) AS v(code, rate, min_amt, max_amt, min_t, max_t)
  JOIN product.products p ON p.product_code = v.code;

INSERT INTO product.product_fees (product_id, fee_type_id, calc_method, fixed_amount, percent_rate, valid_from)
SELECT p.id, ft.id, v.method, v.fixed, v.pct, DATE '2024-01-01'
  FROM (VALUES
    ('CONS_STD',   'DISBURSEMENT_FEE', 'PERCENT_OF_PRINCIPAL', NULL,   1.0),
    ('CONS_FAST',  'DISBURSEMENT_FEE', 'PERCENT_OF_PRINCIPAL', NULL,   2.0),
    ('SAL_STD',    'DISBURSEMENT_FEE', 'PERCENT_OF_PRINCIPAL', NULL,   0.5),
    ('BIZ_WC',     'DISBURSEMENT_FEE', 'PERCENT_OF_PRINCIPAL', NULL,   1.5),
    ('MORT_STD',   'DISBURSEMENT_FEE', 'PERCENT_OF_PRINCIPAL', NULL,   0.5),
    ('AUTO_NEW',   'DISBURSEMENT_FEE', 'PERCENT_OF_PRINCIPAL', NULL,   1.0),
    ('CONS_STD',   'EARLY_REPAY_FEE',  'PERCENT_OF_OUTSTANDING', NULL, 2.0),
    ('MORT_STD',   'EARLY_REPAY_FEE',  'PERCENT_OF_OUTSTANDING', NULL, 1.0),
    ('CONS_STD',   'STATEMENT_FEE',    'FIXED',                5000,   NULL)
  ) AS v(pcode, fcode, method, fixed, pct)
  JOIN product.products p  ON p.product_code = v.pcode
  JOIN product.fee_types ft ON ft.fee_code = v.fcode;

INSERT INTO product.product_limits (product_id, min_amount, max_amount, min_term_months, max_term_months,
                                    max_active_loans, valid_from)
SELECT p.id, pr.min_amount, pr.max_amount, pr.min_term_months, pr.max_term_months, 3, DATE '2024-01-01'
  FROM product.products p
  JOIN product.product_rates pr ON pr.product_id = p.id;

-- Төлбөр хуваарилах дараалал (бүх зээлийн бүтээгдэхүүнд ижил)
INSERT INTO product.payment_allocation_rules (product_id, allocation_order, component, valid_from)
SELECT p.id, v.ord, v.comp, DATE '2024-01-01'
  FROM product.products p
  JOIN product.product_types pt ON pt.id = p.product_type_id AND pt.category = 'LOAN'
  CROSS JOIN (VALUES (1, 'PENALTY'), (2, 'FEE'), (3, 'OVERDUE_INTEREST'), (4, 'INTEREST'), (5, 'PRINCIPAL'))
       AS v(ord, comp);

INSERT INTO product.product_branch_availability (product_id, branch_id)
SELECT p.id, b.id FROM product.products p CROSS JOIN core.branches b;

-- ---------------------------------------------------------------------
-- Зээлийн төлөв ба шилжилт
-- ---------------------------------------------------------------------
INSERT INTO loan.loan_application_statuses (status_code, status_name, is_terminal, is_approved, sort_order) VALUES
    ('DRAFT',        'Ноорог',              FALSE, FALSE, 1),
    ('SUBMITTED',    'Илгээгдсэн',          FALSE, FALSE, 2),
    ('UNDER_REVIEW', 'Судалгаанд',          FALSE, FALSE, 3),
    ('PENDING_DOCS', 'Баримт хүлээгдэж буй',FALSE, FALSE, 4),
    ('APPROVED',     'Батлагдсан',          FALSE, TRUE,  5),
    ('REJECTED',     'Татгалзсан',          TRUE,  FALSE, 6),
    ('WITHDRAWN',    'Буцаан татсан',       TRUE,  FALSE, 7),
    ('EXPIRED',      'Хугацаа дууссан',     TRUE,  FALSE, 8),
    ('CONTRACTED',   'Гэрээ байгуулсан',    TRUE,  TRUE,  9);

INSERT INTO loan.loan_statuses (status_code, status_name, is_active_loan, is_terminal, accrues_interest, sort_order) VALUES
    ('PENDING_DISBURSEMENT', 'Олголт хүлээж буй', FALSE, FALSE, FALSE, 1),
    ('ACTIVE',               'Идэвхтэй',          TRUE,  FALSE, TRUE,  2),
    ('OVERDUE',              'Хугацаа хэтэрсэн',  TRUE,  FALSE, TRUE,  3),
    ('RESTRUCTURED',         'Бүтэц өөрчилсөн',   TRUE,  FALSE, TRUE,  4),
    ('LEGAL',                'Хууль зүйн шатанд', TRUE,  FALSE, TRUE,  5),
    ('WRITTEN_OFF',          'Данснаас хассан',   FALSE, FALSE, FALSE, 6),
    ('CLOSED',               'Хаагдсан',          FALSE, TRUE,  FALSE, 7),
    ('CANCELLED',            'Цуцлагдсан',        FALSE, TRUE,  FALSE, 8);

INSERT INTO loan.loan_status_transitions (from_status_id, to_status_id, requires_approval, description)
SELECT f.id, t.id, v.appr, v.descr
  FROM (VALUES
    ('PENDING_DISBURSEMENT', 'ACTIVE',        FALSE, 'Зээл олгогдов'),
    ('PENDING_DISBURSEMENT', 'CANCELLED',     TRUE,  'Олголтоос өмнө цуцлав'),
    ('ACTIVE',               'OVERDUE',       FALSE, 'Хугацаа хэтэрлээ'),
    ('ACTIVE',               'CLOSED',        FALSE, 'Бүрэн төлөгдөв'),
    ('ACTIVE',               'RESTRUCTURED',  TRUE,  'Бүтцийн өөрчлөлт'),
    ('OVERDUE',              'ACTIVE',        FALSE, 'Хугацаа хэтрэлт барагдав'),
    ('OVERDUE',              'RESTRUCTURED',  TRUE,  'Бүтцийн өөрчлөлт'),
    ('OVERDUE',              'LEGAL',         TRUE,  'Хууль зүйн шат руу'),
    ('OVERDUE',              'CLOSED',        FALSE, 'Бүрэн төлөгдөв'),
    ('OVERDUE',              'WRITTEN_OFF',   TRUE,  'Данснаас хасав'),
    ('RESTRUCTURED',         'ACTIVE',        FALSE, 'Шинэ хуваарь идэвхжив'),
    ('RESTRUCTURED',         'OVERDUE',       FALSE, 'Дахин хэтэрлээ'),
    ('RESTRUCTURED',         'CLOSED',        FALSE, 'Бүрэн төлөгдөв'),
    ('LEGAL',                'WRITTEN_OFF',   TRUE,  'Данснаас хасав'),
    ('LEGAL',                'CLOSED',        FALSE, 'Барагдав'),
    ('LEGAL',                'RESTRUCTURED',  TRUE,  'Эвлэрлийн гэрээ'),
    ('WRITTEN_OFF',          'CLOSED',        TRUE,  'Нөхөн төлүүлэлт дууслаа')
  ) AS v(from_code, to_code, appr, descr)
  JOIN loan.loan_statuses f ON f.status_code = v.from_code
  JOIN loan.loan_statuses t ON t.status_code = v.to_code;

-- Активын ангилал.
-- АНХААР: доорх босго нь ЖИШЭЭ. Мөрдөж буй журмаар баталгаажуулж тохируулна.
INSERT INTO loan.classification_grades
    (grade_code, grade_name_mn, grade_name_en, min_dpd, max_dpd, provision_rate, is_npl, is_performing, sort_order, regulation_ref) VALUES
    ('NORMAL',       'Хэвийн',           'Performing',       0,   14,   1.0, FALSE, TRUE,  1, 'ББСБ-ын активын ангиллын журам'),
    ('WATCH',        'Хяналтад',         'Special mention',  15,  30,   5.0, FALSE, TRUE,  2, 'ББСБ-ын активын ангиллын журам'),
    ('SUBSTANDARD',  'Чанар муудсан',    'Substandard',      31,  90,  25.0, FALSE, FALSE, 3, 'ББСБ-ын активын ангиллын журам'),
    ('DOUBTFUL',     'Эргэлзээтэй',      'Doubtful',         91,  180, 50.0, TRUE,  FALSE, 4, 'ББСБ-ын активын ангиллын журам'),
    ('LOSS',         'Муу',              'Loss',             181, NULL,100.0, TRUE,  FALSE, 5, 'ББСБ-ын активын ангиллын журам');

-- ---------------------------------------------------------------------
-- Барьцаа
-- ---------------------------------------------------------------------
INSERT INTO collateral.collateral_types (type_code, type_name, category, max_ltv_ratio, haircut_pct,
                                         revaluation_months, requires_insurance, requires_registration) VALUES
    ('APARTMENT',   'Орон сууц',            'REAL_ESTATE', 80.0, 10.0, 12, TRUE,  TRUE),
    ('HOUSE',       'Хашаа байшин',         'REAL_ESTATE', 70.0, 15.0, 12, TRUE,  TRUE),
    ('LAND',        'Газар',                'REAL_ESTATE', 60.0, 20.0, 24, FALSE, TRUE),
    ('VEHICLE_NEW', 'Автомашин (шинэ)',     'VEHICLE',     70.0, 15.0, 12, TRUE,  TRUE),
    ('VEHICLE_USED','Автомашин (хуучин)',   'VEHICLE',     50.0, 25.0, 12, TRUE,  TRUE),
    ('EQUIPMENT',   'Тоног төхөөрөмж',      'EQUIPMENT',   50.0, 30.0, 12, TRUE,  FALSE),
    ('GOLD',        'Алт, үнэт эдлэл',      'OTHER',       60.0, 20.0, 6,  FALSE, FALSE),
    ('DEPOSIT_PLG', 'Хадгаламж барьцаа',    'DEPOSIT',     95.0,  0.0, NULL, FALSE, FALSE),
    ('LIVESTOCK',   'Мал сүрэг',            'LIVESTOCK',   40.0, 35.0, 6,  TRUE,  FALSE);

-- ---------------------------------------------------------------------
-- Данс, гүйлгээ
-- ---------------------------------------------------------------------
INSERT INTO account.account_types (type_code, type_name, account_class, normal_balance, allows_negative, is_customer_facing) VALUES
    ('CURRENT',     'Харилцах данс',        'CUSTOMER',   'CREDIT', FALSE, TRUE),
    ('SAVINGS',     'Хадгаламжийн данс',    'CUSTOMER',   'CREDIT', FALSE, TRUE),
    ('LOAN_ACC',    'Зээлийн данс',         'CUSTOMER',   'DEBIT',  TRUE,  TRUE),
    ('CASH_VAULT',  'Кассын данс',          'CASH',       'DEBIT',  FALSE, FALSE),
    ('SUSPENSE',    'Түдгэлзүүлсэн данс',   'SUSPENSE',   'CREDIT', TRUE,  FALSE),
    ('SETTLEMENT',  'Тооцооны данс',        'SETTLEMENT', 'DEBIT',  TRUE,  FALSE),
    ('NOSTRO',      'Банкин дахь данс',     'NOSTRO',     'DEBIT',  FALSE, FALSE);

INSERT INTO account.transaction_channels (channel_code, channel_name, is_digital, requires_otp, daily_limit) VALUES
    ('BRANCH',   'Салбар',          FALSE, FALSE, NULL),
    ('MOBILE',   'Мобайл апп',      TRUE,  TRUE,  10000000),
    ('INTERNET', 'Интернэт банк',   TRUE,  TRUE,  20000000),
    ('ATM',      'АТМ',             TRUE,  FALSE, 2000000),
    ('POS',      'ПОС терминал',    TRUE,  FALSE, 5000000),
    ('API',      'Партнёрын API',   TRUE,  FALSE, 50000000),
    ('BATCH',    'Автомат багц',    FALSE, FALSE, NULL);

INSERT INTO account.transaction_statuses (status_code, status_name, is_final, is_successful, affects_balance, sort_order) VALUES
    ('PENDING',   'Хүлээгдэж буй', FALSE, FALSE, FALSE, 1),
    ('PROCESSING','Боловсруулж буй',FALSE,FALSE, FALSE, 2),
    ('COMPLETED', 'Амжилттай',     TRUE,  TRUE,  TRUE,  3),
    ('FAILED',    'Амжилтгүй',     TRUE,  FALSE, FALSE, 4),
    ('REVERSED',  'Сторно хийсэн', TRUE,  FALSE, TRUE,  5),
    ('CANCELLED', 'Цуцлагдсан',    TRUE,  FALSE, FALSE, 6);

INSERT INTO account.transaction_types (type_code, type_name, direction, category, requires_approval, approval_threshold) VALUES
    ('CASH_DEPOSIT',    'Бэлэн мөнгө тушаах',     'C', 'DEPOSIT',           FALSE, NULL),
    ('CASH_WITHDRAWAL', 'Бэлэн мөнгө авах',       'D', 'WITHDRAWAL',        TRUE,  5000000),
    ('TRANSFER_IN',     'Орлогын шилжүүлэг',      'C', 'TRANSFER',          FALSE, NULL),
    ('TRANSFER_OUT',    'Зарлагын шилжүүлэг',     'D', 'TRANSFER',          TRUE,  10000000),
    ('LOAN_DISB',       'Зээл олголт',            'C', 'LOAN_DISBURSEMENT', TRUE,  NULL),
    ('LOAN_REPAY',      'Зээлийн эргэн төлөлт',   'D', 'LOAN_REPAYMENT',    FALSE, NULL),
    ('FEE_CHARGE',      'Шимтгэл суутгал',        'D', 'FEE',               FALSE, NULL),
    ('INTEREST_CREDIT', 'Хүү тооцох',             'C', 'INTEREST',          FALSE, NULL),
    ('PENALTY_CHARGE',  'Торгууль',               'D', 'PENALTY',           FALSE, NULL),
    ('REVERSAL',        'Сторно',                 'C', 'REVERSAL',          TRUE,  NULL);

INSERT INTO account.payment_methods (method_code, method_name, provider, settlement_days, fee_fixed) VALUES
    ('CASH',        'Бэлэн мөнгө',      NULL,        0, 0),
    ('BANK_TRANSFER','Банкны шилжүүлэг','ХУРДАН',     0, 500),
    ('CARD',        'Карт',             'VISA/MC',   1, 0),
    ('QPAY',        'QPay',             'QPay',      0, 100),
    ('SOCIALPAY',   'SocialPay',        'Голомт',    0, 100),
    ('AUTO_DEBIT',  'Автомат суутгал',  NULL,        0, 0);

-- ---------------------------------------------------------------------
-- Нягтлан: дансны төлөвлөгөө
-- ---------------------------------------------------------------------
INSERT INTO accounting.chart_of_accounts (account_code, account_name_mn, account_name_en, account_type, normal_balance, level, is_postable, is_control, control_domain) VALUES
    ('1000', 'АКТИВ',                        'ASSETS',                   'ASSET',     'DEBIT',  1, FALSE, FALSE, NULL),
    ('1100', 'Мөнгө ба түүнтэй адилтгах',    'Cash and equivalents',     'ASSET',     'DEBIT',  2, FALSE, FALSE, NULL),
    ('1110', 'Касс',                         'Cash on hand',             'ASSET',     'DEBIT',  3, TRUE,  FALSE, NULL),
    ('1120', 'Банкин дахь харилцах',         'Bank accounts',            'ASSET',     'DEBIT',  3, TRUE,  FALSE, NULL),
    ('1200', 'Зээлийн багц',                 'Loan portfolio',           'ASSET',     'DEBIT',  2, FALSE, FALSE, NULL),
    ('1210', 'Хэвийн зээл',                  'Performing loans',         'ASSET',     'DEBIT',  3, TRUE,  TRUE,  'LOAN'),
    ('1220', 'Хугацаа хэтэрсэн зээл',        'Overdue loans',            'ASSET',     'DEBIT',  3, TRUE,  TRUE,  'LOAN'),
    ('1230', 'Чанаргүй зээл (NPL)',          'Non-performing loans',     'ASSET',     'DEBIT',  3, TRUE,  TRUE,  'LOAN'),
    ('1290', 'Эрсдэлийн сан (хасагдуулга)',  'Loan loss provision',      'ASSET',     'CREDIT', 3, TRUE,  FALSE, NULL),
    ('1300', 'Хуримтлагдсан хүүгийн авлага', 'Accrued interest receivable','ASSET',   'DEBIT',  2, TRUE,  TRUE,  'LOAN'),
    ('1310', 'Торгуулийн авлага',            'Penalty receivable',       'ASSET',     'DEBIT',  3, TRUE,  FALSE, NULL),
    ('1320', 'Шимтгэлийн авлага',            'Fee receivable',           'ASSET',     'DEBIT',  3, TRUE,  FALSE, NULL),
    ('2000', 'ЭХ ҮҮСВЭР',                    'LIABILITIES',              'LIABILITY', 'CREDIT', 1, FALSE, FALSE, NULL),
    ('2100', 'Харилцагчийн хадгаламж',       'Customer deposits',        'LIABILITY', 'CREDIT', 2, TRUE,  TRUE,  'DEPOSIT'),
    ('2110', 'Хугацаатай хадгаламж',         'Term deposits',            'LIABILITY', 'CREDIT', 3, TRUE,  TRUE,  'DEPOSIT'),
    ('2120', 'Харилцах данс',                'Current accounts',         'LIABILITY', 'CREDIT', 3, TRUE,  TRUE,  'CUSTOMER_ACCOUNT'),
    ('2200', 'Төлбөл зохих хүү',             'Interest payable',         'LIABILITY', 'CREDIT', 2, TRUE,  FALSE, NULL),
    ('2300', 'Татварын өр',                  'Tax payable',              'LIABILITY', 'CREDIT', 2, TRUE,  FALSE, NULL),
    ('2400', 'Түдгэлзүүлсэн данс',           'Suspense account',         'LIABILITY', 'CREDIT', 2, TRUE,  FALSE, NULL),
    ('3000', 'ӨӨРИЙН ХӨРӨНГӨ',              'EQUITY',                   'EQUITY',    'CREDIT', 1, FALSE, FALSE, NULL),
    ('3100', 'Хувь нийлүүлсэн хөрөнгө',      'Share capital',            'EQUITY',    'CREDIT', 2, TRUE,  FALSE, NULL),
    ('3200', 'Хуримтлагдсан ашиг',           'Retained earnings',        'EQUITY',    'CREDIT', 2, TRUE,  FALSE, NULL),
    ('4000', 'ОРЛОГО',                       'INCOME',                   'INCOME',    'CREDIT', 1, FALSE, FALSE, NULL),
    ('4100', 'Хүүгийн орлого',               'Interest income',          'INCOME',    'CREDIT', 2, TRUE,  FALSE, NULL),
    ('4200', 'Шимтгэлийн орлого',            'Fee and commission income','INCOME',    'CREDIT', 2, TRUE,  FALSE, NULL),
    ('4300', 'Торгуулийн орлого',            'Penalty income',           'INCOME',    'CREDIT', 2, TRUE,  FALSE, NULL),
    ('4400', 'Ханшийн олз',                  'FX gain',                  'INCOME',    'CREDIT', 2, TRUE,  FALSE, NULL),
    ('4500', 'Нөхөн төлөгдсөн зээл',         'Recovery income',          'INCOME',    'CREDIT', 2, TRUE,  FALSE, NULL),
    ('5000', 'ЗАРДАЛ',                       'EXPENSES',                 'EXPENSE',   'DEBIT',  1, FALSE, FALSE, NULL),
    ('5100', 'Хүүгийн зардал',               'Interest expense',         'EXPENSE',   'DEBIT',  2, TRUE,  FALSE, NULL),
    ('5200', 'Эрсдэлийн сангийн зардал',     'Provision expense',        'EXPENSE',   'DEBIT',  2, TRUE,  FALSE, NULL),
    ('5300', 'Зээлийн хасалтын зардал',      'Write-off expense',        'EXPENSE',   'DEBIT',  2, TRUE,  FALSE, NULL),
    ('5400', 'Үйл ажиллагааны зардал',       'Operating expense',        'EXPENSE',   'DEBIT',  2, TRUE,  FALSE, NULL),
    ('5500', 'Ханшийн гарз',                 'FX loss',                  'EXPENSE',   'DEBIT',  2, TRUE,  FALSE, NULL),
    ('9000', 'БАЛАНСЫН ГАДУУР',              'OFF BALANCE',              'OFF_BALANCE','DEBIT', 1, FALSE, FALSE, NULL),
    ('9100', 'Данснаас хассан зээл',         'Written-off loans',        'OFF_BALANCE','DEBIT', 2, TRUE,  FALSE, NULL),
    ('9200', 'Барьцаа хөрөнгө',              'Collateral held',          'OFF_BALANCE','DEBIT', 2, TRUE,  FALSE, NULL);

UPDATE accounting.chart_of_accounts c
   SET parent_id = p.id
  FROM accounting.chart_of_accounts p
 WHERE p.account_code = CASE
        WHEN c.level = 2 THEN left(c.account_code, 1) || '000'
        WHEN c.level = 3 THEN left(c.account_code, 2) || '00'
      END
   AND c.level > 1;

-- text2sql-new засвар: анх 2026-оос өмнөх үеийг 'CLOSED' гэж оруулдаг байсан ч
-- ck_period_closed (closed_at, closed_by заавал) зөрчиж seed бүхэлдээ унадаг байв.
-- Энд бүгдийг 'OPEN' оруулж, хэрэглэгч үүссэний дараа хаана
-- (seeds/001_demo_data.sql эсвэл text2sql-ийн өгөгдөл үүсгэгч).
INSERT INTO accounting.accounting_periods (period_code, fiscal_year, period_number, start_date, end_date, status)
SELECT to_char(d, 'YYYY-MM'), EXTRACT(YEAR FROM d)::SMALLINT, EXTRACT(MONTH FROM d)::SMALLINT,
       d::DATE, (d + INTERVAL '1 month - 1 day')::DATE,
       'OPEN'
  FROM generate_series(DATE '2025-01-01', DATE '2027-12-01', INTERVAL '1 month') d;

INSERT INTO accounting.journals (journal_code, journal_name, journal_type) VALUES
    ('GJ',      'Ерөнхий журнал',        'GENERAL'),
    ('LOAN_J',  'Зээлийн журнал',        'LOAN'),
    ('DEP_J',   'Хадгаламжийн журнал',   'DEPOSIT'),
    ('CASH_J',  'Кассын журнал',         'CASH'),
    ('ACCR_J',  'Хуримтлалын журнал',    'ACCRUAL'),
    ('FEE_J',   'Шимтгэлийн журнал',     'FEE'),
    ('FX_J',    'Ханшийн журнал',        'FX'),
    ('ADJ_J',   'Залруулгын журнал',     'ADJUSTMENT');

INSERT INTO accounting.tax_types (tax_code, tax_name, tax_rate, tax_base, valid_from) VALUES
    ('WHT_DEPOSIT', 'Хадгаламжийн хүүгийн татвар', 10.0, 'DEPOSIT_INTEREST', DATE '2024-01-01'),
    ('CIT',         'Аж ахуйн нэгжийн орлогын албан татвар', 10.0, 'PROFIT', DATE '2024-01-01'),
    ('VAT',         'Нэмэгдсэн өртгийн албан татвар', 10.0, 'FEE_INCOME', DATE '2024-01-01');

-- Автомат бичилтийн дүрэм: зээл олголт / төлөлт / хүү / шимтгэл
INSERT INTO accounting.accounting_rules (rule_code, rule_name, event_type, journal_id, valid_from)
SELECT v.code, v.name, v.evt, j.id, DATE '2024-01-01'
  FROM (VALUES
    ('AR_LOAN_DISB',   'Зээл олголт',            'LOAN_DISBURSEMENT', 'LOAN_J'),
    ('AR_LOAN_REPAY',  'Зээлийн эргэн төлөлт',   'LOAN_REPAYMENT',    'LOAN_J'),
    ('AR_INT_ACCRUAL', 'Хүүгийн хуримтлал',      'INTEREST_ACCRUAL',  'ACCR_J'),
    ('AR_FEE',         'Шимтгэл тооцох',         'FEE_CHARGE',        'FEE_J'),
    ('AR_PENALTY',     'Торгууль тооцох',        'PENALTY_CHARGE',    'ACCR_J'),
    ('AR_WRITEOFF',    'Зээл данснаас хасах',    'WRITE_OFF',         'ADJ_J'),
    ('AR_DEP_RECEIPT', 'Хадгаламж хүлээн авах',  'DEPOSIT_RECEIPT',   'DEP_J')
  ) AS v(code, name, evt, jcode)
  JOIN accounting.journals j ON j.journal_code = v.jcode;

INSERT INTO accounting.accounting_rule_lines (rule_id, line_no, dr_cr, gl_account_id, amount_source, description_template)
SELECT r.id, v.line_no, v.dc::system.dr_cr, coa.id, v.src, v.descr
  FROM (VALUES
    ('AR_LOAN_DISB',   1, 'D', '1210', 'PRINCIPAL', 'Зээл олголт — үндсэн'),
    ('AR_LOAN_DISB',   2, 'C', '1110', 'PRINCIPAL', 'Зээл олголт — касс'),
    ('AR_LOAN_REPAY',  1, 'D', '1110', 'TOTAL',     'Зээлийн төлөлт — касс'),
    ('AR_LOAN_REPAY',  2, 'C', '1210', 'PRINCIPAL', 'Зээлийн төлөлт — үндсэн'),
    ('AR_LOAN_REPAY',  3, 'C', '1300', 'INTEREST',  'Зээлийн төлөлт — хүү'),
    ('AR_LOAN_REPAY',  4, 'C', '1310', 'PENALTY',   'Зээлийн төлөлт — торгууль'),
    ('AR_INT_ACCRUAL', 1, 'D', '1300', 'INTEREST',  'Хүүгийн хуримтлал'),
    ('AR_INT_ACCRUAL', 2, 'C', '4100', 'INTEREST',  'Хүүгийн орлого'),
    ('AR_FEE',         1, 'D', '1320', 'FEE',       'Шимтгэлийн авлага'),
    ('AR_FEE',         2, 'C', '4200', 'FEE',       'Шимтгэлийн орлого'),
    ('AR_PENALTY',     1, 'D', '1310', 'PENALTY',   'Торгуулийн авлага'),
    ('AR_PENALTY',     2, 'C', '4300', 'PENALTY',   'Торгуулийн орлого'),
    ('AR_WRITEOFF',    1, 'D', '1290', 'PRINCIPAL', 'Эрсдэлийн сангаас'),
    ('AR_WRITEOFF',    2, 'C', '1230', 'PRINCIPAL', 'Чанаргүй зээлээс хасав'),
    ('AR_DEP_RECEIPT', 1, 'D', '1110', 'TOTAL',     'Хадгаламж — касс'),
    ('AR_DEP_RECEIPT', 2, 'C', '2110', 'TOTAL',     'Хадгаламжийн өглөг')
  ) AS v(rule_code, line_no, dc, acc, src, descr)
  JOIN accounting.accounting_rules r ON r.rule_code = v.rule_code
  JOIN accounting.chart_of_accounts coa ON coa.account_code = v.acc;

-- ---------------------------------------------------------------------
-- Эрсдэл, скоринг
-- ---------------------------------------------------------------------
INSERT INTO risk.risk_grades (grade_code, grade_name, min_score, max_score, pd_estimate, lgd_estimate,
                              rate_premium, requires_collateral, auto_reject, sort_order) VALUES
    ('AAA', 'Маш бага эрсдэлтэй', 850, 1000, 0.00500, 0.35, 0.0,  FALSE, FALSE, 1),
    ('AA',  'Бага эрсдэлтэй',     750,  849, 0.01500, 0.40, 1.2,  FALSE, FALSE, 2),
    ('A',   'Хэвийн',             650,  749, 0.03500, 0.45, 2.4,  FALSE, FALSE, 3),
    ('BBB', 'Дунд зэрэг',         550,  649, 0.07000, 0.50, 4.8,  TRUE,  FALSE, 4),
    ('BB',  'Өндөр',              450,  549, 0.15000, 0.55, 7.2,  TRUE,  FALSE, 5),
    ('B',   'Маш өндөр',          350,  449, 0.28000, 0.60, 12.0, TRUE,  FALSE, 6),
    ('C',   'Хүлээн зөвшөөрөхгүй',  0,  349, 0.50000, 0.70, 0.0,  TRUE,  TRUE,  7);

INSERT INTO risk.credit_score_models (model_code, model_name, model_version, model_type, min_score, max_score,
                                      cutoff_score, gini_coefficient, trained_at, deployed_at, status) VALUES
    ('APP_SCORE_IND', 'Хувь хүний хүсэлтийн оноо', 'v1.0', 'SCORECARD', 0, 1000, 450, 0.4200,
     DATE '2025-06-01', DATE '2025-09-01', 'CHAMPION'),
    ('BEH_SCORE_IND', 'Хувь хүний зан төлвийн оноо','v1.0', 'LOGISTIC_REGRESSION', 0, 1000, 400, 0.4800,
     DATE '2025-08-01', DATE '2025-11-01', 'ACTIVE'),
    ('APP_SCORE_SME', 'ЖДҮ-ийн хүсэлтийн оноо',    'v1.0', 'EXPERT_RULES', 0, 1000, 500, NULL,
     NULL, DATE '2025-09-01', 'ACTIVE');

INSERT INTO risk.credit_score_model_factors (model_id, factor_code, factor_name, factor_group, weight, max_points)
SELECT m.id, v.code, v.name, v.grp, v.w, v.pts
  FROM (VALUES
    ('APP_SCORE_IND', 'AGE',            'Нас',                        'DEMOGRAPHIC', 0.8000,  80),
    ('APP_SCORE_IND', 'INCOME',         'Сарын орлого',               'FINANCIAL',   2.0000, 200),
    ('APP_SCORE_IND', 'DTI',            'Өр/орлогын харьцаа',         'FINANCIAL',   2.5000, 250),
    ('APP_SCORE_IND', 'EMPLOYMENT_LEN', 'Ажилласан хугацаа',          'DEMOGRAPHIC', 1.2000, 120),
    ('APP_SCORE_IND', 'BUREAU_DPD',     'Зээлийн түүхийн хэтрэлт',    'BUREAU',      2.5000, 250),
    ('APP_SCORE_IND', 'RELATIONSHIP',   'Байгууллагатай харилцсан хугацаа','RELATIONSHIP',1.0000,100)
  ) AS v(mcode, code, name, grp, w, pts)
  JOIN risk.credit_score_models m ON m.model_code = v.mcode;

INSERT INTO risk.fraud_rules (rule_code, rule_name, rule_category, conditions, action, severity, risk_points) VALUES
    ('DUP_IDENTITY',   'Давхардсан регистр',        'IDENTITY',   '{"check": "registration_hash"}'::JSONB, 'BLOCK', 'CRITICAL', 100),
    ('VELOCITY_APP',   'Богино хугацаанд олон хүсэлт','APPLICATION','{"count": 3, "days": 7}'::JSONB,      'REVIEW','HIGH',      60),
    ('DEVICE_SHARED',  'Нэг төхөөрөмжөөс олон данс', 'DEVICE',    '{"max_accounts": 3}'::JSONB,           'ALERT', 'MEDIUM',    40),
    ('LARGE_FIRST_TXN','Анхны гүйлгээ хэт том',      'TRANSACTION','{"amount": 50000000}'::JSONB,          'REVIEW','HIGH',      70),
    ('NIGHT_ACTIVITY', 'Шөнийн цагийн идэвх',        'BEHAVIORAL', '{"from": "01:00", "to": "05:00"}'::JSONB,'LOG_ONLY','LOW',   10);

-- ---------------------------------------------------------------------
-- Барагдуулалт
-- ---------------------------------------------------------------------
INSERT INTO collection.collection_stages (stage_code, stage_name, min_dpd, max_dpd, responsible_unit, escalation_days, sort_order) VALUES
    ('REMINDER',    'Сануулга',           1,   7,   'SOFT_COLLECTION', 7,  1),
    ('SOFT_CALL',   'Утсаар холбогдох',   8,   30,  'SOFT_COLLECTION', 15, 2),
    ('FIELD_VISIT', 'Биечлэн уулзах',     31,  60,  'FIELD',           20, 3),
    ('FORMAL',      'Албан шаардлага',    61,  90,  'FIELD',           30, 4),
    ('PRE_LEGAL',   'Хууль зүйн өмнөх',   91,  180, 'LEGAL',           45, 5),
    ('LEGAL',       'Шүүхийн шат',        181, NULL,'LEGAL',           NULL, 6);

INSERT INTO collection.action_types (action_code, action_name, channel, cost_estimate, min_dpd) VALUES
    ('SMS_REMINDER',  'СМС сануулга',           'SMS',          100,   1),
    ('AUTO_CALL',     'Автомат дуудлага',       'CALL',         200,   3),
    ('MANUAL_CALL',   'Ажилтны дуудлага',       'CALL',        1000,   8),
    ('EMAIL_NOTICE',  'Имэйл сануулга',         'EMAIL',         50,   5),
    ('LETTER',        'Албан бичиг',            'LETTER',      5000,  31),
    ('FIELD_VISIT',   'Айлчлал',                'FIELD_VISIT',30000,  31),
    ('LEGAL_NOTICE',  'Хууль зүйн шаардлага',   'LEGAL_NOTICE',50000, 61),
    ('COURT_FILING',  'Шүүхэд нэхэмжлэл',       'COURT',     500000, 181);

INSERT INTO collection.collection_agencies (agency_code, agency_name, commission_rate) VALUES
    ('INTERNAL', 'Дотоод баг',                    0.0),
    ('AGENCY_A', 'Барагдуулалтын А компани',     15.0),
    ('AGENCY_B', 'Барагдуулалтын Б компани',     20.0);

-- ---------------------------------------------------------------------
-- Баримт, мэдэгдэл
-- ---------------------------------------------------------------------
INSERT INTO system.document_types (type_code, type_name, category, retention_years, is_mandatory, sensitivity) VALUES
    ('ID_CARD',        'Иргэний үнэмлэх',        'KYC',        10, TRUE,  'RESTRICTED'),
    ('ADDRESS_PROOF',  'Оршин суугаа хаягийн тодорхойлолт','KYC',10,TRUE,'CONFIDENTIAL'),
    ('INCOME_PROOF',   'Орлогын тодорхойлолт',   'FINANCIAL',  10, TRUE,  'CONFIDENTIAL'),
    ('LOAN_CONTRACT',  'Зээлийн гэрээ',          'CONTRACT',   15, TRUE,  'CONFIDENTIAL'),
    ('PLEDGE_AGREEMENT','Барьцааны гэрээ',       'COLLATERAL', 15, TRUE,  'CONFIDENTIAL'),
    ('VALUATION',      'Үнэлгээний тайлан',      'COLLATERAL', 10, FALSE, 'INTERNAL'),
    ('COMPANY_CERT',   'Улсын бүртгэлийн гэрчилгээ','KYC',     10, TRUE,  'CONFIDENTIAL'),
    ('BANK_STATEMENT', 'Банкны хуулга',          'FINANCIAL',   7, FALSE, 'CONFIDENTIAL'),
    ('COURT_DOC',      'Шүүхийн баримт',         'LEGAL',      20, FALSE, 'RESTRICTED');

INSERT INTO system.notification_templates (template_code, template_name, channel, subject_mn, body_mn, variables, event_trigger) VALUES
    ('PAYMENT_REMINDER', 'Төлөлтийн сануулга', 'SMS', NULL,
     'Эрхэм {{customer_name}}, таны {{loan_number}} зээлийн {{amount}}₮ төлбөр {{due_date}}-нд төлөгдөнө.',
     ARRAY['customer_name','loan_number','amount','due_date'], 'PAYMENT_DUE'),
    ('OVERDUE_NOTICE',   'Хугацаа хэтэрсэн',  'SMS', NULL,
     'Эрхэм {{customer_name}}, таны {{loan_number}} зээл {{dpd}} хоног хэтэрсэн. Дүн: {{amount}}₮.',
     ARRAY['customer_name','loan_number','dpd','amount'], 'LOAN_OVERDUE'),
    ('LOAN_APPROVED',    'Зээл батлагдсан',   'SMS', NULL,
     'Баяр хүргэе! Таны {{amount}}₮ зээлийн хүсэлт батлагдлаа. Салбартаа хандана уу.',
     ARRAY['amount'], 'LOAN_APPROVED'),
    ('PAYMENT_RECEIVED', 'Төлбөр хүлээн авав','SMS', NULL,
     'Таны {{amount}}₮ төлбөр хүлээн авлаа. Үлдэгдэл: {{balance}}₮. Баярлалаа.',
     ARRAY['amount','balance'], 'PAYMENT_POSTED'),
    ('KYC_EXPIRING',     'KYC шинэчлэх',      'EMAIL', 'KYC мэдээлэл шинэчлэх шаардлагатай',
     'Эрхэм {{customer_name}}, таны бүртгэлийн мэдээлэл {{expiry_date}}-нд хүчингүй болно.',
     ARRAY['customer_name','expiry_date'], 'KYC_EXPIRING');
