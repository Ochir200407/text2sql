-- =====================================================================
-- 013_collection.sql — Эргэн төлүүлэлт, барагдуулалт
-- ---------------------------------------------------------------------
-- Хугацаа хэтэрсэн зээл дээр НЭЭГДЭХ кейс. Үе шат (stage) нь DPD-ээс
-- хамаарч ахина: сануулга → залгах → уулзалт → албан шаардлага → хууль.
-- Амлалт (PTP — promise to pay) ба түүний биелэлт нь цуглуулгын багийн
-- гол KPI.
-- =====================================================================

\set ON_ERROR_STOP on

CREATE TABLE collection.collection_stages (
    id                SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    stage_code        system.code_key NOT NULL,
    stage_name        VARCHAR(100)    NOT NULL,
    min_dpd           SMALLINT NOT NULL CHECK (min_dpd >= 0),
    max_dpd           SMALLINT,
    responsible_unit  VARCHAR(50),                        -- SOFT_COLLECTION, FIELD, LEGAL
    allowed_actions   TEXT[],
    escalation_days   SMALLINT CHECK (escalation_days > 0),
    sort_order        SMALLINT NOT NULL DEFAULT 0,
    is_active         BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_collection_stages_code UNIQUE (stage_code),
    CONSTRAINT ck_collection_stage_dpd CHECK (max_dpd IS NULL OR max_dpd >= min_dpd)
);

CREATE TABLE collection.collection_agencies (
    id              SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    agency_code     system.code_key NOT NULL,
    agency_name     VARCHAR(200)    NOT NULL,
    contract_ref    VARCHAR(100),
    commission_rate system.percent_share NOT NULL DEFAULT 0,
    contact_person  VARCHAR(200),
    phone           system.phone_number,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by      BIGINT,
    CONSTRAINT uq_collection_agencies_code UNIQUE (agency_code)
);

CREATE TABLE collection.collection_cases (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    case_no             VARCHAR(30) NOT NULL,
    loan_id             UUID     NOT NULL REFERENCES loan.loans (id) ON DELETE RESTRICT,
    customer_id         UUID     NOT NULL REFERENCES customer.customers (id),
    branch_id           BIGINT   NOT NULL REFERENCES core.branches (id),
    stage_id            SMALLINT NOT NULL REFERENCES collection.collection_stages (id),
    currency_code       system.currency_code NOT NULL,

    opened_date         DATE     NOT NULL,
    dpd_at_open         INTEGER  NOT NULL CHECK (dpd_at_open >= 0),
    current_dpd         INTEGER  NOT NULL DEFAULT 0 CHECK (current_dpd >= 0),
    outstanding_at_open system.money_positive NOT NULL,
    current_outstanding system.money_positive NOT NULL DEFAULT 0,
    overdue_amount      system.money_positive NOT NULL DEFAULT 0,
    collected_amount    system.money_positive NOT NULL DEFAULT 0,

    assigned_to         BIGINT   REFERENCES core.employees (id),
    agency_id           SMALLINT REFERENCES collection.collection_agencies (id),
    priority            VARCHAR(10) NOT NULL DEFAULT 'MEDIUM'
                        CHECK (priority IN ('LOW', 'MEDIUM', 'HIGH', 'URGENT')),
    status              VARCHAR(20) NOT NULL DEFAULT 'OPEN'
                        CHECK (status IN ('OPEN', 'IN_PROGRESS', 'PROMISE_TO_PAY', 'LEGAL', 'RESTRUCTURED',
                                          'RESOLVED', 'WRITTEN_OFF', 'CLOSED')),
    closed_date         DATE,
    close_reason        VARCHAR(300),
    next_action_date    DATE,
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by          BIGINT,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by          BIGINT,
    version             INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT uq_collection_case_no UNIQUE (case_no),
    CONSTRAINT fk_coll_case_currency FOREIGN KEY (loan_id, currency_code) REFERENCES loan.loans (id, currency_code),
    CONSTRAINT ck_coll_case_closed CHECK (closed_date IS NULL OR closed_date >= opened_date),
    CONSTRAINT ck_coll_case_close_reason CHECK (status NOT IN ('RESOLVED', 'CLOSED') OR close_reason IS NOT NULL)
);
COMMENT ON TABLE collection.collection_cases IS
    'Нэг зээл дээр нэг идэвхтэй кейс (partial unique index 015-д). Хаагдсаны дараа дахин нээгдэж болно';

CREATE TABLE collection.action_types (
    id             SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    action_code    system.code_key NOT NULL,
    action_name    VARCHAR(150)    NOT NULL,
    channel        VARCHAR(20) NOT NULL
                   CHECK (channel IN ('SMS', 'CALL', 'EMAIL', 'LETTER', 'FIELD_VISIT', 'LEGAL_NOTICE', 'COURT', 'SYSTEM')),
    cost_estimate  system.money_positive NOT NULL DEFAULT 0,
    requires_approval BOOLEAN NOT NULL DEFAULT FALSE,
    min_dpd        SMALLINT NOT NULL DEFAULT 0,
    is_active      BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_action_types_code UNIQUE (action_code)
);

CREATE TABLE collection.collection_actions (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_id          UUID     NOT NULL REFERENCES collection.collection_cases (id) ON DELETE CASCADE,
    action_type_id   SMALLINT NOT NULL REFERENCES collection.action_types (id),
    action_date      DATE     NOT NULL,
    performed_by     BIGINT   REFERENCES core.employees (id),
    agency_id        SMALLINT REFERENCES collection.collection_agencies (id),
    contact_person   VARCHAR(200),
    contact_result   VARCHAR(30)
                     CHECK (contact_result IN ('CONTACTED', 'NO_ANSWER', 'WRONG_NUMBER', 'REFUSED',
                                               'PROMISE_OBTAINED', 'DISPUTE', 'UNREACHABLE', 'DECEASED')),
    outcome          VARCHAR(500),
    cost_amount      system.money_positive NOT NULL DEFAULT 0,
    next_action_date DATE,
    attachment_document_id UUID,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by       BIGINT
);
COMMENT ON TABLE collection.collection_actions IS
    'Барагдуулалтын үйлдэл бүрийн бүртгэл — хууль зүйн маргаанд нотлох баримт';

