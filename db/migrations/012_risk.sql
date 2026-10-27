-- =====================================================================
-- 012_risk.sql — Зээлийн оноо, эрсдэлийн үнэлгээ, залилангийн хяналт
-- ---------------------------------------------------------------------
-- Скорингийн МОДЕЛЬ нь хувилбартай (versioned): оноо бүр аль хувилбараар
-- гарсныг мэдэх ёстой — эс бөгөөс хожим модель солигдоход өнгөрсөн
-- шийдвэрийг тайлбарлах боломжгүй болно (model governance).
-- =====================================================================

\set ON_ERROR_STOP on

CREATE TABLE risk.credit_score_models (
    id               SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    model_code       system.code_key NOT NULL,
    model_name       VARCHAR(200)    NOT NULL,
    model_version    VARCHAR(20)     NOT NULL,
    model_type       VARCHAR(30)     NOT NULL
                     CHECK (model_type IN ('SCORECARD', 'LOGISTIC_REGRESSION', 'DECISION_TREE',
                                           'GRADIENT_BOOSTING', 'EXPERT_RULES', 'BUREAU_SCORE')),
    target_segment   VARCHAR(50),
    min_score        SMALLINT NOT NULL DEFAULT 0,
    max_score        SMALLINT NOT NULL DEFAULT 1000,
    cutoff_score     SMALLINT,                            -- энэ доош автомат татгалзал
    approval_rate_target system.percent_share,
    gini_coefficient NUMERIC(5, 4),                       -- моделийн ялгах чадвар
    trained_at       DATE,
    deployed_at      DATE,
    retired_at       DATE,
    status           VARCHAR(20) NOT NULL DEFAULT 'DRAFT'
                     CHECK (status IN ('DRAFT', 'VALIDATION', 'ACTIVE', 'CHAMPION', 'CHALLENGER', 'RETIRED')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by       BIGINT,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by       BIGINT,
    version          INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT uq_score_model UNIQUE (model_code, model_version),
    CONSTRAINT ck_score_model_range CHECK (max_score > min_score),
    CONSTRAINT ck_score_model_cutoff CHECK (cutoff_score IS NULL OR cutoff_score BETWEEN min_score AND max_score)
);
COMMENT ON COLUMN risk.credit_score_models.status IS
    'CHAMPION/CHALLENGER — үйлдвэрлэлийн A/B туршилт хийх боломж';

CREATE TABLE risk.credit_score_model_factors (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    model_id       SMALLINT NOT NULL REFERENCES risk.credit_score_models (id) ON DELETE CASCADE,
    factor_code    system.code_key NOT NULL,
    factor_name    VARCHAR(200)    NOT NULL,
    factor_group   VARCHAR(30) NOT NULL
                   CHECK (factor_group IN ('DEMOGRAPHIC', 'FINANCIAL', 'BEHAVIORAL', 'BUREAU', 'COLLATERAL', 'RELATIONSHIP')),
    weight         NUMERIC(8, 4) NOT NULL,
    max_points     NUMERIC(8, 2),
    bands          JSONB,                                 -- [{min, max, points}] — оноожуулалтын шат
    is_active      BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_score_model_factor UNIQUE (model_id, factor_code)
);

CREATE TABLE risk.risk_grades (
    id               SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- text2sql-new засвар: system.code_key нь 2+ тэмдэгт шаарддаг тул 'A', 'B', 'C' зэрэглэл
    -- seed-д унадаг байв. Нэг үсэгтэй кодыг зөвшөөрөх тусдаа CHECK.
    grade_code       VARCHAR(20)     NOT NULL CHECK (grade_code ~ '^[A-Z][A-Z0-9_]{0,19}$'),
    grade_name       VARCHAR(100)    NOT NULL,
    min_score        SMALLINT NOT NULL,
    max_score        SMALLINT NOT NULL,
    pd_estimate      NUMERIC(7, 5),                       -- дефолтын магадлал (0..1)
    lgd_estimate     NUMERIC(7, 5),                       -- алдагдлын хувь
    rate_premium     system.rate_pct NOT NULL DEFAULT 0,  -- эрсдэлийн нэмэгдэл хүү
    max_exposure     system.money_positive,
    requires_collateral BOOLEAN NOT NULL DEFAULT FALSE,
    auto_reject      BOOLEAN NOT NULL DEFAULT FALSE,
    sort_order       SMALLINT NOT NULL DEFAULT 0,
    is_active        BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_risk_grades_code UNIQUE (grade_code),
    CONSTRAINT ck_risk_grade_range CHECK (max_score >= min_score),
    CONSTRAINT ck_risk_grade_pd CHECK (pd_estimate IS NULL OR pd_estimate BETWEEN 0 AND 1),
    CONSTRAINT ck_risk_grade_lgd CHECK (lgd_estimate IS NULL OR lgd_estimate BETWEEN 0 AND 1)
);
COMMENT ON TABLE risk.risk_grades IS 'Эрсдэлийн зэрэглэл: оноо → зэрэглэл → хүүгийн нэмэгдэл, PD/LGD (ECL тооцоонд)';

CREATE TABLE risk.credit_scores (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id       UUID     NOT NULL REFERENCES customer.customers (id) ON DELETE CASCADE,
    application_id    UUID     REFERENCES loan.loan_applications (id),
    loan_id           UUID     REFERENCES loan.loans (id),
    model_id          SMALLINT NOT NULL REFERENCES risk.credit_score_models (id),
    risk_grade_id     SMALLINT REFERENCES risk.risk_grades (id),
    score             SMALLINT NOT NULL,
    pd_estimate       NUMERIC(7, 5),
    factor_values     JSONB NOT NULL DEFAULT '{}'::JSONB,   -- {factor_code: {value, points}}
    decision_hint     VARCHAR(20)
                      CHECK (decision_hint IN ('AUTO_APPROVE', 'MANUAL_REVIEW', 'AUTO_REJECT')),
    score_type        VARCHAR(20) NOT NULL DEFAULT 'APPLICATION'
                      CHECK (score_type IN ('APPLICATION', 'BEHAVIORAL', 'COLLECTION', 'BUREAU', 'PERIODIC')),
    scored_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_until       DATE,
    is_current        BOOLEAN NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by        BIGINT
);
COMMENT ON COLUMN risk.credit_scores.factor_values IS
    'Оноо гарахад ашигласан бүх хүчин зүйлийн утга — шийдвэрийг ХОЖИМ тайлбарлах (explainability)';

CREATE TABLE risk.credit_bureau_reports (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id       UUID     NOT NULL REFERENCES customer.customers (id) ON DELETE CASCADE,
    application_id    UUID     REFERENCES loan.loan_applications (id),
    bureau_name       VARCHAR(100) NOT NULL,              -- Зээлийн мэдээллийн сан
    request_ref       VARCHAR(100),
    bureau_score      SMALLINT,
    total_exposure    system.money_positive,
    active_loan_count SMALLINT CHECK (active_loan_count >= 0),
    overdue_loan_count SMALLINT CHECK (overdue_loan_count >= 0),
    max_dpd_12m       INTEGER CHECK (max_dpd_12m >= 0),
    worst_classification VARCHAR(30),
    has_writeoff      BOOLEAN NOT NULL DEFAULT FALSE,
    inquiry_count_3m  SMALLINT CHECK (inquiry_count_3m >= 0),
    raw_response      JSONB,
    consent_reference VARCHAR(100),                       -- харилцагчийн зөвшөөрөл (хууль)
    requested_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    requested_by      BIGINT,
    valid_until       DATE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_bureau_consent CHECK (consent_reference IS NOT NULL)
);
COMMENT ON CONSTRAINT ck_bureau_consent ON risk.credit_bureau_reports IS
    'Зээлийн мэдээллийн санд хандахад харилцагчийн зөвшөөрөл ЗААВАЛ баримтжина';

CREATE TABLE risk.risk_assessments (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_type      system.entity_type NOT NULL,
    entity_id        UUID     NOT NULL,
    customer_id      UUID     REFERENCES customer.customers (id),
    assessment_type  VARCHAR(30) NOT NULL
                     CHECK (assessment_type IN ('CREDIT', 'OPERATIONAL', 'MARKET', 'LIQUIDITY', 'COMPLIANCE', 'CONCENTRATION')),
    risk_grade_id    SMALLINT REFERENCES risk.risk_grades (id),
    risk_score       NUMERIC(8, 2),
    exposure_amount  system.money_positive,
    expected_loss    system.money_positive,
    mitigations      TEXT,
    findings         TEXT,
    assessed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    assessed_by      BIGINT REFERENCES core.employees (id),
    valid_until      DATE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by       BIGINT
);

-- ---------------------------------------------------------------------
-- Залилангийн хяналт
-- ---------------------------------------------------------------------
CREATE TABLE risk.fraud_rules (
    id             SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    rule_code      system.code_key NOT NULL,
    rule_name      VARCHAR(200)    NOT NULL,
    rule_category  VARCHAR(30)     NOT NULL
                   CHECK (rule_category IN ('IDENTITY', 'APPLICATION', 'TRANSACTION', 'ACCOUNT_TAKEOVER',
                                            'VELOCITY', 'DEVICE', 'BEHAVIORAL', 'SYNTHETIC_ID')),
    conditions     JSONB       NOT NULL DEFAULT '{}'::JSONB,
    action         VARCHAR(20) NOT NULL DEFAULT 'ALERT'
                   CHECK (action IN ('ALERT', 'BLOCK', 'REVIEW', 'STEP_UP_AUTH', 'LOG_ONLY')),
    severity       VARCHAR(10) NOT NULL DEFAULT 'MEDIUM'
                   CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    risk_points    SMALLINT NOT NULL DEFAULT 0,
    is_active      BOOLEAN  NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by     BIGINT,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by     BIGINT,
    version        INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT uq_fraud_rules_code UNIQUE (rule_code)
);

CREATE TABLE risk.fraud_alerts (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    alert_no       VARCHAR(30) NOT NULL,
    rule_id        SMALLINT NOT NULL REFERENCES risk.fraud_rules (id),
    customer_id    UUID     REFERENCES customer.customers (id),
    entity_type    system.entity_type,
    entity_id      UUID,
    transaction_id BIGINT,                                -- account.transactions (сул холбоос)
    risk_score     SMALLINT,
    severity       VARCHAR(10) NOT NULL DEFAULT 'MEDIUM'
                   CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    triggered_data JSONB,
    action_taken   VARCHAR(20) NOT NULL DEFAULT 'ALERT'
                   CHECK (action_taken IN ('ALERT', 'BLOCK', 'REVIEW', 'STEP_UP_AUTH', 'LOG_ONLY')),
    status         VARCHAR(20) NOT NULL DEFAULT 'OPEN'
                   CHECK (status IN ('OPEN', 'INVESTIGATING', 'CONFIRMED_FRAUD', 'FALSE_POSITIVE', 'CLOSED')),
    detected_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    assigned_to    BIGINT REFERENCES core.employees (id),
    resolved_at    TIMESTAMPTZ,
    resolved_by    BIGINT REFERENCES core.employees (id),
    resolution_notes TEXT,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    version        INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT uq_fraud_alerts_no UNIQUE (alert_no),
    CONSTRAINT ck_fraud_alert_resolved CHECK (status NOT IN ('CONFIRMED_FRAUD', 'FALSE_POSITIVE', 'CLOSED')
                                              OR resolved_at IS NOT NULL)
);

CREATE TABLE risk.fraud_cases (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_no           VARCHAR(30) NOT NULL,
    customer_id       UUID REFERENCES customer.customers (id),
    case_type         VARCHAR(30) NOT NULL
                      CHECK (case_type IN ('IDENTITY_THEFT', 'DOCUMENT_FORGERY', 'INTERNAL_FRAUD',
                                           'LOAN_FRAUD', 'PAYMENT_FRAUD', 'MONEY_LAUNDERING', 'OTHER')),
    estimated_loss    system.money_positive NOT NULL DEFAULT 0,
    actual_loss       system.money_positive NOT NULL DEFAULT 0,
    recovered_amount  system.money_positive NOT NULL DEFAULT 0,
    currency_code     system.currency_code NOT NULL DEFAULT 'MNT',
    status            VARCHAR(20) NOT NULL DEFAULT 'OPEN'
                      CHECK (status IN ('OPEN', 'INVESTIGATING', 'REPORTED_TO_POLICE', 'IN_COURT', 'CLOSED_CONFIRMED',
                                        'CLOSED_UNCONFIRMED', 'WRITTEN_OFF')),
    opened_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at         TIMESTAMPTZ,
    investigator_id   BIGINT REFERENCES core.employees (id),
    police_reference  VARCHAR(100),
    court_reference   VARCHAR(100),
    description       TEXT NOT NULL,
    findings          TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by        BIGINT,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by        BIGINT,
    version           INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT uq_fraud_cases_no UNIQUE (case_no),
    CONSTRAINT ck_fraud_case_recovery CHECK (recovered_amount <= actual_loss OR actual_loss = 0),
    CONSTRAINT ck_fraud_case_closed CHECK (status NOT LIKE 'CLOSED%' OR closed_at IS NOT NULL)
);
