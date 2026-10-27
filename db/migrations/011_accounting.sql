-- =====================================================================
-- 011_accounting.sql — Давхар бичилтийн нягтлан бодох бүртгэл
-- ---------------------------------------------------------------------
-- Санхүүгийн байгууллагын ЗҮРХ. Дүрмүүд:
--   1. Журналын бичилт бүрд Σ дебит = Σ кредит (валют тус бүрээр).
--      → DEFERRABLE CONSTRAINT TRIGGER (triggers/001_triggers.sql):
--        мөрүүдийг оруулж дуустал шалгахгүй, COMMIT дээр шалгана.
--   2. Батлагдсан (POSTED) бичилтийг ЗАСАХГҮЙ — зөвхөн сторно (reversal).
--   3. Хаагдсан тайлант үед бичилт оруулахгүй.
--   4. Операцийн гүйлгээ бүр журналтай холбогдоно (§22.19).
-- =====================================================================

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------------
-- Дансны төлөвлөгөө (мод бүтэц)
-- ---------------------------------------------------------------------
CREATE TABLE accounting.chart_of_accounts (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_code      VARCHAR(20) NOT NULL,
    account_name_mn   VARCHAR(200) NOT NULL,
    account_name_en   VARCHAR(200),
    parent_id         BIGINT REFERENCES accounting.chart_of_accounts (id),
    account_type      system.gl_account_type NOT NULL,
    normal_balance    system.balance_side    NOT NULL,
    level             SMALLINT NOT NULL DEFAULT 1 CHECK (level BETWEEN 1 AND 8),
    is_postable       BOOLEAN  NOT NULL DEFAULT TRUE,     -- зөвхөн навч дансанд бичилт хийнэ
    is_control        BOOLEAN  NOT NULL DEFAULT FALSE,    -- дэд дэвтэртэй тулгагдана
    control_domain    VARCHAR(30) CHECK (control_domain IN ('LOAN', 'DEPOSIT', 'CUSTOMER_ACCOUNT', 'FIXED_ASSET')),
    requires_branch   BOOLEAN  NOT NULL DEFAULT TRUE,
    requires_currency BOOLEAN  NOT NULL DEFAULT TRUE,
    allow_manual_entry BOOLEAN NOT NULL DEFAULT FALSE,    -- гараар бичилт зөвшөөрөх эсэх
    regulatory_code   VARCHAR(30),                        -- зохицуулагчийн тайлангийн мөр
    is_active         BOOLEAN  NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by        BIGINT,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by        BIGINT,
    version           INTEGER  NOT NULL DEFAULT 1,
    deleted_at        TIMESTAMPTZ,
    CONSTRAINT uq_coa_code UNIQUE (account_code),
    CONSTRAINT ck_coa_no_self_parent CHECK (parent_id IS NULL OR parent_id <> id),
    CONSTRAINT ck_coa_control CHECK (NOT is_control OR control_domain IS NOT NULL)
);
COMMENT ON TABLE  accounting.chart_of_accounts IS 'Дансны нэгдсэн төлөвлөгөө — мод бүтэц, зөвхөн навчид бичилт';
COMMENT ON COLUMN accounting.chart_of_accounts.is_control IS
    'Хяналтын данс: үлдэгдэл нь дэд дэвтэртэй (зээл/хадгаламж) тулгагдах ёстой';

-- ---------------------------------------------------------------------
-- Тайлант үе
-- ---------------------------------------------------------------------
CREATE TABLE accounting.accounting_periods (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    period_code    VARCHAR(10) NOT NULL,                  -- 2026-01
    fiscal_year    SMALLINT NOT NULL CHECK (fiscal_year BETWEEN 2000 AND 2100),
    period_number  SMALLINT NOT NULL CHECK (period_number BETWEEN 1 AND 13),
    start_date     DATE NOT NULL,
    end_date       DATE NOT NULL,
    status         VARCHAR(20) NOT NULL DEFAULT 'OPEN'
                   CHECK (status IN ('FUTURE', 'OPEN', 'CLOSING', 'CLOSED', 'LOCKED')),
    closed_at      TIMESTAMPTZ,
    closed_by      BIGINT,
    locked_at      TIMESTAMPTZ,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by     BIGINT,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by     BIGINT,
    version        INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT uq_period_code UNIQUE (period_code),
    CONSTRAINT uq_period_year_num UNIQUE (fiscal_year, period_number),
    CONSTRAINT ck_period_dates CHECK (end_date >= start_date),
    CONSTRAINT ck_period_closed CHECK (status <> 'CLOSED' OR (closed_at IS NOT NULL AND closed_by IS NOT NULL)),
    CONSTRAINT ex_period_overlap EXCLUDE USING gist (daterange(start_date, end_date, '[]') WITH &&)
);
COMMENT ON CONSTRAINT ex_period_overlap ON accounting.accounting_periods IS
    'Тайлант үеүүд огтлолцохгүй — нэг өдөр яг нэг үед харьяалагдана';

