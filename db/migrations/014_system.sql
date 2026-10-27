-- =====================================================================
-- 014_system.sql — Лавлах, валют, баримт, мэдэгдэл, аудит, интеграц
-- ---------------------------------------------------------------------
-- Энэ файлын төгсгөлд:
--   * баримтын (document_id) FK-үүд бүх домэйнд нэмэгдэнэ
--   * created_by / updated_by → core.users FK-үүд DO-давталтаар нэмэгдэнэ
--     (бүх хүснэгт үүссэний ДАРАА — циклик хамаарлыг тойрох цэвэр арга)
-- =====================================================================

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------------
-- Лавлах
-- ---------------------------------------------------------------------
CREATE TABLE system.countries (
    code           system.country_code PRIMARY KEY,
    name_mn        VARCHAR(150) NOT NULL,
    name_en        VARCHAR(150) NOT NULL,
    iso3           CHAR(3),
    phone_prefix   VARCHAR(10),
    is_high_risk   BOOLEAN NOT NULL DEFAULT FALSE,        -- FATF өндөр эрсдэлт улс
    is_sanctioned  BOOLEAN NOT NULL DEFAULT FALSE,
    is_active      BOOLEAN NOT NULL DEFAULT TRUE
);
COMMENT ON COLUMN system.countries.is_high_risk IS 'AML эрсдэлийн үнэлгээнд газарзүйн хүчин зүйл болж ордог';

