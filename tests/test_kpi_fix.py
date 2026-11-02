"""kpi_fix (pipeline/kpi_fix.py): glossary/AUTHORING-ийн каноник хэлбэр рүү детерминист засвар — нөхцөл яг таарвал л; бусад
үед SQL өөрчлөгдөхгүй. RunOptions.kpi_fix анхдагч унтраалттай."""
from mn_text2sql.knowledge.semantic import cached_model
from mn_text2sql.pipeline.kpi_fix import id_columns, kpi_fix
from mn_text2sql.pipeline.options import RunOptions

M = cached_model("knowledge/semantic_model.yaml")


def test_npl_count_to_amount_only_for_ratio_questions():
    sql = ("SELECT i.gender, 100.0 * SUM(CASE WHEN l.is_npl THEN 1 ELSE 0 END) / NULLIF(COUNT(l.loan_id), 0) FROM loans AS l "
           "JOIN individuals AS i ON i.customer_id = l.customer_id WHERE l.is_active_portfolio GROUP BY i.gender")
    out, tags = kpi_fix("Хүйсээр NPL хувийг харьцуул", sql, M)
    assert tags == ["npl_amount"]
    assert "THEN l.principal_outstanding_mnt ELSE 0" in out and "SUM(l.principal_outstanding_mnt), 0)" in out
    assert kpi_fix("Зээлийн хэдэн хувь нь NPL вэ?", sql, M) == (sql, [])        # тоогоор асуусан


def test_posted_added_only_without_status():
    out, tags = kpi_fix("Эргэн төлөлтийн тоо", "SELECT COUNT(*) FROM loan_payments AS p WHERE p.amount > 0", M)
    assert tags == ["posted"] and "p.status = 'POSTED'" in out
    failed = "SELECT COUNT(*) FROM loan_payments WHERE status = 'FAILED'"
    assert kpi_fix("Амжилтгүй төлбөр", failed, M) == (failed, [])


def test_id_column_routing():
    assert id_columns(M)["LN"] == ("loans", "loan_number")
    sql = ("SELECT la.channel FROM loan_applications AS la JOIN loans AS l ON l.application_id = la.application_id "
           "WHERE la.application_no = 'LN-2025-000244'")
    out, tags = kpi_fix("LN-2025-000244 зээл", sql, M)
    assert tags == ["id_column"] and "l.loan_number = 'LN-2025-000244'" in out
    no_loans = "SELECT channel FROM loan_applications WHERE application_no = 'LN-2025-000244'"
    assert kpi_fix("LN-2025-000244", no_loans, M) == (no_loans, [])            # loans scope-д алга — хөндөхгүй


def test_active_portfolio_triggers_and_skips():
    out, tags = kpi_fix("Салбар бүрийн багцын үлдэгдэл", "SELECT SUM(l.principal_outstanding_mnt) FROM loans AS l", M)
    assert tags == ["active_portfolio"] and "WHERE l.is_active_portfolio" in out
    out, tags = kpi_fix("30-аас дээш хоног хэтэрсэн зээл", "SELECT COUNT(*) FROM loans WHERE days_past_due > 30", M)
    assert tags == ["active_portfolio"]                                           # F2a: DPD шүүлт
    skip = "SELECT SUM(principal_amount_mnt) FROM loans WHERE days_past_due > 0"
    assert kpi_fix("Энэ онд олгосон зээлийн хугацаа хэтэрсэн дүн", skip, M) == (skip, [])   # «олгосон» — алгасна
    status = "SELECT COUNT(*) FROM loans WHERE days_past_due > 0 AND status_code = 'OVERDUE'"
    assert kpi_fix("Төлөвөөр", status, M) == (status, [])


def test_open_case_and_usd_native():
    out, tags = kpi_fix("Хаагдаагүй кейс хэд вэ?", "SELECT COUNT(*) FROM collection_cases AS c WHERE c.status = 'OPEN'", M)
    assert tags == ["open_case"] and "NOT c.status IN ('RESOLVED', 'CLOSED', 'WRITTEN_OFF')" in out
    sql = "SELECT SUM(d.current_balance_mnt) FROM deposits AS d WHERE d.currency_code = 'USD' AND d.status = 'ACTIVE'"
    out, tags = kpi_fix("Ам.долларын хадгаламжийн үлдэгдэл хэдэн доллар вэ?", sql, M)
    assert tags == ["usd_native"] and "SUM(d.current_balance)" in out
    assert kpi_fix("Ам.долларын хадгаламж хэдэн төгрөг вэ? хэдэн доллар", sql, M) == (sql, [])   # «төгрөг» — хөндөхгүй


