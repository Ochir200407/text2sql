-- =====================================================================
-- 006_loan.sql — Зээлийн бүрэн lifecycle
-- ---------------------------------------------------------------------
-- APPLICATION → UNDER_REVIEW → APPROVED/REJECTED → CONTRACTED → DISBURSED
--   → ACTIVE → OVERDUE → RESTRUCTURED → WRITTEN_OFF → CLOSED
--
-- Гол шийдвэрүүд:
--  * loans.* үлдэгдлийн багана нь ЗОРИУДЫН denormalization — 10 сая
--    гүйлгээтэй үед портфелийн тайлан бүрд SUM() хийхгүй. Эх сурвалж нь
--    schedule + payments; validation/001_validation.sql-д тулгана.
--  * loans UNIQUE (id, currency_code) — хүүхэд хүснэгтүүд composite FK-ээр
--    валютын зөрчлийг ДЕКЛАРАТИВААР хаана (§21.7 currency consistency).
--  * Их эзэлхүүнтэй append-only хүснэгтүүд (accrual, DPD snapshot) —
--    RANGE partition, IDENTITY биш sequence DEFAULT (partition-д найдвартай).
-- =====================================================================

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------------
-- Лавлах: төлөв ба шилжилт
-- ---------------------------------------------------------------------
CREATE TABLE loan.loan_application_statuses (
    id            SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    status_code   system.code_key NOT NULL,
    status_name   VARCHAR(100)    NOT NULL,
    is_terminal   BOOLEAN  NOT NULL DEFAULT FALSE,
    is_approved   BOOLEAN  NOT NULL DEFAULT FALSE,
    sort_order    SMALLINT NOT NULL DEFAULT 0,
    CONSTRAINT uq_loan_app_status_code UNIQUE (status_code)
);

CREATE TABLE loan.loan_statuses (
    id             SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    status_code    system.code_key NOT NULL,
    status_name    VARCHAR(100)    NOT NULL,
    is_active_loan BOOLEAN  NOT NULL DEFAULT TRUE,   -- портфельд тооцогдох эсэх
    is_terminal    BOOLEAN  NOT NULL DEFAULT FALSE,
    accrues_interest BOOLEAN NOT NULL DEFAULT TRUE,  -- хүү бодогдох эсэх
    sort_order     SMALLINT NOT NULL DEFAULT 0,
    CONSTRAINT uq_loan_statuses_code UNIQUE (status_code)
);
COMMENT ON COLUMN loan.loan_statuses.accrues_interest IS
    'WRITTEN_OFF, CLOSED төлөвт хүү бодохгүй — хүү бодох ажил энэ талбараар шүүнэ';

CREATE TABLE loan.loan_status_transitions (
    id             SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    from_status_id SMALLINT NOT NULL REFERENCES loan.loan_statuses (id),
    to_status_id   SMALLINT NOT NULL REFERENCES loan.loan_statuses (id),
    requires_approval BOOLEAN NOT NULL DEFAULT FALSE,
    required_permission system.code_key,
    description    VARCHAR(200),
    CONSTRAINT uq_loan_transition UNIQUE (from_status_id, to_status_id),
    CONSTRAINT ck_loan_transition_diff CHECK (from_status_id <> to_status_id)
);
COMMENT ON TABLE loan.loan_status_transitions IS
    'Зөвшөөрөгдсөн төлөвийн шилжилт. trigger нь энэ хүснэгтгүй шилжилтийг хориглоно (§21 invalid status transition)';

-- Активын ангилал (БОДЛОГЫН БОСГО — өгөгдөлд, кодонд биш)
CREATE TABLE loan.classification_grades (
    id                SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    grade_code        system.code_key NOT NULL,
    grade_name_mn     VARCHAR(100)    NOT NULL,
    grade_name_en     VARCHAR(100)    NOT NULL,
    min_dpd           SMALLINT NOT NULL CHECK (min_dpd >= 0),
    max_dpd           SMALLINT,                          -- NULL = хязгааргүй
    provision_rate    system.percent_share NOT NULL,     -- эрсдэлийн сангийн хувь
    is_npl            BOOLEAN  NOT NULL DEFAULT FALSE,
    is_performing     BOOLEAN  NOT NULL DEFAULT TRUE,
    sort_order        SMALLINT NOT NULL DEFAULT 0,
    regulation_ref    VARCHAR(200),
    CONSTRAINT uq_class_grade_code UNIQUE (grade_code),
    CONSTRAINT ck_class_grade_dpd CHECK (max_dpd IS NULL OR max_dpd >= min_dpd)
);
COMMENT ON TABLE loan.classification_grades IS
    'Активын ангилал ба эрсдэлийн сангийн хувь. Босго нь зохицуулагчийн журмаар өөрчлөгддөг тул '
    'ӨГӨГДӨЛД хадгална — seed дэх утгыг мөрдөж буй журмаар баталгаажуулна';

