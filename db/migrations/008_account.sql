-- =====================================================================
-- 008_account.sql — Данс, үлдэгдэл, блоклолт
-- ---------------------------------------------------------------------
-- Данс нь БҮХ мөнгөн хөдөлгөөний тулгуур: зээл олгох, төлбөр хүлээн авах,
-- хадгаламж, шимтгэл. Дотоод (харилцагчийн) ба техникийн (suspense, cash,
-- settlement) дансыг НЭГ хүснэгтэд account_types-аар ялгана.
--
-- Үлдэгдэл: current_balance нь denormalized. Жинхэнэ эх сурвалж нь
-- account.transactions; өдөр бүр account_balances-д агшинг хадгална
-- (тулгалт, хуулга, DWH).
-- =====================================================================

\set ON_ERROR_STOP on

CREATE TABLE account.account_types (
    id                 SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    type_code          system.code_key NOT NULL,
    type_name          VARCHAR(150)    NOT NULL,
    account_class      VARCHAR(20)     NOT NULL
                       CHECK (account_class IN ('CUSTOMER', 'INTERNAL', 'SUSPENSE', 'CASH', 'SETTLEMENT', 'NOSTRO')),
    normal_balance     system.balance_side NOT NULL,
    gl_account_code    VARCHAR(20),                       -- 011-д FK
    allows_negative    BOOLEAN NOT NULL DEFAULT FALSE,    -- овердрафт зөвшөөрөх эсэх
    allows_interest    BOOLEAN NOT NULL DEFAULT FALSE,
    is_customer_facing BOOLEAN NOT NULL DEFAULT TRUE,
    is_active          BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_account_types_code UNIQUE (type_code)
);
COMMENT ON COLUMN account.account_types.allows_negative IS
    'Сөрөг үлдэгдэл зөвшөөрөх эсэх — trigger энэ талбарыг шалгаж хориглоно (§21)';

CREATE TABLE account.accounts (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_number     VARCHAR(30) NOT NULL,
    account_type_id    SMALLINT NOT NULL REFERENCES account.account_types (id),
    customer_id        UUID     REFERENCES customer.customers (id),   -- дотоод дансанд NULL
    branch_id          BIGINT   NOT NULL REFERENCES core.branches (id),
    product_id         BIGINT   REFERENCES product.products (id),
    currency_code      system.currency_code NOT NULL DEFAULT 'MNT',
    account_name       VARCHAR(300) NOT NULL,

    current_balance    system.money_amount   NOT NULL DEFAULT 0,
    available_balance  system.money_amount   NOT NULL DEFAULT 0,
    hold_amount        system.money_positive NOT NULL DEFAULT 0,
    overdraft_limit    system.money_positive NOT NULL DEFAULT 0,
    last_transaction_at TIMESTAMPTZ,

    status             VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
                       CHECK (status IN ('PENDING', 'ACTIVE', 'DORMANT', 'BLOCKED', 'FROZEN', 'CLOSED')),
    is_dormant         BOOLEAN NOT NULL DEFAULT FALSE,
    block_reason       VARCHAR(300),
    opened_date        DATE   NOT NULL,
    closed_date        DATE,
    dormant_since      DATE,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by         BIGINT,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by         BIGINT,
    version            INTEGER NOT NULL DEFAULT 1,
    deleted_at         TIMESTAMPTZ,
    CONSTRAINT uq_accounts_number UNIQUE (account_number),
    CONSTRAINT uq_accounts_id_currency UNIQUE (id, currency_code),
    CONSTRAINT ck_accounts_closed CHECK (closed_date IS NULL OR closed_date >= opened_date),
    CONSTRAINT ck_accounts_closed_zero CHECK (status <> 'CLOSED' OR current_balance = 0),
    CONSTRAINT ck_accounts_blocked_reason CHECK (status NOT IN ('BLOCKED', 'FROZEN') OR block_reason IS NOT NULL),
    CONSTRAINT ck_accounts_hold CHECK (hold_amount >= 0)
);
COMMENT ON CONSTRAINT ck_accounts_closed_zero ON account.accounts IS
    'Тэг биш үлдэгдэлтэй данс хаагдахгүй — санхүүгийн үндсэн дүрэм';
