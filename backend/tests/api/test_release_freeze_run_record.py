"""A failed AI run must leave a record.

Found while building the stub-provider tests: the executor carefully writes
`status=FAILED`, an error code and an error message, and then re-raises — and the caller's
`session_scope` rolls the whole transaction back, so **nothing survives**.

The product states the guarantee in its own words: every AI invocation is recorded as an
`ai_runs` row "so the product can answer 'what did the AI actually do?'". A failed
invocation is exactly when that record matters most, and it is the one case where it was
being discarded. The recorder's docstring says the same thing — "if the model times out at
step 4 of an agent loop, we still want the first three tool calls" — and the rollback
defeated it.

These tests go over HTTP, because that is where the transaction is owned.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import delete, select

from app.infrastructure.database import session_scope
from app.models.ai import AIRun

from ..integration._stub_provider import StubProvider, answer, no_choices

#: Synthetic, and marked as such so the repository's secret scanner can tell.
STUB_KEY = "sk-fake-stub-0000000000-not-a-real-credential"


async def _register(client: httpx.AsyncClient) -> tuple[uuid.UUID, dict[str, str]]:
    name = f"rec_{uuid.uuid4().hex[:8]}"
    response = await client.post(
        "/auth/register",
        json={"email": f"{name}@aiworkbench.dev", "username": name, "password": "record1234"},
    )
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    return uuid.UUID(data["user"]["id"]), {"Authorization": f"Bearer {data['access_token']}"}


def _point_at(owner: uuid.UUID, base_url: str, *, timeout: float = 20.0) -> None:
    from app.ai.providers.factory import reset_provider_cache
    from app.ai.runtime_config import save

    save(
        owner,
        {
            "provider": "openai_compatible",
            "base_url": base_url,
            "api_key": STUB_KEY,
            "model": "stub-model",
            "timeout_seconds": timeout,
        },
    )
    reset_provider_cache()


async def _runs_for(owner: uuid.UUID) -> list[AIRun]:
    async with session_scope() as session:
        return list(
            (
                await session.execute(
                    select(AIRun).where(AIRun.owner_id == owner).order_by(AIRun.created_at)
                )
            ).scalars()
        )


async def _drop(owner: uuid.UUID) -> None:
    async with session_scope() as session:
        await session.execute(delete(AIRun).where(AIRun.owner_id == owner))


@pytest.fixture
async def owner_and_headers(client: httpx.AsyncClient):
    owner, headers = await _register(client)
    try:
        yield owner, headers
    finally:
        from app.ai.providers.factory import reset_provider_cache

        reset_provider_cache()
        await _drop(owner)


async def test_a_provider_format_failure_still_records_the_run(
    client: httpx.AsyncClient, owner_and_headers
) -> None:
    """HTTP 200 with no `choices`. The request fails; the record must not vanish."""
    owner, headers = owner_and_headers

    with StubProvider([no_choices()]) as stub:
        _point_at(owner, stub.base_url)
        response = await client.post(
            "/agents/run", headers=headers, json={"goal": "列一下未完成的任务", "max_steps": 2}
        )

    # The request itself fails — that is correct and unchanged.
    assert response.status_code >= 400, response.text

    runs = await _runs_for(owner)
    assert runs, "a failed run must still leave an ai_runs row"
    run = runs[-1]
    assert run.status == "FAILED", f"expected FAILED, got {run.status}"
    assert run.error_code, "the record must say what went wrong"
    assert run.run_type == "AGENT"


async def test_a_provider_http_failure_still_records_the_run(
    client: httpx.AsyncClient, owner_and_headers
) -> None:
    owner, headers = owner_and_headers

    with StubProvider([answer()], status=503) as stub:
        _point_at(owner, stub.base_url)
        response = await client.post(
            "/agents/run", headers=headers, json={"goal": "列一下未完成的任务", "max_steps": 2}
        )

    assert response.status_code >= 400
    runs = await _runs_for(owner)
    assert runs, "a 503 from the provider must still leave a record"
    assert runs[-1].status in {"FAILED", "TIMEOUT"}
    assert runs[-1].error_code


async def test_a_successful_run_is_recorded_exactly_once(
    client: httpx.AsyncClient, owner_and_headers
) -> None:
    """The guard on the guard: the fix must not double-write on the happy path."""
    owner, headers = owner_and_headers

    with StubProvider([answer("做完了。")]) as stub:
        _point_at(owner, stub.base_url)
        response = await client.post(
            "/agents/run", headers=headers, json={"goal": "回答一个问题", "max_steps": 2}
        )

    assert response.status_code == 200, response.text
    runs = await _runs_for(owner)
    assert len(runs) == 1, f"expected exactly one run row, got {len(runs)}"
    assert runs[0].status == "SUCCEEDED"
    assert runs[0].response_text
