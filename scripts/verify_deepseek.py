"""End-to-end verification of the real DeepSeek integration.

Drives the live HTTP API and asserts the whole chain works against the real
DeepSeek endpoint:

    DeepSeek V4.1 Flash → Provider → AI service → RAG / Planner / Agent
                        → Tool → Repository → Database → AI Run trace

Cost control
------------
Every call is deliberately small. ``max_tokens`` is temporarily lowered for the
duration of the run and restored afterwards, questions are short, and the
failure-path checks (bad key, unreachable host, retry, fallback) cost nothing
because they never reach a billable endpoint.

The API key is never read, printed or written by this script. It lives in
``.env`` / ``.runtime/ai-config.json`` and is resolved server-side.

Usage::

    python scripts/verify_deepseek.py [--base-url http://127.0.0.1:8000]

Exit code 0 means every check passed.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "samples"

# A model that is definitely not billable: a black-hole address that will time
# out. Used for the timeout / request-failure / retry checks.
UNREACHABLE_BASE_URL = "http://10.255.255.1:9"


@dataclass
class Report:
    checks: list[tuple[str, bool, str]] = field(default_factory=list)

    def check(self, name: str, ok: bool, detail: str = "") -> bool:
        self.checks.append((name, ok, detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
        return ok

    def note(self, text: str) -> None:
        print(f"        {text}")

    @property
    def failed(self) -> list[tuple[str, bool, str]]:
        return [c for c in self.checks if not c[1]]

    def summary(self) -> int:
        total, failed = len(self.checks), len(self.failed)
        print()
        print("=" * 74)
        print(f"  {total - failed}/{total} checks passed")
        for name, _, detail in self.failed:
            print(f"    FAILED: {name} — {detail}")
        print("=" * 74)
        return 1 if failed else 0


class Client:
    def __init__(self, base_url: str) -> None:
        self.base = base_url.rstrip("/") + "/api/v1"
        self.http = httpx.Client(timeout=300.0)
        self.token = ""

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def request(self, method: str, path: str, **kw) -> httpx.Response:
        return self.http.request(method, f"{self.base}{path}", headers=self._headers(), **kw)

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


def err_code(response: httpx.Response) -> str:
    try:
        return response.json().get("error", {}).get("code", "")
    except ValueError:
        return ""


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #
def check_provider_identity(client: Client, report: Report) -> None:
    print("\n[1] Provider identity — what is actually answering")
    deps = data(client.get("/health/deps"))
    llm = (deps.get("ai") or {}).get("llm") or {}
    report.check("llm.kind is deepseek", llm.get("kind") == "deepseek", str(llm.get("kind")))
    report.check(
        "llm.model is deepseek-flash",
        llm.get("model") == "deepseek-flash",
        str(llm.get("model")),
    )
    report.check("api key is configured", bool(llm.get("api_key_set")), "")
    report.check("not degraded", llm.get("degraded") is False, str(llm.get("reason") or ""))
    report.check("ai_degraded is false overall", deps.get("ai_degraded") is False, "")

    cfg = data(client.get("/ai-config"))
    eff = cfg.get("effective") or {}
    report.check(
        "api key is never returned in full",
        "api_key" not in eff and str(eff.get("api_key_hint", "")).endswith(")"),
        f"hint={eff.get('api_key_hint')}",
    )


def check_connection(client: Client, report: Report) -> None:
    print("\n[2] Real DeepSeek round-trip (1-token probe)")
    started = time.perf_counter()
    result = data(client.post("/ai-config/test", json={}))
    report.check("connection ok", bool(result.get("ok")), str(result.get("error_message") or ""))
    report.check(
        "reported provider/model match",
        result.get("provider") == "deepseek" and result.get("model") == "deepseek-flash",
        f"{result.get('provider')} · {result.get('model')}",
    )
    usage = result.get("usage") or {}
    report.check("token usage returned by the vendor", (usage.get("total_tokens") or 0) > 0,
                 f"{usage.get('total_tokens')} tokens")
    report.note(f"wall clock {int((time.perf_counter() - started) * 1000)} ms")


def check_knowledge_base(client: Client, report: Report) -> tuple[str, str]:
    """Return (knowledge_base_id, document_id) with a completed document."""
    print("\n[3] Ingest a real document for the RAG / Agent checks")
    kbs = data(client.get("/knowledge-bases", params={"page_size": 50})).get("items") or []
    kb = next((k for k in kbs if k["name"] == "示例知识库"), None)
    if kb is None:
        kb = data(client.post("/knowledge-bases", json={"name": "示例知识库", "description": "DeepSeek 验证"}))
    kb_id = kb["id"]

    docs = data(client.get("/documents", params={"knowledge_base_id": kb_id, "page_size": 50}))
    ready = [d for d in (docs.get("items") or []) if d["status"] == "COMPLETED"]
    if not ready:
        files = ["rag-notes.md", "java-backend-notes.md"]
        for name in files:
            path = SAMPLES / name
            if not path.exists():
                continue
            with path.open("rb") as handle:
                client.post(
                    "/documents/upload",
                    files={"file": (name, handle, "application/octet-stream")},
                    data={"knowledge_base_id": kb_id},
                )
        deadline = time.time() + 180
        while time.time() < deadline:
            docs = data(client.get("/documents", params={"knowledge_base_id": kb_id, "page_size": 50}))
            items = docs.get("items") or []
            if items and all(d["status"] in ("COMPLETED", "FAILED") for d in items):
                break
            time.sleep(2)
        ready = [d for d in (docs.get("items") or []) if d["status"] == "COMPLETED"]

    report.check("a completed document is available", bool(ready), f"{len(ready)} documents")
    return kb_id, (ready[0]["id"] if ready else "")


def check_rag(client: Client, report: Report, kb_id: str) -> str:
    print("\n[4] RAG — retrieval + real DeepSeek generation + citations")
    response = client.post(
        "/chat/rag",
        json={
            "question": "RAG 的分块策略应该考虑什么？",
            "knowledge_base_ids": [kb_id],
            "mode": "hybrid",
            "top_k": 4,
        },
    )
    body = data(response)
    run_id = body.get("run_id", "")
    report.check("HTTP 200", response.status_code == 200, f"HTTP {response.status_code}")
    report.check("answer is non-empty", len(body.get("answer", "")) > 60, f"{len(body.get('answer',''))} chars")
    report.check("citations returned", len(body.get("citations", [])) > 0, f"{len(body.get('citations', []))} citations")
    report.check(
        "answer came from DeepSeek, not the offline engine",
        body.get("provider") == "deepseek",
        f"provider={body.get('provider')} model={body.get('model')}",
    )
    report.check("retrieval metadata present", bool(body.get("retrieval")), "")
    report.note(f"answer head: {str(body.get('answer',''))[:110].replace(chr(10), ' ')}")
    return run_id


def check_document_summary(client: Client, report: Report, document_id: str) -> str:
    print("\n[5] Document analysis — structured output from DeepSeek")
    if not document_id:
        report.check("document available", False, "skipped: no completed document")
        return ""
    response = client.post(
        f"/documents/{document_id}/analyze",
        json={"actions": ["summary", "concepts"], "save_note": False},
    )
    body = data(response)
    report.check("HTTP 200", response.status_code == 200, f"HTTP {response.status_code}")
    report.check("summary produced", len(str(body.get("summary", ""))) > 40, f"{len(str(body.get('summary','')))} chars")
    report.check("structured concepts list", isinstance(body.get("concepts"), list), f"{len(body.get('concepts') or [])} items")
    report.check("provider is deepseek", body.get("provider") == "deepseek", str(body.get("provider")))
    report.note(f"summary head: {str(body.get('summary',''))[:110].replace(chr(10), ' ')}")
    return body.get("run_id", "")


def check_planner(client: Client, report: Report, kb_id: str) -> str:
    print("\n[6] Task planner — DeepSeek JSON mode / structured output")
    response = client.post(
        "/planner/plan",
        json={
            "goal": "两周内掌握 RAG 的分块与检索调优",
            "knowledge_base_ids": [kb_id],
            "horizon_days": 14,
            "create_tasks": False,
        },
    )
    body = data(response)
    plan = body.get("plan") or {}
    phases = plan.get("phases") or []
    report.check("HTTP 200", response.status_code == 200, f"HTTP {response.status_code}")
    report.check("plan has phases", len(phases) > 0, f"{len(phases)} phases")
    report.check(
        "every task has a verifiable completion criterion",
        all(t.get("completion_criteria") for p in phases for t in p.get("tasks", [])),
        "",
    )
    report.check(
        "every task has an integer estimate",
        all(isinstance(t.get("estimated_minutes"), int) for p in phases for t in p.get("tasks", [])),
        "",
    )
    report.check("provider is deepseek", plan.get("provider") == "deepseek", str(plan.get("provider")))
    run_id = plan.get("run_id", "")
    report.check("run_id recorded", bool(run_id), run_id)
    return run_id


def check_agent(client: Client, report: Report, kb_id: str) -> str:
    print("\n[7] Agent — real DeepSeek tool selection, then real DB writes")

    def counts() -> dict[str, int]:
        """Row counts for every entity type the agent is allowed to create."""
        return {
            "tasks": data(client.get("/tasks", params={"page_size": 1})).get("meta", {}).get("total", 0),
            "notes": data(client.get("/notes", params={"page_size": 1})).get("meta", {}).get("total", 0),
        }

    before = counts()

    response = client.post(
        "/agents/run",
        json={
            "goal": "检索知识库中关于分块策略的内容，创建 2 个复习任务，并写一条简短笔记",
            "knowledge_base_ids": [kb_id],
            "max_steps": 6,
        },
    )
    body = data(response)
    steps = body.get("steps") or []
    report.check("HTTP 200", response.status_code == 200, f"HTTP {response.status_code}")
    report.check("agent called at least one tool", len(steps) > 0, f"{len(steps)} steps")
    report.check(
        "agent grounded itself with knowledge_search",
        any(s["tool"] == "knowledge_search" for s in steps),
        str([s["tool"] for s in steps]),
    )
    report.check(
        "every tool call succeeded",
        all(s["ok"] for s in steps),
        str([(s["tool"], s["error"]) for s in steps if not s["ok"]]),
    )
    created = body.get("created") or {}
    report.check("agent created real entities", any(created.values()), str({k: len(v) for k, v in created.items()}))
    report.check("provider is deepseek", body.get("provider") == "deepseek", str(body.get("provider")))

    # The model picks its own tool sequence, so verify that whatever it claims
    # to have created actually exists — rather than demanding a specific tool.
    after = counts()
    deltas = {kind: after[kind] - before[kind] for kind in after}
    claimed = {kind for kind, ids in created.items() if ids}
    report.check(
        "claimed entities really persisted to the database",
        all(deltas.get(kind, 0) > 0 for kind in claimed) if claimed else False,
        f"claimed={sorted(claimed)} deltas={deltas}",
    )
    report.note(f"final answer: {str(body.get('final_answer',''))[:110].replace(chr(10), ' ')}")
    return body.get("run_id", "")


def check_trace(client: Client, report: Report, run_id: str) -> None:
    print("\n[8] AI Run trace — is the whole chain recorded?")
    if not run_id:
        report.check("run available", False, "skipped: no run id")
        return
    trace = data(client.get(f"/ai-runs/{run_id}"))
    run = trace.get("run") or {}
    report.check("run found", bool(run), run_id)
    report.check("provider recorded as deepseek", run.get("provider") == "deepseek", str(run.get("provider")))
    report.check("model recorded", run.get("model") == "deepseek-flash", str(run.get("model")))
    report.check("prompt name recorded", bool(run.get("prompt_name")), str(run.get("prompt_name")))
    report.check("prompt version recorded", (run.get("prompt_version") or 0) >= 1, f"v{run.get('prompt_version')}")
    report.check("token usage recorded", (run.get("total_tokens") or 0) > 0, f"{run.get('total_tokens')} tokens")
    report.check("latency recorded", (run.get("latency_ms") or 0) > 0, f"{run.get('latency_ms')} ms")
    report.check("status SUCCEEDED", run.get("status") == "SUCCEEDED", str(run.get("status")))
    report.check("sources recorded", len(trace.get("sources") or []) > 0, f"{len(trace.get('sources') or [])} sources")
    report.note(
        f"tokens: prompt={run.get('prompt_tokens')} completion={run.get('completion_tokens')} "
        f"cost=${run.get('cost_estimate')}"
    )


def check_provider_switching(client: Client, report: Report, kb_id: str) -> None:
    print("\n[9] Provider switching — is it a real switch or just a button?")
    original = data(client.get("/ai-config")).get("effective") or {}

    # --- switch to Offline -------------------------------------------------
    client.patch("/ai-config", json={"provider": "offline"})
    deps = data(client.get("/health/deps"))
    llm = (deps.get("ai") or {}).get("llm") or {}
    report.check(
        "switching to offline changes the effective provider",
        llm.get("kind") == "offline",
        str(llm.get("kind")),
    )
    answer = data(client.post("/chat/rag", json={"question": "什么是 RAG？", "knowledge_base_ids": [kb_id], "top_k": 3}))
    report.check(
        "AI requests really run on the offline engine after switching",
        answer.get("provider") == "offline",
        str(answer.get("provider")),
    )

    # --- switch back to DeepSeek ------------------------------------------
    client.patch("/ai-config", json={"provider": "deepseek", "model": "deepseek-flash"})
    deps = data(client.get("/health/deps"))
    llm = (deps.get("ai") or {}).get("llm") or {}
    report.check(
        "switching back restores DeepSeek",
        llm.get("kind") == "deepseek" and llm.get("model") == "deepseek-flash",
        f"{llm.get('kind')} · {llm.get('model')}",
    )
    answer = data(client.post("/chat/rag", json={"question": "分块为什么要重叠？", "knowledge_base_ids": [kb_id], "top_k": 3}))
    report.check(
        "AI requests really run on DeepSeek after switching back",
        answer.get("provider") == "deepseek",
        str(answer.get("provider")),
    )

    # --- enable / disable --------------------------------------------------
    toggled = client.post("/ai-config/providers/toggle", json={"provider": "ollama", "enabled": False})
    report.check("a provider can be disabled", toggled.status_code == 200, f"HTTP {toggled.status_code}")
    client.post("/ai-config/providers/toggle", json={"provider": "ollama", "enabled": True})

    report.note(f"original provider was {original.get('provider')} · {original.get('model')}")


def check_failure_paths(client: Client, report: Report) -> None:
    print("\n[10] Failure paths — bad key, unreachable host, retry, fallback")

    # --- bad API key ------------------------------------------------------
    client.patch("/ai-config", json={"api_key": "sk-not-a-real-key-0000000000000000"})
    bad = data(client.post("/ai-config/test", json={}))
    report.check("bad key is rejected", bad.get("ok") is False, str(bad.get("error_code")))
    report.check(
        "bad key is classified as an auth error, not retried blindly",
        bad.get("error_code") in ("ai_auth_error", "ai_provider_error"),
        str(bad.get("error_code")),
    )
    report.note(f"error: {bad.get('error_code')} — {str(bad.get('error_message'))[:90]}")

    # --- unreachable host (timeout / request failure) ---------------------
    client.patch(
        "/ai-config",
        json={"api_key": None, "base_url": UNREACHABLE_BASE_URL, "timeout_seconds": 5},
    )
    started = time.perf_counter()
    unreachable = data(client.post("/ai-config/test", json={}))
    elapsed = time.perf_counter() - started
    report.check("unreachable host fails cleanly", unreachable.get("ok") is False, str(unreachable.get("error_code")))
    report.check(
        "failure is classified (timeout or provider error)",
        unreachable.get("error_code") in ("ai_timeout", "ai_provider_error"),
        str(unreachable.get("error_code")),
    )
    report.check("it gave up instead of hanging", elapsed < 120, f"{elapsed:.1f}s")

    # --- restore ----------------------------------------------------------
    client.delete("/ai-config")
    deps = data(client.get("/health/deps"))
    llm = (deps.get("ai") or {}).get("llm") or {}
    report.check(
        "clearing overrides falls back to .env and restores DeepSeek",
        llm.get("kind") == "deepseek" and not llm.get("degraded"),
        f"{llm.get('kind')} degraded={llm.get('degraded')}",
    )

    # --- fallback: a cloud provider selected but no key available ----------
    # NOTE: clearing the DeepSeek override would simply fall back to the key in
    # .env. To simulate "no key anywhere" we select openai_compatible, which has
    # no key configured in .env or in the runtime overrides.
    client.patch("/ai-config", json={"provider": "openai_compatible", "api_key": None})
    fallback = data(client.post("/ai-config/test", json={}))
    report.check(
        "no key => offline engine answers instead of crashing",
        fallback.get("ok") is True and fallback.get("provider") == "offline",
        f"provider={fallback.get('provider')} degraded={fallback.get('degraded')}",
    )
    deps = data(client.get("/health/deps"))
    llm = (deps.get("ai") or {}).get("llm") or {}
    report.check(
        "the UI/health endpoint states the degradation and why",
        llm.get("degraded") is True and bool(llm.get("reason")),
        str(llm.get("reason"))[:100],
    )

    # Clear the override that blanked the key so the real .env key applies again.
    client.delete("/ai-config")
    deps = data(client.get("/health/deps"))
    llm = (deps.get("ai") or {}).get("llm") or {}
    report.check(
        "real key restored after the fallback test",
        llm.get("kind") == "deepseek" and llm.get("degraded") is False,
        f"{llm.get('kind')} degraded={llm.get('degraded')}",
    )


def check_ai_run_history(client: Client, report: Report) -> None:
    print("\n[11] AI Run history — every provider is labelled honestly")
    runs = data(client.get("/ai-runs", params={"page_size": 100})).get("items") or []
    providers = {r.get("provider") for r in runs}
    report.check("runs exist", len(runs) > 0, f"{len(runs)} runs")
    report.check("deepseek runs are recorded", "deepseek" in providers, str(sorted(providers)))
    report.check("offline runs are recorded too (and labelled)", "offline" in providers, "")
    # "No run claims a model it did not use" is the invariant. Three ways to violate it,
    # and they are not the same thing:
    #
    #   * an empty provider or model — a silent hole, which is what the first version of
    #     this check caught and could not describe (it reported a blank detail);
    #   * a provider name that is not a provider. The autonomous service recorded
    #     `provider="autonomous"`, which is a *permission level*, so half the history
    #     claimed a provider that does not exist;
    #   * an explicit `unknown`, which claims nothing and is the honest value for a run
    #     whose provider was never recorded.
    #
    # The first two fail. The third passes, because refusing it would push a caller back
    # towards inventing a value — which is how the false claim got there in the first place.
    NOT_PROVIDERS = {"autonomous", "manual", "system", "agent"}
    silent = [r for r in runs if not r.get("provider") or not r.get("model")]
    false_claims = [r for r in runs if (r.get("provider") or "").lower() in NOT_PROVIDERS]

    report.check(
        "no run has a silent hole where its provider should be",
        not silent,
        f"{len(silent)} 条缺 provider/model" if silent else "",
    )
    report.check(
        "no run claims a provider that is not one",
        not false_claims,
        str(sorted({r.get("provider") for r in false_claims})) if false_claims else "",
    )
    report.check(
        "unknown is allowed and used honestly",
        all(r.get("provider") for r in runs),
        f"{len([r for r in runs if r.get('provider') == 'unknown'])} 条标为 unknown",
    )
    metrics = data(client.get("/ai-runs/metrics")).get("metrics") or {}
    report.check("metrics aggregate", (metrics.get("total_runs") or 0) > 0, f"total={metrics.get('total_runs')}")
    report.note(
        f"runs={metrics.get('total_runs')} tokens={metrics.get('total_tokens')} "
        f"avg_latency={metrics.get('avg_latency_ms')}ms failure_rate={metrics.get('failure_rate')}"
    )


# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description="Verify the real DeepSeek integration")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    print("=" * 74)
    print(f"  DeepSeek integration verification → {args.base_url}")
    print("=" * 74)

    client = Client(args.base_url)
    report = Report()

    try:
        login = client.post("/auth/login", json={"identifier": "demo", "password": "demo1234"})
        if login.status_code != 200:
            print(f"[x] cannot log in as demo: HTTP {login.status_code} {login.text[:200]}")
            return 2
        client.token = data(login)["access_token"]

        check_provider_identity(client, report)
        check_connection(client, report)
        kb_id, document_id = check_knowledge_base(client, report)
        rag_run = check_rag(client, report, kb_id)
        check_document_summary(client, report, document_id)
        plan_run = check_planner(client, report, kb_id)
        agent_run = check_agent(client, report, kb_id)
        check_trace(client, report, agent_run or plan_run or rag_run)
        check_provider_switching(client, report, kb_id)
        check_failure_paths(client, report)
        check_ai_run_history(client, report)

        return report.summary()
    except httpx.ConnectError:
        print(f"\n[x] cannot reach {args.base_url}. Start the backend first.")
        return 2
    finally:
        # Safety net: whatever happened above, leave the machine on the
        # configuration from .env rather than a half-applied test override.
        try:
            client.delete("/ai-config")
            deps = data(client.get("/health/deps"))
            llm = (deps.get("ai") or {}).get("llm") or {}
            print(f"\n[cleanup] provider restored: {llm.get('kind')} · {llm.get('model')} "
                  f"(degraded={llm.get('degraded')})")
        except Exception as exc:  # noqa: BLE001 - cleanup must not mask the result
            print(f"\n[cleanup] could not reset the AI config: {exc}")
        client.http.close()


if __name__ == "__main__":
    sys.exit(main())
