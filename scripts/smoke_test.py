"""End-to-end smoke test.

Drives the real HTTP API against a running backend and asserts the whole
product loop works: upload → parse → chunk → embed → retrieve → answer →
plan → agent → trace.  Also exercises the failure paths.

Usage::

    python scripts/smoke_test.py [--base-url http://127.0.0.1:8000]

Exit code 0 means every check passed.
"""

from __future__ import annotations

import argparse
import io
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"


@dataclass
class Report:
    checks: list[tuple[str, bool, str]] = field(default_factory=list)

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append((name, ok, detail))
        icon = "PASS" if ok else "FAIL"
        print(f"  [{icon}] {name}" + (f" — {detail}" if detail else ""))
        return ok

    @property
    def failed(self) -> list[tuple[str, bool, str]]:
        return [c for c in self.checks if not c[1]]

    def summary(self) -> int:
        total = len(self.checks)
        failed = len(self.failed)
        print()
        print("=" * 72)
        print(f"  {total - failed}/{total} checks passed")
        if failed:
            print("  FAILED:")
            for name, _, detail in self.failed:
                print(f"    - {name}: {detail}")
        print("=" * 72)
        return 1 if failed else 0


class Client:
    def __init__(self, base_url: str) -> None:
        self.base = base_url.rstrip("/") + "/api/v1"
        self.http = httpx.Client(timeout=180.0)
        self.token = ""

    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def request(self, method: str, path: str, **kwargs) -> httpx.Response:
        return self.http.request(
            method, f"{self.base}{path}", headers=self.headers(), **kwargs
        )

    def get(self, path: str, **kw) -> httpx.Response:
        return self.request("GET", path, **kw)

    def post(self, path: str, **kw) -> httpx.Response:
        return self.request("POST", path, **kw)

    def patch(self, path: str, **kw) -> httpx.Response:
        return self.request("PATCH", path, **kw)

    def delete(self, path: str, **kw) -> httpx.Response:
        return self.request("DELETE", path, **kw)


def data(response: httpx.Response) -> dict:
    try:
        payload = response.json()
    except ValueError:
        return {}
    return payload.get("data") or {}


def error_code(response: httpx.Response) -> str:
    try:
        return response.json().get("error", {}).get("code", "")
    except ValueError:
        return ""


