-- =====================================================================
-- functions/002_loan_functions.sql — Зээлийн тооцооллын функцүүд
-- ---------------------------------------------------------------------
-- Эдгээр нь бизнес логикийн ЦӨМ. Апп давхаргад давхардуулж бичихгүй —
-- хуваарь, DPD, ангилал, төлбөрийн хуваарилалт нэг л газраас гарна.
-- =====================================================================

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------------
-- 1. Эргэн төлөлтийн хуваарь үүсгэх
--    ANNUITY | EQUAL_PRINCIPAL | BULLET | INTEREST_ONLY
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION loan.fn_generate_schedule(
    p_loan_id  UUID,
    p_reason   VARCHAR DEFAULT 'ORIGINAL',
    p_user_id  BIGINT  DEFAULT NULL
)
RETURNS BIGINT
LANGUAGE plpgsql
AS $$
DECLARE
    v_loan        loan.loans%ROWTYPE;
    v_schedule_id BIGINT;
    v_version     SMALLINT;
    v_monthly     NUMERIC;
    v_payment     NUMERIC;
    v_balance     NUMERIC;
    v_principal   NUMERIC;
    v_interest    NUMERIC;
    v_due         DATE;
    v_prev_due    DATE;
    v_total_int   NUMERIC := 0;
    k             INTEGER;
BEGIN
    SELECT * INTO v_loan FROM loan.loans WHERE id = p_loan_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Зээл олдсонгүй: %', p_loan_id USING ERRCODE = 'no_data_found';
    END IF;
    IF v_loan.first_payment_date IS NULL THEN
        RAISE EXCEPTION 'first_payment_date тодорхойгүй — хуваарь үүсгэх боломжгүй';
    END IF;

    SELECT COALESCE(MAX(schedule_version), 0) + 1 INTO v_version
      FROM loan.loan_schedules WHERE loan_id = p_loan_id;

    UPDATE loan.loan_schedules SET is_current = FALSE
     WHERE loan_id = p_loan_id AND is_current;

    v_balance  := v_loan.principal_amount;
    v_monthly  := v_loan.interest_rate / 100 / 12;
    v_prev_due := COALESCE(v_loan.disbursement_date, v_loan.contract_date, CURRENT_DATE);

    -- Аннуитетийн тогтмол төлөлт
    IF v_loan.amortization_type = 'ANNUITY' THEN
        v_payment := CASE
            WHEN v_monthly = 0 THEN v_balance / v_loan.term_months
            ELSE v_balance * v_monthly / (1 - power(1 + v_monthly, -v_loan.term_months))
        END;
    END IF;

    INSERT INTO loan.loan_schedules (
        loan_id, schedule_version, is_current, generated_reason,
        total_principal, total_interest, installment_count,
        first_due_date, last_due_date, generated_by
    ) VALUES (
        p_loan_id, v_version, TRUE, p_reason,
        v_loan.principal_amount, 0, v_loan.term_months,
        v_loan.first_payment_date,
        (v_loan.first_payment_date + ((v_loan.term_months - 1) || ' months')::INTERVAL)::DATE,
        p_user_id
    ) RETURNING id INTO v_schedule_id;

    FOR k IN 1 .. v_loan.term_months LOOP
        v_due := (v_loan.first_payment_date + ((k - 1) || ' months')::INTERVAL)::DATE;
        v_due := system.fn_next_business_day(v_due);

        v_interest := round(v_balance * v_monthly, 2);

        v_principal := CASE v_loan.amortization_type
            WHEN 'ANNUITY'         THEN round(v_payment - v_interest, 2)
            WHEN 'EQUAL_PRINCIPAL' THEN round(v_loan.principal_amount / v_loan.term_months, 2)
            WHEN 'BULLET'          THEN CASE WHEN k = v_loan.term_months THEN v_balance ELSE 0 END
            WHEN 'INTEREST_ONLY'   THEN CASE WHEN k = v_loan.term_months THEN v_balance ELSE 0 END
        END;

        -- Сүүлийн хуваарьт бөөрөнхийлөлтийн үлдэгдлийг бүрэн хаана
        IF k = v_loan.term_months OR v_principal > v_balance THEN
            v_principal := v_balance;
        END IF;

        INSERT INTO loan.loan_schedule_items (
            schedule_id, loan_id, installment_no, due_date,
            period_start_date, period_end_date,
            principal_due, interest_due, opening_balance, closing_balance
        ) VALUES (
            v_schedule_id, p_loan_id, k, v_due,
            v_prev_due, v_due,
            v_principal, v_interest, v_balance, v_balance - v_principal
        );

        v_total_int := v_total_int + v_interest;
        v_balance   := v_balance - v_principal;
        v_prev_due  := v_due;
    END LOOP;

    UPDATE loan.loan_schedules
       SET total_interest = v_total_int
     WHERE id = v_schedule_id;

    RETURN v_schedule_id;
