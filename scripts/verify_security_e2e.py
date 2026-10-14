"""End-to-end check of the round's security fixes against the running API.

Real HTTP, real database, real Redis. Nothing mocked.

    python scripts/verify_security_e2e.py
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000/api/v1"
results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    results.append((label, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}{f' — {detail}' if detail else ''}")


def call(path: str, method: str = "GET", body=None, token: str | None = None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        try:
            return exc.code, json.loads(raw)
        except json.JSONDecodeError:
            return exc.code, raw[:200]


def main() -> int:
    print("=== 1. logout really invalidates the token ===")
    status, body = call("/auth/login", "POST", {"identifier": "demo", "password": "demo1234"})
    if status != 200:
        print(f"  cannot log in ({status}); is the stack up?")
        return 2
    token = body["data"]["access_token"]

    status, _ = call("/auth/me", token=token)
    check("the token works before logout", status == 200, f"status={status}")

    status, body = call("/auth/logout", "POST", token=token)
    revoked = body.get("data", {}).get("token_revoked") if isinstance(body, dict) else None
    check("logout reports the token as revoked", revoked is True, f"token_revoked={revoked}")

    status, _ = call("/auth/me", token=token)
    check(
        "**the same token is refused after logout**",
        status == 401,
        f"status={status} (401 means the server decided, not the client)",
    )

    # A fresh login must still work — a revocation list that locks the owner out is a bug.
    status, body = call("/auth/login", "POST", {"identifier": "demo", "password": "demo1234"})
    check("a new login still works", status == 200, f"status={status}")
    if status == 200:
        fresh = body["data"]["access_token"]
        status, _ = call("/auth/me", token=fresh)
        check("the new token is accepted", status == 200, f"status={status}")

    print("\n=== 2. /metrics needs a token, /health does not ===")
    status, _ = call("/metrics")
    check("/metrics refuses an anonymous caller", status in (401, 403), f"status={status}")
    status, _ = call("/health")
    check("/health is still public", status == 200, f"status={status}")
    status, body = call("/health")
    payload = body.get("data", {}) if isinstance(body, dict) else {}
    check(
        "/health answers only 'alive'",
        set(payload.keys()) <= {"status", "app"},
        f"keys={sorted(payload.keys())}",
    )

    print("\n=== 3. the permission policy is per user ===")
    # The demo account's own policy must not be affected by anything above, and the
    # endpoint must accept and use the caller's identity.
    #
    # **`fresh`, not `token`.** The first version of this check passed the token that was
    # revoked three steps earlier and read the resulting 401 as a failure — the endpoint
    # was right and the test was wrong.
    status, body = call("/agent/permissions", token=fresh)
    check("/agent/permissions is reachable with a valid token", status == 200, f"status={status}")

    # And the revoked one is still refused here too, on a different route.
    status, _ = call("/agent/permissions", token=token)
    check("the revoked token is refused here as well", status == 401, f"status={status}")

    failed = [r for r in results if not r[1]]
    print(f"\n=== {len(results) - len(failed)}/{len(results)} passed ===")
    for label, _ok, detail in failed:
        print(f"  [FAIL] {label} — {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