def run(base_url: str) -> int:
    report = Report()
    client = Client(base_url)
    stamp = str(int(time.time()))

    # ---------------------------------------------------------------- health --
    print("\n[1] health & dependencies")
    health = client.get("/health")
    report.check("GET /health", health.status_code == 200, f"HTTP {health.status_code}")
    deps = client.get("/health/deps")
    dep_data = data(deps)
    report.check(
        "PostgreSQL reachable", bool(dep_data.get("postgres", {}).get("ok")), ""
    )
    report.check("Redis reachable", bool(dep_data.get("redis", {}).get("ok")), "")
    report.check("Qdrant reachable", bool(dep_data.get("qdrant", {}).get("ok")), "")
    ai_kind = dep_data.get("ai", {}).get("llm", {}).get("kind", "?")
    print(f"      AI provider in use: {ai_kind}")

    # ------------------------------------------------------------------ auth --
    print("\n[2] authentication")
    login = client.post("/auth/login", json={"identifier": "demo", "password": "demo1234"})
    if login.status_code != 200:
        register = client.post(
            "/auth/register",
            json={
                "email": f"demo{stamp}@aiworkbench.dev",
                "username": f"demo{stamp}",
                "password": "demo1234",
                "display_name": "Demo",
            },
        )
        report.check("register fallback", register.status_code == 201, f"HTTP {register.status_code}")
        client.token = data(register).get("access_token", "")
    else:
        client.token = data(login).get("access_token", "")
    report.check("obtain access token", bool(client.token), "token acquired" if client.token else "no token")

    me = client.get("/auth/me")
    report.check("GET /auth/me", me.status_code == 200, data(me).get("username", ""))

    anon = httpx.get(f"{base_url.rstrip('/')}/api/v1/auth/me", timeout=20)
    report.check(
        "unauthenticated request is rejected (401)",
        anon.status_code == 401 and error_code(anon) == "authentication_error",
        f"HTTP {anon.status_code} code={error_code(anon)}",
    )
    bad = client.post("/auth/login", json={"identifier": "demo", "password": "wrong-password"})
    report.check("wrong password is rejected (401)", bad.status_code == 401, f"HTTP {bad.status_code}")

    # -------------------------------------------------------------- project ---
    print("\n[3] project & knowledge base")
    project = client.post(
        "/projects",
        json={"name": f"毕业设计 {stamp}", "description": "端到端冒烟测试项目", "color": "#6366f1"},
    )
    report.check("POST /projects", project.status_code == 201, f"HTTP {project.status_code}")
    project_id = data(project).get("id", "")

    kb = client.post(
        "/knowledge-bases",
        json={
            "name": f"冒烟知识库 {stamp}",
            "description": "端到端测试用知识库",
            "project_id": project_id or None,
        },
    )
    report.check("POST /knowledge-bases", kb.status_code == 201, f"HTTP {kb.status_code}")
    kb_id = data(kb).get("id", "")
    if not kb_id:
        report.check("knowledge base id present", False, "cannot continue without a KB")
        return report.summary()

    dup_kb = client.post("/knowledge-bases", json={"name": f"冒烟知识库 {stamp}"})
    report.check(
        "duplicate knowledge base name is rejected (409)",
        dup_kb.status_code == 409,
        f"HTTP {dup_kb.status_code}",
    )

    # --------------------------------------------------------------- upload ---
    print("\n[4] document ingestion (Markdown / TXT / PDF)")
    uploaded: list[dict] = []
    for name in (
        "java-backend-notes.md",
        "rag-notes.md",
        "software-engineering-checklist.txt",
        "distributed-systems.pdf",
    ):
        path = SAMPLES / name
        if not path.exists():
            report.check(f"sample {name} exists", False, "missing fixture")
            continue
        with path.open("rb") as handle:
            response = client.post(
                "/documents/upload",
                files={"file": (name, handle, "application/octet-stream")},
                data={"knowledge_base_id": kb_id},
            )
        ok = response.status_code == 202
        report.check(f"upload {name}", ok, f"HTTP {response.status_code}")
        if ok:
            uploaded.append(data(response)["document"])

    # duplicate detection
    first = SAMPLES / "java-backend-notes.md"
    if first.exists():
        with first.open("rb") as handle:
            dup = client.post(
                "/documents/upload",
                files={"file": ("java-backend-notes.md", handle, "application/octet-stream")},
                data={"knowledge_base_id": kb_id},
            )
        report.check(
            "duplicate upload is detected",
            dup.status_code == 202 and bool(data(dup).get("duplicate")),
            f"duplicate={data(dup).get('duplicate')}",
        )

    # unsupported type
    bad_file = client.post(
        "/documents/upload",
        files={"file": ("malware.exe", io.BytesIO(b"MZ\x90\x00"), "application/octet-stream")},
        data={"knowledge_base_id": kb_id},
    )
    report.check(
        "unsupported file type is rejected (415)",
        bad_file.status_code == 415,
        f"HTTP {bad_file.status_code} code={error_code(bad_file)}",
    )

    # oversized file
    big = io.BytesIO(b"a" * (26 * 1024 * 1024))
    too_big = client.post(
        "/documents/upload",
        files={"file": ("huge.txt", big, "text/plain")},
        data={"knowledge_base_id": kb_id},
    )
    report.check(
        "oversized upload is rejected (413)",
        too_big.status_code == 413,
        f"HTTP {too_big.status_code} code={error_code(too_big)}",
    )

    # ------------------------------------------------------ wait for pipeline --
    print("\n[5] background ingestion pipeline")
    deadline = time.time() + 240
    final: dict[str, dict] = {}
    while time.time() < deadline:
        statuses = {}
        for document in uploaded:
            response = client.get(f"/documents/{document['id']}")
            body = data(response)
            statuses[document["id"]] = body
            final[document["id"]] = body
        states = {b.get("status") for b in statuses.values()}
        if states and states <= {"COMPLETED", "FAILED"}:
            break
        time.sleep(2)

    for document in uploaded:
        body = final.get(document["id"], {})
        name = document["title"]
        status = body.get("status")
        chunks = body.get("chunk_count", 0)
        embedded = body.get("embedded_chunk_count", 0)
        report.check(
            f"ingest {name}",
            status == "COMPLETED" and chunks > 0 and embedded == chunks,
            f"status={status} chunks={chunks} embedded={embedded} err={body.get('error_message', '')[:80]}",
        )

    # ---------------------------------------------------------------- search --
    print("\n[6] keyword / semantic / hybrid search")
    for mode in ("keyword", "semantic", "hybrid"):
        response = client.get(
            "/search",
            params={"q": "垃圾回收 收集器", "mode": mode, "knowledge_base_id": kb_id, "limit": 5},
        )
        body = data(response)
        results = body.get("results", [])
        report.check(
            f"{mode} search returns hits",
            response.status_code == 200 and len(results) > 0,
            f"hits={len(results)} took={body.get('took_ms')}ms",
        )

    hybrid = data(
        client.get(
            "/search", params={"q": "混合检索 权重", "mode": "hybrid", "knowledge_base_id": kb_id}
        )
    )
    hits = hybrid.get("results", [])
    report.check(
        "hybrid search returns both score components",
        bool(hits) and all("vector_score" in h and "keyword_score" in h for h in hits),
        f"hits={len(hits)}",
    )
    cached = data(
        client.get(
            "/search", params={"q": "混合检索 权重", "mode": "hybrid", "knowledge_base_id": kb_id}
        )
    )
    report.check("second identical search is served from cache", bool(cached.get("cached")), "")

    # ------------------------------------------------------------------- RAG --
    print("\n[7] RAG question answering with citations")
    rag = client.post(
        "/chat/rag",
        json={
            "question": "根据我的资料，JVM 有哪些垃圾收集器，分别适合什么场景？",
            "knowledge_base_ids": [kb_id],
            "mode": "hybrid",
        },
    )
    rag_body = data(rag)
    report.check("POST /chat/rag", rag.status_code == 200, f"HTTP {rag.status_code}")
    report.check("answer is non-empty", len(rag_body.get("answer", "")) > 40, f"{len(rag_body.get('answer',''))} chars")
    report.check("citations returned", len(rag_body.get("citations", [])) > 0, f"{len(rag_body.get('citations', []))} citations")
    report.check(
        "every citation points at a real chunk",
        all(c.get("chunk_id") and c.get("document_title") for c in rag_body.get("citations", [])),
        "",
    )
    report.check("run_id recorded", bool(rag_body.get("run_id")), rag_body.get("run_id", ""))
    rag_run_id = rag_body.get("run_id", "")

    no_kb = client.post(
        "/chat/rag",
        json={
            "question": "hi",
            "knowledge_base_ids": ["00000000-0000-0000-0000-0000000000ff"],
        },
    )
    report.check(
        "RAG against an unknown knowledge base is rejected (404)",
        no_kb.status_code == 404,
        f"HTTP {no_kb.status_code}",
    )

    # --------------------------------------------------------------- planner --
    print("\n[8] AI task planner")
    plan = client.post(
        "/planner/plan",
        json={
            "goal": "在一个月内掌握 Java 后端基础，包括 JVM、集合与并发",
            "knowledge_base_ids": [kb_id],
            "project_id": project_id or None,
            "horizon_days": 30,
            "create_tasks": True,
        },
    )
    plan_body = data(plan)
    plan_payload = plan_body.get("plan", {})
    phases = plan_payload.get("phases", [])
    created = plan_body.get("created") or {}
    report.check("POST /planner/plan", plan.status_code == 200, f"HTTP {plan.status_code}")
    report.check("plan has phases", len(phases) > 0, f"{len(phases)} phases")
    report.check(
        "every task has a completion criterion",
        all(
            t.get("completion_criteria")
            for phase in phases
            for t in phase.get("tasks", [])
        ),
        "",
    )
    report.check("tasks persisted", created.get("created", 0) > 0, f"{created.get('created')} tasks")

    tree = data(client.get("/tasks/tree", params={"project_id": project_id}))
    report.check("task tree is hierarchical", len(tree.get("tree", [])) > 0, f"{tree.get('count')} nodes")

    # ----------------------------------------------------------------- agent --
    print("\n[9] tool-calling agent")
    agent = client.post(
        "/agents/run",
        json={
            "goal": "根据知识库里的 RAG 资料，制定一个 7 天学习计划并创建对应任务",
            "project_id": project_id or None,
            "knowledge_base_ids": [kb_id],
            "max_steps": 8,
        },
    )
    agent_body = data(agent)
    steps = agent_body.get("steps", [])
    report.check("POST /agents/run", agent.status_code == 200, f"HTTP {agent.status_code}")
    report.check("agent executed at least one tool", len(steps) > 0, f"{len(steps)} steps")
    report.check(
        "agent produced a final answer",
        len(agent_body.get("final_answer", "")) > 20,
        agent_body.get("stopped_reason", ""),
    )
    report.check(
        "agent created real entities",
        any(agent_body.get("created", {}).values()),
        str({k: len(v) for k, v in agent_body.get("created", {}).items()}),
    )
    tools = data(client.get("/agents/tools"))
    report.check("tool catalogue exposed", len(tools.get("tools", [])) >= 6, f"{tools.get('count')} tools")

    # --------------------------------------------------------------- analysis --
    print("\n[10] AI document analysis")
    if uploaded:
        target = uploaded[0]["id"]
        analysis = client.post(
            f"/documents/{target}/analyze",
            json={"actions": ["summary", "concepts", "todos", "cards"], "save_note": True},
        )
        analysis_body = data(analysis)
        report.check("POST /documents/{id}/analyze", analysis.status_code == 200, f"HTTP {analysis.status_code}")
        report.check("summary produced", len(str(analysis_body.get("summary", ""))) > 30, "")
        report.check("note persisted", len(analysis_body.get("note_ids", [])) > 0, "")

    # -------------------------------------------------------------- ai runs ---
    print("\n[11] AI execution trace")
    runs = data(client.get("/ai-runs", params={"page_size": 50}))
    items = runs.get("items", [])
    report.check("GET /ai-runs", len(items) > 0, f"{runs.get('meta', {}).get('total')} runs recorded")
    report.check(
        "runs carry prompt version + token usage",
        all(r.get("prompt_name") is not None and r.get("total_tokens", 0) >= 0 for r in items),
        "",
    )
    if rag_run_id:
        trace = data(client.get(f"/ai-runs/{rag_run_id}"))
        report.check("trace exposes retrieval metadata", bool(trace.get("retrieval")), "")
        report.check("trace exposes cited sources", len(trace.get("sources", [])) > 0, f"{len(trace.get('sources', []))} sources")
        report.check(
            "trace exposes prompt name and version",
            bool(trace.get("run", {}).get("prompt_name")),
            f"{trace.get('run', {}).get('prompt_name')} v{trace.get('run', {}).get('prompt_version')}",
        )
    metrics = data(client.get("/ai-runs/metrics"))
    report.check("AI metrics aggregate", metrics.get("metrics", {}).get("total_runs", 0) > 0, "")

    # --------------------------------------------------------------- prompts --
    print("\n[12] prompt versioning")
    prompts = data(client.get("/prompts"))
    report.check("prompt templates listed", len(prompts) >= 8, f"{len(prompts)} templates")
    detail = data(client.get("/prompts/rag_answer"))
    report.check("prompt detail has versions", len(detail.get("versions", [])) > 0, f"v{detail.get('versions',[{}])[0].get('version')}")
    new_version = client.post(
        "/prompts/rag_answer/versions",
        json={
            "content": detail["versions"][0]["content"] + "\n6. 回答结尾必须给出置信度说明。",
            "change_note": "smoke test: add confidence requirement",
            "activate": True,
        },
    )
    report.check("new prompt version created", new_version.status_code == 201, f"HTTP {new_version.status_code}")
    rolled_back = client.post("/prompts/rag_answer/reset")
    report.check("prompt reset to built-in", rolled_back.status_code == 200, f"HTTP {rolled_back.status_code}")

    # ------------------------------------------------------------- dashboard --
    print("\n[13] dashboard, graph and statistics")
    dashboard = data(client.get("/stats/dashboard"))
    report.check("dashboard returns task stats", "tasks" in dashboard, f"open={dashboard.get('tasks', {}).get('open')}")
    report.check(
        "dashboard knowledge numbers come from the database",
        dashboard.get("knowledge", {}).get("documents", 0) >= len(uploaded),
        f"documents={dashboard.get('knowledge', {}).get('documents')} chunks={dashboard.get('knowledge', {}).get('chunks')}",
    )
    report.check("dashboard AI stats", dashboard.get("ai", {}).get("total_runs", 0) > 0, f"runs={dashboard.get('ai', {}).get('total_runs')}")
    report.check("dashboard activity feed", len(dashboard.get("recent", {}).get("activity", [])) > 0, "")
    report.check("dashboard provider status", "llm" in dashboard.get("providers", {}), "")

    graph = data(client.get("/graph", params={"project_id": project_id}))
    node_types = {n["type"] for n in graph.get("nodes", [])}
    report.check("graph returns nodes and edges", len(graph.get("nodes", [])) > 0, f"{len(graph.get('nodes', []))} nodes / {len(graph.get('edges', []))} edges")
    report.check(
        "graph links knowledge to tasks",
        {"document", "task"} <= node_types,
        f"types={sorted(node_types)}",
    )

    # --------------------------------------------------------- error paths ---
    print("\n[14] error handling and validation")
    missing = client.get("/documents/00000000-0000-0000-0000-000000000000")
    report.check("missing resource returns 404", missing.status_code == 404, f"HTTP {missing.status_code}")
    invalid_uuid = client.get("/documents/not-a-uuid")
    report.check("invalid uuid returns 422", invalid_uuid.status_code == 422, f"HTTP {invalid_uuid.status_code}")
    bad_body = client.post("/tasks", json={"title": ""})
    report.check("empty task title rejected (422)", bad_body.status_code == 422, f"HTTP {bad_body.status_code}")
    bad_mode = client.get("/search", params={"q": "x", "mode": "bogus"})
    report.check("invalid enum rejected (422)", bad_mode.status_code == 422, f"HTTP {bad_mode.status_code}")

    # task workflow
    print("\n[15] task workflow, dependencies and knowledge links")
    t1 = data(client.post("/tasks", json={"title": "准备 JVM 学习资料", "project_id": project_id or None}))
    t2 = data(
        client.post(
            "/tasks",
            json={"title": "阅读 GC 章节", "project_id": project_id or None},
        )
    )
    dep = client.post(
        f"/tasks/{t2['id']}/dependencies", json={"depends_on_task_id": t1["id"]}
    )
    report.check("dependency created", dep.status_code == 200, f"HTTP {dep.status_code}")
    blocked = client.post(f"/tasks/{t2['id']}/complete", json={})
    report.check(
        "completing a blocked task is rejected (409)",
        blocked.status_code == 409,
        f"HTTP {blocked.status_code}",
    )
    cycle = client.post(f"/tasks/{t1['id']}/dependencies", json={"depends_on_task_id": t2["id"]})
    report.check(
        "circular dependency is rejected (409)",
        cycle.status_code == 409,
        f"HTTP {cycle.status_code}",
    )
    client.post(f"/tasks/{t1['id']}/complete", json={})
    unblocked = client.post(f"/tasks/{t2['id']}/complete", json={})
    report.check("task completes after its dependency", unblocked.status_code == 200, f"HTTP {unblocked.status_code}")

    if uploaded:
        link = client.post(
            f"/tasks/{t1['id']}/knowledge", json={"document_id": uploaded[0]["id"]}
        )
        report.check("knowledge linked to task", link.status_code == 200, f"HTTP {link.status_code}")

    # --------------------------------------------------------------- cleanup --
    print("\n[16] cascade cleanup")
    deleted = client.delete(f"/knowledge-bases/{kb_id}")
    report.check("knowledge base deleted", deleted.status_code == 200, f"HTTP {deleted.status_code}")
    after = data(client.get(f"/knowledge-bases/{kb_id}"))
    report.check("deleted knowledge base is gone", not after, "")
    gone = client.get("/documents", params={"knowledge_base_id": kb_id})
    report.check(
        "documents cascade with the knowledge base",
        data(gone).get("meta", {}).get("total", -1) == 0,
        f"remaining={data(gone).get('meta', {}).get('total')}",
    )

    return report.summary()


def main() -> int:
    parser = argparse.ArgumentParser(description="AI Workbench end-to-end smoke test")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    print("=" * 72)
    print(f"  AI Workbench smoke test -> {args.base_url}")
    print("=" * 72)
    try:
        return run(args.base_url)
    except httpx.ConnectError:
        print(f"\n[x] cannot reach {args.base_url}. Start the backend first:")
        print("      powershell -ExecutionPolicy Bypass -File scripts\\infra-start.ps1")
        print("      python -m app.cli serve")
        return 2


if __name__ == "__main__":
    sys.exit(main())