END;
$$;

COMMENT ON FUNCTION loan.fn_generate_schedule(UUID, VARCHAR, BIGINT) IS
    'Хуваарийн шинэ хувилбар үүсгэнэ. Хуучин хувилбар is_current = FALSE болж ХАДГАЛАГДАНА';

-- ---------------------------------------------------------------------
-- 2. DPD — хамгийн эртний төлөгдөөгүй хуваарийн мөрөөс
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION loan.fn_calculate_dpd(
    p_loan_id UUID,
    p_as_of   DATE DEFAULT CURRENT_DATE
)
RETURNS INTEGER
LANGUAGE sql
STABLE
AS $$
    SELECT COALESCE(GREATEST(0, p_as_of - MIN(si.due_date))::INTEGER, 0)
      FROM loan.loan_schedule_items si
      JOIN loan.loan_schedules s ON s.id = si.schedule_id AND s.is_current
     WHERE si.loan_id = p_loan_id
       AND si.due_date < p_as_of
       AND si.status IN ('PENDING', 'PARTIALLY_PAID', 'OVERDUE')
       AND (si.principal_due + si.interest_due + si.fee_due + si.insurance_due)
         > (si.principal_paid + si.interest_paid + si.fee_paid + si.insurance_paid);
$$;

COMMENT ON FUNCTION loan.fn_calculate_dpd(UUID, DATE) IS
    'Хугацаа хэтэрсэн хоног — ХАМГИЙН ЭРТНИЙ төлөгдөөгүй мөрөөс тооцно (олон улсын нийтлэг арга)';

-- ---------------------------------------------------------------------
-- 3. Активын ангилал — DPD → зэрэглэл (босго нь өгөгдөлд)
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION loan.fn_classify_by_dpd(p_dpd INTEGER)
RETURNS SMALLINT
LANGUAGE sql
STABLE
AS $$
    SELECT id FROM loan.classification_grades
     WHERE p_dpd >= min_dpd AND (max_dpd IS NULL OR p_dpd <= max_dpd)
     ORDER BY min_dpd DESC
     LIMIT 1;
$$;

CREATE OR REPLACE FUNCTION loan.fn_dpd_bucket(p_dpd INTEGER)
RETURNS VARCHAR
LANGUAGE sql
IMMUTABLE
AS $$
    SELECT CASE
        WHEN p_dpd <= 0   THEN 'CURRENT'
        WHEN p_dpd <= 30  THEN 'DPD_1_30'
        WHEN p_dpd <= 60  THEN 'DPD_31_60'
        WHEN p_dpd <= 90  THEN 'DPD_61_90'
        WHEN p_dpd <= 180 THEN 'DPD_91_180'
        ELSE 'DPD_180_PLUS'
    END;
$$;

-- ---------------------------------------------------------------------
-- 4. Зээлийн үлдэгдлийг эх сурвалжаас дахин тооцох (тулгалт, засвар)
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION loan.fn_recalculate_balances(p_loan_id UUID)
RETURNS VOID
LANGUAGE plpgsql
AS $$
DECLARE
    v_disbursed NUMERIC;
    v_prin_due  NUMERIC;
    v_prin_paid NUMERIC;
    v_int_due   NUMERIC;
    v_int_paid  NUMERIC;
    v_fee_due   NUMERIC;
    v_fee_paid  NUMERIC;
    v_pen_due   NUMERIC;
    v_pen_paid  NUMERIC;
    v_ovd_prin  NUMERIC;
    v_ovd_int   NUMERIC;
    v_dpd       INTEGER;
