"""Shared fixtures. Integration tests run against the live FalkorDB in FALKORDB_HOST/PORT and are
skipped (not failed) when it is unreachable. Every test namespace is a uniquely named sandbox that
is dropped afterwards."""
from __future__ import annotations

import uuid

import pytest
import redis

from domesday.config import load_settings
from domesday.db.client import Database
from domesday.db.registry import Registry
from domesday.providers.fake import FakeEmbedder, FakeLLM


@pytest.fixture(scope="session")
def settings():
    return load_settings(dotenv=False)


@pytest.fixture(scope="session")
def db(settings):
    database = Database(settings)
    try:
        database.ping()
    except redis.ConnectionError:
        pytest.skip(f"FalkorDB not reachable at {settings.falkordb_host}:{settings.falkordb_port}")
    return database


@pytest.fixture(scope="session")
def registry(db):
    return Registry(db)


@pytest.fixture
def embedder():
    return FakeEmbedder()


@pytest.fixture
def llm():
    return FakeLLM()


@pytest.fixture
def make_ns(registry, embedder):
    """Factory: make_ns(owner='agent') -> a fresh sandbox Namespace, cleaned up after the test."""
    created: list[str] = []

    def _make(owner: str = "test-agent", kind: str = "sandbox"):
        ident = f"t-{uuid.uuid4().hex[:12]}"
        created.append((kind, ident))
        return registry.create_namespace(kind, ident, embedder.model, embedder.dim, owner=owner)

    yield _make
    for kind, ident in created:
        registry.delete_namespace(kind, ident)
