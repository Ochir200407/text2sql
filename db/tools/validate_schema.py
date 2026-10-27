"""ББСБ схемийн статик шалгагч — PostgreSQL сервергүйгээр DDL-ийг шалгана.

Шалгах зүйл:
  * FK бүрийн зорилтот хүснэгт/багана байгаа эсэх
  * Хүснэгт бүр PK-тэй эсэх
  * Нэршлийн дүрэм (snake_case, schema.table)
  * Мөнгөн дүнд FLOAT/DOUBLE/REAL ашиглаагүй эсэх
  * Индекс/trigger-ийн зорилтот хүснэгт байгаа эсэх
  * Давхардсан хүснэгт, constraint, индексийн нэр
  * Партицлагдсан хүснэгтийн PK/UNIQUE-д partition key орсон эсэх

Ажиллуулах:  python database/tools/validate_schema.py
Гаралт:      статистик + алдааны жагсаалт; алдаатай бол exit code 1
"""

from __future__ import annotations

import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SQL_DIRS = ["migrations", "functions", "triggers", "views", "seeds", "validation", "queries"]

DOLLAR_BLOCK = re.compile(r"\$\$.*?\$\$", re.DOTALL)
LINE_COMMENT = re.compile(r"--[^\n]*")
CREATE_TABLE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z_]+)\.([a-z_0-9]+)\s*\(", re.IGNORECASE
)
PARTITION_OF = re.compile(
    r"CREATE\s+TABLE\s+([a-z_]+)\.([a-z_0-9]+)\s+PARTITION\s+OF\s+([a-z_]+)\.([a-z_0-9]+)", re.IGNORECASE
)
CREATE_INDEX = re.compile(
    r"CREATE\s+(UNIQUE\s+)?INDEX\s+([a-z_0-9]+)\s+ON\s+([a-z_]+)\.([a-z_0-9]+)", re.IGNORECASE
)
CREATE_TRIGGER = re.compile(
    r"CREATE\s+(?:CONSTRAINT\s+)?TRIGGER\s+([a-z_0-9]+)[\s\S]{0,200}?\sON\s+([a-z_]+)\.([a-z_0-9]+)",
    re.IGNORECASE,
)
CREATE_FUNCTION = re.compile(r"CREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+([a-z_]+)\.([a-z_0-9]+)", re.IGNORECASE)
CREATE_VIEW = re.compile(r"CREATE\s+(?:OR\s+REPLACE\s+)?VIEW\s+([a-z_]+)\.([a-z_0-9]+)", re.IGNORECASE)
CREATE_SCHEMA = re.compile(r"CREATE\s+SCHEMA\s+(?:IF\s+NOT\s+EXISTS\s+)?([a-z_]+)", re.IGNORECASE)
ALTER_FK = re.compile(
    r"ALTER\s+TABLE\s+([a-z_]+)\.([a-z_0-9]+)\s+ADD\s+CONSTRAINT\s+([a-z_0-9]+)\s+"
    r"FOREIGN\s+KEY\s*\(([^)]+)\)\s*REFERENCES\s+([a-z_]+)\.([a-z_0-9]+)\s*\(([^)]+)\)",
    re.IGNORECASE,
)
INLINE_REF = re.compile(r"REFERENCES\s+([a-z_]+)\.([a-z_0-9]+)\s*\(([^)]+)\)", re.IGNORECASE)
TABLE_FK = re.compile(
    r"FOREIGN\s+KEY\s*\(([^)]+)\)\s*REFERENCES\s+([a-z_]+)\.([a-z_0-9]+)\s*\(([^)]+)\)", re.IGNORECASE
)
CONSTRAINT_NAME = re.compile(r"CONSTRAINT\s+([a-z_0-9]+)", re.IGNORECASE)
FLOAT_TYPE = re.compile(r"\b(FLOAT|DOUBLE\s+PRECISION|REAL)\b", re.IGNORECASE)
MONEY_HINT = re.compile(r"(amount|balance|principal|interest|penalty|fee|rate|value|income|salary|cost)")
IDENT_OK = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass
class Table:
    schema: str
    name: str
    file: str
    columns: list[str] = field(default_factory=list)
    pk_columns: list[str] = field(default_factory=list)
    unique_sets: list[list[str]] = field(default_factory=list)
    fks: list[tuple] = field(default_factory=list)
    partition_key: str | None = None
    is_partition: bool = False

    @property
    def full(self) -> str:
        return f"{self.schema}.{self.name}"


def strip_noise(sql: str) -> str:
    """Функцийн их бие ($$...$$) ба мөрийн тайлбарыг арилгана."""
    return LINE_COMMENT.sub("", DOLLAR_BLOCK.sub(" $BODY$ ", sql))


def split_top_level(body: str) -> list[str]:
    """Хаалтын гүнийг тооцож таслалаар салгана."""
    parts, depth, current = [], 0, []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if "".join(current).strip():
        parts.append("".join(current).strip())
    return parts


