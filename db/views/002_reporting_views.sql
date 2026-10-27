-- =====================================================================
-- views/002_reporting_views.sql — Тайлан, BI-д зориулсан нэгтгэсэн view
-- ---------------------------------------------------------------------
-- Эдгээр view нь BI хэрэгсэл (Metabase, Power BI, Superset) шууд холбогдох
-- давхарга. Хүнд нэгтгэлүүд (v_par_analysis, v_branch_performance) нь
-- эзэлхүүн өссөн үед MATERIALIZED VIEW болгож REFRESH хийхэд бэлэн —
-- шаардлагатай өөрчлөлт нь зөвхөн CREATE MATERIALIZED VIEW болгох.
-- =====================================================================

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------------
-- Портфелийн үндсэн үзүүлэлт
-- ---------------------------------------------------------------------
CREATE OR REPLACE VIEW reporting.v_portfolio_summary AS
SELECT b.branch_code,
       b.branch_name,
       p.product_code,
       p.product_name,
       l.currency_code,
       count(*)                                    AS loan_count,
       count(DISTINCT l.customer_id)               AS customer_count,
       SUM(l.principal_amount)                     AS total_disbursed,
       SUM(l.principal_outstanding)                AS principal_outstanding,
       SUM(l.interest_outstanding)                 AS interest_outstanding,
       SUM(l.total_outstanding)                    AS total_outstanding,
       SUM(l.overdue_principal + l.overdue_interest) AS overdue_amount,
       count(*) FILTER (WHERE l.days_past_due > 0)   AS overdue_count,
       count(*) FILTER (WHERE l.is_npl)              AS npl_count,
       SUM(l.total_outstanding) FILTER (WHERE l.is_npl) AS npl_amount,
       round(AVG(l.interest_rate), 2)              AS avg_interest_rate,
       round(AVG(l.days_past_due), 1)              AS avg_dpd
  FROM loan.loans l
  JOIN loan.loan_statuses ls ON ls.id = l.status_id AND ls.is_active_loan
  JOIN core.branches b       ON b.id = l.branch_id
  JOIN product.products p    ON p.id = l.product_id
 WHERE l.deleted_at IS NULL
 GROUP BY b.branch_code, b.branch_name, p.product_code, p.product_name, l.currency_code;

-- PAR (Portfolio at Risk) — DPD хувиарлалт
CREATE OR REPLACE VIEW reporting.v_par_analysis AS
WITH portfolio AS (
    SELECT l.branch_id, l.product_id,
           loan.fn_dpd_bucket(l.days_past_due) AS bucket,
           l.total_outstanding, l.days_past_due
      FROM loan.loans l
      JOIN loan.loan_statuses ls ON ls.id = l.status_id AND ls.is_active_loan
     WHERE l.deleted_at IS NULL
), totals AS (
    SELECT branch_id,
           SUM(total_outstanding)                                    AS branch_total,
           SUM(total_outstanding) FILTER (WHERE days_past_due > 30)   AS par30,
           SUM(total_outstanding) FILTER (WHERE days_past_due > 90)   AS par90
      FROM portfolio GROUP BY branch_id
)
SELECT b.branch_code,
       b.branch_name,
       pr.bucket                                   AS dpd_bucket,
       count(*)                                    AS loan_count,
       SUM(pr.total_outstanding)                   AS outstanding,
       t.branch_total,
       round(100.0 * SUM(pr.total_outstanding) / NULLIF(t.branch_total, 0), 2) AS pct_of_portfolio,
       COALESCE(t.par30, 0)                        AS par30_amount,
       COALESCE(t.par90, 0)                        AS par90_amount,
       round(100.0 * COALESCE(t.par30, 0) / NULLIF(t.branch_total, 0), 2) AS par30_ratio,
       round(100.0 * COALESCE(t.par90, 0) / NULLIF(t.branch_total, 0), 2) AS par90_ratio
  FROM portfolio pr
  JOIN core.branches b ON b.id = pr.branch_id
  JOIN totals t        ON t.branch_id = pr.branch_id
 GROUP BY b.branch_code, b.branch_name, pr.bucket, t.branch_total, t.par30, t.par90;

COMMENT ON VIEW reporting.v_par_analysis IS
    'PAR30 / PAR90 — салбар тус бүрээр. Микрофинансын салбарын гол чанарын үзүүлэлт';

