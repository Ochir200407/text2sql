-- =====================================================================
-- 003_customer.sql — Харилцагчийн мастер (хувь хүн + байгууллага)
-- ---------------------------------------------------------------------
-- Архитектур: party/role загвар.
--   customer                — нийтлэг хэсэг (дугаар, төрөл, төлөв, сегмент)
--   customer_individuals    — хувь хүний өргөтгөл (1:1)
--   customer_organizations  — байгууллагын өргөтгөл (1:1)
-- Ингэснээр зээл, данс, KYC бүгд ГАНЦ customer_id-тэй ажиллана.
--
-- PK: UUID — харилцагч нь мобайл апп, credit bureau, ETL зэрэг гадаад
-- системд ил гардаг; дараалсан BIGINT нь харилцагчийн тоог алдагдуулна
-- (enumeration attack) ба олон салбарт офлайн үүсгэх боломж хаагдана.
--
-- Sensitive: регистрийн дугаарыг ил хадгалахгүй —
--   *_enc  (pgcrypto-оор шифрлэсэн), *_hash (HMAC — хайлт/давхардал), *_masked (харуулах)
-- =====================================================================

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------------
-- Лавлах
-- ---------------------------------------------------------------------
CREATE TABLE customer.customer_types (
    id           SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    type_code    system.code_key   NOT NULL,
    type_name    VARCHAR(100)      NOT NULL,
    party_type   system.party_type NOT NULL,
    description  TEXT,
    is_active    BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_customer_types_code UNIQUE (type_code)
);

CREATE TABLE customer.customer_statuses (
    id           SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    status_code  system.code_key NOT NULL,
    status_name  VARCHAR(100)    NOT NULL,
    is_active_state BOOLEAN NOT NULL DEFAULT TRUE,   -- энэ төлөвт үйлчилгээ авах боломжтой юу
    blocks_new_loan BOOLEAN NOT NULL DEFAULT FALSE,
    sort_order   SMALLINT NOT NULL DEFAULT 0,
    CONSTRAINT uq_customer_statuses_code UNIQUE (status_code)
);

CREATE TABLE customer.customer_segments (
    id            SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    segment_code  system.code_key NOT NULL,
    segment_name  VARCHAR(100)    NOT NULL,
    description   TEXT,
    min_income    system.money_positive,
    priority_rank SMALLINT NOT NULL DEFAULT 0,
    is_active     BOOLEAN  NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_customer_segments_code UNIQUE (segment_code)
);

