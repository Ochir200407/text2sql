"""vote_lint (pipeline/sql_lint.py): chasm/fan (semantic_model-ийн FK), NPL-ийг тоогоор, идэвхтэй багцгүй — санал хураалтаас
унасан хувилбарыг (унаагүй нь байвал) хасна. Анхдагч унтраалттай, fingerprint өөрчлөгдөхгүй."""
from types import SimpleNamespace as NS

from mn_text2sql.knowledge.semantic import cached_model
from mn_text2sql.pipeline.options import RunOptions
from mn_text2sql.pipeline.orchestrator import Pipeline
from mn_text2sql.pipeline.sql_lint import child_map, lint_sql

KIDS = child_map(cached_model("knowledge/semantic_model.yaml"))


def test_child_map_from_fks():
    assert "customers" in KIDS["loans"] and "customers" in KIDS["deposits"]


def test_chasm_and_fan():
    chasm = ("SELECT c.segment_name, SUM(l.principal_outstanding_mnt), SUM(d.current_balance_mnt) FROM t2s.loans l "
             "JOIN t2s.customers c ON c.customer_id = l.customer_id JOIN t2s.deposits d ON d.customer_id = c.customer_id "
             "GROUP BY c.segment_name")
    assert "chasm" in lint_sql("Сегмент бүрээр", chasm, KIDS)
    fan = ("SELECT b.branch_name, SUM(c.annual_income) FROM t2s.customers c JOIN t2s.loans l ON l.customer_id = "
           "c.customer_id JOIN t2s.branches b ON b.branch_id = c.branch_id GROUP BY b.branch_name")
    assert "fan" in lint_sql("Салбар бүрээр", fan, KIDS)
    cte = ("WITH lo AS (SELECT customer_id, SUM(principal_outstanding_mnt) AS x FROM t2s.loans GROUP BY 1), "
           "de AS (SELECT customer_id, SUM(current_balance_mnt) AS y FROM t2s.deposits GROUP BY 1) "
           "SELECT SUM(lo.x), SUM(de.y) FROM lo JOIN de ON de.customer_id = lo.customer_id")
    assert lint_sql("Сегмент бүрээр", cte, KIDS) == set()
    child_sum = ("SELECT b.branch_name, SUM(l.principal_outstanding_mnt) FROM t2s.loans l JOIN t2s.branches b "
                 "ON b.branch_id = l.branch_id WHERE l.is_active_portfolio GROUP BY 1")
    assert lint_sql("Салбар бүрийн багцын үлдэгдэл", child_sum, KIDS) == set()   # хүүхдийн хэмжүүр — давхардалгүй


def test_kpi_flags():
    by_count = "SELECT 100.0 * SUM(CASE WHEN is_npl THEN 1 ELSE 0 END) / COUNT(*) FROM t2s.loans WHERE is_active_portfolio"
    by_amount = ("SELECT 100.0 * SUM(principal_outstanding_mnt) FILTER (WHERE is_npl) / SUM(principal_outstanding_mnt) "
                 "FROM t2s.loans WHERE is_active_portfolio")
    assert lint_sql("Хүйсээр NPL хувийг гарга", by_count, KIDS) == {"npl_by_count"}
    assert lint_sql("Хүйсээр NPL хувийг гарга", by_amount, KIDS) == set()
    assert lint_sql("Зээлийн хэдэн хувь нь NPL вэ?", by_count, KIDS) == set()          # тоогоор асуусан
    no_ap = "SELECT SUM(principal_outstanding_mnt) FROM t2s.loans"
    assert lint_sql("Зээлийн багцын үлдэгдэл хэд вэ?", no_ap, KIDS) == {"no_active_portfolio"}
    assert lint_sql("Нийт олгосон зээлийн дүн хэд вэ?", no_ap, KIDS) == set()


def _c(h, flags=None, ok=True):
    return NS(ok=ok, result_hash=h, degenerate=False, bad_literals=None, lint_flags=flags)


def test_vote_drops_flagged_only_when_clean_exists():
    a1, a2, b = _c("A", ["fan"]), _c("A", ["fan"]), _c("B")
    assert Pipeline.vote([a1, a2, b], False)[0] is a1            # lint-гүй: олонх
    assert Pipeline.vote([a1, a2, b], False, True)[0] is b       # lint: цэвэр нь ялна
    assert Pipeline.vote([a1, a2], False, True)[0] is a1         # бүгд унасан — хэвээр


def test_option_default_and_cli(monkeypatch):
    assert RunOptions().vote_lint is False and "vote_lint" not in RunOptions().fingerprint()
    import mn_text2sql.eval.reselect as rs
    from mn_text2sql import cli
    seen = {}
    monkeypatch.setattr(rs, "reselect_run", lambda *a, **k: seen.update(k) or [])
    assert cli.main(["reselect", "--from", "M1rr", "--to", "X", "--lint"]) == 0 and seen["lint"] is True


def test_domain_unrequested_active_label_and_antijoin():
    from mn_text2sql.pipeline.sql_lint import label_codes
    labels = label_codes(cached_model("knowledge/semantic_model.yaml"))
    loans_only = "SELECT COUNT(*) FROM t2s.loan_payments WHERE payment_type = 'EARLY_FULL'"
    assert "domain_mismatch" in lint_sql("Хугацаанаас өмнө хаагдсан хадгаламж хэд вэ?", loans_only, KIDS)
    assert "domain_mismatch" not in lint_sql("Хугацаанаас өмнө төлөгдсөн зээл хэд вэ?", loans_only, KIDS)
    active = "SELECT COUNT(*) FROM t2s.deposits WHERE status = 'ACTIVE' AND opened_date >= '2026-01-01'"
    assert "unrequested_active" in lint_sql("Энэ онд шинээр нээгдсэн хадгаламж хэд вэ?", active, KIDS)
    assert "unrequested_active" not in lint_sql("Энэ онд нээгдсэн идэвхтэй хадгаламж хэд вэ?", active, KIDS)
    broad = "SELECT COUNT(*) FROM t2s.collection_promises WHERE status IN ('PARTIALLY_KEPT', 'CANCELLED')"
    assert "label_mismatch" in lint_sql("Биелээгүй төлөх амлалт хэд вэ?", broad, KIDS, labels)
    exact = "SELECT COUNT(*) FROM t2s.collection_promises WHERE status = 'BROKEN'"
    assert "label_mismatch" not in lint_sql("Биелээгүй төлөх амлалт хэд вэ?", exact, KIDS, labels)
    anti = ("SELECT b.branch_name, SUM(c.annual_income) FROM t2s.customers c LEFT JOIN t2s.loans l ON l.customer_id = "
            "c.customer_id JOIN t2s.branches b ON b.branch_id = c.branch_id WHERE l.loan_id IS NULL GROUP BY 1")
    assert "fan" not in lint_sql("Зээлгүй харилцагчийн орлого", anti, KIDS)            # anti-join мөр үржүүлэхгүй
    per_row = ("SELECT SUM(h.total_outstanding * l.fx_rate_to_mnt) FROM t2s.loan_dpd_history h JOIN t2s.loans l "
               "ON l.loan_id = h.loan_id")
    assert lint_sql("Сар бүрийн үлдэгдэл", per_row, KIDS) == set()                       # мөр бүрийн үржвэр — fan биш
