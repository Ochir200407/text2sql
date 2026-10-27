-- =====================================================================
-- 015_indexes.sql — Индексийн стратеги
-- ---------------------------------------------------------------------
-- ЗАРЧИМ: индекс бүр ТОДОРХОЙ query pattern-д үйлчилнэ. Индекс бүр
-- INSERT/UPDATE-ийг удаашруулж, диск иддэг тул "болгоomoo индекслэх"-гүй.
--
-- PostgreSQL PK ба UNIQUE constraint дээр индексийг АВТОМАТААР үүсгэдэг —
-- тэдгээрийг энд давхардуулахгүй. FK багана дээр автомат индекс БАЙХГҮЙ
-- тул JOIN/шүүлтэд ашиглагддаг FK-үүдийг л энд индекслэв.
--
-- Хэрэглэсэн төрлүүд:
--   B-tree   — ердийн тэнцүү/муж хайлт
--   Partial  — WHERE-тэй индекс: идэвхтэй мөр цөөн үед хамаагүй жижиг
--   Composite— олон баганын дараалал: (сонгомол багана, дараа нь муж)
--   GIN+trgm — нэрийн хэсэгчилсэн хайлт (ILIKE '%...%')
--   GIN      — JSONB доторх хайлт
--   BRIN     — асар том, огноогоор эрэмбэлэгдсэн append-only хүснэгт
-- =====================================================================

\set ON_ERROR_STOP on

-- ---------------------------------------------------------------------
-- CORE
-- ---------------------------------------------------------------------
-- Салбарын мод (эцэг → хүүхэд)
CREATE INDEX ix_branches_parent ON core.branches (parent_branch_id) WHERE parent_branch_id IS NOT NULL;
-- "Тухайн салбарын идэвхтэй ажилтнууд"
CREATE INDEX ix_employees_branch_status ON core.employees (branch_id, employment_status) WHERE deleted_at IS NULL;
CREATE INDEX ix_employees_manager ON core.employees (manager_id) WHERE manager_id IS NOT NULL;
-- Нэвтрэлт: username-ээр (UNIQUE индекс аль хэдийн бий), идэвхтэй session
CREATE INDEX ix_user_sessions_user_active ON core.user_sessions (user_id, started_at DESC) WHERE ended_at IS NULL;
CREATE INDEX ix_user_roles_role ON core.user_roles (role_id);
CREATE INDEX ix_role_permissions_permission ON core.role_permissions (permission_id);
CREATE INDEX ix_employee_assignments_employee ON core.employee_assignments (employee_id, valid_from DESC);

-- ---------------------------------------------------------------------
-- CUSTOMER — 1 сая+ харилцагч
-- ---------------------------------------------------------------------
-- "Салбарын идэвхтэй харилцагчид" (жагсаалт, тайлан)
CREATE INDEX ix_customers_branch_status ON customer.customers (branch_id, status_id) WHERE deleted_at IS NULL;
CREATE INDEX ix_customers_segment ON customer.customers (segment_id) WHERE deleted_at IS NULL;
CREATE INDEX ix_customers_rm ON customer.customers (relationship_manager_id) WHERE relationship_manager_id IS NOT NULL;
-- Нэрээр хэсэгчлэн хайх: WHERE display_name ILIKE '%батбаяр%'
CREATE INDEX ix_customers_name_trgm ON customer.customers USING gin (display_name gin_trgm_ops);
-- KYC хугацаа дуусах гэж буй харилцагчид (нийцлийн ажил)
CREATE INDEX ix_customers_kyc_expiry ON customer.customers (kyc_expires_at)
    WHERE kyc_status = 'VERIFIED' AND deleted_at IS NULL;
-- Эрсдэлт харилцагчийн шүүлт (маш цөөн мөр → partial индекс жижиг)
CREATE INDEX ix_customers_high_risk ON customer.customers (risk_level, branch_id)
    WHERE risk_level IN ('HIGH', 'CRITICAL') OR is_pep OR is_sanctioned OR is_blacklisted;
-- JSONB нэмэлт талбараар хайх
CREATE INDEX ix_customers_extra ON customer.customers USING gin (extra_attributes);

