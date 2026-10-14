"""The proxy is for the network, not for this machine.

These tests set a proxy the way Windows does — in the environment, with no bypass list —
because that is the configuration that broke: `urllib.request.getproxies()` returns
`ProxyServer` and ignores `ProxyOverride`, so `127.0.0.1` is proxied like any other host.
"""

from __future__ import annotations

import http.server
import os
import pathlib
import subprocess
import sys
import threading
from collections.abc import Iterator

import httpx
import pytest

from app.core.local_net import LOOPBACK_HOSTS, exempt_loopback_from_proxy


@pytest.fixture
def local_server() -> Iterator[str]:
    """A real HTTP server on loopback, on a port the OS picks."""

    class Handler(http.server.BaseHTTPRequestHandler):
        # `do_GET` is the name `BaseHTTPRequestHandler` dispatches to.
        def do_GET(self) -> None:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"ok")

        def log_message(self, *args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def windows_style_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """A proxy that cannot work, and no bypass list — the state the product shipped into.

    Port 1 has nothing on it, so a request that reaches the proxy fails loudly instead of
    quietly succeeding through some other path.
    """
    monkeypatch.setenv("http_proxy", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.delenv("no_proxy", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)


def test_a_loopback_request_reaches_this_machine_with_a_proxy_configured(
    windows_style_proxy: None, local_server: str
) -> None:
    """The behaviour that was broken.

    Without the exemption this raises `ConnectError`, because the request is sent to the
    proxy. With it, the request goes where it was addressed.
    """
    exempt_loopback_from_proxy()

    with httpx.Client(timeout=5) as client:
        assert client.get(local_server).status_code == 200


def test_the_exemption_is_visible_under_both_spellings(windows_style_proxy: None) -> None:
    """Read it back through both names, not just the one this module writes last.

    On Windows these are one variable — `os.environ` upper-cases the key — so this asserts
    what a caller on either platform would see.
    """
    exempt_loopback_from_proxy()

    for name in ("no_proxy", "NO_PROXY"):
        value = os.environ.get(name, "")
        for host in LOOPBACK_HOSTS:
            assert host in value.split(","), f"{name} is missing {host}"


def test_importing_the_settings_module_is_enough(local_server: str, tmp_path: pathlib.Path) -> None:
    """The exemption is not something each caller has to remember to invoke.

    This is what the second clean-machine run found. The call used to sit at the two process
    entry points — `app.main.lifespan` and `awbnative.bootstrap.ensure_ready` — and
    `scripts/verify_qdrant_key.py`, the tool the task book tells a reader to use for judging
    semantic retrieval, builds a `VectorStore` directly and goes through neither. Its own
    probe was sent to the proxy, so its verdict flipped with the proxy's behaviour: 12/12
    where the proxy forwards loopback, 9/12 where it refuses. **The tool that decides
    whether the defect regressed was the thing producing the false negative.**

    It now lives in `app.core.config`, which is not a convenience but a data dependency: a
    client cannot be pointed at a local service without first asking `settings` where that
    service is.

    Run in a child process on purpose — the variable is read when a client is constructed,
    and by the time this suite runs, `app.core.config` is long since imported.
    """
    backend = pathlib.Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["http_proxy"] = "http://127.0.0.1:1"  # nothing listens there
    env["HTTP_PROXY"] = "http://127.0.0.1:1"
    env.pop("no_proxy", None)
    env.pop("NO_PROXY", None)
    env["AI_WORKBENCH_DATA_DIR"] = str(tmp_path)  # keep `ensure_dirs` out of the repo
    code = (
        f"import sys; sys.path.insert(0, {str(backend)!r});"
        "import app.core.config;"  # the only line under test
        "import httpx;"
        f"print(httpx.get({local_server!r}, timeout=5).status_code)"
    )

    result = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "200", result.stdout + result.stderr


def test_the_users_own_bypass_list_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    """A machine that already bypasses its intranet must still bypass its intranet.

    Only `no_proxy` is set. Deleting `NO_PROXY` as well would delete this same variable on
    Windows, where environment names are case-insensitive — which is how this test first
    failed, and the reason it is written down.
    """
    monkeypatch.setenv("no_proxy", "10.0.0.0/8,corp.example")

    value = exempt_loopback_from_proxy()

    assert "10.0.0.0/8" in value
    assert "corp.example" in value
    assert "127.0.0.1" in value


def test_calling_it_twice_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("no_proxy", "")

    once = exempt_loopback_from_proxy()
    twice = exempt_loopback_from_proxy()

    assert once == twice