def extract_body(sql: str, open_paren_idx: int) -> tuple[str, int]:
    """Нээх хаалтнаас эхлээд тохирох хаах хаалт хүртэлх хэсгийг буцаана."""
    depth, i = 0, open_paren_idx
    while i < len(sql):
        if sql[i] == "(":
            depth += 1
        elif sql[i] == ")":
            depth -= 1
            if depth == 0:
                return sql[open_paren_idx + 1 : i], i
        i += 1
    return sql[open_paren_idx + 1 :], len(sql)


def parse_columns(table: Table, body: str) -> list[str]:
    """Хүснэгтийн биеийг задалж багана, PK, UNIQUE, FK-г бүртгэнэ. Constraint нэрсийг буцаана."""
    names: list[str] = []
    for item in split_top_level(body):
        upper = item.upper().lstrip()
        cname = CONSTRAINT_NAME.match(upper.replace("CONSTRAINT ", "CONSTRAINT ", 1))
        if cname:
            names.append(CONSTRAINT_NAME.search(item).group(1))

        if upper.startswith(("CONSTRAINT", "PRIMARY KEY", "UNIQUE", "CHECK", "FOREIGN KEY", "EXCLUDE")):
            if "PRIMARY KEY" in upper:
                cols = re.search(r"PRIMARY\s+KEY\s*\(([^)]+)\)", item, re.IGNORECASE)
                if cols:
                    table.pk_columns = [c.strip() for c in cols.group(1).split(",")]
            if re.search(r"\bUNIQUE\s*\(", upper):
                cols = re.search(r"UNIQUE\s*\(([^)]+)\)", item, re.IGNORECASE)
                if cols:
                    table.unique_sets.append([c.strip() for c in cols.group(1).split(",")])
            fk = TABLE_FK.search(item)
            if fk:
                table.fks.append((
                    [c.strip() for c in fk.group(1).split(",")],
                    fk.group(2), fk.group(3),
                    [c.strip() for c in fk.group(4).split(",")],
                ))
            continue

        col = item.split()[0].strip('"') if item.split() else ""
        if not col:
            continue
        table.columns.append(col)
        if re.search(r"\bPRIMARY\s+KEY\b", upper):
            table.pk_columns = [col]
        ref = INLINE_REF.search(item)
        if ref:
            table.fks.append(([col], ref.group(1), ref.group(2),
                              [c.strip() for c in ref.group(3).split(",")]))
        if FLOAT_TYPE.search(item) and MONEY_HINT.search(col):
            table.columns.append(f"!FLOAT:{col}")
    return names


def parse_file(path: Path, tables: dict[str, Table], stats: Counter,
               constraints: list[tuple[str, str]], problems: list[str]) -> None:
    """Нэг SQL файлыг задалж бүх обьектыг бүртгэнэ."""
    raw = path.read_text(encoding="utf-8")
    sql = strip_noise(raw)
    rel = path.relative_to(ROOT).as_posix()

    for m in CREATE_SCHEMA.finditer(sql):
        stats[f"schema:{m.group(1)}"] += 1

    for m in PARTITION_OF.finditer(sql):
        child = Table(m.group(1), m.group(2), rel, is_partition=True)
        tables[child.full] = child
        stats["partitions"] += 1
        parent = f"{m.group(3)}.{m.group(4)}"
        if parent not in tables:
            problems.append(f"{rel}: {child.full} нь байхгүй эцэг хүснэгтийн партиц ({parent})")

    for m in CREATE_TABLE.finditer(sql):
        schema, name = m.group(1), m.group(2)
        body, end = extract_body(sql, m.end() - 1)
        table = Table(schema, name, rel)
        tail = sql[end : end + 120]
        part = re.search(r"PARTITION\s+BY\s+\w+\s*\(([^)]+)\)", tail, re.IGNORECASE)
        if part:
            table.partition_key = part.group(1).strip()
            stats["partitioned_tables"] += 1
        for cname in parse_columns(table, body):
            constraints.append((cname, table.full))
        if table.full in tables:
            problems.append(f"{rel}: давхардсан хүснэгтийн нэр — {table.full}")
        tables[table.full] = table

    for m in ALTER_FK.finditer(sql):
        src = f"{m.group(1)}.{m.group(2)}"
        constraints.append((m.group(3), src))
        if src in tables:
            tables[src].fks.append((
                [c.strip() for c in m.group(4).split(",")], m.group(5), m.group(6),
                [c.strip() for c in m.group(7).split(",")],
            ))
        else:
            problems.append(f"{rel}: ALTER TABLE байхгүй хүснэгт дээр — {src}")

    for m in CREATE_INDEX.finditer(sql):
        stats["indexes"] += 1
        if m.group(1):
            stats["unique_indexes"] += 1
        target = f"{m.group(3)}.{m.group(4)}"
        if target not in tables:
            problems.append(f"{rel}: индекс {m.group(2)} байхгүй хүснэгт дээр — {target}")
        constraints.append((m.group(2), target))

    for m in CREATE_TRIGGER.finditer(sql):
        stats["triggers"] += 1
        target = f"{m.group(2)}.{m.group(3)}"
        if target not in tables:
            problems.append(f"{rel}: trigger {m.group(1)} байхгүй хүснэгт дээр — {target}")

    stats["functions"] += len(CREATE_FUNCTION.findall(sql))
    stats["views"] += len(CREATE_VIEW.findall(sql))


