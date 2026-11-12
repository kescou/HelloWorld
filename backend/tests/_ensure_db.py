"""Create the test database if it does not exist yet (used by `make test`; not a test module).

Run from backend/:  DATABASE_URL=postgresql+psycopg://.../wealth_test_qa python tests/_ensure_db.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.core.config import get_settings  # noqa: E402

url = make_url(get_settings().database_url)
try:
    with create_engine(url).connect() as conn:
        conn.execute(text("select 1"))
except Exception as exc:  # noqa: BLE001
    if "does not exist" not in str(exc):
        raise SystemExit(f"Test-Datenbank nicht erreichbar: {str(exc).splitlines()[0]}") from exc
    with create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT").connect() as conn:
        conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    print(f'Test-Datenbank "{url.database}" angelegt.')
