-- =====================================================================
-- queries/analytics.sql — Шаардлагатай тайлангийн асуулгууд (§21.6)
-- ---------------------------------------------------------------------
-- Асуулга бүр: зорилго, ашиглах индекс, тайлбар.
-- Ажиллуулах: psql -f queries/analytics.sql  (эсвэл тус тусад нь)
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. Идэвхтэй зээлийн багц (Active loan portfolio)
--    Индекс: ix_loans_branch_status (branch_id, status_id) partial
-- ---------------------------------------------------------------------
SELECT b.branch_name,
       p.product_name,
       count(*)                      AS loan_count,
       SUM(l.principal_amount)       AS disbursed,
       SUM(l.total_outstanding)      AS outstanding,
       round(AVG(l.interest_rate), 2) AS avg_rate
  FROM loan.loans l
  JOIN loan.loan_statuses ls ON ls.id = l.status_id AND ls.is_active_loan
  JOIN core.branches b       ON b.id = l.branch_id
  JOIN product.products p    ON p.id = l.product_id
 WHERE l.deleted_at IS NULL
 GROUP BY ROLLUP (b.branch_name, p.product_name)
 ORDER BY b.branch_name NULLS LAST, outstanding DESC;

-- ---------------------------------------------------------------------
-- 2. Нийт зээлийн үлдэгдэл (валютаар, суурь валютад хөрвүүлсэн)
-- ---------------------------------------------------------------------
SELECT l.currency_code,
       count(*)                     AS loans,
       SUM(l.principal_outstanding) AS principal,
       SUM(l.interest_outstanding)  AS interest,
       SUM(l.penalty_outstanding)   AS penalty,
       SUM(l.fee_outstanding)       AS fees,
       SUM(l.total_outstanding)     AS total,
       SUM(system.fn_convert_amount(l.total_outstanding, l.currency_code, 'MNT', CURRENT_DATE)) AS total_mnt
  FROM loan.loans l
  JOIN loan.loan_statuses ls ON ls.id = l.status_id AND ls.is_active_loan
 WHERE l.deleted_at IS NULL
 GROUP BY l.currency_code;

-- ---------------------------------------------------------------------
-- 3. Харилцагчийн нийт өр (бүх бүтээгдэхүүн, батлан даалт хамт)
--    Холбогдох этгээдийн эрсдэлийг харах суурь
-- ---------------------------------------------------------------------
SELECT c.customer_number,
       c.display_name,
       cs.segment_name,
       count(DISTINCT l.id)                       AS active_loans,
       COALESCE(SUM(l.total_outstanding), 0)      AS direct_debt,
       COALESCE((SELECT SUM(lg.guarantee_amount)
                   FROM collateral.loan_guarantors lg
                   JOIN collateral.guarantors g ON g.id = lg.guarantor_id
                  WHERE g.customer_id = c.id AND lg.status = 'ACTIVE'), 0) AS guaranteed_debt,
       COALESCE(MAX(l.days_past_due), 0)          AS max_dpd,
       COALESCE(SUM(a.current_balance), 0)        AS deposits
  FROM customer.customers c
  LEFT JOIN loan.loans l ON l.customer_id = c.id AND l.deleted_at IS NULL
       AND l.status_id IN (SELECT id FROM loan.loan_statuses WHERE is_active_loan)
  LEFT JOIN account.accounts a ON a.customer_id = c.id AND a.status = 'ACTIVE'
  LEFT JOIN customer.customer_segments cs ON cs.id = c.segment_id
 WHERE c.deleted_at IS NULL
 GROUP BY c.id, c.customer_number, c.display_name, cs.segment_name
HAVING COALESCE(SUM(l.total_outstanding), 0) > 0
 ORDER BY direct_debt DESC
 LIMIT 100;

-- ---------------------------------------------------------------------
-- 4. DPD 30+ зээлүүд (индекс: ix_loans_dpd partial WHERE days_past_due > 0)
-- ---------------------------------------------------------------------
SELECT l.loan_number, c.display_name, b.branch_name,
       l.days_past_due, l.overdue_principal + l.overdue_interest AS overdue_amount,
       l.total_outstanding, cg.grade_name_mn AS classification
  FROM loan.loans l
  JOIN customer.customers c ON c.id = l.customer_id
  JOIN core.branches b      ON b.id = l.branch_id
  LEFT JOIN loan.classification_grades cg ON cg.id = l.classification_id
 WHERE l.days_past_due > 30 AND l.deleted_at IS NULL
 ORDER BY l.days_past_due DESC, l.total_outstanding DESC;

