"""Read-only гүйцэтгэгч: транзакц бүр READ ONLY, statement_timeout, EXPLAIN-ийн өртгийн хязгаар, мөрийн хязгаар.

max_result_bytes (заавал биш, анхдагч None = хуучин зан төлөв, үнэлгээ үүнийг ашигладаггүй): вэб үйлчилгээнд үр дүнгийн
санах ойн төсөв. psycopg2-ийн энгийн (client-side) cursor нь `execute()` дотор бүх үр дүнг санах ойд татдаг тул мөрийн
хязгаар (LIMIT 1000) хэмжээг барихгүй — `SELECT repeat('x', 100000000) FROM … LIMIT 1000` ≈ 100 GB. Төсөвтэй үед
server-side (нэрлэсэн) cursor-оор `fetch_rows` мөрөөр татаж, утгын ойролцоо хэмжээг нэмж, төсөв хэтэрмэгц
error_code="too_large" алдаа буцаана (cursor-ийг хааж, rollback хийнэ). statement_timeout нь FETCH бүрт тусдаа
үйлчилдэг тул нийт хугацааг (DECLARE-аас) statement_timeout-оор (+ ≤ 10% зөрүү) мөн хязгаарлана.
Үлдэх эрсдэл: нэг FETCH-ийн мөр (анхдагч fetch_rows=1 → нэг мөр) PostgreSQL-ийн нэг утгын/мөрийн хязгаар (~1 GB) хүртэл
том байж болно — төсөв нь түүнийг татсаны ДАРАА л шалгагдана.
"""

from __future__ import annotations

import itertools
import json
import logging
import sys
import threading
import time
from dataclasses import dataclass, field

import psycopg2
import psycopg2.pool

log = logging.getLogger(__name__)


def _approx_bytes(value, limit: int | None = None) -> int:
    """Python утгын ойролцоо санах ойн хэмжээ (sys.getsizeof; list/tuple/dict — json, массив — давталтаар).
    limit хэтэрмэгц тоолохоо зогсооно (аварга json-ыг бүхэлд нь гүйхгүй)."""
    total, stack = 0, [value]
    while stack and (limit is None or total <= limit):
        v = stack.pop()
        if isinstance(v, (list, tuple)):
            total += sys.getsizeof(v)
            stack.extend(v)
        elif isinstance(v, dict):
            total += sys.getsizeof(v)
            stack.extend(v.keys())
            stack.extend(v.values())
        else:
            total += sys.getsizeof(v)
    return total


