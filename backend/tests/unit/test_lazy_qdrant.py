"""The deferred Qdrant import, and the two ways it can silently break.

`app/infrastructure/vector_store.py` no longer imports `qdrant_client` at module scope.
That is not a style preference — it is the largest single item in the API's startup:

    import qdrant_client     1.19 s
    import app.main          3.08–5.70 s   (before)
    import app.main          1.81–1.86 s   (after)

The cost was paid on **every** start because the import chain reaches this module through

    app.api.v1.ai -> app.services.ai_services -> app.ai.rag.pipeline
    -> app.ai.rag.retriever -> app.infrastructure.vector_store

and nothing on the startup path needs Qdrant.

Two ways that breaks, both covered below:

1. **Someone re-adds the module-scope import.** Everything still *works* — it is just
   slow again, and no functional test would notice. Caught by importing the app in a
   fresh interpreter and asking whether Qdrant came along.

2. **The imports move back to module scope via a `__getattr__`.** This looks tidier and
   does not work: a module `__getattr__` is consulted for attribute access on the module
   object, but *not* for a plain global lookup inside a function defined in the module.
   Every `models.Filter(...)` and every `except UnexpectedResponse` would raise
   `NameError` — at runtime, in the retrieval path. Caught by driving a real method.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2]


def test_importing_the_app_does_not_import_qdrant() -> None:
    """In a fresh interpreter, because `sys.modules` is process-wide.

    Checked in a subprocess rather than by inspecting the current one: another test
    having already imported Qdrant would make this pass for the wrong reason, which is
    the failure mode a "not imported" assertion is most prone to.
    """
    code = textwrap.dedent(
        """
        import sys
        import app.main  # noqa: F401
        print("qdrant_client" in sys.modules)
        """
    )
    result = subprocess.run(
        [sys.executable, "-W", "ignore", "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=BACKEND_DIR,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False", (
        "importing app.main pulled in qdrant_client; the deferral in vector_store.py is "
        f"no longer doing anything (stdout={result.stdout!r})"
    )


def test_importing_vector_store_alone_does_not_import_qdrant() -> None:
    """The narrow version of the same check, so a failure points at one file."""
    code = textwrap.dedent(
        """
        import sys
        from app.infrastructure import vector_store  # noqa: F401
        print("qdrant_client" in sys.modules)
        """
    )
    result = subprocess.run(
        [sys.executable, "-W", "ignore", "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=BACKEND_DIR,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False", result.stdout


@pytest.mark.asyncio
async def test_a_method_that_uses_models_resolves_it_from_its_local_import() -> None:
    """`search` builds a `models.Filter` before it touches the network.

    Pointed at a port nothing listens on, so the call cannot succeed — what matters is
    *how* it fails. A `NameError` means the local import is gone and the module-scope
    name is being looked up in a way a module `__getattr__` cannot serve. A
    `VectorStoreError` means the filter was built and the failure was the connection,
    which is the intended shape.
    """
    from app.core.errors import VectorStoreError
    from app.infrastructure.vector_store import VectorStore

    store = VectorStore(url="http://127.0.0.1:1", collection="lazy_probe")
    with pytest.raises((VectorStoreError, OSError)) as caught:
        await store.search([0.1, 0.2], knowledge_base_ids=[uuid.uuid4()])
    assert not isinstance(caught.value, NameError)
    assert "models" not in str(caught.value)


def test_client_construction_still_works_without_a_server() -> None:
    """`client()` builds the real client object; the lazy import must not break it."""
    from app.infrastructure.vector_store import VectorStore

    store = VectorStore(url="http://127.0.0.1:1", collection="lazy_probe")
    client = store.client()
    assert client is store.client(), "the client should be created once and reused"
    assert type(client).__name__ == "AsyncQdrantClient"