CREATE INDEX ix_cust_addresses_customer ON customer.customer_addresses (customer_id) WHERE deleted_at IS NULL;
-- Нэг харилцагчид нэг төрөлд ганц үндсэн хаяг
CREATE UNIQUE INDEX uix_cust_address_primary ON customer.customer_addresses (customer_id, address_type)
    WHERE is_primary AND deleted_at IS NULL;
CREATE INDEX ix_cust_contacts_customer ON customer.customer_contacts (customer_id) WHERE deleted_at IS NULL;
-- Утас/имэйлээр харилцагч олох (дуудлагын төв, нэвтрэлт)
CREATE INDEX ix_cust_contacts_value ON customer.customer_contacts (contact_value) WHERE deleted_at IS NULL;
CREATE INDEX ix_cust_rel_customer ON customer.customer_relationships (customer_id);
CREATE INDEX ix_cust_rel_related ON customer.customer_relationships (related_customer_id)
    WHERE related_customer_id IS NOT NULL;
CREATE INDEX ix_cust_employments_customer ON customer.customer_employments (customer_id);
CREATE INDEX ix_cust_status_hist ON customer.customer_status_history (customer_id, changed_at DESC);
CREATE INDEX ix_cust_segment_hist ON customer.customer_segment_history (customer_id, changed_at DESC);

-- ---------------------------------------------------------------------
-- KYC / AML
-- ---------------------------------------------------------------------
CREATE INDEX ix_kyc_profiles_customer ON kyc.kyc_profiles (customer_id, status);
-- Дахин үнэлгээ хийх хугацаа болсон профайлууд
CREATE INDEX ix_kyc_profiles_review ON kyc.kyc_profiles (next_review_date) WHERE status = 'VERIFIED';
CREATE INDEX ix_kyc_documents_profile ON kyc.kyc_documents (kyc_profile_id);
CREATE INDEX ix_kyc_documents_expiry ON kyc.kyc_documents (expiry_date) WHERE expiry_date IS NOT NULL;
CREATE INDEX ix_kyc_verifications_profile ON kyc.kyc_verifications (kyc_profile_id, performed_at DESC);
-- Харилцагч бүрд ЯГ НЭГ идэвхтэй эрсдэлийн профайл
CREATE UNIQUE INDEX uix_cust_risk_current ON kyc.customer_risk_profiles (customer_id) WHERE is_current;
CREATE INDEX ix_cust_risk_review ON kyc.customer_risk_profiles (next_review_date) WHERE is_current;
-- Санкцын жагсаалтаас нэрээр хайх (fuzzy match)
CREATE INDEX ix_sanction_entries_name_trgm ON kyc.sanction_entries USING gin (entity_name gin_trgm_ops);
CREATE INDEX ix_sanction_entries_list ON kyc.sanction_entries (list_id) WHERE delisted_date IS NULL;
CREATE INDEX ix_pep_records_customer ON kyc.pep_records (customer_id) WHERE customer_id IS NOT NULL;
CREATE INDEX ix_pep_records_name_trgm ON kyc.pep_records USING gin (person_name gin_trgm_ops);
CREATE INDEX ix_aml_screenings_customer ON kyc.aml_screenings (customer_id, screened_at DESC);
-- Шийдвэрлээгүй тааралтууд (нийцлийн ажлын дараалал)
CREATE INDEX ix_aml_matches_pending ON kyc.aml_screening_matches (screening_id) WHERE decision = 'PENDING';
CREATE INDEX ix_aml_alerts_open ON kyc.aml_alerts (status, severity, detected_at DESC)
    WHERE status IN ('OPEN', 'UNDER_REVIEW', 'ESCALATED');
CREATE INDEX ix_aml_alerts_customer ON kyc.aml_alerts (customer_id, detected_at DESC);