BEGIN
    SELECT COALESCE(SUM(amount), 0) INTO v_disbursed
      FROM loan.loan_disbursements WHERE loan_id = p_loan_id AND status = 'EXECUTED';

    SELECT COALESCE(SUM(si.principal_due), 0), COALESCE(SUM(si.principal_paid), 0),
           COALESCE(SUM(si.interest_due), 0),  COALESCE(SUM(si.interest_paid), 0),
           COALESCE(SUM(si.principal_due - si.principal_paid) FILTER (WHERE si.due_date < CURRENT_DATE), 0),
           COALESCE(SUM(si.interest_due  - si.interest_paid)  FILTER (WHERE si.due_date < CURRENT_DATE), 0)
      INTO v_prin_due, v_prin_paid, v_int_due, v_int_paid, v_ovd_prin, v_ovd_int
      FROM loan.loan_schedule_items si
      JOIN loan.loan_schedules s ON s.id = si.schedule_id AND s.is_current
     WHERE si.loan_id = p_loan_id;

    SELECT COALESCE(SUM(amount - waived_amount), 0), COALESCE(SUM(paid_amount), 0)
      INTO v_fee_due, v_fee_paid
      FROM loan.loan_fees WHERE loan_id = p_loan_id AND status <> 'REVERSED';

    SELECT COALESCE(SUM(amount - waived_amount), 0), COALESCE(SUM(paid_amount), 0)
      INTO v_pen_due, v_pen_paid
      FROM loan.loan_penalties WHERE loan_id = p_loan_id AND status <> 'REVERSED';

    v_dpd := loan.fn_calculate_dpd(p_loan_id);

    UPDATE loan.loans
       SET disbursed_amount      = v_disbursed,
           principal_outstanding = GREATEST(v_prin_due - v_prin_paid, 0),
           interest_outstanding  = GREATEST(v_int_due  - v_int_paid,  0),
           fee_outstanding       = GREATEST(v_fee_due  - v_fee_paid,  0),
           penalty_outstanding   = GREATEST(v_pen_due  - v_pen_paid,  0),
           principal_paid        = v_prin_paid,
           interest_paid         = v_int_paid,
           fee_paid              = v_fee_paid,
           penalty_paid          = v_pen_paid,
           overdue_principal     = GREATEST(v_ovd_prin, 0),
           overdue_interest      = GREATEST(v_ovd_int, 0),
           days_past_due         = v_dpd,
           max_days_past_due     = GREATEST(max_days_past_due, v_dpd),
           classification_id     = loan.fn_classify_by_dpd(v_dpd),
           is_npl                = COALESCE((SELECT is_npl FROM loan.classification_grades
                                              WHERE id = loan.fn_classify_by_dpd(v_dpd)), FALSE),
           updated_at            = now()
     WHERE id = p_loan_id;
END;
$$;

COMMENT ON FUNCTION loan.fn_recalculate_balances(UUID) IS
    'Denormalized үлдэгдлийг ЭХ СУРВАЛЖААС (хуваарь, олголт, шимтгэл, торгууль) дахин тооцно';

-- ---------------------------------------------------------------------
-- 5. Төлбөрийн хуваарилалт — бүтээгдэхүүний дүрмийн дарааллаар
--    Анхдагч дараалал: торгууль → шимтгэл → хугацаа хэтэрсэн хүү → хүү → үндсэн
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION loan.fn_allocate_payment(p_payment_id BIGINT)
RETURNS system.money_amount
LANGUAGE plpgsql
AS $$
DECLARE
    v_pay       loan.loan_payments%ROWTYPE;
    v_product   BIGINT;
    v_remaining NUMERIC;
    v_alloc     NUMERIC;
    v_order     SMALLINT := 0;
    r_comp      RECORD;
    r_item      RECORD;
    v_p NUMERIC := 0; v_i NUMERIC := 0; v_f NUMERIC := 0; v_pen NUMERIC := 0;
