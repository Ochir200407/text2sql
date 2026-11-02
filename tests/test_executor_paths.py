"""Executor-ийн бүх зам (3-р аудит L9 / H2): транзакцын төлөвтэй хуурамч psycopg2 холболт + ЖИНХЭНЭ
psycopg2.pool.ThreadedConnectionPool. PostgreSQL шаардлагагүй.

Шалгана: амжилт, SQL алдаа, timeout, fetch-ийн үеийн exception, EXPLAIN өртөг, холболт тасрах, max_result_bytes-ийн
server-side cursor зам (DECLARE/FETCH/CLOSE, too_large, нийт хугацааны deadline). Дуудлага бүрийн дараа: set_session
транзакц дотор хэзээ ч дуудагдаагүй, pool-д буцсан холболт бүр idle (транзакцгүй), slot бүгд чөлөөтэй.
"""

from __future__ import annotations

import threading

import psycopg2
import psycopg2.errors
import psycopg2.extensions as ext
import psycopg2.pool
import pytest

from mn_text2sql.db import executor as ex_mod
from mn_text2sql.db.executor import Executor

READY, BEGIN, INERROR = 1, 2, 3


class Info:
    def __init__(self, c):
        self.c = c

    @property
    def transaction_status(self):
        if self.c.closed:
            return ext.TRANSACTION_STATUS_UNKNOWN
        return {READY: ext.TRANSACTION_STATUS_IDLE, BEGIN: ext.TRANSACTION_STATUS_INTRANS,
                INERROR: ext.TRANSACTION_STATUS_INERROR}[self.c.status]


class FakeConn:
    """psycopg2 connection-ийн төлөв: эхний execute → BEGIN (autocommit=False), алдаа → INERROR, rollback → READY.
    set_session транзакц дотор → ProgrammingError (жинхэнэ psycopg2 шиг)."""

    n = 0

    def __init__(self, world):
        FakeConn.n += 1
        self.id, self.world = FakeConn.n, world
        self.status, self.autocommit, self.readonly, self.closed = READY, False, None, 0
        self.encoding, self.log = "SQL_ASCII", []
        self.info = Info(self)

    # --- psycopg2 API
    def set_client_encoding(self, enc):
        self._alive()
        if enc != self.encoding:
            if self.status != READY:
                self.status = READY
                self.log.append("ROLLBACK(encoding)")
            self.encoding = enc

    def set_session(self, readonly=None, autocommit=None, **kw):
        self._alive()
        if self.status != READY:
            self.world.events.append(("set_session_in_tx", self.id))
            raise psycopg2.ProgrammingError("set_session cannot be used inside a transaction")
        if readonly is not None:
            self.readonly = readonly
        if autocommit is not None:
            self.autocommit = autocommit
        self.log.append(f"set_session(ro={readonly}, ac={autocommit})")

    def cursor(self, name=None, **kw):
        return NamedCur(self, name) if name else Cur(self)

    def rollback(self):
        self._alive()
        self.status = READY
        self.log.append("ROLLBACK")

    def close(self):
        self.closed = 1

    # --- туслах
    def _alive(self):
        if self.closed:
            raise psycopg2.InterfaceError("connection already closed")

    def _stmt(self, sql):
        self._alive()
        if self.status == INERROR:
            raise psycopg2.errors.InFailedSqlTransaction("current transaction is aborted")
        if self.status == READY and not self.autocommit:
            self.status = BEGIN
            self.log.append("BEGIN READ ONLY" if self.readonly else "BEGIN")
        self.log.append(sql)

    def _fail(self, exc):
        self.status = INERROR
        raise exc


class Cur:
    def __init__(self, c):
        self.c, self.description, self.rows = c, None, []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql):
        c, w = self.c, self.c.world
        c._stmt(sql)
        if w.mode == "dropped" and not sql.startswith("SET"):
            c.closed = 2
            raise psycopg2.OperationalError("server closed the connection unexpectedly")
        if sql.startswith("EXPLAIN"):
            if w.mode == "sqlerr":
                c._fail(psycopg2.errors.UndefinedColumn("column x does not exist"))
            self.description = [("QUERY PLAN",)]
            self.rows = [([{"Plan": {"Total Cost": 5000.0 if w.mode == "cost" else 1.0}}],)]
            return
        if sql.startswith("SET"):
            return
        if w.mode == "sqlerr":
            c._fail(psycopg2.errors.UndefinedColumn("column x does not exist"))
        if w.mode == "timeout":
            c._fail(psycopg2.errors.QueryCanceled("canceling statement due to statement timeout"))
        if w.mode == "dropped":
            c.closed = 2
            raise psycopg2.OperationalError("server closed the connection unexpectedly")
        if w.mode == "nodesc":
            self.description = None
            return
        self.description = [("a",), ("b",)]
        self.rows = list(w.rows)

    def fetchone(self):
        return self.rows[0]

    def fetchmany(self, n):
        if self.c.world.mode == "fetchboom":
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")
        return self.rows[:n]