COMMENT ON COLUMN account.accounts.available_balance IS
    'current_balance − hold_amount + overdraft_limit. Trigger-ээр тооцоологдоно';

CREATE TABLE account.account_holds (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id     UUID     NOT NULL REFERENCES account.accounts (id) ON DELETE RESTRICT,
    currency_code  system.currency_code NOT NULL,
    amount         system.money_positive NOT NULL CHECK (amount > 0),
    hold_type      VARCHAR(20) NOT NULL
                   CHECK (hold_type IN ('LOAN_REPAYMENT', 'COURT_ORDER', 'FRAUD_INVESTIGATION',
                                        'PENDING_TRANSACTION', 'COLLATERAL', 'TAX_AUTHORITY')),
    reference_type system.entity_type,
    reference_id   UUID,
    reason         VARCHAR(300) NOT NULL,
    placed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    placed_by      BIGINT,
    expires_at     TIMESTAMPTZ,
    released_at    TIMESTAMPTZ,
    released_by    BIGINT,
    status         VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
                   CHECK (status IN ('ACTIVE', 'RELEASED', 'EXPIRED', 'EXECUTED')),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT fk_acc_hold_currency FOREIGN KEY (account_id, currency_code)
        REFERENCES account.accounts (id, currency_code),
    CONSTRAINT ck_acc_hold_released CHECK (status <> 'RELEASED' OR released_at IS NOT NULL)
);
COMMENT ON TABLE account.account_holds IS 'Блоклосон дүн — шүүхийн шийдвэр, зээлийн суутгал, залилангийн шалгалт';

-- Өдрийн эцсийн үлдэгдлийн агшин (хуулга, тулгалт, DWH) — partition
CREATE SEQUENCE account.seq_account_balances;
CREATE TABLE account.account_balances (
    id                BIGINT NOT NULL DEFAULT nextval('account.seq_account_balances'),
    account_id        UUID   NOT NULL REFERENCES account.accounts (id) ON DELETE RESTRICT,
    balance_date      DATE   NOT NULL,
    opening_balance   system.money_amount NOT NULL,
    total_debit       system.money_positive NOT NULL DEFAULT 0,
    total_credit      system.money_positive NOT NULL DEFAULT 0,
    closing_balance   system.money_amount NOT NULL,
    transaction_count INTEGER NOT NULL DEFAULT 0 CHECK (transaction_count >= 0),
    hold_amount       system.money_positive NOT NULL DEFAULT 0,
    currency_code     system.currency_code NOT NULL,
    base_closing_balance system.money_amount,             -- MNT-д хөрвүүлсэн (тайланд)
    fx_rate           system.fx_rate,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (id, balance_date),
    CONSTRAINT uq_account_balance_day UNIQUE (account_id, balance_date),
    CONSTRAINT ck_acc_bal_movement CHECK (closing_balance = opening_balance + total_credit - total_debit
                                          OR closing_balance = opening_balance + total_debit - total_credit)
) PARTITION BY RANGE (balance_date);

CREATE TABLE account.account_balances_2025 PARTITION OF account.account_balances
    FOR VALUES FROM ('2025-01-01') TO ('2026-01-01');
CREATE TABLE account.account_balances_2026 PARTITION OF account.account_balances
    FOR VALUES FROM ('2026-01-01') TO ('2027-01-01');
CREATE TABLE account.account_balances_2027 PARTITION OF account.account_balances
    FOR VALUES FROM ('2027-01-01') TO ('2028-01-01');
CREATE TABLE account.account_balances_default PARTITION OF account.account_balances DEFAULT;

COMMENT ON CONSTRAINT ck_acc_bal_movement ON account.account_balances IS
    'Хөдөлгөөн тэнцэх ёстой. Дебит/кредит талын чиглэл дансны төрлөөс хамаарна';

CREATE TABLE account.account_status_history (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    account_id     UUID        NOT NULL REFERENCES account.accounts (id) ON DELETE CASCADE,
    old_status     VARCHAR(20),
    new_status     VARCHAR(20) NOT NULL,
    balance_at_change system.money_amount,
    change_reason  VARCHAR(300),
    changed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    changed_by     BIGINT
);
