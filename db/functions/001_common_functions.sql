-- =====================================================================
-- functions/001_common_functions.sql — Нийтлэг функцүүд
-- Ажиллуулах дараалал: migrations/001..015 → functions/* → triggers/* → views/* → 016_seed
-- =====================================================================

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------------
-- Бизнес дугаар олгох (LN-2026-00000123). Атом: FOR UPDATE түгжээ.
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION system.fn_next_number(
    p_sequence_code system.code_key,
    p_branch_code   VARCHAR DEFAULT NULL
)
RETURNS VARCHAR
LANGUAGE plpgsql
AS $$
DECLARE
    v_seq    system.number_sequences%ROWTYPE;
    v_next   BIGINT;
    v_result VARCHAR;
    v_today  DATE := CURRENT_DATE;
BEGIN
    SELECT * INTO v_seq FROM system.number_sequences
     WHERE sequence_code = p_sequence_code FOR UPDATE;

    IF NOT FOUND THEN
        RAISE EXCEPTION 'Дугаарлалтын тохиргоо олдсонгүй: %', p_sequence_code
            USING ERRCODE = 'no_data_found';
    END IF;

    -- Тохиргоонд заасан үечлэлээр тоолуурыг тэглэнэ
    IF v_seq.reset_period <> 'NEVER' AND v_seq.last_reset_at IS NOT NULL THEN
        IF (v_seq.reset_period = 'YEARLY'  AND date_trunc('year',  v_today) > date_trunc('year',  v_seq.last_reset_at))
        OR (v_seq.reset_period = 'MONTHLY' AND date_trunc('month', v_today) > date_trunc('month', v_seq.last_reset_at))
        OR (v_seq.reset_period = 'DAILY'   AND v_today > v_seq.last_reset_at) THEN
            v_seq.current_value := 0;
        END IF;
    END IF;

    v_next := v_seq.current_value + v_seq.increment_by;

    UPDATE system.number_sequences
       SET current_value = v_next,
           last_reset_at = v_today
     WHERE id = v_seq.id;

    v_result := v_seq.prefix;
    IF v_seq.include_year THEN
        v_result := v_result || to_char(v_today, 'YYYY') || '-';
    END IF;
    IF v_seq.include_branch AND p_branch_code IS NOT NULL THEN
        v_result := v_result || p_branch_code || '-';
    END IF;
    RETURN v_result || lpad(v_next::TEXT, v_seq.padding_length, '0') || v_seq.suffix;
END;
$$;

COMMENT ON FUNCTION system.fn_next_number(system.code_key, VARCHAR) IS
    'Бизнес дугаар атомаар олгоно. Партицлагдсан хүснэгтэд глобал давхардлаас хамгаална';

-- ---------------------------------------------------------------------
-- Аудитын trigger функц — бүх хянагдах хүснэгтэд нэг л энэ функц
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION system.fn_audit()
RETURNS TRIGGER
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = system, pg_catalog
AS $$
DECLARE
    v_old      JSONB;
    v_new      JSONB;
    v_pk       TEXT;
    v_changed  TEXT[];
    v_action   system.audit_action;
BEGIN
    IF TG_OP = 'INSERT' THEN
        v_action := 'INSERT';
        v_new := to_jsonb(NEW);
    ELSIF TG_OP = 'UPDATE' THEN
        v_action := 'UPDATE';
        v_old := to_jsonb(OLD);
        v_new := to_jsonb(NEW);
        -- Зөвхөн ӨӨРЧЛӨГДСӨН талбаруудыг тэмдэглэнэ (лог хэмнэнэ)
        SELECT array_agg(key ORDER BY key) INTO v_changed
          FROM jsonb_each(v_new) n
         WHERE n.value IS DISTINCT FROM v_old -> n.key;
        IF v_changed IS NULL OR v_changed = ARRAY['updated_at']::TEXT[] THEN
            RETURN NULL;   -- зөвхөн updated_at өөрчлөгдсөн бол лог бичихгүй
        END IF;
    ELSE
        v_action := 'DELETE';
        v_old := to_jsonb(OLD);
    END IF;

    v_pk := COALESCE(
        (CASE WHEN TG_OP = 'DELETE' THEN v_old ELSE v_new END) ->> 'id',
        (CASE WHEN TG_OP = 'DELETE' THEN v_old ELSE v_new END) ->> 'customer_id',
        '?'
    );

    INSERT INTO system.audit_logs (
        schema_name, table_name, record_pk, action, user_id, ip_address,
        session_id, application, reason, old_values, new_values, changed_fields
    ) VALUES (
        TG_TABLE_SCHEMA, TG_TABLE_NAME, v_pk, v_action,
        NULLIF(current_setting('app.user_id', TRUE), '')::BIGINT,
        NULLIF(current_setting('app.ip_address', TRUE), '')::INET,
        NULLIF(current_setting('app.session_id', TRUE), ''),
        current_setting('application_name', TRUE),
        NULLIF(current_setting('app.reason', TRUE), ''),
        v_old, v_new, v_changed
    );
    RETURN NULL;   -- AFTER trigger
END;
$$;

COMMENT ON FUNCTION system.fn_audit() IS
    'Хянагдах хүснэгт бүрд AFTER INSERT/UPDATE/DELETE. Хэрэглэгч, IP, session-ийг '
    'app.* session хувьсагчаас авна (system.fn_set_session_context)';

-- ---------------------------------------------------------------------
-- Валют хөрвүүлэлт — тухайн өдрийн буюу түүнээс өмнөх сүүлийн ханшаар
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION system.fn_exchange_rate(
    p_from    system.currency_code,
    p_to      system.currency_code,
    p_date    DATE DEFAULT CURRENT_DATE
)
RETURNS system.fx_rate
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    v_rate system.fx_rate;
BEGIN
    IF p_from = p_to THEN
        RETURN 1;
    END IF;

    SELECT mid_rate INTO v_rate
      FROM system.exchange_rates
     WHERE from_currency = p_from AND to_currency = p_to
       AND rate_date <= p_date AND is_active
     ORDER BY rate_date DESC
     LIMIT 1;

    IF v_rate IS NOT NULL THEN
        RETURN v_rate;
    END IF;

    -- Урвуу чиглэлээр бүртгэсэн байж болно
    SELECT 1 / mid_rate INTO v_rate
      FROM system.exchange_rates
     WHERE from_currency = p_to AND to_currency = p_from
       AND rate_date <= p_date AND is_active
     ORDER BY rate_date DESC
     LIMIT 1;

    IF v_rate IS NULL THEN
        RAISE EXCEPTION 'Ханш олдсонгүй: % → % (% өдөр)', p_from, p_to, p_date
            USING ERRCODE = 'no_data_found';
    END IF;
    RETURN v_rate;
END;
$$;

CREATE OR REPLACE FUNCTION system.fn_convert_amount(
    p_amount  system.money_amount,
    p_from    system.currency_code,
    p_to      system.currency_code,
    p_date    DATE DEFAULT CURRENT_DATE
)
RETURNS system.money_amount
LANGUAGE sql
STABLE
AS $$
    SELECT round(p_amount * system.fn_exchange_rate(p_from, p_to, p_date), 4)::system.money_amount;
$$;

-- ---------------------------------------------------------------------
-- Ажлын өдөр — амралтын өдөр таарвал дараагийн ажлын өдөрт шилжүүлнэ
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION system.fn_next_business_day(p_date DATE)
RETURNS DATE
LANGUAGE plpgsql
STABLE
AS $$
DECLARE
    v_date DATE := p_date;
    v_i    INT  := 0;
BEGIN
    WHILE v_i < 15 LOOP
        IF EXISTS (SELECT 1 FROM system.business_calendar
                    WHERE calendar_date = v_date AND is_business_day) THEN
            RETURN v_date;
        END IF;
        -- Хуанли бөглөгдөөгүй бол долоо хоногийн өдрөөр шийднэ
        IF NOT EXISTS (SELECT 1 FROM system.business_calendar WHERE calendar_date = v_date)
           AND EXTRACT(ISODOW FROM v_date) < 6 THEN
            RETURN v_date;
        END IF;
        v_date := v_date + 1;
        v_i := v_i + 1;
    END LOOP;
    RETURN v_date;
END;
$$;

-- ---------------------------------------------------------------------
-- Хоногийн тоо — тооцооны конвенцоор (хүү бодоход)
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION system.fn_day_count_factor(
    p_from       DATE,
    p_to         DATE,
    p_convention system.day_count_conv
)
RETURNS NUMERIC
LANGUAGE plpgsql
IMMUTABLE
AS $$
DECLARE
    v_days INTEGER := p_to - p_from;
BEGIN
    RETURN CASE p_convention
        WHEN 'ACT_365'  THEN v_days::NUMERIC / 365
        WHEN 'ACT_360'  THEN v_days::NUMERIC / 360
        WHEN 'D30_360'  THEN (
            (EXTRACT(YEAR FROM p_to) - EXTRACT(YEAR FROM p_from)) * 360
          + (EXTRACT(MONTH FROM p_to) - EXTRACT(MONTH FROM p_from)) * 30
          + (LEAST(EXTRACT(DAY FROM p_to), 30) - LEAST(EXTRACT(DAY FROM p_from), 30))
        )::NUMERIC / 360
        WHEN 'ACT_ACT'  THEN v_days::NUMERIC /
            CASE WHEN EXTRACT(YEAR FROM p_from)::INT % 4 = 0 THEN 366 ELSE 365 END
    END;
END;
$$;

COMMENT ON FUNCTION system.fn_day_count_factor(DATE, DATE, system.day_count_conv) IS
    'Хүүгийн хугацааны коэффициент. Хүү = үлдэгдэл × жилийн хүү% / 100 × энэ коэффициент';

-- ---------------------------------------------------------------------
-- Партиц автоматаар үүсгэх (жилийн эцэст cron-оор дуудна)
-- ---------------------------------------------------------------------
CREATE OR REPLACE FUNCTION system.fn_create_year_partition(
    p_schema TEXT,
    p_table  TEXT,
    p_year   INTEGER
)
RETURNS TEXT
LANGUAGE plpgsql
AS $$
DECLARE
    v_part  TEXT := format('%s_%s', p_table, p_year);
    v_from  TEXT := format('%s-01-01', p_year);
    v_to    TEXT := format('%s-01-01', p_year + 1);
BEGIN
    IF EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = p_schema AND c.relname = v_part) THEN
        RETURN format('%s.%s аль хэдийн байна', p_schema, v_part);
    END IF;

    EXECUTE format(
        'CREATE TABLE %I.%I PARTITION OF %I.%I FOR VALUES FROM (%L) TO (%L)',
        p_schema, v_part, p_schema, p_table, v_from, v_to
    );
    RETURN format('%s.%s үүсгэв', p_schema, v_part);
END;
$$;

COMMENT ON FUNCTION system.fn_create_year_partition(TEXT, TEXT, INTEGER) IS
    'Ирэх жилийн партицыг урьдчилан үүсгэнэ. DEFAULT партиц дүүрэхээс сэргийлнэ';