-- ---------------------------------------------------------------------
-- PRODUCT
-- ---------------------------------------------------------------------
CREATE INDEX ix_products_type_status ON product.products (product_type_id, status) WHERE deleted_at IS NULL;
-- Тухайн өдөр хүчинтэй хүү олох: WHERE product_id = ? AND valid_from <= ? AND (valid_to IS NULL OR valid_to >= ?)
CREATE INDEX ix_product_rates_lookup ON product.product_rates (product_id, valid_from DESC, valid_to);
CREATE INDEX ix_product_rate_hist_product ON product.product_rate_history (product_id, effective_from DESC);
CREATE INDEX ix_product_fees_product ON product.product_fees (product_id, fee_type_id) WHERE is_active;
CREATE INDEX ix_product_limits_product ON product.product_limits (product_id, valid_from DESC);
CREATE INDEX ix_pay_alloc_rules_product ON product.payment_allocation_rules (product_id, allocation_order);

-- ---------------------------------------------------------------------
-- LOAN — хамгийн их ачаалалтай домэйн
-- ---------------------------------------------------------------------
-- Хүсэлтийн ажлын дараалал: "миний салбарын хүлээгдэж буй хүсэлтүүд"
CREATE INDEX ix_loan_app_branch_status ON loan.loan_applications (branch_id, status_id, created_at DESC)
    WHERE deleted_at IS NULL;
CREATE INDEX ix_loan_app_customer ON loan.loan_applications (customer_id, created_at DESC);
CREATE INDEX ix_loan_app_officer ON loan.loan_applications (loan_officer_id, status_id)
    WHERE loan_officer_id IS NOT NULL;
CREATE INDEX ix_loan_app_product ON loan.loan_applications (product_id, created_at DESC);
CREATE INDEX ix_loan_app_docs_app ON loan.loan_application_documents (application_id);
CREATE INDEX ix_loan_assessments_app ON loan.loan_assessments (application_id, assessed_at DESC);
CREATE INDEX ix_loan_decisions_app ON loan.loan_decisions (application_id, decided_at DESC);

-- Портфелийн үндсэн шүүлт: салбар × төлөв (идэвхтэй зээл л сонирхолтой)
CREATE INDEX ix_loans_branch_status ON loan.loans (branch_id, status_id) WHERE deleted_at IS NULL;
CREATE INDEX ix_loans_customer ON loan.loans (customer_id, status_id) WHERE deleted_at IS NULL;
CREATE INDEX ix_loans_product ON loan.loans (product_id, status_id) WHERE deleted_at IS NULL;
CREATE INDEX ix_loans_officer ON loan.loans (loan_officer_id) WHERE loan_officer_id IS NOT NULL;
-- DPD тайлан: "DPD 30+ / 90+ зээлүүд" — хугацаа хэтэрсэн зээл цөөнх тул partial
CREATE INDEX ix_loans_dpd ON loan.loans (days_past_due DESC, branch_id) WHERE days_past_due > 0;
CREATE INDEX ix_loans_npl ON loan.loans (classification_id, branch_id) WHERE is_npl;
-- Хугацаа дуусах, дуудлага хийх зээлүүд
CREATE INDEX ix_loans_maturity ON loan.loans (maturity_date) WHERE maturity_date IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX ix_loans_application ON loan.loans (application_id) WHERE application_id IS NOT NULL;
CREATE INDEX ix_loan_borrowers_customer ON loan.loan_borrowers (customer_id) WHERE is_active;
CREATE INDEX ix_loan_contracts_loan ON loan.loan_contracts (loan_id, status);
CREATE INDEX ix_loan_disb_loan ON loan.loan_disbursements (loan_id, disbursement_date DESC);
-- Сарын олголтын тайлан
CREATE INDEX ix_loan_disb_date ON loan.loan_disbursements (disbursement_date) WHERE status = 'EXECUTED';

-- Хуваарь: "тухайн зээлийн идэвхтэй хуваарь" — зээл бүрд ганц
CREATE UNIQUE INDEX uix_loan_schedule_current ON loan.loan_schedules (loan_id) WHERE is_current;
-- Төлбөрийн хуваарийн мөр: due_date-ээр хайх нь өдөр тутмын ажлын гол хэв маяг
CREATE INDEX ix_loan_sched_items_loan_due ON loan.loan_schedule_items (loan_id, due_date);
-- "Өнөөдөр төлөгдөх ёстой / хугацаа хэтэрсэн мөрүүд" — batch ажил
CREATE INDEX ix_loan_sched_items_due_status ON loan.loan_schedule_items (due_date, status)
    WHERE status IN ('PENDING', 'PARTIALLY_PAID', 'OVERDUE');

