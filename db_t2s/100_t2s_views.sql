-- =====================================================================
-- 100_t2s_views.sql — Text2SQL bot-д зориулсан "data mart" view давхарга
-- ---------------------------------------------------------------------
-- Зааврын 1-р хэсэг: "загварт түүхий warehouse биш, сонгож тайлбарласан view өг".
-- Зааврын 7-р хэсэг: регистр, утас, дансны дугаар, хаяг зэрэг хувийн мэдээллийг
-- view-д оруулахгүй; чөлөөт текст (notes, description, counterparty) оруулахгүй.
-- Бүх view нь t2s_reader эзэмшилд байж, суурь хүснэгтийн RLS-ээр салбараар шүүгдэнэ
-- (110_roles_rls.sql). PostgreSQL 14+.
-- =====================================================================

DROP SCHEMA IF EXISTS t2s CASCADE;   -- давтан ачаалахад цэвэр эхэлнэ
CREATE SCHEMA t2s;
COMMENT ON SCHEMA t2s IS 'Text2SQL bot-ийн харах цорын ганц схем: тайлбартай, хувийн мэдээлэлгүй view-ууд';

-- Системийн ажлын өдөр (өгөгдлийн "өнөөдөр")
CREATE OR REPLACE FUNCTION t2s.business_date()
RETURNS DATE LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT param_value::DATE FROM system.system_parameters
     WHERE param_code = 'CURRENT_BUSINESS_DATE' ORDER BY valid_from DESC LIMIT 1
$$;
COMMENT ON FUNCTION t2s.business_date() IS 'Системийн ажлын өдөр (өгөгдлийн "өнөөдөр")';
-- t2s.fx_to_mnt: SECURITY DEFINER — view-ийн доторх функц дуудагчийн эрхээр ажилладаг тул

-- Валютын ханш (ажлын өдөр буюу түүнээс өмнөх сүүлийн Монголбанкны дундаж ханш)
CREATE OR REPLACE FUNCTION t2s.fx_to_mnt(p_currency TEXT, p_date DATE)
RETURNS NUMERIC LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog AS $$
    SELECT CASE WHEN p_currency = 'MNT' THEN 1::NUMERIC ELSE
           (SELECT mid_rate FROM system.exchange_rates
             WHERE from_currency = p_currency AND to_currency = 'MNT' AND rate_date <= p_date AND is_active
             ORDER BY rate_date DESC LIMIT 1) END
$$;


-- ---------------------------------------------------------------------
-- Байгууллага
-- ---------------------------------------------------------------------
CREATE VIEW t2s.branches AS
SELECT b.id AS branch_id, b.branch_code, b.branch_name, b.branch_type, b.parent_branch_id,
       b.province, b.district, b.opened_date, b.closed_date, b.is_active
  FROM core.branches b
 WHERE b.deleted_at IS NULL;

CREATE VIEW t2s.employees AS
SELECT e.id AS employee_id, e.employee_no, e.first_name, e.last_name,
       e.last_name || ' ' || e.first_name AS full_name,
       e.branch_id, d.department_code, d.department_name, p.position_code, p.position_name,
       e.manager_id, e.hire_date, e.termination_date, e.employment_status
  FROM core.employees e
  LEFT JOIN core.departments d ON d.id = e.department_id
  LEFT JOIN core.positions p   ON p.id = e.position_id
 WHERE e.deleted_at IS NULL;