BEGIN
    SELECT * INTO v_pay FROM loan.loan_payments WHERE id = p_payment_id FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'Төлбөр олдсонгүй: %', p_payment_id USING ERRCODE = 'no_data_found';
    END IF;
    IF v_pay.status <> 'POSTED' THEN
        RAISE EXCEPTION 'Зөвхөн POSTED төлбөрийг хуваарилна (одоогийн: %)', v_pay.status;
    END IF;

    SELECT product_id INTO v_product FROM loan.loans WHERE id = v_pay.loan_id;
    v_remaining := v_pay.amount;

    FOR r_comp IN
        SELECT component
          FROM product.payment_allocation_rules
         WHERE product_id = v_product
           AND valid_from <= v_pay.value_date
           AND (valid_to IS NULL OR valid_to >= v_pay.value_date)
         ORDER BY allocation_order
    LOOP
        EXIT WHEN v_remaining <= 0;

        IF r_comp.component = 'PENALTY' THEN
            FOR r_item IN
                SELECT id, (amount - waived_amount - paid_amount) AS due
                  FROM loan.loan_penalties
                 WHERE loan_id = v_pay.loan_id AND status IN ('CHARGED', 'PARTIALLY_PAID')
                   AND (amount - waived_amount - paid_amount) > 0
                 ORDER BY charged_date
            LOOP
                EXIT WHEN v_remaining <= 0;
                v_alloc := LEAST(v_remaining, r_item.due);
                UPDATE loan.loan_penalties
                   SET paid_amount = paid_amount + v_alloc,
                       status = CASE WHEN paid_amount + v_alloc >= amount - waived_amount
                                     THEN 'PAID' ELSE 'PARTIALLY_PAID' END
                 WHERE id = r_item.id;
                v_order := v_order + 1;
                INSERT INTO loan.loan_payment_allocations (payment_id, component, amount, allocation_order)
                VALUES (p_payment_id, 'PENALTY', v_alloc, v_order);
                v_remaining := v_remaining - v_alloc;
                v_pen := v_pen + v_alloc;
            END LOOP;

        ELSIF r_comp.component = 'FEE' THEN
            FOR r_item IN
                SELECT id, (amount - waived_amount - paid_amount) AS due
                  FROM loan.loan_fees
                 WHERE loan_id = v_pay.loan_id AND status IN ('CHARGED', 'PARTIALLY_PAID')
                   AND (amount - waived_amount - paid_amount) > 0
                 ORDER BY charged_date
            LOOP
                EXIT WHEN v_remaining <= 0;
                v_alloc := LEAST(v_remaining, r_item.due);
                UPDATE loan.loan_fees
                   SET paid_amount = paid_amount + v_alloc,
                       status = CASE WHEN paid_amount + v_alloc >= amount - waived_amount
                                     THEN 'PAID' ELSE 'PARTIALLY_PAID' END
                 WHERE id = r_item.id;
                v_order := v_order + 1;
                INSERT INTO loan.loan_payment_allocations (payment_id, component, amount, allocation_order)
                VALUES (p_payment_id, 'FEE', v_alloc, v_order);
                v_remaining := v_remaining - v_alloc;
                v_f := v_f + v_alloc;
            END LOOP;

        ELSIF r_comp.component IN ('INTEREST', 'OVERDUE_INTEREST') THEN
            FOR r_item IN
                SELECT si.id, (si.interest_due - si.interest_paid) AS due
                  FROM loan.loan_schedule_items si
                  JOIN loan.loan_schedules s ON s.id = si.schedule_id AND s.is_current
                 WHERE si.loan_id = v_pay.loan_id
                   AND si.interest_due > si.interest_paid
                   AND (r_comp.component = 'INTEREST' OR si.due_date < v_pay.value_date)
                 ORDER BY si.due_date
            LOOP
                EXIT WHEN v_remaining <= 0;
                v_alloc := LEAST(v_remaining, r_item.due);
                UPDATE loan.loan_schedule_items SET interest_paid = interest_paid + v_alloc WHERE id = r_item.id;
                v_order := v_order + 1;
                INSERT INTO loan.loan_payment_allocations
                    (payment_id, schedule_item_id, component, amount, allocation_order)
                VALUES (p_payment_id, r_item.id, r_comp.component, v_alloc, v_order);
                v_remaining := v_remaining - v_alloc;
                v_i := v_i + v_alloc;
            END LOOP;

        ELSIF r_comp.component = 'PRINCIPAL' THEN
            FOR r_item IN
                SELECT si.id, (si.principal_due - si.principal_paid) AS due
                  FROM loan.loan_schedule_items si
                  JOIN loan.loan_schedules s ON s.id = si.schedule_id AND s.is_current
                 WHERE si.loan_id = v_pay.loan_id AND si.principal_due > si.principal_paid
                 ORDER BY si.due_date
            LOOP
                EXIT WHEN v_remaining <= 0;
                v_alloc := LEAST(v_remaining, r_item.due);
                UPDATE loan.loan_schedule_items SET principal_paid = principal_paid + v_alloc WHERE id = r_item.id;
                v_order := v_order + 1;
                INSERT INTO loan.loan_payment_allocations
                    (payment_id, schedule_item_id, component, amount, allocation_order)
                VALUES (p_payment_id, r_item.id, 'PRINCIPAL', v_alloc, v_order);
                v_remaining := v_remaining - v_alloc;
                v_p := v_p + v_alloc;
            END LOOP;
        END IF;
    END LOOP;

    -- Хуваарийн мөрийн төлөвийг шинэчилнэ
    UPDATE loan.loan_schedule_items si
       SET status = CASE
               WHEN si.principal_paid >= si.principal_due AND si.interest_paid >= si.interest_due
                    AND si.fee_paid >= si.fee_due THEN 'PAID'
               WHEN si.principal_paid > 0 OR si.interest_paid > 0 THEN 'PARTIALLY_PAID'
               WHEN si.due_date < CURRENT_DATE THEN 'OVERDUE'
               ELSE 'PENDING' END,
           paid_date = CASE
               WHEN si.principal_paid >= si.principal_due AND si.interest_paid >= si.interest_due
                    AND si.fee_paid >= si.fee_due THEN COALESCE(si.paid_date, v_pay.value_date)
               ELSE si.paid_date END,
           updated_at = now()
      FROM loan.loan_schedules s
     WHERE s.id = si.schedule_id AND s.is_current AND si.loan_id = v_pay.loan_id;

    UPDATE loan.loan_payments
       SET allocated_principal = v_p,
           allocated_interest  = v_i,
           allocated_penalty   = v_pen,
           allocated_fee       = v_f,
           unallocated_amount  = v_remaining,
           updated_at          = now()
     WHERE id = p_payment_id;

    UPDATE loan.loans
       SET last_payment_date = GREATEST(COALESCE(last_payment_date, v_pay.value_date), v_pay.value_date)
     WHERE id = v_pay.loan_id;

    PERFORM loan.fn_recalculate_balances(v_pay.loan_id);
    RETURN v_remaining;   -- хуваарилагдаагүй үлдэгдэл (урьдчилгаа)