-- ---------------------------------------------------------------------
-- 5. DPD 90+ (NPL нэр дэвшигчид)
-- ---------------------------------------------------------------------
SELECT b.branch_name,
       count(*)                                                     AS loans,
       SUM(l.total_outstanding)                                     AS outstanding,
       round(AVG(l.days_past_due), 0)                               AS avg_dpd,
       SUM(l.total_outstanding * COALESCE(cg.provision_rate, 0) / 100) AS required_provision
  FROM loan.loans l
  JOIN core.branches b ON b.id = l.branch_id
  LEFT JOIN loan.classification_grades cg ON cg.id = l.classification_id
 WHERE l.days_past_due > 90 AND l.deleted_at IS NULL
 GROUP BY b.branch_name
 ORDER BY outstanding DESC;

-- ---------------------------------------------------------------------
-- 6. NPL харьцаа (нийт ба салбараар)
-- ---------------------------------------------------------------------
SELECT COALESCE(b.branch_name, 'НИЙТ')                              AS branch,
       SUM(l.total_outstanding)                                     AS portfolio,
       SUM(l.total_outstanding) FILTER (WHERE l.is_npl)             AS npl_amount,
       round(100.0 * SUM(l.total_outstanding) FILTER (WHERE l.is_npl)
             / NULLIF(SUM(l.total_outstanding), 0), 2)              AS npl_ratio_pct,
       count(*) FILTER (WHERE l.is_npl)                             AS npl_count,
       round(100.0 * count(*) FILTER (WHERE l.is_npl) / NULLIF(count(*), 0), 2) AS npl_count_pct
  FROM loan.loans l
  JOIN loan.loan_statuses ls ON ls.id = l.status_id AND ls.is_active_loan
  JOIN core.branches b       ON b.id = l.branch_id
 WHERE l.deleted_at IS NULL
 GROUP BY ROLLUP (b.branch_name);

-- ---------------------------------------------------------------------
-- 7. Салбарын гүйцэтгэл (view ашиглана)
-- ---------------------------------------------------------------------
SELECT branch_code, branch_name, customers, active_loans, portfolio,
       npl_amount,
       round(100.0 * npl_amount / NULLIF(portfolio, 0), 2) AS npl_ratio,
       disbursed_mtd, collected_mtd, deposits, staff_count,
       round(portfolio / NULLIF(staff_count, 0), 0)        AS portfolio_per_staff
  FROM reporting.v_branch_performance
 ORDER BY portfolio DESC;

-- ---------------------------------------------------------------------
-- 8. Бүтээгдэхүүний гүйцэтгэл
-- ---------------------------------------------------------------------
SELECT product_code, product_name, loan_count, total_disbursed, outstanding,
       interest_earned, npl_ratio, avg_rate, avg_term
  FROM reporting.v_product_performance
 WHERE loan_count > 0
 ORDER BY outstanding DESC;

-- ---------------------------------------------------------------------
-- 9. Сарын олголт (сүүлийн 12 сар)
-- ---------------------------------------------------------------------
SELECT month, branch_name, product_name, loan_count, disbursed_amount, avg_loan_size
  FROM reporting.v_monthly_disbursement
 WHERE month >= date_trunc('month', CURRENT_DATE) - INTERVAL '12 months'
 ORDER BY month DESC, disbursed_amount DESC;

-- ---------------------------------------------------------------------
-- 10. Сарын эргэн төлөлт + цуглуулгын үр ашиг
--     (төлөх ёстой байсан дүнтэй харьцуулав)
-- ---------------------------------------------------------------------
WITH expected AS (
    SELECT date_trunc('month', si.due_date)::DATE AS month,
           SUM(si.total_due) AS expected_amount
      FROM loan.loan_schedule_items si
      JOIN loan.loan_schedules s ON s.id = si.schedule_id AND s.is_current
     GROUP BY 1
), actual AS (
    SELECT date_trunc('month', payment_date)::DATE AS month,
           SUM(amount) AS collected
      FROM loan.loan_payments WHERE status = 'POSTED'
     GROUP BY 1
)
SELECT e.month, e.expected_amount, COALESCE(a.collected, 0) AS collected,
       round(100.0 * COALESCE(a.collected, 0) / NULLIF(e.expected_amount, 0), 2) AS collection_rate_pct
  FROM expected e
  LEFT JOIN actual a ON a.month = e.month
 WHERE e.month <= date_trunc('month', CURRENT_DATE)
 ORDER BY e.month DESC;

-- ---------------------------------------------------------------------
-- 11. Хүүгийн орлого (нягтлангийн бодит бичилтээс)
-- ---------------------------------------------------------------------
SELECT ap.period_code,
       SUM(CASE WHEN jl.dr_cr = 'C' THEN jl.base_amount ELSE -jl.base_amount END) AS interest_income
  FROM accounting.journal_lines jl
  JOIN accounting.journal_entries je     ON je.id = jl.entry_id AND je.status = 'POSTED'
  JOIN accounting.accounting_periods ap  ON ap.id = je.period_id
  JOIN accounting.chart_of_accounts coa  ON coa.id = jl.gl_account_id
 WHERE coa.account_code = '4100'
 GROUP BY ap.period_code
 ORDER BY ap.period_code DESC;

