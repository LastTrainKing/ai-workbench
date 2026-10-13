"""Pytest fixtures.

Test isolation strategy
-----------------------
* A **dedicated database** (``aiworkbench_test``) and a **dedicated Redis
  database index** are used so tests can never touch development data.
* Environment variables are set *before* ``app`` is imported, because the
  settings object and the SQLAlchemy engine are created at import time.
* Tables are created once per session and truncated between tests, which is far
  faster than re-creating the schema and still gives full isolation.
* The AI providers are forced to the deterministic offline engine so the test
  suite needs no API key and produces stable, reproducible output.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import uuid
from collections.abc import AsyncIterator

# ---------------------------------------------------------------------------
# Environment must be configured before importing anything from `app`.
# ---------------------------------------------------------------------------
TEST_DB_NAME = "aiworkbench_test"
TEST_DB_URL = f"postgresql+asyncpg://aiworkbench:aiworkbench@127.0.0.1:5432/{TEST_DB_NAME}"

os.environ["DATABASE_URL"] = TEST_DB_URL
os.environ["REDIS_URL"] = "redis://127.0.0.1:6379/1"
os.environ["QDRANT_COLLECTION"] = "ai_workbench_chunks_test"
os.environ["AI_PROVIDER"] = "offline"
os.environ["EMBEDDING_PROVIDER"] = "offline"
# A test-only runtime directory, and not the developer's.
#
# The two lines above set the provider through **environment variables**, which sit below
# an install-level `ai-config.json` in the resolution order. So on a machine where someone
# had configured a real key — which the product asks them to do — 16 tests failed, all of
# them asserting the offline engine, none of them wrong about anything except which
# machine they were on.
#
# The suite already promised, in this file's own docstring, that it "can never touch
# development data". For the AI configuration it did not keep that promise. Pointing the
# runtime root somewhere of its own makes the promise structural: there is no file to read,
# so nothing can outrank the environment.
os.environ["AI_WORKBENCH_DATA_DIR"] = str(
    __import__("pathlib").Path(__file__).resolve().parents[2] / ".runtime" / "test-runtime"
)

# ...and inherit the machine-local secrets it still needs.
#
# Qdrant is started with an API key that lives in the runtime directory, so moving the
# runtime moved the key too and the dependency probe started reporting Qdrant as down. The
# AI configuration is what the test runtime must *not* inherit; the rest of it is just the
# machine's runtime, and a test environment that cannot reach Qdrant is testing a different
# product.
def _inherit_machine_runtime() -> None:
    import pathlib
    import shutil

    machine = pathlib.Path(__file__).resolve().parents[2] / ".runtime"
    mine = pathlib.Path(os.environ["AI_WORKBENCH_DATA_DIR"])
    mine.mkdir(parents=True, exist_ok=True)
    for name in ("qdrant-api-key.txt",):
        source = machine / name
        target = mine / name
        if source.is_file() and not target.exists():
            shutil.copyfile(source, target)


_inherit_machine_runtime()
os.environ["WORKER_ENABLED"] = "false"
os.environ["CACHE_ENABLED"] = "true"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ["APP_ENV"] = "test"
os.environ["STORAGE_DIR"] = str(
    __import__("pathlib").Path(__file__).resolve().parents[2] / ".runtime" / "test-storage"
)

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import text

from app.core.config import settings
from app.infrastructure.database import dispose_engine, get_engine, session_scope
from app.models import Base


def _stamp_head() -> None:
    """Record the head revision on the freshly created test schema.

    Blocking, so it is called through `asyncio.to_thread`. See the note in `_database`.
    """
    from alembic.config import Config

    from alembic import command
    from app.core.schema_guard import ALEMBIC_INI, BACKEND_ROOT

    config = Config(str(ALEMBIC_INI))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", settings.database_url)
    command.stamp(config, "head")


async def _ensure_test_database() -> None:
    """Create the test database if it does not exist yet."""
    import asyncpg

    admin = await asyncpg.connect(
        user="aiworkbench",
        password="aiworkbench",
        host="127.0.0.1",
        port=5432,
        database="postgres",
    )
    try:
        exists = await admin.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", TEST_DB_NAME
        )
        if not exists:
            await admin.execute(f'CREATE DATABASE "{TEST_DB_NAME}"')
    finally:
        await admin.close()


@pytest_asyncio.fixture(scope="session")
async def _database() -> AsyncIterator[None]:
    """Create the schema once per session.

    Deliberately **not** ``autouse``: pure unit tests (chunking, tokenising,
    JSON repair, retry policy) must be runnable on a machine with no PostgreSQL,
    no Redis and no Qdrant. Only the fixtures below pull the database in.
    """
    await _ensure_test_database()
    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    # The schema here is built from the models, which is what the migrations describe at
    # head. Recording that keeps `app.core.schema_guard.ensure_schema` — which refuses to
    # start the application against a database whose revision it cannot account for — from
    # rejecting the database this fixture just built. The stamp records a fact that is
    # true here; it is not used to paper over a mismatch in production.
    await asyncio.to_thread(_stamp_head)
    yield
    await dispose_engine()


@pytest_asyncio.fixture
async def _clean_state(_database: None) -> AsyncIterator[None]:
    """Truncate every table and clear the Redis test database between DB tests."""
    from app.core.cache import get_redis

    async with session_scope() as session:
        await session.execute(
            text(
                "TRUNCATE TABLE "
                + ", ".join(f'"{name}"' for name in Base.metadata.tables)
                + " RESTART IDENTITY CASCADE"
            )
        )
        # Prompt templates are reference data: seed them for every test.
        from app.ai.prompts.registry import ensure_seeded

        await ensure_seeded(session)
    # The cache is optional in tests: a Redis outage must not fail the suite.
    with contextlib.suppress(Exception):
        await get_redis().flushdb()
    yield


@pytest_asyncio.fixture
async def session(_clean_state: None) -> AsyncIterator[object]:
    async with session_scope() as s:
        yield s


@pytest_asyncio.fixture
async def client(_clean_state: None) -> AsyncIterator[httpx.AsyncClient]:
    from app.main import create_app

    app = create_app()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test/api/v1", timeout=60.0
    ) as c:
        yield c


@pytest_asyncio.fixture
async def user_token(client: httpx.AsyncClient) -> tuple[str, dict]:
    """Register a fresh user and return (token, user dict)."""
    payload = {
        "email": f"user_{uuid.uuid4().hex[:8]}@aiworkbench.dev",
        "username": f"user_{uuid.uuid4().hex[:8]}",
        "password": "test1234",
        "display_name": "Test User",
    }
    response = await client.post("/auth/register", json=payload)
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    return data["access_token"], data["user"]


@pytest_asyncio.fixture
async def auth_headers(user_token: tuple[str, dict]) -> dict[str, str]:
    token, _ = user_token
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def knowledge_base(client: httpx.AsyncClient, auth_headers: dict) -> dict:
    response = await client.post(
        "/knowledge-bases",
        headers=auth_headers,
        json={"name": f"测试知识库 {uuid.uuid4().hex[:6]}", "description": "pytest fixture"},
    )
    assert response.status_code == 201, response.text
    return response.json()["data"]


@pytest.fixture
def sample_markdown() -> bytes:
    return (
        "# JVM 学习笔记\n\n"
        "## 垃圾回收\n\n"
        "G1 收集器把堆划分为多个 Region，可以预测停顿时间，是 JDK 9 之后的默认收集器。\n"
        "ZGC 追求极低停顿，停顿时间与堆大小基本无关。\n\n"
        "## 集合框架\n\n"
        "HashMap 在 JDK 8 之后采用数组加链表加红黑树的结构，链表长度超过 8 且容量不小于 64 时转为红黑树。\n\n"
        "## 待办\n\n"
        "- TODO: 整理 JVM 调优参数\n"
        "- 待办：补充线程池监控指标\n"
    ).encode()


@pytest.fixture
def settings_override():
    """Expose the live settings object so tests can assert on config."""
    return settings