def test_unparseable_and_default_off():
    assert kpi_fix("NPL хувь", "SELEC broken (", M) == ("SELEC broken (", [])
    assert kpi_fix("NPL хувь", None, M) == (None, [])
    assert RunOptions().kpi_fix is False and "kpi_fix" not in RunOptions().fingerprint()


def test_mnt_filter_lastn_next_due():
    sql = "SELECT SUM(p.amount * t2s.fx_to_mnt(p.currency_code, p.payment_date)) FROM loan_payments AS p WHERE p.status = 'POSTED'"
    out, tags = kpi_fix("Төгрөгийн зээлийн эргэн төлөлтийн дүн хэд вэ?", sql, M)
    assert "mnt_filter" in tags and "p.currency_code = 'MNT'" in out
    assert "mnt_filter" not in kpi_fix("Хэдэн төгрөгийн зээл олгосон бэ?", sql, M)[1]           # нэгж — шүүлт биш
    out, tags = kpi_fix("Сүүлийн 90 хоногт төлбөр хийсэн зээл",
                        "SELECT COUNT(*) FROM loan_payments AS p WHERE p.status = 'POSTED' AND p.payment_date >= "
                        "CURRENT_DATE - INTERVAL '90 days'", M)
    assert tags == ["lastn_boundary"] and "p.payment_date > CURRENT_DATE" in out
    out, tags = kpi_fix("Энэ зээлийн дараагийн төлөлт хэзээ вэ?",
                        "SELECT s.due_date, s.total_due FROM loan_schedule AS s ORDER BY s.due_date LIMIT 1", M)
    assert tags == ["next_due"] and "s.due_date > CURRENT_DATE" in out


def test_application_decision_rules():
    out, tags = kpi_fix("Өнгөрсөн улиралд хэдэн хүсэлт батлагдсан бэ?",
                        "SELECT COUNT(*) FROM loan_applications WHERE status_code = 'APPROVED' AND submitted_date >= '2026-01-01'", M)
    assert set(tags) == {"approved_set", "decided_date"}
    assert "IN ('APPROVED', 'CONTRACTED', 'EXPIRED')" in out and "decided_date >=" in out
    assert "approved_set" not in kpi_fix("Батлагдсан төлөвтэй хүсэлт",
                                         "SELECT COUNT(*) FROM loan_applications WHERE status_code = 'APPROVED'", M)[1]
    received = "SELECT COUNT(*) FROM loan_applications WHERE status_code = 'REJECTED' AND submitted_date >= '2025-01-01'"
    assert "decided_date" not in kpi_fix("2025 онд ирсэн хүсэлтээс татгалзсан нь хэд вэ?", received, M)[1]
    out, tags = kpi_fix("Салбар бүрийн татгалзсан хувийг гарга",
                        "SELECT 100.0 * COUNT(*) FILTER (WHERE a.status_code = 'REJECTED') / COUNT(*) FROM loan_applications AS a", M)
    assert tags == ["rate_denominator"]
    assert "COUNT(*) FILTER(WHERE a.status_code IN ('APPROVED', 'CONTRACTED', 'EXPIRED', 'REJECTED'))" in out


def test_negated_label_and_product_name():
    out, tags = kpi_fix("Ажлаас гараагүй ажилтнуудын тоо",
                        "SELECT COUNT(*) FROM employees WHERE employment_status = 'TERMINATED'", M)
    assert tags == ["negated_label"] and "employment_status <> 'TERMINATED'" in out
    keep = "SELECT COUNT(*) FROM employees WHERE employment_status = 'TERMINATED'"
    assert kpi_fix("Ажлаас гарсан ажилтнуудын тоо", keep, M) == (keep, [])
    out, tags = kpi_fix("Бүтээгдэхүүн бүрээр зээлийн тоог гарга",
                        "SELECT p.product_type_name, COUNT(*) FROM loans AS l JOIN products AS p ON p.product_id = l.product_id "
                        "GROUP BY p.product_type_name", M)
    assert tags == ["product_name"] and "p.product_name" in out and "product_type_name" not in out
    typed = "SELECT p.product_type_name, COUNT(*) FROM products AS p GROUP BY p.product_type_name"
    assert kpi_fix("Бүтээгдэхүүний төрөл бүрээр", typed, M) == (typed, [])
