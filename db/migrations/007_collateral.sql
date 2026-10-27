-- =====================================================================
-- 007_collateral.sql — Барьцаа хөрөнгө, үнэлгээ, даатгал, батлан даагч
-- ---------------------------------------------------------------------
-- Барьцаа нь ЗЭЭЛЭЭС ХАМААРАЛГҮЙ обьект: нэг хөрөнгө олон зээлийг
-- (эсвэл нэг зээл олон хөрөнгийг) хангаж болно → loan_collaterals M:N,
-- хуваарилсан дүн (allocated_amount) хамт.
-- =====================================================================

\set ON_ERROR_STOP on

CREATE TABLE collateral.collateral_types (
    id                SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    type_code         system.code_key NOT NULL,
    type_name         VARCHAR(150)    NOT NULL,
    category          VARCHAR(30)     NOT NULL
                      CHECK (category IN ('REAL_ESTATE', 'VEHICLE', 'EQUIPMENT', 'INVENTORY', 'DEPOSIT',
                                          'SECURITIES', 'RECEIVABLE', 'LIVESTOCK', 'GUARANTEE', 'OTHER')),
    max_ltv_ratio     system.percent_share NOT NULL,     -- энэ төрлийн зөвшөөрөх дээд LTV
    haircut_pct       system.percent_share NOT NULL DEFAULT 0,
    revaluation_months SMALLINT CHECK (revaluation_months > 0),
    requires_insurance BOOLEAN NOT NULL DEFAULT FALSE,
    requires_registration BOOLEAN NOT NULL DEFAULT FALSE, -- улсын бүртгэлд бүртгүүлэх эсэх
    is_active         BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_collateral_types_code UNIQUE (type_code)
);
COMMENT ON COLUMN collateral.collateral_types.haircut_pct IS
    'Үнэлгээнээс хасах хувь — эрсдэлийн бууралт (зах зээлийн хэлбэлзэл)';

CREATE TABLE collateral.collaterals (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    collateral_number    VARCHAR(30) NOT NULL,
    collateral_type_id   SMALLINT NOT NULL REFERENCES collateral.collateral_types (id),
    owner_customer_id    UUID     NOT NULL REFERENCES customer.customers (id),
    branch_id            BIGINT   REFERENCES core.branches (id),

    description          VARCHAR(500) NOT NULL,
    -- Улсын бүртгэл / гэрчилгээ
    registration_number  VARCHAR(100),
    registration_authority VARCHAR(200),
    registered_date      DATE,
    -- Байршил (үл хөдлөх, тээврийн хэрэгсэл)
    location_address     VARCHAR(500),
    geo_lat              NUMERIC(10, 7) CHECK (geo_lat BETWEEN -90 AND 90),
    geo_lon              NUMERIC(10, 7) CHECK (geo_lon BETWEEN -180 AND 180),

    market_value         system.money_positive NOT NULL,
    forced_sale_value    system.money_positive,
    accepted_value       system.money_positive NOT NULL,   -- haircut-ийн дараах хүлээн зөвшөөрөх дүн
    currency_code        system.currency_code NOT NULL DEFAULT 'MNT',
    valuation_date       DATE     NOT NULL,
    next_valuation_date  DATE,

    is_insured           BOOLEAN  NOT NULL DEFAULT FALSE,
    encumbrance_status   VARCHAR(20) NOT NULL DEFAULT 'FREE'
                         CHECK (encumbrance_status IN ('FREE', 'PLEDGED', 'PARTIALLY_PLEDGED', 'SEIZED', 'SOLD', 'RELEASED')),
    pledge_rank          SMALLINT CHECK (pledge_rank > 0),  -- 1 = тэргүүн ээлжийн барьцаа
    status               VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
                         CHECK (status IN ('DRAFT', 'ACTIVE', 'RELEASED', 'LIQUIDATED', 'WRITTEN_OFF')),
    attributes           JSONB NOT NULL DEFAULT '{}'::JSONB,  -- төрөл бүрийн онцлог (м², улсын дугаар, он)
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by           BIGINT,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by           BIGINT,
    version              INTEGER  NOT NULL DEFAULT 1,
    deleted_at           TIMESTAMPTZ,
    CONSTRAINT uq_collaterals_number UNIQUE (collateral_number),
    CONSTRAINT uq_collaterals_id_currency UNIQUE (id, currency_code),
    CONSTRAINT ck_collateral_accepted_le_market CHECK (accepted_value <= market_value),
    CONSTRAINT ck_collateral_next_val CHECK (next_valuation_date IS NULL OR next_valuation_date >= valuation_date)
);
COMMENT ON COLUMN collateral.collaterals.attributes IS
    'Төрлөөс хамаарсан талбарууд JSONB-д: {area_m2, floor, plate_no, vin, year} — schema тэсрэхгүй';

