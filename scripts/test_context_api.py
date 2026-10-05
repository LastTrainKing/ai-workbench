"""API tests for the personal context endpoints.

Every endpoint gets three cases: the success path, a 404 where an id is involved,
and a validation failure where the payload could be wrong. Testing only the happy
path is how an API ends up returning 500 for a typo.

Run against a live server:

    python scripts/test_context_api.py
"""

from __future__ import annotations

import httpx

BASE = "http://127.0.0.1:8000/api/v1"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, evidence: str = "") -> None:
    results.append((name, ok, evidence))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {evidence}" if evidence else ""))


def main() -> int:
    client = httpx.Client(timeout=30)
    login = client.post(f"{BASE}/auth/login", json={"identifier": "demo", "password": "demo1234"})
    if login.status_code != 200:
        print(f"  login failed: {login.status_code}")
        return 1
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    # ---------------------------------------------------------------- profile --
    r = client.get(f"{BASE}/profile", headers=headers)
    check("GET /profile", r.status_code == 200, f"HTTP {r.status_code}")
    check("  profile has digest field", "digest" in r.json()["data"])

    r = client.patch(
        f"{BASE}/profile",
        headers=headers,
        json={"identity": "计算机专业学生", "stage": "大三", "current_goals": ["完成毕业设计"]},
    )
    check("PATCH /profile", r.status_code == 200, f"HTTP {r.status_code}")
    digest = r.json()["data"].get("digest", "")
    check("  digest reflects the update", "计算机专业学生" in digest, digest[:60])

    r = client.patch(f"{BASE}/profile", headers=headers, json={"identity": "x" * 300})
    check("PATCH /profile rejects over-long value", r.status_code == 422, f"HTTP {r.status_code}")

    # --------------------------------------------------------------- memories --
    r = client.post(
        f"{BASE}/memories",
        headers=headers,
        json={"content": "偏好使用 TypeScript 而不是 JavaScript", "kind": "preference"},
    )
    check("POST /memories", r.status_code == 200, f"HTTP {r.status_code}")
    memory_id = r.json()["data"]["id"]

    r = client.post(f"{BASE}/memories", headers=headers, json={"content": "x", "kind": "nonsense"})
    check("POST /memories rejects bad kind", r.status_code == 422, f"HTTP {r.status_code}")

    r = client.get(f"{BASE}/memories", headers=headers)
    check("GET /memories", r.status_code == 200, f"{r.json()['data']['count']} rows")

    r = client.get(f"{BASE}/memories", headers=headers, params={"kind": "preference"})
    check("GET /memories?kind= filter", r.status_code == 200, f"{r.json()['data']['count']} rows")

    r = client.get(f"{BASE}/memories", headers=headers, params={"kind": "bogus"})
    check("GET /memories rejects bad kind", r.status_code == 422, f"HTTP {r.status_code}")

    r = client.patch(f"{BASE}/memories/{memory_id}", headers=headers, json={"content": "偏好 TypeScript"})
    check("PATCH /memories/{id}", r.status_code == 200, f"HTTP {r.status_code}")

    r = client.patch(
        f"{BASE}/memories/00000000-0000-0000-0000-000000000000", headers=headers, json={"content": "x"}
    )
    check("PATCH /memories/{id} 404", r.status_code == 404, f"HTTP {r.status_code}")

    r = client.get(f"{BASE}/memories/stats", headers=headers)
    check("GET /memories/stats", r.status_code == 200, str(r.json()["data"])[:80])

    # -------------------------------------------------------------- candidates --
    r = client.get(f"{BASE}/memory-candidates", headers=headers)
    check("GET /memory-candidates", r.status_code == 200, f"{r.json()['data']['count']} pending")

    r = client.post(
        f"{BASE}/memory-candidates/00000000-0000-0000-0000-000000000000/approve", headers=headers
    )
    check("approve unknown candidate 404", r.status_code == 404, f"HTTP {r.status_code}")

    r = client.post(
        f"{BASE}/memory-candidates/00000000-0000-0000-0000-000000000000/reject", headers=headers
    )
    check("reject unknown candidate 404", r.status_code == 404, f"HTTP {r.status_code}")

    # ------------------------------------------------------------- preferences --
    r = client.patch(
        f"{BASE}/preferences",
        headers=headers,
        json={"preferences": {"output_style": "concise", "ppt_style": "academic"}},
    )
    check("PATCH /preferences", r.status_code == 200, f"HTTP {r.status_code}")

    r = client.patch(f"{BASE}/preferences", headers=headers, json={"preferences": {}})
    check("PATCH /preferences rejects empty", r.status_code == 422, f"HTTP {r.status_code}")

    r = client.get(f"{BASE}/preferences", headers=headers)
    check("GET /preferences", r.status_code == 200, f"{r.json()['data']['count']} rows")

    # ----------------------------------------------------------------- context --
    r = client.get(f"{BASE}/context/preview", headers=headers)
    check("GET /context/preview", r.status_code == 200, f"HTTP {r.status_code}")
    data = r.json()["data"]
    check("  preview has rendered text", bool(data.get("rendered")), f"{len(data.get('rendered',''))} chars")
    check("  preview reports a token estimate", data.get("token_estimate", 0) > 0, str(data.get("token_estimate")))
    check("  preview lists memory ids", isinstance(data.get("memory_ids"), list), str(len(data.get("memory_ids", []))))

    r = client.get(f"{BASE}/context/preview", headers=headers, params={"budget": 100})
    check("GET /context/preview rejects tiny budget", r.status_code == 422, f"HTTP {r.status_code}")

    r = client.post(
        f"{BASE}/context/preview",
        headers=headers,
        json={
            "knowledge_chunks": [{"document_title": "测试文档", "content": "这是一段知识片段。", "chunk_id": "c1"}],
            "project_digest": "正在做毕业设计",
        },
    )
    check("POST /context/preview with knowledge", r.status_code == 200, f"HTTP {r.status_code}")
    kinds = [b["kind"] for b in r.json()["data"]["blocks"]]
    check("  blocks include knowledge and project", "knowledge" in kinds and "project" in kinds, str(kinds))

    r = client.get(f"{BASE}/context/snapshots", headers=headers)
    check("GET /context/snapshots", r.status_code == 200, f"{r.json()['data']['count']} rows")

    r = client.get(f"{BASE}/context/stats", headers=headers)
    check("GET /context/stats", r.status_code == 200, str(r.json()["data"])[:100])

    # ------------------------------------------------------------------ delete --
    r = client.delete(f"{BASE}/memories/{memory_id}", headers=headers)
    check("DELETE /memories/{id}", r.status_code == 200, f"HTTP {r.status_code}")

    r = client.delete(f"{BASE}/memories/{memory_id}", headers=headers)
    check("DELETE twice returns 404", r.status_code == 404, f"HTTP {r.status_code}")

    # ------------------------------------------------------- auth is required --
    r = client.get(f"{BASE}/memories")
    check("GET /memories requires auth", r.status_code == 401, f"HTTP {r.status_code}")

    failed = [row for row in results if not row[1]]
    print()
    print("=" * 72)
    print(f"  {len(results) - len(failed)}/{len(results)} 通过")
    if failed:
        print("  失败项：")
        for name, _, evidence in failed:
            print(f"    - {name} ({evidence})")
    print("=" * 72)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
