"""Opening a produced file with the operating system's default program.

Why this is a backend endpoint and not a `file://` link or a Tauri command
------------------------------------------------------------------------

Both of the obvious approaches are broken, and both were shipped:

* ``window.open(`file:///${path}`)`` — a WebView refuses `file://` navigation. The
  button did nothing at all, silently.
* A Tauri command taking a path — `desktop/src-tauri/src/commands.rs` is explicit
  that the shell layer accepts **no path from the page**, and it is right: a
  path-taking command is a general-purpose file opener reachable from any script
  on the page, which is exactly the escape hatch that file's header forbids.

So the path never comes from the client. The client sends a deliverable **id**; the
backend looks the row up, checks it belongs to the caller, and resolves the path
from its own record. Opening an arbitrary file is not expressible.

The launch itself is the OS's job, and a failure is reported rather than swallowed.
`os.startfile` raises when there is no handler for the extension; the endpoint
turns that into a message naming the file and the reason. A button that appears to
do nothing is the defect this module exists to remove.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.core.errors import ValidationError
from app.core.logging import get_logger

logger = get_logger(__name__)


def _launch(path: Path) -> str:
    """Hand the file to the OS. Returns how it was launched, for the response.

    Synchronous and blocking-by-nature, so callers run it off the event loop — see
    `open_path`. `os.startfile` returns immediately and does not report whether the
    application then opened successfully; that is the platform's contract, not
    something this can improve on.
    """
    if sys.platform == "win32":
        # Raises OSError when the extension has no association, which is the
        # failure worth surfacing rather than swallowing.
        os.startfile(str(path))  # noqa: S606 - deliberate, documented above
        return "os.startfile"

    if sys.platform == "darwin":
        subprocess.Popen(["open", str(path)], close_fds=True)
        return "open"

    subprocess.Popen(["xdg-open", str(path)], close_fds=True)
    return "xdg-open"


async def open_path(path: Path) -> dict[str, Any]:
    """Open `path` with the default application.

    Raises `ValidationError` when the file is gone or the OS has no handler. Both
    are user-visible conditions, and neither may be reported as success.
    """
    if not path.exists():
        raise ValidationError(f"文件已不在磁盘上，无法打开：{path}")
    if not path.is_file():
        raise ValidationError(f"这不是一个文件，无法打开：{path}")

    try:
        # `to_thread`, because `os.startfile` and `Popen` are blocking calls and
        # this runs on the API's event loop. A slow shell handler would otherwise
        # stall every other request on the worker.
        method = await asyncio.to_thread(_launch, path)
    except OSError as exc:
        logger.warning("open_path_failed", extra={"path": str(path), "error": str(exc)})
        raise ValidationError(
            f"系统没有可以用来打开「{path.name}」的程序（{exc.strerror or exc}）。"
            "文件本身没有问题，可以用「下载」把它存到别处再打开。"
        ) from exc

    logger.info("opened_path", extra={"path": str(path), "method": method})
    return {"path": str(path), "name": path.name, "opened": True, "method": method}


__all__ = ["open_path"]