CREATE TABLE system.currencies (
    code           system.currency_code PRIMARY KEY,
    name_mn        VARCHAR(100) NOT NULL,
    name_en        VARCHAR(100) NOT NULL,
    symbol         VARCHAR(10),
    decimal_places SMALLINT NOT NULL DEFAULT 2 CHECK (decimal_places BETWEEN 0 AND 6),
    is_base        BOOLEAN NOT NULL DEFAULT FALSE,        -- суурь валют (MNT) — яг нэг
    is_active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
COMMENT ON TABLE system.currencies IS 'MNT, USD, EUR, CNY, KRW, JPY ... Суурь валют яг нэг (partial unique index 015-д)';

CREATE TABLE system.exchange_rates (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    from_currency  system.currency_code NOT NULL REFERENCES system.currencies (code),
    to_currency    system.currency_code NOT NULL REFERENCES system.currencies (code),
    rate_date      DATE          NOT NULL,
    buy_rate       system.fx_rate,
    sell_rate      system.fx_rate,
    mid_rate       system.fx_rate NOT NULL,
    official_rate  system.fx_rate,                        -- Монголбанкны албан ханш
    source         VARCHAR(50) NOT NULL DEFAULT 'CENTRAL_BANK'
                   CHECK (source IN ('CENTRAL_BANK', 'INTERNAL', 'MARKET', 'PARTNER')),
    is_active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by     BIGINT,
    CONSTRAINT uq_exchange_rate UNIQUE (from_currency, to_currency, rate_date, source),
    CONSTRAINT ck_fx_different CHECK (from_currency <> to_currency),
    CONSTRAINT ck_fx_spread CHECK (buy_rate IS NULL OR sell_rate IS NULL OR sell_rate >= buy_rate)
);
COMMENT ON TABLE system.exchange_rates IS
    'Ханшийн ТҮҮХ — өдөр бүрийн мөр үлдэнэ. Гүйлгээ өөрийн үеийн ханшаар хөрвөнө (дахин тооцоологдохгүй)';

CREATE TABLE system.business_calendar (
    calendar_date  DATE PRIMARY KEY,
    is_business_day BOOLEAN NOT NULL DEFAULT TRUE,
    is_holiday     BOOLEAN NOT NULL DEFAULT FALSE,
    holiday_name   VARCHAR(150),
    country_code   system.country_code NOT NULL DEFAULT 'MN',
    year_number    SMALLINT GENERATED ALWAYS AS (EXTRACT(YEAR FROM calendar_date)::SMALLINT) STORED,
    month_number   SMALLINT GENERATED ALWAYS AS (EXTRACT(MONTH FROM calendar_date)::SMALLINT) STORED,
    day_of_week    SMALLINT GENERATED ALWAYS AS (EXTRACT(ISODOW FROM calendar_date)::SMALLINT) STORED,
    CONSTRAINT ck_calendar_holiday CHECK (NOT is_holiday OR holiday_name IS NOT NULL)
);
COMMENT ON TABLE system.business_calendar IS
    'Ажлын өдрийн хуанли — төлбөрийн хугацаа амралтын өдөр таарвал шилжүүлэх дүрэмд. DWH-ийн огнооны хэмжигдэхүүн';

CREATE TABLE system.system_parameters (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    param_code     system.code_key NOT NULL,
    param_group    VARCHAR(50)     NOT NULL,
    param_value    TEXT            NOT NULL,
    value_type     VARCHAR(20)     NOT NULL DEFAULT 'STRING'
                   CHECK (value_type IN ('STRING', 'NUMBER', 'BOOLEAN', 'DATE', 'JSON')),
    description    VARCHAR(300),
    is_encrypted   BOOLEAN NOT NULL DEFAULT FALSE,
    is_editable    BOOLEAN NOT NULL DEFAULT TRUE,
    requires_restart BOOLEAN NOT NULL DEFAULT FALSE,
    valid_from     DATE   NOT NULL DEFAULT CURRENT_DATE,
    valid_to       DATE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by     BIGINT,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by     BIGINT,
    version        INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT uq_system_param UNIQUE (param_code, valid_from),
    CONSTRAINT ck_system_param_period CHECK (valid_to IS NULL OR valid_to >= valid_from)
);
COMMENT ON TABLE system.system_parameters IS
    'Ажлын өдрийн огноо, торгуулийн хөнгөлөлтийн хоног, DPD босго, дугаарлалтын формат гэх мэт';

CREATE TABLE system.number_sequences (
    id              SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sequence_code   system.code_key NOT NULL,
    prefix          VARCHAR(10) NOT NULL DEFAULT '',
    suffix          VARCHAR(10) NOT NULL DEFAULT '',
    current_value   BIGINT   NOT NULL DEFAULT 0 CHECK (current_value >= 0),
    increment_by    INTEGER  NOT NULL DEFAULT 1 CHECK (increment_by > 0),
    padding_length  SMALLINT NOT NULL DEFAULT 8 CHECK (padding_length BETWEEN 1 AND 20),
    include_year    BOOLEAN  NOT NULL DEFAULT FALSE,
    include_branch  BOOLEAN  NOT NULL DEFAULT FALSE,
    reset_period    VARCHAR(10) NOT NULL DEFAULT 'NEVER'
                    CHECK (reset_period IN ('NEVER', 'YEARLY', 'MONTHLY', 'DAILY')),
    last_reset_at   DATE,
    description     VARCHAR(200),
    CONSTRAINT uq_number_sequences_code UNIQUE (sequence_code)
);
COMMENT ON TABLE system.number_sequences IS
    'Бизнес дугаарлалт (LN-2026-00000123). system.fn_next_number() атомаар олгоно';

-- ---------------------------------------------------------------------
-- Баримт бичиг
-- ШИЙДВЭР: файлын БИНАРЫГ DB-д хадгалахгүй.
--   Учир: WAL хэт томорно, backup/restore удаана, replication ачаална,
--   CDN/stream боломжгүй. → S3/MinIO-д хадгалж, DB-д зөвхөн metadata +
--   storage_key + checksum. Checksum нь бүрэн бүтэн байдлыг батална.
-- ---------------------------------------------------------------------
CREATE TABLE system.document_types (
    id                SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    type_code         system.code_key NOT NULL,
    type_name         VARCHAR(150)    NOT NULL,
    category          VARCHAR(30)     NOT NULL
                      CHECK (category IN ('KYC', 'LOAN', 'COLLATERAL', 'CONTRACT', 'FINANCIAL', 'LEGAL', 'INTERNAL')),
    retention_years   SMALLINT NOT NULL DEFAULT 10 CHECK (retention_years > 0),
    is_mandatory      BOOLEAN  NOT NULL DEFAULT FALSE,
    sensitivity       system.data_sensitivity NOT NULL DEFAULT 'CONFIDENTIAL',
    allowed_mime_types TEXT[]  NOT NULL DEFAULT ARRAY['application/pdf', 'image/jpeg', 'image/png'],
    max_size_mb       SMALLINT NOT NULL DEFAULT 20 CHECK (max_size_mb > 0),
    is_active         BOOLEAN  NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_document_types_code UNIQUE (type_code)
);
COMMENT ON COLUMN system.document_types.retention_years IS 'Хадгалах хугацаа — архивын бодлого, хууль эрх зүйн шаардлага';

CREATE TABLE system.documents (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    document_no       VARCHAR(30) NOT NULL,
    document_type_id  SMALLINT    NOT NULL REFERENCES system.document_types (id),
    owner_entity_type system.entity_type NOT NULL,
    owner_entity_id   UUID        NOT NULL,
    customer_id       UUID        REFERENCES customer.customers (id),

    file_name         VARCHAR(300) NOT NULL,
    mime_type         VARCHAR(100) NOT NULL,
    file_size_bytes   BIGINT       NOT NULL CHECK (file_size_bytes > 0),
    storage_provider  VARCHAR(20)  NOT NULL DEFAULT 'S3'
                      CHECK (storage_provider IN ('S3', 'MINIO', 'AZURE_BLOB', 'GCS', 'NFS')),
    storage_key       VARCHAR(500) NOT NULL,              -- обьект хадгалалтын зам
    storage_bucket    VARCHAR(100),
    checksum_sha256   CHAR(64)     NOT NULL,              -- бүрэн бүтэн байдал
    is_encrypted      BOOLEAN      NOT NULL DEFAULT TRUE,
    encryption_key_id VARCHAR(100),                       -- KMS түлхүүрийн лавлагаа

    current_version   SMALLINT     NOT NULL DEFAULT 1 CHECK (current_version > 0),
    status            VARCHAR(20)  NOT NULL DEFAULT 'ACTIVE'
                      CHECK (status IN ('UPLOADING', 'ACTIVE', 'ARCHIVED', 'DELETED', 'QUARANTINED')),
    virus_scan_status VARCHAR(20)  NOT NULL DEFAULT 'PENDING'
                      CHECK (virus_scan_status IN ('PENDING', 'CLEAN', 'INFECTED', 'SKIPPED')),
    retention_until   DATE,
    uploaded_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    uploaded_by       BIGINT,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    created_by        BIGINT,
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_by        BIGINT,
    version           INTEGER      NOT NULL DEFAULT 1,
    deleted_at        TIMESTAMPTZ,
    CONSTRAINT uq_documents_no UNIQUE (document_no),
    CONSTRAINT uq_documents_storage_key UNIQUE (storage_provider, storage_key),
    CONSTRAINT ck_documents_checksum CHECK (checksum_sha256 ~ '^[a-f0-9]{64}$')
);
COMMENT ON TABLE system.documents IS
    'Баримтын МЕТА мэдээлэл. Файл нь обьект хадгалалтад (S3/MinIO) — DB-д BYTEA-гээр хадгалахгүй';

CREATE TABLE system.document_versions (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    document_id      UUID     NOT NULL REFERENCES system.documents (id) ON DELETE CASCADE,
    version_no       SMALLINT NOT NULL CHECK (version_no > 0),
    storage_key      VARCHAR(500) NOT NULL,
    file_size_bytes  BIGINT   NOT NULL CHECK (file_size_bytes > 0),
    checksum_sha256  CHAR(64) NOT NULL,
    change_reason    VARCHAR(300),
    replaced_at      TIMESTAMPTZ,
    uploaded_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    uploaded_by      BIGINT,
    CONSTRAINT uq_document_version UNIQUE (document_id, version_no),
    CONSTRAINT ck_doc_version_checksum CHECK (checksum_sha256 ~ '^[a-f0-9]{64}$')
);

-- ---------------------------------------------------------------------
-- Мэдэгдэл
-- ---------------------------------------------------------------------
CREATE TABLE system.notification_templates (
    id             SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    template_code  system.code_key NOT NULL,
    template_name  VARCHAR(150)    NOT NULL,
    channel        VARCHAR(20)     NOT NULL
                   CHECK (channel IN ('SMS', 'EMAIL', 'PUSH', 'IN_APP', 'LETTER')),
    subject_mn     VARCHAR(300),
    body_mn        TEXT NOT NULL,
    subject_en     VARCHAR(300),
    body_en        TEXT,
    variables      TEXT[],                                -- {customer_name, amount, due_date}
    event_trigger  VARCHAR(50),
    is_active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by     BIGINT,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by     BIGINT,
    version        INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT uq_notification_templates UNIQUE (template_code, channel)
);

CREATE TABLE system.notifications (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    template_id    SMALLINT REFERENCES system.notification_templates (id),
    customer_id    UUID     REFERENCES customer.customers (id),
    user_id        BIGINT   REFERENCES core.users (id),
    channel        VARCHAR(20) NOT NULL
                   CHECK (channel IN ('SMS', 'EMAIL', 'PUSH', 'IN_APP', 'LETTER')),
    recipient      VARCHAR(300) NOT NULL,
    subject        VARCHAR(300),
    body           TEXT NOT NULL,
    entity_type    system.entity_type,
    entity_id      UUID,
    priority       VARCHAR(10) NOT NULL DEFAULT 'NORMAL'
                   CHECK (priority IN ('LOW', 'NORMAL', 'HIGH', 'URGENT')),
    status         VARCHAR(20) NOT NULL DEFAULT 'QUEUED'
                   CHECK (status IN ('QUEUED', 'SENDING', 'SENT', 'DELIVERED', 'READ', 'FAILED', 'CANCELLED')),
    scheduled_at   TIMESTAMPTZ,
    sent_at        TIMESTAMPTZ,
    delivered_at   TIMESTAMPTZ,
    read_at        TIMESTAMPTZ,
    retry_count    SMALLINT NOT NULL DEFAULT 0 CHECK (retry_count >= 0),
    error_message  VARCHAR(500),
    provider_ref   VARCHAR(100),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by     BIGINT,
    CONSTRAINT ck_notification_target CHECK (customer_id IS NOT NULL OR user_id IS NOT NULL),
    CONSTRAINT ck_notification_failed CHECK (status <> 'FAILED' OR error_message IS NOT NULL)
);

-- ---------------------------------------------------------------------
-- Аудит (партицлагдсан — хамгийн хурдан ургадаг хүснэгт)
-- ---------------------------------------------------------------------
CREATE SEQUENCE system.seq_audit_logs;
CREATE TABLE system.audit_logs (
    id             BIGINT NOT NULL DEFAULT nextval('system.seq_audit_logs'),
    changed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    schema_name    VARCHAR(50)  NOT NULL,
    table_name     VARCHAR(100) NOT NULL,
    record_pk      TEXT         NOT NULL,                 -- UUID эсвэл BIGINT — текстээр нэгтгэв
    action         system.audit_action NOT NULL,
    user_id        BIGINT,
    username       VARCHAR(60),
    ip_address     INET,
    session_id     TEXT,
    application    VARCHAR(50),
    reason         VARCHAR(500),
    old_values     JSONB,
    new_values     JSONB,
    changed_fields TEXT[],
    PRIMARY KEY (id, changed_at)
) PARTITION BY RANGE (changed_at);

CREATE TABLE system.audit_logs_2025 PARTITION OF system.audit_logs
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE system.audit_logs_2026 PARTITION OF system.audit_logs
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE system.audit_logs_2027 PARTITION OF system.audit_logs
    FOR VALUES FROM ('2027-01-01') TO ('2028-01-01');
CREATE TABLE system.audit_logs_default PARTITION OF system.audit_logs DEFAULT;

COMMENT ON TABLE system.audit_logs IS
    'Хэн, юуг, хэзээ, хаанаас, яагаад өөрчилсөн + өмнөх/дараах утга. '
    'Trigger автоматаар бичнэ; INSERT-ээс өөр үйлдэл хийхийг эрхээр хаана';
COMMENT ON COLUMN system.audit_logs.changed_fields IS 'Зөвхөн өөрчлөгдсөн баганын нэрс — хайлт хурдан';

CREATE SEQUENCE system.seq_integration_logs;
CREATE TABLE system.integration_logs (
    id              BIGINT NOT NULL DEFAULT nextval('system.seq_integration_logs'),
    logged_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    integration_code VARCHAR(50) NOT NULL,                -- BUREAU, KHUR, BANK_API, SMS_GATEWAY
    direction       VARCHAR(10) NOT NULL CHECK (direction IN ('INBOUND', 'OUTBOUND')),
    endpoint        VARCHAR(300),
    http_method     VARCHAR(10),
    request_ref     VARCHAR(100),
    entity_type     system.entity_type,
    entity_id       UUID,
    request_payload JSONB,
    response_payload JSONB,
    status_code     INTEGER,
    is_success      BOOLEAN NOT NULL DEFAULT FALSE,
    duration_ms     INTEGER CHECK (duration_ms >= 0),
    error_message   VARCHAR(1000),
    retry_count     SMALLINT NOT NULL DEFAULT 0,
    created_by      BIGINT,
    PRIMARY KEY (id, logged_at)
) PARTITION BY RANGE (logged_at);

CREATE TABLE system.integration_logs_2025 PARTITION OF system.integration_logs
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE system.integration_logs_2026 PARTITION OF system.integration_logs
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE system.integration_logs_2027 PARTITION OF system.integration_logs
    FOR VALUES FROM ('2027-01-01') TO ('2028-01-01');
CREATE TABLE system.integration_logs_default PARTITION OF system.integration_logs DEFAULT;

COMMENT ON COLUMN system.integration_logs.request_payload IS
    'Хувийн мэдээллийг маскалсан хэлбэрээр хадгална (нууц үг, картын дугаар ХЭЗЭЭ Ч биш)';

-- Outbox — DWH/ETL, event-driven интеграцид (CDC-ийн найдвартай хувилбар)
CREATE SEQUENCE system.seq_outbox_events;
CREATE TABLE system.outbox_events (
    id              BIGINT NOT NULL DEFAULT nextval('system.seq_outbox_events'),
    occurred_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    event_type      VARCHAR(80) NOT NULL,                 -- loan.disbursed, customer.created
    aggregate_type  system.entity_type NOT NULL,
    aggregate_id    TEXT NOT NULL,
    payload         JSONB NOT NULL,
    published       BOOLEAN NOT NULL DEFAULT FALSE,
    published_at    TIMESTAMPTZ,
    attempt_count   SMALLINT NOT NULL DEFAULT 0,
    last_error      VARCHAR(500),
    PRIMARY KEY (id, occurred_at)
) PARTITION BY RANGE (occurred_at);

CREATE TABLE system.outbox_events_2025 PARTITION OF system.outbox_events
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE system.outbox_events_2026 PARTITION OF system.outbox_events
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE system.outbox_events_2027 PARTITION OF system.outbox_events
    FOR VALUES FROM ('2027-01-01') TO ('2028-01-01');
CREATE TABLE system.outbox_events_default PARTITION OF system.outbox_events DEFAULT;

COMMENT ON TABLE system.outbox_events IS
    'Transactional outbox — үйл явдлыг гүйлгээтэй НЭГ commit-д бичиж, ETL/Kafka руу найдвартай дамжуулна (§13)';

-- ---------------------------------------------------------------------
-- Баримтын FK-үүд (documents хүснэгт үүссэний дараа)
-- ---------------------------------------------------------------------
ALTER TABLE kyc.kyc_documents
    ADD CONSTRAINT fk_kyc_documents_document FOREIGN KEY (document_id) REFERENCES system.documents (id);
ALTER TABLE loan.loan_application_documents
    ADD CONSTRAINT fk_loan_app_doc_document FOREIGN KEY (document_id) REFERENCES system.documents (id);
ALTER TABLE loan.loan_contracts
    ADD CONSTRAINT fk_loan_contract_document FOREIGN KEY (document_id) REFERENCES system.documents (id);
ALTER TABLE collateral.collateral_documents
    ADD CONSTRAINT fk_collateral_doc_document FOREIGN KEY (document_id) REFERENCES system.documents (id);
ALTER TABLE collateral.collateral_valuations
    ADD CONSTRAINT fk_collateral_val_document FOREIGN KEY (report_document_id) REFERENCES system.documents (id);
ALTER TABLE collateral.loan_guarantors
    ADD CONSTRAINT fk_loan_guarantor_document FOREIGN KEY (contract_document_id) REFERENCES system.documents (id);
ALTER TABLE deposit.deposit_contracts
    ADD CONSTRAINT fk_deposit_contract_document FOREIGN KEY (document_id) REFERENCES system.documents (id);
ALTER TABLE collection.collection_actions
    ADD CONSTRAINT fk_collection_action_document FOREIGN KEY (attachment_document_id) REFERENCES system.documents (id);

ALTER TABLE system.documents
    ADD CONSTRAINT fk_documents_uploaded_by FOREIGN KEY (uploaded_by) REFERENCES core.users (id);
ALTER TABLE system.document_versions
    ADD CONSTRAINT fk_document_versions_uploaded_by FOREIGN KEY (uploaded_by) REFERENCES core.users (id);

-- Валютын FK-үүд (currencies хүснэгт үүссэний дараа — гол мөнгөн хүснэгтүүдэд)
ALTER TABLE account.accounts
    ADD CONSTRAINT fk_accounts_currency FOREIGN KEY (currency_code) REFERENCES system.currencies (code);
ALTER TABLE loan.loans
    ADD CONSTRAINT fk_loans_currency FOREIGN KEY (currency_code) REFERENCES system.currencies (code);
ALTER TABLE deposit.deposit_accounts
    ADD CONSTRAINT fk_deposit_currency FOREIGN KEY (currency_code) REFERENCES system.currencies (code);
ALTER TABLE product.products
    ADD CONSTRAINT fk_products_currency FOREIGN KEY (currency_code) REFERENCES system.currencies (code);

-- ---------------------------------------------------------------------
-- created_by / updated_by → core.users FK (бүх хүснэгтэд нэг дор)
-- Партицын хүүхэд хүснэгтийг алгасна (эцэгт нь тавигдана).
-- ---------------------------------------------------------------------
DO $$
DECLARE
    r        RECORD;
    col_name TEXT;
BEGIN
    FOREACH col_name IN ARRAY ARRAY['created_by', 'updated_by'] LOOP
        FOR r IN
            SELECT n.nspname AS sch, c.relname AS tbl
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = col_name
                                AND a.attnum > 0 AND NOT a.attisdropped
            WHERE c.relkind IN ('r', 'p')
              AND n.nspname IN ('system', 'core', 'customer', 'kyc', 'product', 'loan',
                                'collateral', 'account', 'deposit', 'accounting', 'risk', 'collection')
              AND NOT EXISTS (SELECT 1 FROM pg_inherits i WHERE i.inhrelid = c.oid)
            -- core.users өөрөө ч мөн адил (self-FK): эхний админ хэрэглэгчид NULL
            ORDER BY 1, 2
        LOOP
            EXECUTE format(
                'ALTER TABLE %I.%I ADD CONSTRAINT %I FOREIGN KEY (%I) REFERENCES core.users (id)',
                r.sch, r.tbl, 'fk_' || r.tbl || '_' || col_name, col_name
            );
        END LOOP;
    END LOOP;
END;
$$;

COMMENT ON SCHEMA system IS
    'Лавлах, тохиргоо, аудит, интеграц. created_by/updated_by FK-үүд энэ файлын төгсгөлд автоматаар нэмэгдэнэ';