class NamedCur:
    """Server-side cursor: execute → DECLARE (алдаа = parse/plan), fetchmany → FETCH (timeout/тасралт = гүйцэтгэл),
    description эхний FETCH-ийн дараа. close: алдаатай/тасарсан транзакцад CLOSE илгээхгүй (psycopg2 шиг)."""

    def __init__(self, c, name):
        self.c, self.name, self.description, self.pos, self.closed = c, name, None, 0, False

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self.close()
        return False

    def execute(self, sql):
        c = self.c
        c._stmt(f"DECLARE {self.name} CURSOR WITHOUT HOLD FOR {sql}")
        c.world.declared.append(self.name)
        if c.world.mode == "sqlerr":
            c._fail(psycopg2.errors.UndefinedColumn("column x does not exist"))

    def fetchmany(self, n):
        c, w = self.c, self.c.world
        c._stmt(f"FETCH FORWARD {n} FROM {self.name}")
        w.clock_step()
        if w.mode == "timeout" and self.pos >= w.timeout_after:
            c._fail(psycopg2.errors.QueryCanceled("canceling statement due to statement timeout"))
        if w.mode == "dropped":
            c.closed = 2
            raise psycopg2.OperationalError("server closed the connection unexpectedly")
        if w.mode == "fetchboom":
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")
        out = list(w.rows[self.pos:self.pos + n])
        self.pos += len(out)
        self.description = [("a",), ("b",)]
        return out

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.c.closed or self.c.status != BEGIN:
            return
        self.c.log.append(f"CLOSE {self.name}")
        self.c.world.closed_cursors.append(self.name)


class World:
    def __init__(self):
        self.mode, self.rows, self.events = "ok", [(i, f"v{i}") for i in range(5)], []
        self.declared, self.closed_cursors, self.conns = [], [], []
        self.timeout_after = 0
        self.now, self.step = 0.0, 0.0

    def connect(self, *a, **kw):
        c = FakeConn(self)
        self.conns.append(c)
        return c

    def clock_step(self):
        self.now += self.step

    def perf_counter(self):
        return self.now


@pytest.fixture
def world(monkeypatch):
    w = World()
    monkeypatch.setattr(psycopg2.pool.psycopg2, "connect", w.connect)
    return w


def _ex(w, **kw):
    base = {"statement_timeout_ms": 1000, "max_rows": 3, "max_cost": 1000.0, "minconn": 1, "maxconn": 4}
    base.update(kw)
    return Executor("dbname=x", **base)


def _healthy(ex, w):
    """pool-д буцсан холболт бүр idle ба нээлттэй, ашиглагдаж буй холболтгүй, slot бүгд чөлөөтэй, set_session-ийг
    транзакц дотор дуудаагүй."""
    pool = ex._pool
    assert not w.events, w.events
    assert all(c.status == READY and not c.closed for c in pool._pool), [(c.id, c.status, c.closed) for c in pool._pool]
    assert not pool._used
    assert ex._slots._value == 4


# холболт тасарсан (conn.closed) → "connection" (eval runner дахин асууна); Python талын алдаа → "client_error";
# холболт амьд үеийн SQL-ийн алдаа → "db_error" (хуучин шигээ)
MODES = [("ok", True, None), ("sqlerr", False, "db_error"), ("timeout", False, "timeout"), ("nodesc", False, "db_error"),
         ("fetchboom", False, "client_error"), ("cost", False, "cost_too_high"), ("dropped", False, "connection")]


@pytest.mark.parametrize("bounded", [False, True], ids=["client_cursor", "server_cursor"])
@pytest.mark.parametrize("mode,ok,code", MODES, ids=[m[0] for m in MODES])
def test_run_paths_keep_pool_idle(world, mode, ok, code, bounded):
    ex = _ex(world, **({"max_result_bytes": 1_000_000} if bounded else {}))
    if bounded and mode == "nodesc":
        pytest.skip("DECLARE зөвхөн SELECT/VALUES-д — description-гүй FETCH байхгүй")
    world.mode = mode
    r = ex.run("SELECT a, b FROM t")
    assert r.ok is ok and r.error_code == code, (r.error_code, r.error)
    _healthy(ex, world)
    world.mode = "ok"                                   # дараагийн асуулга эрүүл pool-оос ажиллана
    r2 = ex.run("SELECT a, b FROM t")
    assert r2.ok and r2.rows == [(0, "v0"), (1, "v1"), (2, "v2")] and r2.truncated
    _healthy(ex, world)