CREATE TABLE collateral.collateral_valuations (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    collateral_id     UUID     NOT NULL REFERENCES collateral.collaterals (id) ON DELETE CASCADE,
    valuation_no      SMALLINT NOT NULL DEFAULT 1 CHECK (valuation_no > 0),
    valuation_type    VARCHAR(20) NOT NULL DEFAULT 'INITIAL'
                      CHECK (valuation_type IN ('INITIAL', 'PERIODIC', 'PRE_LIQUIDATION', 'DISPUTE', 'INTERNAL')),
    market_value      system.money_positive NOT NULL,
    forced_sale_value system.money_positive,
    currency_code     system.currency_code NOT NULL DEFAULT 'MNT',
    method            VARCHAR(30) CHECK (method IN ('MARKET_COMPARISON', 'INCOME', 'COST', 'EXPERT_OPINION')),
    valuator_name     VARCHAR(200),
    valuator_license  VARCHAR(100),
    is_independent    BOOLEAN  NOT NULL DEFAULT TRUE,
    valuation_date    DATE     NOT NULL,
    valid_until       DATE,
    report_document_id UUID,
    notes             TEXT,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by        BIGINT,
    CONSTRAINT uq_collateral_valuation_no UNIQUE (collateral_id, valuation_no),
    CONSTRAINT ck_collateral_val_until CHECK (valid_until IS NULL OR valid_until >= valuation_date)
);
COMMENT ON TABLE collateral.collateral_valuations IS 'Үнэлгээний ТҮҮХ — дарж бичихгүй. Одоогийн утга collaterals дээр';

CREATE TABLE collateral.collateral_documents (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    collateral_id  UUID        NOT NULL REFERENCES collateral.collaterals (id) ON DELETE CASCADE,
    document_id    UUID,                                  -- system.documents(id) — 014-т FK
    doc_type       VARCHAR(40) NOT NULL
                   CHECK (doc_type IN ('OWNERSHIP_CERTIFICATE', 'PLEDGE_AGREEMENT', 'VALUATION_REPORT',
                                       'INSURANCE_POLICY', 'PHOTO', 'REGISTRATION_EXTRACT', 'RELEASE_LETTER', 'OTHER')),
    doc_number     VARCHAR(100),
    issued_date    DATE,
    expiry_date    DATE,
    is_original_held BOOLEAN NOT NULL DEFAULT FALSE,      -- эх хувь байгууллагад хадгалагдаж байгаа эсэх
    vault_location VARCHAR(100),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by     BIGINT,
    CONSTRAINT ck_collateral_doc_dates CHECK (expiry_date IS NULL OR issued_date IS NULL OR expiry_date > issued_date)
);

CREATE TABLE collateral.collateral_insurances (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    collateral_id     UUID        NOT NULL REFERENCES collateral.collaterals (id) ON DELETE CASCADE,
    insurer_name      VARCHAR(200) NOT NULL,
    policy_number     VARCHAR(100) NOT NULL,
    insured_amount    system.money_positive NOT NULL,
    currency_code     system.currency_code NOT NULL DEFAULT 'MNT',
    premium_amount    system.money_positive,
    beneficiary       VARCHAR(200),                       -- ББСБ нь ашиг хүртэгч эсэх
    is_lender_beneficiary BOOLEAN NOT NULL DEFAULT TRUE,
    start_date        DATE NOT NULL,
    end_date          DATE NOT NULL,
    status            VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
                      CHECK (status IN ('ACTIVE', 'EXPIRED', 'CANCELLED', 'CLAIMED')),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by        BIGINT,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by        BIGINT,
    version           INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT uq_collateral_policy UNIQUE (insurer_name, policy_number),
    CONSTRAINT ck_collateral_ins_period CHECK (end_date > start_date)
);

