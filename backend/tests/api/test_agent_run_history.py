"""The run history endpoint: one entry per run, and it survives a restart.

The bug this pins down: ``GET /agent/runs`` answered from the process-local
``RunRegistry`` alone. The registry is empty when the process starts, so every run a user
had ever started disappeared from the endpoint after a restart while the rows sat
untouched in ``agent_runs``. The database held the history and nothing read it.

What is asserted here:

* a stored run is returned by the list endpoint;
* a run that is *also* live appears **once**, marked live, with the live state — not twice;
* one account cannot see another's runs, in the list or by id;
* each status maps to a state the UI has a case for (``SUCCEEDED`` is a row status and not
  a valid ``RunState``; handing it through would put a value on screen that
  ``describeState()`` cannot render).

The restart itself is not testable from inside a pytest process — the registry is a module
global and the point is that a *new* process has an empty one. ``scripts/
verify_agent_run_persistence.py`` does that part against a real server on a real socket,
and is where the "still there after a restart" evidence comes from.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.domain.enums import RunStatus, RunType


async def _register(client: httpx.AsyncClient) -> tuple[str, uuid.UUID]:
    """A fresh account, returning its token and user id."""
    handle = uuid.uuid4().hex[:8]
    response = await client.post(
        "/auth/register",
        json={
            "email": f"runs_{handle}@aiworkbench.dev",
            "username": f"runs_{handle}",
            "password": "test1234",
            "display_name": "Runs",
        },
    )
    assert response.status_code == 201, response.text
    data = response.json()["data"]
    return data["access_token"], uuid.UUID(data["user"]["id"])


async def _store_run(
    session,
    *,
    owner_id: uuid.UUID,
    goal: str,
    status: RunStatus,
    state: str | None = None,
    created_at: datetime | None = None,
) -> str:
    """Write an AIRun + AgentRun pair directly.

    Deliberately not by running the agent: a run would call the planner, and this round's
    model-call count has to stay at zero. The rows are what the endpoint reads, so the
    rows are what the test writes.
    """
    from app.models.ai import AgentRun, AIRun

    run_id = f"run_{uuid.uuid4().hex[:12]}"
    ai_run = AIRun(
        run_id=run_id,
        owner_id=owner_id,
        run_type=RunType.AGENT.value,
        status=status.value,
        provider="offline",
        model="heuristic-extractive-v1",
        prompt_name="agent_planner",
        request_payload={"goal": goal},
    )
    if created_at is not None:
        ai_run.created_at = created_at
        ai_run.updated_at = created_at
    session.add(ai_run)
    await session.flush()

    session.add(
        AgentRun(
            ai_run_id=ai_run.id,
            owner_id=owner_id,
            goal=goal,
            status=status.value,
            max_steps=8,
            steps_used=3,
            scratchpad={"state": state} if state else {},
        )
    )
    await session.commit()
    return run_id


@pytest.mark.asyncio
async def test_a_stored_run_is_returned_by_the_list_endpoint(
    client: httpx.AsyncClient, session
) -> None:
    token, user_id = await _register(client)
    run_id = await _store_run(
        session,
        owner_id=user_id,
        goal="写一份目录 PPT",
        status=RunStatus.SUCCEEDED,
        state="COMPLETED",
    )

    body = (await client.get("/agent/runs", headers={"Authorization": f"Bearer {token}"})).json()[
        "data"
    ]
    ids = [entry["run_id"] for entry in body["runs"]]
    assert run_id in ids, ids

    entry = next(item for item in body["runs"] if item["run_id"] == run_id)
    assert entry["goal"] == "写一份目录 PPT"
    assert entry["status"] == "SUCCEEDED"
    assert entry["state"] == "COMPLETED"
    assert entry["live"] is False


@pytest.mark.asyncio
async def test_a_run_that_is_also_live_appears_once(client: httpx.AsyncClient, session) -> None:
    """The same run is in the registry *and* in the table. The user sees it once.

    Listing both is the obvious way to get this wrong: the run shows as "running" from the
    registry and as whatever the row still says, and the user is told they have two runs.
    """
    from app.ai.agent.plan import RunState
    from app.services.autonomous_service import get_run_registry

    token, user_id = await _register(client)
    run_id = await _store_run(
        session,
        owner_id=user_id,
        goal="写一份目录 PPT",
        status=RunStatus.RUNNING,
        state="EXECUTING",
    )

    controller = get_run_registry().create(run_id, user_id)
    controller.set_state(RunState.OBSERVING, "正在读取结果")
    try:
        body = (
            await client.get("/agent/runs", headers={"Authorization": f"Bearer {token}"})
        ).json()["data"]
        matches = [item for item in body["runs"] if item["run_id"] == run_id]
        assert len(matches) == 1, f"the run appears {len(matches)} times"
        assert matches[0]["live"] is True
        # The live state wins over the stored one — it is the newer fact.
        assert matches[0]["state"] == "OBSERVING"
    finally:
        get_run_registry().drop(run_id)


@pytest.mark.asyncio
async def test_one_account_cannot_read_anothers_runs(client: httpx.AsyncClient, session) -> None:
    """And the refusal is provably the ownership rule, not a malformed request.

    The run-scoped routes used to raise `ValidationError`, which is **422 with
    `code="validation_error"`** — byte-identical to what FastAPI returns when a request
    body or path parameter fails validation. So "422" on its own proved nothing: a test
    asserting `status == 422` would have passed just as happily if the request had simply
    been broken, and a person reading a log could not tell the two apart.

    Now the ownership refusal is 404 with `code="run_not_found"`, and this test pins both
    halves of that: the refusal is a 404/`run_not_found`, and a genuinely malformed request
    is a 422/`validation_error`. If they ever converge again, one of these fails.
    """
    token_a, user_a = await _register(client)
    token_b, _user_b = await _register(client)
    run_id = await _store_run(
        session, owner_id=user_a, goal="私密任务", status=RunStatus.SUCCEEDED, state="COMPLETED"
    )

    listing = (
        await client.get("/agent/runs", headers={"Authorization": f"Bearer {token_b}"})
    ).json()["data"]
    assert run_id not in [entry["run_id"] for entry in listing["runs"]]

    by_id = await client.get(
        f"/agent/runs/{run_id}", headers={"Authorization": f"Bearer {token_b}"}
    )
    body = by_id.json()
    assert by_id.status_code == 404, f"expected the not-found answer, got {by_id.status_code}"
    assert body["error"]["code"] == "run_not_found", body["error"]

    # The owner still gets it, so the refusal is about ownership and not about the route.
    own = await client.get(f"/agent/runs/{run_id}", headers={"Authorization": f"Bearer {token_a}"})
    assert own.status_code == 200
    assert own.json()["data"]["run_id"] == run_id

    # **Existence is not confirmed.** A run id that does not exist at all produces the same
    # status and the same code as one that exists but is not this account's — otherwise the
    # endpoint would be an oracle for "does this run id exist".
    missing = await client.get(
        "/agent/runs/run_000000000000", headers={"Authorization": f"Bearer {token_b}"}
    )
    assert missing.status_code == by_id.status_code
    assert missing.json()["error"]["code"] == body["error"]["code"]

    # And the two failure kinds are still distinguishable. A malformed request is a 422
    # with `validation_error`; the ownership refusal above is not.
    malformed = await client.post(
        "/agent/runs",
        headers={"Authorization": f"Bearer {token_a}"},
        json={"goal": "x"},  # shorter than the 3-character minimum
    )
    assert malformed.status_code == 422, malformed.status_code
    assert malformed.json()["error"]["code"] == "validation_error"
    assert malformed.json()["error"]["code"] != body["error"]["code"], (
        "the ownership refusal and a malformed request are indistinguishable again"
    )


@pytest.mark.asyncio
async def test_the_refusal_comes_from_the_ownership_rule_not_the_route(
    client: httpx.AsyncClient, session
) -> None:
    """A wrong account is refused for a run that *does* exist and *is* readable by someone.

    This is the control for the test above: the same id, the same route, the same token
    shape — the only variable is which account the token belongs to. A 404 here cannot be
    "the id was malformed" (the owner reads it fine) or "the route is missing" (the owner
    gets a 200 from it).
    """
    token_a, user_a = await _register(client)
    token_b, _user_b = await _register(client)
    run_id = await _store_run(
        session, owner_id=user_a, goal="归属对照", status=RunStatus.SUCCEEDED, state="COMPLETED"
    )

    owner_response = await client.get(
        f"/agent/runs/{run_id}", headers={"Authorization": f"Bearer {token_a}"}
    )
    stranger_response = await client.get(
        f"/agent/runs/{run_id}", headers={"Authorization": f"Bearer {token_b}"}
    )

    assert owner_response.status_code == 200, "the id or the route is wrong, not the account"
    assert stranger_response.status_code == 404
    assert stranger_response.json()["error"]["code"] == "run_not_found"
    # The two accounts differ in exactly one thing: who they are.
    assert owner_response.json()["data"]["run_id"] == run_id


@pytest.mark.asyncio
async def test_every_status_maps_to_a_state_the_ui_can_render(
    client: httpx.AsyncClient, session
) -> None:
    """A row status is not a run state, and the endpoint must not pretend otherwise.

    ``SUCCEEDED`` is what the column holds; ``COMPLETED`` is what the run was doing. Passing
    the column through would hand the frontend a value its ``describeState()`` has no case
    for, which renders as a blank chip rather than an error — the kind of wrong that is
    noticed late.
    """
    from app.ai.agent.plan import RunState

    token, user_id = await _register(client)
    valid_states = {state.value for state in RunState}
    stored = {}
    for status in (RunStatus.RUNNING, RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED):
        # No scratchpad state: this is the mapping the endpoint has to supply itself.
        stored[status.value] = await _store_run(
            session, owner_id=user_id, goal=f"goal {status.value}", status=status
        )

    body = (
        await client.get("/agent/runs?limit=50", headers={"Authorization": f"Bearer {token}"})
    ).json()["data"]
    by_id = {entry["run_id"]: entry for entry in body["runs"]}

    for status, run_id in stored.items():
        entry = by_id[run_id]
        assert entry["state"] in valid_states, f"{status} produced state {entry['state']!r}"
        assert entry["status"] == status, "the raw status is still reported separately"


@pytest.mark.asyncio
async def test_history_is_newest_first_and_paginates(client: httpx.AsyncClient, session) -> None:
    token, user_id = await _register(client)
    base = datetime.now(UTC) - timedelta(hours=5)
    created = []
    for index in range(5):
        created.append(
            await _store_run(
                session,
                owner_id=user_id,
                goal=f"任务 {index}",
                status=RunStatus.SUCCEEDED,
                state="COMPLETED",
                created_at=base + timedelta(hours=index),
            )
        )

    first_page = (
        await client.get(
            "/agent/runs?limit=2&offset=0", headers={"Authorization": f"Bearer {token}"}
        )
    ).json()["data"]
    assert first_page["total"] == 5
    assert len(first_page["runs"]) == 2
    # Newest first: the last one created leads.
    assert first_page["runs"][0]["run_id"] == created[-1]
    assert first_page["runs"][1]["run_id"] == created[-2]

    second_page = (
        await client.get(
            "/agent/runs?limit=2&offset=2", headers={"Authorization": f"Bearer {token}"}
        )
    ).json()["data"]
    assert second_page["runs"][0]["run_id"] == created[-3]
    assert {entry["run_id"] for entry in first_page["runs"]} & {
        entry["run_id"] for entry in second_page["runs"]
    } == set(), "the pages overlap"


@pytest.mark.asyncio
async def test_the_status_filter_is_applied_by_the_database(
    client: httpx.AsyncClient, session
) -> None:
    token, user_id = await _register(client)
    await _store_run(
        session, owner_id=user_id, goal="好的", status=RunStatus.SUCCEEDED, state="COMPLETED"
    )
    await _store_run(
        session, owner_id=user_id, goal="坏的", status=RunStatus.FAILED, state="FAILED"
    )

    body = (
        await client.get(
            "/agent/runs?status_filter=FAILED", headers={"Authorization": f"Bearer {token}"}
        )
    ).json()["data"]
    assert [entry["goal"] for entry in body["runs"]] == ["坏的"]
    assert body["total"] == 1


@pytest.mark.asyncio
async def test_a_terminal_row_never_reports_a_running_state(
    client: httpx.AsyncClient, session
) -> None:
    """A row that has ended must not display as still going.

    This is the shape of a real bug: the startup reconcile marked an interrupted run's row
    `FAILED` while the run's scratchpad still said `EXECUTING`, and the history endpoint
    reported `EXECUTING` — for a run that was over. The scratchpad is usually the more
    informative source (it distinguishes `LIMIT_REACHED` from `CANCELLED`), so it is still
    preferred when it agrees; it is only overridden when it contradicts a terminal row.
    """
    token, user_id = await _register(client)

    stale = await _store_run(
        session, owner_id=user_id, goal="被中断", status=RunStatus.FAILED, state="EXECUTING"
    )
    detailed = await _store_run(
        session, owner_id=user_id, goal="超限", status=RunStatus.CANCELLED, state="LIMIT_REACHED"
    )
    running = await _store_run(
        session, owner_id=user_id, goal="进行中", status=RunStatus.RUNNING, state="EXECUTING"
    )

    body = (
        await client.get("/agent/runs?limit=50", headers={"Authorization": f"Bearer {token}"})
    ).json()["data"]
    by_id = {entry["run_id"]: entry for entry in body["runs"]}

    assert by_id[stale]["state"] == "FAILED", "a finished run is being shown as still running"
    assert by_id[detailed]["state"] == "LIMIT_REACHED", "the more specific state was flattened"
    assert by_id[running]["state"] == "EXECUTING", "a running run lost its live state"
