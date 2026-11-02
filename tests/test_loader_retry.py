"""db/loader._execute_retrying: deadlock гарвал rollback хийж дахин оролдоно (V27 bootstrap, 2026-11-01)."""

import psycopg2.errors
import pytest

from mn_text2sql.db import loader


class FakeCursor:
    def __init__(self, conn):
        self.conn = conn

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql):
        self.conn.calls += 1
        if self.conn.calls <= self.conn.fail_times:
            raise psycopg2.errors.DeadlockDetected("deadlock detected")


class FakeConn:
    def __init__(self, fail_times):
        self.fail_times, self.calls, self.commits, self.rollbacks = fail_times, 0, 0, 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


def test_retries_after_deadlock(monkeypatch):
    monkeypatch.setattr(loader.time, "sleep", lambda s: None)
    conn = FakeConn(fail_times=2)
    loader._execute_retrying(conn, "SELECT 1", "x.sql", tries=3)
    assert (conn.calls, conn.rollbacks, conn.commits) == (3, 2, 1)


def test_gives_up_after_tries(monkeypatch):
    monkeypatch.setattr(loader.time, "sleep", lambda s: None)
    conn = FakeConn(fail_times=5)
    with pytest.raises(psycopg2.errors.DeadlockDetected):
        loader._execute_retrying(conn, "SELECT 1", "x.sql", tries=3)
    assert (conn.calls, conn.rollbacks, conn.commits) == (3, 3, 0)


def test_no_retry_without_error(monkeypatch):
    monkeypatch.setattr(loader.time, "sleep", lambda s: None)
    conn = FakeConn(fail_times=0)
    loader._execute_retrying(conn, "SELECT 1", "x.sql")
    assert (conn.calls, conn.rollbacks, conn.commits) == (1, 0, 1)