-- Зээл ↔ барьцаа (M:N, хуваарилалттай)
CREATE TABLE collateral.loan_collaterals (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    loan_id           UUID     NOT NULL REFERENCES loan.loans (id) ON DELETE RESTRICT,
    collateral_id     UUID     NOT NULL REFERENCES collateral.collaterals (id) ON DELETE RESTRICT,
    currency_code     system.currency_code NOT NULL,
    allocated_amount  system.money_positive NOT NULL CHECK (allocated_amount > 0),
    pledge_rank       SMALLINT NOT NULL DEFAULT 1 CHECK (pledge_rank > 0),
    ltv_ratio         system.percent_share,
    pledge_date       DATE     NOT NULL,
    release_date      DATE,
    release_reason    VARCHAR(300),
    registered_ref    VARCHAR(100),                       -- барьцааны улсын бүртгэлийн дугаар
    status            VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
                      CHECK (status IN ('PENDING', 'ACTIVE', 'RELEASED', 'LIQUIDATED')),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by        BIGINT,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by        BIGINT,
    version           INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT uq_loan_collateral UNIQUE (loan_id, collateral_id),
    CONSTRAINT fk_loan_coll_loan_currency FOREIGN KEY (loan_id, currency_code)
        REFERENCES loan.loans (id, currency_code),
    CONSTRAINT fk_loan_coll_coll_currency FOREIGN KEY (collateral_id, currency_code)
        REFERENCES collateral.collaterals (id, currency_code),
    CONSTRAINT ck_loan_coll_release CHECK (release_date IS NULL OR release_date >= pledge_date),
    CONSTRAINT ck_loan_coll_released CHECK (status <> 'RELEASED' OR release_date IS NOT NULL)
);
COMMENT ON TABLE collateral.loan_collaterals IS
    'Нэг барьцаа олон зээлд хуваарилагдаж болно. Нийт хуваарилалт нь accepted_value-ээс хэтрэхгүй '
    '(validation/001_validation.sql-д шалгана)';

-- Батлан даагч
CREATE TABLE collateral.guarantors (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    guarantor_number   VARCHAR(30) NOT NULL,
    customer_id        UUID REFERENCES customer.customers (id),
    -- Системд бүртгэлгүй батлан даагч
    external_name      VARCHAR(300),
    external_register_hash BYTEA,
    external_phone     system.phone_number,
    guarantor_type     VARCHAR(20) NOT NULL DEFAULT 'PERSONAL'
                       CHECK (guarantor_type IN ('PERSONAL', 'CORPORATE', 'GOVERNMENT', 'FUND', 'BANK')),
    monthly_income     system.money_positive,
    net_worth          system.money_positive,
    max_guarantee_amount system.money_positive,
    status             VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
                       CHECK (status IN ('ACTIVE', 'RELEASED', 'CALLED', 'BLACKLISTED')),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by         BIGINT,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by         BIGINT,
    version            INTEGER NOT NULL DEFAULT 1,
    deleted_at         TIMESTAMPTZ,
    CONSTRAINT uq_guarantors_number UNIQUE (guarantor_number),
    CONSTRAINT ck_guarantor_identity CHECK (customer_id IS NOT NULL OR external_name IS NOT NULL)
);

CREATE TABLE collateral.loan_guarantors (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    loan_id            UUID     NOT NULL REFERENCES loan.loans (id) ON DELETE RESTRICT,
    guarantor_id       UUID     NOT NULL REFERENCES collateral.guarantors (id) ON DELETE RESTRICT,
    currency_code      system.currency_code NOT NULL,
    guarantee_amount   system.money_positive NOT NULL CHECK (guarantee_amount > 0),
    guarantee_pct      system.percent_share,
    liability_type     VARCHAR(20) NOT NULL DEFAULT 'JOINT'
                       CHECK (liability_type IN ('JOINT', 'SEVERAL', 'JOINT_AND_SEVERAL', 'LIMITED')),
    contract_document_id UUID,
    signed_date        DATE,
    valid_from         DATE     NOT NULL,
    valid_to           DATE,
    called_date        DATE,                              -- батлан даалт нэхэмжилсэн огноо
    called_amount      system.money_positive NOT NULL DEFAULT 0,
    recovered_amount   system.money_positive NOT NULL DEFAULT 0,
    status             VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
                       CHECK (status IN ('PENDING', 'ACTIVE', 'RELEASED', 'CALLED', 'SETTLED')),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by         BIGINT,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by         BIGINT,
    version            INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT uq_loan_guarantor UNIQUE (loan_id, guarantor_id),
    CONSTRAINT fk_loan_guar_currency FOREIGN KEY (loan_id, currency_code) REFERENCES loan.loans (id, currency_code),
    CONSTRAINT ck_loan_guar_period CHECK (valid_to IS NULL OR valid_to >= valid_from),
    CONSTRAINT ck_loan_guar_called CHECK (status <> 'CALLED' OR called_date IS NOT NULL),
    CONSTRAINT ck_loan_guar_recovered CHECK (recovered_amount <= called_amount OR called_amount = 0)
);