CREATE INDEX ix_loan_payments_loan_date ON loan.loan_payments (loan_id, payment_date DESC);
CREATE INDEX ix_loan_payments_customer ON loan.loan_payments (customer_id, payment_date DESC);
-- Өдрийн/сарын төлөлтийн тайлан (POSTED л тооцогдоно)
CREATE INDEX ix_loan_payments_date ON loan.loan_payments (payment_date) WHERE status = 'POSTED';
CREATE INDEX ix_loan_payments_branch ON loan.loan_payments (branch_id, payment_date DESC);
CREATE INDEX ix_loan_pay_alloc_payment ON loan.loan_payment_allocations (payment_id);
CREATE INDEX ix_loan_pay_alloc_item ON loan.loan_payment_allocations (schedule_item_id)
    WHERE schedule_item_id IS NOT NULL;

CREATE INDEX ix_loan_rate_hist_loan ON loan.loan_interest_rate_history (loan_id, effective_from DESC);
-- Хуримтлалын partition бүрд: зээлээр хайх
CREATE INDEX ix_loan_accruals_loan ON loan.loan_interest_accruals (loan_id, accrual_date DESC);
-- Дэвтэрт бичигдээгүй хуримтлалууд (өдрийн хаалтын ажил)
CREATE INDEX ix_loan_accruals_unposted ON loan.loan_interest_accruals (accrual_date) WHERE NOT is_posted;
CREATE INDEX ix_loan_fees_loan ON loan.loan_fees (loan_id, charged_date DESC);
CREATE INDEX ix_loan_penalties_loan ON loan.loan_penalties (loan_id, charged_date DESC);
CREATE INDEX ix_loan_overdues_loan ON loan.loan_overdues (loan_id, snapshot_date DESC);
-- PAR тайлан: тухайн өдрийн бүх зээлийн DPD ангилал
CREATE INDEX ix_loan_overdues_bucket ON loan.loan_overdues (snapshot_date, dpd_bucket);
CREATE INDEX ix_loan_class_loan ON loan.loan_classifications (loan_id, classification_date DESC);
CREATE INDEX ix_loan_restructures_loan ON loan.loan_restructures (loan_id, effective_date DESC);
CREATE INDEX ix_loan_writeoffs_loan ON loan.loan_writeoffs (loan_id, writeoff_date DESC);
CREATE INDEX ix_loan_status_hist_loan ON loan.loan_status_history (loan_id, changed_at DESC);

-- ---------------------------------------------------------------------
-- COLLATERAL
-- ---------------------------------------------------------------------
CREATE INDEX ix_collaterals_owner ON collateral.collaterals (owner_customer_id) WHERE deleted_at IS NULL;
CREATE INDEX ix_collaterals_type_status ON collateral.collaterals (collateral_type_id, status)
    WHERE deleted_at IS NULL;
-- Дахин үнэлгээ хийх хугацаа болсон хөрөнгүүд
CREATE INDEX ix_collaterals_revaluation ON collateral.collaterals (next_valuation_date)
    WHERE status = 'ACTIVE' AND deleted_at IS NULL;
CREATE INDEX ix_collaterals_attributes ON collateral.collaterals USING gin (attributes);
CREATE INDEX ix_collateral_val_collateral ON collateral.collateral_valuations (collateral_id, valuation_date DESC);
CREATE INDEX ix_collateral_docs_collateral ON collateral.collateral_documents (collateral_id);
-- Даатгал дуусах
CREATE INDEX ix_collateral_ins_expiry ON collateral.collateral_insurances (end_date) WHERE status = 'ACTIVE';
CREATE INDEX ix_loan_collaterals_loan ON collateral.loan_collaterals (loan_id) WHERE status = 'ACTIVE';
CREATE INDEX ix_loan_collaterals_collateral ON collateral.loan_collaterals (collateral_id) WHERE status = 'ACTIVE';
CREATE INDEX ix_guarantors_customer ON collateral.guarantors (customer_id) WHERE customer_id IS NOT NULL;
CREATE INDEX ix_loan_guarantors_loan ON collateral.loan_guarantors (loan_id) WHERE status = 'ACTIVE';
CREATE INDEX ix_loan_guarantors_guarantor ON collateral.loan_guarantors (guarantor_id);

