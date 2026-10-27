-- =====================================================================
-- functions/003_accounting_functions.sql — Нягтлангийн функцүүд
-- ---------------------------------------------------------------------
-- Давхар бичилтийн бүрэн бүтэн байдлыг ХАНГАХ давхарга.
-- =====================================================================

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------------
-- Огноо → нээлттэй тайлант үе
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION accounting.fn_period_for_date(p_date DATE)
RETURNS BIGINT
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    v_id     BIGINT;
    v_status VARCHAR;
BEGIN
    SELECT id, status INTO v_id, v_status
      FROM accounting.accounting_periods
     WHERE p_date BETWEEN start_date AND end_date;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Тайлант үе тодорхойлогдоогүй: %', p_date USING ERRCODE = 'no_data_found';
    END IF;
    IF v_status IN ('CLOSED', 'LOCKED') THEN
        RAISE EXCEPTION 'Хаагдсан тайлант үед бичилт хийх боломжгүй: % (%)', p_date, v_status
            USING ERRCODE = 'raise_exception';
    END IF;
    RETURN v_id;
END;
$$;

-- ---------------------------------------------------------------------
-- Журналын тэнцлийн шалгалт — DEFERRABLE CONSTRAINT TRIGGER-ийн функц.
-- COMMIT-ийн үед л ажиллана: мөрүүдийг ээлжлэн оруулах боломжтой.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION accounting.fn_check_entry_balance()
RETURNS TRIGGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_entry_id BIGINT := COALESCE(NEW.entry_id, OLD.entry_id);
    v_debit    NUMERIC;
    v_credit   NUMERIC;
    v_status   VARCHAR;
    r_cur      RECORD;
BEGIN
    SELECT status INTO v_status FROM accounting.journal_entries WHERE id = v_entry_id;
    IF v_status IS NULL THEN
        RETURN NULL;   -- толгой нь устсан (CASCADE) — шалгах зүйлгүй
    END IF;

    SELECT COALESCE(SUM(base_amount) FILTER (WHERE dr_cr = 'D'), 0),
           COALESCE(SUM(base_amount) FILTER (WHERE dr_cr = 'C'), 0)
      INTO v_debit, v_credit
      FROM accounting.journal_lines WHERE entry_id = v_entry_id;

    IF v_debit <> v_credit THEN
        RAISE EXCEPTION 'Журналын бичилт тэнцэхгүй (entry_id=%): дебит % ≠ кредит %',
            v_entry_id, v_debit, v_credit USING ERRCODE = 'check_violation';
    END IF;

    -- Валют тус бүрээр ч тэнцэх ёстой (ханшийн зөрүү тусдаа дансаар)
    FOR r_cur IN
        SELECT currency_code,
               COALESCE(SUM(amount) FILTER (WHERE dr_cr = 'D'), 0) AS d,
               COALESCE(SUM(amount) FILTER (WHERE dr_cr = 'C'), 0) AS c
          FROM accounting.journal_lines
         WHERE entry_id = v_entry_id
         GROUP BY currency_code
    LOOP
        IF r_cur.d <> r_cur.c THEN
            RAISE EXCEPTION 'Журнал % валютаар тэнцэхгүй (%): дебит % ≠ кредит %',
                v_entry_id, r_cur.currency_code, r_cur.d, r_cur.c USING ERRCODE = 'check_violation';
        END IF;
    END LOOP;

    -- Толгойн нийлбэрийг мөрүүдээс шинэчилнэ
    UPDATE accounting.journal_entries
       SET total_debit = v_debit, total_credit = v_credit
     WHERE id = v_entry_id AND (total_debit <> v_debit OR total_credit <> v_credit);

    RETURN NULL;
END;
$$;

COMMENT ON FUNCTION accounting.fn_check_entry_balance() IS
    'Σ дебит = Σ кредит (суурь валютаар БА валют тус бүрээр). DEFERRABLE — COMMIT дээр шалгана';

-- ---------------------------------------------------------------------
-- Журнал батлах: төлөв → POSTED, дэвтрийн үлдэгдэл шинэчлэх
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION accounting.fn_post_entry(
    p_entry_id BIGINT,
    p_user_id  BIGINT
)
RETURNS VOID
LANGUAGE plpgsql
AS $$
DECLARE
    v_entry accounting.journal_entries%ROWTYPE;
