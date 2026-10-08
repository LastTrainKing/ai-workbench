"""「全选 → 一键清空」必须清空整个数据集，不是第一页。

真实 HTTP、真实数据库。

    python scripts/verify_bulk_delete.py
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000/api/v1"
TARGET = 60  # more than the list endpoint's default page of 50 — that is the whole point
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
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")[:200]


def main() -> int:
    status, body = call("/auth/login", "POST", {"identifier": "demo", "password": "demo1234"})
    if status != 200:
        print(f"  cannot log in ({status})")
        return 2
    token = body["data"]["access_token"]

    # Start from nothing, using the same "all" the fix introduces.
    call("/conversations/delete", "POST", {"ids": [], "all": True}, token)

    print(f"=== create {TARGET} conversations ===")
    for index in range(TARGET):
        call("/conversations", "POST", {"title": f"BULK-{index:03d}"}, token)

    status, body = call("/conversations?limit=200", token=token)
    total = len(body["data"]["conversations"]) if status == 200 else -1
    check(f"{TARGET} conversations exist", total == TARGET, f"count={total}")

    # The client's view: what 全选 can actually select.
    status, body = call("/conversations", token=token)
    first_page = len(body["data"]["conversations"]) if status == 200 else -1
    check(
        "the default page really is smaller than the set",
        first_page < TARGET,
        f"page={first_page}, total={total} — this is the gap the fix closes",
    )

    print("\n=== 全选 → 一键清空 ===")
    # What the client now sends when everything it loaded is selected: `all`.
    status, body = call("/conversations/delete", "POST", {"ids": [], "all": True}, token)
    deleted = body["data"]["conversations"] if status == 200 else -1
    check("the delete reports the whole set", deleted == TARGET, f"deleted={deleted}")

    status, body = call("/conversations?limit=200", token=token)
    remaining = len(body["data"]["conversations"]) if status == 200 else -1
    check("**nothing is left**", remaining == 0, f"remaining={remaining}")

    print("\n=== the old behaviour, for contrast ===")
    # What the old client did: send only the ids it had loaded.
    for index in range(TARGET):
        call("/conversations", "POST", {"title": f"OLD-{index:03d}"}, token)
    status, body = call("/conversations", token=token)
    loaded = [item["id"] for item in body["data"]["conversations"]]
    status, body = call("/conversations/delete", "POST", {"ids": loaded}, token)
    after = call("/conversations?limit=200", token=token)[1]["data"]["conversations"]
    check(
        "deleting only the loaded page leaves the rest behind — which is why `all` exists",
        len(after) == TARGET - len(loaded),
        f"deleted {len(loaded)} of {TARGET}, {len(after)} left",
    )

    # Leave the account empty.
    call("/conversations/delete", "POST", {"ids": [], "all": True}, token)

    failed = [r for r in results if not r[1]]
    print(f"\n=== {len(results) - len(failed)}/{len(results)} passed ===")
    for label, _ok, detail in failed:
        print(f"  [FAIL] {label} — {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
