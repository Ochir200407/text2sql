-- =====================================================================
-- 009_transaction.sql — Гүйлгээ, суваг, төлбөрийн даалгавар
-- ---------------------------------------------------------------------
-- Гүйлгээ бол системийн ХАМГИЙН ИХ мөртэй хүснэгт (10 сая+). Тиймээс:
--   * value_date-ээр RANGE partition (жилээр; сар болгон нарийсгаж болно)
--   * PK (id, value_date) — partition key заавал орно
--   * IDENTITY биш sequence DEFAULT — partitioned хүснэгтэд найдвартай
--   * transaction_no-ийн глобал давхардлыг sequence өөрөө хаана
--     (UNIQUE нь partition key-гүйгээр глобалаар тавигдахгүй — доор тайлбар)
--
-- Нэг гүйлгээ = НЭГ ХӨЛ (нэг данс, D эсвэл C). Шилжүүлэг нь ижил
-- transfer_group_id-тай хоёр мөр. Нягтлангийн давхар бичилт нь
-- accounting.journal_entries — тусдаа давхарга (§7).
-- =====================================================================

\set ON_ERROR_STOP on

CREATE TABLE account.transaction_types (
    id                SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    type_code         system.code_key NOT NULL,
    type_name         VARCHAR(150)    NOT NULL,
    direction         system.dr_cr    NOT NULL,
    category          VARCHAR(30)     NOT NULL
                      CHECK (category IN ('DEPOSIT', 'WITHDRAWAL', 'TRANSFER', 'LOAN_DISBURSEMENT',
                                          'LOAN_REPAYMENT', 'FEE', 'INTEREST', 'PENALTY', 'REVERSAL',
                                          'ADJUSTMENT', 'FX', 'WRITEOFF')),
    affects_balance   BOOLEAN NOT NULL DEFAULT TRUE,
    requires_approval BOOLEAN NOT NULL DEFAULT FALSE,
    approval_threshold system.money_positive,
    accounting_rule_code system.code_key,                 -- 011: ямар журнал үүсгэх
    is_active         BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_transaction_types_code UNIQUE (type_code)
);

CREATE TABLE account.transaction_channels (
    id             SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    channel_code   system.code_key NOT NULL,
    channel_name   VARCHAR(100)    NOT NULL,
    is_digital     BOOLEAN NOT NULL DEFAULT FALSE,
    requires_otp   BOOLEAN NOT NULL DEFAULT FALSE,
    daily_limit    system.money_positive,
    per_txn_limit  system.money_positive,
    is_active      BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_transaction_channels_code UNIQUE (channel_code)
);
COMMENT ON TABLE account.transaction_channels IS 'Салбар, мобайл, интернэт банк, ATM, POS, партнёр, API';

CREATE TABLE account.transaction_statuses (
    id            SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    status_code   system.code_key NOT NULL,
    status_name   VARCHAR(100)    NOT NULL,
    is_final      BOOLEAN NOT NULL DEFAULT FALSE,
    is_successful BOOLEAN NOT NULL DEFAULT FALSE,
    affects_balance BOOLEAN NOT NULL DEFAULT FALSE,
    sort_order    SMALLINT NOT NULL DEFAULT 0,
    CONSTRAINT uq_transaction_statuses_code UNIQUE (status_code)
);

CREATE TABLE account.payment_methods (
    id            SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    method_code   system.code_key NOT NULL,
    method_name   VARCHAR(100)    NOT NULL,
    provider      VARCHAR(100),
    settlement_days SMALLINT NOT NULL DEFAULT 0 CHECK (settlement_days >= 0),
    fee_percent   system.percent_share NOT NULL DEFAULT 0,
    fee_fixed     system.money_positive NOT NULL DEFAULT 0,
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_payment_methods_code UNIQUE (method_code)
);

-- ---------------------------------------------------------------------
-- Гүйлгээ (партицлагдсан)
-- ---------------------------------------------------------------------
CREATE SEQUENCE account.seq_transactions;
CREATE TABLE account.transactions (
    id                  BIGINT NOT NULL DEFAULT nextval('account.seq_transactions'),
    transaction_no      VARCHAR(30) NOT NULL,
    account_id          UUID     NOT NULL REFERENCES account.accounts (id),
    currency_code       system.currency_code NOT NULL,
    transaction_type_id SMALLINT NOT NULL REFERENCES account.transaction_types (id),
    channel_id          SMALLINT NOT NULL REFERENCES account.transaction_channels (id),
    status_id           SMALLINT NOT NULL REFERENCES account.transaction_statuses (id),
    branch_id           BIGINT   REFERENCES core.branches (id),
    customer_id         UUID     REFERENCES customer.customers (id),

    direction           system.dr_cr NOT NULL,
    amount              system.money_positive NOT NULL CHECK (amount > 0),
    fee_amount          system.money_positive NOT NULL DEFAULT 0,
    balance_before      system.money_amount,
    balance_after       system.money_amount,

    -- Олон валют: анхны валют + суурь валют (MNT) руу хөрвүүлсэн дүн
    base_currency_code  system.currency_code NOT NULL DEFAULT 'MNT',
    base_amount         system.money_positive,
    fx_rate             system.fx_rate,

    transaction_date    DATE        NOT NULL,             -- бүртгэсэн огноо
    value_date          DATE        NOT NULL,             -- хүчинтэй огноо (partition key)
    posted_at           TIMESTAMPTZ,

    -- Эх сурвалж (полиморф, сул холбоос — олон домэйнээс үүсдэг)
    source_entity_type  system.entity_type,
    source_entity_id    UUID,
    transfer_group_id   UUID,                             -- шилжүүлгийн хос мөрийг холбоно
    counterparty_account VARCHAR(40),
    counterparty_name   VARCHAR(300),
    counterparty_bank   VARCHAR(100),

    journal_entry_id    BIGINT,                           -- accounting.journal_entries (сул холбоос)
    reversed_by_id      BIGINT,                           -- сторно гүйлгээний id
    reversal_of_id      BIGINT,
    is_reversal         BOOLEAN NOT NULL DEFAULT FALSE,
    reversal_reason     VARCHAR(300),

    description         VARCHAR(500),
    external_ref        VARCHAR(100),                     -- сувгийн лавлагаа (идемпотент)
    device_id           VARCHAR(100),
    ip_address          INET,
    initiated_by        BIGINT,
    approved_by         BIGINT,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by          BIGINT,

    PRIMARY KEY (id, value_date),
    CONSTRAINT uq_transactions_no UNIQUE (transaction_no, value_date),
    CONSTRAINT fk_transactions_currency FOREIGN KEY (account_id, currency_code)
        REFERENCES account.accounts (id, currency_code),
    CONSTRAINT ck_transactions_value_date CHECK (value_date >= transaction_date - 30),
    CONSTRAINT ck_transactions_fx CHECK (
        currency_code = base_currency_code OR (fx_rate IS NOT NULL AND base_amount IS NOT NULL)
    ),
    CONSTRAINT ck_transactions_reversal CHECK (NOT is_reversal OR reversal_of_id IS NOT NULL)
) PARTITION BY RANGE (value_date);