-- ---------------------------------------------------------------------
-- Зээлийн хүсэлт
-- ---------------------------------------------------------------------
CREATE TABLE loan.loan_applications (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    application_no       VARCHAR(30) NOT NULL,
    customer_id          UUID     NOT NULL REFERENCES customer.customers (id),
    product_id           BIGINT   NOT NULL REFERENCES product.products (id),
    branch_id            BIGINT   NOT NULL REFERENCES core.branches (id),
    status_id            SMALLINT NOT NULL REFERENCES loan.loan_application_statuses (id),
    channel              VARCHAR(20) NOT NULL DEFAULT 'BRANCH'
                         CHECK (channel IN ('BRANCH', 'MOBILE', 'WEB', 'AGENT', 'CALL_CENTER', 'PARTNER')),

    requested_amount     system.money_positive NOT NULL,
    requested_term_months SMALLINT NOT NULL CHECK (requested_term_months > 0),
    currency_code        system.currency_code NOT NULL DEFAULT 'MNT',
    loan_purpose         VARCHAR(300),
    purpose_category     VARCHAR(30)
                         CHECK (purpose_category IN ('CONSUMER', 'BUSINESS', 'MORTGAGE', 'AUTO', 'EDUCATION',
                                                     'MEDICAL', 'AGRICULTURE', 'REFINANCE', 'OTHER')),
    repayment_source     VARCHAR(200),

    -- Хүсэлт өгөх үеийн санхүүгийн зураг (шийдвэрийн мөчид хөлдөөсөн)
    declared_income      system.money_positive,
    declared_expense     system.money_positive,
    existing_debt_payment system.money_positive,
    dti_ratio            system.percent_share,

    approved_amount      system.money_positive,
    approved_term_months SMALLINT CHECK (approved_term_months > 0),
    approved_rate        system.rate_pct,

    loan_officer_id      BIGINT REFERENCES core.employees (id),
    submitted_at         TIMESTAMPTZ,
    decided_at           TIMESTAMPTZ,
    expires_at           TIMESTAMPTZ,
    rejection_reason     VARCHAR(300),
    withdrawal_reason    VARCHAR(300),
    notes                TEXT,
    extra_data           JSONB NOT NULL DEFAULT '{}'::JSONB,

    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by           BIGINT,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by           BIGINT,
    version              INTEGER     NOT NULL DEFAULT 1,
    deleted_at           TIMESTAMPTZ,
    CONSTRAINT uq_loan_app_no UNIQUE (application_no),
    CONSTRAINT ck_loan_app_approved CHECK (
        (approved_amount IS NULL AND approved_term_months IS NULL AND approved_rate IS NULL)
        OR (approved_amount IS NOT NULL AND approved_term_months IS NOT NULL AND approved_rate IS NOT NULL)
    ),
    CONSTRAINT ck_loan_app_decided CHECK (decided_at IS NULL OR submitted_at IS NULL OR decided_at >= submitted_at)
);
COMMENT ON TABLE loan.loan_applications IS 'Зээлийн хүсэлт. Батлагдсаны дараа loans мөр үүснэ (1:0..1)';

CREATE TABLE loan.loan_application_documents (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    application_id  UUID        NOT NULL REFERENCES loan.loan_applications (id) ON DELETE CASCADE,
    document_id     UUID,                                -- system.documents(id) — 014-т FK
    doc_type        VARCHAR(40) NOT NULL,
    is_mandatory    BOOLEAN     NOT NULL DEFAULT FALSE,
    is_received     BOOLEAN     NOT NULL DEFAULT FALSE,
    received_at     TIMESTAMPTZ,
    verified_by     BIGINT REFERENCES core.employees (id),
    notes           VARCHAR(300),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by      BIGINT,
    CONSTRAINT ck_loan_app_doc_received CHECK (NOT is_received OR received_at IS NOT NULL)
);

-- Зээлийн судалгаа (шинжээчийн үнэлгээ)
CREATE TABLE loan.loan_assessments (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    application_id      UUID     NOT NULL REFERENCES loan.loan_applications (id) ON DELETE CASCADE,
    assessment_type     VARCHAR(20) NOT NULL DEFAULT 'CREDIT'
                        CHECK (assessment_type IN ('CREDIT', 'COLLATERAL', 'LEGAL', 'FIELD_VISIT', 'RISK', 'COMPLIANCE')),
    assessed_by         BIGINT   NOT NULL REFERENCES core.employees (id),
    verified_income     system.money_positive,
    verified_expense    system.money_positive,
    calculated_dti      system.percent_share,
    calculated_ltv      system.percent_share,
    credit_score        SMALLINT CHECK (credit_score BETWEEN 0 AND 1000),
    risk_grade_code     VARCHAR(20),
    recommended_amount  system.money_positive,
    recommended_term_months SMALLINT CHECK (recommended_term_months > 0),
    recommended_rate    system.rate_pct,
    recommendation      VARCHAR(30) NOT NULL   -- text2sql-new засвар: 20→30 ('APPROVE_WITH_CONDITIONS' = 23)
                        CHECK (recommendation IN ('APPROVE', 'APPROVE_WITH_CONDITIONS', 'REJECT', 'NEED_MORE_INFO')),
    conditions          TEXT,
    findings            TEXT,
    assessed_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by          BIGINT
);
COMMENT ON TABLE loan.loan_assessments IS 'Судалгаа — нэг хүсэлтэд олон төрлийн үнэлгээ (зээл, барьцаа, хууль)';

-- Шийдвэр (эрх бүхий этгээд/хороо)
CREATE TABLE loan.loan_decisions (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    application_id    UUID     NOT NULL REFERENCES loan.loan_applications (id) ON DELETE CASCADE,
    decision_level    VARCHAR(20) NOT NULL DEFAULT 'OFFICER'
                      CHECK (decision_level IN ('AUTO', 'OFFICER', 'BRANCH_MANAGER', 'CREDIT_COMMITTEE', 'BOARD')),
    decision          VARCHAR(20) NOT NULL
                      CHECK (decision IN ('APPROVED', 'REJECTED', 'DEFERRED', 'ESCALATED', 'CANCELLED')),
    decided_by        BIGINT REFERENCES core.employees (id),
    committee_ref     VARCHAR(100),                      -- хурлын дугаар
    approved_amount   system.money_positive,
    approved_term_months SMALLINT CHECK (approved_term_months > 0),
    approved_rate     system.rate_pct,
    conditions        TEXT,
    reason            VARCHAR(500),
    decided_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_final          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by        BIGINT,
    CONSTRAINT ck_loan_decision_approved CHECK (decision <> 'APPROVED' OR approved_amount IS NOT NULL),
    CONSTRAINT ck_loan_decision_rejected CHECK (decision <> 'REJECTED' OR reason IS NOT NULL),
    CONSTRAINT ck_loan_decision_maker    CHECK (decision_level = 'AUTO' OR decided_by IS NOT NULL)
);
COMMENT ON TABLE loan.loan_decisions IS 'Шийдвэрийн бүртгэл — дарж бичихгүй. Эцсийн шийдвэр is_final = TRUE';