-- ---------------------------------------------------------------------
-- 12. Шимтгэлийн орлого (бүтээгдэхүүн, төрлөөр)
-- ---------------------------------------------------------------------
SELECT p.product_name, ft.fee_name,
       count(*)                  AS charge_count,
       SUM(lf.amount)            AS charged,
       SUM(lf.paid_amount)       AS collected,
       SUM(lf.waived_amount)     AS waived,
       round(100.0 * SUM(lf.paid_amount) / NULLIF(SUM(lf.amount), 0), 2) AS collection_rate
  FROM loan.loan_fees lf
  JOIN loan.loans l         ON l.id = lf.loan_id
  JOIN product.products p   ON p.id = l.product_id
  JOIN product.fee_types ft ON ft.id = lf.fee_type_id
 GROUP BY p.product_name, ft.fee_name
 ORDER BY charged DESC;

-- ---------------------------------------------------------------------
-- 13. Харилцагчийн ашигт байдал (орлого − хүлээгдэж буй алдагдал)
-- ---------------------------------------------------------------------
SELECT c.customer_number, c.display_name,
       COALESCE(SUM(l.interest_paid), 0)                    AS interest_income,
       COALESCE(SUM(l.penalty_paid), 0)                     AS penalty_income,
       COALESCE(SUM(lf.paid_amount), 0)                     AS fee_income,
       COALESCE(SUM(l.total_outstanding * cg.provision_rate / 100), 0) AS expected_loss,
       COALESCE(SUM(l.interest_paid), 0) + COALESCE(SUM(l.penalty_paid), 0)
         + COALESCE(SUM(lf.paid_amount), 0)
         - COALESCE(SUM(l.total_outstanding * cg.provision_rate / 100), 0) AS net_contribution
  FROM customer.customers c
  JOIN loan.loans l ON l.customer_id = c.id AND l.deleted_at IS NULL
  LEFT JOIN loan.classification_grades cg ON cg.id = l.classification_id
  LEFT JOIN (SELECT loan_id, SUM(paid_amount) AS paid_amount FROM loan.loan_fees GROUP BY loan_id) lf
         ON lf.loan_id = l.id
 GROUP BY c.customer_number, c.display_name
 ORDER BY net_contribution DESC
 LIMIT 50;

-- ---------------------------------------------------------------------
-- 14. Хадгаламжийн үлдэгдэл
-- ---------------------------------------------------------------------
SELECT branch_name, deposit_type, currency_code, deposit_count, customer_count,
       total_balance, accrued_interest, avg_rate, maturing_30d
  FROM reporting.v_deposit_portfolio
 ORDER BY total_balance DESC;

-- ---------------------------------------------------------------------
-- 15. Өдрийн гүйлгээний хэмжээ (сүүлийн 30 хоног)
--     Индекс: ix_transactions_branch_date + partition pruning (value_date)
-- ---------------------------------------------------------------------
SELECT value_date, channel_name, transaction_category,
       txn_count, total_amount, unique_customers
  FROM reporting.v_daily_transaction_volume
 WHERE value_date >= CURRENT_DATE - 30
 ORDER BY value_date DESC, total_amount DESC;

-- ---------------------------------------------------------------------
-- 16. Барагдуулалтын гүйцэтгэл (ажилтан, шатаар)
-- ---------------------------------------------------------------------
SELECT branch_code, stage_name, collector, case_count, opened_amount, collected_amount,
       recovery_rate, promises_made, promises_kept, ptp_keep_rate, collection_cost
  FROM reporting.v_collection_performance
 ORDER BY collected_amount DESC NULLS LAST;

-- ---------------------------------------------------------------------
-- НЭМЭЛТ: чанарын шилжилтийн матриц (migration matrix)
--   Өнгөрсөн сарын ангиллаас энэ сарын ангилал руу хэрхэн шилжсэн бэ
-- ---------------------------------------------------------------------
WITH t0 AS (
    SELECT loan_id, classification_id FROM loan.loan_overdues
     WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM loan.loan_overdues
                             WHERE snapshot_date < date_trunc('month', CURRENT_DATE))
), t1 AS (
    SELECT loan_id, classification_id FROM loan.loan_overdues
     WHERE snapshot_date = (SELECT MAX(snapshot_date) FROM loan.loan_overdues)
)
SELECT COALESCE(g0.grade_code, 'ШИНЭ') AS from_grade,
       COALESCE(g1.grade_code, 'ХААГДСАН') AS to_grade,
       count(*) AS loan_count
  FROM t1
  FULL JOIN t0 ON t0.loan_id = t1.loan_id
  LEFT JOIN loan.classification_grades g0 ON g0.id = t0.classification_id
  LEFT JOIN loan.classification_grades g1 ON g1.id = t1.classification_id
 GROUP BY 1, 2
 ORDER BY 1, 2;