-- ---------------------------------------------------------------------
-- Харилцагч
-- ---------------------------------------------------------------------
CREATE VIEW t2s.customers AS
SELECT c.id AS customer_id, c.customer_number, c.display_name AS customer_name, c.party_type::TEXT AS party_type,
       ct.type_code AS customer_type_code, ct.type_name AS customer_type_name,
       cs.status_code, cs.status_name, seg.segment_code, seg.segment_name,
       c.branch_id, c.relationship_manager_id, c.kyc_status, c.aml_status, c.risk_level,
       c.is_pep, c.is_blacklisted, c.is_resident, c.country_code,
       (c.onboarded_at AT TIME ZONE 'Asia/Ulaanbaatar')::DATE AS onboarded_date,
       (c.closed_at AT TIME ZONE 'Asia/Ulaanbaatar')::DATE AS closed_date,
       a.province, a.district
  FROM customer.customers c
  JOIN customer.customer_types ct    ON ct.id = c.customer_type_id
  JOIN customer.customer_statuses cs ON cs.id = c.status_id
  LEFT JOIN customer.customer_segments seg ON seg.id = c.segment_id
  LEFT JOIN LATERAL (SELECT ca.province, ca.district FROM customer.customer_addresses ca
                      WHERE ca.customer_id = c.id AND ca.is_primary AND ca.deleted_at IS NULL
                      ORDER BY ca.id LIMIT 1) a ON TRUE
 WHERE c.deleted_at IS NULL;

CREATE VIEW t2s.individuals AS
SELECT i.customer_id, i.gender::TEXT AS gender, EXTRACT(YEAR FROM i.birth_date)::INT AS birth_year,
       date_part('year', age(t2s.business_date(), i.birth_date))::INT AS age,
       i.marital_status, i.dependants_count, i.education_level, i.occupation, i.employer_name,
       i.employment_type, i.monthly_income, i.income_currency
  FROM customer.customer_individuals i
  JOIN customer.customers c ON c.id = i.customer_id AND c.deleted_at IS NULL;

CREATE VIEW t2s.organizations AS
SELECT o.customer_id, o.legal_name AS org_name, o.legal_form, o.industry_code,
       CASE left(o.industry_code, 1)
            WHEN 'A' THEN 'Хөдөө аж ахуй' WHEN 'B' THEN 'Уул уурхай' WHEN 'C' THEN 'Боловсруулах үйлдвэрлэл'
            WHEN 'F' THEN 'Барилга' WHEN 'G' THEN 'Худалдаа' WHEN 'H' THEN 'Тээвэр, агуулах'
            WHEN 'I' THEN 'Зочид буудал, хоолны үйлчилгээ' WHEN 'J' THEN 'Мэдээлэл, холбоо'
            WHEN 'M' THEN 'Мэргэжлийн үйлчилгээ' WHEN 'P' THEN 'Боловсрол' WHEN 'Q' THEN 'Эрүүл мэнд'
            ELSE 'Бусад' END AS industry_sector,
       o.establishment_date, o.employee_count, o.annual_revenue, o.revenue_currency
  FROM customer.customer_organizations o
  JOIN customer.customers c ON c.id = o.customer_id AND c.deleted_at IS NULL;

-- ---------------------------------------------------------------------
-- Бүтээгдэхүүн
-- ---------------------------------------------------------------------
CREATE VIEW t2s.products AS
SELECT p.id AS product_id, p.product_code, p.product_name, pt.type_code AS product_type_code,
       pt.type_name AS product_type_name, pt.category, p.currency_code,
       p.amortization_type::TEXT AS amortization_type, p.requires_collateral, p.requires_guarantor,
       p.min_credit_score, p.max_dti_ratio, p.max_ltv_ratio, p.launch_date, p.discontinue_date, p.status,
       r.annual_rate, r.min_amount, r.max_amount, r.min_term_months, r.max_term_months
  FROM product.products p
  JOIN product.product_types pt ON pt.id = p.product_type_id
  LEFT JOIN LATERAL (SELECT pr.annual_rate, pr.min_amount, pr.max_amount, pr.min_term_months, pr.max_term_months
                       FROM product.product_rates pr
                      WHERE pr.product_id = p.id
                      ORDER BY pr.is_active DESC, pr.valid_from DESC LIMIT 1) r ON TRUE
 WHERE p.deleted_at IS NULL;

