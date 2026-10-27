-- =====================================================================
-- 010_deposit.sql — Хадгаламж (хугацаатай, хугацаагүй, хуримтлал)
-- ---------------------------------------------------------------------
-- Хадгаламжийн данс нь account.accounts дээр СУУНА (мөнгөн хөдөлгөөн нэг
-- механизмаар явна), деposit_accounts нь зөвхөн хадгаламжийн нэмэлт
-- нөхцөлийг агуулна (1:1 өргөтгөл).
-- =====================================================================

\set ON_ERROR_STOP on

CREATE TABLE deposit.deposit_accounts (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deposit_number       VARCHAR(30) NOT NULL,
    account_id           UUID     NOT NULL REFERENCES account.accounts (id) ON DELETE RESTRICT,
    customer_id          UUID     NOT NULL REFERENCES customer.customers (id),
    product_id           BIGINT   NOT NULL REFERENCES product.products (id),
    branch_id            BIGINT   NOT NULL REFERENCES core.branches (id),
    currency_code        system.currency_code NOT NULL DEFAULT 'MNT',

    deposit_type         VARCHAR(20) NOT NULL
                         CHECK (deposit_type IN ('TERM', 'DEMAND', 'SAVINGS', 'CHILD', 'PENSION', 'ESCROW')),
    principal_amount     system.money_positive NOT NULL CHECK (principal_amount > 0),
    current_balance      system.money_positive NOT NULL DEFAULT 0,
    interest_rate        system.rate_pct NOT NULL,
    interest_method      system.interest_method NOT NULL DEFAULT 'SIMPLE',
    day_count_convention system.day_count_conv NOT NULL DEFAULT 'ACT_365',
    capitalization_period system.period_unit,             -- хүү нэмэгдүүлэх давтамж
    term_months          SMALLINT CHECK (term_months > 0),

    accrued_interest     system.money_positive NOT NULL DEFAULT 0,
    paid_interest        system.money_positive NOT NULL DEFAULT 0,
    withholding_tax_paid system.money_positive NOT NULL DEFAULT 0,

    opened_date          DATE NOT NULL,
    maturity_date        DATE,
    closed_date          DATE,
    auto_renew           BOOLEAN NOT NULL DEFAULT FALSE,
    renewal_count        SMALLINT NOT NULL DEFAULT 0 CHECK (renewal_count >= 0),
    payout_account_id    UUID REFERENCES account.accounts (id),
    is_pledged           BOOLEAN NOT NULL DEFAULT FALSE,   -- зээлийн барьцаанд тавигдсан эсэх
    status               VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
                         CHECK (status IN ('PENDING', 'ACTIVE', 'MATURED', 'CLOSED', 'BLOCKED', 'EARLY_CLOSED')),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by           BIGINT,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by           BIGINT,
    version              INTEGER NOT NULL DEFAULT 1,
    deleted_at           TIMESTAMPTZ,
    CONSTRAINT uq_deposit_number UNIQUE (deposit_number),
    CONSTRAINT uq_deposit_account UNIQUE (account_id),
    CONSTRAINT uq_deposit_id_currency UNIQUE (id, currency_code),
    CONSTRAINT fk_deposit_acc_currency FOREIGN KEY (account_id, currency_code)
        REFERENCES account.accounts (id, currency_code),
    CONSTRAINT ck_deposit_maturity CHECK (maturity_date IS NULL OR maturity_date > opened_date),
    CONSTRAINT ck_deposit_term CHECK (deposit_type <> 'TERM' OR (term_months IS NOT NULL AND maturity_date IS NOT NULL)),
    CONSTRAINT ck_deposit_closed CHECK (closed_date IS NULL OR closed_date >= opened_date)
);
COMMENT ON COLUMN deposit.deposit_accounts.is_pledged IS
    'Хадгаламж барьцаалсан зээлд ашиглагдаж байгаа бол буцаан авахыг хориглоно';

CREATE TABLE deposit.deposit_contracts (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    deposit_account_id UUID        NOT NULL REFERENCES deposit.deposit_accounts (id) ON DELETE RESTRICT,
    contract_number    VARCHAR(40) NOT NULL,
    contract_type      VARCHAR(20) NOT NULL DEFAULT 'DEPOSIT'
                       CHECK (contract_type IN ('DEPOSIT', 'RENEWAL', 'AMENDMENT', 'TERMINATION')),
    parent_contract_id UUID REFERENCES deposit.deposit_contracts (id),
    signed_date        DATE,
    effective_date     DATE NOT NULL,
    expiry_date        DATE,
    terms_snapshot     JSONB,
    document_id        UUID,
    status             VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
                       CHECK (status IN ('DRAFT', 'ACTIVE', 'RENEWED', 'TERMINATED', 'EXPIRED')),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by         BIGINT,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by         BIGINT,
    version            INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT uq_deposit_contract_no UNIQUE (contract_number),
    CONSTRAINT ck_deposit_contract_expiry CHECK (expiry_date IS NULL OR expiry_date >= effective_date)
);