-- Журналын дэвтэр
CREATE TABLE accounting.journals (
    id            SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    journal_code  system.code_key NOT NULL,
    journal_name  VARCHAR(150)    NOT NULL,
    journal_type  VARCHAR(20)     NOT NULL
                  CHECK (journal_type IN ('GENERAL', 'LOAN', 'DEPOSIT', 'CASH', 'FEE', 'ACCRUAL', 'FX', 'CLOSING', 'ADJUSTMENT')),
    auto_generated BOOLEAN NOT NULL DEFAULT TRUE,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_journals_code UNIQUE (journal_code)
);

-- ---------------------------------------------------------------------
-- Журналын бичилт (толгой)
-- ---------------------------------------------------------------------
CREATE TABLE accounting.journal_entries (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entry_no          VARCHAR(30) NOT NULL,
    journal_id        SMALLINT NOT NULL REFERENCES accounting.journals (id),
    period_id         BIGINT   NOT NULL REFERENCES accounting.accounting_periods (id),
    branch_id         BIGINT   REFERENCES core.branches (id),

    entry_date        DATE NOT NULL,                      -- бичилтийн огноо
    value_date        DATE NOT NULL,                      -- хүчинтэй огноо
    posting_date      DATE NOT NULL,                      -- дэвтэрт бүртгэсэн огноо (partition key of lines)

    description       VARCHAR(500) NOT NULL,
    reference         VARCHAR(100),
    source_system     VARCHAR(30) NOT NULL DEFAULT 'CORE'
                      CHECK (source_system IN ('CORE', 'LOAN', 'DEPOSIT', 'TELLER', 'BATCH', 'MANUAL', 'INTEGRATION')),
    source_entity_type system.entity_type,
    source_entity_id  UUID,
    source_transaction_id BIGINT,                         -- account.transactions (сул холбоос)

    currency_code     system.currency_code NOT NULL DEFAULT 'MNT',
    total_debit       system.money_positive NOT NULL DEFAULT 0,
    total_credit      system.money_positive NOT NULL DEFAULT 0,

    status            VARCHAR(20) NOT NULL DEFAULT 'DRAFT'
                      CHECK (status IN ('DRAFT', 'PENDING_APPROVAL', 'POSTED', 'REVERSED', 'CANCELLED')),
    posted_at         TIMESTAMPTZ,
    posted_by         BIGINT,
    approved_by       BIGINT,
    reversal_of_id    BIGINT REFERENCES accounting.journal_entries (id),
    reversed_by_id    BIGINT REFERENCES accounting.journal_entries (id),
    reversal_reason   VARCHAR(300),
    is_reversal       BOOLEAN NOT NULL DEFAULT FALSE,

    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by        BIGINT,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by        BIGINT,
    version           INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT uq_journal_entry_no UNIQUE (entry_no),
    CONSTRAINT ck_je_balanced CHECK (total_debit = total_credit),
    CONSTRAINT ck_je_posted CHECK (status <> 'POSTED' OR (posted_at IS NOT NULL AND posted_by IS NOT NULL)),
    CONSTRAINT ck_je_reversal CHECK (NOT is_reversal OR reversal_of_id IS NOT NULL),
    CONSTRAINT ck_je_reversal_reason CHECK (status <> 'REVERSED' OR reversal_reason IS NOT NULL)
);
COMMENT ON CONSTRAINT ck_je_balanced ON accounting.journal_entries IS
    'Толгойн түвшний тэнцэл. Мөрүүдийн нийлбэртэй тохирохыг DEFERRABLE trigger баталгаажуулна';