-- NPL ба эрсдэлийн сан
CREATE OR REPLACE VIEW reporting.v_npl_summary AS
SELECT b.branch_code,
       b.branch_name,
       cg.grade_code,
       cg.grade_name_mn,
       cg.is_npl,
       cg.provision_rate,
       count(*)                                                       AS loan_count,
       SUM(l.total_outstanding)                                       AS outstanding,
       round(SUM(l.total_outstanding * cg.provision_rate / 100), 2)    AS required_provision,
       round(100.0 * SUM(l.total_outstanding)
             / NULLIF(SUM(SUM(l.total_outstanding)) OVER (PARTITION BY b.branch_code), 0), 2) AS pct_of_branch
  FROM loan.loans l
  JOIN loan.loan_statuses ls ON ls.id = l.status_id AND ls.is_active_loan
  JOIN loan.classification_grades cg ON cg.id = l.classification_id
  JOIN core.branches b ON b.id = l.branch_id
 WHERE l.deleted_at IS NULL
 GROUP BY b.branch_code, b.branch_name, cg.grade_code, cg.grade_name_mn, cg.is_npl, cg.provision_rate;

-- Сарын олголт
CREATE OR REPLACE VIEW reporting.v_monthly_disbursement AS
SELECT date_trunc('month', d.disbursement_date)::DATE AS month,
       b.branch_code,
       b.branch_name,
       p.product_code,
       p.product_name,
       count(DISTINCT d.loan_id) AS loan_count,
       SUM(d.amount)             AS disbursed_amount,
       round(AVG(d.amount), 2)   AS avg_loan_size
  FROM loan.loan_disbursements d
  JOIN loan.loans l       ON l.id = d.loan_id
  JOIN core.branches b    ON b.id = l.branch_id
  JOIN product.products p ON p.id = l.product_id
 WHERE d.status = 'EXECUTED'
 GROUP BY 1, 2, 3, 4, 5;

-- Сарын эргэн төлөлт
CREATE OR REPLACE VIEW reporting.v_monthly_repayment AS
SELECT date_trunc('month', pay.payment_date)::DATE AS month,
       b.branch_code,
       b.branch_name,
       p.product_code,
       count(*)                        AS payment_count,
       SUM(pay.amount)                 AS total_collected,
       SUM(pay.allocated_principal)    AS principal_collected,
       SUM(pay.allocated_interest)     AS interest_collected,
       SUM(pay.allocated_penalty)      AS penalty_collected,
       SUM(pay.allocated_fee)          AS fee_collected
  FROM loan.loan_payments pay
  JOIN loan.loans l       ON l.id = pay.loan_id
  JOIN core.branches b    ON b.id = l.branch_id
  JOIN product.products p ON p.id = l.product_id
 WHERE pay.status = 'POSTED'
 GROUP BY 1, 2, 3, 4;

-- Орлогын нэгтгэл (нягтлангаас — жинхэнэ санхүүгийн эх сурвалж)
CREATE OR REPLACE VIEW reporting.v_income_summary AS
SELECT ap.period_code,
       ap.fiscal_year,
       b.branch_code,
       coa.account_code,
       coa.account_name_mn,
       SUM(CASE WHEN jl.dr_cr = 'C' THEN jl.base_amount ELSE -jl.base_amount END) AS net_income
  FROM accounting.journal_lines jl
  JOIN accounting.journal_entries je ON je.id = jl.entry_id AND je.status = 'POSTED'
  JOIN accounting.accounting_periods ap ON ap.id = je.period_id
  JOIN accounting.chart_of_accounts coa ON coa.id = jl.gl_account_id
  LEFT JOIN core.branches b ON b.id = jl.branch_id
 WHERE coa.account_type = 'INCOME'
 GROUP BY ap.period_code, ap.fiscal_year, b.branch_code, coa.account_code, coa.account_name_mn;

-- Хадгаламжийн портфель
CREATE OR REPLACE VIEW reporting.v_deposit_portfolio AS
SELECT b.branch_code,
       b.branch_name,
       da.deposit_type,
       da.currency_code,
       count(*)                       AS deposit_count,
       count(DISTINCT da.customer_id) AS customer_count,
       SUM(da.current_balance)        AS total_balance,
       SUM(da.accrued_interest)       AS accrued_interest,
       round(AVG(da.interest_rate), 2) AS avg_rate,
       count(*) FILTER (WHERE da.maturity_date <= CURRENT_DATE + 30) AS maturing_30d
  FROM deposit.deposit_accounts da
  JOIN core.branches b ON b.id = da.branch_id
 WHERE da.status = 'ACTIVE' AND da.deleted_at IS NULL
 GROUP BY 1, 2, 3, 4;