-- ---------------------------------------------------------------------
-- ACCOUNT / TRANSACTION — 10 сая+ мөр
-- ---------------------------------------------------------------------
CREATE INDEX ix_accounts_customer ON account.accounts (customer_id, status)
    WHERE customer_id IS NOT NULL AND deleted_at IS NULL;
CREATE INDEX ix_accounts_branch_type ON account.accounts (branch_id, account_type_id) WHERE deleted_at IS NULL;
-- Идэвхгүй болох дансыг илрүүлэх batch
CREATE INDEX ix_accounts_dormant ON account.accounts (last_transaction_at) WHERE status = 'ACTIVE' AND NOT is_dormant;
CREATE INDEX ix_account_holds_account ON account.account_holds (account_id) WHERE status = 'ACTIVE';
CREATE INDEX ix_account_balances_account ON account.account_balances (account_id, balance_date DESC);
CREATE INDEX ix_account_status_hist ON account.account_status_history (account_id, changed_at DESC);

-- Хуулга: "тухайн дансны сүүлийн гүйлгээнүүд" — хамгийн түгээмэл асуулга
CREATE INDEX ix_transactions_account_date ON account.transactions (account_id, value_date DESC, id DESC);
CREATE INDEX ix_transactions_customer_date ON account.transactions (customer_id, value_date DESC)
    WHERE customer_id IS NOT NULL;
-- Өдрийн гүйлгээний хэмжээ, кассын тайлан
CREATE INDEX ix_transactions_branch_date ON account.transactions (branch_id, transaction_date)
    WHERE branch_id IS NOT NULL;
CREATE INDEX ix_transactions_type_date ON account.transactions (transaction_type_id, value_date);
-- Шилжүүлгийн хос мөрийг олох
CREATE INDEX ix_transactions_transfer_group ON account.transactions (transfer_group_id)
    WHERE transfer_group_id IS NOT NULL;
-- Эх сурвалжаас буцаж холбох (зээлийн төлөлт → гүйлгээ)
CREATE INDEX ix_transactions_source ON account.transactions (source_entity_type, source_entity_id)
    WHERE source_entity_id IS NOT NULL;
-- Идемпотент: нэг суваг + external_ref давхардахгүй (partition key заавал орно)
CREATE UNIQUE INDEX uix_transactions_external_ref ON account.transactions (channel_id, external_ref, value_date)
    WHERE external_ref IS NOT NULL;
-- Огромный архивт: BRIN нь B-tree-ээс ~1000 дахин бага зайтай, муж хайлтад хангалттай
CREATE INDEX ix_transactions_date_brin ON account.transactions USING brin (transaction_date)
    WITH (pages_per_range = 64);

CREATE INDEX ix_payment_orders_status ON account.payment_orders (status, requested_date)
    WHERE status IN ('PENDING_APPROVAL', 'APPROVED', 'PROCESSING');
CREATE INDEX ix_payment_orders_customer ON account.payment_orders (customer_id, requested_date DESC);
CREATE INDEX ix_payment_orders_debit_acc ON account.payment_orders (debit_account_id)
    WHERE debit_account_id IS NOT NULL;

-- ---------------------------------------------------------------------
-- DEPOSIT
-- ---------------------------------------------------------------------
CREATE INDEX ix_deposit_customer ON deposit.deposit_accounts (customer_id, status) WHERE deleted_at IS NULL;
CREATE INDEX ix_deposit_branch ON deposit.deposit_accounts (branch_id, status) WHERE deleted_at IS NULL;
-- Хугацаа дуусах хадгаламж (сунгалт, хүү төлөлт)
CREATE INDEX ix_deposit_maturity ON deposit.deposit_accounts (maturity_date)
    WHERE status = 'ACTIVE' AND maturity_date IS NOT NULL;
