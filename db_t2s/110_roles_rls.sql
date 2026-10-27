-- =====================================================================
-- 110_roles_rls.sql — Bot-ийн DB role, эрх, timeout, салбарын RLS
-- ---------------------------------------------------------------------
-- Зааврын 7-р хэсэг:
--  * Bot-д тусдаа DB role: зөвхөн t2s view дээр SELECT; table owner, superuser, BYPASSRLS биш.
--  * Row-level security: эцсийн хэрэглэгчийн салбараар; FORCE ROW LEVEL SECURITY.
--  * statement_timeout, lock_timeout, idle_in_transaction_session_timeout, read-only.
-- Загвар:
--  t2s_reader (NOLOGIN) — t2s view-уудын эзэмшигч; суурь хүснэгтэд SELECT; RLS-д захирагдана.
--  t2s_bot (LOGIN)      — bot-ийн холболт; бүх салбар (t2s_acl.role_scope-д тохируулна).
--  t2s_branch_demo      — зөвхөн UB01 салбарыг харах жишээ role (RLS тест).
-- View нь эзэмшигчийн (t2s_reader) эрхээр суурь хүснэгтэд хандах тул RLS бодлого t2s_reader-т
-- тавигдаж, жинхэнэ хэрэглэгчийг session_user-ээр (SET ROLE-оор солигдохгүй) тодорхойлно.
-- PostgreSQL 14+ (security_invoker view шаардлагагүй).
-- =====================================================================

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 't2s_reader') THEN
        CREATE ROLE t2s_reader NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 't2s_bot') THEN
        CREATE ROLE t2s_bot LOGIN PASSWORD '{{BOT_PASSWORD}}' NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS
            CONNECTION LIMIT 64;
    ELSE
        ALTER ROLE t2s_bot LOGIN PASSWORD '{{BOT_PASSWORD}}' NOSUPERUSER NOBYPASSRLS;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 't2s_branch_demo') THEN
        CREATE ROLE t2s_branch_demo LOGIN PASSWORD '{{BOT_PASSWORD}}' NOSUPERUSER NOCREATEDB NOCREATEROLE
            NOBYPASSRLS CONNECTION LIMIT 8;
    END IF;
END;
$$;

-- Role-ийн түвшний хамгаалалт (DB бүрд биш, кластерт хамаарна)
ALTER ROLE t2s_bot SET default_transaction_read_only = on;
ALTER ROLE t2s_bot SET statement_timeout = '20s';
ALTER ROLE t2s_bot SET lock_timeout = '2s';
ALTER ROLE t2s_bot SET idle_in_transaction_session_timeout = '30s';
ALTER ROLE t2s_bot SET search_path = t2s;
ALTER ROLE t2s_bot SET temp_file_limit = '1GB';
ALTER ROLE t2s_bot SET work_mem = '64MB';
ALTER ROLE t2s_branch_demo SET default_transaction_read_only = on;
ALTER ROLE t2s_branch_demo SET statement_timeout = '20s';
ALTER ROLE t2s_branch_demo SET lock_timeout = '2s';
ALTER ROLE t2s_branch_demo SET idle_in_transaction_session_timeout = '30s';
ALTER ROLE t2s_branch_demo SET search_path = t2s;
ALTER ROLE t2s_branch_demo SET temp_file_limit = '1GB';

-- public схем ба түр хүснэгт үүсгэх эрхийг хаана
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
DO $$
BEGIN
    EXECUTE format('REVOKE TEMPORARY ON DATABASE %I FROM PUBLIC', current_database());
    EXECUTE format('REVOKE CREATE ON DATABASE %I FROM PUBLIC', current_database());
END;
$$;

-- ---------------------------------------------------------------------
-- Хандалтын хүрээ (ACL) — role бүр аль салбарыг харах вэ
-- ---------------------------------------------------------------------
CREATE SCHEMA IF NOT EXISTS t2s_acl;
REVOKE ALL ON SCHEMA t2s_acl FROM PUBLIC;
CREATE TABLE IF NOT EXISTS t2s_acl.role_scope (
    role_name     NAME PRIMARY KEY,
    all_branches  BOOLEAN NOT NULL DEFAULT FALSE,
    branch_codes  TEXT[]  NOT NULL DEFAULT '{}',
    note          TEXT
);
COMMENT ON TABLE t2s_acl.role_scope IS 'Bot role бүрийн харах салбар. Апп эцсийн хэрэглэгч бүрд тусдаа DB role ашиглана';
INSERT INTO t2s_acl.role_scope (role_name, all_branches, branch_codes, note) VALUES
    ('t2s_bot', TRUE, '{}', 'Төв оффисын шинжээч — бүх салбар'),
    ('t2s_branch_demo', FALSE, '{UB01}', 'RLS-ийн жишээ: зөвхөн Сүхбаатар салбар')
