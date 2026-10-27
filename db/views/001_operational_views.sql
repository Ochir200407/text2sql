-- =====================================================================
-- views/001_operational_views.sql — Өдөр тутмын ажлын view
-- ---------------------------------------------------------------------
-- Эдгээр нь ОПЕРАЦИЙН view: апп давхаргад JOIN давтахгүйн тулд.
-- Тайлангийн (нэгтгэсэн) view нь 002_reporting_views.sql.
-- =====================================================================

\set ON_ERROR_STOP on

-- Харилцагчийн нэгдсэн дүр зураг (Customer 360)
CREATE OR REPLACE VIEW reporting.v_customer_360 AS
SELECT c.id                              AS customer_id,
       c.customer_number,
       c.display_name,
       c.party_type,
       ct.type_name                      AS customer_type,
       cs.status_name                     AS customer_status,
       seg.segment_name                   AS segment,
       b.branch_name,
       e.first_name || ' ' || e.last_name AS relationship_manager,
       c.kyc_status,
       c.aml_status,
       c.risk_level,
       c.is_pep,
       c.is_sanctioned,
       ci.birth_date,
       ci.monthly_income,
       co.legal_name,
       co.annual_revenue,
       (SELECT contact_value FROM customer.customer_contacts cc
         WHERE cc.customer_id = c.id AND cc.contact_type = 'MOBILE' AND cc.is_primary
           AND cc.deleted_at IS NULL LIMIT 1)             AS primary_phone,
       (SELECT full_address FROM customer.customer_addresses ca
         WHERE ca.customer_id = c.id AND ca.is_primary AND ca.deleted_at IS NULL LIMIT 1) AS primary_address,
       (SELECT count(*) FROM loan.loans l
         WHERE l.customer_id = c.id AND l.deleted_at IS NULL)                   AS total_loans,
       (SELECT count(*) FROM loan.loans l
          JOIN loan.loan_statuses ls ON ls.id = l.status_id
         WHERE l.customer_id = c.id AND ls.is_active_loan AND l.deleted_at IS NULL) AS active_loans,
       (SELECT COALESCE(SUM(l.total_outstanding), 0) FROM loan.loans l
          JOIN loan.loan_statuses ls ON ls.id = l.status_id
         WHERE l.customer_id = c.id AND ls.is_active_loan AND l.deleted_at IS NULL) AS total_exposure,
       (SELECT COALESCE(MAX(l.days_past_due), 0) FROM loan.loans l
         WHERE l.customer_id = c.id AND l.deleted_at IS NULL)                   AS max_dpd,
       (SELECT COALESCE(SUM(a.current_balance), 0) FROM account.accounts a
         WHERE a.customer_id = c.id AND a.status = 'ACTIVE' AND a.deleted_at IS NULL) AS total_deposits,
       c.onboarded_at,
       c.created_at
  FROM customer.customers c
  JOIN customer.customer_types ct    ON ct.id = c.customer_type_id
  JOIN customer.customer_statuses cs ON cs.id = c.status_id
  LEFT JOIN customer.customer_segments seg ON seg.id = c.segment_id
  JOIN core.branches b               ON b.id = c.branch_id
  LEFT JOIN core.employees e         ON e.id = c.relationship_manager_id
  LEFT JOIN customer.customer_individuals ci   ON ci.customer_id = c.id
  LEFT JOIN customer.customer_organizations co ON co.customer_id = c.id
 WHERE c.deleted_at IS NULL;

COMMENT ON VIEW reporting.v_customer_360 IS
    'Харилцагчийн бүрэн дүр зураг: KYC, эрсдэл, зээл, хадгаламж — дуудлагын төв, зээлийн ажилтанд';

-- Зээлийн дэлгэрэнгүй (JOIN-ийг нэг газар)
CREATE OR REPLACE VIEW reporting.v_loan_details AS
SELECT l.id                       AS loan_id,
       l.loan_number,
       c.customer_number,
       c.display_name             AS customer_name,
       p.product_code,
       p.product_name,
       b.branch_code,
       b.branch_name,
       ls.status_code             AS loan_status,
       cg.grade_code              AS classification,
       cg.grade_name_mn           AS classification_name,
       cg.provision_rate,
       l.currency_code,
       l.principal_amount,
       l.interest_rate,
       l.term_months,
       l.disbursement_date,
       l.maturity_date,
       l.principal_outstanding,
       l.interest_outstanding,
       l.penalty_outstanding,
       l.fee_outstanding,
       l.total_outstanding,
       l.principal_paid,
       l.interest_paid,
       l.overdue_principal,
       l.overdue_interest,
       l.days_past_due,
       loan.fn_dpd_bucket(l.days_past_due) AS dpd_bucket,
       l.is_npl,
       l.is_restructured,
       l.last_payment_date,
       round(l.total_outstanding * cg.provision_rate / 100, 2) AS provision_amount,
       e.first_name || ' ' || e.last_name AS loan_officer,
       (SELECT COALESCE(SUM(lc.allocated_amount), 0)
          FROM collateral.loan_collaterals lc
         WHERE lc.loan_id = l.id AND lc.status = 'ACTIVE')    AS collateral_value,
       (SELECT MIN(si.due_date)
          FROM loan.loan_schedule_items si
          JOIN loan.loan_schedules s ON s.id = si.schedule_id AND s.is_current
         WHERE si.loan_id = l.id AND si.status IN ('PENDING', 'PARTIALLY_PAID', 'OVERDUE')) AS next_due_date
  FROM loan.loans l
  JOIN customer.customers c    ON c.id = l.customer_id
  JOIN product.products p      ON p.id = l.product_id
  JOIN core.branches b         ON b.id = l.branch_id
  JOIN loan.loan_statuses ls   ON ls.id = l.status_id
  LEFT JOIN loan.classification_grades cg ON cg.id = l.classification_id
  LEFT JOIN core.employees e   ON e.id = l.loan_officer_id
 WHERE l.deleted_at IS NULL;