@pytest.mark.parametrize("mode,want", [("ok", (1.0, None)), ("cost", (5000.0, None)), ("sqlerr", None),
                                       ("dropped", None)])
def test_explain_cost_paths(world, mode, want):
    ex = _ex(world)
    world.mode = mode
    cost, err = ex.explain_cost("SELECT a FROM t")
    if want is not None:
        assert (cost, err) == want
    else:
        assert cost is None and err
    _healthy(ex, world)


def test_readonly_session_set_once_outside_transaction(world):
    ex = _ex(world)
    for _ in range(3):
        assert ex.run("SELECT a, b FROM t").ok
    c = world.conns[0]
    assert sum(s.startswith("set_session") for s in c.log) == 1
    assert c.log.index("set_session(ro=True, ac=False)") < c.log.index("BEGIN READ ONLY")
    assert c.log[c.log.index("BEGIN READ ONLY") + 1:][:2] == ["SET TRANSACTION READ ONLY", "SET LOCAL statement_timeout = 1000"]
    _healthy(ex, world)


# ---------------- max_result_bytes: server-side cursor (H2) ----------------

def test_bounded_small_result_matches_default_path(world):
    plain, bounded = _ex(world, max_rows=10), _ex(world, max_rows=10, max_result_bytes=1_000_000)
    a, b = plain.run("SELECT a, b FROM t"), bounded.run("SELECT a, b FROM t")
    assert (a.ok, a.columns, a.rows, a.rowcount, a.truncated) == (b.ok, b.columns, b.rows, b.rowcount, b.truncated)
    assert b.rows == world.rows and not b.truncated and b.explain_cost == 1.0
    log = world.conns[-1].log
    decl = [s for s in log if s.startswith("DECLARE")]
    assert len(decl) == 1 and decl[0].endswith("FOR SELECT a, b FROM t")
    name = decl[0].split()[1]
    assert sum(s.startswith("FETCH FORWARD 1 ") for s in log) == 6    # 5 мөр + хоосон FETCH
    assert f"CLOSE {name}" in log and log.index(f"CLOSE {name}") < len(log) - 1 and log[-1] == "ROLLBACK"
    # EXPLAIN (өртгийн шалгалт) DECLARE-ээс өмнө, ижил транзакцад; cursor-ийн төлөвлөгөө = энгийн асуулгынх
    assert log.index("EXPLAIN (FORMAT JSON) SELECT a, b FROM t") < log.index(decl[0])
    assert log.index("SET LOCAL cursor_tuple_fraction = 1.0") < log.index(decl[0])
    assert log.index("BEGIN READ ONLY") < log.index("SET TRANSACTION READ ONLY") < log.index(decl[0])
    _healthy(bounded, world)


def test_bounded_truncation_fetches_one_extra_row(world):
    ex = _ex(world, max_rows=3, max_result_bytes=1_000_000)
    r = ex.run("SELECT a, b FROM t")
    assert r.ok and r.truncated and r.rows == world.rows[:3] and r.rowcount == 3
    assert sum(s.startswith("FETCH") for s in world.conns[-1].log) == 4   # max_rows + 1
    world.rows = world.rows[:3]                                          # яг max_rows мөр → truncated биш
    r = ex.run("SELECT a, b FROM t")
    assert r.ok and not r.truncated and r.rowcount == 3
    _healthy(ex, world)


def test_bounded_fetch_rows_chunks(world):
    ex = _ex(world, max_rows=10, max_result_bytes=1_000_000, fetch_rows=2)
    r = ex.run("SELECT a, b FROM t")
    assert r.ok and r.rows == world.rows
    assert [s for s in world.conns[-1].log if s.startswith("FETCH")] == [
        f"FETCH FORWARD 2 FROM {world.declared[-1]}"] * 3                  # 2 + 2 + 1 (< 2 → дууссан)
    _healthy(ex, world)