END;
$$;

COMMENT ON FUNCTION loan.fn_allocate_payment(BIGINT) IS
    'Төлбөрийг бүтээгдэхүүний дүрмийн дарааллаар хуваарилна. Буцаах: хуваарилагдаагүй үлдэгдэл';

-- ---------------------------------------------------------------------
-- 6. Өдрийн хүү хуримтлуулах (batch — өдрийн хаалтад)
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION loan.fn_accrue_interest(p_as_of DATE DEFAULT CURRENT_DATE)
RETURNS INTEGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_count INTEGER := 0;
    r       RECORD;
    v_int   NUMERIC;
BEGIN
    FOR r IN
        SELECT l.id, l.principal_outstanding, l.interest_rate, l.day_count_convention, l.is_npl
          FROM loan.loans l
          JOIN loan.loan_statuses st ON st.id = l.status_id
         WHERE st.accrues_interest
           AND l.principal_outstanding > 0
           AND l.deleted_at IS NULL
           AND NOT EXISTS (SELECT 1 FROM loan.loan_interest_accruals a
                            WHERE a.loan_id = l.id AND a.accrual_date = p_as_of)
    LOOP
        v_int := round(r.principal_outstanding * r.interest_rate / 100
                       * system.fn_day_count_factor(p_as_of - 1, p_as_of, r.day_count_convention), 4);

        INSERT INTO loan.loan_interest_accruals (
            loan_id, accrual_date, principal_base, annual_rate, interest_amount, is_suspended
        ) VALUES (
            r.id, p_as_of, r.principal_outstanding, r.interest_rate, v_int, r.is_npl
        );
        v_count := v_count + 1;
    END LOOP;
    RETURN v_count;
END;
$$;

COMMENT ON FUNCTION loan.fn_accrue_interest(DATE) IS
    'Өдрийн хүүг зээл тус бүрд бүртгэнэ. NPL зээлийн хуримтлалыг is_suspended = TRUE гэж тэмдэглэнэ '
    '(орлогод хүлээн зөвшөөрөхгүй, харин нэхэмжлэх эрх хэвээр)';

-- ---------------------------------------------------------------------
-- 7. DPD-ийн өдрийн агшин бичих (batch)
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION loan.fn_snapshot_overdues(p_as_of DATE DEFAULT CURRENT_DATE)
RETURNS INTEGER
LANGUAGE plpgsql
AS $$
DECLARE
    v_count INTEGER := 0;
BEGIN
    INSERT INTO loan.loan_overdues (
        loan_id, snapshot_date, days_past_due, dpd_bucket,
        overdue_principal, overdue_interest, overdue_penalty, overdue_fee,
        total_outstanding, oldest_due_date, classification_id
    )
    SELECT l.id, p_as_of, l.days_past_due, loan.fn_dpd_bucket(l.days_past_due),
           l.overdue_principal, l.overdue_interest, l.penalty_outstanding, l.fee_outstanding,
           l.total_outstanding,
           (SELECT MIN(si.due_date) FROM loan.loan_schedule_items si
             JOIN loan.loan_schedules s ON s.id = si.schedule_id AND s.is_current
            WHERE si.loan_id = l.id AND si.status IN ('PENDING', 'PARTIALLY_PAID', 'OVERDUE')),
           l.classification_id
      FROM loan.loans l
      JOIN loan.loan_statuses st ON st.id = l.status_id
     WHERE st.is_active_loan AND l.deleted_at IS NULL
    ON CONFLICT (loan_id, snapshot_date) DO NOTHING;

    GET DIAGNOSTICS v_count = ROW_COUNT;
    RETURN v_count;
END;
$$;