-- Өдрийн гүйлгээний хэмжээ
CREATE OR REPLACE VIEW reporting.v_daily_transaction_volume AS
SELECT t.value_date,
       b.branch_code,
       ch.channel_name,
       tt.category                    AS transaction_category,
       count(*)                       AS txn_count,
       SUM(t.amount)                  AS total_amount,
       SUM(t.base_amount)             AS total_base_amount,
       SUM(t.fee_amount)              AS total_fees,
       count(DISTINCT t.customer_id)  AS unique_customers
  FROM account.transactions t
  JOIN account.transaction_statuses ts ON ts.id = t.status_id AND ts.is_successful
  JOIN account.transaction_types tt    ON tt.id = t.transaction_type_id
  JOIN account.transaction_channels ch ON ch.id = t.channel_id
  LEFT JOIN core.branches b            ON b.id = t.branch_id
 GROUP BY 1, 2, 3, 4;

-- Салбарын гүйцэтгэл
CREATE OR REPLACE VIEW reporting.v_branch_performance AS
SELECT b.branch_code,
       b.branch_name,
       (SELECT count(*) FROM customer.customers c
         WHERE c.branch_id = b.id AND c.deleted_at IS NULL)                         AS customers,
       (SELECT count(*) FROM loan.loans l JOIN loan.loan_statuses ls ON ls.id = l.status_id
         WHERE l.branch_id = b.id AND ls.is_active_loan)                            AS active_loans,
       (SELECT COALESCE(SUM(l.total_outstanding), 0) FROM loan.loans l
          JOIN loan.loan_statuses ls ON ls.id = l.status_id
         WHERE l.branch_id = b.id AND ls.is_active_loan)                            AS portfolio,
       (SELECT COALESCE(SUM(l.total_outstanding), 0) FROM loan.loans l
          JOIN loan.loan_statuses ls ON ls.id = l.status_id
         WHERE l.branch_id = b.id AND ls.is_active_loan AND l.is_npl)               AS npl_amount,
       (SELECT COALESCE(SUM(d.amount), 0) FROM loan.loan_disbursements d
          JOIN loan.loans l ON l.id = d.loan_id
         WHERE l.branch_id = b.id AND d.status = 'EXECUTED'
           AND d.disbursement_date >= date_trunc('month', CURRENT_DATE))            AS disbursed_mtd,
       (SELECT COALESCE(SUM(pay.amount), 0) FROM loan.loan_payments pay
         WHERE pay.branch_id = b.id AND pay.status = 'POSTED'
           AND pay.payment_date >= date_trunc('month', CURRENT_DATE))               AS collected_mtd,
       (SELECT COALESCE(SUM(da.current_balance), 0) FROM deposit.deposit_accounts da
         WHERE da.branch_id = b.id AND da.status = 'ACTIVE')                        AS deposits,
       (SELECT count(*) FROM core.employees e
         WHERE e.branch_id = b.id AND e.employment_status = 'ACTIVE')               AS staff_count
  FROM core.branches b
 WHERE b.deleted_at IS NULL AND b.is_active;

-- Бүтээгдэхүүний гүйцэтгэл
CREATE OR REPLACE VIEW reporting.v_product_performance AS
SELECT p.product_code,
       p.product_name,
       pt.category,
       count(l.id)                                        AS loan_count,
       COALESCE(SUM(l.principal_amount), 0)               AS total_disbursed,
       COALESCE(SUM(l.total_outstanding), 0)              AS outstanding,
       COALESCE(SUM(l.interest_paid), 0)                  AS interest_earned,
       COALESCE(SUM(l.penalty_paid), 0)                   AS penalty_earned,
       count(l.id) FILTER (WHERE l.is_npl)                AS npl_count,
       COALESCE(SUM(l.total_outstanding) FILTER (WHERE l.is_npl), 0) AS npl_amount,
       round(100.0 * COALESCE(SUM(l.total_outstanding) FILTER (WHERE l.is_npl), 0)
             / NULLIF(SUM(l.total_outstanding), 0), 2)    AS npl_ratio,
       round(AVG(l.interest_rate), 2)                     AS avg_rate,
       round(AVG(l.term_months), 1)                       AS avg_term
  FROM product.products p
  JOIN product.product_types pt ON pt.id = p.product_type_id
  LEFT JOIN loan.loans l        ON l.product_id = p.id AND l.deleted_at IS NULL
 GROUP BY p.product_code, p.product_name, pt.category;

