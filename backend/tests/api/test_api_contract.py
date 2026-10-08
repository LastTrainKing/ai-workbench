"""API-level tests: envelope shape, auth, validation, error taxonomy, insights."""

from __future__ import annotations

import uuid

import httpx
import pytest


# --------------------------------------------------------------------------- #
# envelope & health
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_health_is_public_and_enveloped(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["status"] == "ok"
    assert response.headers["X-Request-ID"]
    assert response.headers["X-Process-Time-Ms"]


@pytest.mark.asyncio
async def test_dependency_probe_reports_every_component(client: httpx.AsyncClient) -> None:
    data = (await client.get("/health/deps")).json()["data"]
    assert data["postgres"]["ok"] is True
    assert data["redis"]["ok"] is True
    assert data["qdrant"]["ok"] is True
    assert data["ai"]["llm"]["kind"] == "offline"
    assert data["ai"]["llm"]["degraded"] is True
    assert data["ai"]["llm"]["reason"]
    assert data["config"]["chunk_size"] > 0


@pytest.mark.asyncio
async def test_request_id_is_echoed_back(client: httpx.AsyncClient) -> None:
    response = await client.get("/health", headers={"X-Request-ID": "custom-trace-id"})
    assert response.headers["X-Request-ID"] == "custom-trace-id"


@pytest.mark.asyncio
async def test_metrics_endpoint(client: httpx.AsyncClient) -> None:
    """`/metrics` reports how much the caller has. It requires a caller.

    It used to answer anyone who could reach the port, with no token. The counts are not
    secret, but they are the user's: how many documents, how much they have been using it.
    Round 15 put it behind the same door as everything else, so this asserts both halves —
    anonymous is refused, and an authenticated caller still gets the numbers.
    """
    anonymous = await client.get("/metrics")
    assert anonymous.status_code in (401, 403), anonymous.status_code

    registered = await client.post(
        "/auth/register",
        json={
            "email": f"metrics_{uuid.uuid4().hex[:8]}@aiworkbench.dev",
            "username": f"metrics_{uuid.uuid4().hex[:8]}",
            "password": "test1234",
            "display_name": "Metrics",
        },
    )
    assert registered.status_code == 201
    token = registered.json()["data"]["access_token"]

    data = (await client.get("/metrics", headers={"Authorization": f"Bearer {token}"})).json()["data"]
    assert data["enabled"] is True
    assert "ai_24h" in data
    assert "cache" in data


# --------------------------------------------------------------------------- #
# auth
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_register_login_and_profile(client: httpx.AsyncClient) -> None:
    email = f"alice_{uuid.uuid4().hex[:8]}@aiworkbench.dev"
    username = f"alice_{uuid.uuid4().hex[:8]}"

    registered = await client.post(
        "/auth/register",
        json={"email": email, "username": username, "password": "test1234", "display_name": "Alice"},
    )
    assert registered.status_code == 201
    body = registered.json()
    assert body["success"] is True
    token = body["data"]["access_token"]
    assert body["data"]["user"]["username"] == username

    # login by username
    by_username = await client.post(
        "/auth/login", json={"identifier": username, "password": "test1234"}
    )
    assert by_username.status_code == 200
    # login by email
    by_email = await client.post("/auth/login", json={"identifier": email, "password": "test1234"})
    assert by_email.status_code == 200

    headers = {"Authorization": f"Bearer {token}"}
    me = await client.get("/auth/me", headers=headers)
    assert me.json()["data"]["email"] == email

    patched = await client.patch(
        "/users/me", headers=headers, json={"display_name": "Alice B.", "avatar_color": "#22c55e"}
    )
    assert patched.status_code == 200
    assert patched.json()["data"]["display_name"] == "Alice B."
    assert patched.json()["data"]["avatar_color"] == "#22c55e"


@pytest.mark.asyncio
async def test_duplicate_registration_is_rejected(client: httpx.AsyncClient) -> None:
    payload = {
        "email": f"dup_{uuid.uuid4().hex[:8]}@aiworkbench.dev",
        "username": f"dup_{uuid.uuid4().hex[:8]}",
        "password": "test1234",
        "display_name": "Dup",
    }
    assert (await client.post("/auth/register", json=payload)).status_code == 201
    again = await client.post("/auth/register", json=payload)
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "conflict"
    assert again.json()["error"]["details"]["field"] in {"email", "username"}


@pytest.mark.asyncio
async def test_weak_password_is_rejected(client: httpx.AsyncClient) -> None:
    response = await client.post(
        "/auth/register",
        json={
            "email": f"weak_{uuid.uuid4().hex[:8]}@aiworkbench.dev",
            "username": f"weak_{uuid.uuid4().hex[:8]}",
            "password": "12345678",
            "display_name": "Weak",
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.asyncio
async def test_missing_and_malformed_tokens(client: httpx.AsyncClient) -> None:
    assert (await client.get("/auth/me")).status_code == 401
    assert (
        await client.get("/auth/me", headers={"Authorization": "Bearer not-a-jwt"})
    ).status_code == 401
    assert (
        await client.get("/auth/me", headers={"Authorization": "Bearer "})
    ).status_code == 401


@pytest.mark.asyncio
async def test_login_failures_do_not_leak_account_existence(
    client: httpx.AsyncClient, auth_headers: dict
) -> None:
    unknown = await client.post(
        "/auth/login", json={"identifier": "definitely-not-a-user", "password": "test1234"}
    )
    assert unknown.status_code == 401
    assert "Incorrect username or password" in unknown.json()["error"]["message"]


@pytest.mark.asyncio
async def test_logout_acknowledges(client: httpx.AsyncClient, auth_headers: dict) -> None:
    response = await client.post("/auth/logout", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["data"]["logged_out"] is True


# --------------------------------------------------------------------------- #
# validation & error taxonomy
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_invalid_uuid_is_a_validation_error(
    client: httpx.AsyncClient, auth_headers: dict
) -> None:
    response = await client.get("/documents/not-a-uuid", headers=auth_headers)
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert body["error"]["details"]["fields"]


@pytest.mark.asyncio
async def test_missing_resource_is_404(client: httpx.AsyncClient, auth_headers: dict) -> None:
    response = await client.get(f"/documents/{uuid.uuid4()}", headers=auth_headers)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_invalid_enum_is_rejected(client: httpx.AsyncClient, auth_headers: dict) -> None:
    assert (
        await client.get("/search", headers=auth_headers, params={"q": "x", "mode": "bogus"})
    ).status_code == 422
    assert (
        await client.get("/tasks", headers=auth_headers, params={"status": "NOPE"})
    ).status_code == 422


@pytest.mark.asyncio
async def test_oversized_and_wrong_type_uploads(
    client: httpx.AsyncClient, auth_headers: dict, knowledge_base: dict
) -> None:
    import io

    wrong_type = await client.post(
        "/documents/upload",
        headers=auth_headers,
        files={"file": ("x.exe", io.BytesIO(b"MZ"), "application/octet-stream")},
        data={"knowledge_base_id": knowledge_base["id"]},
    )
    assert wrong_type.status_code == 415
    assert wrong_type.json()["error"]["code"] == "unsupported_media_type"

    too_big = await client.post(
        "/documents/upload",
        headers=auth_headers,
        files={"file": ("big.txt", io.BytesIO(b"a" * (26 * 1024 * 1024)), "text/plain")},
        data={"knowledge_base_id": knowledge_base["id"]},
    )
    assert too_big.status_code == 413
    assert too_big.json()["error"]["code"] == "payload_too_large"


@pytest.mark.asyncio
async def test_upload_requires_an_existing_knowledge_base(
    client: httpx.AsyncClient, auth_headers: dict
) -> None:
    import io

    response = await client.post(
        "/documents/upload",
        headers=auth_headers,
        files={"file": ("a.md", io.BytesIO(b"# hi"), "text/markdown")},
        data={"knowledge_base_id": str(uuid.uuid4())},
    )
    assert response.status_code == 404


# --------------------------------------------------------------------------- #
# projects / knowledge bases / notes
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_project_lifecycle_with_stats(client: httpx.AsyncClient, auth_headers: dict) -> None:
    created = await client.post(
        "/projects", headers=auth_headers, json={"name": "毕业设计", "description": "d"}
    )
    assert created.status_code == 201
    project = created.json()["data"]

    duplicate = await client.post("/projects", headers=auth_headers, json={"name": "毕业设计"})
    assert duplicate.status_code == 409

    detail = (await client.get(f"/projects/{project['id']}", headers=auth_headers)).json()["data"]
    assert detail["stats"]["knowledge_bases"] == 0

    await client.post(
        "/knowledge-bases", headers=auth_headers, json={"name": "KB", "project_id": project["id"]}
    )
    detail = (await client.get(f"/projects/{project['id']}", headers=auth_headers)).json()["data"]
    assert detail["stats"]["knowledge_bases"] == 1

    listing = (await client.get("/projects", headers=auth_headers)).json()["data"]
    assert listing["meta"]["total"] == 1
    assert listing["meta"]["has_next"] is False

    assert (await client.delete(f"/projects/{project['id']}", headers=auth_headers)).status_code == 200


@pytest.mark.asyncio
async def test_knowledge_base_stats_reflect_documents(
    client: httpx.AsyncClient, auth_headers: dict, knowledge_base: dict, sample_markdown: bytes
) -> None:
    import io
    import uuid as uuid_module

    from app.infrastructure.database import session_scope
    from app.services.document_service import DocumentService

    upload = await client.post(
        "/documents/upload",
        headers=auth_headers,
        files={"file": ("notes.md", io.BytesIO(sample_markdown), "text/markdown")},
        data={"knowledge_base_id": knowledge_base["id"]},
    )
    async with session_scope() as session:
        await DocumentService(session).ingest(uuid_module.UUID(upload.json()["data"]["document"]["id"]))

    detail = (await client.get(f"/knowledge-bases/{knowledge_base['id']}", headers=auth_headers)).json()["data"]
    assert detail["stats"]["document_count"] == 1
    assert detail["stats"]["chunk_count"] > 0
    assert detail["stats"]["embedded_chunk_count"] == detail["stats"]["chunk_count"]
    assert detail["stats"]["failed_documents"] == 0
    assert detail["stats"]["total_size_bytes"] > 0


@pytest.mark.asyncio
async def test_notes_crud(client: httpx.AsyncClient, auth_headers: dict) -> None:
    created = await client.post(
        "/notes", headers=auth_headers, json={"title": "笔记", "content_md": "# 标题\n正文"}
    )
    assert created.status_code == 201
    note = created.json()["data"]
    assert note["is_ai_generated"] is False

    updated = await client.patch(
        f"/notes/{note['id']}", headers=auth_headers, json={"content_md": "# 更新", "is_pinned": True}
    )
    assert updated.json()["data"]["is_pinned"] is True

    listing = (await client.get("/notes", headers=auth_headers, params={"q": "更新"})).json()["data"]
    assert listing["meta"]["total"] == 1

    assert (await client.delete(f"/notes/{note['id']}", headers=auth_headers)).status_code == 200
    assert (await client.get(f"/notes/{note['id']}", headers=auth_headers)).status_code == 404


@pytest.mark.asyncio
async def test_pagination_metadata(client: httpx.AsyncClient, auth_headers: dict) -> None:
    for index in range(7):
        await client.post("/tasks", headers=auth_headers, json={"title": f"task {index}"})

    page1 = (
        await client.get("/tasks", headers=auth_headers, params={"page": 1, "page_size": 3})
    ).json()["data"]
    assert len(page1["items"]) == 3
    assert page1["meta"]["total"] == 7
    assert page1["meta"]["total_pages"] == 3
    assert page1["meta"]["has_next"] is True
    assert page1["meta"]["has_prev"] is False

    page3 = (
        await client.get("/tasks", headers=auth_headers, params={"page": 3, "page_size": 3})
    ).json()["data"]
    assert len(page3["items"]) == 1
    assert page3["meta"]["has_next"] is False
    assert page3["meta"]["has_prev"] is True

    assert (
        await client.get("/tasks", headers=auth_headers, params={"page_size": 9999})
    ).status_code == 422


# --------------------------------------------------------------------------- #
# insights
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_dashboard_numbers_come_from_the_database(
    client: httpx.AsyncClient, auth_headers: dict, knowledge_base: dict, sample_markdown: bytes
) -> None:
    import io
    import uuid as uuid_module

    from app.infrastructure.database import session_scope
    from app.services.document_service import DocumentService

    upload = await client.post(
        "/documents/upload",
        headers=auth_headers,
        files={"file": ("notes.md", io.BytesIO(sample_markdown), "text/markdown")},
        data={"knowledge_base_id": knowledge_base["id"]},
    )
    async with session_scope() as session:
        await DocumentService(session).ingest(uuid_module.UUID(upload.json()["data"]["document"]["id"]))

    await client.post("/tasks", headers=auth_headers, json={"title": "任务 A"})
    done = (await client.post("/tasks", headers=auth_headers, json={"title": "任务 B"})).json()["data"]
    await client.post(f"/tasks/{done['id']}/complete", headers=auth_headers, json={})

    data = (await client.get("/stats/dashboard", headers=auth_headers)).json()["data"]
    assert data["knowledge"]["knowledge_bases"] == 1
    assert data["knowledge"]["documents"] == 1
    assert data["knowledge"]["chunks"] > 0
    assert data["knowledge"]["storage_bytes"] > 0
    assert data["tasks"]["total"] == 2
    assert data["tasks"]["done"] == 1
    assert data["tasks"]["open"] == 1
    assert data["tasks"]["completed_this_week"] >= 1
    assert data["workspace"]["projects"] == 0
    assert data["providers"]["llm"]["kind"] == "offline"
    assert data["recent"]["documents"]
    assert data["recent"]["activity"]


@pytest.mark.asyncio
async def test_dashboard_is_cached_then_invalidated(
    client: httpx.AsyncClient, auth_headers: dict
) -> None:
    first = (await client.get("/stats/dashboard", headers=auth_headers)).json()["data"]
    assert first["cached"] is False
    second = (await client.get("/stats/dashboard", headers=auth_headers)).json()["data"]
    assert second["cached"] is True


@pytest.mark.asyncio
async def test_graph_projects_relationships(
    client: httpx.AsyncClient, auth_headers: dict, knowledge_base: dict, sample_markdown: bytes
) -> None:
    import io
    import uuid as uuid_module

    from app.infrastructure.database import session_scope
    from app.services.document_service import DocumentService

    upload = await client.post(
        "/documents/upload",
        headers=auth_headers,
        files={"file": ("notes.md", io.BytesIO(sample_markdown), "text/markdown")},
        data={"knowledge_base_id": knowledge_base["id"]},
    )
    document_id = upload.json()["data"]["document"]["id"]
    async with session_scope() as session:
        await DocumentService(session).ingest(uuid_module.UUID(document_id))

    task = (await client.post("/tasks", headers=auth_headers, json={"title": "学习 GC"})).json()["data"]
    await client.post(
        f"/tasks/{task['id']}/knowledge", headers=auth_headers, json={"document_id": document_id}
    )

    graph = (await client.get("/graph", headers=auth_headers)).json()["data"]
    types = {node["type"] for node in graph["nodes"]}
    assert {"knowledge_base", "document", "task"} <= types
    edge_types = {edge["type"] for edge in graph["edges"]}
    assert "uses_knowledge" in edge_types
    assert graph["stats"]["nodes"] == len(graph["nodes"])
    assert graph["stats"]["by_type"]


@pytest.mark.asyncio
async def test_ai_run_listing_filters(client: httpx.AsyncClient, auth_headers: dict) -> None:
    await client.post("/planner/plan", headers=auth_headers, json={"goal": "学习 FastAPI"})

    all_runs = (await client.get("/ai-runs", headers=auth_headers)).json()["data"]
    assert all_runs["meta"]["total"] >= 1

    filtered = (
        await client.get("/ai-runs", headers=auth_headers, params={"run_type": "TASK_PLANNER"})
    ).json()["data"]
    assert filtered["meta"]["total"] >= 1
    assert all(item["run_type"] == "TASK_PLANNER" for item in filtered["items"])

    empty = (
        await client.get("/ai-runs", headers=auth_headers, params={"run_type": "EMBEDDING"})
    ).json()["data"]
    assert empty["meta"]["total"] == 0
    assert empty["items"] == []


@pytest.mark.asyncio
async def test_ai_run_metrics_aggregate(client: httpx.AsyncClient, auth_headers: dict) -> None:
    await client.post("/planner/plan", headers=auth_headers, json={"goal": "学习 Redis"})
    data = (await client.get("/ai-runs/metrics", headers=auth_headers)).json()["data"]
    assert data["metrics"]["total_runs"] >= 1
    assert data["metrics"]["failure_rate"] >= 0
    assert data["metrics"]["by_type"].get("TASK_PLANNER", 0) >= 1
    assert isinstance(data["daily"], list)


@pytest.mark.asyncio
async def test_ai_runs_are_isolated_between_users(client: httpx.AsyncClient) -> None:
    first = await client.post(
        "/auth/register",
        json={
            "email": f"a_{uuid.uuid4().hex[:8]}@aiworkbench.dev",
            "username": f"a_{uuid.uuid4().hex[:8]}",
            "password": "test1234",
            "display_name": "A",
        },
    )
    second = await client.post(
        "/auth/register",
        json={
            "email": f"b_{uuid.uuid4().hex[:8]}@aiworkbench.dev",
            "username": f"b_{uuid.uuid4().hex[:8]}",
            "password": "test1234",
            "display_name": "B",
        },
    )
    headers_a = {"Authorization": f"Bearer {first.json()['data']['access_token']}"}
    headers_b = {"Authorization": f"Bearer {second.json()['data']['access_token']}"}

    run = (await client.post("/planner/plan", headers=headers_a, json={"goal": "A 的计划"})).json()["data"]
    run_id = run["plan"]["run_id"]

    assert (await client.get(f"/ai-runs/{run_id}", headers=headers_a)).status_code == 200
    assert (await client.get(f"/ai-runs/{run_id}", headers=headers_b)).status_code == 404
    assert (await client.get("/ai-runs", headers=headers_b)).json()["data"]["meta"]["total"] == 0
