"""Executor read-only (review C7/S9): PostgreSQL шаардлагагүй — pool/холболтыг mock-лоно."""

from __future__ import annotations

from unittest import mock

import psycopg2

from mn_text2sql.db import executor as ex_mod
from mn_text2sql.db.executor import Executor


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn
        self.description = None
        self._rows = []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql):
        self.conn.statements.append(sql)
        if sql.startswith("EXPLAIN"):
            self.description = [("QUERY PLAN",)]
            self._rows = [([{"Plan": {"Total Cost": 12.5}}],)]
        elif sql.upper().startswith("SELECT"):
            self.description = [("a",), ("b",)]
            self._rows = [(1, "x"), (2, "y"), (3, "z")]

    def fetchone(self):
        return self._rows[0]

    def fetchmany(self, n):
        return self._rows[:n]


class FakeConn:
    def __init__(self):
        self.statements: list[str] = []
        self.session_calls: list[dict] = []
        self.autocommit = False
        self.readonly = None
        self.closed = 0
        self.rollbacks = 0

    def set_client_encoding(self, enc):
        pass

    def set_session(self, **kw):
        self.session_calls.append(kw)
        if "readonly" in kw:
            self.readonly = kw["readonly"]
        if "autocommit" in kw:
            self.autocommit = kw["autocommit"]

    def cursor(self):
        return FakeCursor(self)

    def rollback(self):
        self.rollbacks += 1


class FakePool:
    def __init__(self, conns):
        self.conns = list(conns)
        self.put = []

    def getconn(self):
        return self.conns.pop(0)

    def putconn(self, conn, close=False):
        self.put.append((conn, close))
        if not close:
            self.conns.append(conn)

    def closeall(self):
        pass


def _executor(conns, **kw):
    pool = FakePool(conns)
    with mock.patch.object(ex_mod.psycopg2.pool, "ThreadedConnectionPool", return_value=pool):
        ex = Executor("dbname=x", statement_timeout_ms=1234, **kw)
    return ex, pool


def test_run_sets_session_readonly_and_timeout():
    conn = FakeConn()
    ex, _ = _executor([conn])
    res = ex.run("SELECT a, b FROM t")
    assert res.ok and res.columns == ["a", "b"] and res.rows == [(1, "x"), (2, "y"), (3, "z")]
    assert conn.session_calls == [{"readonly": True, "autocommit": False}]
    assert conn.readonly is True and conn.autocommit is False
    # BEGIN READ ONLY (psycopg2-ийн далд транзакц дотор үр нөлөөгүй) дахиж илгээгдэхгүй
    assert not any(s.upper().startswith("BEGIN") for s in conn.statements)
    assert conn.statements[:2] == ["SET TRANSACTION READ ONLY", "SET LOCAL statement_timeout = 1234"]
    assert conn.rollbacks == 1


def test_pooled_connection_reused_and_reset_if_changed():
    conn = FakeConn()
    ex, _ = _executor([conn])
    ex.run("SELECT 1")
    ex.run("SELECT 2")
    assert len(conn.session_calls) == 1          # аль хэдийн readonly → дахин дуудахгүй
    conn.readonly = False                         # хэн нэгэн сессийг өөрчилсөн бол
    ex.run("SELECT 3")
    assert len(conn.session_calls) == 2 and conn.readonly is True


def test_each_pooled_connection_is_readonly():
    c1, c2 = FakeConn(), FakeConn()
    ex, pool = _executor([c1, c2])
    a, b = ex._conn(), ex._conn()
    assert c1.readonly is True and c2.readonly is True
    ex._put(a)
    ex._put(b)


def test_explain_cost_readonly():
    conn = FakeConn()
    ex, _ = _executor([conn])
    cost, err = ex.explain_cost("SELECT 1")
    assert err is None and cost == 12.5
    assert conn.readonly is True
    assert conn.statements[:2] == ["SET TRANSACTION READ ONLY", "SET LOCAL statement_timeout = 1234"]


def test_run_with_max_cost_and_truncation():
    conn = FakeConn()
    ex, _ = _executor([conn], max_cost=100.0, max_rows=2)
    res = ex.run("SELECT a, b FROM t")
    assert res.ok and res.truncated and res.rowcount == 2 and res.explain_cost == 12.5
    assert conn.statements[0] == "SET TRANSACTION READ ONLY"


def test_set_session_failure_releases_slot_and_closes():
    conn = FakeConn()

    def boom(**kw):
        raise psycopg2.OperationalError("conn lost")

    conn.set_session = boom
    ex, pool = _executor([conn], maxconn=1)
    import pytest
    with pytest.raises(psycopg2.OperationalError):
        ex._conn()
    assert pool.put == [(conn, True)]            # эвдэрсэн холболтыг хаана
    assert ex._slots.acquire(blocking=False)      # slot суллагдсан
