from types import SimpleNamespace as NS

from mn_text2sql.eval.pick import parse_choice, pick_pair, preview, top_two
from mn_text2sql.llm.client import MockLLM


def _c(sql, h, rows=((1,),)):
    res = NS(ok=True, columns=["n"], rows=list(rows), rowcount=len(rows))
    return NS(sql=sql, ok=True, result_hash=h, result=res, degenerate=False, bad_literals=None)


def test_top_two_picks_runner_up():
    a1, a2, b, c = _c("A", "h1"), _c("A2", "h1"), _c("B", "h2"), _c("C", "h3")
    assert top_two([b, a1, a2, c], a1) == (a1, b)       # тэнцвэл эхэнд байгаа (b)
    assert top_two([a1, a2], a1) is None


def test_pick_pair_needs_consistent_swap():
    a, b = _c("SELECT 1", "h1"), _c("SELECT 2", "h2")
    # Query A-д ямар SQL байгаагаас үл хамааран үргэлж «SELECT 2»-ийг сонгоно → хоёр асуултад нийцтэй → swap
    smart = MockLLM(lambda m: "<answer>A</answer>" if "Query A:\nSELECT 2" in m[1]["content"] else "<answer>B</answer>")
    assert pick_pair(smart, "q", a, b) == (b, "swap")
    # байрлалын хазайлт (үргэлж A) → нийцгүй → санал хураалт хэвээр
    assert pick_pair(MockLLM(lambda m: "<think>x</think><answer>A</answer>"), "q", a, b) == (a, "keep")


def test_preview_truncates():
    assert "(3 rows)" in preview(_c("x", "h", rows=((1,), (2,), (3,))).result)


def test_parse_choice_ignores_stray_letters():
    assert parse_choice("<think>a b</think><answer> B </answer>") == "B"
    assert parse_choice("A") == "A"
    assert parse_choice("<answer>SELECT a.x FROM t a</answer>") is None   # SQL доторх alias
    assert parse_choice("I think a better query is B") is None            # тайлбар — тодорхойгүй


# ------------------------------------------------------------------ T2: нөхцөлийн түвшний pick (--pick-diff)
from mn_text2sql.eval.pick import SYSTEM, SYSTEM_DIFF, Diff, diff_clauses  # noqa: E402

POSTED = "SELECT count(*) FROM t2s.loan_payments p WHERE p.payment_date >= '2026-05-01' AND p.status = 'POSTED'"
NOPOST = "SELECT COUNT(*) FROM t2s.loan_payments WHERE payment_date >= '2026-05-01'"


def test_diff_clauses_where_alias_insensitive():
    assert diff_clauses(POSTED, NOPOST) == [Diff("where", ("status = 'POSTED'",), ())]
    assert diff_clauses(POSTED, POSTED.replace(" p ", " lp ").replace("p.", "lp.")) == []   # alias ялгаа биш


def test_diff_clauses_tables_aggregates_group_by():
    a = "SELECT b.branch_name, SUM(l.x) FROM t2s.loans l JOIN t2s.branches b ON b.id = l.bid GROUP BY 1"
    b = "SELECT b.branch_name, AVG(l.x) FROM t2s.loan_applications l JOIN t2s.branches b ON b.id = l.bid GROUP BY b.branch_name"
    d = diff_clauses(a, b)
    assert [x.kind for x in d] == ["tables", "aggregates"]                 # GROUP BY 1 == GROUP BY branch_name
    assert d[0].only_a == ("loans",) and d[0].only_b == ("loan_applications",)
    assert "SUM(x)" in d[1].describe() and "AVG(x)" in d[1].describe()
    g = diff_clauses("SELECT a, count(*) FROM t GROUP BY a", "SELECT a, count(DISTINCT c) FROM t GROUP BY a, b")
    assert {x.kind for x in g} == {"group_by", "aggregates"}
    # CTE-ийн нэр хүснэгт биш; зөвхөн ORDER BY ялгаа → хоосон (бүтэн асуулт руу буцна)
    assert diff_clauses("WITH m AS (SELECT a FROM t2s.loans) SELECT a FROM m",
                        "WITH m AS (SELECT a FROM t2s.loans) SELECT a FROM m ORDER BY a") == []
    assert diff_clauses("SELECT 1 FROM t", "SELEC broken (((") is None
    assert Diff("where", ("x",), ()).swapped() == Diff("where", (), ("x",))


def _sys_of(m):
    return m[0]["content"]