BEGIN
    SELECT * INTO v_entry FROM accounting.journal_entries WHERE id = p_entry_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Журналын бичилт олдсонгүй: %', p_entry_id USING ERRCODE = 'no_data_found';
    END IF;
    IF v_entry.status = 'POSTED' THEN
        RAISE EXCEPTION 'Бичилт аль хэдийн батлагдсан: %', p_entry_id;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM accounting.journal_lines WHERE entry_id = p_entry_id) THEN
        RAISE EXCEPTION 'Мөргүй бичилтийг батлах боломжгүй: %', p_entry_id;
    END IF;

    PERFORM accounting.fn_period_for_date(v_entry.posting_date);

    -- text2sql-new засвар: толгойн нийлбэрийг POSTED болгохоос ӨМНӨ мөрүүдээс тооцно.
    -- Эс бөгөөс COMMIT дээрх DEFERRABLE fn_check_entry_balance нь POSTED бичилтийн
    -- нийлбэрийг шинэчлэх гэж оролдоод trg_journal_entries_protect-д унадаг байв.
    UPDATE accounting.journal_entries je
       SET status = 'POSTED', posted_at = now(), posted_by = p_user_id,
           total_debit  = s.d,
           total_credit = s.c
      FROM (SELECT COALESCE(SUM(base_amount) FILTER (WHERE dr_cr = 'D'), 0) AS d,
                   COALESCE(SUM(base_amount) FILTER (WHERE dr_cr = 'C'), 0) AS c
              FROM accounting.journal_lines WHERE entry_id = p_entry_id) s
     WHERE je.id = p_entry_id;

    -- Дэвтрийн үлдэгдлийн нэгтгэлийг шинэчлэх (upsert)
    INSERT INTO accounting.ledger_balances AS lb (
        gl_account_id, period_id, branch_id, currency_code,
        period_debit, period_credit, closing_debit, closing_credit, base_closing_balance
    )
    SELECT jl.gl_account_id, v_entry.period_id, jl.branch_id, jl.currency_code,
           COALESCE(SUM(jl.amount) FILTER (WHERE jl.dr_cr = 'D'), 0),
           COALESCE(SUM(jl.amount) FILTER (WHERE jl.dr_cr = 'C'), 0),
           COALESCE(SUM(jl.amount) FILTER (WHERE jl.dr_cr = 'D'), 0),
           COALESCE(SUM(jl.amount) FILTER (WHERE jl.dr_cr = 'C'), 0),
           COALESCE(SUM(jl.base_amount) FILTER (WHERE jl.dr_cr = 'D'), 0)
         - COALESCE(SUM(jl.base_amount) FILTER (WHERE jl.dr_cr = 'C'), 0)
      FROM accounting.journal_lines jl
     WHERE jl.entry_id = p_entry_id
     GROUP BY jl.gl_account_id, jl.branch_id, jl.currency_code
    ON CONFLICT (gl_account_id, period_id, branch_id, currency_code) DO UPDATE
       SET period_debit  = lb.period_debit  + EXCLUDED.period_debit,
           period_credit = lb.period_credit + EXCLUDED.period_credit,
           closing_debit  = lb.closing_debit  + EXCLUDED.closing_debit,
           closing_credit = lb.closing_credit + EXCLUDED.closing_credit,
           base_closing_balance = lb.base_closing_balance + EXCLUDED.base_closing_balance,
           last_updated_at = now();
END;
$$;

COMMENT ON FUNCTION accounting.fn_post_entry(BIGINT, BIGINT) IS
    'Бичилтийг батална. Хаагдсан үед бичихийг хориглоно, ledger_balances-ыг шинэчилнэ';

-- ---------------------------------------------------------------------
-- Сторно — батлагдсан бичилтийг ЗАСАХГҮЙ, эсрэг бичилт үүсгэнэ
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION accounting.fn_reverse_entry(
    p_entry_id BIGINT,
    p_reason   VARCHAR,
    p_user_id  BIGINT,
    p_date     DATE DEFAULT CURRENT_DATE
)
RETURNS BIGINT
LANGUAGE plpgsql
AS $$
DECLARE
    v_src      accounting.journal_entries%ROWTYPE;
    v_new_id   BIGINT;
BEGIN
    SELECT * INTO v_src FROM accounting.journal_entries WHERE id = p_entry_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Бичилт олдсонгүй: %', p_entry_id USING ERRCODE = 'no_data_found';
    END IF;
    IF v_src.status <> 'POSTED' THEN
        RAISE EXCEPTION 'Зөвхөн батлагдсан бичилтийг сторно хийнэ (одоо: %)', v_src.status;
    END IF;
    IF v_src.reversed_by_id IS NOT NULL THEN
        RAISE EXCEPTION 'Бичилт аль хэдийн сторно хийгдсэн: %', p_entry_id;
    END IF;

    INSERT INTO accounting.journal_entries (
        entry_no, journal_id, period_id, branch_id, entry_date, value_date, posting_date,
        description, reference, source_system, source_entity_type, source_entity_id,
        currency_code, status, is_reversal, reversal_of_id, reversal_reason, created_by
    ) VALUES (
        system.fn_next_number('JOURNAL_ENTRY'), v_src.journal_id,
        accounting.fn_period_for_date(p_date), v_src.branch_id, p_date, p_date, p_date,
        'СТОРНО: ' || v_src.description, v_src.entry_no, v_src.source_system,
        v_src.source_entity_type, v_src.source_entity_id,
        v_src.currency_code, 'DRAFT', TRUE, p_entry_id, p_reason, p_user_id
    ) RETURNING id INTO v_new_id;

    -- Мөр бүрийг эсрэг талд нь
    INSERT INTO accounting.journal_lines (
        entry_id, posting_date, line_no, gl_account_id, branch_id, dr_cr, amount,
        currency_code, fx_rate, base_amount, subledger_type, subledger_id,
        customer_id, product_id, cost_center, description
    )
    SELECT v_new_id, p_date, line_no, gl_account_id, branch_id,
           CASE dr_cr WHEN 'D' THEN 'C'::system.dr_cr ELSE 'D'::system.dr_cr END,
           amount, currency_code, fx_rate, base_amount, subledger_type, subledger_id,
           customer_id, product_id, cost_center, 'СТОРНО: ' || COALESCE(description, '')
      FROM accounting.journal_lines
     WHERE entry_id = p_entry_id
     ORDER BY line_no;

    PERFORM accounting.fn_post_entry(v_new_id, p_user_id);

    UPDATE accounting.journal_entries
       SET status = 'REVERSED', reversed_by_id = v_new_id, reversal_reason = p_reason
     WHERE id = p_entry_id;

    RETURN v_new_id;