@dataclass
class ExecResult:
    ok: bool
    columns: list[str] = field(default_factory=list)
    rows: list[tuple] = field(default_factory=list)
    rowcount: int = 0
    truncated: bool = False
    error: str | None = None
    error_code: str | None = None      # db_error | timeout | cost_too_high | connection (холболт тасарсан) |
                                       # client_error (Python талын) | too_large (max_result_bytes)
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
    """Thread-safe. Нэг Executor = нэг DSN (bot role) = нэг DB.

    max_result_bytes: None (анхдагч) — хуучин зан төлөв (client-side cursor). Тоо — үр дүнгийн санах ойн төсөв (вэб
    үйлчилгээ): server-side cursor-оор fetch_rows мөрөөр татаж, хэтэрвэл error_code="too_large" (модулийн docstring)."""

    def __init__(self, dsn: str, statement_timeout_ms: int = 20000, max_rows: int = 100000,
                 max_cost: float | None = None, minconn: int = 1, maxconn: int = 16,
                 max_result_bytes: int | None = None, fetch_rows: int = 1):
        self.dsn = dsn
        self.timeout = int(statement_timeout_ms)
        self.max_rows = max_rows
        self.max_cost = max_cost
        self.max_result_bytes = int(max_result_bytes) if max_result_bytes else None
        self.fetch_rows = max(1, int(fetch_rows))
        self._cursor_ids = itertools.count(1)
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
        try:
            conn.set_client_encoding("UTF8")
            # psycopg2 эхний execute-ээс өмнө өөрөө BEGIN илгээдэг тул гараар "BEGIN READ ONLY" нь
            # аль хэдийн нээгдсэн транзакц дотор WARNING өгөөд үр нөлөөгүй болдог. Session түвшинд
            # readonly тохируулахад psycopg2 транзакц бүрийг "BEGIN READ ONLY"-оор эхлүүлнэ (pool-ын
            # холболт бүрт, дахин ашиглагдах үед ч). set_session-ийг транзакцгүй (idle) үед л дуудах боломжтой.
            if conn.autocommit is not False or getattr(conn, "readonly", None) is not True:
                conn.set_session(readonly=True, autocommit=False)
        except Exception:
            self._put(conn, broken=True)
            raise
        return conn

    def _begin(self, cur) -> None:
        """Транзакцын эхний команд: READ ONLY (set_session-ийн давхар хамгаалалт) + statement_timeout."""
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute(f"SET LOCAL statement_timeout = {self.timeout}")

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
                self._begin(cur)
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
                self._begin(cur)
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
                if self.max_result_bytes:   # вэб: хэмжээний төсөвтэй, server-side cursor (H2)
                    return self._run_bounded(conn, cur, sql, max_rows, t0, cost)
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
            # Холболт тасарсан (conn.closed) бол дэд бүтцийн алдаа → "connection" (eval runner дахин асууна); холболт
            # амьд үеийн SQL-ийн алдаа → "db_error" (хуучин шигээ). Мессеж (_pg_error) аль алинд нь өөрчлөгдөөгүй.
            return ExecResult(False, error=_pg_error(e), error_code="connection" if conn.closed else "db_error",
                              elapsed_ms=(time.perf_counter() - t0) * 1000, explain_cost=cost)
        except Exception as e:   # Python талын алдаа (decode г.м.) — холболтын алдаа биш (дахин асуухад засрахгүй)
            broken = True
            return ExecResult(False, error=str(e), error_code="client_error",
                              elapsed_ms=(time.perf_counter() - t0) * 1000)
        finally:
            try:
                conn.rollback()
            except psycopg2.Error:
                broken = True
            self._put(conn, broken)

    def _run_bounded(self, conn, cur, sql: str, max_rows: int, t0: float, cost: float | None) -> ExecResult:
        """max_result_bytes-тэй гүйцэтгэл: `DECLARE … CURSOR WITHOUT HOLD` (нэрлэсэн cursor, ижил READ ONLY транзакц) →
        `FETCH fetch_rows` давталт. Мөр бүрийн ойролцоо хэмжээг нэмж, төсөв хэтэрвэл too_large. statement_timeout нь
        FETCH бүрт тусдаа тул нийт хугацааг deadline-аар хянаж, үлдсэн хугацааг `SET LOCAL statement_timeout`-оор
        (≤ 10% зөрүүтэй) багасгана. Нэрлэсэн cursor-ийг `with` үргэлж хаана (CLOSE; алдаатай транзакцад psycopg2
        алгасна), rollback/холболтыг run()-ий finally буцаана. Алдааг (QueryCanceled г.м.) run() барина."""
        budget = int(self.max_result_bytes)
        deadline = time.perf_counter() + self.timeout / 1000
        slack_ms = max(100, self.timeout // 10)
        limit_ms = self.timeout                  # одоо үйлчилж буй statement_timeout (_begin-ийнх)
        rows: list = []
        total = 0
        cols = None
        # DECLARE-ийн төлөвлөгчийг энгийн асуулгынхтай ижил болгоно (анхдагч 0.1 = эхний мөрийг хурдан гаргах төлөвлөгөө
        # → LIMIT-гүй/ORDER BY-гүй асуулгын мөрийн дараалал, хурд өөр байж болно; EXPLAIN өртөг ч энгийн төлөвлөгөөнийх)
        cur.execute("SET LOCAL cursor_tuple_fraction = 1.0")
        with conn.cursor(name=f"t2s_res_{next(self._cursor_ids)}") as ncur:
            ncur.execute(sql)
            while len(rows) <= max_rows:
                remaining_ms = (deadline - time.perf_counter()) * 1000
                if remaining_ms <= 0:
                    return ExecResult(False, error=f"Хугацаа хэтэрлээ (statement_timeout {self.timeout} ms): үр дүнг "
                                                   "татаж дуусаагүй. Query timed out while fetching rows.",
                                      error_code="timeout", elapsed_ms=(time.perf_counter() - t0) * 1000,
                                      explain_cost=cost)
                if limit_ms - remaining_ms > slack_ms:   # дараагийн FETCH deadline-ийг slack-аас илүү давж болзошгүй
                    limit_ms = max(1, int(remaining_ms))
                    cur.execute(f"SET LOCAL statement_timeout = {limit_ms}")
                n = min(self.fetch_rows, max_rows + 1 - len(rows))
                chunk = ncur.fetchmany(n)
                if cols is None:
                    if ncur.description is None:
                        return ExecResult(False, error="Үр дүн буцаагаагүй (SELECT биш).", error_code="db_error")
                    cols = [d[0] for d in ncur.description]
                for r in chunk:
                    total += _approx_bytes(r, budget - total)
                    if total > budget:
                        return ExecResult(False, error=f"Үр дүн хэт том (санах ойн хязгаар ≈ {budget / 1e6:g} MB, "
                                                       f"{len(rows) + 1}-р мөрөнд хэтэрсэн) — шүүлт, бүлэглэлт нэмэх эсвэл "
                                                       "цөөн багана сонгоно уу. Result too large.",
                                          error_code="too_large", elapsed_ms=(time.perf_counter() - t0) * 1000,
                                          explain_cost=cost)
                    rows.append(r)
                if len(chunk) < n:
                    break
        truncated = len(rows) > max_rows
        rows = rows[:max_rows]
        return ExecResult(True, cols, rows, len(rows), truncated, elapsed_ms=(time.perf_counter() - t0) * 1000,
                          explain_cost=cost)


def _pg_error(e: psycopg2.Error) -> str:
    msg = (e.pgerror or str(e) or "").strip()
    lines = [ln for ln in msg.splitlines() if ln.strip()]
    return " | ".join(lines[:4])[:600]