-- ---------------------------------------------------------------------
-- Харилцагч — нийтлэг хэсэг
-- ---------------------------------------------------------------------
CREATE TABLE customer.customers (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_number     VARCHAR(20)       NOT NULL,       -- бизнес түлхүүр (DWH business key)
    party_type          system.party_type NOT NULL,
    customer_type_id    SMALLINT NOT NULL REFERENCES customer.customer_types (id),
    status_id           SMALLINT NOT NULL REFERENCES customer.customer_statuses (id),
    segment_id          SMALLINT REFERENCES customer.customer_segments (id),
    branch_id           BIGINT   NOT NULL REFERENCES core.branches (id),
    relationship_manager_id BIGINT REFERENCES core.employees (id),

    display_name        VARCHAR(300) NOT NULL,            -- хайлт/жагсаалтад (хувь хүн: овог нэр)
    registration_no_enc    BYTEA,                         -- регистр/ТТД — шифрлэсэн
    registration_no_hash   BYTEA,                         -- HMAC — давхардал шалгах
    registration_no_masked VARCHAR(20),                   -- УБ**1234 — дэлгэцэнд

    country_code        system.country_code NOT NULL DEFAULT 'MN',
    is_resident         BOOLEAN NOT NULL DEFAULT TRUE,

    -- KYC/AML товч төлөв (дэлгэрэнгүй kyc схемд; энд зөвхөн шийдвэрт хэрэглэх хураангуй)
    kyc_status          VARCHAR(20) NOT NULL DEFAULT 'NOT_STARTED'
                        CHECK (kyc_status IN ('NOT_STARTED', 'IN_PROGRESS', 'VERIFIED', 'REJECTED', 'EXPIRED')),
    kyc_verified_at     TIMESTAMPTZ,
    kyc_expires_at      TIMESTAMPTZ,
    aml_status          VARCHAR(20) NOT NULL DEFAULT 'NOT_SCREENED'
                        CHECK (aml_status IN ('NOT_SCREENED', 'CLEAR', 'MATCH_PENDING', 'BLOCKED')),
    risk_level          VARCHAR(10) CHECK (risk_level IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    is_pep              BOOLEAN NOT NULL DEFAULT FALSE,
    is_sanctioned       BOOLEAN NOT NULL DEFAULT FALSE,
    is_blacklisted      BOOLEAN NOT NULL DEFAULT FALSE,

    onboarded_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    closed_at           TIMESTAMPTZ,
    close_reason        VARCHAR(200),
    notes               TEXT,
    extra_attributes    JSONB NOT NULL DEFAULT '{}'::JSONB,  -- бүтээгдэхүүн/кампанит ажлын нэмэлт талбар

    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by          BIGINT,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by          BIGINT,
    version             INTEGER     NOT NULL DEFAULT 1,
    deleted_at          TIMESTAMPTZ,

    CONSTRAINT uq_customers_number UNIQUE (customer_number),
    CONSTRAINT uq_customers_reg_hash UNIQUE (registration_no_hash),
    CONSTRAINT ck_customers_kyc_verified
        CHECK (kyc_status <> 'VERIFIED' OR kyc_verified_at IS NOT NULL),
    CONSTRAINT ck_customers_closed
        CHECK (closed_at IS NULL OR closed_at >= onboarded_at)
);
COMMENT ON TABLE  customer.customers IS 'Харилцагчийн нийтлэг мастер. Хувь хүн/байгууллагын өргөтгөл 1:1 хүснэгтэд';
COMMENT ON COLUMN customer.customers.registration_no_enc  IS 'pgp_sym_encrypt(регистр, key) — түлхүүр нь KMS-д, DB-д биш';
COMMENT ON COLUMN customer.customers.registration_no_hash IS 'hmac(регистр, pepper) — давхардал/хайлтад. UNIQUE';
COMMENT ON COLUMN customer.customers.extra_attributes     IS 'Схем өөрчлөхгүйгээр нэмэх талбарууд (JSONB, индекслэх боломжтой)';

-- Хувь хүн (1:1)
CREATE TABLE customer.customer_individuals (
    customer_id       UUID PRIMARY KEY REFERENCES customer.customers (id) ON DELETE RESTRICT,
    last_name         VARCHAR(100) NOT NULL,
    first_name        VARCHAR(100) NOT NULL,
    middle_name       VARCHAR(100),
    birth_date        DATE         NOT NULL,
    gender            system.gender_type,
    nationality_code  system.country_code NOT NULL DEFAULT 'MN',
    marital_status    VARCHAR(20) CHECK (marital_status IN ('SINGLE', 'MARRIED', 'DIVORCED', 'WIDOWED')),
    dependants_count  SMALLINT CHECK (dependants_count >= 0),
    education_level   VARCHAR(30),
    occupation        VARCHAR(150),
    employer_name     VARCHAR(200),
    employment_type   VARCHAR(30) CHECK (employment_type IN
                       ('EMPLOYED', 'SELF_EMPLOYED', 'BUSINESS_OWNER', 'RETIRED', 'STUDENT', 'UNEMPLOYED')),
    monthly_income    system.money_positive,
    income_currency   system.currency_code NOT NULL DEFAULT 'MNT',
    id_document_type  VARCHAR(30) NOT NULL DEFAULT 'NATIONAL_ID'
                      CHECK (id_document_type IN ('NATIONAL_ID', 'PASSPORT', 'RESIDENCE_PERMIT', 'DRIVER_LICENSE')),
    id_issued_date    DATE,
    id_expiry_date    DATE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by        BIGINT,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by        BIGINT,
    version           INTEGER     NOT NULL DEFAULT 1,
    -- CHECK дотор CURRENT_DATE ашиглахгүй: тухайн үедээ зөв байсан мөр хожим "буруу" болж
    -- pg_dump/restore унана. Насны шалгалт trigger-т (triggers/001_triggers.sql).
    CONSTRAINT ck_cust_ind_birth CHECK (birth_date > DATE '1900-01-01'),
    CONSTRAINT ck_cust_ind_id_dates CHECK (id_expiry_date IS NULL OR id_issued_date IS NULL
                                           OR id_expiry_date > id_issued_date)
);

-- Байгууллага (1:1)
CREATE TABLE customer.customer_organizations (
    customer_id           UUID PRIMARY KEY REFERENCES customer.customers (id) ON DELETE RESTRICT,
    legal_name            VARCHAR(300) NOT NULL,
    trade_name            VARCHAR(300),
    legal_form            VARCHAR(30) CHECK (legal_form IN ('LLC', 'JSC', 'PARTNERSHIP', 'SOLE_PROPRIETOR', 'NGO', 'STATE', 'COOPERATIVE')),
    industry_code         VARCHAR(20),                    -- ISIC/ҮАА-ны салбарын код
    establishment_date    DATE,
    employee_count        INTEGER CHECK (employee_count >= 0),
    annual_revenue        system.money_positive,
    revenue_currency      system.currency_code NOT NULL DEFAULT 'MNT',
    tax_registration_no   VARCHAR(30),
    is_licensed           BOOLEAN NOT NULL DEFAULT FALSE,
    license_number        VARCHAR(50),
    license_expiry_date   DATE,
    ultimate_owner_name   VARCHAR(300),                   -- UBO — AML шаардлага
    website               VARCHAR(200),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by            BIGINT,
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by            BIGINT,
    version               INTEGER     NOT NULL DEFAULT 1,
    CONSTRAINT ck_cust_org_license CHECK (NOT is_licensed OR license_number IS NOT NULL)
);
COMMENT ON COLUMN customer.customer_organizations.ultimate_owner_name IS 'Эцсийн өмчлөгч (UBO) — AML-ийн заавал шаардлага';

-- ---------------------------------------------------------------------
-- Хаяг, холбоо барих
-- ---------------------------------------------------------------------
CREATE TABLE customer.customer_addresses (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id   UUID        NOT NULL REFERENCES customer.customers (id) ON DELETE CASCADE,
    address_type  VARCHAR(20) NOT NULL
                  CHECK (address_type IN ('RESIDENTIAL', 'REGISTERED', 'WORK', 'MAILING', 'LEGAL', 'OPERATIONAL')),
    country_code  system.country_code NOT NULL DEFAULT 'MN',
    province      VARCHAR(100),
    district      VARCHAR(100),
    khoroo        VARCHAR(50),
    street        VARCHAR(200),
    building      VARCHAR(100),
    apartment     VARCHAR(50),
    postal_code   VARCHAR(20),
    full_address  VARCHAR(500) GENERATED ALWAYS AS (
        COALESCE(province || ', ', '') || COALESCE(district || ', ', '') ||
        COALESCE(khoroo || ', ', '') || COALESCE(street || ' ', '') ||
        COALESCE(building, '') || COALESCE(' ' || apartment, '')
    ) STORED,
    geo_lat       NUMERIC(10, 7) CHECK (geo_lat BETWEEN -90 AND 90),
    geo_lon       NUMERIC(10, 7) CHECK (geo_lon BETWEEN -180 AND 180),
    is_primary    BOOLEAN     NOT NULL DEFAULT FALSE,
    valid_from    DATE        NOT NULL DEFAULT CURRENT_DATE,
    valid_to      DATE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by    BIGINT,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by    BIGINT,
    version       INTEGER     NOT NULL DEFAULT 1,
    deleted_at    TIMESTAMPTZ,
    CONSTRAINT ck_cust_addr_period CHECK (valid_to IS NULL OR valid_to >= valid_from)
);
COMMENT ON COLUMN customer.customer_addresses.full_address IS 'Generated column — хайлт/хэвлэлтэд бэлэн бүтэн хаяг';

CREATE TABLE customer.customer_contacts (
    id            BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id   UUID        NOT NULL REFERENCES customer.customers (id) ON DELETE CASCADE,
    contact_type  VARCHAR(20) NOT NULL
                  CHECK (contact_type IN ('MOBILE', 'HOME_PHONE', 'WORK_PHONE', 'EMAIL', 'FAX', 'SOCIAL')),
    contact_value VARCHAR(200) NOT NULL,
    is_primary    BOOLEAN     NOT NULL DEFAULT FALSE,
    is_verified   BOOLEAN     NOT NULL DEFAULT FALSE,
    verified_at   TIMESTAMPTZ,
    opt_in_marketing BOOLEAN  NOT NULL DEFAULT FALSE,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by    BIGINT,
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by    BIGINT,
    version       INTEGER     NOT NULL DEFAULT 1,
    deleted_at    TIMESTAMPTZ,
    CONSTRAINT uq_cust_contact UNIQUE (customer_id, contact_type, contact_value),
    CONSTRAINT ck_cust_contact_verified CHECK (NOT is_verified OR verified_at IS NOT NULL)
);

-- ---------------------------------------------------------------------
-- Холбоотой этгээд (гэр бүл, хувьцаа эзэмшигч, охин компани, албан тушаалтан)
-- ---------------------------------------------------------------------
CREATE TABLE customer.customer_relationships (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id        UUID NOT NULL REFERENCES customer.customers (id) ON DELETE CASCADE,
    related_customer_id UUID REFERENCES customer.customers (id),
    relationship_type  VARCHAR(30) NOT NULL
                       CHECK (relationship_type IN ('SPOUSE', 'PARENT', 'CHILD', 'SIBLING', 'EMPLOYER',
                                                    'SHAREHOLDER', 'DIRECTOR', 'SUBSIDIARY', 'PARENT_COMPANY',
                                                    'GUARANTOR', 'RELATED_PARTY', 'EMERGENCY_CONTACT')),
    -- Холбоотой этгээд системд бүртгэлгүй бол нэрээр
    related_name       VARCHAR(300),
    related_phone      system.phone_number,
    ownership_pct      system.percent_share,
    is_related_party   BOOLEAN NOT NULL DEFAULT FALSE,    -- холбогдох этгээдийн зээлийн хязгаарт
    valid_from         DATE    NOT NULL DEFAULT CURRENT_DATE,
    valid_to           DATE,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by         BIGINT,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by         BIGINT,
    version            INTEGER     NOT NULL DEFAULT 1,
    CONSTRAINT ck_cust_rel_target CHECK (related_customer_id IS NOT NULL OR related_name IS NOT NULL),
    CONSTRAINT ck_cust_rel_not_self CHECK (related_customer_id IS NULL OR related_customer_id <> customer_id),
    CONSTRAINT ck_cust_rel_period CHECK (valid_to IS NULL OR valid_to >= valid_from)
);
COMMENT ON TABLE customer.customer_relationships IS
    'Холбогдох этгээдийн сүлжээ — АМL, холбоотой этгээдийн зээлийн хязгаар, батлан даалтад ашиглагдана';

-- Орлогын эх үүсвэр (DTI тооцоонд)
CREATE TABLE customer.customer_employments (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id      UUID NOT NULL REFERENCES customer.customers (id) ON DELETE CASCADE,
    employer_name    VARCHAR(300) NOT NULL,
    employer_customer_id UUID REFERENCES customer.customers (id),
    position         VARCHAR(150),
    income_type      VARCHAR(30) NOT NULL DEFAULT 'SALARY'
                     CHECK (income_type IN ('SALARY', 'BUSINESS', 'RENTAL', 'PENSION', 'DIVIDEND', 'OTHER')),
    monthly_income   system.money_positive NOT NULL,
    currency_code    system.currency_code  NOT NULL DEFAULT 'MNT',
    is_verified      BOOLEAN NOT NULL DEFAULT FALSE,
    verification_source VARCHAR(50),                      -- НДШ, банкны хуулга, гэрээ
    start_date       DATE,
    end_date         DATE,
    is_primary       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by       BIGINT,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by       BIGINT,
    version          INTEGER     NOT NULL DEFAULT 1,
    CONSTRAINT ck_cust_emp_period CHECK (end_date IS NULL OR start_date IS NULL OR end_date >= start_date)
);

-- Гадаад банкны данс (зээл олгох / төлбөр татах)
CREATE TABLE customer.customer_bank_accounts (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id    UUID        NOT NULL REFERENCES customer.customers (id) ON DELETE CASCADE,
    bank_code      VARCHAR(20) NOT NULL,
    bank_name      VARCHAR(200) NOT NULL,
    account_number VARCHAR(40) NOT NULL,
    account_name   VARCHAR(300) NOT NULL,
    currency_code  system.currency_code NOT NULL DEFAULT 'MNT',
    is_primary     BOOLEAN     NOT NULL DEFAULT FALSE,
    is_verified    BOOLEAN     NOT NULL DEFAULT FALSE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by     BIGINT,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by     BIGINT,
    version        INTEGER     NOT NULL DEFAULT 1,
    deleted_at     TIMESTAMPTZ,
    CONSTRAINT uq_cust_bank_acc UNIQUE (customer_id, bank_code, account_number)
);

-- ---------------------------------------------------------------------
-- Түүхийн хүснэгтүүд — дарж бичихгүй (§15)
-- ---------------------------------------------------------------------
CREATE TABLE customer.customer_status_history (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id     UUID     NOT NULL REFERENCES customer.customers (id) ON DELETE CASCADE,
    old_status_id   SMALLINT REFERENCES customer.customer_statuses (id),
    new_status_id   SMALLINT NOT NULL REFERENCES customer.customer_statuses (id),
    change_reason   VARCHAR(300),
    changed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    changed_by      BIGINT
);

CREATE TABLE customer.customer_segment_history (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id     UUID     NOT NULL REFERENCES customer.customers (id) ON DELETE CASCADE,
    old_segment_id  SMALLINT REFERENCES customer.customer_segments (id),
    new_segment_id  SMALLINT NOT NULL REFERENCES customer.customer_segments (id),
    change_reason   VARCHAR(300),
    valid_from      DATE     NOT NULL DEFAULT CURRENT_DATE,
    changed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    changed_by      BIGINT
);
COMMENT ON TABLE customer.customer_segment_history IS
    'Сегментийн шилжилтийн түүх — DWH-д SCD Type 2 хэмжигдэхүүн болж хөрвөнө';
