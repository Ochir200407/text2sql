-- =====================================================================
-- 005_product.sql — Бүтээгдэхүүний каталог (зээл, хадгаламж, данс)
-- ---------------------------------------------------------------------
-- Зарчим: бүтээгдэхүүний параметрийг КОДОНД биш ӨГӨГДӨЛД хадгална.
-- Шинэ зээлийн бүтээгдэхүүн нэмэхэд schema өөрчлөхгүй (§22.15).
-- Хүү, шимтгэл, хязгаар бүр хугацаатай (valid_from/valid_to) — түүх устахгүй.
-- =====================================================================

\set ON_ERROR_STOP on

CREATE TABLE product.product_types (
    id             SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    type_code      system.code_key NOT NULL,
    type_name      VARCHAR(150)    NOT NULL,
    category       VARCHAR(20)     NOT NULL
                   CHECK (category IN ('LOAN', 'DEPOSIT', 'CURRENT_ACCOUNT', 'GUARANTEE', 'LEASING')),
    description    TEXT,
    is_active      BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_product_types_code UNIQUE (type_code)
);
COMMENT ON TABLE product.product_types IS 'Бүтээгдэхүүний ангилал: цалингийн зээл, барьцаат зээл, хугацаатай хадгаламж ...';

CREATE TABLE product.products (
    id                    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_code          system.code_key NOT NULL,
    product_name          VARCHAR(200)    NOT NULL,
    product_type_id       SMALLINT NOT NULL REFERENCES product.product_types (id),
    currency_code         system.currency_code NOT NULL DEFAULT 'MNT',

    -- Зээлийн үндсэн параметр (хадгаламжид NULL)
    amortization_type     system.amortization_type,
    interest_method       system.interest_method,
    day_count_convention  system.day_count_conv NOT NULL DEFAULT 'ACT_365',
    repayment_period_unit system.period_unit,
    grace_period_days     SMALLINT NOT NULL DEFAULT 0 CHECK (grace_period_days >= 0),
    penalty_grace_days    SMALLINT NOT NULL DEFAULT 0 CHECK (penalty_grace_days >= 0),

    -- Нягтлангийн холбоос (011-д FK нэмнэ)
    gl_principal_account_code VARCHAR(20),
    gl_interest_income_code   VARCHAR(20),
    gl_penalty_income_code    VARCHAR(20),
    gl_fee_income_code        VARCHAR(20),

    requires_collateral   BOOLEAN NOT NULL DEFAULT FALSE,
    requires_guarantor    BOOLEAN NOT NULL DEFAULT FALSE,
    min_credit_score      SMALLINT CHECK (min_credit_score BETWEEN 0 AND 1000),
    max_dti_ratio         system.percent_share,          -- зээлийн үйлчилгээ/орлогын харьцаа
    max_ltv_ratio         system.percent_share,          -- зээл/барьцааны харьцаа

    launch_date           DATE    NOT NULL,
    discontinue_date      DATE,
    status                VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
                          CHECK (status IN ('DRAFT', 'ACTIVE', 'SUSPENDED', 'DISCONTINUED')),
    description           TEXT,
    terms_and_conditions  TEXT,
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by            BIGINT,
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by            BIGINT,
    version               INTEGER     NOT NULL DEFAULT 1,
    deleted_at            TIMESTAMPTZ,
    CONSTRAINT uq_products_code UNIQUE (product_code),
    CONSTRAINT ck_products_discontinue CHECK (discontinue_date IS NULL OR discontinue_date >= launch_date),
    CONSTRAINT ck_products_ltv CHECK (NOT requires_collateral OR max_ltv_ratio IS NOT NULL)
);
COMMENT ON COLUMN product.products.max_dti_ratio IS 'DTI дээд хязгаар — зээлийн шийдвэрт автоматаар шалгагдана';

