"""Мөрүүдийг санах ойд цуглуулж PostgreSQL руу COPY-оор хурдан ачаална."""

from __future__ import annotations

import io
import json
import logging
import uuid
from datetime import date, datetime
from decimal import Decimal

log = logging.getLogger(__name__)


def _escape(text: str) -> str:
    return (text.replace("\\", "\\\\").replace("\t", "\\t")
                .replace("\n", "\\n").replace("\r", "\\r"))


def _array_literal(values) -> str:
    parts = []
    for v in values:
        if v is None:
            parts.append("NULL")
        else:
            s = str(v).replace("\\", "\\\\").replace('"', '\\"')
            parts.append(f'"{s}"')
    return "{" + ",".join(parts) + "}"


def to_copy(value) -> str:
    if value is None:
        return "\\N"
    if isinstance(value, bool):
        return "t" if value else "f"
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (int, float)):
        return repr(value) if isinstance(value, float) else str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, bytes):
        return _escape("\\x" + value.hex())
    if isinstance(value, dict):
        return _escape(json.dumps(value, ensure_ascii=False, default=str))
    if isinstance(value, (list, tuple)):
        return _escape(_array_literal(value))
    return _escape(str(value))


class TableBuffer:
    """Нэг хүснэгтийн мөрүүд. add(**kw) — баганын нэр шалгана (алдаатай нэр бол шууд унана)."""

    def __init__(self, name: str, columns: list[str]):
        self.name = name
        self.columns = list(columns)
        self._index = {c: i for i, c in enumerate(self.columns)}
        self.rows: list[tuple] = []

    def add(self, **kw) -> dict:
        unknown = set(kw) - self._index.keys()
        if unknown:
            raise KeyError(f"{self.name}: үл мэдэгдэх багана {sorted(unknown)}")
        self.rows.append(tuple(kw.get(c) for c in self.columns))
        return kw

    def update(self, index: int, **kw) -> None:
        """Өмнө нэмсэн мөрийн баганыг шинэчилнэ (жишээ: дараа нь мэдэгдсэн гүйлгээний ID)."""
        row = list(self.rows[index])
        for key, value in kw.items():
            row[self._index[key]] = value
        self.rows[index] = tuple(row)

    def get(self, index: int, column: str):
        return self.rows[index][self._index[column]]

    def col(self, column: str) -> int:
        return self._index[column]

    def __len__(self) -> int:
        return len(self.rows)


class Store:
    """Бүх хүснэгтийн буфер. Ачаалах дараалал = бүртгэсэн дараалал."""

    def __init__(self):
        self.tables: dict[str, TableBuffer] = {}

    def table(self, name: str, columns: list[str]) -> TableBuffer:
        if name not in self.tables:
            self.tables[name] = TableBuffer(name, columns)
        return self.tables[name]

    def __getitem__(self, name: str) -> TableBuffer:
        return self.tables[name]

    def copy_all(self, conn, batch_rows: int = 50000) -> dict[str, int]:
        counts: dict[str, int] = {}
        with conn.cursor() as cur:
            for name, buf in self.tables.items():
                if not buf.rows:
                    continue
                cols = ", ".join(buf.columns)
                sql = f"COPY {name} ({cols}) FROM STDIN"
                for start in range(0, len(buf.rows), batch_rows):
                    chunk = buf.rows[start:start + batch_rows]
                    data = io.StringIO()
                    for row in chunk:
                        data.write("\t".join(to_copy(v) for v in row))
                        data.write("\n")
                    data.seek(0)
                    try:
                        cur.copy_expert(sql, data)
                    except Exception:
                        log.error("COPY алдаа: %s", name)
                        raise
                counts[name] = len(buf.rows)
                log.info("%-45s %8d мөр", name, len(buf.rows))
        return counts