CREATE TABLE collection.collection_promises (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_id           UUID     NOT NULL REFERENCES collection.collection_cases (id) ON DELETE CASCADE,
    action_id         BIGINT   REFERENCES collection.collection_actions (id),
    promised_amount   system.money_positive NOT NULL CHECK (promised_amount > 0),
    currency_code     system.currency_code NOT NULL DEFAULT 'MNT',
    promised_date     DATE     NOT NULL,
    promise_made_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    obtained_by       BIGINT REFERENCES core.employees (id),
    paid_amount       system.money_positive NOT NULL DEFAULT 0,
    paid_date         DATE,
    status            VARCHAR(20) NOT NULL DEFAULT 'PENDING'
                      CHECK (status IN ('PENDING', 'KEPT', 'PARTIALLY_KEPT', 'BROKEN', 'CANCELLED')),
    notes            VARCHAR(500),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by       BIGINT,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by       BIGINT,
    version          INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT ck_coll_promise_kept CHECK (status NOT IN ('KEPT', 'PARTIALLY_KEPT') OR paid_date IS NOT NULL)
);
COMMENT ON TABLE collection.collection_promises IS
    'Төлөх амлалт (PTP) — биелэлт нь цуглуулгын үр нөлөөний гол хэмжүүр';

CREATE TABLE collection.collection_payments (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_id          UUID     NOT NULL REFERENCES collection.collection_cases (id) ON DELETE CASCADE,
    promise_id       BIGINT   REFERENCES collection.collection_promises (id),
    loan_payment_id  BIGINT   REFERENCES loan.loan_payments (id),
    amount           system.money_positive NOT NULL CHECK (amount > 0),
    currency_code    system.currency_code NOT NULL DEFAULT 'MNT',
    payment_date     DATE     NOT NULL,
    agency_id        SMALLINT REFERENCES collection.collection_agencies (id),
    commission_amount system.money_positive NOT NULL DEFAULT 0,
    collected_by     BIGINT   REFERENCES core.employees (id),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by       BIGINT
);
COMMENT ON TABLE collection.collection_payments IS
    'Барагдуулалтаар орсон төлбөр — loan_payments-тай холбогдоно (мөнгө нэг л удаа бүртгэгдэнэ)';

CREATE TABLE collection.recovery_cases (
    id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    recovery_no         VARCHAR(30) NOT NULL,
    loan_id             UUID     NOT NULL REFERENCES loan.loans (id) ON DELETE RESTRICT,
    writeoff_id         BIGINT   REFERENCES loan.loan_writeoffs (id),
    collection_case_id  UUID     REFERENCES collection.collection_cases (id),
    recovery_type       VARCHAR(30) NOT NULL
                        CHECK (recovery_type IN ('COLLATERAL_SALE', 'GUARANTOR_CLAIM', 'LEGAL_JUDGMENT',
                                                 'VOLUNTARY_PAYMENT', 'INSURANCE_CLAIM', 'DEBT_SALE', 'SETTLEMENT')),
    expected_amount     system.money_positive NOT NULL DEFAULT 0,
    recovered_amount    system.money_positive NOT NULL DEFAULT 0,
    recovery_cost       system.money_positive NOT NULL DEFAULT 0,
    net_recovery        system.money_amount GENERATED ALWAYS AS (recovered_amount - recovery_cost) STORED,
    currency_code       system.currency_code NOT NULL DEFAULT 'MNT',
    court_case_ref      VARCHAR(100),
    bailiff_ref         VARCHAR(100),
    started_date        DATE NOT NULL,
    completed_date      DATE,
    status              VARCHAR(20) NOT NULL DEFAULT 'IN_PROGRESS'
                        CHECK (status IN ('PLANNED', 'IN_PROGRESS', 'COURT', 'ENFORCEMENT', 'COMPLETED', 'FAILED', 'ABANDONED')),
    notes               TEXT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by          BIGINT,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by          BIGINT,
    version             INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT uq_recovery_no UNIQUE (recovery_no),
    CONSTRAINT ck_recovery_completed CHECK (completed_date IS NULL OR completed_date >= started_date),
    CONSTRAINT ck_recovery_status_done CHECK (status <> 'COMPLETED' OR completed_date IS NOT NULL)
);

CREATE TABLE collection.case_assignments (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    case_id       UUID     NOT NULL REFERENCES collection.collection_cases (id) ON DELETE CASCADE,
    assigned_to   BIGINT   REFERENCES core.employees (id),
    agency_id     SMALLINT REFERENCES collection.collection_agencies (id),
    stage_id      SMALLINT NOT NULL REFERENCES collection.collection_stages (id),
    assigned_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    assigned_by   BIGINT,
    unassigned_at TIMESTAMPTZ,
    reason        VARCHAR(300),
    CONSTRAINT ck_case_assign_target CHECK (assigned_to IS NOT NULL OR agency_id IS NOT NULL),
    CONSTRAINT ck_case_assign_period CHECK (unassigned_at IS NULL OR unassigned_at >= assigned_at)
);
COMMENT ON TABLE collection.case_assignments IS 'Кейс хэнд хэзээ хуваарилагдсан түүх — ажилтны гүйцэтгэлийн хэмжүүр';
