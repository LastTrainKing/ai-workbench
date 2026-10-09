"""Release-freeze regression tests — authentication over HTTP.

These drive the real application: register, log in, log out, and read a protected
endpoint. They exist because the unit tests prove the pieces and these prove the wiring —
a logout that records a revocation but is never checked, or a login that never reaches
the rehash branch, would pass every unit test and still be broken.
"""

from __future__ import annotations

import uuid

import bcrypt
import httpx
import pytest
from sqlalchemy import select

from app.core.security import MAX_PASSWORD_CHARS
from app.models.user import User


def _legacy_hash(password: str) -> str:
    """A hash in the pre-change format: bare bcrypt over the truncated bytes."""
    return bcrypt.hashpw(password.encode("utf-8")[:72], bcrypt.gensalt(rounds=4)).decode("utf-8")


async def test_logout_makes_the_token_stop_working(client: httpx.AsyncClient) -> None:
    """Logout is a server decision, not a message to the client."""
    creds = {
        "email": f"logout_{uuid.uuid4().hex[:8]}@aiworkbench.dev",
        "username": f"logout_{uuid.uuid4().hex[:8]}",
        "password": "logout1234",
    }
    registered = await client.post("/auth/register", json=creds)
    assert registered.status_code == 201, registered.text
    token = registered.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # The token works before logout.
    assert (await client.get("/auth/me", headers=headers)).status_code == 200

    logged_out = await client.post("/auth/logout", headers=headers)
    assert logged_out.status_code == 200, logged_out.text
    assert logged_out.json()["data"]["token_revoked"] is True

    # ... and not after. Same token, same signature, still unexpired.
    after = await client.get("/auth/me", headers=headers)
    assert after.status_code == 401, after.text
    assert after.json()["error"]["code"] == "token_revoked"


async def test_revocation_survives_losing_the_cache(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A Redis restart must not resurrect a logged-out token.

    The cache is emptied after the logout; the answer has to come from the database and
    still be 'revoked'.
    """
    creds = {
        "email": f"cold_{uuid.uuid4().hex[:8]}@aiworkbench.dev",
        "username": f"cold_{uuid.uuid4().hex[:8]}",
        "password": "coldcache1234",
    }
    registered = await client.post("/auth/register", json=creds)
    token = registered.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    assert (await client.post("/auth/logout", headers=headers)).status_code == 200

    async def _cache_miss(_ns: str, _key: str) -> object | None:
        return None

    monkeypatch.setattr("app.core.cache.cache_get", _cache_miss)
    assert (await client.get("/auth/me", headers=headers)).status_code == 401


async def test_never_revoked_token_is_not_locked_out_by_a_cache_miss(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half: failing closed must not lock out valid tokens."""
    creds = {
        "email": f"valid_{uuid.uuid4().hex[:8]}@aiworkbench.dev",
        "username": f"valid_{uuid.uuid4().hex[:8]}",
        "password": "stillvalid1234",
    }
    token = (await client.post("/auth/register", json=creds)).json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    async def _cache_miss(_ns: str, _key: str) -> object | None:
        return None

    monkeypatch.setattr("app.core.cache.cache_get", _cache_miss)
    assert (await client.get("/auth/me", headers=headers)).status_code == 200


async def test_legacy_hash_logs_in_and_is_upgraded(client: httpx.AsyncClient) -> None:
    """An account created before the scheme change keeps working, and migrates."""
    from app.infrastructure.database import session_scope

    username = f"legacy_{uuid.uuid4().hex[:8]}"
    password = "legacypass1234"
    async with session_scope() as session:
        user = User(
            email=f"{username}@aiworkbench.dev",
            username=username,
            display_name="Legacy",
            hashed_password=_legacy_hash(password),
        )
        session.add(user)
        await session.flush()
        user_id = user.id
        original = user.hashed_password
    assert not original.startswith("bcrypt_sha256$")

    response = await client.post("/auth/login", json={"identifier": username, "password": password})
    assert response.status_code == 200, response.text

    async with session_scope() as session:
        refreshed = (await session.execute(select(User).where(User.id == user_id))).scalar_one()
        upgraded = refreshed.hashed_password
    assert upgraded.startswith("bcrypt_sha256$")
    assert upgraded != original

    # And the new hash still accepts the same password.
    again = await client.post("/auth/login", json={"identifier": username, "password": password})
    assert again.status_code == 200


async def test_multibyte_password_registers_and_logs_in(client: httpx.AsyncClient) -> None:
    username = f"multi_{uuid.uuid4().hex[:8]}"
    password = "中文密码测试🔐123"
    registered = await client.post(
        "/auth/register",
        json={
            "email": f"{username}@aiworkbench.dev",
            "username": username,
            "password": password,
        },
    )
    assert registered.status_code == 201, registered.text
    login = await client.post("/auth/login", json={"identifier": username, "password": password})
    assert login.status_code == 200, login.text
    wrong = await client.post(
        "/auth/login", json={"identifier": username, "password": "中文密码测试🔐124"}
    )
    assert wrong.status_code == 401


async def test_over_length_password_is_rejected_not_truncated(client: httpx.AsyncClient) -> None:
    username = f"toolong_{uuid.uuid4().hex[:8]}"
    over = "a1" * (MAX_PASSWORD_CHARS // 2 + 1)
    assert len(over) > MAX_PASSWORD_CHARS
    response = await client.post(
        "/auth/register",
        json={"email": f"{username}@aiworkbench.dev", "username": username, "password": over},
    )
    assert response.status_code == 422, response.text


async def test_blank_password_is_rejected(client: httpx.AsyncClient) -> None:
    username = f"blank_{uuid.uuid4().hex[:8]}"
    response = await client.post(
        "/auth/register",
        json={
            "email": f"{username}@aiworkbench.dev",
            "username": username,
            "password": " " * 12,
        },
    )
    assert response.status_code == 422, response.text


async def test_two_accounts_do_not_share_credentials(client: httpx.AsyncClient) -> None:
    """A/B check that password verification is per account, not per password."""
    a_name = f"ab_a_{uuid.uuid4().hex[:8]}"
    b_name = f"ab_b_{uuid.uuid4().hex[:8]}"
    for name, password in ((a_name, "alicepass1234"), (b_name, "bobpass1234")):
        r = await client.post(
            "/auth/register",
            json={"email": f"{name}@aiworkbench.dev", "username": name, "password": password},
        )
        assert r.status_code == 201, r.text

    # A's password must not open B's account.
    cross = await client.post(
        "/auth/login", json={"identifier": b_name, "password": "alicepass1234"}
    )
    assert cross.status_code == 401