-- Чөлөөт параметр (кампанит ажил, тусгай нөхцөл)
CREATE TABLE product.product_parameters (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id     BIGINT      NOT NULL REFERENCES product.products (id) ON DELETE CASCADE,
    param_code     system.code_key NOT NULL,
    param_value    TEXT        NOT NULL,
    value_type     VARCHAR(20) NOT NULL DEFAULT 'STRING'
                   CHECK (value_type IN ('STRING', 'NUMBER', 'BOOLEAN', 'DATE', 'JSON')),
    valid_from     DATE        NOT NULL DEFAULT CURRENT_DATE,
    valid_to       DATE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by     BIGINT,
    CONSTRAINT ck_prod_param_period CHECK (valid_to IS NULL OR valid_to >= valid_from),
    CONSTRAINT ex_prod_param_overlap EXCLUDE USING gist (
        product_id WITH =, param_code WITH =,
        daterange(valid_from, COALESCE(valid_to, DATE '9999-12-31'), '[]') WITH &&
    )
);

-- Хүүгийн хүснэгт — дүн/хугацаа/сегментээр өөр
CREATE TABLE product.product_rates (
    id                 BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id         BIGINT NOT NULL REFERENCES product.products (id) ON DELETE CASCADE,
    rate_type          system.rate_type NOT NULL DEFAULT 'FIXED',
    annual_rate        system.rate_pct  NOT NULL,        -- жилийн хүү %
    base_rate_code     VARCHAR(30),                      -- хувьсах хүүд: POLICY_RATE + spread
    spread             system.rate_pct,
    min_amount         system.money_positive,
    max_amount         system.money_positive,
    min_term_months    SMALLINT CHECK (min_term_months > 0),
    max_term_months    SMALLINT CHECK (max_term_months > 0),
    segment_id         SMALLINT REFERENCES customer.customer_segments (id),
    risk_grade_code    VARCHAR(20),
    valid_from         DATE   NOT NULL,
    valid_to           DATE,
    is_active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by         BIGINT,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by         BIGINT,
    version            INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT ck_prod_rate_amount CHECK (max_amount IS NULL OR min_amount IS NULL OR max_amount >= min_amount),
    CONSTRAINT ck_prod_rate_term   CHECK (max_term_months IS NULL OR min_term_months IS NULL OR max_term_months >= min_term_months),
    CONSTRAINT ck_prod_rate_period CHECK (valid_to IS NULL OR valid_to >= valid_from),
    CONSTRAINT ck_prod_rate_variable CHECK (rate_type <> 'VARIABLE' OR base_rate_code IS NOT NULL)
);
COMMENT ON TABLE product.product_rates IS 'Одоо мөрдөж буй хүүгийн хүснэгт. Өөрчлөгдөхөд product_rate_history-д хуучин утга үлдэнэ';

CREATE TABLE product.product_rate_history (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_rate_id BIGINT NOT NULL REFERENCES product.product_rates (id) ON DELETE CASCADE,
    product_id     BIGINT NOT NULL REFERENCES product.products (id),
    old_annual_rate system.rate_pct,
    new_annual_rate system.rate_pct NOT NULL,
    old_spread     system.rate_pct,
    new_spread     system.rate_pct,
    change_reason  VARCHAR(300),
    effective_from DATE        NOT NULL,
    changed_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    changed_by     BIGINT,
    approved_by    BIGINT
);
COMMENT ON TABLE product.product_rate_history IS 'Хүүгийн өөрчлөлтийн түүх — ХЭЗЭЭ Ч устгахгүй (§6, §15)';

CREATE TABLE product.fee_types (
    id            SMALLINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    fee_code      system.code_key NOT NULL,
    fee_name      VARCHAR(150)    NOT NULL,
    charge_event  VARCHAR(30)     NOT NULL
                  CHECK (charge_event IN ('APPLICATION', 'DISBURSEMENT', 'MONTHLY', 'EARLY_REPAYMENT',
                                          'LATE_PAYMENT', 'RESTRUCTURE', 'STATEMENT', 'TRANSACTION', 'CLOSURE')),
    is_refundable BOOLEAN NOT NULL DEFAULT FALSE,
    is_taxable    BOOLEAN NOT NULL DEFAULT TRUE,
    gl_income_code VARCHAR(20),
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_fee_types_code UNIQUE (fee_code)
);