def check_foreign_keys(tables: dict[str, Table], problems: list[str]) -> int:
    """FK бүрийн зорилтот хүснэгт ба багана байгаа эсэхийг шалгана."""
    total = 0
    for table in tables.values():
        for cols, tschema, tname, tcols in table.fks:
            total += 1
            target = f"{tschema}.{tname}"
            if target not in tables:
                problems.append(f"{table.full}: FK зорилт байхгүй — {target}")
                continue
            for col in cols:
                if col not in table.columns:
                    problems.append(f"{table.full}: FK эх багана байхгүй — {col}")
            for col in tcols:
                if col not in tables[target].columns:
                    problems.append(f"{table.full} → {target}: зорилтот багана байхгүй — {col}")
    return total


def check_tables(tables: dict[str, Table], problems: list[str]) -> None:
    """PK, нэршил, мөнгөн төрөл, партицын түлхүүрийг шалгана."""
    for table in tables.values():
        if table.is_partition:
            continue
        if not table.pk_columns:
            problems.append(f"{table.full}: PRIMARY KEY алга")
        if not IDENT_OK.match(table.name):
            problems.append(f"{table.full}: snake_case биш нэр")
        for col in table.columns:
            if col.startswith("!FLOAT:"):
                problems.append(f"{table.full}: мөнгөн/хувийн баганад FLOAT — {col[7:]}")
            elif not IDENT_OK.match(col):
                problems.append(f"{table.full}: snake_case биш багана — {col}")
        if table.partition_key:
            key = table.partition_key
            if table.pk_columns and key not in table.pk_columns:
                problems.append(f"{table.full}: partition key '{key}' PK-д алга")
            for uset in table.unique_sets:
                if key not in uset:
                    problems.append(f"{table.full}: UNIQUE({', '.join(uset)})-д partition key алга")


def main() -> int:
    tables: dict[str, Table] = {}
    stats: Counter = Counter()
    constraints: list[tuple[str, str]] = []
    problems: list[str] = []

    files = sorted(p for d in SQL_DIRS for p in (ROOT / d).glob("*.sql"))
    if not files:
        sys.stdout.write("SQL файл олдсонгүй\n")
        return 1

    for path in files:
        parse_file(path, tables, stats, constraints, problems)

    fk_count = check_foreign_keys(tables, problems)
    check_tables(tables, problems)

    dupes = [name for name, cnt in Counter(c[0] for c in constraints).items() if cnt > 1]
    for name in sorted(dupes):
        owners = sorted({owner for cname, owner in constraints if cname == name})
        if len(owners) > 1:
            problems.append(f"давхардсан constraint/индексийн нэр: {name} → {', '.join(owners)}")

    by_schema: dict[str, list[str]] = defaultdict(list)
    for table in tables.values():
        if not table.is_partition:
            by_schema[table.schema].append(table.name)

    out = sys.stdout
    out.write("=" * 68 + "\n")
    out.write("ББСБ DATABASE — СТАТИК ШАЛГАЛТ\n")
    out.write("=" * 68 + "\n")
    out.write(f"SQL файл:            {len(files)}\n")
    out.write(f"Схем:                {len(by_schema)}\n")
    out.write(f"Хүснэгт (үндсэн):    {sum(len(v) for v in by_schema.values())}\n")
    out.write(f"Партицын хүснэгт:    {stats['partitions']} ({stats['partitioned_tables']} эцэг)\n")
    out.write(f"Primary key:         {sum(1 for t in tables.values() if t.pk_columns and not t.is_partition)}\n")
    out.write(f"Foreign key:         {fk_count}\n")
    out.write(f"Индекс (тусад нь):   {stats['indexes']} (UNIQUE: {stats['unique_indexes']})\n")
    out.write(f"Trigger:             {stats['triggers']}\n")
    out.write(f"Функц:               {stats['functions']}\n")
    out.write(f"View:                {stats['views']}\n")
    out.write("-" * 68 + "\n")
    for schema in sorted(by_schema):
        out.write(f"  {schema:<12} {len(by_schema[schema]):>3} хүснэгт\n")
    out.write("-" * 68 + "\n")

    if problems:
        out.write(f"АСУУДАЛ: {len(problems)}\n")
        for p in problems[:60]:
            out.write(f"  - {p}\n")
        if len(problems) > 60:
            out.write(f"  ... бас {len(problems) - 60}\n")
        return 1

    out.write("БҮХ ШАЛГАЛТ ЦЭВЭР\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