-- Хугацаа хэтэрсэн хуваарийн мөрүүд (цуглуулгын ажлын жагсаалт)
CREATE OR REPLACE VIEW reporting.v_overdue_installments AS
SELECT si.loan_id,
       l.loan_number,
       c.display_name              AS customer_name,
       c.customer_number,
       b.branch_name,
       si.installment_no,
       si.due_date,
       CURRENT_DATE - si.due_date  AS days_overdue,
       si.total_due,
       si.principal_due - si.principal_paid AS principal_unpaid,
       si.interest_due  - si.interest_paid  AS interest_unpaid,
       si.penalty_charged - si.penalty_paid AS penalty_unpaid,
       si.status,
       (SELECT contact_value FROM customer.customer_contacts cc
         WHERE cc.customer_id = c.id AND cc.contact_type = 'MOBILE' AND cc.is_primary LIMIT 1) AS phone
  FROM loan.loan_schedule_items si
  JOIN loan.loan_schedules s ON s.id = si.schedule_id AND s.is_current
  JOIN loan.loans l          ON l.id = si.loan_id AND l.deleted_at IS NULL
  JOIN customer.customers c  ON c.id = l.customer_id
  JOIN core.branches b       ON b.id = l.branch_id
 WHERE si.due_date < CURRENT_DATE
   AND si.status IN ('PENDING', 'PARTIALLY_PAID', 'OVERDUE');

-- Дансны хуулга (сүүлийн гүйлгээ)
CREATE OR REPLACE VIEW reporting.v_account_statement AS
SELECT t.id            AS transaction_id,
       t.transaction_no,
       a.account_number,
       a.account_name,
       c.customer_number,
       c.display_name  AS customer_name,
       tt.type_name    AS transaction_type,
       ch.channel_name AS channel,
       ts.status_name  AS status,
       t.direction,
       t.amount,
       t.fee_amount,
       t.currency_code,
       t.balance_before,
       t.balance_after,
       t.transaction_date,
       t.value_date,
       t.description,
       t.counterparty_name,
       b.branch_name
  FROM account.transactions t
  JOIN account.accounts a            ON a.id = t.account_id
  LEFT JOIN customer.customers c     ON c.id = t.customer_id
  JOIN account.transaction_types tt  ON tt.id = t.transaction_type_id
  JOIN account.transaction_channels ch ON ch.id = t.channel_id
  JOIN account.transaction_statuses ts ON ts.id = t.status_id
  LEFT JOIN core.branches b          ON b.id = t.branch_id;

-- KYC/AML анхаарал шаардсан харилцагчид
CREATE OR REPLACE VIEW reporting.v_compliance_watchlist AS
SELECT c.customer_number,
       c.display_name,
       c.risk_level,
       c.kyc_status,
       c.kyc_expires_at,
       c.aml_status,
       c.is_pep,
       c.is_sanctioned,
       kp.next_review_date            AS kyc_review_date,
       crp.risk_score,
       (SELECT count(*) FROM kyc.aml_alerts al
         WHERE al.customer_id = c.id AND al.status IN ('OPEN', 'UNDER_REVIEW', 'ESCALATED')) AS open_alerts,
       (SELECT COALESCE(SUM(l.total_outstanding), 0) FROM loan.loans l
          JOIN loan.loan_statuses ls ON ls.id = l.status_id
         WHERE l.customer_id = c.id AND ls.is_active_loan)                                   AS exposure
  FROM customer.customers c
  LEFT JOIN kyc.kyc_profiles kp            ON kp.customer_id = c.id
  LEFT JOIN kyc.customer_risk_profiles crp ON crp.customer_id = c.id AND crp.is_current
 WHERE c.deleted_at IS NULL
   AND (c.risk_level IN ('HIGH', 'CRITICAL')
        OR c.is_pep OR c.is_sanctioned
        OR c.kyc_status IN ('EXPIRED', 'REJECTED')
        OR kp.next_review_date <= CURRENT_DATE + 30);

COMMENT ON VIEW reporting.v_compliance_watchlist IS
    'Нийцлийн албаны өдөр тутмын ажлын жагсаалт — KYC дуусах, өндөр эрсдэл, нээлттэй AML сэрэмжлүүлэг';
