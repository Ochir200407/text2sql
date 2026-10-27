-- =====================================================================
-- triggers/001_triggers.sql — Бүрэн бүтэн байдлыг сахиулах trigger-үүд
-- ---------------------------------------------------------------------
-- Хэрэглэх дараалал: migrations/001..015 → functions/* → ЭНЭ ФАЙЛ
-- =====================================================================

\set ON_ERROR_STOP on

-- =====================================================================
-- 1. updated_at + version — updated_at багана бүхий БҮХ хүснэгтэд
-- =====================================================================
DO $$
DECLARE r RECORD;
BEGIN
    FOR r IN
        SELECT n.nspname AS sch, c.relname AS tbl
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attname = 'updated_at'
                            AND a.attnum > 0 AND NOT a.attisdropped
        JOIN pg_attribute v ON v.attrelid = c.oid AND v.attname = 'version'
                            AND v.attnum > 0 AND NOT v.attisdropped
        WHERE c.relkind IN ('r', 'p')
          AND n.nspname IN ('system', 'core', 'customer', 'kyc', 'product', 'loan',
                            'collateral', 'account', 'deposit', 'accounting', 'risk', 'collection')
          AND NOT EXISTS (SELECT 1 FROM pg_inherits i WHERE i.inhrelid = c.oid)
        ORDER BY 1, 2
    LOOP
        EXECUTE format(
            'CREATE TRIGGER trg_%s_updated_at BEFORE UPDATE ON %I.%I
             FOR EACH ROW EXECUTE FUNCTION system.fn_set_updated_at()',
            r.tbl, r.sch, r.tbl
        );
    END LOOP;
END;
$$;

-- =====================================================================
-- 2. Аудит — санхүүгийн болон хувийн мэдээлэл агуулсан хүснэгтүүдэд
--    (БҮХ хүснэгтэд тавихгүй: аудитын лог хэт хурдан ургана.
--     Сонголтын шалгуур: мөнгө, эрх, хувийн мэдээлэл, шийдвэр)
-- =====================================================================
DO $$
DECLARE
    r      RECORD;
    tables TEXT[] := ARRAY[
        'core.users', 'core.roles', 'core.permissions', 'core.role_permissions',
        'core.user_roles', 'core.user_branch_access', 'core.employees', 'core.branches',
        'customer.customers', 'customer.customer_individuals', 'customer.customer_organizations',
        'customer.customer_addresses', 'customer.customer_contacts', 'customer.customer_bank_accounts',
        'kyc.kyc_profiles', 'kyc.customer_risk_profiles', 'kyc.aml_alerts',
        'product.products', 'product.product_rates', 'product.product_fees', 'product.product_limits',
        'loan.loan_applications', 'loan.loan_decisions', 'loan.loans', 'loan.loan_contracts',
        'loan.loan_disbursements', 'loan.loan_payments', 'loan.loan_fees', 'loan.loan_penalties',
        'loan.loan_restructures', 'loan.loan_writeoffs', 'loan.loan_schedules',
        'collateral.collaterals', 'collateral.loan_collaterals', 'collateral.loan_guarantors',
        'account.accounts', 'account.account_holds', 'account.payment_orders',
        'deposit.deposit_accounts', 'deposit.deposit_early_withdrawals',
        'accounting.chart_of_accounts', 'accounting.accounting_periods', 'accounting.journal_entries',
        'risk.credit_score_models', 'risk.fraud_cases',
        'collection.collection_cases', 'collection.collection_promises',
        'system.system_parameters', 'system.documents', 'system.exchange_rates'
    ];
    t TEXT;
BEGIN
    FOREACH t IN ARRAY tables LOOP
        SELECT split_part(t, '.', 1) AS sch, split_part(t, '.', 2) AS tbl INTO r;
        EXECUTE format(
            'CREATE TRIGGER trg_%s_audit AFTER INSERT OR UPDATE OR DELETE ON %I.%I
             FOR EACH ROW EXECUTE FUNCTION system.fn_audit()',
            r.tbl, r.sch, r.tbl
        );
    END LOOP;
END;
$$;

-- =====================================================================
-- 3. Давхар бичилтийн тэнцэл — DEFERRABLE (COMMIT дээр шалгана)
-- =====================================================================
CREATE CONSTRAINT TRIGGER trg_journal_lines_balance
    AFTER INSERT OR UPDATE OR DELETE ON accounting.journal_lines
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION accounting.fn_check_entry_balance();

COMMENT ON FUNCTION accounting.fn_check_entry_balance() IS
    'Мөр бүрийн дараа биш, гүйлгээний ТӨГСГӨЛД шалгана — эс бөгөөс эхний мөр үргэлж тэнцэхгүй';

-- =====================================================================
-- 4. Батлагдсан журналыг өөрчлөхийг хориглох (зөвхөн сторно)
-- =====================================================================
CREATE OR REPLACE FUNCTION accounting.fn_protect_posted_entry()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        IF OLD.status = 'POSTED' THEN
            RAISE EXCEPTION 'Батлагдсан журналын бичилтийг устгах хориотой (id=%). Сторно ашиглана', OLD.id
                USING ERRCODE = 'restrict_violation';
        END IF;
        RETURN OLD;
    END IF;

    IF OLD.status = 'POSTED' AND NEW.status = 'POSTED' THEN
        IF NEW.total_debit <> OLD.total_debit OR NEW.total_credit <> OLD.total_credit
           OR NEW.posting_date <> OLD.posting_date OR NEW.journal_id <> OLD.journal_id THEN
            RAISE EXCEPTION 'Батлагдсан бичилтийн дүн/огноог өөрчлөх хориотой (id=%)', OLD.id
                USING ERRCODE = 'restrict_violation';
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_journal_entries_protect
    BEFORE UPDATE OR DELETE ON accounting.journal_entries
    FOR EACH ROW EXECUTE FUNCTION accounting.fn_protect_posted_entry();

-- Батлагдсан бичилтийн МӨРИЙГ өөрчлөхийг мөн хориглоно
CREATE OR REPLACE FUNCTION accounting.fn_protect_posted_lines()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_status VARCHAR;
BEGIN
    SELECT status INTO v_status FROM accounting.journal_entries
     WHERE id = COALESCE(NEW.entry_id, OLD.entry_id);
    IF v_status = 'POSTED' THEN
        RAISE EXCEPTION 'Батлагдсан бичилтийн мөрийг өөрчлөх/устгах хориотой (entry_id=%)',
            COALESCE(NEW.entry_id, OLD.entry_id) USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$$;

CREATE TRIGGER trg_journal_lines_protect
    BEFORE UPDATE OR DELETE ON accounting.journal_lines
    FOR EACH ROW EXECUTE FUNCTION accounting.fn_protect_posted_lines();

-- =====================================================================
-- 5. Зээлийн төлөвийн шилжилтийн хяналт + түүх
-- =====================================================================
CREATE OR REPLACE FUNCTION loan.fn_check_status_transition()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.status_id = OLD.status_id THEN
        RETURN NEW;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM loan.loan_status_transitions
         WHERE from_status_id = OLD.status_id AND to_status_id = NEW.status_id
    ) THEN
        RAISE EXCEPTION 'Зөвшөөрөгдөөгүй төлөвийн шилжилт: % → % (зээл %)',
            (SELECT status_code FROM loan.loan_statuses WHERE id = OLD.status_id),
            (SELECT status_code FROM loan.loan_statuses WHERE id = NEW.status_id),
            OLD.loan_number
            USING ERRCODE = 'check_violation';
    END IF;

    INSERT INTO loan.loan_status_history (
        loan_id, old_status_id, new_status_id, change_reason,
        outstanding_at_change, dpd_at_change, changed_by
    ) VALUES (
        NEW.id, OLD.status_id, NEW.status_id,
        NULLIF(current_setting('app.reason', TRUE), ''),
        NEW.total_outstanding, NEW.days_past_due,
        system.fn_current_user_id()
    );
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_loans_status_transition
    BEFORE UPDATE OF status_id ON loan.loans
    FOR EACH ROW EXECUTE FUNCTION loan.fn_check_status_transition();

COMMENT ON FUNCTION loan.fn_check_status_transition() IS
    'Зөвшөөрөгдсөн шилжилт (loan_status_transitions)-ээс гадуур шилжихийг DB түвшинд хориглоно';

-- =====================================================================
-- 6. Харилцагчийн төлөвийн түүх — автоматаар
-- =====================================================================
CREATE OR REPLACE FUNCTION customer.fn_track_status_change()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.status_id IS DISTINCT FROM OLD.status_id THEN
        INSERT INTO customer.customer_status_history (
            customer_id, old_status_id, new_status_id, change_reason, changed_by
        ) VALUES (
            NEW.id, OLD.status_id, NEW.status_id,
            NULLIF(current_setting('app.reason', TRUE), ''), system.fn_current_user_id()
        );
    END IF;
    IF NEW.segment_id IS DISTINCT FROM OLD.segment_id AND NEW.segment_id IS NOT NULL THEN
        INSERT INTO customer.customer_segment_history (
            customer_id, old_segment_id, new_segment_id, change_reason, changed_by
        ) VALUES (
            NEW.id, OLD.segment_id, NEW.segment_id,
            NULLIF(current_setting('app.reason', TRUE), ''), system.fn_current_user_id()
        );
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_customers_track_status
    AFTER UPDATE OF status_id, segment_id ON customer.customers
    FOR EACH ROW EXECUTE FUNCTION customer.fn_track_status_change();

-- =====================================================================
-- 7. Дансны үлдэгдлийн хамгаалалт (сөрөг үлдэгдэл, боломжит үлдэгдэл)
-- =====================================================================
CREATE OR REPLACE FUNCTION account.fn_guard_balance()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_allows_negative BOOLEAN;
BEGIN
    SELECT allows_negative INTO v_allows_negative
      FROM account.account_types WHERE id = NEW.account_type_id;

    IF NOT COALESCE(v_allows_negative, FALSE)
       AND NEW.current_balance < -NEW.overdraft_limit THEN
        RAISE EXCEPTION 'Сөрөг үлдэгдэл зөвшөөрөгдөхгүй (данс %, үлдэгдэл %, овердрафт %)',
            NEW.account_number, NEW.current_balance, NEW.overdraft_limit
            USING ERRCODE = 'check_violation';
    END IF;

    IF NEW.hold_amount > GREATEST(NEW.current_balance, 0) + NEW.overdraft_limit THEN
        RAISE EXCEPTION 'Блоклосон дүн үлдэгдлээс их байна (данс %)', NEW.account_number
            USING ERRCODE = 'check_violation';
    END IF;

    NEW.available_balance := NEW.current_balance - NEW.hold_amount + NEW.overdraft_limit;

    IF NEW.status = 'ACTIVE' AND NEW.last_transaction_at IS NOT NULL
       AND NEW.last_transaction_at < now() - INTERVAL '365 days' THEN
        NEW.is_dormant := TRUE;
        NEW.dormant_since := COALESCE(NEW.dormant_since, CURRENT_DATE);
    END IF;

    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_accounts_guard_balance
    BEFORE INSERT OR UPDATE ON account.accounts
    FOR EACH ROW EXECUTE FUNCTION account.fn_guard_balance();

COMMENT ON FUNCTION account.fn_guard_balance() IS
    'Сөрөг үлдэгдлийг account_types.allows_negative + overdraft_limit-ээр зөвшөөрнө. '
    'available_balance-ыг үргэлж дахин тооцно';

-- =====================================================================
-- 8. Хувь хүний нас (CHECK-д CURRENT_DATE ашиглах боломжгүй тул trigger)
-- =====================================================================
CREATE OR REPLACE FUNCTION customer.fn_check_individual_age()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
BEGIN
    IF NEW.birth_date > CURRENT_DATE THEN
        RAISE EXCEPTION 'Төрсөн огноо ирээдүйд байж болохгүй: %', NEW.birth_date
            USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.birth_date > CURRENT_DATE - INTERVAL '18 years' THEN
        RAISE EXCEPTION '18 нас хүрээгүй этгээдтэй харилцагчийн харилцаа үүсгэхгүй (төрсөн: %)', NEW.birth_date
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_customer_individuals_age
    BEFORE INSERT OR UPDATE OF birth_date ON customer.customer_individuals
    FOR EACH ROW EXECUTE FUNCTION customer.fn_check_individual_age();

-- =====================================================================
-- 9. Барьцааны хуваарилалт нийт үнэлгээнээс хэтрэхгүй
-- =====================================================================
CREATE OR REPLACE FUNCTION collateral.fn_check_allocation()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_accepted  NUMERIC;
    v_allocated NUMERIC;
BEGIN
    SELECT accepted_value INTO v_accepted FROM collateral.collaterals WHERE id = NEW.collateral_id;

    SELECT COALESCE(SUM(allocated_amount), 0) INTO v_allocated
      FROM collateral.loan_collaterals
     WHERE collateral_id = NEW.collateral_id
       AND status IN ('PENDING', 'ACTIVE')
       AND id <> COALESCE(NEW.id, -1);

    IF v_allocated + NEW.allocated_amount > v_accepted THEN
        RAISE EXCEPTION 'Барьцааны хуваарилалт хүлээн зөвшөөрсөн үнэлгээнээс хэтэрлээ '
                        '(хуваарилсан %, шинэ %, үнэлгээ %)', v_allocated, NEW.allocated_amount, v_accepted
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER trg_loan_collaterals_allocation
    BEFORE INSERT OR UPDATE OF allocated_amount ON collateral.loan_collaterals
    FOR EACH ROW EXECUTE FUNCTION collateral.fn_check_allocation();

-- =====================================================================
-- 10. Outbox — гол үйл явдлыг ETL/интеграцид найдвартай дамжуулах
-- =====================================================================
CREATE OR REPLACE FUNCTION system.fn_emit_outbox_event()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_event TEXT;
BEGIN
    v_event := TG_ARGV[0];
    INSERT INTO system.outbox_events (event_type, aggregate_type, aggregate_id, payload)
    VALUES (v_event, TG_ARGV[1]::system.entity_type, (to_jsonb(NEW) ->> 'id'), to_jsonb(NEW));
    RETURN NULL;
END;
$$;

CREATE TRIGGER trg_loans_outbox
    AFTER INSERT ON loan.loans
    FOR EACH ROW EXECUTE FUNCTION system.fn_emit_outbox_event('loan.created', 'LOAN');

CREATE TRIGGER trg_customers_outbox
    AFTER INSERT ON customer.customers
    FOR EACH ROW EXECUTE FUNCTION system.fn_emit_outbox_event('customer.created', 'CUSTOMER');

CREATE TRIGGER trg_loan_payments_outbox
    AFTER INSERT ON loan.loan_payments
    FOR EACH ROW EXECUTE FUNCTION system.fn_emit_outbox_event('loan.payment_received', 'LOAN');

COMMENT ON FUNCTION system.fn_emit_outbox_event() IS
    'Үйл явдлыг гүйлгээтэй нэг commit-д бичнэ — Kafka/ETL алдагдсан ч мэдээлэл алдагдахгүй';