CREATE TABLE account.transactions_2025 PARTITION OF account.transactions
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE account.transactions_2026 PARTITION OF account.transactions
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE account.transactions_2027 PARTITION OF account.transactions
    FOR VALUES FROM ('2027-01-01') TO ('2028-01-01');
CREATE TABLE account.transactions_default PARTITION OF account.transactions DEFAULT;

COMMENT ON TABLE account.transactions IS
    'Мөнгөн хөдөлгөөний бүртгэл. Нэг мөр = нэг данс, нэг тал (D/C). '
    'value_date-ээр жилийн partition — хуучин жилийг detach хийж архивлана';
COMMENT ON CONSTRAINT uq_transactions_no ON account.transactions IS
    'Partitioned хүснэгтэд UNIQUE нь partition key-г агуулах ёстой. Глобал давхардлаас '
    'sequence-д суурилсан дугаарлалт хамгаална (system.fn_next_number)';
COMMENT ON COLUMN account.transactions.external_ref IS
    'Сувгийн давхардлаас хамгаалах түлхүүр — идемпотент боловсруулалт (partial unique index 015-д)';
COMMENT ON COLUMN account.transactions.base_amount IS
    'MNT руу хөрвүүлсэн дүн — олон валютын тайланг нэг дор нэгтгэхэд';

-- ---------------------------------------------------------------------
-- Төлбөрийн даалгавар (гүйлгээ болохоос өмнөх шат: батлах, боловсруулах)
-- ---------------------------------------------------------------------
CREATE TABLE account.payment_orders (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_no            VARCHAR(30) NOT NULL,
    payment_method_id   SMALLINT NOT NULL REFERENCES account.payment_methods (id),
    channel_id          SMALLINT NOT NULL REFERENCES account.transaction_channels (id),
    debit_account_id    UUID     REFERENCES account.accounts (id),
    credit_account_id   UUID     REFERENCES account.accounts (id),
    customer_id         UUID     REFERENCES customer.customers (id),
    branch_id           BIGINT   REFERENCES core.branches (id),

    amount              system.money_positive NOT NULL CHECK (amount > 0),
    currency_code       system.currency_code NOT NULL DEFAULT 'MNT',
    fee_amount          system.money_positive NOT NULL DEFAULT 0,

    beneficiary_name    VARCHAR(300),
    beneficiary_account VARCHAR(40),
    beneficiary_bank    VARCHAR(100),
    purpose             VARCHAR(300),

    requested_date      DATE   NOT NULL,
    value_date          DATE   NOT NULL,
    executed_at         TIMESTAMPTZ,
    status              VARCHAR(20) NOT NULL DEFAULT 'DRAFT'
                        CHECK (status IN ('DRAFT', 'PENDING_APPROVAL', 'APPROVED', 'PROCESSING',
                                          'EXECUTED', 'REJECTED', 'FAILED', 'CANCELLED')),
    failure_reason      VARCHAR(300),
    external_ref        VARCHAR(100),
    requested_by        BIGINT,
    approved_by         BIGINT,
    approved_at         TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by          BIGINT,
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by          BIGINT,
    version             INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT uq_payment_orders_no UNIQUE (order_no),
    CONSTRAINT ck_payment_order_accounts CHECK (debit_account_id IS NOT NULL OR credit_account_id IS NOT NULL),
    CONSTRAINT ck_payment_order_diff CHECK (debit_account_id IS NULL OR credit_account_id IS NULL
                                            OR debit_account_id <> credit_account_id),
    CONSTRAINT ck_payment_order_approved CHECK (status NOT IN ('APPROVED', 'EXECUTED') OR approved_by IS NOT NULL),
    CONSTRAINT ck_payment_order_failed CHECK (status NOT IN ('FAILED', 'REJECTED') OR failure_reason IS NOT NULL)
);
COMMENT ON TABLE account.payment_orders IS
    'Даалгавар → батлах → гүйцэтгэх. Гүйцэтгэсний дараа account.transactions мөр(үүд) үүснэ';
