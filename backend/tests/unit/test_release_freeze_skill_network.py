"""Release-freeze regression tests — the skill-discovery outbound gate.

The confirmed defect: ``_authorise`` returned early when ``permissions`` was None, and
``POST /skills/discover`` — the only caller — never passed a policy. The domain check on
that path therefore never ran. The GitHub URLs were hard-coded so it was not exploitable
as written, but the gate itself was empty.

The second half is redirects. ``fetch_skill_file`` used ``follow_redirects=True``, which
authorises the first URL and then follows wherever it is sent. An allowed host could hand
the client to ``127.0.0.1`` and the only check that ran would be the one that passed.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.ai.skills import discovery
from app.ai.skills.discovery import _authorise, _get_checked
from app.ai.tools.permissions import PermissionManager
from app.ai.tools.policy_store import build_policy
from app.core.errors import PermissionDeniedError


def _manager() -> PermissionManager:
    """The real policy with default settings — the thing that actually runs in production."""
    return PermissionManager(build_policy(None))


# ------------------------------------------------------------------ the empty gate --


async def test_missing_policy_is_denied() -> None:
    """The defect itself: `permissions is None` used to mean 'go ahead'."""
    with pytest.raises(PermissionDeniedError):
        await _authorise(None, "https://api.github.com/search/repositories")


async def test_allow_listed_host_passes() -> None:
    await _authorise(_manager(), "https://api.github.com/search/repositories")


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1:6333/collections",  # the app's own unauthenticated vector store
        "http://localhost:8000/health",
        "http://[::1]:8000/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",  # cloud metadata
        "http://2130706433/",  # decimal 127.0.0.1
        "https://evilgithub.com/x",  # suffix spoof of an allow-listed name
    ],
)
async def test_dangerous_targets_are_refused(url: str) -> None:
    with pytest.raises(PermissionDeniedError):
        await _authorise(_manager(), url)


async def test_search_refuses_before_making_a_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """No policy means no request at all — not a request that happens to be safe."""

    class _ExplodingClient:
        def __init__(self, **_kwargs: Any) -> None:
            raise AssertionError("a request was attempted without a permission policy")

    monkeypatch.setattr(discovery.httpx, "AsyncClient", _ExplodingClient)

    with pytest.raises(PermissionDeniedError):
        await discovery.search_github("topic:agent-skills", permissions=None)


# ------------------------------------------------------------------- redirect hops --


class _FakeResponse:
    def __init__(self, status_code: int, *, location: str = "", payload: Any = None) -> None:
        self.status_code = status_code
        self.headers = {"location": location} if location else {}
        self._payload = payload

    def json(self) -> Any:
        return self._payload


class _FakeClient:
    """Returns a canned response per URL and records what was requested."""

    def __init__(self, responses: dict[str, _FakeResponse]) -> None:
        self.responses = responses
        self.requested: list[str] = []

    async def get(self, url: str, **_kwargs: Any) -> _FakeResponse:
        self.requested.append(url)
        if url not in self.responses:
            raise AssertionError(f"unexpected request to {url}")
        return self.responses[url]


async def test_redirect_to_loopback_is_refused() -> None:
    """The defect: `follow_redirects=True` would have fetched this."""
    client = _FakeClient(
        {
            "https://api.github.com/repos/x/y/contents/SKILL.md": _FakeResponse(
                302, location="http://127.0.0.1:6333/collections"
            ),
        }
    )
    with pytest.raises(PermissionDeniedError):
        await _get_checked(
            client,  # type: ignore[arg-type]
            "https://api.github.com/repos/x/y/contents/SKILL.md",
            permissions=_manager(),
            headers={},
        )
    # The dangerous hop was never requested.
    assert client.requested == ["https://api.github.com/repos/x/y/contents/SKILL.md"]


async def test_redirect_to_metadata_is_refused() -> None:
    client = _FakeClient(
        {
            "https://api.github.com/a": _FakeResponse(
                301, location="http://169.254.169.254/latest/meta-data/"
            ),
        }
    )
    with pytest.raises(PermissionDeniedError):
        await _get_checked(
            client,
            "https://api.github.com/a",
            permissions=_manager(),
            headers={},  # type: ignore[arg-type]
        )


async def test_redirect_chain_inside_the_allow_list_is_followed() -> None:
    client = _FakeClient(
        {
            "https://api.github.com/a": _FakeResponse(302, location="https://api.github.com/b"),
            "https://api.github.com/b": _FakeResponse(200, payload={"ok": True}),
        }
    )
    response = await _get_checked(
        client,
        "https://api.github.com/a",
        permissions=_manager(),
        headers={},  # type: ignore[arg-type]
    )
    assert response.status_code == 200
    assert client.requested == ["https://api.github.com/a", "https://api.github.com/b"]


async def test_redirect_loop_is_bounded() -> None:
    client = _FakeClient(
        {
            "https://api.github.com/a": _FakeResponse(302, location="https://api.github.com/a"),
        }
    )
    with pytest.raises(PermissionDeniedError):
        await _get_checked(
            client,
            "https://api.github.com/a",
            permissions=_manager(),
            headers={},  # type: ignore[arg-type]
        )
    # Bounded, not infinite.
    assert len(client.requested) <= discovery._MAX_REDIRECTS + 1


async def test_relative_redirect_is_resolved_against_the_current_url() -> None:
    client = _FakeClient(
        {
            "https://api.github.com/repos/x/y": _FakeResponse(302, location="/other"),
            "https://api.github.com/other": _FakeResponse(200, payload={}),
        }
    )
    response = await _get_checked(
        client,  # type: ignore[arg-type]
        "https://api.github.com/repos/x/y",
        permissions=_manager(),
        headers={},
    )
    assert response.status_code == 200
    assert client.requested[-1] == "https://api.github.com/other"


async def test_fetch_skill_file_does_not_ask_httpx_to_follow_redirects() -> None:
    """The client must not be the thing that follows a redirect."""
    captured: dict[str, Any] = {}

    class _RecordingClient:
        def __init__(self, **kwargs: Any) -> None:
            captured.update(kwargs)

        async def __aenter__(self) -> _RecordingClient:
            return self

        async def __aexit__(self, *_exc: Any) -> bool:
            return False

        async def get(self, _url: str, **_kwargs: Any) -> _FakeResponse:
            return _FakeResponse(404)

    original = discovery.httpx.AsyncClient
    discovery.httpx.AsyncClient = _RecordingClient  # type: ignore[assignment]
    try:
        result = await discovery.fetch_skill_file(
            "x/y", "SKILL.md", ref="deadbeef", permissions=_manager()
        )
    finally:
        discovery.httpx.AsyncClient = original  # type: ignore[assignment]

    # A 404 is "not a skill", not an error — and the client was built without redirect
    # following, so `_get_checked` is the only thing that can move the request.
    assert result is None
    assert captured.get("follow_redirects") is False
    assert httpx.__name__ == "httpx"  # the real module is still imported
