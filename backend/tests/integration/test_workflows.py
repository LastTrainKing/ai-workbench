"""Integration tests: task workflow, planner, agent and prompt versioning."""

from __future__ import annotations

import uuid

import httpx
import pytest

pytestmark = pytest.mark.integration


# --------------------------------------------------------------------------- #
# tasks
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_task_crud_and_status_transitions(
    client: httpx.AsyncClient, auth_headers: dict
) -> None:
    created = await client.post(
        "/tasks",
        headers=auth_headers,
        json={"title": "整理 JVM 笔记", "priority": "HIGH", "estimated_minutes": 90, "due_date": "+7d"},
    )
    assert created.status_code == 201
    task = created.json()["data"]
    assert task["status"] == "TODO"
    assert task["due_date"] is not None

    updated = await client.patch(
        f"/tasks/{task['id']}", headers=auth_headers, json={"status": "IN_PROGRESS"}
    )
    assert updated.status_code == 200
    assert updated.json()["data"]["status"] == "IN_PROGRESS"

    completed = await client.post(
        f"/tasks/{task['id']}/complete", headers=auth_headers, json={"actual_minutes": 45}
    )
    assert completed.status_code == 200
    assert completed.json()["data"]["status"] == "DONE"
    assert completed.json()["data"]["actual_minutes"] == 45
    assert completed.json()["data"]["completed_at"] is not None

    assert (await client.delete(f"/tasks/{task['id']}", headers=auth_headers)).status_code == 200
    assert (await client.get(f"/tasks/{task['id']}", headers=auth_headers)).status_code == 404


@pytest.mark.asyncio
async def test_relative_and_iso_due_dates(client: httpx.AsyncClient, auth_headers: dict) -> None:
    for raw in ("2026-11-01", "+7d", "2w", "3个月"):
        response = await client.post(
            "/tasks", headers=auth_headers, json={"title": f"due {raw}", "due_date": raw}
        )
        assert response.status_code == 201, raw
        assert response.json()["data"]["due_date"] is not None