def test_bounded_too_large_stops_and_closes_cursor(world):
    world.rows = [(1, "x" * 10)] + [(i, "y" * 400_000) for i in range(2, 50)]   # жижиг эхэлж, дараа нь аварга
    ex = _ex(world, max_rows=1000, max_result_bytes=1_000_000)
    r = ex.run("SELECT a, b FROM t")
    assert not r.ok and r.error_code == "too_large" and "Result too large" in r.error and not r.rows
    fetches = [s for s in world.conns[-1].log if s.startswith("FETCH")]
    assert len(fetches) == 4                      # 3-р аварга мөрөнд (≈ 1.2 MB) зогссон — бүх 49 мөрийг татаагүй
    assert world.closed_cursors == world.declared[-1:]
    _healthy(ex, world)


def test_bounded_counts_nested_json_values(world):
    blob = {"items": [{"k": "z" * 1000} for _ in range(2000)]}         # psycopg2 json → dict/list (≈ 2+ MB)
    world.rows = [(1, blob)]
    r = _ex(world, max_result_bytes=1_000_000).run("SELECT a, b FROM t")
    assert not r.ok and r.error_code == "too_large"
    assert ex_mod._approx_bytes(blob) > 2_000_000 and ex_mod._approx_bytes(blob, limit=10_000) < 50_000


def test_bounded_timeout_during_fetch(world):
    world.mode, world.timeout_after = "timeout", 2
    ex = _ex(world, max_rows=10, max_result_bytes=1_000_000)
    r = ex.run("SELECT a, b FROM t")
    assert not r.ok and r.error_code == "timeout" and "statement_timeout 1000" in r.error
    assert world.closed_cursors == []             # алдаатай транзакцад CLOSE алгасна; rollback cursor-ийг хаана
    assert world.conns[-1].log[-1] == "ROLLBACK"
    _healthy(ex, world)


def test_bounded_overall_deadline(world, monkeypatch):
    """FETCH бүр statement_timeout-оос богино ч нийлбэр нь хэтэрвэл зогсооно; үлдсэн хугацааг SET LOCAL-оор багасгана."""
    monkeypatch.setattr(ex_mod, "time", world)    # time.perf_counter → world.now
    world.rows, world.step = [(i, "v") for i in range(100)], 0.15   # FETCH бүр 150 ms
    ex = _ex(world, max_rows=1000, max_result_bytes=1_000_000)
    r = ex.run("SELECT a, b FROM t")
    assert not r.ok and r.error_code == "timeout" and "fetching" in r.error
    log = world.conns[-1].log
    sets = [int(s.rsplit(" ", 1)[1]) for s in log if s.startswith("SET LOCAL statement_timeout")]
    assert sets[0] == 1000 and len(sets) > 2 and sets[1:] == sorted(sets[1:], reverse=True) and min(sets[1:]) < 300
    assert sum(s.startswith("FETCH") for s in log) <= 7          # ≈ 1000 / 150 мс
    assert world.closed_cursors == world.declared[-1:]
    _healthy(ex, world)


@pytest.mark.parametrize("mode,code", [("dropped", "connection"), ("fetchboom", "client_error"), ("sqlerr", "db_error")])
def test_bounded_errors_without_explain(world, mode, code):
    """max_cost-гүй (EXPLAIN-гүй): алдаа DECLARE (sqlerr) эсвэл FETCH (тасралт, decode) дээр гарна."""
    ex = _ex(world, max_cost=None, max_result_bytes=1_000_000)
    world.mode = mode
    r = ex.run("SELECT a, b FROM t")
    assert not r.ok and r.error_code == code, (r.error_code, r.error)
    assert world.declared and not any(s.startswith("EXPLAIN") for c in world.conns for s in c.log)
    _healthy(ex, world)
    world.mode = "ok"
    assert ex.run("SELECT a, b FROM t").ok
    _healthy(ex, world)


def test_bounded_default_none_keeps_client_cursor(world):
    ex = _ex(world)
    assert ex.max_result_bytes is None
    assert ex.run("SELECT a, b FROM t").ok
    assert not any(s.startswith(("DECLARE", "FETCH")) for s in world.conns[-1].log)


def test_concurrent_mixed_paths_keep_pool_consistent(world):
    plain, bounded = _ex(world, maxconn=4), _ex(world, maxconn=4, max_result_bytes=1_000_000)
    errors = []

    def worker(i):
        try:
            for _ in range(30):
                for ex in (plain, bounded):
                    r = ex.run("SELECT a, b FROM t")
                    assert r.ok, r.error
        except Exception as e:  # noqa: BLE001
            errors.append(repr(e))

    ts = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors and not world.events
    _healthy(plain, world)
    _healthy(bounded, world)
