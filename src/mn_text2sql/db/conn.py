"""DSN бүрдүүлэх ба холбогдох туслах функцүүд."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

import psycopg2
import psycopg2.extensions
from psycopg2.extensions import make_dsn, parse_dsn

from ..config import Config


def with_dbname(dsn: str, dbname: str | None) -> str:
    if not dbname:
        return dsn
    params = parse_dsn(dsn)
    params["dbname"] = dbname
    return make_dsn(**params)


def admin_dsn(cfg: Config, dbname: str | None = None) -> str:
    return with_dbname(cfg["db"]["admin_dsn"], dbname or cfg["db"]["name"])


def bot_dsn(cfg: Config, dbname: str | None = None, role: str | None = None,
            password: str | None = None) -> str:
    """Bot (read-only) холболтын DSN. db.bot_dsn өгөөгүй бол admin_dsn-ээс бүрдүүлнэ."""
    explicit = cfg["db"].get("bot_dsn")
    if explicit and role is None:
        return with_dbname(explicit, dbname or parse_dsn(explicit).get("dbname") or cfg["db"]["name"])
    params = parse_dsn(cfg["db"]["admin_dsn"])
    params["user"] = role or cfg["db"]["bot_role"]
    params["password"] = password if password is not None else cfg["db"]["bot_password"]
    params["dbname"] = dbname or cfg["db"]["name"]
    return make_dsn(**params)


def connect(dsn: str, autocommit: bool = False, application_name: str = "mn_text2sql"):
    conn = psycopg2.connect(dsn, application_name=application_name)
    conn.set_client_encoding("UTF8")
    conn.autocommit = autocommit
    return conn


@contextmanager
def connection(dsn: str, autocommit: bool = False) -> Iterator[psycopg2.extensions.connection]:
    conn = connect(dsn, autocommit=autocommit)
    try:
        yield conn
        if not autocommit:
            conn.commit()
    except Exception:
        if not autocommit:
            conn.rollback()
        raise
    finally:
        conn.close()


def database_exists(cfg: Config, name: str) -> bool:
    with connection(with_dbname(cfg["db"]["admin_dsn"], "postgres"), autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pg_database WHERE datname = %s", (name,))
            return cur.fetchone() is not None


def server_version_num(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SHOW server_version_num")
        return int(cur.fetchone()[0])
