"""This machine's own services are on loopback, and loopback must not go through a proxy.

**The measurement, because the symptom does not look like a network problem.** On a Windows
machine with a system proxy configured, `http://127.0.0.1:6333` is sent *to the proxy*. With
the proxy running, `_check_qdrant` receives `HTTP 401`; with it stopped, `ConnectError
[WinError 10061]`. Neither answer comes from Qdrant, which was running and healthy in both
cases — and the second one was reported to the user as "Qdrant 不可达", i.e. as a service
that needs starting.

It is not only the probe. `AsyncQdrantClient.get_collections()` raised
`ResponseHandlingException: All connection attempts failed` against the same healthy Qdrant,
so semantic retrieval was dead and the product had silently fallen back to keyword search.
That is the defect this module exists to prevent, and it is invisible from a development
machine whose proxy happens to be off — the failure mode just changes shape.

**Why the system proxy applies at all.** Windows stores it in the registry, and
`urllib.request.getproxies()` — which httpx calls — returns `ProxyServer` without consulting
`ProxyOverride`. That override is the field every other Windows program uses to skip
`127.0.0.1`; Python does not see it, so `localhost` is proxied unless `no_proxy` says
otherwise. A Clash-style client on `127.0.0.1:7890` is the ordinary configuration this
breaks.

**Why an environment variable rather than `trust_env=False` on each client.** `no_proxy` is
read by httpx itself, so one call here covers the readiness probes, the vector store client,
and the Qdrant client inside `qdrant_client`, which builds its own `httpx.AsyncClient` and
would have to be reached through a passthrough argument. Setting it per client means
remembering to set it at every construction site, including in libraries — and the failure
mode of forgetting is a product that quietly stops doing semantic search.

**Only loopback is exempted.** Everything off this machine still goes through the proxy,
which is what a proxy is for.
"""

from __future__ import annotations

import os

#: Hosts that mean "this machine". The same set `app.ai.runtime_config._LOCAL_HOSTS` allows
#: plain HTTP to, minus `host.docker.internal` — that name goes through DNS and is not a
#: literal loopback address, so exempting it would be a guess.
LOOPBACK_HOSTS: tuple[str, ...] = ("127.0.0.1", "localhost", "::1")


def _with_loopback(value: str) -> str:
    """`value` plus any loopback host it is missing, preserving what was already there."""
    entries = [item.strip() for item in value.split(",") if item.strip()]
    present = {item.lower() for item in entries}
    missing = [host for host in LOOPBACK_HOSTS if host.lower() not in present]
    return ",".join(entries + missing)


def exempt_loopback_from_proxy() -> str:
    """Add loopback to `no_proxy`, keeping whatever the user already had.

    Both spellings are written. httpx accepts either, but they are read by different
    programs and a proxy configured by one tool routinely sets only one of them.

    Returns the resulting value so a caller can log it. Idempotent.
    """
    for name in ("no_proxy", "NO_PROXY"):
        os.environ[name] = _with_loopback(os.environ.get(name, ""))
    return os.environ["no_proxy"]
