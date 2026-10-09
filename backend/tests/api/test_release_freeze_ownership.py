"""Release-freeze regression tests — authentication and cross-account ownership.

Two properties, checked across the whole API rather than on a hand-picked endpoint.

**Every route requires authentication.** The first part enumerates the real route table
from the OpenAPI schema and asserts that an unauthenticated request is refused. A route
added without the `CurrentUser` dependency fails this immediately, which is the point —
"most routes are protected" is not a property anyone can rely on.

**A resource belongs to one account.** The second part creates data as A and asserts that
B cannot read, update or delete it. The check is on the *resource*, not on the requester:
being logged in is not the same as owning the row.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

#: Endpoints that are deliberately reachable without a token.
PUBLIC = {
    ("GET", "/api/v1/health"),
    ("GET", "/api/v1/health/deps"),
    ("POST", "/api/v1/auth/register"),
    ("POST", "/api/v1/auth/login"),
}

#: A placeholder for `{...}` path parameters. Any value works: the auth dependency runs
#: before the handler, so the request never reaches the code that would look it up.
_PLACEHOLDER = uuid.UUID("00000000-0000-4000-8000-000000000001")


def _routes() -> list[tuple[str, str]]:
    from app.main import create_app

    schema = create_app().openapi()
    found: list[tuple[str, str]] = []
    for path, operations in schema.get("paths", {}).items():
        for method in operations:
            if method.upper() in {"GET", "POST", "PATCH", "PUT", "DELETE"}:
                found.append((method.upper(), path))
    return sorted(found)


def _concrete(path: str) -> str:
    """An OpenAPI path turned into something the test client can request.

    Two adjustments, both necessary:

    * ``{param}`` becomes a UUID, because the auth dependency runs before the handler and
      the value is never looked up.
    * the ``/api/v1`` prefix is stripped. The OpenAPI schema reports full paths, but the
      test client's base URL already ends in ``/api/v1/`` and httpx **concatenates**
      rather than resolving, so a full path becomes ``/api/v1/api/v1/...`` — which lands
      on the SPA catch-all and returns 404/405 instead of 401.
    """
    path = path.removeprefix("/api/v1")
    while "{" in path:
        start = path.index("{")
        end = path.index("}", start)
        path = path[:start] + str(_PLACEHOLDER) + path[end + 1 :]
    return path or "/"


def test_the_route_table_is_not_empty() -> None:
    """A guard on the guard: an empty enumeration would make everything below vacuous."""
    routes = _routes()
    assert len(routes) > 80, f"only {len(routes)} routes enumerated"


@pytest.mark.parametrize(("method", "path"), _routes())
async def test_every_route_refuses_an_unauthenticated_request(
    client: httpx.AsyncClient, method: str, path: str
) -> None:
    if (method, path) in PUBLIC:
        pytest.skip("deliberately public")
    response = await client.request(method, _concrete(path), json={})
    assert response.status_code == 401, f"{method} {path} -> {response.status_code}"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        (m, p)
        for m, p in _routes()
        if (m, p) not in PUBLIC
        # Exclude the routes that are documented as returning their own status before
        # authentication for a different reason, if any are ever added.
    ],
)
async def test_unauthenticated_requests_do_not_leak_a_body(
    client: httpx.AsyncClient, method: str, path: str
) -> None:
    """A 401 must not carry resource data or an internal traceback."""
    response = await client.request(method, _concrete(path), json={})
    body = response.text
    assert "Traceback" not in body
    assert "password" not in body.lower()


# ------------------------------------------------------------ cross-account access --


async def _register(client: httpx.AsyncClient) -> tuple[str, dict[str, str]]:
    name = f"own_{uuid.uuid4().hex[:8]}"
    response = await client.post(
        "/auth/register",
        json={"email": f"{name}@aiworkbench.dev", "username": name, "password": "owner1234"},
    )
    assert response.status_code == 201, response.text
    return name, {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


def _created(response: httpx.Response) -> dict[str, Any]:
    assert response.status_code in (200, 201), response.text
    payload = response.json()
    data = payload.get("data", payload)
    return data


async def _pair(client: httpx.AsyncClient) -> tuple[dict[str, str], dict[str, str]]:
    _, a = await _register(client)
    _, b = await _register(client)
    return a, b


async def test_project_is_not_reachable_by_another_account(client: httpx.AsyncClient) -> None:
    a, b = await _pair(client)
    project = _created(
        await client.post("/projects", headers=a, json={"name": "A's project"})
    )
    pid = project["id"]

    assert (await client.get(f"/projects/{pid}", headers=b)).status_code == 404
    assert (await client.patch(f"/projects/{pid}", headers=b, json={"name": "hijacked"})).status_code == 404
    assert (await client.delete(f"/projects/{pid}", headers=b)).status_code == 404

    # ... and A still has it, unmodified.
    still = await client.get(f"/projects/{pid}", headers=a)
    assert still.status_code == 200
    assert still.json()["data"]["name"] == "A's project"


async def test_knowledge_base_is_not_reachable_by_another_account(client: httpx.AsyncClient) -> None:
    a, b = await _pair(client)
    kb = _created(await client.post("/knowledge-bases", headers=a, json={"name": "A's KB"}))
    kid = kb["id"]

    assert (await client.get(f"/knowledge-bases/{kid}", headers=b)).status_code == 404
    assert (await client.delete(f"/knowledge-bases/{kid}", headers=b)).status_code == 404


async def test_task_is_not_reachable_by_another_account(client: httpx.AsyncClient) -> None:
    a, b = await _pair(client)
    task = _created(
        await client.post("/tasks", headers=a, json={"title": "A's task"})
    )
    tid = task["id"]

    assert (await client.get(f"/tasks/{tid}", headers=b)).status_code == 404
    assert (await client.patch(f"/tasks/{tid}", headers=b, json={"title": "hijacked"})).status_code == 404
    assert (await client.delete(f"/tasks/{tid}", headers=b)).status_code == 404


async def test_note_is_not_reachable_by_another_account(client: httpx.AsyncClient) -> None:
    a, b = await _pair(client)
    note = _created(await client.post("/notes", headers=a, json={"title": "A's note", "body": "x"}))
    nid = note["id"]

    assert (await client.get(f"/notes/{nid}", headers=b)).status_code == 404
    assert (await client.delete(f"/notes/{nid}", headers=b)).status_code == 404


async def test_listing_only_returns_your_own_resources(client: httpx.AsyncClient) -> None:
    """The list endpoint must filter by owner, not just hide the detail page."""
    a, b = await _pair(client)
    mine = _created(await client.post("/projects", headers=a, json={"name": "only A's"}))

    listed = await client.get("/projects", headers=b)
    assert listed.status_code == 200
    payload = listed.json()["data"]
    items = payload.get("items", payload if isinstance(payload, list) else [])
    assert all(str(item.get("id")) != str(mine["id"]) for item in items)


async def test_guessing_a_parent_id_does_not_grant_access(client: httpx.AsyncClient) -> None:
    """A child created under someone else's parent must be refused, not silently created."""
    a, b = await _pair(client)
    kb = _created(await client.post("/knowledge-bases", headers=a, json={"name": "A's KB"}))

    response = await client.post(
        "/tasks",
        headers=b,
        json={"title": "injected", "knowledge_base_id": kb["id"]},
    )
    # Either refused outright, or created without the foreign link — never attached to A's KB.
    if response.status_code in (200, 201):
        data = response.json().get("data", {})
        assert data.get("knowledge_base_id") != kb["id"]
    else:
        assert response.status_code in (400, 403, 404, 422)