-- ---------------------------------------------------------------------
-- Зээлийн хүсэлт
-- ---------------------------------------------------------------------
CREATE VIEW t2s.loan_applications AS
SELECT a.id AS application_id, a.application_no, a.customer_id, a.product_id, a.branch_id, a.loan_officer_id,
       s.status_code, s.status_name, a.channel, a.requested_amount, a.requested_term_months, a.currency_code,
       a.purpose_category, a.declared_income, a.dti_ratio, a.approved_amount, a.approved_term_months,
       a.approved_rate, sc.score AS credit_score, d.decision_level,
       (a.submitted_at AT TIME ZONE 'Asia/Ulaanbaatar')::DATE AS submitted_date,
       (a.decided_at AT TIME ZONE 'Asia/Ulaanbaatar')::DATE AS decided_date,
       a.rejection_reason
  FROM loan.loan_applications a
  JOIN loan.loan_application_statuses s ON s.id = a.status_id
  LEFT JOIN LATERAL (SELECT x.score FROM risk.credit_scores x
                      WHERE x.application_id = a.id AND x.score_type = 'APPLICATION'
                      ORDER BY x.scored_at DESC LIMIT 1) sc ON TRUE
  LEFT JOIN LATERAL (SELECT ld.decision_level FROM loan.loan_decisions ld
                      WHERE ld.application_id = a.id AND ld.is_final
                      ORDER BY ld.decided_at DESC LIMIT 1) d ON TRUE
 WHERE a.deleted_at IS NULL;

-- ---------------------------------------------------------------------
-- Зээл (гол view)
-- ---------------------------------------------------------------------
CREATE VIEW t2s.loans AS
SELECT l.id AS loan_id, l.loan_number, l.application_id, l.customer_id, l.product_id, l.branch_id,
       l.loan_officer_id, ls.status_code, ls.status_name, ls.is_active_loan AS is_active_portfolio,
       l.currency_code, l.principal_amount, l.interest_rate, l.penalty_rate, l.effective_rate, l.term_months,
       l.amortization_type::TEXT AS amortization_type, l.contract_date, l.disbursement_date,
       l.first_payment_date, l.maturity_date, l.closed_date, l.disbursed_amount,
       l.principal_outstanding, l.interest_outstanding, l.penalty_outstanding, l.fee_outstanding,
       l.total_outstanding, l.principal_paid, l.interest_paid, l.penalty_paid, l.fee_paid,
       l.written_off_amount, l.overdue_principal, l.overdue_interest, l.days_past_due, l.max_days_past_due,
       CASE WHEN l.days_past_due <= 0 THEN 'CURRENT' WHEN l.days_past_due <= 30 THEN 'DPD_1_30'
            WHEN l.days_past_due <= 60 THEN 'DPD_31_60' WHEN l.days_past_due <= 90 THEN 'DPD_61_90'
            WHEN l.days_past_due <= 180 THEN 'DPD_91_180' ELSE 'DPD_180_PLUS' END AS dpd_bucket,
       cg.grade_code AS classification_code, cg.grade_name_mn AS classification_name, cg.provision_rate,
       l.is_npl, l.is_restructured, l.restructure_count, l.last_payment_date,
       fx.rate AS fx_rate_to_mnt,
       round(l.principal_amount * fx.rate, 2)      AS principal_amount_mnt,
       round(l.principal_outstanding * fx.rate, 2) AS principal_outstanding_mnt,
       round(l.total_outstanding * fx.rate, 2)     AS total_outstanding_mnt
  FROM loan.loans l
  JOIN loan.loan_statuses ls ON ls.id = l.status_id
  LEFT JOIN loan.classification_grades cg ON cg.id = l.classification_id
  CROSS JOIN LATERAL (SELECT COALESCE(t2s.fx_to_mnt(l.currency_code, t2s.business_date()), 1) AS rate) fx
 WHERE l.deleted_at IS NULL;

CREATE VIEW t2s.loan_schedule AS
SELECT si.loan_id, si.installment_no, si.due_date, si.principal_due, si.interest_due, si.total_due,
       si.principal_paid, si.interest_paid, si.penalty_charged, si.penalty_paid, si.status, si.paid_date,
       si.days_past_due, s.schedule_version
  FROM loan.loan_schedule_items si
  JOIN loan.loan_schedules s ON s.id = si.schedule_id AND s.is_current
  JOIN loan.loans l ON l.id = si.loan_id AND l.deleted_at IS NULL;