ON CONFLICT (role_name) DO UPDATE SET all_branches = EXCLUDED.all_branches, branch_codes = EXCLUDED.branch_codes;

CREATE OR REPLACE FUNCTION t2s_acl.visible_branch_ids()
RETURNS BIGINT[] LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT CASE WHEN (SELECT r.rolsuper FROM pg_roles r WHERE r.rolname = session_user)
                THEN (SELECT array_agg(b.id) FROM core.branches b) ELSE COALESCE((
        SELECT CASE WHEN s.all_branches THEN (SELECT array_agg(b.id) FROM core.branches b)
                    ELSE (SELECT array_agg(b.id) FROM core.branches b WHERE b.branch_code = ANY (s.branch_codes)) END
          FROM t2s_acl.role_scope s
         WHERE s.role_name = session_user), '{}'::BIGINT[]) END
$$;
CREATE OR REPLACE FUNCTION t2s_acl.sees_all_branches()
RETURNS BOOLEAN LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT COALESCE((SELECT r.rolsuper FROM pg_roles r WHERE r.rolname = session_user), FALSE)
        OR COALESCE((SELECT s.all_branches FROM t2s_acl.role_scope s WHERE s.role_name = session_user), FALSE)
$$;
COMMENT ON FUNCTION t2s_acl.visible_branch_ids() IS
    'session_user-ийн харах салбарууд (superuser — бүгд). SET ROLE-оор солигдохгүй (session_user) — RLS бодлогод';
REVOKE ALL ON FUNCTION t2s_acl.visible_branch_ids(), t2s_acl.sees_all_branches() FROM PUBLIC;
GRANT USAGE ON SCHEMA t2s_acl TO t2s_reader, t2s_bot, t2s_branch_demo;
GRANT EXECUTE ON FUNCTION t2s_acl.visible_branch_ids(), t2s_acl.sees_all_branches()
    TO t2s_reader, t2s_bot, t2s_branch_demo;

-- ---------------------------------------------------------------------
-- View эзэмшигчийн (t2s_reader) эрх — зөвхөн view-д хэрэгтэй хүснэгтүүд
-- ---------------------------------------------------------------------
GRANT USAGE ON SCHEMA core, customer, product, loan, collateral, account, deposit, accounting, risk,
                      collection, kyc, system TO t2s_reader;
GRANT SELECT ON
    core.branches, core.employees, core.departments, core.positions,
    customer.customers, customer.customer_types, customer.customer_statuses, customer.customer_segments,
    customer.customer_addresses, customer.customer_individuals, customer.customer_organizations,
    product.products, product.product_types, product.product_rates, product.fee_types,
    loan.loan_applications, loan.loan_application_statuses, loan.loan_decisions, loan.loans, loan.loan_statuses,
    loan.classification_grades, loan.loan_schedules, loan.loan_schedule_items, loan.loan_payments,
    loan.loan_overdues, loan.loan_fees, loan.loan_penalties, loan.loan_restructures, loan.loan_writeoffs,
    collateral.collaterals, collateral.collateral_types, collateral.loan_collaterals, collateral.loan_guarantors,
    collateral.guarantors,
    account.accounts, account.account_types, account.transactions, account.transaction_types,
    account.transaction_channels, account.transaction_statuses, account.account_balances,
    deposit.deposit_accounts,
    collection.collection_cases, collection.collection_stages, collection.collection_agencies,
    collection.collection_actions, collection.action_types, collection.collection_promises,
    risk.credit_scores, risk.credit_score_models, risk.risk_grades,
    kyc.aml_alerts, kyc.aml_rules,
    accounting.journal_lines, accounting.journal_entries, accounting.journals, accounting.accounting_periods,
    accounting.chart_of_accounts,
    system.system_parameters, system.exchange_rates
TO t2s_reader;