-- Хадгаламжийн хүүгийн шат (дүн/хугацаагаар)
CREATE TABLE deposit.deposit_rates (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id      BIGINT   NOT NULL REFERENCES product.products (id) ON DELETE CASCADE,
    deposit_type    VARCHAR(20) NOT NULL,
    currency_code   system.currency_code NOT NULL DEFAULT 'MNT',
    min_amount      system.money_positive NOT NULL DEFAULT 0,
    max_amount      system.money_positive,
    min_term_months SMALLINT,
    max_term_months SMALLINT,
    annual_rate     system.rate_pct NOT NULL,
    early_withdrawal_rate system.rate_pct NOT NULL DEFAULT 0,
    valid_from      DATE NOT NULL,
    valid_to        DATE,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by      BIGINT,
    CONSTRAINT ck_deposit_rate_amount CHECK (max_amount IS NULL OR max_amount >= min_amount),
    CONSTRAINT ck_deposit_rate_term CHECK (max_term_months IS NULL OR min_term_months IS NULL
                                           OR max_term_months >= min_term_months),
    CONSTRAINT ck_deposit_rate_period CHECK (valid_to IS NULL OR valid_to >= valid_from)
);
COMMENT ON COLUMN deposit.deposit_rates.early_withdrawal_rate IS
    'Хугацаанаас өмнө татахад хэрэглэх буурсан хүү — гэрээнд заасан';

CREATE TABLE deposit.deposit_interest_accruals (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    deposit_account_id UUID   NOT NULL REFERENCES deposit.deposit_accounts (id) ON DELETE RESTRICT,
    accrual_date       DATE   NOT NULL,
    balance_base       system.money_positive NOT NULL,
    annual_rate        system.rate_pct NOT NULL,
    day_count          SMALLINT NOT NULL DEFAULT 1 CHECK (day_count > 0),
    interest_amount    system.money_positive NOT NULL,
    withholding_tax    system.money_positive NOT NULL DEFAULT 0,
    is_capitalized     BOOLEAN NOT NULL DEFAULT FALSE,
    is_posted          BOOLEAN NOT NULL DEFAULT FALSE,
    journal_entry_id   BIGINT,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_deposit_accrual UNIQUE (deposit_account_id, accrual_date)
);

CREATE TABLE deposit.deposit_transactions (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    deposit_account_id UUID     NOT NULL REFERENCES deposit.deposit_accounts (id) ON DELETE RESTRICT,
    currency_code      system.currency_code NOT NULL,
    transaction_id     BIGINT,                            -- account.transactions (сул холбоос — partition)
    transaction_value_date DATE,
    txn_type           VARCHAR(20) NOT NULL
                       CHECK (txn_type IN ('DEPOSIT', 'WITHDRAWAL', 'INTEREST_PAYOUT', 'CAPITALIZATION',
                                           'TAX_WITHHOLDING', 'PENALTY', 'CLOSURE')),
    amount             system.money_positive NOT NULL CHECK (amount > 0),
    balance_after      system.money_positive NOT NULL,
    value_date         DATE NOT NULL,
    description        VARCHAR(300),
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by         BIGINT,
    CONSTRAINT fk_deposit_txn_currency FOREIGN KEY (deposit_account_id, currency_code)
        REFERENCES deposit.deposit_accounts (id, currency_code)
);

CREATE TABLE deposit.deposit_early_withdrawals (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    deposit_account_id UUID NOT NULL REFERENCES deposit.deposit_accounts (id) ON DELETE RESTRICT,
    request_date       DATE NOT NULL,
    withdrawal_amount  system.money_positive NOT NULL CHECK (withdrawal_amount > 0),
    original_rate      system.rate_pct NOT NULL,
    penalty_rate       system.rate_pct NOT NULL,
    interest_forfeited system.money_positive NOT NULL DEFAULT 0,
    penalty_amount     system.money_positive NOT NULL DEFAULT 0,
    net_payout         system.money_positive NOT NULL,
    reason             VARCHAR(300),
    approved_by        BIGINT REFERENCES core.employees (id),
    status             VARCHAR(20) NOT NULL DEFAULT 'PENDING'
                       CHECK (status IN ('PENDING', 'APPROVED', 'EXECUTED', 'REJECTED')),
    executed_at        TIMESTAMPTZ,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by         BIGINT,
    CONSTRAINT ck_deposit_early_approved CHECK (status NOT IN ('APPROVED', 'EXECUTED') OR approved_by IS NOT NULL)
);
COMMENT ON TABLE deposit.deposit_early_withdrawals IS
    'Хугацаанаас өмнө татах хүсэлт — алдагдсан хүү, торгуулийг тооцоолж баримтжуулна';