END;
$$;

COMMENT ON FUNCTION accounting.fn_reverse_entry(BIGINT, VARCHAR, BIGINT, DATE) IS
    'Батлагдсан бичилтийг устгах/засахгүй — эсрэг бичилтээр хаана (аудитын мөр бүрэн үлдэнэ)';

-- ---------------------------------------------------------------------
-- Дүрэмд суурилсан автомат бичилт (жишээ: зээл олголт)
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION accounting.fn_create_entry_from_rule(
    p_rule_code   system.code_key,
    p_amounts     JSONB,          -- {"PRINCIPAL": 5000000, "INTEREST": 0, ...}
    p_date        DATE,
    p_branch_id   BIGINT,
    p_currency    system.currency_code,
    p_description VARCHAR,
    p_entity_type system.entity_type DEFAULT NULL,
    p_entity_id   UUID DEFAULT NULL,
    p_user_id     BIGINT DEFAULT NULL
)
RETURNS BIGINT
LANGUAGE plpgsql
AS $$
DECLARE
    v_rule    accounting.accounting_rules%ROWTYPE;
    v_entry   BIGINT;
    r_line    RECORD;
    v_amount  NUMERIC;
    v_rate    NUMERIC;
    v_line_no SMALLINT := 0;
BEGIN
    SELECT * INTO v_rule FROM accounting.accounting_rules
     WHERE rule_code = p_rule_code AND is_active
       AND valid_from <= p_date AND (valid_to IS NULL OR valid_to >= p_date);
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Нягтлангийн дүрэм олдсонгүй эсвэл хүчингүй: %', p_rule_code
            USING ERRCODE = 'no_data_found';
    END IF;

    v_rate := system.fn_exchange_rate(p_currency, 'MNT', p_date);

    INSERT INTO accounting.journal_entries (
        entry_no, journal_id, period_id, branch_id, entry_date, value_date, posting_date,
        description, source_system, source_entity_type, source_entity_id,
        currency_code, status, created_by
    ) VALUES (
        system.fn_next_number('JOURNAL_ENTRY'), v_rule.journal_id,
        accounting.fn_period_for_date(p_date), p_branch_id, p_date, p_date, p_date,
        p_description, 'CORE', p_entity_type, p_entity_id, p_currency, 'DRAFT', p_user_id
    ) RETURNING id INTO v_entry;

    FOR r_line IN
        SELECT * FROM accounting.accounting_rule_lines WHERE rule_id = v_rule.id ORDER BY line_no
    LOOP
        v_amount := COALESCE((p_amounts ->> r_line.amount_source)::NUMERIC, 0);
        CONTINUE WHEN v_amount = 0;

        IF r_line.gl_account_id IS NULL THEN
            RAISE EXCEPTION 'Дүрмийн мөрөнд данс тодорхойгүй (rule=%, line=%). '
                            'Динамик данс апп давхаргаас дамжуулагдана', p_rule_code, r_line.line_no;
        END IF;

        v_line_no := v_line_no + 1;
        INSERT INTO accounting.journal_lines (
            entry_id, posting_date, line_no, gl_account_id, branch_id, dr_cr, amount,
            currency_code, fx_rate, base_amount, subledger_type, subledger_id, description
        ) VALUES (
            v_entry, p_date, v_line_no, r_line.gl_account_id, p_branch_id, r_line.dr_cr, v_amount,
            p_currency, v_rate, round(v_amount * v_rate, 4),
            p_entity_type, p_entity_id, COALESCE(r_line.description_template, p_description)
        );
    END LOOP;

    IF v_line_no = 0 THEN
        RAISE EXCEPTION 'Дүрэм % дээр бичих дүн олдсонгүй', p_rule_code;
    END IF;

    PERFORM accounting.fn_post_entry(v_entry, p_user_id);
    RETURN v_entry;
END;
$$;

COMMENT ON FUNCTION accounting.fn_create_entry_from_rule IS
    'Үйл явдал → журнал автоматаар. Шинэ бүтээгдэхүүнд SQL/код БИЧИХГҮЙ, зөвхөн дүрэм нэмнэ';