-- ---------------------------------------------------------------------
-- Зээл (гол хүснэгт)
-- ---------------------------------------------------------------------
CREATE TABLE loan.loans (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    loan_number           VARCHAR(30) NOT NULL,
    application_id        UUID     REFERENCES loan.loan_applications (id),
    customer_id           UUID     NOT NULL REFERENCES customer.customers (id),
    product_id            BIGINT   NOT NULL REFERENCES product.products (id),
    branch_id             BIGINT   NOT NULL REFERENCES core.branches (id),
    status_id             SMALLINT NOT NULL REFERENCES loan.loan_statuses (id),
    loan_officer_id       BIGINT   REFERENCES core.employees (id),
    currency_code         system.currency_code NOT NULL DEFAULT 'MNT',

    -- Гэрээний нөхцөл
    principal_amount      system.money_positive NOT NULL CHECK (principal_amount > 0),
    interest_rate         system.rate_pct       NOT NULL,
    penalty_rate          system.rate_pct       NOT NULL DEFAULT 0,
    effective_rate        system.rate_pct,                   -- EIR (шимтгэл оруулсан)
    rate_type             system.rate_type       NOT NULL DEFAULT 'FIXED',
    interest_method       system.interest_method NOT NULL DEFAULT 'SIMPLE',
    day_count_convention  system.day_count_conv  NOT NULL DEFAULT 'ACT_365',
    amortization_type     system.amortization_type NOT NULL DEFAULT 'ANNUITY',
    term_months           SMALLINT NOT NULL CHECK (term_months > 0),
    payment_frequency     system.period_unit NOT NULL DEFAULT 'MONTH',
    grace_period_days     SMALLINT NOT NULL DEFAULT 0 CHECK (grace_period_days >= 0),

    -- Огноо
    contract_date         DATE,
    disbursement_date     DATE,
    first_payment_date    DATE,
    maturity_date         DATE,
    closed_date           DATE,

    -- Үлдэгдэл (denormalized, төлбөр/хуримтлал бүрд шинэчлэгдэнэ)
    disbursed_amount      system.money_positive NOT NULL DEFAULT 0,
    principal_outstanding system.money_positive NOT NULL DEFAULT 0,
    interest_outstanding  system.money_positive NOT NULL DEFAULT 0,
    penalty_outstanding   system.money_positive NOT NULL DEFAULT 0,
    fee_outstanding       system.money_positive NOT NULL DEFAULT 0,
    principal_paid        system.money_positive NOT NULL DEFAULT 0,
    interest_paid         system.money_positive NOT NULL DEFAULT 0,
    penalty_paid          system.money_positive NOT NULL DEFAULT 0,
    fee_paid              system.money_positive NOT NULL DEFAULT 0,
    written_off_amount    system.money_positive NOT NULL DEFAULT 0,
    total_outstanding     system.money_amount GENERATED ALWAYS AS
                          (principal_outstanding + interest_outstanding + penalty_outstanding + fee_outstanding) STORED,

    -- Хугацаа хэтрэлт
    overdue_principal     system.money_positive NOT NULL DEFAULT 0,
    overdue_interest      system.money_positive NOT NULL DEFAULT 0,
    days_past_due         INTEGER  NOT NULL DEFAULT 0 CHECK (days_past_due >= 0),
    max_days_past_due     INTEGER  NOT NULL DEFAULT 0 CHECK (max_days_past_due >= 0),
    first_overdue_date    DATE,
    last_payment_date     DATE,
    classification_id     SMALLINT REFERENCES loan.classification_grades (id),
    is_npl                BOOLEAN  NOT NULL DEFAULT FALSE,
    is_restructured       BOOLEAN  NOT NULL DEFAULT FALSE,
    restructure_count     SMALLINT NOT NULL DEFAULT 0 CHECK (restructure_count >= 0),

    notes                 TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by            BIGINT,
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by            BIGINT,
    version               INTEGER     NOT NULL DEFAULT 1,
    deleted_at            TIMESTAMPTZ,

    CONSTRAINT uq_loans_number UNIQUE (loan_number),
    -- Composite FK-ийн зорилгоор: хүүхэд хүснэгт валютыг зөрүүлж чадахгүй
    CONSTRAINT uq_loans_id_currency UNIQUE (id, currency_code),
    CONSTRAINT ck_loans_maturity CHECK (maturity_date IS NULL OR disbursement_date IS NULL
                                        OR maturity_date > disbursement_date),
    CONSTRAINT ck_loans_first_payment CHECK (first_payment_date IS NULL OR disbursement_date IS NULL
                                             OR first_payment_date >= disbursement_date),
    CONSTRAINT ck_loans_disbursed_le_principal CHECK (disbursed_amount <= principal_amount),
    CONSTRAINT ck_loans_dpd_max CHECK (max_days_past_due >= days_past_due),
    CONSTRAINT ck_loans_overdue_le_outstanding CHECK (overdue_principal <= principal_outstanding),
    CONSTRAINT ck_loans_closed CHECK (closed_date IS NULL OR disbursement_date IS NULL
                                      OR closed_date >= disbursement_date)
);
COMMENT ON TABLE  loan.loans IS 'Зээлийн гол хүснэгт. Үлдэгдлийн багана нь гүйцэтгэлийн зорилгоор denormalized';
COMMENT ON COLUMN loan.loans.total_outstanding IS 'Generated column — үргэлж дөрвөн үлдэгдлийн нийлбэр (алдаа гарах боломжгүй)';
COMMENT ON COLUMN loan.loans.effective_rate    IS 'Үр ашигт хүү (EIR) — шимтгэл багтсан, ил тод байдлын шаардлага';

