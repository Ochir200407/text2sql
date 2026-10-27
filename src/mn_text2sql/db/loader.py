"""ББСБ-ийн DB-г (db/) ба text2sql давхаргыг (db_t2s/) PostgreSQL-д ачаална.

psql шаардлагагүй: `\\set ...` зэрэг psql-ийн мета командыг хасаад psycopg2-оор гүйцэтгэнэ.
Ачаалах дараалал (db/functions/001_common_functions.sql-ийн толгой дахь заавраар):
migrations/001..015 → functions/* → triggers/* → views/* → migrations/016_seed.sql
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from ..config import ROOT, Config
from .conn import connection, with_dbname

log = logging.getLogger(__name__)

SCHEMA_FILES = [
    *(f"db/migrations/{n:03d}_{name}.sql" for n, name in [
        (1, "extensions"), (2, "core"), (3, "customer"), (4, "kyc"), (5, "product"), (6, "loan"),
        (7, "collateral"), (8, "account"), (9, "transaction"), (10, "deposit"), (11, "accounting"),
        (12, "risk"), (13, "collection"), (14, "system"), (15, "indexes"),
    ]),
    "db/functions/001_common_functions.sql",
    "db/functions/002_loan_functions.sql",
    "db/functions/003_accounting_functions.sql",
    "db/triggers/001_triggers.sql",
    "db/views/001_operational_views.sql",
    "db/views/002_reporting_views.sql",
    "db/migrations/016_seed.sql",
]

_META = re.compile(r"^\s*\\")


def strip_psql_meta(sql: str) -> str:
    return "\n".join(line for line in sql.splitlines() if not _META.match(line))


def run_sql_file(conn, path: Path) -> None:
    sql = strip_psql_meta(path.read_text(encoding="utf-8"))
    with conn.cursor() as cur:
        cur.execute(sql)


def t2s_files(root: Path = ROOT) -> list[Path]:
    return sorted((root / "db_t2s").glob("*.sql"))


def create_database(cfg: Config, name: str, drop: bool = False) -> None:
    """template0-оос UTF8 DB үүсгэнэ. drop=True бол хуучныг устгана."""
    admin = with_dbname(cfg["db"]["admin_dsn"], "postgres")
    locale = cfg["db"].get("locale")
    with connection(admin, autocommit=True) as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
        exists = cur.fetchone() is not None
        if exists and drop:
            cur.execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = %s AND pid <> pg_backend_pid()", (name,))
            cur.execute(f'DROP DATABASE "{name}"')
            exists = False
        if not exists:
            extra = f" LC_COLLATE '{locale}' LC_CTYPE '{locale}'" if locale else ""
            cur.execute(f"CREATE DATABASE \"{name}\" TEMPLATE template0 ENCODING 'UTF8'{extra}")
            log.info("DB үүслээ: %s", name)


def load_core_schema(cfg: Config, dbname: str, root: Path = ROOT) -> None:
    """db/ доторх схем, функц, trigger, view, лавлах seed-ийг ачаална (demo seed-гүй)."""
    dsn = with_dbname(cfg["db"]["admin_dsn"], dbname)
    with connection(dsn) as conn:
        for rel in SCHEMA_FILES:
            log.info("ачаалж байна: %s", rel)
            run_sql_file(conn, root / rel)
            conn.commit()


def load_t2s_layer(cfg: Config, dbname: str, root: Path = ROOT) -> None:
    """db_t2s/*.sql: bot-д зориулсан view, эрх, RLS, туслах функц."""
    dsn = with_dbname(cfg["db"]["admin_dsn"], dbname)
    with connection(dsn) as conn:
        for path in t2s_files(root):
            log.info("ачаалж байна: %s", path.relative_to(root))
            sql = strip_psql_meta(path.read_text(encoding="utf-8"))
            sql = sql.replace("{{BOT_PASSWORD}}", cfg["db"]["bot_password"].replace("'", "''"))
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()


def run_validation(cfg: Config, dbname: str, root: Path = ROOT) -> list[tuple]:
    """db/validation/001_validation.sql-г ажиллуулж (seq, нэр, n, хүлээгдэж буй) мөрүүдийг буцаана."""
    dsn = with_dbname(cfg["db"]["admin_dsn"], dbname)
    sql = strip_psql_meta((root / "db/validation/001_validation.sql").read_text(encoding="utf-8"))
    with connection(dsn) as conn, conn.cursor() as cur:
        cur.execute(sql)
        return cur.fetchall()