-- ---------------------------------------------------------------------
-- Журналын мөр (партицлагдсан — хамгийн их эзэлхүүнтэй)
-- ---------------------------------------------------------------------
CREATE SEQUENCE accounting.seq_journal_lines;
CREATE TABLE accounting.journal_lines (
    id                BIGINT NOT NULL DEFAULT nextval('accounting.seq_journal_lines'),
    entry_id          BIGINT   NOT NULL REFERENCES accounting.journal_entries (id) ON DELETE CASCADE,
    posting_date      DATE     NOT NULL,
    line_no           SMALLINT NOT NULL CHECK (line_no > 0),
    gl_account_id     BIGINT   NOT NULL REFERENCES accounting.chart_of_accounts (id),
    branch_id         BIGINT   REFERENCES core.branches (id),

    dr_cr             system.dr_cr NOT NULL,
    amount            system.money_positive NOT NULL CHECK (amount > 0),
    currency_code     system.currency_code NOT NULL DEFAULT 'MNT',
    fx_rate           system.fx_rate,
    base_amount       system.money_positive NOT NULL,     -- MNT дүн (тайлан бүхэлдээ MNT-д)

    -- Дэд дэвтрийн холбоос (хяналтын дансны тулгалт)
    subledger_type    system.entity_type,
    subledger_id      UUID,
    customer_id       UUID REFERENCES customer.customers (id),
    product_id        BIGINT REFERENCES product.products (id),
    cost_center       VARCHAR(20),

    description       VARCHAR(300),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id, posting_date),
    CONSTRAINT uq_journal_line UNIQUE (entry_id, line_no, posting_date),
    CONSTRAINT ck_jl_fx CHECK (currency_code = 'MNT' OR fx_rate IS NOT NULL)
) PARTITION BY RANGE (posting_date);

CREATE TABLE accounting.journal_lines_2025 PARTITION OF accounting.journal_lines
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE accounting.journal_lines_2026 PARTITION OF accounting.journal_lines
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE accounting.journal_lines_2027 PARTITION OF accounting.journal_lines
    FOR VALUES FROM ('2027-01-01') TO ('2028-01-01');
CREATE TABLE accounting.journal_lines_default PARTITION OF accounting.journal_lines DEFAULT;

COMMENT ON COLUMN accounting.journal_lines.base_amount IS
    'MNT-д хөрвүүлсэн дүн. Тэнцлийн шалгалт БАЗА валютаар хийгдэнэ — ханшийн зөрүү FX данс руу';
COMMENT ON COLUMN accounting.journal_lines.subledger_id IS
    'Хяналтын дансыг дэд дэвтэртэй тулгах холбоос (жишээ: зээлийн id)';

-- ---------------------------------------------------------------------
-- Автомат бичилтийн дүрэм (гүйлгээний төрөл → журналын загвар)
-- ---------------------------------------------------------------------
CREATE TABLE accounting.accounting_rules (
    id             SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    rule_code      system.code_key NOT NULL,
    rule_name      VARCHAR(200)    NOT NULL,
    event_type     VARCHAR(40)     NOT NULL
                   CHECK (event_type IN ('LOAN_DISBURSEMENT', 'LOAN_REPAYMENT', 'INTEREST_ACCRUAL',
                                         'PENALTY_CHARGE', 'FEE_CHARGE', 'WRITE_OFF', 'RECOVERY',
                                         'DEPOSIT_RECEIPT', 'DEPOSIT_WITHDRAWAL', 'DEPOSIT_INTEREST',
                                         'FX_REVALUATION', 'PROVISION', 'TAX_WITHHOLDING')),
    journal_id     SMALLINT NOT NULL REFERENCES accounting.journals (id),
    product_id     BIGINT   REFERENCES product.products (id),   -- NULL = бүх бүтээгдэхүүнд
    currency_code  system.currency_code,
    is_active      BOOLEAN  NOT NULL DEFAULT TRUE,
    valid_from     DATE     NOT NULL,
    valid_to       DATE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by     BIGINT,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by     BIGINT,
    version        INTEGER  NOT NULL DEFAULT 1,
    CONSTRAINT uq_accounting_rules_code UNIQUE (rule_code),
    CONSTRAINT ck_acc_rule_period CHECK (valid_to IS NULL OR valid_to >= valid_from)
);
COMMENT ON TABLE accounting.accounting_rules IS
    'Ямар үйл явдал ямар бичилт үүсгэхийг ӨГӨГДӨЛД тодорхойлно — шинэ бүтээгдэхүүнд код бичихгүй';