-- Хамтран зээлдэгч
CREATE TABLE loan.loan_borrowers (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    loan_id        UUID NOT NULL REFERENCES loan.loans (id) ON DELETE CASCADE,
    customer_id    UUID NOT NULL REFERENCES customer.customers (id),
    borrower_role  VARCHAR(20) NOT NULL DEFAULT 'CO_BORROWER'
                   CHECK (borrower_role IN ('PRIMARY', 'CO_BORROWER', 'SPOUSE_CONSENT')),
    liability_share system.percent_share NOT NULL DEFAULT 100,
    is_active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by     BIGINT,
    CONSTRAINT uq_loan_borrower UNIQUE (loan_id, customer_id)
);

-- Гэрээ
CREATE TABLE loan.loan_contracts (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    loan_id           UUID        NOT NULL REFERENCES loan.loans (id) ON DELETE RESTRICT,
    contract_number   VARCHAR(40) NOT NULL,
    contract_type     VARCHAR(20) NOT NULL DEFAULT 'LOAN'
                      CHECK (contract_type IN ('LOAN', 'AMENDMENT', 'RESTRUCTURE', 'SETTLEMENT')),
    parent_contract_id UUID REFERENCES loan.loan_contracts (id),
    signed_date       DATE,
    effective_date    DATE        NOT NULL,
    expiry_date       DATE,
    signed_by_customer BOOLEAN    NOT NULL DEFAULT FALSE,
    signed_by_officer_id BIGINT   REFERENCES core.employees (id),
    signature_method  VARCHAR(20) CHECK (signature_method IN ('WET_INK', 'DIGITAL_ID', 'OTP', 'BIOMETRIC')),
    document_id       UUID,                              -- system.documents(id) — 014-т FK
    status            VARCHAR(20) NOT NULL DEFAULT 'DRAFT'
                      CHECK (status IN ('DRAFT', 'PENDING_SIGNATURE', 'ACTIVE', 'AMENDED', 'TERMINATED', 'EXPIRED')),
    terms_snapshot    JSONB,                             -- гэрээний мөчид хөлдөөсөн нөхцөл
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by        BIGINT,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by        BIGINT,
    version           INTEGER     NOT NULL DEFAULT 1,
    CONSTRAINT uq_loan_contract_no UNIQUE (contract_number),
    CONSTRAINT ck_loan_contract_expiry CHECK (expiry_date IS NULL OR expiry_date >= effective_date),
    CONSTRAINT ck_loan_contract_signed CHECK (status <> 'ACTIVE' OR signed_date IS NOT NULL)
);
COMMENT ON COLUMN loan.loan_contracts.terms_snapshot IS
    'Гэрээ байгуулах үеийн нөхцөлийн хуулбар — сүүлд бүтээгдэхүүн өөрчлөгдсөн ч маргаанд эх нотолгоо';

-- Олголт (хэсэгчилсэн олголт дэмжинэ)
CREATE TABLE loan.loan_disbursements (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    loan_id            UUID    NOT NULL REFERENCES loan.loans (id) ON DELETE RESTRICT,
    currency_code      system.currency_code NOT NULL,
    disbursement_no    SMALLINT NOT NULL DEFAULT 1 CHECK (disbursement_no > 0),
    amount             system.money_positive NOT NULL CHECK (amount > 0),
    disbursement_date  DATE    NOT NULL,
    value_date         DATE    NOT NULL,
    method             VARCHAR(20) NOT NULL DEFAULT 'BANK_TRANSFER'
                       CHECK (method IN ('CASH', 'BANK_TRANSFER', 'INTERNAL_ACCOUNT', 'CARD', 'WALLET', 'THIRD_PARTY')),
    destination_account_id BIGINT REFERENCES customer.customer_bank_accounts (id),
    internal_account_id UUID,                            -- account.accounts(id) — 008-д FK
    transaction_id     BIGINT,                           -- account.transactions — сул холбоос (partition)
    journal_entry_id   BIGINT,                           -- accounting.journal_entries — 011-д FK
    status             VARCHAR(20) NOT NULL DEFAULT 'PENDING'
                       CHECK (status IN ('PENDING', 'APPROVED', 'EXECUTED', 'FAILED', 'REVERSED')),
    executed_at        TIMESTAMPTZ,
    approved_by        BIGINT REFERENCES core.employees (id),
    reversal_reason    VARCHAR(300),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by         BIGINT,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by         BIGINT,
    version            INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT uq_loan_disb_no UNIQUE (loan_id, disbursement_no),
    CONSTRAINT fk_loan_disb_currency FOREIGN KEY (loan_id, currency_code)
        REFERENCES loan.loans (id, currency_code),
    CONSTRAINT ck_loan_disb_executed CHECK (status <> 'EXECUTED' OR executed_at IS NOT NULL)
);
COMMENT ON CONSTRAINT fk_loan_disb_currency ON loan.loan_disbursements IS
    'Олголтын валют зээлийн валютаас зөрөх боломжгүй — DB түвшинд';