CREATE VIEW t2s.loan_payments AS
SELECT p.id AS payment_id, p.payment_no, p.loan_id, p.customer_id, p.branch_id, p.currency_code, p.amount,
       p.payment_date, p.payment_method, p.payment_type, p.allocated_principal, p.allocated_interest,
       p.allocated_penalty, p.allocated_fee, p.unallocated_amount, p.status
  FROM loan.loan_payments p
  JOIN loan.loans l ON l.id = p.loan_id AND l.deleted_at IS NULL;

CREATE VIEW t2s.loan_dpd_history AS
SELECT o.loan_id, o.snapshot_date, o.days_past_due, o.dpd_bucket, o.overdue_principal, o.overdue_interest,
       o.overdue_penalty, o.total_outstanding, cg.grade_code AS classification_code
  FROM loan.loan_overdues o
  JOIN loan.loans l ON l.id = o.loan_id AND l.deleted_at IS NULL
  LEFT JOIN loan.classification_grades cg ON cg.id = o.classification_id;

CREATE VIEW t2s.loan_charges AS
SELECT f.loan_id, 'FEE'::TEXT AS charge_type, ft.fee_code AS charge_code, ft.fee_name AS charge_name,
       f.currency_code, f.amount, f.charged_date, f.paid_amount, f.waived_amount, f.status, NULL::INT AS days_overdue
  FROM loan.loan_fees f
  JOIN product.fee_types ft ON ft.id = f.fee_type_id
  JOIN loan.loans l ON l.id = f.loan_id AND l.deleted_at IS NULL
UNION ALL
SELECT pn.loan_id, 'PENALTY', pn.penalty_type, 'Хугацаа хэтрүүлсний торгууль', pn.currency_code, pn.amount,
       pn.charged_date, pn.paid_amount, pn.waived_amount, pn.status, pn.days_overdue
  FROM loan.loan_penalties pn
  JOIN loan.loans l ON l.id = pn.loan_id AND l.deleted_at IS NULL;

CREATE VIEW t2s.loan_restructures AS
SELECT r.loan_id, r.restructure_no, r.restructure_type, r.reason, r.old_principal, r.new_principal,
       r.old_rate, r.new_rate, r.old_term_months, r.new_term_months, r.old_maturity_date, r.new_maturity_date,
       r.effective_date, r.status
  FROM loan.loan_restructures r
  JOIN loan.loans l ON l.id = r.loan_id AND l.deleted_at IS NULL;

CREATE VIEW t2s.loan_writeoffs AS
SELECT w.loan_id, w.writeoff_date, w.principal_amount, w.interest_amount, w.penalty_amount, w.fee_amount,
       w.total_amount, w.writeoff_type, w.recovered_amount
  FROM loan.loan_writeoffs w
  JOIN loan.loans l ON l.id = w.loan_id AND l.deleted_at IS NULL;


-- ---------------------------------------------------------------------
-- Барьцаа, батлан даагч
-- ---------------------------------------------------------------------
CREATE VIEW t2s.collaterals AS
SELECT c.id AS collateral_id, c.collateral_number, ct.type_code AS collateral_type_code,
       ct.type_name AS collateral_type_name, ct.category AS collateral_category, c.owner_customer_id,
       c.branch_id, c.market_value, c.forced_sale_value, c.accepted_value, c.currency_code,
       c.valuation_date, c.next_valuation_date, c.is_insured, c.encumbrance_status, c.status
  FROM collateral.collaterals c
  JOIN collateral.collateral_types ct ON ct.id = c.collateral_type_id
 WHERE c.deleted_at IS NULL;

CREATE VIEW t2s.loan_collaterals AS
SELECT lc.loan_id, lc.collateral_id, lc.currency_code, lc.allocated_amount, lc.ltv_ratio, lc.pledge_date,
       lc.release_date, lc.status
  FROM collateral.loan_collaterals lc
  JOIN loan.loans l ON l.id = lc.loan_id AND l.deleted_at IS NULL;