CREATE INDEX ix_deposit_contracts_account ON deposit.deposit_contracts (deposit_account_id, status);
CREATE INDEX ix_deposit_rates_product ON deposit.deposit_rates (product_id, valid_from DESC) WHERE is_active;
CREATE INDEX ix_deposit_accruals_account ON deposit.deposit_interest_accruals (deposit_account_id, accrual_date DESC);
CREATE INDEX ix_deposit_txn_account ON deposit.deposit_transactions (deposit_account_id, value_date DESC);

-- ---------------------------------------------------------------------
-- ACCOUNTING
-- ---------------------------------------------------------------------
CREATE INDEX ix_coa_parent ON accounting.chart_of_accounts (parent_id) WHERE parent_id IS NOT NULL;
CREATE INDEX ix_coa_type ON accounting.chart_of_accounts (account_type) WHERE is_active AND deleted_at IS NULL;
CREATE INDEX ix_periods_dates ON accounting.accounting_periods (start_date, end_date);
CREATE INDEX ix_je_period_status ON accounting.journal_entries (period_id, status);
CREATE INDEX ix_je_posting_date ON accounting.journal_entries (posting_date, journal_id);
CREATE INDEX ix_je_source ON accounting.journal_entries (source_entity_type, source_entity_id)
    WHERE source_entity_id IS NOT NULL;
CREATE INDEX ix_je_source_txn ON accounting.journal_entries (source_transaction_id)
    WHERE source_transaction_id IS NOT NULL;
-- Ерөнхий дэвтэр: "данс × хугацаа" — trial balance, дансны хуулга
CREATE INDEX ix_jl_account_date ON accounting.journal_lines (gl_account_id, posting_date);
CREATE INDEX ix_jl_entry ON accounting.journal_lines (entry_id);
-- Хяналтын дансыг дэд дэвтэртэй тулгах
CREATE INDEX ix_jl_subledger ON accounting.journal_lines (subledger_type, subledger_id)
    WHERE subledger_id IS NOT NULL;
CREATE INDEX ix_jl_branch_date ON accounting.journal_lines (branch_id, posting_date) WHERE branch_id IS NOT NULL;
CREATE INDEX ix_jl_date_brin ON accounting.journal_lines USING brin (posting_date) WITH (pages_per_range = 64);
CREATE INDEX ix_ledger_balances_period ON accounting.ledger_balances (period_id, gl_account_id);
CREATE INDEX ix_acc_rules_event ON accounting.accounting_rules (event_type, product_id) WHERE is_active;
CREATE INDEX ix_acc_rule_lines_rule ON accounting.accounting_rule_lines (rule_id, line_no);

-- ---------------------------------------------------------------------
-- RISK
-- ---------------------------------------------------------------------
CREATE UNIQUE INDEX uix_credit_scores_current ON risk.credit_scores (customer_id, score_type) WHERE is_current;
CREATE INDEX ix_credit_scores_app ON risk.credit_scores (application_id) WHERE application_id IS NOT NULL;
CREATE INDEX ix_credit_scores_model ON risk.credit_scores (model_id, scored_at DESC);
CREATE INDEX ix_bureau_reports_customer ON risk.credit_bureau_reports (customer_id, requested_at DESC);
CREATE INDEX ix_risk_assessments_entity ON risk.risk_assessments (entity_type, entity_id, assessed_at DESC);
CREATE INDEX ix_fraud_alerts_open ON risk.fraud_alerts (status, severity, detected_at DESC)
    WHERE status IN ('OPEN', 'INVESTIGATING');
CREATE INDEX ix_fraud_alerts_customer ON risk.fraud_alerts (customer_id, detected_at DESC)
    WHERE customer_id IS NOT NULL;
CREATE INDEX ix_fraud_cases_status ON risk.fraud_cases (status, opened_at DESC);

-- ---------------------------------------------------------------------
-- COLLECTION
-- ---------------------------------------------------------------------
-- Нэг зээл дээр НЭГ л нээлттэй кейс
CREATE UNIQUE INDEX uix_collection_case_active ON collection.collection_cases (loan_id)
    WHERE status NOT IN ('RESOLVED', 'CLOSED', 'WRITTEN_OFF');