-- ---------------------------------------------------------------------
-- Эргэн төлөлтийн хуваарь
-- ---------------------------------------------------------------------
CREATE TABLE loan.loan_schedules (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    loan_id          UUID     NOT NULL REFERENCES loan.loans (id) ON DELETE CASCADE,
    schedule_version SMALLINT NOT NULL DEFAULT 1 CHECK (schedule_version > 0),
    is_current       BOOLEAN  NOT NULL DEFAULT TRUE,
    generated_reason VARCHAR(30) NOT NULL DEFAULT 'ORIGINAL'
                     CHECK (generated_reason IN ('ORIGINAL', 'RESTRUCTURE', 'RATE_CHANGE', 'PARTIAL_PREPAYMENT',
                                                 'DISBURSEMENT', 'CORRECTION')),
    total_principal  system.money_positive NOT NULL,
    total_interest   system.money_positive NOT NULL,
    total_fee        system.money_positive NOT NULL DEFAULT 0,
    installment_count SMALLINT NOT NULL CHECK (installment_count > 0),
    first_due_date   DATE NOT NULL,
    last_due_date    DATE NOT NULL,
    generated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    generated_by     BIGINT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_loan_schedule_version UNIQUE (loan_id, schedule_version),
    CONSTRAINT ck_loan_schedule_dates CHECK (last_due_date >= first_due_date)
);
COMMENT ON TABLE loan.loan_schedules IS
    'Хуваарийн ХУВИЛБАР. Бүтэц өөрчлөх/дахин тооцоход шинэ version үүснэ, хуучин нь үлдэнэ (§15)';

CREATE TABLE loan.loan_schedule_items (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    schedule_id       BIGINT   NOT NULL REFERENCES loan.loan_schedules (id) ON DELETE CASCADE,
    loan_id           UUID     NOT NULL REFERENCES loan.loans (id) ON DELETE CASCADE,
    installment_no    SMALLINT NOT NULL CHECK (installment_no > 0),
    due_date          DATE     NOT NULL,
    period_start_date DATE     NOT NULL,
    period_end_date   DATE     NOT NULL,

    principal_due     system.money_positive NOT NULL DEFAULT 0,
    interest_due      system.money_positive NOT NULL DEFAULT 0,
    fee_due           system.money_positive NOT NULL DEFAULT 0,
    insurance_due     system.money_positive NOT NULL DEFAULT 0,
    total_due         system.money_amount GENERATED ALWAYS AS
                      (principal_due + interest_due + fee_due + insurance_due) STORED,

    principal_paid    system.money_positive NOT NULL DEFAULT 0,
    interest_paid     system.money_positive NOT NULL DEFAULT 0,
    fee_paid          system.money_positive NOT NULL DEFAULT 0,
    insurance_paid    system.money_positive NOT NULL DEFAULT 0,
    penalty_charged   system.money_positive NOT NULL DEFAULT 0,
    penalty_paid      system.money_positive NOT NULL DEFAULT 0,

    opening_balance   system.money_positive NOT NULL DEFAULT 0,
    closing_balance   system.money_positive NOT NULL DEFAULT 0,

    status            VARCHAR(20) NOT NULL DEFAULT 'PENDING'
                      CHECK (status IN ('PENDING', 'PARTIALLY_PAID', 'PAID', 'OVERDUE', 'WAIVED', 'RESTRUCTURED')),
    paid_date         DATE,
    days_past_due     INTEGER NOT NULL DEFAULT 0 CHECK (days_past_due >= 0),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    version           INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT uq_loan_sched_item UNIQUE (schedule_id, installment_no),
    CONSTRAINT ck_loan_sched_item_period CHECK (period_end_date >= period_start_date),
    CONSTRAINT ck_loan_sched_item_due CHECK (due_date >= period_start_date),
    CONSTRAINT ck_loan_sched_item_paid CHECK (
        principal_paid <= principal_due AND interest_paid <= interest_due
        AND fee_paid <= fee_due AND insurance_paid <= insurance_due
    ),
    CONSTRAINT ck_loan_sched_item_status_paid CHECK (status <> 'PAID' OR paid_date IS NOT NULL)
);
COMMENT ON TABLE loan.loan_schedule_items IS 'Хуваарийн мөр бүр — төлөх ба төлөгдсөн дүн бүрэлдэхүүн тус бүрээр';

-- ---------------------------------------------------------------------
-- Төлөлт
-- ---------------------------------------------------------------------
CREATE TABLE loan.loan_payments (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    payment_no       VARCHAR(30) NOT NULL,
    loan_id          UUID     NOT NULL REFERENCES loan.loans (id) ON DELETE RESTRICT,
    currency_code    system.currency_code NOT NULL,
    customer_id      UUID     NOT NULL REFERENCES customer.customers (id),
    branch_id        BIGINT   REFERENCES core.branches (id),
    amount           system.money_positive NOT NULL CHECK (amount > 0),
    payment_date     DATE     NOT NULL,
    value_date       DATE     NOT NULL,
    payment_method   VARCHAR(20) NOT NULL DEFAULT 'CASH'
                     CHECK (payment_method IN ('CASH', 'BANK_TRANSFER', 'CARD', 'WALLET', 'AUTO_DEBIT', 'SALARY_DEDUCTION', 'OFFSET')),
    payment_type     VARCHAR(20) NOT NULL DEFAULT 'REGULAR'
                     CHECK (payment_type IN ('REGULAR', 'EARLY_PARTIAL', 'EARLY_FULL', 'OVERDUE', 'SETTLEMENT', 'RECOVERY')),
    -- Хуваарилагдсан дүнгүүд (loan_payment_allocations-ийн нийлбэр)
    allocated_principal system.money_positive NOT NULL DEFAULT 0,
    allocated_interest  system.money_positive NOT NULL DEFAULT 0,
    allocated_penalty   system.money_positive NOT NULL DEFAULT 0,
    allocated_fee       system.money_positive NOT NULL DEFAULT 0,
    unallocated_amount  system.money_positive NOT NULL DEFAULT 0,   -- урьдчилгаа (suspense)
    transaction_id   BIGINT,                             -- account.transactions — сул холбоос
    journal_entry_id BIGINT,                             -- accounting.journal_entries — 011-д FK
    status           VARCHAR(20) NOT NULL DEFAULT 'POSTED'
                     CHECK (status IN ('PENDING', 'POSTED', 'REVERSED', 'FAILED')),
    reversed_payment_id BIGINT REFERENCES loan.loan_payments (id),
    reversal_reason  VARCHAR(300),
    received_by      BIGINT REFERENCES core.employees (id),
    notes            VARCHAR(300),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by       BIGINT,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by       BIGINT,
    version          INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT uq_loan_payment_no UNIQUE (payment_no),
    CONSTRAINT fk_loan_payment_currency FOREIGN KEY (loan_id, currency_code)
        REFERENCES loan.loans (id, currency_code),
    CONSTRAINT ck_loan_payment_alloc CHECK (
        allocated_principal + allocated_interest + allocated_penalty + allocated_fee + unallocated_amount = amount
    ),
    CONSTRAINT ck_loan_payment_reversal CHECK (status <> 'REVERSED' OR reversal_reason IS NOT NULL)
);
COMMENT ON CONSTRAINT ck_loan_payment_alloc ON loan.loan_payments IS
    'Хуваарилалт нийт дүнтэй тэнцүү байх — нэг ч төгрөг алдагдахгүй (§21 payment allocation)';