-- View ба туслах функцийн эзэмшлийг t2s_reader руу шилжүүлнэ
DO $$
DECLARE r RECORD;
BEGIN
    FOR r IN SELECT c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
              WHERE n.nspname = 't2s' AND c.relkind = 'v' LOOP
        EXECUTE format('ALTER VIEW t2s.%I OWNER TO t2s_reader', r.relname);
    END LOOP;
END;
$$;
ALTER FUNCTION t2s.business_date() OWNER TO t2s_reader;
ALTER FUNCTION t2s.fx_to_mnt(TEXT, DATE) OWNER TO t2s_reader;

-- Bot role-ууд: зөвхөн t2s схемд SELECT
REVOKE ALL ON SCHEMA t2s FROM PUBLIC;
GRANT USAGE ON SCHEMA t2s TO t2s_bot, t2s_branch_demo;
GRANT SELECT ON ALL TABLES IN SCHEMA t2s TO t2s_bot, t2s_branch_demo;
REVOKE ALL ON FUNCTION t2s.business_date(), t2s.fx_to_mnt(TEXT, DATE) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION t2s.business_date(), t2s.fx_to_mnt(TEXT, DATE) TO t2s_reader, t2s_bot, t2s_branch_demo;

-- ---------------------------------------------------------------------
-- Row-level security (салбараар). FORCE — хүснэгтийн эзэмшигчид ч хамаарна
-- (superuser үргэлж RLS-ийг алгасдаг тул өгөгдөл үүсгэх ажилд нөлөөлөхгүй).
-- ---------------------------------------------------------------------
DO $$
DECLARE
    t TEXT;
    nullable_branch TEXT[] := ARRAY['account.transactions', 'loan.loan_payments', 'collateral.collaterals',
                                    'accounting.journal_lines'];
    tables TEXT[] := ARRAY['customer.customers', 'loan.loans', 'loan.loan_applications', 'account.accounts',
                           'deposit.deposit_accounts', 'collection.collection_cases', 'account.transactions',
                           'loan.loan_payments', 'collateral.collaterals', 'accounting.journal_lines'];
BEGIN
    FOREACH t IN ARRAY tables LOOP
        EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE %s FORCE ROW LEVEL SECURITY', t);
        EXECUTE format('DROP POLICY IF EXISTS t2s_branch_scope ON %s', t);
        IF t = ANY (nullable_branch) THEN
            EXECUTE format('CREATE POLICY t2s_branch_scope ON %s FOR SELECT TO t2s_reader USING ('
                           'branch_id = ANY ((SELECT t2s_acl.visible_branch_ids())::BIGINT[]) '
                           'OR (branch_id IS NULL AND (SELECT t2s_acl.sees_all_branches())))', t);
        ELSE
            EXECUTE format('CREATE POLICY t2s_branch_scope ON %s FOR SELECT TO t2s_reader USING ('
                           'branch_id = ANY ((SELECT t2s_acl.visible_branch_ids())::BIGINT[]))', t);
        END IF;
    END LOOP;
END;
$$;

-- ---------------------------------------------------------------------
-- Аюултай системийн функцийг PUBLIC-ээс хураана (validator-ийн ард DB-ийн хоёр дахь хамгаалалт):
-- унтуулах (DoS), session-ий тохиргоо өөрчлөх, дурын SQL мөрийг гүйцэтгэх функцүүд.
-- Superuser-т нөлөөлөхгүй; энэ DB зөвхөн text2sql-ийн үнэлгээнд зориулагдсан хуулбар.
-- ---------------------------------------------------------------------
REVOKE EXECUTE ON FUNCTION
    pg_catalog.pg_sleep(DOUBLE PRECISION),
    pg_catalog.pg_sleep_for(INTERVAL),
    pg_catalog.pg_sleep_until(TIMESTAMPTZ),
    pg_catalog.set_config(TEXT, TEXT, BOOLEAN),
    pg_catalog.query_to_xml(TEXT, BOOLEAN, BOOLEAN, TEXT),
    pg_catalog.query_to_xmlschema(TEXT, BOOLEAN, BOOLEAN, TEXT),
    pg_catalog.query_to_xml_and_xmlschema(TEXT, BOOLEAN, BOOLEAN, TEXT),
    pg_catalog.pg_advisory_lock(BIGINT),
    pg_catalog.pg_advisory_xact_lock(BIGINT)
FROM PUBLIC;