CREATE VIEW t2s.loan_guarantors AS
SELECT lg.loan_id, lg.guarantor_id, g.guarantor_type, (g.customer_id IS NOT NULL) AS is_existing_customer,
       g.customer_id AS guarantor_customer_id, lg.currency_code, lg.guarantee_amount, lg.guarantee_pct,
       lg.liability_type, lg.valid_from, lg.valid_to, lg.called_date, lg.called_amount, lg.recovered_amount,
       lg.status
  FROM collateral.loan_guarantors lg
  JOIN collateral.guarantors g ON g.id = lg.guarantor_id
  JOIN loan.loans l ON l.id = lg.loan_id AND l.deleted_at IS NULL;

-- ---------------------------------------------------------------------
-- Данс, гүйлгээ, хадгаламж (дансны дугаар, нэр, харьцагч талын мэдээлэл ОРОХГҮЙ)
-- ---------------------------------------------------------------------
CREATE VIEW t2s.accounts AS
SELECT a.id AS account_id, a.customer_id, a.branch_id, a.product_id, at.type_code AS account_type_code,
       at.type_name AS account_type_name, a.currency_code, a.current_balance, a.available_balance, a.hold_amount,
       a.status, a.is_dormant, a.opened_date, a.closed_date,
       (a.last_transaction_at AT TIME ZONE 'Asia/Ulaanbaatar')::DATE AS last_transaction_date
  FROM account.accounts a
  JOIN account.account_types at ON at.id = a.account_type_id
 WHERE a.customer_id IS NOT NULL AND a.deleted_at IS NULL;

CREATE VIEW t2s.transactions AS
SELECT t.id AS transaction_id, t.transaction_no, t.account_id, t.customer_id, t.branch_id,
       tt.type_code AS transaction_type_code, tt.type_name AS transaction_type_name, tt.category,
       ch.channel_code, ch.channel_name, ts.status_code, t.direction::TEXT AS direction, t.amount, t.fee_amount,
       t.currency_code, t.base_amount AS amount_mnt, t.transaction_date, t.value_date
  FROM account.transactions t
  JOIN account.accounts a              ON a.id = t.account_id AND a.customer_id IS NOT NULL
  JOIN account.transaction_types tt    ON tt.id = t.transaction_type_id
  JOIN account.transaction_channels ch ON ch.id = t.channel_id
  JOIN account.transaction_statuses ts ON ts.id = t.status_id;

CREATE VIEW t2s.account_balances_monthly AS
SELECT b.account_id, b.balance_date, b.opening_balance, b.total_debit, b.total_credit, b.closing_balance,
       b.transaction_count, b.currency_code, b.base_closing_balance AS closing_balance_mnt
  FROM account.account_balances b
  JOIN account.accounts a ON a.id = b.account_id AND a.customer_id IS NOT NULL;

CREATE VIEW t2s.deposits AS
SELECT d.id AS deposit_id, d.deposit_number, d.account_id, d.customer_id, d.product_id, d.branch_id,
       d.currency_code, d.deposit_type, d.principal_amount, d.current_balance, d.interest_rate,
       d.interest_method::TEXT AS interest_method, d.term_months, d.accrued_interest, d.paid_interest,
       d.withholding_tax_paid, d.opened_date, d.maturity_date, d.closed_date, d.auto_renew, d.renewal_count,
       d.is_pledged, d.status,
       round(d.current_balance * COALESCE(t2s.fx_to_mnt(d.currency_code, t2s.business_date()), 1), 2)
           AS current_balance_mnt
  FROM deposit.deposit_accounts d
 WHERE d.deleted_at IS NULL;

-- ---------------------------------------------------------------------
-- Барагдуулалт
-- ---------------------------------------------------------------------
CREATE VIEW t2s.collection_cases AS
SELECT cc.id AS case_id, cc.case_no, cc.loan_id, cc.customer_id, cc.branch_id, st.stage_code, st.stage_name,
       cc.currency_code, cc.opened_date, cc.dpd_at_open, cc.current_dpd, cc.outstanding_at_open,
       cc.current_outstanding, cc.overdue_amount, cc.collected_amount, cc.assigned_to,
       ag.agency_code, ag.agency_name, cc.priority, cc.status, cc.closed_date, cc.close_reason
  FROM collection.collection_cases cc
  JOIN collection.collection_stages st ON st.id = cc.stage_id
  LEFT JOIN collection.collection_agencies ag ON ag.id = cc.agency_id;