-- Цуглуулгын ажилтны ажлын дараалал
CREATE INDEX ix_collection_cases_assignee ON collection.collection_cases (assigned_to, next_action_date)
    WHERE status IN ('OPEN', 'IN_PROGRESS', 'PROMISE_TO_PAY');
CREATE INDEX ix_collection_cases_stage ON collection.collection_cases (stage_id, current_dpd DESC);
CREATE INDEX ix_collection_cases_branch ON collection.collection_cases (branch_id, status);
CREATE INDEX ix_collection_cases_customer ON collection.collection_cases (customer_id);
CREATE INDEX ix_collection_actions_case ON collection.collection_actions (case_id, action_date DESC);
CREATE INDEX ix_collection_actions_performer ON collection.collection_actions (performed_by, action_date DESC)
    WHERE performed_by IS NOT NULL;
-- Хугацаа нь болсон, биелээгүй амлалтууд
CREATE INDEX ix_collection_promises_due ON collection.collection_promises (promised_date, status)
    WHERE status = 'PENDING';
CREATE INDEX ix_collection_promises_case ON collection.collection_promises (case_id);
CREATE INDEX ix_collection_payments_case ON collection.collection_payments (case_id, payment_date DESC);
CREATE INDEX ix_recovery_cases_loan ON collection.recovery_cases (loan_id, started_date DESC);
CREATE INDEX ix_case_assignments_case ON collection.case_assignments (case_id, assigned_at DESC);

-- ---------------------------------------------------------------------
-- SYSTEM
-- ---------------------------------------------------------------------
-- Суурь валют ЯГ НЭГ
CREATE UNIQUE INDEX uix_currencies_base ON system.currencies ((is_base)) WHERE is_base;
-- Ханш хайх: (валют хос, огноо DESC) — "тухайн өдрийн буюу түүнээс өмнөх сүүлийн ханш"
CREATE INDEX ix_fx_rates_lookup ON system.exchange_rates (from_currency, to_currency, rate_date DESC);
CREATE INDEX ix_system_params_group ON system.system_parameters (param_group, param_code);
CREATE INDEX ix_documents_owner ON system.documents (owner_entity_type, owner_entity_id) WHERE deleted_at IS NULL;
CREATE INDEX ix_documents_customer ON system.documents (customer_id) WHERE customer_id IS NOT NULL;
-- Хадгалах хугацаа дууссан баримтыг архивлах batch
CREATE INDEX ix_documents_retention ON system.documents (retention_until) WHERE status = 'ACTIVE';
CREATE INDEX ix_document_versions_doc ON system.document_versions (document_id, version_no DESC);
CREATE INDEX ix_notifications_status ON system.notifications (status, scheduled_at)
    WHERE status IN ('QUEUED', 'SENDING');
CREATE INDEX ix_notifications_customer ON system.notifications (customer_id, created_at DESC)
    WHERE customer_id IS NOT NULL;

-- Аудит: "энэ мөрийг хэн хэзээ өөрчилсөн бэ" — хамгийн түгээмэл аудитын асуулга
CREATE INDEX ix_audit_logs_record ON system.audit_logs (schema_name, table_name, record_pk, changed_at DESC);
CREATE INDEX ix_audit_logs_user ON system.audit_logs (user_id, changed_at DESC) WHERE user_id IS NOT NULL;
-- JSONB доторх утгаар хайх (жишээ: тодорхой дүн өөрчлөгдсөн бичлэгүүд)
CREATE INDEX ix_audit_logs_new_values ON system.audit_logs USING gin (new_values);
CREATE INDEX ix_integration_logs_code ON system.integration_logs (integration_code, logged_at DESC);
CREATE INDEX ix_integration_logs_failed ON system.integration_logs (logged_at DESC) WHERE NOT is_success;
-- Outbox: нийтлээгүй үйл явдлыг дарааллаар нь татах
CREATE INDEX ix_outbox_unpublished ON system.outbox_events (occurred_at) WHERE NOT published;
