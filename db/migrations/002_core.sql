-- =====================================================================
-- 002_core.sql — Байгууллагын бүтэц, ажилтан, хэрэглэгч, эрхийн загвар
-- ---------------------------------------------------------------------
-- PK: BIGINT IDENTITY — дотоод, бага эзэлхүүнтэй, аудит/эрхийн шалгалтад
-- байнга JOIN хийгддэг тул нягт индекс чухал (UUID-аас 2 дахин бага).
-- =====================================================================

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------------
-- Салбар
-- ---------------------------------------------------------------------
CREATE TABLE core.branches (
    id                BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    branch_code       VARCHAR(20)  NOT NULL,
    branch_name       VARCHAR(200) NOT NULL,
    branch_type       VARCHAR(20)  NOT NULL DEFAULT 'BRANCH'
                      CHECK (branch_type IN ('HEAD_OFFICE', 'BRANCH', 'SUB_BRANCH', 'AGENT', 'DIGITAL')),
    parent_branch_id  BIGINT REFERENCES core.branches (id),
    country_code      system.country_code NOT NULL DEFAULT 'MN',
    province          VARCHAR(100),
    district          VARCHAR(100),
    address_line      VARCHAR(300),
    phone             system.phone_number,
    email             system.email_address,
    gl_prefix         VARCHAR(10),                       -- салбарын нягтлангийн код
    opened_date       DATE         NOT NULL,
    closed_date       DATE,
    is_active         BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    created_by        BIGINT,
    updated_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_by        BIGINT,
    version           INTEGER      NOT NULL DEFAULT 1,
    deleted_at        TIMESTAMPTZ,
    CONSTRAINT uq_branches_code UNIQUE (branch_code),
    CONSTRAINT ck_branches_close_after_open CHECK (closed_date IS NULL OR closed_date >= opened_date),
    CONSTRAINT ck_branches_no_self_parent CHECK (parent_branch_id IS NULL OR parent_branch_id <> id)
);
COMMENT ON TABLE core.branches IS 'Салбар — мод бүтэцтэй (parent_branch_id). Бүх гүйлгээ, зээл салбарт харьяалагдана';

-- ---------------------------------------------------------------------
-- Хэлтэс ба албан тушаал
-- ---------------------------------------------------------------------
CREATE TABLE core.departments (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    department_code system.code_key NOT NULL,
    department_name VARCHAR(150)    NOT NULL,
    parent_id       BIGINT REFERENCES core.departments (id),
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by      BIGINT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by      BIGINT,
    version         INTEGER     NOT NULL DEFAULT 1,
    deleted_at      TIMESTAMPTZ,
    CONSTRAINT uq_departments_code UNIQUE (department_code)
);

CREATE TABLE core.positions (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    position_code    system.code_key NOT NULL,
    position_name    VARCHAR(150)    NOT NULL,
    grade_level      SMALLINT        CHECK (grade_level BETWEEN 1 AND 20),
    approval_limit   system.money_positive,             -- зээл батлах эрхийн дээд хязгаар
    is_active        BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by       BIGINT,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by       BIGINT,
    version          INTEGER     NOT NULL DEFAULT 1,
    deleted_at       TIMESTAMPTZ,
    CONSTRAINT uq_positions_code UNIQUE (position_code)
);
COMMENT ON COLUMN core.positions.approval_limit IS 'Тухайн албан тушаалын зээл батлах эрхийн дээд дүн (MNT)';

