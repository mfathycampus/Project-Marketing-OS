import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import os

os.environ.setdefault("WORKER_ENABLED", "false")
os.environ.setdefault("VOICE_PROVIDER", "off")  # no network voice in tests; voice tests opt in

import app.models  # noqa: F401
from app.ai.provider import FakeProvider
from app.api.routes import get_llm
from app.db import Base, get_session
from app.main import app


@pytest.fixture(autouse=True)
def engine(monkeypatch):
    """In-memory database; code that opens its own session (background jobs, lessons) uses it too."""
    from app import db

    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(eng)
    monkeypatch.setattr(db, "SessionLocal", sessionmaker(eng, expire_on_commit=False))
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


@pytest.fixture(autouse=True)
def isolated_media_library(tmp_path, monkeypatch):
    """Every test gets an empty media library in its own temp folder (never the real one)."""
    from app.media import library
    from app.storage.local import LocalStorage

    lib = library.MediaLibrary(LocalStorage(str(tmp_path / "media-store")))
    monkeypatch.setattr(library, "_lib", lib)
    return lib


@pytest.fixture(autouse=True)
def quick_music(monkeypatch, request):
    """Generated music costs ~1.5 s per video; tests get a short silent bed (test_music_gen uses the real one)."""
    if request.node.get_closest_marker("real_music"):
        return
    import io
    import wave

    from app.video import music_gen

    def silent(seconds, style="modern", seed=0):
        buf = io.BytesIO()
        with wave.open(buf, "wb") as w:
            w.setnchannels(1)
            w.setsampwidth(2)
            w.setframerate(8000)
            w.writeframes(b"\x00\x00" * int(8000 * seconds))
        return buf.getvalue()

    monkeypatch.setattr(music_gen, "generate", silent)