CREATE TABLE accounting.accounting_rule_lines (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    rule_id        SMALLINT NOT NULL REFERENCES accounting.accounting_rules (id) ON DELETE CASCADE,
    line_no        SMALLINT NOT NULL CHECK (line_no > 0),
    dr_cr          system.dr_cr NOT NULL,
    gl_account_id  BIGINT   REFERENCES accounting.chart_of_accounts (id),
    gl_account_source VARCHAR(30)                        -- динамик данс: бүтээгдэхүүнээс авах
                   CHECK (gl_account_source IN ('PRODUCT_PRINCIPAL', 'PRODUCT_INTEREST_INCOME',
                                                'PRODUCT_PENALTY_INCOME', 'PRODUCT_FEE_INCOME',
                                                'ACCOUNT_TYPE_GL', 'PARAMETER')),
    amount_source  VARCHAR(30) NOT NULL
                   CHECK (amount_source IN ('PRINCIPAL', 'INTEREST', 'PENALTY', 'FEE', 'TAX', 'TOTAL', 'NET')),
    description_template VARCHAR(300),
    CONSTRAINT uq_acc_rule_line UNIQUE (rule_id, line_no),
    CONSTRAINT ck_acc_rule_line_account CHECK (gl_account_id IS NOT NULL OR gl_account_source IS NOT NULL)
);

-- ---------------------------------------------------------------------
-- Дансны үлдэгдэл (тайлант үе бүрээр — trial balance хурдан)
-- ---------------------------------------------------------------------
CREATE TABLE accounting.ledger_balances (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    gl_account_id     BIGINT   NOT NULL REFERENCES accounting.chart_of_accounts (id),
    period_id         BIGINT   NOT NULL REFERENCES accounting.accounting_periods (id),
    branch_id         BIGINT   REFERENCES core.branches (id),
    currency_code     system.currency_code NOT NULL DEFAULT 'MNT',
    opening_debit     system.money_positive NOT NULL DEFAULT 0,
    opening_credit    system.money_positive NOT NULL DEFAULT 0,
    period_debit      system.money_positive NOT NULL DEFAULT 0,
    period_credit     system.money_positive NOT NULL DEFAULT 0,
    closing_debit     system.money_positive NOT NULL DEFAULT 0,
    closing_credit    system.money_positive NOT NULL DEFAULT 0,
    base_closing_balance system.money_amount NOT NULL DEFAULT 0,
    last_updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_ledger_balance UNIQUE (gl_account_id, period_id, branch_id, currency_code)
);
COMMENT ON TABLE accounting.ledger_balances IS
    'Үеийн үлдэгдлийн нэгтгэл — trial balance-ыг 100 сая мөр уншихгүйгээр гаргана';

CREATE TABLE accounting.tax_types (
    id             SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tax_code       system.code_key NOT NULL,
    tax_name       VARCHAR(150)    NOT NULL,
    tax_rate       system.percent_share NOT NULL,
    tax_base       VARCHAR(30) NOT NULL
                   CHECK (tax_base IN ('INTEREST_INCOME', 'FEE_INCOME', 'DEPOSIT_INTEREST', 'PROFIT', 'OTHER')),
    gl_payable_account_id BIGINT REFERENCES accounting.chart_of_accounts (id),
    valid_from     DATE NOT NULL,
    valid_to       DATE,
    is_active      BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_tax_types_code UNIQUE (tax_code),
    CONSTRAINT ck_tax_period CHECK (valid_to IS NULL OR valid_to >= valid_from)
);

-- Ханшийн тэгшитгэл (валютын нээлттэй позиц)
CREATE TABLE accounting.fx_revaluations (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    revaluation_date  DATE NOT NULL,
    gl_account_id     BIGINT NOT NULL REFERENCES accounting.chart_of_accounts (id),
    currency_code     system.currency_code NOT NULL,
    foreign_balance   system.money_amount NOT NULL,
    old_rate          system.fx_rate NOT NULL,
    new_rate          system.fx_rate NOT NULL,
    old_base_balance  system.money_amount NOT NULL,
    new_base_balance  system.money_amount NOT NULL,
    revaluation_gain_loss system.money_amount NOT NULL,
    journal_entry_id  BIGINT REFERENCES accounting.journal_entries (id),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by        BIGINT,
    CONSTRAINT uq_fx_reval UNIQUE (revaluation_date, gl_account_id, currency_code)
);
COMMENT ON TABLE accounting.fx_revaluations IS
    'Гадаад валютын үлдэгдлийг тайлант үеийн ханшаар дахин үнэлэх — ханшийн олз/гарз';