COMMENT ON COLUMN loan.loan_payments.unallocated_amount IS 'Илүү төлсөн дүн — suspense данс, дараагийн хуваарьт';

CREATE TABLE loan.loan_payment_allocations (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    payment_id        BIGINT   NOT NULL REFERENCES loan.loan_payments (id) ON DELETE CASCADE,
    schedule_item_id  BIGINT   REFERENCES loan.loan_schedule_items (id),
    component         VARCHAR(20) NOT NULL
                      CHECK (component IN ('PRINCIPAL', 'INTEREST', 'OVERDUE_INTEREST', 'PENALTY', 'FEE', 'INSURANCE')),
    amount            system.money_positive NOT NULL CHECK (amount > 0),
    allocation_order  SMALLINT NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE loan.loan_payment_allocations IS
    'Төлбөр → хуваарийн мөр → бүрэлдэхүүн. Аудит: аль төлбөр аль хугацааны юуг хаасныг харна';

-- ---------------------------------------------------------------------
-- Хүү: түүх ба өдрийн хуримтлал
-- ---------------------------------------------------------------------
CREATE TABLE loan.loan_interest_rate_history (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    loan_id         UUID   NOT NULL REFERENCES loan.loans (id) ON DELETE CASCADE,
    old_rate        system.rate_pct,
    new_rate        system.rate_pct NOT NULL,
    rate_type       system.rate_type NOT NULL,
    change_reason   VARCHAR(30) NOT NULL
                    CHECK (change_reason IN ('BASE_RATE_CHANGE', 'RESTRUCTURE', 'PROMOTION', 'PENALTY_RATE',
                                             'CORRECTION', 'CONTRACT_AMENDMENT')),
    effective_from  DATE   NOT NULL,
    effective_to    DATE,
    approved_by     BIGINT REFERENCES core.employees (id),
    changed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    changed_by      BIGINT,
    CONSTRAINT ck_loan_rate_hist_period CHECK (effective_to IS NULL OR effective_to >= effective_from),
    CONSTRAINT ex_loan_rate_overlap EXCLUDE USING gist (
        loan_id WITH =,
        daterange(effective_from, COALESCE(effective_to, DATE '9999-12-31'), '[]') WITH &&
    )
);
COMMENT ON TABLE loan.loan_interest_rate_history IS
    'Хүүгийн түүх — огтлолцох хугацаа байхгүй (EXCLUDE). Аль өдөр ямар хүүтэй байсныг сэргээж харна';

-- Өдрийн хүүгийн хуримтлал — их эзэлхүүн, зөвхөн нэмнэ → partition
CREATE SEQUENCE loan.seq_interest_accruals;
CREATE TABLE loan.loan_interest_accruals (
    id                BIGINT NOT NULL DEFAULT nextval('loan.seq_interest_accruals'),
    loan_id           UUID   NOT NULL REFERENCES loan.loans (id) ON DELETE RESTRICT,
    accrual_date      DATE   NOT NULL,
    principal_base    system.money_positive NOT NULL,
    annual_rate       system.rate_pct NOT NULL,
    day_count         SMALLINT NOT NULL DEFAULT 1 CHECK (day_count > 0),
    interest_amount   system.money_positive NOT NULL,
    penalty_amount    system.money_positive NOT NULL DEFAULT 0,
    is_posted         BOOLEAN NOT NULL DEFAULT FALSE,
    journal_entry_id  BIGINT,
    is_suspended      BOOLEAN NOT NULL DEFAULT FALSE,    -- NPL: хүү хуримтлуулахаа зогсоох
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id, accrual_date),
    CONSTRAINT uq_loan_accrual UNIQUE (loan_id, accrual_date)
) PARTITION BY RANGE (accrual_date);

CREATE TABLE loan.loan_interest_accruals_2025 PARTITION OF loan.loan_interest_accruals
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE loan.loan_interest_accruals_2026 PARTITION OF loan.loan_interest_accruals
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE loan.loan_interest_accruals_2027 PARTITION OF loan.loan_interest_accruals
    FOR VALUES FROM ('2027-01-01') TO ('2028-01-01');
CREATE TABLE loan.loan_interest_accruals_default PARTITION OF loan.loan_interest_accruals DEFAULT;

COMMENT ON TABLE loan.loan_interest_accruals IS
    'Өдөр тутмын хүүгийн хуримтлал. 10K зээл × 365 өдөр ≈ 3.6 сая мөр/жил → жилээр partition';