@pytest.mark.asyncio
async def test_invalid_due_date_is_rejected(client: httpx.AsyncClient, auth_headers: dict) -> None:
    response = await client.post(
        "/tasks", headers=auth_headers, json={"title": "bad", "due_date": "not-a-date"}
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_dependency_blocks_completion(client: httpx.AsyncClient, auth_headers: dict) -> None:
    first = (
        await client.post("/tasks", headers=auth_headers, json={"title": "准备资料"})
    ).json()["data"]
    second = (
        await client.post("/tasks", headers=auth_headers, json={"title": "阅读资料"})
    ).json()["data"]

    link = await client.post(
        f"/tasks/{second['id']}/dependencies",
        headers=auth_headers,
        json={"depends_on_task_id": first["id"]},
    )
    assert link.status_code == 200

    blocked = await client.post(f"/tasks/{second['id']}/complete", headers=auth_headers, json={})
    assert blocked.status_code == 409
    assert blocked.json()["error"]["code"] == "conflict"

    detail = (await client.get(f"/tasks/{second['id']}", headers=auth_headers)).json()["data"]
    assert detail["is_blocked"] is True

    await client.post(f"/tasks/{first['id']}/complete", headers=auth_headers, json={})
    unblocked = await client.post(f"/tasks/{second['id']}/complete", headers=auth_headers, json={})
    assert unblocked.status_code == 200


@pytest.mark.asyncio
async def test_circular_dependency_is_rejected(
    client: httpx.AsyncClient, auth_headers: dict
) -> None:
    a = (await client.post("/tasks", headers=auth_headers, json={"title": "A"})).json()["data"]
    b = (await client.post("/tasks", headers=auth_headers, json={"title": "B"})).json()["data"]

    assert (
        await client.post(
            f"/tasks/{b['id']}/dependencies",
            headers=auth_headers,
            json={"depends_on_task_id": a["id"]},
        )
    ).status_code == 200

    cycle = await client.post(
        f"/tasks/{a['id']}/dependencies",
        headers=auth_headers,
        json={"depends_on_task_id": b["id"]},
    )
    assert cycle.status_code == 409

    self_dep = await client.post(
        f"/tasks/{a['id']}/dependencies",
        headers=auth_headers,
        json={"depends_on_task_id": a["id"]},
    )
    assert self_dep.status_code == 422


@pytest.mark.asyncio
async def test_task_tree_is_hierarchical(client: httpx.AsyncClient, auth_headers: dict) -> None:
    root = (
        await client.post("/tasks", headers=auth_headers, json={"title": "阶段一"})
    ).json()["data"]
    for index in range(3):
        await client.post(
            "/tasks", headers=auth_headers, json={"title": f"任务 {index}", "parent_id": root["id"]}
        )

    tree = (await client.get("/tasks/tree", headers=auth_headers)).json()["data"]
    assert tree["count"] == 4
    node = next(n for n in tree["tree"] if n["id"] == root["id"])
    assert len(node["children"]) == 3


@pytest.mark.asyncio
async def test_knowledge_link_lifecycle(
    client: httpx.AsyncClient, auth_headers: dict, knowledge_base: dict, sample_markdown: bytes
) -> None:
    import io

    upload = await client.post(
        "/documents/upload",
        headers=auth_headers,
        files={"file": ("notes.md", io.BytesIO(sample_markdown), "text/markdown")},
        data={"knowledge_base_id": knowledge_base["id"]},
    )
    document_id = upload.json()["data"]["document"]["id"]
    task = (await client.post("/tasks", headers=auth_headers, json={"title": "学习 GC"})).json()["data"]

    linked = await client.post(
        f"/tasks/{task['id']}/knowledge",
        headers=auth_headers,
        json={"document_id": document_id, "relevance": 0.8},
    )
    assert linked.status_code == 200

    detail = (await client.get(f"/tasks/{task['id']}", headers=auth_headers)).json()["data"]
    assert len(detail["documents"]) == 1
    assert detail["documents"][0]["id"] == document_id

    unlinked = await client.delete(
        f"/tasks/{task['id']}/knowledge/{document_id}", headers=auth_headers
    )
    assert unlinked.json()["data"]["removed"] == 1


@pytest.mark.asyncio
async def test_task_owner_isolation(client: httpx.AsyncClient, auth_headers: dict) -> None:
    task = (await client.post("/tasks", headers=auth_headers, json={"title": "私有任务"})).json()["data"]

    other = await client.post(
        "/auth/register",
        json={
            "email": f"x_{uuid.uuid4().hex[:8]}@aiworkbench.dev",
            "username": f"x_{uuid.uuid4().hex[:8]}",
            "password": "test1234",
            "display_name": "X",
        },
    )
    other_headers = {"Authorization": f"Bearer {other.json()['data']['access_token']}"}

    assert (await client.get(f"/tasks/{task['id']}", headers=other_headers)).status_code == 404
    assert (await client.delete(f"/tasks/{task['id']}", headers=other_headers)).status_code == 404


# --------------------------------------------------------------------------- #
# planner
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_planner_generates_a_grounded_plan(
    client: httpx.AsyncClient, auth_headers: dict, knowledge_base: dict
) -> None:
    response = await client.post(
        "/planner/plan",
        headers=auth_headers,
        json={
            "goal": "在一个月内掌握 Java 后端基础",
            "knowledge_base_ids": [knowledge_base["id"]],
            "horizon_days": 30,
            "create_tasks": False,
        },
    )
    assert response.status_code == 200
    data = response.json()["data"]
    plan = data["plan"]
    assert plan["phases"]
    assert plan["goal"]
    assert plan["estimated_total_minutes"] > 0
    assert data["created"] is None
    for phase in plan["phases"]:
        for task in phase["tasks"]:
            assert task["completion_criteria"]
            assert task["priority"] in {"LOW", "MEDIUM", "HIGH", "URGENT"}


@pytest.mark.asyncio
async def test_planner_persists_a_task_tree(
    client: httpx.AsyncClient, auth_headers: dict
) -> None:
    response = await client.post(
        "/planner/plan",
        headers=auth_headers,
        json={"goal": "学习 RAG 检索增强生成", "horizon_days": 14, "create_tasks": True},
    )
    data = response.json()["data"]
    assert data["created"]["created"] > 0

    tree = (await client.get("/tasks/tree", headers=auth_headers)).json()["data"]
    assert tree["count"] == data["created"]["created"]
    # The plan must be a tree, not a flat list.
    assert any(node["children"] for node in tree["tree"])


@pytest.mark.asyncio
async def test_plan_can_be_applied_from_a_run(
    client: httpx.AsyncClient, auth_headers: dict
) -> None:
    generated = await client.post(
        "/planner/plan",
        headers=auth_headers,
        json={"goal": "学习 Docker 与容器化", "horizon_days": 10, "create_tasks": False},
    )
    run_id = generated.json()["data"]["plan"]["run_id"]

    applied = await client.post(
        "/planner/plan/apply", headers=auth_headers, json={"run_id": run_id}
    )
    assert applied.status_code == 200
    assert applied.json()["data"]["created"] > 0


@pytest.mark.asyncio
async def test_study_plan_is_day_by_day(client: httpx.AsyncClient, auth_headers: dict) -> None:
    response = await client.post(
        "/planner/study-plan",
        headers=auth_headers,
        json={"goal": "掌握 JVM 调优", "horizon_days": 7},
    )
    assert response.status_code == 200
    days = response.json()["data"]["days"]
    assert days
    assert all(day["day"] >= 1 for day in days)


@pytest.mark.asyncio
async def test_planner_rejects_empty_goal(client: httpx.AsyncClient, auth_headers: dict) -> None:
    response = await client.post("/planner/plan", headers=auth_headers, json={"goal": "x"})
    assert response.status_code == 422


# --------------------------------------------------------------------------- #
# agent
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_agent_executes_tools_and_creates_entities(
    client: httpx.AsyncClient, auth_headers: dict, knowledge_base: dict, sample_markdown: bytes
) -> None:
    import io

    upload = await client.post(
        "/documents/upload",
        headers=auth_headers,
        files={"file": ("notes.md", io.BytesIO(sample_markdown), "text/markdown")},
        data={"knowledge_base_id": knowledge_base["id"]},
    )
    from app.infrastructure.database import session_scope
    from app.services.document_service import DocumentService

    async with session_scope() as session:
        await DocumentService(session).ingest(uuid.UUID(upload.json()["data"]["document"]["id"]))

    response = await client.post(
        "/agents/run",
        headers=auth_headers,
        json={
            "goal": "根据知识库里的资料，制定一个 7 天学习计划并创建对应任务",
            "knowledge_base_ids": [knowledge_base["id"]],
            "max_steps": 8,
        },
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["run_id"]
    assert data["steps"], "the agent must actually call tools"
    assert data["created"], "the agent must create real entities"

    tools_used = [step["tool"] for step in data["steps"]]
    assert "knowledge_search" in tools_used, "the agent must ground itself in the knowledge base"
    assert all(step["ok"] for step in data["steps"]), [
        (s["tool"], s["error"]) for s in data["steps"] if not s["ok"]
    ]

    tasks = (await client.get("/tasks", headers=auth_headers)).json()["data"]
    assert tasks["meta"]["total"] > 0

    trace = (await client.get(f"/ai-runs/{data['run_id']}", headers=auth_headers)).json()["data"]
    assert trace["run"]["run_type"] == "AGENT"
    assert trace["tool_calls"]
    assert trace["agent"]["goal"]


@pytest.mark.asyncio
async def test_agent_stops_at_max_steps(client: httpx.AsyncClient, auth_headers: dict) -> None:
    response = await client.post(
        "/agents/run",
        headers=auth_headers,
        json={"goal": "制定学习计划并创建任务", "max_steps": 1},
    )
    data = response.json()["data"]
    assert len(data["steps"]) <= 1


@pytest.mark.asyncio
async def test_agent_rejects_empty_goal(client: httpx.AsyncClient, auth_headers: dict) -> None:
    assert (
        await client.post("/agents/run", headers=auth_headers, json={"goal": "x"})
    ).status_code == 422


@pytest.mark.asyncio
async def test_tool_catalogue_is_exposed(client: httpx.AsyncClient, auth_headers: dict) -> None:
    data = (await client.get("/agents/tools", headers=auth_headers)).json()["data"]
    assert data["count"] == 11
    names = {tool["name"] for tool in data["tools"]}
    assert {"knowledge_search", "calculator", "task_create_batch"} <= names


# --------------------------------------------------------------------------- #
# prompt versioning
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_prompt_templates_are_seeded(client: httpx.AsyncClient, auth_headers: dict) -> None:
    prompts = (await client.get("/prompts", headers=auth_headers)).json()["data"]
    names = {p["name"] for p in prompts}
    assert {
        "rag_answer",
        "document_summary",
        "knowledge_extraction",
        "task_planner",
        "study_plan",
        "interview_generator",
        "note_generation",
        "query_rewrite",
        "agent_system",
    } <= names


@pytest.mark.asyncio
async def test_prompt_version_lifecycle(client: httpx.AsyncClient, auth_headers: dict) -> None:
    detail = (await client.get("/prompts/rag_answer", headers=auth_headers)).json()["data"]
    current = detail["versions"][0]
    assert current["is_current"] is True
    baseline_version = current["version"]

    created = await client.post(
        "/prompts/rag_answer/versions",
        headers=auth_headers,
        json={
            "content": current["content"] + "\n6. 回答结尾必须说明置信度。",
            "change_note": "test: add confidence requirement",
            "activate": True,
        },
    )
    assert created.status_code == 201
    new_version = created.json()["data"]["version"]
    assert new_version == baseline_version + 1

    after = (await client.get("/prompts/rag_answer", headers=auth_headers)).json()["data"]
    assert after["versions"][0]["version"] == new_version
    assert after["versions"][0]["is_current"] is True
    assert after["versions"][1]["is_current"] is False

    rolled_back = await client.post(
        f"/prompts/rag_answer/versions/{baseline_version}/activate", headers=auth_headers
    )
    assert rolled_back.status_code == 200
    assert rolled_back.json()["data"]["version"] == baseline_version

    reset = await client.post("/prompts/rag_answer/reset", headers=auth_headers)
    assert reset.status_code == 200
    assert reset.json()["data"]["version"] == baseline_version + 2


@pytest.mark.asyncio
async def test_identical_prompt_version_is_rejected(
    client: httpx.AsyncClient, auth_headers: dict
) -> None:
    detail = (await client.get("/prompts/rag_answer", headers=auth_headers)).json()["data"]
    content = detail["versions"][0]["content"]
    response = await client.post(
        "/prompts/rag_answer/versions", headers=auth_headers, json={"content": content}
    )
    assert response.status_code == 409


@pytest.mark.asyncio
async def test_prompt_used_by_rag_is_recorded_on_the_run(
    client: httpx.AsyncClient, auth_headers: dict, knowledge_base: dict, sample_markdown: bytes
) -> None:
    import io

    from app.infrastructure.database import session_scope
    from app.services.document_service import DocumentService

    upload = await client.post(
        "/documents/upload",
        headers=auth_headers,
        files={"file": ("notes.md", io.BytesIO(sample_markdown), "text/markdown")},
        data={"knowledge_base_id": knowledge_base["id"]},
    )
    async with session_scope() as session:
        await DocumentService(session).ingest(uuid.UUID(upload.json()["data"]["document"]["id"]))

    # Bump the prompt to v2, then confirm the run records v2 (not v1).
    detail = (await client.get("/prompts/rag_answer", headers=auth_headers)).json()["data"]
    await client.post(
        "/prompts/rag_answer/versions",
        headers=auth_headers,
        json={
            "content": detail["versions"][0]["content"] + "\n7. 额外约束。",
            "change_note": "bump for trace assertion",
            "activate": True,
        },
    )

    rag = (
        await client.post(
            "/chat/rag",
            headers=auth_headers,
            json={"question": "G1 的特点？", "knowledge_base_ids": [knowledge_base["id"]]},
        )
    ).json()["data"]
    trace = (await client.get(f"/ai-runs/{rag['run_id']}", headers=auth_headers)).json()["data"]
    assert trace["run"]["prompt_version"] >= 2
