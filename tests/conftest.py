"""pytest тохиргоо. DB шаардлагатай тестүүд (@pytest.mark.db) T2S_ADMIN_DSN-тэй, DB холбогдох үед л ажиллана."""

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def _db_available() -> bool:
    if not os.environ.get("T2S_ADMIN_DSN"):
        return False
    try:
        import psycopg2

        from mn_text2sql.config import load_config
        from mn_text2sql.db.conn import bot_dsn
        conn = psycopg2.connect(bot_dsn(load_config()), connect_timeout=3)
        conn.close()
        return True
    except Exception:
        return False


DB_OK = _db_available()


def pytest_collection_modifyitems(config, items):
    if DB_OK:
        return
    skip = pytest.mark.skip(reason="DB алга (T2S_ADMIN_DSN тохируулаад db-build хийнэ)")
    for item in items:
        if "db" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(scope="session")
def cfg():
    from mn_text2sql.config import load_config
    return load_config()