-- ---------------------------------------------------------------------
-- Ажилтан
-- ---------------------------------------------------------------------
CREATE TABLE core.employees (
    id              BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    employee_no     VARCHAR(20)  NOT NULL,
    first_name      VARCHAR(100) NOT NULL,
    last_name       VARCHAR(100) NOT NULL,
    register_hash   BYTEA,                              -- регистрийн HMAC (хайлт/давхардал)
    branch_id       BIGINT       NOT NULL REFERENCES core.branches (id),
    department_id   BIGINT       REFERENCES core.departments (id),
    position_id     BIGINT       REFERENCES core.positions (id),
    manager_id      BIGINT       REFERENCES core.employees (id),
    email           system.email_address,
    phone           system.phone_number,
    hire_date       DATE         NOT NULL,
    termination_date DATE,
    employment_status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE'
                      CHECK (employment_status IN ('ACTIVE', 'ON_LEAVE', 'SUSPENDED', 'TERMINATED')),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    created_by      BIGINT,
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_by      BIGINT,
    version         INTEGER      NOT NULL DEFAULT 1,
    deleted_at      TIMESTAMPTZ,
    CONSTRAINT uq_employees_no UNIQUE (employee_no),
    CONSTRAINT ck_employees_term CHECK (termination_date IS NULL OR termination_date >= hire_date),
    CONSTRAINT ck_employees_no_self_manager CHECK (manager_id IS NULL OR manager_id <> id),
    CONSTRAINT ck_employees_terminated_has_date
        CHECK (employment_status <> 'TERMINATED' OR termination_date IS NOT NULL)
);
COMMENT ON TABLE core.employees IS 'Ажилтан. Зээлийн мэргэжилтэн, батлагч, кассчин бүгд эндээс';

-- Ажилтны салбар/албан тушаалын түүх (DWH-ийн SCD2-ийн эх)
CREATE TABLE core.employee_assignments (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    employee_id    BIGINT NOT NULL REFERENCES core.employees (id),
    branch_id      BIGINT NOT NULL REFERENCES core.branches (id),
    department_id  BIGINT REFERENCES core.departments (id),
    position_id    BIGINT REFERENCES core.positions (id),
    valid_from     DATE   NOT NULL,
    valid_to       DATE,
    change_reason  VARCHAR(200),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by     BIGINT,
    CONSTRAINT ck_emp_assign_period CHECK (valid_to IS NULL OR valid_to >= valid_from),
    -- Нэг ажилтанд огтлолцох хугацаа байхгүй
    CONSTRAINT ex_emp_assign_overlap EXCLUDE USING gist (
        employee_id WITH =,
        daterange(valid_from, COALESCE(valid_to, DATE '9999-12-31'), '[]') WITH &&
    )
);
COMMENT ON TABLE core.employee_assignments IS 'Ажилтны байршлын түүх — дарж бичихгүй, шинэ мөр нэмнэ';

-- ---------------------------------------------------------------------
-- Хэрэглэгч ба эрх
-- ---------------------------------------------------------------------
CREATE TABLE core.users (
    id                   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_uid             UUID         NOT NULL DEFAULT gen_random_uuid(),  -- гадаад API-д ил гарах ID
    username             VARCHAR(60)  NOT NULL,
    employee_id          BIGINT       REFERENCES core.employees (id),
    password_hash        TEXT         NOT NULL,          -- argon2id/bcrypt — plaintext ХЭЗЭЭ Ч биш
    password_algo        VARCHAR(20)  NOT NULL DEFAULT 'ARGON2ID',
    password_changed_at  TIMESTAMPTZ  NOT NULL DEFAULT now(),
    must_change_password BOOLEAN      NOT NULL DEFAULT TRUE,
    mfa_enabled          BOOLEAN      NOT NULL DEFAULT FALSE,
    mfa_secret_enc       BYTEA,                          -- pgcrypto-оор шифрлэсэн
    email                system.email_address,
    status               VARCHAR(20)  NOT NULL DEFAULT 'ACTIVE'
                         CHECK (status IN ('ACTIVE', 'LOCKED', 'DISABLED', 'PENDING_ACTIVATION')),
    failed_login_count   SMALLINT     NOT NULL DEFAULT 0 CHECK (failed_login_count >= 0),
    locked_until         TIMESTAMPTZ,
    last_login_at        TIMESTAMPTZ,
    data_access_level    system.data_sensitivity NOT NULL DEFAULT 'INTERNAL',
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    created_by           BIGINT,
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_by           BIGINT,
    version              INTEGER      NOT NULL DEFAULT 1,
    deleted_at           TIMESTAMPTZ,
    CONSTRAINT uq_users_username UNIQUE (username),
    CONSTRAINT uq_users_uid      UNIQUE (user_uid),
    CONSTRAINT ck_users_password_not_plain CHECK (length(password_hash) >= 20)
);
COMMENT ON COLUMN core.users.password_hash IS 'Argon2id/bcrypt хэш. Нууц үгийг ил хадгалахыг CHECK-ээр ч сануулав';
COMMENT ON COLUMN core.users.data_access_level IS 'Хэрэглэгчийн харах эрхийн дээд түвшин — RESTRICTED өгөгдөлд шүүлт хийнэ';

