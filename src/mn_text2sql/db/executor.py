"""Read-only гүйцэтгэгч: транзакц бүр READ ONLY, statement_timeout, EXPLAIN-ийн өртгийн хязгаар, мөрийн хязгаар."""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field

import psycopg2
import psycopg2.pool

log = logging.getLogger(__name__)


@dataclass
class ExecResult:
    ok: bool
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    rowcount: int = 0
    truncated: bool = False
    error: str | None = None
    error_code: str | None = None      # db_error | timeout | cost_too_high | connection
    elapsed_ms: float = 0.0
    explain_cost: float | None = None

    def preview(self, n: int = 10, width: int = 60) -> str:
        """Загварт буцаах товч үр дүн (sqrl-ийн <observation>, засварын prompt). Хэт урт утгыг тайрна."""
        if not self.ok:
            return f"ERROR: {self.error}"
        lines = ["\t".join(self.columns)]
        for row in self.rows[:n]:
            lines.append("\t".join(str(v)[:width] if v is not None else "NULL" for v in row))
        if self.rowcount > n:
            lines.append(f"... ({self.rowcount} мөр)")
        if self.rowcount == 0:
            lines.append("(хоосон үр дүн / empty result)")
        return "\n".join(lines)


class Executor:
    """Thread-safe. Нэг Executor = нэг DSN (bot role) = нэг DB."""

    def __init__(self, dsn: str, statement_timeout_ms: int = 20000, max_rows: int = 100000,
                 max_cost: float | None = None, minconn: int = 1, maxconn: int = 16):
        self.dsn = dsn
        self.timeout = int(statement_timeout_ms)
        self.max_rows = max_rows
        self.max_cost = max_cost
        self._pool = psycopg2.pool.ThreadedConnectionPool(minconn, maxconn, dsn,
                                                          application_name="mn_text2sql_exec")
        self._lock = threading.Lock()
        # ThreadedConnectionPool нь бүх холболт завгүй үед хүлээхгүй (PoolError) — semaphore-оор хүлээлгэнэ
        self._slots = threading.BoundedSemaphore(maxconn)

    def close(self) -> None:
        self._pool.closeall()

    def _conn(self):
        self._slots.acquire()
        try:
            conn = self._pool.getconn()
        except Exception:
            self._slots.release()
            raise
        conn.set_client_encoding("UTF8")
        conn.autocommit = False
        return conn

    def _put(self, conn, broken: bool = False) -> None:
        try:
            self._pool.putconn(conn, close=broken)
        except psycopg2.pool.PoolError:
            pass
        finally:
            self._slots.release()

    def explain_cost(self, sql: str) -> tuple[float | None, str | None]:
        conn = self._conn()
        broken = False
        try:
            with conn.cursor() as cur:
                cur.execute("BEGIN READ ONLY")
                cur.execute(f"SET LOCAL statement_timeout = {self.timeout}")
                cur.execute("EXPLAIN (FORMAT JSON) " + sql)
                plan = cur.fetchone()[0]
                if isinstance(plan, str):
                    plan = json.loads(plan)
                return float(plan[0]["Plan"]["Total Cost"]), None
        except psycopg2.Error as e:
            return None, _pg_error(e)
        except Exception as e:  # холболт тасарсан г.м.
            broken = True
            return None, str(e)
        finally:
            try:
                conn.rollback()
            except psycopg2.Error:
                broken = True
            self._put(conn, broken)

    def run(self, sql: str, max_rows: int | None = None, explain: bool = False) -> ExecResult:
        max_rows = max_rows or self.max_rows
        t0 = time.perf_counter()
        conn = self._conn()
        broken = False
        cost = None
        try:
            with conn.cursor() as cur:
                cur.execute("BEGIN READ ONLY")
                cur.execute(f"SET LOCAL statement_timeout = {self.timeout}")
                if explain or self.max_cost:
                    cur.execute("EXPLAIN (FORMAT JSON) " + sql)
                    plan = cur.fetchone()[0]
                    if isinstance(plan, str):
                        plan = json.loads(plan)
                    cost = float(plan[0]["Plan"]["Total Cost"])
                    if self.max_cost and cost > self.max_cost:
                        return ExecResult(False, error=f"Асуулгын өртөг хэт өндөр (EXPLAIN cost {cost:.0f} > "
                                                       f"{self.max_cost:.0f}). Query too expensive.",
                                          error_code="cost_too_high", explain_cost=cost,
                                          elapsed_ms=(time.perf_counter() - t0) * 1000)
                cur.execute(sql)
                if cur.description is None:
                    return ExecResult(False, error="Үр дүн буцаагаагүй (SELECT биш).", error_code="db_error")
                cols = [d[0] for d in cur.description]
                rows = cur.fetchmany(max_rows + 1)
                truncated = len(rows) > max_rows
                rows = rows[:max_rows]
                return ExecResult(True, cols, rows, len(rows), truncated, elapsed_ms=(time.perf_counter() - t0) * 1000,
                                  explain_cost=cost)
        except psycopg2.errors.QueryCanceled as e:
            return ExecResult(False, error=f"Хугацаа хэтэрлээ (statement_timeout {self.timeout} ms): {_pg_error(e)}",
                              error_code="timeout", elapsed_ms=(time.perf_counter() - t0) * 1000, explain_cost=cost)
        except psycopg2.Error as e:
            if conn.closed:
                broken = True
            return ExecResult(False, error=_pg_error(e), error_code="db_error",
                              elapsed_ms=(time.perf_counter() - t0) * 1000, explain_cost=cost)
        except Exception as e:
            broken = True
            return ExecResult(False, error=str(e), error_code="connection",
                              elapsed_ms=(time.perf_counter() - t0) * 1000)
        finally:
            try:
                conn.rollback()
            except psycopg2.Error:
                broken = True
            self._put(conn, broken)


def _pg_error(e: psycopg2.Error) -> str:
    msg = (e.pgerror or str(e) or "").strip()
    lines = [ln for ln in msg.splitlines() if ln.strip()]
    return " | ".join(lines[:4])[:600]
