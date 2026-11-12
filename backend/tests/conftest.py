"""Shared test fixtures.

Each agent uses its OWN database to avoid clashes when tests run in parallel:
    TEST_DATABASE_URL=postgresql+psycopg://wealth:wealth@localhost:5432/wealth_test_<domain>
Tables are dropped/created once per session; each test runs inside a rolled-back transaction.
"""
import os

os.environ.setdefault("DATABASE_URL", os.environ.get(
    "TEST_DATABASE_URL", "postgresql+psycopg://wealth:wealth@localhost:5432/wealth_test_qa"))
os.environ.setdefault("ENABLE_SCHEDULER", "false")
os.environ.setdefault("ALLOWED_HOSTS", "testserver")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.orm import Session  # noqa: E402

import app.models  # noqa: E402,F401
from app.core.db import Base, engine, get_db  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _schema():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    yield


@pytest.fixture()
def db():
    conn = engine.connect()
    trans = conn.begin()
    session = Session(bind=conn, join_transaction_mode="create_savepoint", expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        trans.rollback()
        conn.close()


@pytest.fixture()
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