CREATE TABLE core.user_sessions (
    id             BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_uid    UUID        NOT NULL DEFAULT gen_random_uuid(),
    user_id        BIGINT      NOT NULL REFERENCES core.users (id),
    ip_address     INET,
    user_agent     TEXT,
    device_id      VARCHAR(100),
    started_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    ended_at       TIMESTAMPTZ,
    end_reason     VARCHAR(30) CHECK (end_reason IN ('LOGOUT', 'TIMEOUT', 'ADMIN_REVOKED', 'PASSWORD_CHANGED')),
    CONSTRAINT uq_user_sessions_uid UNIQUE (session_uid),
    CONSTRAINT ck_user_sessions_end CHECK (ended_at IS NULL OR ended_at >= started_at)
);

CREATE TABLE core.roles (
    id           BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    role_code    system.code_key NOT NULL,
    role_name    VARCHAR(150)    NOT NULL,
    description  TEXT,
    is_system    BOOLEAN     NOT NULL DEFAULT FALSE,     -- систем role — устгах боломжгүй
    is_active    BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by   BIGINT,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by   BIGINT,
    version      INTEGER     NOT NULL DEFAULT 1,
    deleted_at   TIMESTAMPTZ,
    CONSTRAINT uq_roles_code UNIQUE (role_code)
);

CREATE TABLE core.permissions (
    id               BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    permission_code  system.code_key NOT NULL,
    module           VARCHAR(50)     NOT NULL,           -- LOAN, CUSTOMER, ACCOUNTING ...
    action           VARCHAR(30)     NOT NULL
                     CHECK (action IN ('VIEW', 'CREATE', 'UPDATE', 'DELETE', 'APPROVE', 'POST', 'EXPORT', 'ADMIN')),
    description      TEXT,
    sensitivity      system.data_sensitivity NOT NULL DEFAULT 'INTERNAL',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_permissions_code   UNIQUE (permission_code),
    CONSTRAINT uq_permissions_module UNIQUE (module, action)
);

CREATE TABLE core.role_permissions (
    role_id       BIGINT NOT NULL REFERENCES core.roles (id) ON DELETE CASCADE,
    permission_id BIGINT NOT NULL REFERENCES core.permissions (id) ON DELETE CASCADE,
    granted_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    granted_by    BIGINT,
    PRIMARY KEY (role_id, permission_id)
);

CREATE TABLE core.user_roles (
    user_id     BIGINT NOT NULL REFERENCES core.users (id) ON DELETE CASCADE,
    role_id     BIGINT NOT NULL REFERENCES core.roles (id),
    valid_from  DATE   NOT NULL DEFAULT CURRENT_DATE,
    valid_to    DATE,
    assigned_by BIGINT,
    assigned_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, role_id),
    CONSTRAINT ck_user_roles_period CHECK (valid_to IS NULL OR valid_to >= valid_from)
);

CREATE TABLE core.user_branch_access (
    user_id     BIGINT  NOT NULL REFERENCES core.users (id) ON DELETE CASCADE,
    branch_id   BIGINT  NOT NULL REFERENCES core.branches (id),
    access_type VARCHAR(20) NOT NULL DEFAULT 'READ_WRITE'
                CHECK (access_type IN ('READ', 'READ_WRITE', 'APPROVE')),
    granted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    granted_by  BIGINT,
    PRIMARY KEY (user_id, branch_id)
);
COMMENT ON TABLE core.user_branch_access IS
    'Салбарын түвшний өгөгдлийн хандалт. Хоосон = зөвхөн өөрийн салбар (апп давхарга шийднэ)';
