import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import os

os.environ.setdefault("WORKER_ENABLED", "false")

import app.models  # noqa: F401
from app.ai.provider import FakeProvider
from app.api.routes import get_llm
from app.db import Base, get_session
from app.main import app


@pytest.fixture
def engine():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    return eng


@pytest.fixture
def session(engine):
    with sessionmaker(engine, expire_on_commit=False)() as s:
        yield s


@pytest.fixture
def fake_llm():
    return FakeProvider()


@pytest.fixture
def client(engine, fake_llm):
    maker = sessionmaker(engine, expire_on_commit=False)

    def _session():
        with maker() as s:
            yield s

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_llm] = lambda: fake_llm
    yield TestClient(app)
    app.dependency_overrides.clear()
