-- =====================================================================
-- 004_kyc.sql — KYC, AML, санкц, PEP, гүйлгээний хяналт
-- ---------------------------------------------------------------------
-- Зарчим: KYC бол ҮЙЛ ЯВЦ — статус биш. Тиймээс profile (одоогийн байдал),
-- verification (шалгалт бүрийн бичлэг), screening (AML хайлт), alert
-- (сэжигтэй гүйлгээ) гэж салгав. customers.kyc_status нь зөвхөн хураангуй.
-- =====================================================================

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------------
-- KYC профайл — харилцагч бүрд нэг идэвхтэй
-- ---------------------------------------------------------------------
CREATE TABLE kyc.kyc_profiles (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id        UUID        NOT NULL REFERENCES customer.customers (id) ON DELETE RESTRICT,
    kyc_level          VARCHAR(20) NOT NULL DEFAULT 'STANDARD'
                       CHECK (kyc_level IN ('SIMPLIFIED', 'STANDARD', 'ENHANCED')),
    status             VARCHAR(20) NOT NULL DEFAULT 'IN_PROGRESS'
                       CHECK (status IN ('IN_PROGRESS', 'VERIFIED', 'REJECTED', 'EXPIRED', 'SUSPENDED')),
    identity_verified  BOOLEAN     NOT NULL DEFAULT FALSE,
    address_verified   BOOLEAN     NOT NULL DEFAULT FALSE,
    income_verified    BOOLEAN     NOT NULL DEFAULT FALSE,
    source_of_funds    VARCHAR(200),
    source_of_wealth   VARCHAR(200),
    purpose_of_relation VARCHAR(200),
    expected_monthly_turnover system.money_positive,
    verified_at        TIMESTAMPTZ,
    verified_by        BIGINT REFERENCES core.employees (id),
    expires_at         TIMESTAMPTZ,
    next_review_date   DATE,
    rejection_reason   VARCHAR(300),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by         BIGINT,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by         BIGINT,
    version            INTEGER     NOT NULL DEFAULT 1,
    CONSTRAINT ck_kyc_profile_verified CHECK (status <> 'VERIFIED' OR (verified_at IS NOT NULL AND verified_by IS NOT NULL)),
    CONSTRAINT ck_kyc_profile_rejected CHECK (status <> 'REJECTED' OR rejection_reason IS NOT NULL),
    CONSTRAINT ck_kyc_enhanced_sow     CHECK (kyc_level <> 'ENHANCED' OR source_of_wealth IS NOT NULL)
);
COMMENT ON CONSTRAINT ck_kyc_enhanced_sow ON kyc.kyc_profiles IS
    'Өндөр эрсдэлт (ENHANCED) харилцагчид хөрөнгийн эх үүсвэр заавал';

