-- =====================================================================
-- 001_extensions.sql — Өргөтгөл, схем, домэйн, ENUM, нийтлэг тохиргоо
-- ББСБ Core Database · PostgreSQL 14+
-- ---------------------------------------------------------------------
-- Энэ файл дараагийн бүх migration-ий суурь. Тусдаа ажиллуулна:
--   psql -v ON_ERROR_STOP=1 -f migrations/001_extensions.sql
-- =====================================================================

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------------
-- 1. Өргөтгөл
-- ---------------------------------------------------------------------
CREATE EXTENSION IF NOT EXISTS pgcrypto;    -- gen_random_uuid(), digest(), hmac() — sensitive өгөгдөл
CREATE EXTENSION IF NOT EXISTS citext;      -- имэйл — том/жижиг үсэг ялгахгүй unique
CREATE EXTENSION IF NOT EXISTS pg_trgm;     -- нэрээр хайх (trigram index)
CREATE EXTENSION IF NOT EXISTS btree_gist;  -- давхцахгүй хугацаа (EXCLUDE constraint)

-- ---------------------------------------------------------------------
-- 2. Домэйн схемүүд
--    Схем бүр нэг bounded context. Хандах эрхийг схемийн түвшинд өгнө.
-- ---------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS system;      -- лавлах, тохиргоо, аудит, интеграц
CREATE SCHEMA IF NOT EXISTS core;        -- байгууллагын бүтэц, хэрэглэгч, эрх
CREATE SCHEMA IF NOT EXISTS customer;    -- харилцагчийн мастер
CREATE SCHEMA IF NOT EXISTS kyc;         -- KYC / AML / PEP / sanctions
CREATE SCHEMA IF NOT EXISTS product;     -- бүтээгдэхүүн, хүү, шимтгэл
CREATE SCHEMA IF NOT EXISTS loan;        -- зээлийн бүрэн lifecycle
CREATE SCHEMA IF NOT EXISTS collateral;  -- барьцаа, батлан даагч
CREATE SCHEMA IF NOT EXISTS account;     -- данс, гүйлгээ, төлбөр
CREATE SCHEMA IF NOT EXISTS deposit;     -- хадгаламж
CREATE SCHEMA IF NOT EXISTS accounting;  -- давхар бичилтийн нягтлан
CREATE SCHEMA IF NOT EXISTS risk;        -- скоринг, эрсдэл, залилан
CREATE SCHEMA IF NOT EXISTS collection;  -- эргэн төлүүлэлт, барагдуулалт
CREATE SCHEMA IF NOT EXISTS reporting;   -- зөвхөн view — BI холболтод

COMMENT ON SCHEMA system     IS 'Лавлах өгөгдөл, системийн тохиргоо, аудит, интеграцийн лог';
COMMENT ON SCHEMA core       IS 'Салбар, ажилтан, хэрэглэгч, role/permission';
COMMENT ON SCHEMA customer   IS 'Харилцагчийн мастер: хувь хүн ба байгууллага';
COMMENT ON SCHEMA kyc        IS 'KYC, AML, PEP, санкцын шалгалт';
COMMENT ON SCHEMA product    IS 'Бүтээгдэхүүний каталог, хүү, шимтгэл, хязгаар';
COMMENT ON SCHEMA loan       IS 'Зээлийн хүсэлтээс хаалт хүртэлх бүрэн мөчлөг';
COMMENT ON SCHEMA collateral IS 'Барьцаа хөрөнгө, үнэлгээ, батлан даагч';
COMMENT ON SCHEMA account    IS 'Харилцах данс, гүйлгээ, төлбөрийн даалгавар';
COMMENT ON SCHEMA deposit    IS 'Хадгаламжийн бүтээгдэхүүн, гэрээ, хүү';
COMMENT ON SCHEMA accounting IS 'Дансны төлөвлөгөө, журнал, ерөнхий дэвтэр';
COMMENT ON SCHEMA risk       IS 'Зээлийн оноо, эрсдэлийн үнэлгээ, залилангийн хяналт';
COMMENT ON SCHEMA collection IS 'Хугацаа хэтэрсэн зээлийн барагдуулалт';
COMMENT ON SCHEMA reporting  IS 'Тайлангийн view давхарга (хүснэгт үүсгэхгүй)';

-- ---------------------------------------------------------------------
-- 3. Домэйн (нэг төрлийг нэг газар тодорхойлж, бүх схемд дахин ашиглана)
--    Мөнгөн дүнд FLOAT/DOUBLE ХЭЗЭЭ Ч ашиглахгүй — NUMERIC.
-- ---------------------------------------------------------------------
CREATE DOMAIN system.money_amount   AS NUMERIC(20, 4);            -- ±999 их наяд, 4 орон
CREATE DOMAIN system.money_positive AS NUMERIC(20, 4) CHECK (VALUE >= 0);
CREATE DOMAIN system.rate_pct       AS NUMERIC(9, 6)  CHECK (VALUE >= 0 AND VALUE <= 1000);
CREATE DOMAIN system.fx_rate        AS NUMERIC(18, 8) CHECK (VALUE > 0);
CREATE DOMAIN system.currency_code  AS CHAR(3)        CHECK (VALUE ~ '^[A-Z]{3}$');
CREATE DOMAIN system.country_code   AS CHAR(2)        CHECK (VALUE ~ '^[A-Z]{2}$');
CREATE DOMAIN system.email_address  AS CITEXT         CHECK (VALUE ~ '^[^@[:space:]]+@[^@[:space:]]+\.[a-z]{2,}$');
CREATE DOMAIN system.phone_number   AS VARCHAR(20)    CHECK (VALUE ~ '^[0-9+][0-9 +()-]{5,19}$');
CREATE DOMAIN system.code_key       AS VARCHAR(50)    CHECK (VALUE ~ '^[A-Z][A-Z0-9_]{1,49}$');
CREATE DOMAIN system.percent_share  AS NUMERIC(7, 4)  CHECK (VALUE >= 0 AND VALUE <= 100);

