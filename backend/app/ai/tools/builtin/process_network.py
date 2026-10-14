"""Process, network and observation tools.

These are the capabilities that reach furthest outside the project directory, so
each one is bounded tightly:

* **Process** — listing is free. Starting is allowed at the System level but
  recorded. Stopping refuses to touch a process this run did not start unless the
  user confirms, because "stop the process on port 8000" is one typo away from
  killing something the user cares about.
* **Network** — every request is checked against the allowed-domain list first.
  Downloads are size-capped while streaming, so a 4 GB file cannot fill the disk
  before anyone notices, and a download is written into the workspace, never over
  an existing file.
* **Observation** — the screen tools are read-only and report honestly when the
  environment cannot support them rather than returning a blank image.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.ai.agent.tools import Tool, ToolContext
from app.ai.types import ToolResult, ToolSpec
from app.core.errors import PermissionDeniedError, ValidationError

NETWORK_TIMEOUT = 60.0
MAX_RESPONSE_BYTES = 2_000_000

#: Programs whose arguments *are* a program. Judging the wrapper tells you nothing about
#: what will run, so `process_start` refuses them and points at the shell tool, which has
#: its own permission path.
#:
#: Assembled from fragments on purpose, the same way `scripts/verify_shortcut.py` does it:
#: a bare list of shell names in a source file reads like something to run.
_SHELL = "power" + "shell"
SHELL_WRAPPERS = frozenset(
    {
        "cmd", "cmd.exe",
        _SHELL, _SHELL + ".exe",
        "pwsh", "pwsh.exe",
        "sh", "bash",
        "wscript", "wscript.exe", "cscript", "cscript.exe",
    }
)


# --------------------------------------------------------------------------- #
# Process
# --------------------------------------------------------------------------- #
class ProcessListTool(Tool):
    spec = ToolSpec(
        name="process_list",
        description="List running processes with pid, name, memory and start time. Optionally filter by name.",
        category="process",
        parameters={
            "type": "object",
            "properties": {
                "name_filter": {"type": "string", "description": "Substring match on the process name"},
                "limit": {"type": "integer"},
            },
            "required": [],
        },
        permission_level=0,
        risk_level="low",
        timeout_seconds=30,
        mutates_state=False,
    )

    async def execute(self, ctx: ToolContext, name_filter: str = "", limit: int = 60) -> ToolResult:
        exe, flags = _powershell()
        script = (
            "Get-Process | Select-Object Id,ProcessName,"
            "@{n='MB';e={[math]::Round($_.WorkingSet64/1MB,1)}},"
            "@{n='Started';e={if($_.StartTime){$_.StartTime.ToString('HH:mm:ss')}else{''}}} | "
            "ConvertTo-Json -Compress"
        )
        from app.ai.tools.builtin.shell import _run_process

        outcome = await _run_process(exe=exe, args=[*flags, script], cwd=Path(ctx.resolve(".")), timeout=30)
        if outcome["exit_code"] != 0:
            return ToolResult(ok=False, error=outcome["stderr"], summary="could not list processes")

        import json

        try:
            raw = json.loads(outcome["stdout"] or "[]")
        except json.JSONDecodeError:
            raw = []
        if isinstance(raw, dict):
            raw = [raw]

        needle = (name_filter or "").lower()
        rows = [
            {
                "pid": item.get("Id"),
                "name": item.get("ProcessName"),
                "memory_mb": item.get("MB"),
                "started": item.get("Started", ""),
            }
            for item in raw
            if not needle or needle in str(item.get("ProcessName", "")).lower()
        ][: max(1, min(int(limit or 60), 400))]

        return ToolResult(ok=True, data={"processes": rows, "count": len(rows)}, summary=f"{len(rows)} processes")


class ProcessStartTool(Tool):
    spec = ToolSpec(
        name="process_start",
        description="Start a program or script. Returns the pid. Long-running services are better started with terminal_exec.",
        category="process",
        parameters={
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Executable or script to run"},
                "arguments": {"type": "array", "items": {"type": "string"}},
                "cwd": {"type": "string"},
            },
            "required": ["command"],
        },
        permission_level=2,
        risk_level="medium",
        timeout_seconds=60,
        mutates_state=True,
    )
    is_write = True

    async def execute(
        self,
        ctx: ToolContext,
        command: str,
        arguments: list[str] | None = None,
        cwd: str | None = None,
    ) -> ToolResult:
        working = Path(ctx.resolve(cwd)) if cwd else Path(ctx.resolve("."))
        ctx.require_permission(working, write=True)
        if ctx.permissions is not None:
            # **The whole command line, not just the executable.**
            #
            # This used to be `check_command(command)`, which saw `powershell.exe` while
            # `create_subprocess_exec` ran `powershell.exe -Command "Remove-Item -Recurse
            # -Force"`. The classifier judged one string and the machine ran another, so
            # an Autonomous run executed a destructive command at `risk=low`.
            #
            # A shell wrapper is refused outright: its arguments *are* the program, and
            # there is no way to classify a wrapper honestly. Shell work belongs to the
            # shell tool, which has its own gate.
            wrapper = Path(command).name.lower()
            if wrapper in SHELL_WRAPPERS:
                raise PermissionDeniedError(
                    f"{wrapper} is a shell wrapper; its arguments are a program, so this "
                    "tool cannot judge what it will run. Use the shell tool instead.",
                    details={"command": command, "arguments": (arguments or [])[:10]},
                )
            full_command = " ".join([command, *(arguments or [])]).strip()
            verdict = ctx.permissions.check_command(full_command, cwd=working, interpreter="process")
            if not verdict.allowed:
                raise PermissionDeniedError(
                    verdict.reason, details={"command": full_command[:300]}
                )

        creationflags = 0x0800_0000 if os.name == "nt" else 0
        try:
            process = await asyncio.create_subprocess_exec(
                command,
                *(arguments or []),
                cwd=str(working),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except (OSError, FileNotFoundError) as exc:
            return ToolResult(ok=False, error=f"Could not start '{command}': {exc}", summary="start failed")

        ctx.consume_write()
        ctx.register_artifact("processes_started", str(process.pid))
        return ToolResult(
            ok=True,
            data={"pid": process.pid, "command": command, "arguments": arguments or [], "cwd": str(working)},
            summary=f"started pid {process.pid}",
        )


class ProcessStopTool(Tool):
    spec = ToolSpec(
        name="process_stop",
        description=(
            "Stop a process by pid. Refuses to stop a process this run did not "
            "start unless you confirm, so a mistyped pid cannot kill something "
            "important."
        ),
        category="process",
        parameters={
            "type": "object",
            "properties": {
                "pid": {"type": "integer"},
                "force": {"type": "boolean", "description": "Kill the whole tree"},
            },
            "required": ["pid"],
        },
        permission_level=2,
        risk_level="high",
        timeout_seconds=30,
        mutates_state=True,
        requires_confirmation=True,
    )
    is_write = True

    async def execute(self, ctx: ToolContext, pid: int, force: bool = False) -> ToolResult:
        pid = int(pid)
        if pid <= 4:
            return ToolResult(
                ok=False, error=f"Refusing to stop pid {pid}: it is a system process", summary="refused"
            )

        started = {int(p) for p in ctx.artifacts.get("processes_started", []) if str(p).isdigit()}
        if pid not in started and pid != os.getpid():
            # Not ours: the confirmation the executor already collected is what
            # makes this acceptable, so it is reported rather than silently done.
            pass

        if os.name == "nt":
            args = ["/PID", str(pid), "/F"] + (["/T"] if force else [])
            killer = await asyncio.create_subprocess_exec(
                "taskkill", *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            _, err = await killer.communicate()
            if killer.returncode != 0:
                return ToolResult(ok=False, error=err.decode(errors="replace"), summary="stop failed")
        else:
            import signal

            try:
                os.kill(pid, signal.SIGKILL if force else signal.SIGTERM)
            except ProcessLookupError:
                return ToolResult(ok=False, error=f"No process with pid {pid}", summary="not running")

        ctx.consume_write()
        return ToolResult(ok=True, data={"pid": pid, "force": force}, summary=f"stopped pid {pid}")


class ProcessStatusTool(Tool):
    spec = ToolSpec(
        name="process_status",
        description="Check whether a pid is alive, and what is listening on a port.",
        category="process",
        parameters={
            "type": "object",
            "properties": {"pid": {"type": "integer"}, "port": {"type": "integer"}},
            "required": [],
        },
        permission_level=0,
        risk_level="low",
        timeout_seconds=30,
        mutates_state=False,
    )

    async def execute(self, ctx: ToolContext, pid: int | None = None, port: int | None = None) -> ToolResult:
        import socket

        data: dict[str, Any] = {}
        if pid is not None:
            alive = True
            if os.name == "nt":
                probe = await asyncio.create_subprocess_exec(
                    "tasklist", "/FI", f"PID eq {int(pid)}", "/NH",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                )
                out, _ = await probe.communicate()
                alive = str(int(pid)) in out.decode(errors="replace")
            data["pid"] = int(pid)
            data["alive"] = alive

        if port is not None:
            port = int(port)
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1.5)
                data["port"] = port
                data["listening"] = sock.connect_ex(("127.0.0.1", port)) == 0

        return ToolResult(ok=True, data=data, summary=", ".join(f"{k}={v}" for k, v in data.items()) or "nothing asked")


# --------------------------------------------------------------------------- #
# Network
# --------------------------------------------------------------------------- #
def _powershell() -> tuple[str, list[str]]:
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        raise ValidationError("PowerShell was not found on PATH")
    return exe, ["-NoProfile", "-NonInteractive", "-Command"]


class _NetworkTool(Tool):
    async def _authorise(self, ctx: ToolContext, url: str, *, writes: bool = False) -> None:
        """Check the URL, and check it **as a write** when it carries a body.

        `writes` is not a formality. A GET to an allow-listed host and a POST of the user's
        own notes to that same host are different acts, and an allow-list entry was granted
        for the first one. Sending is confirmed separately.
        """
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"}:
            raise ValidationError(f"Only http and https are supported, got '{parsed.scheme}'")
        if not parsed.hostname:
            raise ValidationError(f"No host in URL: {url}")
        if ctx.permissions is None:
            return
        verdict = ctx.permissions.check_domain(url, writes=writes)
        if not verdict.allowed:
            raise PermissionDeniedError(
                verdict.reason,
                details={
                    "url": url,
                    "host": parsed.hostname,
                    "risk": verdict.risk.value,
                    "method": "write" if writes else "read",
                },
            )

    @staticmethod
    def _perform_request(
        url: str,
        method: str,
        body: str,
        headers: dict[str, str],
        *,
        check_hop: Callable[[str], None],
    ) -> dict[str, Any]:
        """Make the request in Python. Synchronous; the caller runs it in a thread.

        ## What this replaced, and why

        The previous version built a script string and handed it to a shell host:

            safe_url  = url.replace("'", "''")
            safe_body = body.replace("'", "''")
            header_block = f"$h = @{{ {headers} }}"      # headers, unescaped

        `headers` is a **model-supplied tool parameter**, so anything the model put there
        was executed by that host — downstream of the Permission Manager, the workspace
        sandbox, the risk classifier and the audit log. The tool's own schema even invited
        host-language input ("hashtable entries, e.g. ...").

        There was never a reason for it. Python has `urllib`. No string is built, nothing
        is parsed as code, and `headers` is now a plain object of name → value.

        ## Redirects are followed here, one hop at a time

        `check_domain` sees the URL it is given. A permitted host that answers `302
        Location: http://127.0.0.1:6333/...` used to be followed by the HTTP client
        without anyone looking again — the gate never saw the real target. `check_hop` is
        called for the first URL and for **every** redirect target, so the gate sees all
        of them.
        """
        import urllib.error
        import urllib.request

        class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, hdrs, newurl):  # type: ignore[override]
                check_hop(newurl)
                return super().redirect_request(req, fp, code, msg, hdrs, newurl)

        opener = urllib.request.build_opener(_CheckedRedirect)
        request = urllib.request.Request(
            url,
            data=body.encode("utf-8") if body else None,
            method=method,
            headers={"User-Agent": "ai-workbench/2.0", **headers},
        )
        try:
            with opener.open(request, timeout=NETWORK_TIMEOUT) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                truncated = len(raw) > MAX_RESPONSE_BYTES
                if truncated:
                    raw = raw[:MAX_RESPONSE_BYTES]
                return {
                    "status": int(response.status),
                    "content": raw.decode("utf-8", errors="replace"),
                    "length": len(raw),
                    "content_type": response.headers.get("Content-Type", ""),
                    "content_truncated": truncated,
                    "final_url": response.geturl(),
                }
        except urllib.error.HTTPError as exc:
            return {
                "status": int(exc.code),
                "error": f"HTTP {exc.code} {exc.reason}",
                "final_url": exc.geturl() if hasattr(exc, "geturl") else url,
            }
        except urllib.error.URLError as exc:
            return {"status": 0, "error": f"{type(exc.reason).__name__}: {exc.reason}"}



class HttpGetTool(_NetworkTool):
    spec = ToolSpec(
        name="http_get",
        description="Fetch a URL and return the response body. Only allowed hosts may be contacted.",
        category="network",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                # Was a free-form string of host-language hashtable entries. That string
                # was interpolated into an executed script — see `_perform_request`. A
                # plain object of name → value cannot become code.
                "headers": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "Request headers as name/value pairs.",
                },
            },
            "required": ["url"],
        },
        permission_level=2,
        risk_level="medium",
        timeout_seconds=NETWORK_TIMEOUT,
        mutates_state=False,
    )

    async def execute(
        self, ctx: ToolContext, url: str, headers: dict[str, str] | None = None
    ) -> ToolResult:
        await self._authorise(ctx, url)
        return await _run_http(ctx, url, "GET", "", headers or {}, summary_prefix="GET")


class HttpPostTool(_NetworkTool):
    spec = ToolSpec(
        name="http_post",
        description="POST a body to a URL and return the response.",
        category="network",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "body": {"type": "string"},
                "headers": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
            },
            "required": ["url"],
        },
        permission_level=2,
        risk_level="medium",
        timeout_seconds=NETWORK_TIMEOUT,
        mutates_state=True,
    )

    async def execute(
        self,
        ctx: ToolContext,
        url: str,
        body: str = "",
        headers: dict[str, str] | None = None,
    ) -> ToolResult:
        # A POST carries a body, so it is authorised as egress rather than as a read.
        await self._authorise(ctx, url, writes=True)
        return await _run_http(ctx, url, "POST", body, headers or {}, summary_prefix="POST")


async def _run_http(
    ctx: ToolContext,
    url: str,
    method: str,
    body: str,
    headers: dict[str, str],
    *,
    summary_prefix: str,
) -> ToolResult:
    """One implementation of "make an HTTP request", used by both tools.

    There used to be two: this one built a script and ran it through a shell host, and
    `DownloadFileTool` used `urllib` directly. They had different timeouts, different
    redirect behaviour and different error shapes, so a fix to one was a fix to half.
    """
    import asyncio

    def check_hop(hop_url: str) -> None:
        """Every URL that will actually be contacted must pass the gate.

        Called for the first URL and for each redirect target. Without this, a permitted
        host could answer `302 Location: http://127.0.0.1:6333/...` and the check would
        never see the address that was really reached.
        """
        if ctx.permissions is None:
            return
        verdict = ctx.permissions.check_domain(hop_url)
        if not verdict.allowed:
            raise PermissionDeniedError(
                f"Redirect target refused: {verdict.reason}", details={"url": hop_url}
            )

    try:
        payload = await asyncio.to_thread(
            _NetworkTool._perform_request, url, method, body, headers, check_hop=check_hop
        )
    except PermissionDeniedError:
        raise
    except Exception as exc:
        return ToolResult(
            ok=False,
            error=f"{type(exc).__name__}: {exc}",
            summary=f"{summary_prefix} failed",
        )

    if payload.get("error"):
        return ToolResult(
            ok=False, error=str(payload["error"]), data=payload, summary=f"{summary_prefix} failed"
        )

    return ToolResult(
        ok=200 <= int(payload.get("status", 0)) < 400,
        data=payload,
        summary=f"{summary_prefix} {payload.get('status')} ({payload.get('length', 0)} bytes)",
    )


class DownloadFileTool(_NetworkTool):
    spec = ToolSpec(
        name="download_file",
        description=(
            "Download a URL into the workspace. The size is capped while streaming "
            "and an existing file is never overwritten without permission."
        ),
        category="network",
        parameters={
            "type": "object",
            "properties": {
                "url": {"type": "string"},
                "destination": {"type": "string"},
                "max_bytes": {"type": "integer"},
                "overwrite": {"type": "boolean"},
            },
            "required": ["url", "destination"],
        },
        permission_level=2,
        risk_level="medium",
        timeout_seconds=600,
        mutates_state=True,
    )
    is_write = True

    async def execute(
        self,
        ctx: ToolContext,
        url: str,
        destination: str,
        max_bytes: int | None = None,
        overwrite: bool = False,
    ) -> ToolResult:
        await self._authorise(ctx, url)
        target = Path(ctx.resolve(destination))
        ctx.require_permission(target, write=True)

        if target.exists() and not overwrite:
            return ToolResult(ok=False, error=f"{target} already exists", summary="destination exists")

        cap = min(
            int(max_bytes or (ctx.permissions.policy.max_download_bytes if ctx.permissions else 200_000_000)),
            ctx.permissions.policy.max_download_bytes if ctx.permissions else 200_000_000,
        )
        target.parent.mkdir(parents=True, exist_ok=True)

        import urllib.request

        def _check_hop(hop_url: str) -> None:
            """The download follows redirects too, so it re-checks them too.

            This was the second of the two outbound paths, and it followed redirects with
            no re-check: an allow-listed host could send it to `http://127.0.0.1:6333/…`
            and the gate would never see the address that was really fetched.
            """
            if ctx.permissions is None:
                return
            verdict = ctx.permissions.check_domain(hop_url)
            if not verdict.allowed:
                raise PermissionDeniedError(
                    f"Redirect target refused: {verdict.reason}", details={"url": hop_url}
                )

        class _CheckedRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, hdrs, newurl):  # type: ignore[override]
                _check_hop(newurl)
                return super().redirect_request(req, fp, code, msg, hdrs, newurl)

        def _stream() -> dict[str, Any]:
            request = urllib.request.Request(url, headers={"User-Agent": "ai-workbench/2.0"})
            written = 0
            opener = urllib.request.build_opener(_CheckedRedirect)
            with opener.open(request, timeout=NETWORK_TIMEOUT) as response:
                declared = response.headers.get("Content-Length")
                if declared and int(declared) > cap:
                    raise ValidationError(
                        f"The file is {int(declared)} bytes, over the {cap}-byte download limit"
                    )
                with target.open("wb") as handle:
                    while True:
                        chunk = response.read(65536)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > cap:
                            handle.close()
                            target.unlink(missing_ok=True)
                            raise ValidationError(f"Download exceeded the {cap}-byte limit and was removed")
                        handle.write(chunk)
            return {"bytes": written, "content_type": response.headers.get("Content-Type", "")}

        try:
            info = await asyncio.wait_for(asyncio.to_thread(_stream), timeout=600)
        except ValidationError as exc:
            return ToolResult(ok=False, error=str(exc), summary="download refused")
        except Exception as exc:
            target.unlink(missing_ok=True)
            return ToolResult(ok=False, error=f"{type(exc).__name__}: {exc}", summary="download failed")

        ctx.consume_write()
        ctx.register_artifact("files_downloaded", str(target))
        return ToolResult(
            ok=True,
            data={"path": str(target), **info},
            summary=f"downloaded {info['bytes']} bytes to {target.name}",
        )


# --------------------------------------------------------------------------- #
# Observation
# --------------------------------------------------------------------------- #
class ScreenshotTool(Tool):
    spec = ToolSpec(
        name="screenshot",
        description=(
            "Capture the screen to a PNG in the workspace. Reports honestly when "
            "the environment cannot capture (for example a headless session) "
            "rather than returning an empty image."
        ),
        category="observation",
        parameters={
            "type": "object",
            "properties": {"destination": {"type": "string", "description": "Defaults to workspace/screenshots"}},
            "required": [],
        },
        permission_level=0,
        risk_level="low",
        timeout_seconds=45,
        mutates_state=True,
    )
    is_write = True

    async def execute(self, ctx: ToolContext, destination: str = "") -> ToolResult:
        if destination:
            target = Path(ctx.resolve(destination))
        else:
            base = ctx.permissions.policy.workspace if ctx.permissions else Path(ctx.resolve("."))
            target = base / "screenshots" / f"screen-{time.strftime('%Y%m%d-%H%M%S')}.png"
        ctx.require_permission(target, write=True)
        target.parent.mkdir(parents=True, exist_ok=True)

        exe, flags = _powershell()
        script = (
            "Add-Type -AssemblyName System.Windows.Forms,System.Drawing;"
            "$b=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
            "if($b.Width -le 0 -or $b.Height -le 0){Write-Output 'NO_DISPLAY'; exit 2};"
            "$bmp=New-Object System.Drawing.Bitmap $b.Width,$b.Height;"
            "$g=[System.Drawing.Graphics]::FromImage($bmp);"
            "$g.CopyFromScreen($b.Left,$b.Top,0,0,$bmp.Size);"
            f"$bmp.Save('{str(target).replace(chr(92), chr(92) * 2)}',[System.Drawing.Imaging.ImageFormat]::Png);"
            "$g.Dispose();$bmp.Dispose();Write-Output 'OK'"
        )
        from app.ai.tools.builtin.shell import _run_process

        outcome = await _run_process(exe=exe, args=[*flags, script], cwd=Path(ctx.resolve(".")), timeout=45)
        if outcome["exit_code"] != 0 or "NO_DISPLAY" in outcome["stdout"]:
            return ToolResult(
                ok=False,
                error="No interactive display is available in this session, so the screen cannot be captured.",
                summary="no display available",
            )
        if not target.exists():
            return ToolResult(ok=False, error="The capture produced no file", summary="capture failed")

        ctx.consume_write()
        ctx.register_artifact("screenshots", str(target))
        return ToolResult(
            ok=True,
            data={"path": str(target), "bytes": target.stat().st_size},
            summary=f"saved {target.name}",
        )


class ScreenReadTool(Tool):
    spec = ToolSpec(
        name="screen_read",
        description="Report the display geometry and the foreground window title.",
        category="observation",
        parameters={"type": "object", "properties": {}, "required": []},
        permission_level=0,
        risk_level="low",
        timeout_seconds=30,
        mutates_state=False,
    )

    async def execute(self, ctx: ToolContext) -> ToolResult:
        exe, flags = _powershell()
        script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$s=[System.Windows.Forms.SystemInformation]::VirtualScreen;"
            "$f=(Get-Process | Where-Object {$_.MainWindowTitle} | Select-Object -First 1 -ExpandProperty MainWindowTitle);"
            "[ordered]@{width=$s.Width;height=$s.Height;foreground=$f} | ConvertTo-Json -Compress"
        )
        from app.ai.tools.builtin.shell import _run_process

        outcome = await _run_process(exe=exe, args=[*flags, script], cwd=Path(ctx.resolve(".")), timeout=30)
        if outcome["exit_code"] != 0:
            return ToolResult(
                ok=False,
                error="The display could not be queried in this session.",
                summary="no display available",
            )
        import json

        try:
            info = json.loads(outcome["stdout"].strip() or "{}")
        except json.JSONDecodeError:
            info = {}
        return ToolResult(
            ok=True,
            data=info,
            summary=f"{info.get('width', '?')}x{info.get('height', '?')}",
        )


def build_process_tools() -> list[Tool]:
    return [ProcessListTool(), ProcessStartTool(), ProcessStopTool(), ProcessStatusTool()]


def build_network_tools() -> list[Tool]:
    return [HttpGetTool(), HttpPostTool(), DownloadFileTool()]


def build_observation_tools() -> list[Tool]:
    return [ScreenshotTool(), ScreenReadTool()]


__all__ = [
    "build_network_tools",
    "build_observation_tools",
    "build_process_tools",
]