CREATE VIEW t2s.collection_actions AS
SELECT ca.id AS action_id, ca.case_id, at.action_code, at.action_name, at.channel, ca.action_date,
       ca.performed_by, ag.agency_code, ca.contact_result, ca.cost_amount
  FROM collection.collection_actions ca
  JOIN collection.collection_cases cc ON cc.id = ca.case_id
  JOIN collection.action_types at ON at.id = ca.action_type_id
  LEFT JOIN collection.collection_agencies ag ON ag.id = ca.agency_id;

CREATE VIEW t2s.collection_promises AS
SELECT cp.id AS promise_id, cp.case_id, cp.currency_code, cp.promised_amount, cp.promised_date,
       cp.paid_amount, cp.paid_date, cp.status
  FROM collection.collection_promises cp
  JOIN collection.collection_cases cc ON cc.id = cp.case_id;

-- ---------------------------------------------------------------------
-- Эрсдэл, нийцэл
-- ---------------------------------------------------------------------
CREATE VIEW t2s.credit_scores AS
SELECT s.id AS score_id, s.customer_id, s.application_id, m.model_code, m.model_name, s.score,
       rg.grade_code AS risk_grade_code, rg.grade_name AS risk_grade_name, s.pd_estimate, s.decision_hint,
       s.score_type, (s.scored_at AT TIME ZONE 'Asia/Ulaanbaatar')::DATE AS scored_date
  FROM risk.credit_scores s
  JOIN customer.customers c ON c.id = s.customer_id
  JOIN risk.credit_score_models m ON m.id = s.model_id
  LEFT JOIN risk.risk_grades rg ON rg.id = s.risk_grade_id;

CREATE VIEW t2s.aml_alerts AS
SELECT al.id AS alert_id, al.alert_no, r.rule_code, r.rule_name, al.customer_id, al.alert_amount,
       al.currency_code, al.severity, al.status,
       (al.detected_at AT TIME ZONE 'Asia/Ulaanbaatar')::DATE AS detected_date,
       (al.closed_at AT TIME ZONE 'Asia/Ulaanbaatar')::DATE AS closed_date, al.str_reported
  FROM kyc.aml_alerts al
  JOIN kyc.aml_rules r ON r.id = al.rule_id
  JOIN customer.customers c ON c.id = al.customer_id;


-- ---------------------------------------------------------------------
-- Нягтлан бодох бүртгэл, ханш
-- ---------------------------------------------------------------------

CREATE VIEW t2s.gl_entries AS
SELECT je.entry_no, jl.line_no, jl.posting_date, ap.period_code, ap.fiscal_year, jl.branch_id,
       j.journal_code, j.journal_name, coa.account_code, coa.account_name_mn, coa.account_type::TEXT AS account_type,
       jl.dr_cr::TEXT AS dr_cr, jl.amount, jl.currency_code, jl.base_amount AS amount_mnt, je.source_system,
       je.status AS entry_status
  FROM accounting.journal_lines jl
  JOIN accounting.journal_entries je    ON je.id = jl.entry_id
  JOIN accounting.journals j            ON j.id = je.journal_id
  JOIN accounting.accounting_periods ap ON ap.id = je.period_id
  JOIN accounting.chart_of_accounts coa ON coa.id = jl.gl_account_id;

CREATE VIEW t2s.exchange_rates AS
SELECT from_currency AS currency_code, rate_date, buy_rate, sell_rate, mid_rate
  FROM system.exchange_rates
 WHERE to_currency = 'MNT' AND is_active AND source = 'CENTRAL_BANK';