CREATE TABLE product.product_fees (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id     BIGINT   NOT NULL REFERENCES product.products (id) ON DELETE CASCADE,
    fee_type_id    SMALLINT NOT NULL REFERENCES product.fee_types (id),
    calc_method    VARCHAR(30) NOT NULL   -- text2sql-new: 20→30 ('PERCENT_OF_OUTSTANDING' = 22 тэмдэгт)
                   CHECK (calc_method IN ('FIXED', 'PERCENT_OF_PRINCIPAL', 'PERCENT_OF_OUTSTANDING', 'PERCENT_OF_PAYMENT', 'TIERED')),
    fixed_amount   system.money_positive,
    percent_rate   system.percent_share,
    min_amount     system.money_positive,
    max_amount     system.money_positive,
    currency_code  system.currency_code NOT NULL DEFAULT 'MNT',
    valid_from     DATE     NOT NULL,
    valid_to       DATE,
    is_active      BOOLEAN  NOT NULL DEFAULT TRUE,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by     BIGINT,
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by     BIGINT,
    version        INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT ck_prod_fee_method CHECK (
        (calc_method = 'FIXED' AND fixed_amount IS NOT NULL)
        OR (calc_method <> 'FIXED' AND percent_rate IS NOT NULL)
    ),
    CONSTRAINT ck_prod_fee_period CHECK (valid_to IS NULL OR valid_to >= valid_from)
);

CREATE TABLE product.product_limits (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id        BIGINT NOT NULL REFERENCES product.products (id) ON DELETE CASCADE,
    limit_scope       VARCHAR(20) NOT NULL DEFAULT 'PRODUCT'
                      CHECK (limit_scope IN ('PRODUCT', 'SEGMENT', 'BRANCH', 'CHANNEL')),
    segment_id        SMALLINT REFERENCES customer.customer_segments (id),
    branch_id         BIGINT   REFERENCES core.branches (id),
    min_amount        system.money_positive NOT NULL,
    max_amount        system.money_positive NOT NULL,
    min_term_months   SMALLINT NOT NULL CHECK (min_term_months > 0),
    max_term_months   SMALLINT NOT NULL CHECK (max_term_months > 0),
    max_active_loans  SMALLINT CHECK (max_active_loans > 0),
    valid_from        DATE     NOT NULL,
    valid_to          DATE,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by        BIGINT,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by        BIGINT,
    version           INTEGER NOT NULL DEFAULT 1,
    CONSTRAINT ck_prod_limit_amount CHECK (max_amount >= min_amount),
    CONSTRAINT ck_prod_limit_term   CHECK (max_term_months >= min_term_months),
    CONSTRAINT ck_prod_limit_period CHECK (valid_to IS NULL OR valid_to >= valid_from)
);

-- Төлбөр хуваарилах дараалал (торгууль → шимтгэл → хугацаа хэтэрсэн хүү → хүү → үндсэн)
CREATE TABLE product.payment_allocation_rules (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    product_id      BIGINT   NOT NULL REFERENCES product.products (id) ON DELETE CASCADE,
    allocation_order SMALLINT NOT NULL CHECK (allocation_order > 0),
    component       VARCHAR(30) NOT NULL
                    CHECK (component IN ('PENALTY', 'FEE', 'OVERDUE_INTEREST', 'INTEREST', 'PRINCIPAL', 'INSURANCE')),
    valid_from      DATE     NOT NULL DEFAULT CURRENT_DATE,
    valid_to        DATE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by      BIGINT,
    CONSTRAINT uq_pay_alloc_order UNIQUE (product_id, allocation_order, valid_from),
    CONSTRAINT uq_pay_alloc_comp  UNIQUE (product_id, component, valid_from)
);
COMMENT ON TABLE product.payment_allocation_rules IS
    'Төлбөрийн хуваарилалтын дараалал — хатуу кодлохгүй, бүтээгдэхүүн бүрд өөр байж болно';

-- Аль салбарт зарагдах вэ
CREATE TABLE product.product_branch_availability (
    product_id   BIGINT NOT NULL REFERENCES product.products (id) ON DELETE CASCADE,
    branch_id    BIGINT NOT NULL REFERENCES core.branches (id),
    is_available BOOLEAN NOT NULL DEFAULT TRUE,
    valid_from   DATE   NOT NULL DEFAULT CURRENT_DATE,
    valid_to     DATE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by   BIGINT,
    PRIMARY KEY (product_id, branch_id)
);
