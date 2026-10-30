"""Release-freeze regression tests — AI provider configuration over HTTP.

The unit tests prove the store is layered. These prove the *routes* use it: an endpoint
that still called ``resolve()`` without an owner, or ``save(patch)`` without one, would
pass every unit test and still let one account change another's model.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import httpx
import pytest

from app.ai import runtime_config


@pytest.fixture(autouse=True)
def _isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(runtime_config, "CONFIG_PATH", tmp_path / "ai-config.json")
    monkeypatch.setattr(runtime_config, "CONFIG_DIR", tmp_path / "ai-config")
    runtime_config._cache.clear()


async def _register(client: httpx.AsyncClient) -> tuple[str, dict[str, str]]:
    name = f"prov_{uuid.uuid4().hex[:8]}"
    response = await client.post(
        "/auth/register",
        json={
            "email": f"{name}@aiworkbench.dev",
            "username": name,
            "password": "provider1234",
        },
    )
    assert response.status_code == 201, response.text
    token = response.json()["data"]["access_token"]
    return name, {"Authorization": f"Bearer {token}"}


async def test_account_a_cannot_change_account_b(client: httpx.AsyncClient) -> None:
    """The defect, end to end."""
    _, a_headers = await _register(client)
    _, b_headers = await _register(client)

    before_b = (await client.get("/ai-config", headers=b_headers)).json()["data"]["effective"]

    changed = await client.patch(
        "/ai-config",
        headers=a_headers,
        json={
            "provider": "openai_compatible",
            "model": "model-chosen-by-a",
            "base_url": "https://a.example.com/v1",
            "api_key": "sk-fake-owned-by-account-a",
        },
    )
    assert changed.status_code == 200, changed.text

    after_a = (await client.get("/ai-config", headers=a_headers)).json()["data"]["effective"]
    after_b = (await client.get("/ai-config", headers=b_headers)).json()["data"]["effective"]

    assert after_a["model"] == "model-chosen-by-a"
    assert after_a["api_key_set"] is True

    # B is exactly where it was.
    assert after_b["model"] == before_b["model"]
    assert after_b["base_url"] == before_b["base_url"]
    assert after_b["api_key_set"] is False


async def test_a_key_never_appears_in_any_response(client: httpx.AsyncClient) -> None:
    # Marked synthetic so the repo's secret scanner does not report the test fixture.
    secret = "sk-fake-0000000000-not-a-real-credential"
    _, a_headers = await _register(client)

    patched = await client.patch("/ai-config", headers=a_headers, json={"api_key": secret})
    assert patched.status_code == 200
    fetched = await client.get("/ai-config", headers=a_headers)

    assert secret not in patched.text
    assert secret not in fetched.text
    assert fetched.json()["data"]["effective"]["api_key_set"] is True


async def test_reset_only_affects_the_caller(client: httpx.AsyncClient) -> None:
    _, a_headers = await _register(client)
    _, b_headers = await _register(client)

    await client.patch("/ai-config", headers=a_headers, json={"model": "model-for-a"})
    await client.patch("/ai-config", headers=b_headers, json={"model": "model-for-b"})

    assert (await client.delete("/ai-config", headers=a_headers)).status_code == 200

    a_model = (await client.get("/ai-config", headers=a_headers)).json()["data"]["effective"][
        "model"
    ]
    b_model = (await client.get("/ai-config", headers=b_headers)).json()["data"]["effective"][
        "model"
    ]

    assert a_model != "model-for-a"
    assert b_model == "model-for-b"


async def test_malicious_base_url_is_refused(client: httpx.AsyncClient) -> None:
    _, headers = await _register(client)

    for url in ("http://evil.example.com/v1", "file:///etc/passwd", "https://u:p@x.example.com"):
        response = await client.patch("/ai-config", headers=headers, json={"base_url": url})
        assert response.status_code in (400, 422), f"{url} was accepted: {response.text}"


async def test_local_http_endpoint_is_allowed(client: httpx.AsyncClient) -> None:
    """A local Ollama has no certificate; refusing it would break a supported setup."""
    _, headers = await _register(client)
    response = await client.patch(
        "/ai-config", headers=headers, json={"base_url": "http://127.0.0.1:11434"}
    )
    assert response.status_code == 200, response.text


async def test_config_requires_authentication(client: httpx.AsyncClient) -> None:
    assert (await client.get("/ai-config")).status_code == 401
    assert (await client.patch("/ai-config", json={"model": "x"})).status_code == 401