COMMENT ON DOMAIN system.money_amount  IS 'Мөнгөн дүн NUMERIC(20,4) — сөрөг утга зөвшөөрнө (сторно, залруулга)';
COMMENT ON DOMAIN system.rate_pct      IS 'Хүү/хувь (жилийн %). 12.5% → 12.500000';
COMMENT ON DOMAIN system.code_key      IS 'Лавлах кодын түлхүүр: ТОМ_ҮСЭГ_ЗУРААС';

-- ---------------------------------------------------------------------
-- 4. ENUM — зөвхөн ТОГТМОЛ, техникийн олонлогт.
--    Бизнесийн төлөв (зээл, гүйлгээ, хүсэлт, харилцагч) нь ЛАВЛАХ ХҮСНЭГТ —
--    учир нь тэдгээр нь workflow-той, тайлбартай, өөрчлөгддөг (§18).
-- ---------------------------------------------------------------------
CREATE TYPE system.dr_cr             AS ENUM ('D', 'C');
CREATE TYPE system.party_type        AS ENUM ('INDIVIDUAL', 'ORGANIZATION');
CREATE TYPE system.gender_type       AS ENUM ('MALE', 'FEMALE', 'OTHER');
CREATE TYPE system.rate_type         AS ENUM ('FIXED', 'VARIABLE');
CREATE TYPE system.interest_method   AS ENUM ('SIMPLE', 'COMPOUND');
CREATE TYPE system.day_count_conv    AS ENUM ('ACT_365', 'ACT_360', 'D30_360', 'ACT_ACT');
CREATE TYPE system.amortization_type AS ENUM ('ANNUITY', 'EQUAL_PRINCIPAL', 'BULLET', 'INTEREST_ONLY');
CREATE TYPE system.period_unit       AS ENUM ('DAY', 'WEEK', 'MONTH', 'QUARTER', 'YEAR');
CREATE TYPE system.balance_side      AS ENUM ('DEBIT', 'CREDIT');
CREATE TYPE system.gl_account_type   AS ENUM ('ASSET', 'LIABILITY', 'EQUITY', 'INCOME', 'EXPENSE', 'OFF_BALANCE');
CREATE TYPE system.entity_type       AS ENUM (
    'CUSTOMER', 'LOAN', 'LOAN_APPLICATION', 'ACCOUNT', 'DEPOSIT_ACCOUNT',
    'COLLATERAL', 'GUARANTOR', 'TRANSACTION', 'JOURNAL_ENTRY', 'EMPLOYEE',
    'USER', 'COLLECTION_CASE', 'DOCUMENT'
);
CREATE TYPE system.audit_action      AS ENUM ('INSERT', 'UPDATE', 'DELETE');
CREATE TYPE system.verification_result AS ENUM ('PASSED', 'FAILED', 'MANUAL_REVIEW', 'EXPIRED');
CREATE TYPE system.data_sensitivity  AS ENUM ('PUBLIC', 'INTERNAL', 'CONFIDENTIAL', 'RESTRICTED');

COMMENT ON TYPE system.entity_type IS 'Полиморф холбоос (document, audit, notification) — entity_type + entity_id';

-- ---------------------------------------------------------------------
-- 5. Нийтлэг trigger функцүүд (хүснэгтүүд үүсэхээс өмнө хэрэгтэй)
--    Аудит ба updated_at-ийн бодит trigger-ийг triggers/001_triggers.sql
--    доторх DO-давталт бүх хүснэгтэд автоматаар холбоно.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION system.fn_set_updated_at()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    NEW.updated_at := now();
    -- Optimistic locking: мөр өөрчлөгдөх бүрд version нэмэгдэнэ.
    -- Апп давхарга UPDATE ... WHERE version = :read_version хийж зөрчлийг барина.
    IF TG_OP = 'UPDATE' AND NEW.version = OLD.version THEN
        NEW.version := OLD.version + 1;
    END IF;
    RETURN NEW;
END;
$$;

COMMENT ON FUNCTION system.fn_set_updated_at() IS
    'updated_at шинэчилж, version-ийг нэмэгдүүлнэ (optimistic locking)';

-- ---------------------------------------------------------------------
-- 6. Session context — аудитад хэн, хаанаас өөрчилснийг дамжуулна.
--    Апп холболт эхлэхдээ:  SELECT system.fn_set_session_context(:user_id, :ip, :session);
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION system.fn_set_session_context(
    p_user_id    BIGINT,
    p_ip_address INET    DEFAULT NULL,
    p_session_id TEXT    DEFAULT NULL,
    p_reason     TEXT    DEFAULT NULL
)
RETURNS VOID
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM set_config('app.user_id',    COALESCE(p_user_id::TEXT, ''), FALSE);
    PERFORM set_config('app.ip_address', COALESCE(host(p_ip_address), ''), FALSE);
    PERFORM set_config('app.session_id', COALESCE(p_session_id, ''), FALSE);
    PERFORM set_config('app.reason',     COALESCE(p_reason, ''), FALSE);
END;
$$;

CREATE OR REPLACE FUNCTION system.fn_current_user_id()
RETURNS BIGINT
LANGUAGE sql
STABLE
AS $$
    SELECT NULLIF(current_setting('app.user_id', TRUE), '')::BIGINT;
$$;

COMMENT ON FUNCTION system.fn_set_session_context(BIGINT, INET, TEXT, TEXT) IS
    'Апп холболтын эхэнд дуудна — аудитын trigger эндээс хэрэглэгч/IP-г авна';
