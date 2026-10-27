"""E4: хувилбарын шүүлт (хоосон/0/NULL, DB-д байхгүй утга), дүрмийн татгалзал/тодруулга, '%q' улирлын засвар."""
from types import SimpleNamespace

from mn_text2sql.pipeline.dialect import sqlite_to_postgres
from mn_text2sql.pipeline.orchestrator import Candidate, Pipeline
from mn_text2sql.pipeline.selection import (
    LiteralChecker,
    fix_quarter_pg,
    is_degenerate,
    is_fromless,
    rule_decision,
)


def _res(rows, cols=("a",)):
    return SimpleNamespace(ok=True, rows=rows, rowcount=len(rows), columns=list(cols))


def test_degenerate():
    assert is_degenerate(_res([]))
    assert is_degenerate(_res([(None,), (None,)]))
    assert is_degenerate(_res([(0, None)], ("a", "b")))
    assert not is_degenerate(_res([(0,), (1,)]))
    assert not is_degenerate(_res([(5,)]))
    assert not is_degenerate(_res([(False,)]))          # логик False бол хариулт
    assert not is_degenerate(_res([("0",)]))
    assert not is_degenerate(SimpleNamespace(ok=False, rows=[], rowcount=0, columns=[]))


def test_rules():
    assert rule_decision("Зээлийн мэргэжилтнүүдийн дундаж цалин хэд вэ?") == ("refuse", "missing_data")
    assert rule_decision("Цалингийн зээлийн тоо хэд вэ?") is None
    assert rule_decision("Ирэх оны NPL хэд болох вэ?")[0] == "refuse"
    assert rule_decision("Монголбанкны бодлогын хүү хэд вэ?") == ("refuse", "external_knowledge")
    assert rule_decision("Харилцагчид өгөх гэрээ бичиж өгөөч") == ("refuse", "non_sql")
    assert rule_decision("Скорингийн зөвлөмжөөр татгалзсан хүсэлт хэд вэ?") is None
    assert rule_decision("Сүүлийн 3 сарын олголт хэд вэ?") == ("clarify", "vague")
    assert rule_decision("Сүүлийн 3 сарын (2026 оны 4-р сараас 6-р сар) олголт хэд вэ?") is None
    assert rule_decision("Хамгийн том 5 харилцагч хэн бэ?") == ("clarify", "vague")
    assert rule_decision("Үлдэгдлээр хамгийн том 5 харилцагч хэн бэ?") is None
    assert rule_decision("Сүүлийн 3 сарын олголт хэд вэ?", clarify=False) is None
    assert rule_decision("Хугацаа хэтэрсэн зээлийн тоо хэд вэ?") is None


def test_fromless_and_quarter():
    assert is_fromless("SELECT 3") and not is_fromless("SELECT count(*) FROM t2s.loans")
    assert "'YYYY-Q'" in sqlite_to_postgres("SELECT strftime('%Y-%q', d) FROM t")
    assert "LIKE '%quick%'" in sqlite_to_postgres("SELECT x FROM t WHERE name LIKE '%quick%'")
    assert fix_quarter_pg("SELECT TO_CHAR(d, 'YYYY-%q') FROM t") == "SELECT TO_CHAR(d, 'YYYY-Q') FROM t"


class _FakeEx:
    """information_schema ба EXISTS асуулгыг дуурайна: loans.status-д зөвхөн ACTIVE, CLOSED."""
    def run(self, sql):
        if "information_schema" in sql:
            return SimpleNamespace(ok=True, rows=[("loans", "status", "text"), ("loans", "amount", "numeric")])
        ok = any(f"'{v}'" in sql for v in ("ACTIVE", "CLOSED"))
        return SimpleNamespace(ok=True, rows=[(ok,)])


def test_literal_checker_and_filtered_vote():
    lc = LiteralChecker(_FakeEx())
    assert lc.bad_literals("SELECT count(*) FROM t2s.loans WHERE status = 'ACTIVE'") == []
    assert lc.bad_literals("SELECT count(*) FROM t2s.loans l WHERE l.status IN ('ACTIVE', 'OVERDUE')") == \
        [("loans", "status", "OVERDUE")]
    assert lc.bad_literals("SELECT count(*) FROM t2s.loans WHERE amount = '5'") == []   # текст биш багана
    # 3 нь DB-д байхгүй утгатай (ижил үр дүн), 2 нь зөв → шүүлтгүй бол буруу олонх, шүүлттэй бол зөв
    bad = [Candidate(ok=True, result_hash="h0", bad_literals=[("loans", "status", "X")]) for _ in range(3)]
    good = [Candidate(ok=True, result_hash="h1") for _ in range(2)]
    deg = [Candidate(ok=True, result_hash="h2", degenerate=True) for _ in range(3)]
    cands = bad + good + deg
    assert Pipeline.vote(cands)[0].result_hash == "h0"
    ch, conf, _ = Pipeline.vote(cands, use_filter=True)
    assert ch.result_hash == "h1" and conf == 2 / 8
    only_bad = [Candidate(ok=True, result_hash="h0", bad_literals=[("t", "c", "x")]) for _ in range(2)]
    assert Pipeline.vote(only_bad, use_filter=True)[0].result_hash == "h0"   # бүгд муу бол хэвээр


def test_quarter_formats_online_offline():
    from mn_text2sql.pipeline.dialect import quarter_format_pg
    assert quarter_format_pg("%q") == "Q"
    assert quarter_format_pg("%Y-%q") == "YYYY-Q"
    assert quarter_format_pg("%Y-Q%q") == 'YYYY"-Q"Q'
    assert quarter_format_pg("Q%q %Y") == '"Q"Q YYYY'
    online = sqlite_to_postgres("SELECT strftime('%Y-Q%q', d) FROM t WHERE n LIKE '%quick%'")
    assert "'YYYY\"-Q\"Q'" in online and "LIKE '%quick%'" in online
    offline = fix_quarter_pg("SELECT TO_CHAR(CAST(d AS TIMESTAMP), 'YYYY-Q%q') FROM t WHERE n LIKE '%quick%'")
    assert "'YYYY\"-Q\"Q'" in offline and "LIKE '%quick%'" in offline
    assert fix_quarter_pg("SELECT 1") == "SELECT 1"


def test_literal_checker_accepts_enum_codes_without_rows():
    lc = LiteralChecker(_FakeEx(), enums={("loans", "status"): {"ACTIVE", "CLOSED", "OVERDUE"}})
    assert lc.bad_literals("SELECT count(*) FROM t2s.loans WHERE status = 'OVERDUE'") == []
    assert lc.bad_literals("SELECT count(*) FROM t2s.loans WHERE status = 'BOGUS'") == [("loans", "status", "BOGUS")]


def test_quarter_format_percent_escapes():
    from mn_text2sql.pipeline.dialect import quarter_format_pg
    assert quarter_format_pg("%%q") == '%"q"'
    assert quarter_format_pg("%Y-%q%") == "YYYY-Q%"