-- ---------------------------------------------------------------------
-- Шимтгэл, торгууль, хугацаа хэтрэлт
-- ---------------------------------------------------------------------
CREATE TABLE loan.loan_fees (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    loan_id         UUID     NOT NULL REFERENCES loan.loans (id) ON DELETE CASCADE,
    fee_type_id     SMALLINT NOT NULL REFERENCES product.fee_types (id),
    amount          system.money_positive NOT NULL CHECK (amount > 0),
    currency_code   system.currency_code NOT NULL,
    charged_date    DATE     NOT NULL,
    due_date        DATE,
    paid_amount     system.money_positive NOT NULL DEFAULT 0,
    waived_amount   system.money_positive NOT NULL DEFAULT 0,
    waived_by       BIGINT REFERENCES core.employees (id),
    waive_reason    VARCHAR(300),
    status          VARCHAR(20) NOT NULL DEFAULT 'CHARGED'
                    CHECK (status IN ('CHARGED', 'PARTIALLY_PAID', 'PAID', 'WAIVED', 'REVERSED')),
    journal_entry_id BIGINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by      BIGINT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by      BIGINT,
    version         INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT fk_loan_fee_currency FOREIGN KEY (loan_id, currency_code) REFERENCES loan.loans (id, currency_code),
    CONSTRAINT ck_loan_fee_amounts CHECK (paid_amount + waived_amount <= amount),
    CONSTRAINT ck_loan_fee_waived CHECK (waived_amount = 0 OR (waived_by IS NOT NULL AND waive_reason IS NOT NULL))
);

CREATE TABLE loan.loan_penalties (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    loan_id           UUID     NOT NULL REFERENCES loan.loans (id) ON DELETE CASCADE,
    schedule_item_id  BIGINT   REFERENCES loan.loan_schedule_items (id),
    currency_code     system.currency_code NOT NULL,
    penalty_type      VARCHAR(20) NOT NULL DEFAULT 'LATE_PAYMENT'
                      CHECK (penalty_type IN ('LATE_PAYMENT', 'EARLY_REPAYMENT', 'COVENANT_BREACH', 'OTHER')),
    base_amount       system.money_positive NOT NULL,
    penalty_rate      system.rate_pct NOT NULL,
    days_overdue      INTEGER  NOT NULL CHECK (days_overdue >= 0),
    amount            system.money_positive NOT NULL,
    charged_date      DATE     NOT NULL,
    paid_amount       system.money_positive NOT NULL DEFAULT 0,
    waived_amount     system.money_positive NOT NULL DEFAULT 0,
    waived_by         BIGINT REFERENCES core.employees (id),
    waive_reason      VARCHAR(300),
    status            VARCHAR(20) NOT NULL DEFAULT 'CHARGED'
                      CHECK (status IN ('CHARGED', 'PARTIALLY_PAID', 'PAID', 'WAIVED', 'REVERSED')),
    journal_entry_id  BIGINT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by        BIGINT,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by        BIGINT,
    version           INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT fk_loan_penalty_currency FOREIGN KEY (loan_id, currency_code) REFERENCES loan.loans (id, currency_code),
    CONSTRAINT ck_loan_penalty_amounts CHECK (paid_amount + waived_amount <= amount),
    CONSTRAINT ck_loan_penalty_waived CHECK (waived_amount = 0 OR (waived_by IS NOT NULL AND waive_reason IS NOT NULL))
);
COMMENT ON TABLE loan.loan_penalties IS 'Торгуулийн тооцоолол — суурь дүн, хувь, хоног бүгд хадгалагдана (маргаанд)';

-- DPD-ийн өдөр тутмын агшин (партицлагдсан) — тайлан, чиг хандлагад
CREATE SEQUENCE loan.seq_loan_overdues;
CREATE TABLE loan.loan_overdues (
    id                   BIGINT NOT NULL DEFAULT nextval('loan.seq_loan_overdues'),
    loan_id              UUID   NOT NULL REFERENCES loan.loans (id) ON DELETE RESTRICT,
    snapshot_date        DATE   NOT NULL,
    days_past_due        INTEGER NOT NULL CHECK (days_past_due >= 0),
    dpd_bucket           VARCHAR(20) NOT NULL   -- text2sql-new засвар: 10→20 ('DPD_180_PLUS' = 12 тэмдэгт)
                         CHECK (dpd_bucket IN ('CURRENT', 'DPD_1_30', 'DPD_31_60', 'DPD_61_90', 'DPD_91_180', 'DPD_180_PLUS')),
    overdue_principal    system.money_positive NOT NULL DEFAULT 0,
    overdue_interest     system.money_positive NOT NULL DEFAULT 0,
    overdue_penalty      system.money_positive NOT NULL DEFAULT 0,
    overdue_fee          system.money_positive NOT NULL DEFAULT 0,
    total_outstanding    system.money_positive NOT NULL DEFAULT 0,
    oldest_due_date      DATE,
    classification_id    SMALLINT REFERENCES loan.classification_grades (id),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id, snapshot_date),
    CONSTRAINT uq_loan_overdue_snapshot UNIQUE (loan_id, snapshot_date)
) PARTITION BY RANGE (snapshot_date);

CREATE TABLE loan.loan_overdues_2025 PARTITION OF loan.loan_overdues
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE loan.loan_overdues_2026 PARTITION OF loan.loan_overdues
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE loan.loan_overdues_2027 PARTITION OF loan.loan_overdues
    FOR VALUES FROM ('2027-01-01') TO ('2028-01-01');
CREATE TABLE loan.loan_overdues_default PARTITION OF loan.loan_overdues DEFAULT;

COMMENT ON TABLE loan.loan_overdues IS
    'DPD-ийн өдөр бүрийн агшин — PAR, чанарын шилжилт (migration matrix), DWH-ийн fact-ийн эх';