CREATE TABLE kyc.kyc_documents (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kyc_profile_id   BIGINT      NOT NULL REFERENCES kyc.kyc_profiles (id) ON DELETE CASCADE,
    customer_id      UUID        NOT NULL REFERENCES customer.customers (id),
    document_id      UUID,                               -- system.documents(id) — 014-т FK нэмнэ
    doc_type         VARCHAR(40) NOT NULL
                     CHECK (doc_type IN ('NATIONAL_ID', 'PASSPORT', 'ADDRESS_PROOF', 'INCOME_PROOF',
                                         'COMPANY_CERTIFICATE', 'TAX_CERTIFICATE', 'BOARD_RESOLUTION',
                                         'UBO_DECLARATION', 'BANK_STATEMENT', 'PHOTO', 'SIGNATURE', 'OTHER')),
    doc_number_masked VARCHAR(50),
    issued_by        VARCHAR(200),
    issued_date      DATE,
    expiry_date      DATE,
    is_verified      BOOLEAN     NOT NULL DEFAULT FALSE,
    verified_at      TIMESTAMPTZ,
    verified_by      BIGINT REFERENCES core.employees (id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by       BIGINT,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by       BIGINT,
    version          INTEGER     NOT NULL DEFAULT 1,
    CONSTRAINT ck_kyc_doc_dates CHECK (expiry_date IS NULL OR issued_date IS NULL OR expiry_date > issued_date)
);

CREATE TABLE kyc.kyc_verifications (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    kyc_profile_id   BIGINT      NOT NULL REFERENCES kyc.kyc_profiles (id) ON DELETE CASCADE,
    verification_type VARCHAR(40) NOT NULL
                     CHECK (verification_type IN ('DOCUMENT', 'BIOMETRIC', 'ADDRESS', 'INCOME',
                                                  'PHONE', 'EMAIL', 'DATABASE_LOOKUP', 'VIDEO_CALL', 'IN_PERSON')),
    provider         VARCHAR(100),                       -- ХУР, ДАН, гуравдагч үйлчилгээ
    request_ref      VARCHAR(100),
    result           system.verification_result NOT NULL,
    confidence_score NUMERIC(5, 2) CHECK (confidence_score BETWEEN 0 AND 100),
    response_payload JSONB,                              -- үйлчилгээний бүтэн хариу (аудитад)
    performed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    performed_by     BIGINT REFERENCES core.employees (id),
    notes            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE kyc.kyc_verifications IS 'Шалгалт бүрийн бичлэг — дарж бичихгүй, зөвхөн нэмнэ (append-only)';

-- ---------------------------------------------------------------------
-- Эрсдэлийн үнэлгээ (AML risk rating)
-- ---------------------------------------------------------------------
CREATE TABLE kyc.risk_factors (
    id             SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    factor_code    system.code_key NOT NULL,
    factor_name    VARCHAR(200)    NOT NULL,
    category       VARCHAR(30)     NOT NULL
                   CHECK (category IN ('GEOGRAPHY', 'CUSTOMER', 'PRODUCT', 'CHANNEL', 'TRANSACTION')),
    weight         NUMERIC(5, 2)   NOT NULL DEFAULT 1 CHECK (weight >= 0),
    max_score      NUMERIC(5, 2)   NOT NULL DEFAULT 100,
    is_active      BOOLEAN         NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_risk_factors_code UNIQUE (factor_code)
);

CREATE TABLE kyc.customer_risk_profiles (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id       UUID        NOT NULL REFERENCES customer.customers (id) ON DELETE CASCADE,
    risk_level        VARCHAR(10) NOT NULL CHECK (risk_level IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    risk_score        NUMERIC(6, 2) NOT NULL CHECK (risk_score >= 0),
    factor_scores     JSONB       NOT NULL DEFAULT '{}'::JSONB,   -- {factor_code: score}
    assessment_type   VARCHAR(20) NOT NULL DEFAULT 'PERIODIC'
                      CHECK (assessment_type IN ('ONBOARDING', 'PERIODIC', 'EVENT_DRIVEN', 'MANUAL')),
    assessed_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    assessed_by       BIGINT REFERENCES core.employees (id),
    valid_from        DATE        NOT NULL DEFAULT CURRENT_DATE,
    valid_to          DATE,
    next_review_date  DATE,
    is_current        BOOLEAN     NOT NULL DEFAULT TRUE,
    notes             TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by        BIGINT,
    CONSTRAINT ck_cust_risk_period CHECK (valid_to IS NULL OR valid_to >= valid_from)
);
COMMENT ON TABLE kyc.customer_risk_profiles IS
    'Эрсдэлийн үнэлгээний ТҮҮХ. Одоогийнх нь is_current = TRUE (нэг л мөр — partial unique index 015-д)';

-- ---------------------------------------------------------------------
-- Санкц ба PEP
-- ---------------------------------------------------------------------
CREATE TABLE kyc.sanction_lists (
    id            SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    list_code     system.code_key NOT NULL,              -- UN, OFAC, EU, MN_FIU
    list_name     VARCHAR(200)    NOT NULL,
    source_url    VARCHAR(300),
    jurisdiction  system.country_code,
    last_synced_at TIMESTAMPTZ,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_sanction_lists_code UNIQUE (list_code)
);

CREATE TABLE kyc.sanction_entries (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    list_id        SMALLINT    NOT NULL REFERENCES kyc.sanction_lists (id) ON DELETE CASCADE,
    external_ref   VARCHAR(100) NOT NULL,                -- жагсаалт дахь дугаар
    entity_name    VARCHAR(400) NOT NULL,
    aliases        TEXT[],
    party_type     system.party_type,
    birth_date     DATE,
    country_code   system.country_code,
    program        VARCHAR(200),
    listed_date    DATE,
    delisted_date  DATE,
    raw_record     JSONB,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    version        INTEGER     NOT NULL DEFAULT 1,
    CONSTRAINT uq_sanction_entries UNIQUE (list_id, external_ref)
);

CREATE TABLE kyc.pep_records (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id     UUID REFERENCES customer.customers (id) ON DELETE CASCADE,
    person_name     VARCHAR(300) NOT NULL,
    pep_category    VARCHAR(30)  NOT NULL
                    CHECK (pep_category IN ('DOMESTIC', 'FOREIGN', 'INTERNATIONAL_ORG', 'FAMILY_MEMBER', 'CLOSE_ASSOCIATE')),
    position_held   VARCHAR(300),
    organization    VARCHAR(300),
    country_code    system.country_code,
    start_date      DATE,
    end_date        DATE,
    source          VARCHAR(200),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by      BIGINT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by      BIGINT,
    version         INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT ck_pep_period CHECK (end_date IS NULL OR start_date IS NULL OR end_date >= start_date)
);

-- ---------------------------------------------------------------------
-- AML шалгалт
-- ---------------------------------------------------------------------
CREATE TABLE kyc.aml_screenings (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id     UUID        NOT NULL REFERENCES customer.customers (id) ON DELETE CASCADE,
    screening_type  VARCHAR(20) NOT NULL DEFAULT 'ONBOARDING'
                    CHECK (screening_type IN ('ONBOARDING', 'PERIODIC', 'TRANSACTION', 'LIST_UPDATE', 'MANUAL')),
    screened_name   VARCHAR(400) NOT NULL,
    match_count     INTEGER     NOT NULL DEFAULT 0 CHECK (match_count >= 0),
    result          VARCHAR(20) NOT NULL
                    CHECK (result IN ('CLEAR', 'POTENTIAL_MATCH', 'CONFIRMED_MATCH', 'FALSE_POSITIVE')),
    screened_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    screened_by     BIGINT REFERENCES core.employees (id),
    reviewed_at     TIMESTAMPTZ,
    reviewed_by     BIGINT REFERENCES core.employees (id),
    review_notes    TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_aml_screen_match CHECK (result = 'CLEAR' OR match_count > 0),
    CONSTRAINT ck_aml_screen_review CHECK (result NOT IN ('CONFIRMED_MATCH', 'FALSE_POSITIVE') OR reviewed_by IS NOT NULL)
);

CREATE TABLE kyc.aml_screening_matches (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    screening_id       BIGINT NOT NULL REFERENCES kyc.aml_screenings (id) ON DELETE CASCADE,
    sanction_entry_id  BIGINT REFERENCES kyc.sanction_entries (id),
    pep_record_id      BIGINT REFERENCES kyc.pep_records (id),
    matched_name       VARCHAR(400) NOT NULL,
    match_score        NUMERIC(5, 2) NOT NULL CHECK (match_score BETWEEN 0 AND 100),
    decision           VARCHAR(20) NOT NULL DEFAULT 'PENDING'
                       CHECK (decision IN ('PENDING', 'TRUE_MATCH', 'FALSE_POSITIVE')),
    decided_at         TIMESTAMPTZ,
    decided_by         BIGINT REFERENCES core.employees (id),
    decision_reason    TEXT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_aml_match_target CHECK (sanction_entry_id IS NOT NULL OR pep_record_id IS NOT NULL),
    CONSTRAINT ck_aml_match_decided CHECK (decision = 'PENDING' OR (decided_at IS NOT NULL AND decided_by IS NOT NULL))
);

-- Гүйлгээний хяналтын дүрэм ба сэрэмжлүүлэг
CREATE TABLE kyc.aml_rules (
    id             SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    rule_code      system.code_key NOT NULL,
    rule_name      VARCHAR(200)    NOT NULL,
    rule_type      VARCHAR(30)     NOT NULL
                   CHECK (rule_type IN ('THRESHOLD', 'VELOCITY', 'STRUCTURING', 'GEOGRAPHY', 'PATTERN', 'WATCHLIST')),
    parameters     JSONB           NOT NULL DEFAULT '{}'::JSONB,  -- {amount: 20000000, window_days: 1}
    severity       VARCHAR(10)     NOT NULL DEFAULT 'MEDIUM'
                   CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    is_active      BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by     BIGINT,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by     BIGINT,
    version        INTEGER     NOT NULL DEFAULT 1,
    CONSTRAINT uq_aml_rules_code UNIQUE (rule_code)
);
COMMENT ON COLUMN kyc.aml_rules.parameters IS
    'Босго нь кодонд биш өгөгдөлд — хууль өөрчлөгдөхөд deploy шаардахгүй';

CREATE TABLE kyc.aml_alerts (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    alert_no         VARCHAR(30) NOT NULL,
    rule_id          SMALLINT    NOT NULL REFERENCES kyc.aml_rules (id),
    customer_id      UUID        NOT NULL REFERENCES customer.customers (id),
    entity_type      system.entity_type,
    entity_id        UUID,                               -- сэжигтэй гүйлгээ/данс
    alert_amount     system.money_amount,
    currency_code    system.currency_code,
    severity         VARCHAR(10) NOT NULL DEFAULT 'MEDIUM'
                     CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    status           VARCHAR(30) NOT NULL DEFAULT 'OPEN'   -- text2sql-new засвар: 20→30 ('CLOSED_FALSE_POSITIVE' = 21)
                     CHECK (status IN ('OPEN', 'UNDER_REVIEW', 'ESCALATED', 'CLOSED_FALSE_POSITIVE', 'CLOSED_REPORTED')),
    detected_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    assigned_to      BIGINT REFERENCES core.employees (id),
    closed_at        TIMESTAMPTZ,
    closed_by        BIGINT REFERENCES core.employees (id),
    resolution_notes TEXT,
    str_reported     BOOLEAN     NOT NULL DEFAULT FALSE,  -- СЭГТ-д мэдэгдсэн эсэх
    str_reference    VARCHAR(100),
    details          JSONB       NOT NULL DEFAULT '{}'::JSONB,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by       BIGINT,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by       BIGINT,
    version          INTEGER     NOT NULL DEFAULT 1,
    CONSTRAINT uq_aml_alerts_no UNIQUE (alert_no),
    CONSTRAINT ck_aml_alert_closed CHECK (status NOT LIKE 'CLOSED%' OR (closed_at IS NOT NULL AND closed_by IS NOT NULL)),
    CONSTRAINT ck_aml_alert_str CHECK (NOT str_reported OR str_reference IS NOT NULL)
);
COMMENT ON TABLE kyc.aml_alerts IS 'Сэжигтэй гүйлгээний сэрэмжлүүлэг — СЭГТ-д мэдэгдсэн бүртгэл (STR) хамт';