def test_pick_diff_asks_only_differences_and_keeps_ab_ba():
    a, b = _c(NOPOST, "h1"), _c(POSTED, "h2")
    seen = []

    def reply(m):
        seen.append(m)
        # «status = 'POSTED'» нь A талд байвал A, B талд байвал B — нийцтэй сонголт → swap
        return "<answer>A</answer>" if "A has status = 'POSTED'" in m[1]["content"] else "<answer>B</answer>"
    assert pick_pair(MockLLM(reply), "How many successful repayments in May?", a, b, diff=True) == (b, "swap")
    assert len(seen) == 2 and all(_sys_of(m) == SYSTEM_DIFF for m in seen)
    assert "Differences between the two queries:\n- WHERE conditions: A has (none) | B has status = 'POSTED'" \
        in seen[0][1]["content"]
    assert "A has status = 'POSTED' | B has (none)" in seen[1][1]["content"]    # B/A асуултад тал солигдсон
    # байрлалын хазайлт → санал хураалт хэвээр
    assert pick_pair(MockLLM(lambda m: "<answer>A</answer>"), "q", a, b, diff=True) == (a, "keep")


def test_pick_diff_falls_back_to_full_prompt():
    seen = []

    def reply(m):
        seen.append(_sys_of(m))
        return "<answer>A</answer>"
    many_a = "SELECT SUM(x) FROM t2s.loans WHERE a = 1 GROUP BY c"
    many_b = "SELECT AVG(x) FROM t2s.deposits WHERE a = 2 GROUP BY d"       # 4 төрөл > MAX_DIFFS
    pick_pair(MockLLM(reply), "q", _c(many_a, "h1"), _c(many_b, "h2"), diff=True)
    pick_pair(MockLLM(reply), "q", _c("SELECT 1", "h1"), _c("SELECT 2", "h2"), diff=True)   # ялгаа олдсонгүй
    pick_pair(MockLLM(reply), "q", _c(NOPOST, "h1"), _c(POSTED, "h2"))                      # diff=False (анхдагч)
    assert seen == [SYSTEM] * 6


def test_pick_pair_trace_records_both_orders():
    """Оношлогоо: trace-д хоёр дарааллын parse-лагдсан хариу («BA» = swap, «AA» = байрлалын хазайлт, «-» = parse алдаа)."""
    class _C:
        def __init__(self, sql):
            self.sql, self.result = sql, None

    class _LLM:
        def __init__(self, answers):
            self.answers = list(answers)

        def chat(self, messages, **kw):
            from mn_text2sql.llm.client import LLMResponse
            return LLMResponse([self.answers.pop(0)], 1, 1, 0.0, "mock", {})
    a, b = _C("SELECT 1"), _C("SELECT 2")
    tr = []
    chosen, d = pick_pair(_LLM(["<answer>B</answer>", "<answer>A</answer>"]), "q", a, b, trace=tr)
    assert (chosen, d, tr) == (b, "swap", ["BA"])
    tr = []
    chosen, d = pick_pair(_LLM(["<answer>A</answer>", "no answer"]), "q", a, b, trace=tr)
    assert d == "keep" and tr == ["A-"]


def test_parse_choice_letter_variants_and_sql_answer():
    """V28 dev: 95 pick-ийн 87-д A/B задлагдаагүй — Arctic сургалтын хэлбэрээрээ <answer> дотор SQL бичдэг (Ch2)."""
    assert parse_choice("<answer>Query B</answer>") == "B"
    assert parse_choice("<answer>**A**</answer>") == "A"
    assert parse_choice("<answer> A. </answer>") == "A"
    assert parse_choice("<answer>Both</answer>") is None
    sql_ans = "<think>compare</think><answer>\n```sql\nselect  2 ;\n```\n</answer>"
    assert parse_choice(sql_ans, "SELECT 1", "SELECT 2") == "B"          # хэвийн болгосноор B-тэй ижил
    assert parse_choice(sql_ans, "SELECT 2", "SELECT 1") == "A"
    assert parse_choice(sql_ans) is None                                   # хувилбаргүй — өмнөх зан төлөв
    assert parse_choice(sql_ans, "SELECT 1", "SELECT 3") is None           # аль нь ч биш — таамаглахгүй
    # reasoning доторх (тасарсан эсвэл хаагдсан) SQL-ийг сонголт гэж үзэхгүй
    assert parse_choice("<think>Query A: SELECT 1 is", "SELECT 1", "SELECT 2") is None
    assert parse_choice("<think><answer>SELECT 1</answer></think> hmm", "SELECT 1", "SELECT 2") is None


def test_pick_pair_maps_sql_answers_to_letters():
    a, b = _c("SELECT 1", "h1"), _c("SELECT 2", "h2")
    # үргэлж «SELECT 2»-ийг <answer> доторх SQL-ээр сонгоно → хоёр дараалалд нийцтэй (B, A) → swap
    always2 = MockLLM(lambda m: "<think>x</think><answer>```sql\nSELECT 2\n```</answer>")
    tr = []
    assert pick_pair(always2, "q", a, b, trace=tr) == (b, "swap") and tr == ["BA"]