-- Активын ангиллын түүх
CREATE TABLE loan.loan_classifications (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    loan_id            UUID     NOT NULL REFERENCES loan.loans (id) ON DELETE CASCADE,
    old_classification_id SMALLINT REFERENCES loan.classification_grades (id),
    new_classification_id SMALLINT NOT NULL REFERENCES loan.classification_grades (id),
    days_past_due      INTEGER  NOT NULL CHECK (days_past_due >= 0),
    outstanding_amount system.money_positive NOT NULL,
    provision_rate     system.percent_share NOT NULL,
    provision_amount   system.money_positive NOT NULL,
    classification_date DATE    NOT NULL,
    is_manual_override BOOLEAN  NOT NULL DEFAULT FALSE,
    override_reason    VARCHAR(300),
    approved_by        BIGINT REFERENCES core.employees (id),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by         BIGINT,
    CONSTRAINT ck_loan_class_override CHECK (NOT is_manual_override OR (override_reason IS NOT NULL AND approved_by IS NOT NULL))
);
COMMENT ON TABLE loan.loan_classifications IS 'Ангиллын шилжилтийн түүх + эрсдэлийн сангийн тооцоо';

-- ---------------------------------------------------------------------
-- Бүтцийн өөрчлөлт ба эргэлт буцалтгүй нөхцөл
-- ---------------------------------------------------------------------
CREATE TABLE loan.loan_restructures (
    id                    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    loan_id               UUID     NOT NULL REFERENCES loan.loans (id) ON DELETE RESTRICT,
    restructure_no        SMALLINT NOT NULL DEFAULT 1 CHECK (restructure_no > 0),
    restructure_type      VARCHAR(30) NOT NULL
                          CHECK (restructure_type IN ('TERM_EXTENSION', 'RATE_REDUCTION', 'PAYMENT_HOLIDAY',
                                                      'PRINCIPAL_HAIRCUT', 'CAPITALIZATION', 'REFINANCE', 'CONSOLIDATION')),
    reason                VARCHAR(500) NOT NULL,
    old_principal         system.money_positive NOT NULL,
    new_principal         system.money_positive NOT NULL,
    old_rate              system.rate_pct NOT NULL,
    new_rate              system.rate_pct NOT NULL,
    old_term_months       SMALLINT NOT NULL,
    new_term_months       SMALLINT NOT NULL,
    old_maturity_date     DATE,
    new_maturity_date     DATE,
    capitalized_interest  system.money_positive NOT NULL DEFAULT 0,
    capitalized_penalty   system.money_positive NOT NULL DEFAULT 0,
    waived_amount         system.money_positive NOT NULL DEFAULT 0,
    old_schedule_id       BIGINT REFERENCES loan.loan_schedules (id),
    new_schedule_id       BIGINT REFERENCES loan.loan_schedules (id),
    new_contract_id       UUID   REFERENCES loan.loan_contracts (id),
    effective_date        DATE   NOT NULL,
    approved_by           BIGINT REFERENCES core.employees (id),
    approval_ref          VARCHAR(100),
    status                VARCHAR(20) NOT NULL DEFAULT 'PENDING'
                          CHECK (status IN ('PENDING', 'APPROVED', 'EXECUTED', 'REJECTED', 'CANCELLED')),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by            BIGINT,
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by            BIGINT,
    version               INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT uq_loan_restructure_no UNIQUE (loan_id, restructure_no),
    CONSTRAINT ck_loan_restr_approved CHECK (status NOT IN ('APPROVED', 'EXECUTED') OR approved_by IS NOT NULL)
);
COMMENT ON TABLE loan.loan_restructures IS
    'Бүтцийн өөрчлөлт — өмнөх ба шинэ нөхцөлийг ХОЁУЛАНГ нь хадгална (зохицуулагчийн шаардлага)';

CREATE TABLE loan.loan_writeoffs (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    loan_id            UUID    NOT NULL REFERENCES loan.loans (id) ON DELETE RESTRICT,
    writeoff_date      DATE    NOT NULL,
    principal_amount   system.money_positive NOT NULL,
    interest_amount    system.money_positive NOT NULL DEFAULT 0,
    penalty_amount     system.money_positive NOT NULL DEFAULT 0,
    fee_amount         system.money_positive NOT NULL DEFAULT 0,
    total_amount       system.money_amount GENERATED ALWAYS AS
                       (principal_amount + interest_amount + penalty_amount + fee_amount) STORED,
    provision_used     system.money_positive NOT NULL DEFAULT 0,
    writeoff_type      VARCHAR(20) NOT NULL DEFAULT 'FULL'
                       CHECK (writeoff_type IN ('FULL', 'PARTIAL', 'INTEREST_ONLY')),
    reason             VARCHAR(500) NOT NULL,
    board_resolution_ref VARCHAR(100),
    approved_by        BIGINT REFERENCES core.employees (id),
    journal_entry_id   BIGINT,
    is_recoverable     BOOLEAN NOT NULL DEFAULT TRUE,     -- балансын гадуур хяналт үргэлжилнэ
    recovered_amount   system.money_positive NOT NULL DEFAULT 0,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by         BIGINT,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by         BIGINT,
    version            INTEGER NOT NULL DEFAULT 1
);
COMMENT ON COLUMN loan.loan_writeoffs.is_recoverable IS
    'Данснаас хассан ч балансын гадуур бүртгэлд үлдэж, барагдуулалт үргэлжилнэ';

CREATE TABLE loan.loan_status_history (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    loan_id        UUID     NOT NULL REFERENCES loan.loans (id) ON DELETE CASCADE,
    old_status_id  SMALLINT REFERENCES loan.loan_statuses (id),
    new_status_id  SMALLINT NOT NULL REFERENCES loan.loan_statuses (id),
    change_reason  VARCHAR(300),
    outstanding_at_change system.money_amount,
    dpd_at_change  INTEGER,
    changed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    changed_by     BIGINT
);
COMMENT ON TABLE loan.loan_status_history IS
    'Төлөвийн шилжилт бүр + тухайн үеийн үлдэгдэл/DPD — аудитад өмнөх байдлыг сэргээнэ (§22.20)';