-- ---------------------------------------------------------------------
-- Тайлбар (COMMENT) — semantic_model.yaml-ийн нөөц эх сурвалж
-- ---------------------------------------------------------------------
COMMENT ON VIEW t2s.branches IS 'Салбар (төв оффис, салбар, тооцооны төв, дижитал салбар)';
COMMENT ON VIEW t2s.employees IS 'Ажилтан: зээлийн мэргэжилтэн, салбарын менежер, кассчин, барагдуулагч гэх мэт';
COMMENT ON VIEW t2s.customers IS 'Харилцагч (хувь хүн ба байгууллага) — регистр, утас, хаяг ОРОХГҮЙ';
COMMENT ON VIEW t2s.individuals IS 'Хувь хүн харилцагчийн нэмэлт мэдээлэл (нас, хүйс, ажил, орлого)';
COMMENT ON VIEW t2s.organizations IS 'Байгууллага харилцагчийн нэмэлт мэдээлэл (салбар, хэлбэр, орлого)';
COMMENT ON VIEW t2s.products IS 'Зээл, хадгаламж, харилцах дансны бүтээгдэхүүн ба одоогийн хүү';
COMMENT ON VIEW t2s.loan_applications IS 'Зээлийн хүсэлт ба шийдвэр';
COMMENT ON VIEW t2s.loans IS 'Зээл: нөхцөл, үлдэгдэл, хугацаа хэтрэлт (DPD), активын ангилал';
COMMENT ON VIEW t2s.loan_schedule IS 'Зээлийн эргэн төлөлтийн хуваарь (одоогийн хувилбар)';
COMMENT ON VIEW t2s.loan_payments IS 'Зээлийн эргэн төлөлт (төлбөр) ба хуваарилалт';
COMMENT ON VIEW t2s.loan_dpd_history IS 'Зээлийн хугацаа хэтрэлтийн сарын эцсийн агшин (PAR, чиг хандлага)';
COMMENT ON VIEW t2s.loan_charges IS 'Зээлийн шимтгэл ба хугацаа хэтрүүлсний торгууль';
COMMENT ON VIEW t2s.loan_restructures IS 'Зээлийн бүтцийн өөрчлөлт';
COMMENT ON VIEW t2s.loan_writeoffs IS 'Данснаас хассан зээл';
COMMENT ON VIEW t2s.collaterals IS 'Барьцаа хөрөнгө ба үнэлгээ';
COMMENT ON VIEW t2s.loan_collaterals IS 'Зээл ба барьцааны холбоос (хуваарилсан дүн, LTV)';
COMMENT ON VIEW t2s.loan_guarantors IS 'Зээлийн батлан даагч (нэр, регистр ОРОХГҮЙ)';
COMMENT ON VIEW t2s.accounts IS 'Харилцагчийн данс (харилцах, хадгаламжийн) — дансны дугаар ОРОХГҮЙ';
COMMENT ON VIEW t2s.transactions IS 'Дансны гүйлгээ (харьцагч талын мэдээлэл ОРОХГҮЙ)';
COMMENT ON VIEW t2s.account_balances_monthly IS 'Дансны сарын эцсийн үлдэгдэл';
COMMENT ON VIEW t2s.deposits IS 'Хадгаламж (хугацаатай, хугацаагүй, хүүхдийн)';
COMMENT ON VIEW t2s.collection_cases IS 'Хугацаа хэтэрсэн зээлийн барагдуулалтын кейс';
COMMENT ON VIEW t2s.collection_actions IS 'Барагдуулалтын үйлдэл (СМС, дуудлага, айлчлал, шүүх)';
COMMENT ON VIEW t2s.collection_promises IS 'Төлөх амлалт (PTP) ба биелэлт';
COMMENT ON VIEW t2s.credit_scores IS 'Зээлийн скорингийн оноо ба эрсдэлийн зэрэглэл';
COMMENT ON VIEW t2s.aml_alerts IS 'Сэжигтэй гүйлгээний (AML) сэрэмжлүүлэг';
COMMENT ON VIEW t2s.gl_entries IS 'Ерөнхий дэвтрийн бичилт (журналын мөр), зөвхөн батлагдсан';
COMMENT ON VIEW t2s.exchange_rates IS 'Монголбанкны валютын ханш (төгрөгт)';