-- Барагдуулалтын гүйцэтгэл
CREATE OR REPLACE VIEW reporting.v_collection_performance AS
SELECT b.branch_code,
       cs.stage_code,
       cs.stage_name,
       e.first_name || ' ' || e.last_name  AS collector,
       count(DISTINCT cc.id)               AS case_count,
       SUM(cc.outstanding_at_open)         AS opened_amount,
       SUM(cc.collected_amount)            AS collected_amount,
       round(100.0 * SUM(cc.collected_amount) / NULLIF(SUM(cc.outstanding_at_open), 0), 2) AS recovery_rate,
       count(DISTINCT cp.id)               AS promises_made,
       count(DISTINCT cp.id) FILTER (WHERE cp.status = 'KEPT')     AS promises_kept,
       round(100.0 * count(DISTINCT cp.id) FILTER (WHERE cp.status = 'KEPT')
             / NULLIF(count(DISTINCT cp.id), 0), 2)                AS ptp_keep_rate,
       count(DISTINCT ca.id)               AS actions_taken,
       COALESCE(SUM(ca.cost_amount), 0)    AS collection_cost
  FROM collection.collection_cases cc
  JOIN collection.collection_stages cs ON cs.id = cc.stage_id
  JOIN core.branches b                 ON b.id = cc.branch_id
  LEFT JOIN core.employees e           ON e.id = cc.assigned_to
  LEFT JOIN collection.collection_promises cp ON cp.case_id = cc.id
  LEFT JOIN collection.collection_actions ca  ON ca.case_id = cc.id
 GROUP BY b.branch_code, cs.stage_code, cs.stage_name, e.first_name, e.last_name;

-- Гүйлгээний баланс (trial balance)
CREATE OR REPLACE VIEW reporting.v_trial_balance AS
SELECT ap.period_code,
       coa.account_code,
       coa.account_name_mn,
       coa.account_type,
       coa.normal_balance,
       b.branch_code,
       lb.currency_code,
       SUM(lb.opening_debit)  AS opening_debit,
       SUM(lb.opening_credit) AS opening_credit,
       SUM(lb.period_debit)   AS period_debit,
       SUM(lb.period_credit)  AS period_credit,
       SUM(lb.closing_debit)  AS closing_debit,
       SUM(lb.closing_credit) AS closing_credit,
       SUM(lb.base_closing_balance) AS closing_balance
  FROM accounting.ledger_balances lb
  JOIN accounting.chart_of_accounts coa ON coa.id = lb.gl_account_id
  JOIN accounting.accounting_periods ap ON ap.id = lb.period_id
  LEFT JOIN core.branches b ON b.id = lb.branch_id
 GROUP BY ap.period_code, coa.account_code, coa.account_name_mn, coa.account_type,
          coa.normal_balance, b.branch_code, lb.currency_code;

-- Ажилтны гүйцэтгэл (зээлийн мэргэжилтэн)
CREATE OR REPLACE VIEW reporting.v_officer_performance AS
SELECT e.employee_no,
       e.first_name || ' ' || e.last_name AS officer_name,
       b.branch_code,
       count(DISTINCT l.id)                                    AS loans_managed,
       COALESCE(SUM(l.total_outstanding), 0)                   AS portfolio,
       count(DISTINCT l.id) FILTER (WHERE l.days_past_due > 30) AS loans_par30,
       round(100.0 * COALESCE(SUM(l.total_outstanding) FILTER (WHERE l.days_past_due > 30), 0)
             / NULLIF(SUM(l.total_outstanding), 0), 2)         AS par30_ratio,
       count(DISTINCT la.id)                                   AS applications_handled,
       count(DISTINCT la.id) FILTER (WHERE las.is_approved)    AS applications_approved
  FROM core.employees e
  JOIN core.branches b ON b.id = e.branch_id
  LEFT JOIN loan.loans l ON l.loan_officer_id = e.id AND l.deleted_at IS NULL
  LEFT JOIN loan.loan_applications la ON la.loan_officer_id = e.id
  LEFT JOIN loan.loan_application_statuses las ON las.id = la.status_id
 WHERE e.employment_status = 'ACTIVE'
 GROUP BY e.employee_no, e.first_name, e.last_name, b.branch_code;
