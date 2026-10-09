"""Named-pipe IPC: the out-of-process option, with the guarantees written down.

The in-process bridge (`bridge.py`) is the preferred production path. This module is the
other one the requirement allows, for the case where the backend must be a separate
process — a long-running service the UI attaches to, or a crash boundary between the UI
and the database.

`multiprocessing.connection` over `AF_PIPE` is used for the transport and nothing else. It
gives a Windows named pipe whose ACL is the creating user's, plus `send_bytes` /
`recv_bytes(maxlength)` — and `recv_bytes` **takes the cap**, which is where the message
length limit actually lives rather than in a comment.

Frames are length-prefixed JSON, not pickles. Both ends are ours, so a pickle would
technically work, but a deserialiser that can construct arbitrary objects is a poor thing
to put on an IPC boundary for no gain: the payloads here are plain data.

What every call carries, and why:

``id``        correlation. Two calls can be in flight; without an id, a slow one and a
              fast one are indistinguishable when they land.
``method``    the operation, from a fixed registry — never an import path or an expression.
``params``    a JSON object, validated by the handler.
``timeout``   enforced on the client side, so a wedged server cannot hang the UI.
``error``     a structured ``{code, message}``, never a bare string and never a traceback.

There is no "run this command" method. The method registry is a closed set, so a
compromised UI process cannot turn the pipe into an arbitrary-code channel.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections.abc import Callable
from multiprocessing import connection
from typing import Any

#: Hard ceiling on one frame. A caller that needs to move more than this is moving
#: something that should be a file path, not a message.
MAX_MESSAGE_BYTES = 1 * 1024 * 1024

#: How long a client waits for a reply before giving up.
DEFAULT_TIMEOUT_SECONDS = 30.0

PIPE_NAME = r"\\.\pipe\ai-workbench-native"
_AUTHKEY = b"ai-workbench-native-ui"


class IpcError(RuntimeError):
    """A failure the caller can act on, with a code rather than a paragraph."""

    def __init__(self, message: str, *, code: str = "ipc_error") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _encode(payload: dict[str, Any]) -> bytes:
    raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    if len(raw) > MAX_MESSAGE_BYTES:
        raise IpcError(
            f"消息 {len(raw)} 字节，超过上限 {MAX_MESSAGE_BYTES}",
            code="message_too_large",
        )
    return raw


class PipeServer:
    """Serves a closed set of methods over a named pipe.

    Runs on its own thread; each connection is handled one request at a time, which is
    enough for a UI and keeps the ordering obvious.
    """

    def __init__(
        self,
        handlers: dict[str, Callable[[dict[str, Any]], Any]],
        *,
        name: str = PIPE_NAME,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._handlers = handlers
        self._name = name
        self._timeout = timeout
        self._listener: connection.Listener | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self.served = 0
        self.last_error = ""

    # ------------------------------------------------------------------ control --
    def start(self) -> None:
        self._listener = connection.Listener(self._name, family="AF_PIPE", authkey=_AUTHKEY)
        self._thread = threading.Thread(target=self._accept_loop, daemon=True, name="awb-ipc")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
        if self._thread is not None:
            self._thread.join(timeout=5)

    # ------------------------------------------------------------------ serving --
    def _accept_loop(self) -> None:
        assert self._listener is not None
        while not self._stop.is_set():
            try:
                conn = self._listener.accept()
            except (OSError, EOFError):
                return
            threading.Thread(
                target=self._serve_connection,
                args=(conn,),
                daemon=True,
                name="awb-ipc-conn",
            ).start()

    def _serve_connection(self, conn: connection.Connection) -> None:
        """Serve every request on this connection until the client hangs up.

        One request per connection was the first version, and it was wrong in a way that
        looked like a client bug: the accept loop went straight back to `accept()` after
        the first reply, so the *second* call on an already-open connection was never
        read, and the client eventually saw a broken pipe instead of an answer.
        """
        with conn:
            while not self._stop.is_set():
                try:
                    if not conn.poll(0.5):
                        continue
                    raw = conn.recv_bytes(MAX_MESSAGE_BYTES)
                except (OSError, EOFError):
                    return
                reply = self._dispatch(raw)
                try:
                    conn.send_bytes(_encode(reply))
                    self.served += 1
                except (OSError, EOFError, IpcError) as exc:
                    self.last_error = f"{type(exc).__name__}: {exc}"
                    return

    def _dispatch(self, raw: bytes) -> dict[str, Any]:
        """Turn one frame into one reply. Never raises: the reply carries the failure."""
        request_id = ""
        try:
            request = json.loads(raw.decode("utf-8"))
            request_id = str(request.get("id") or "")
            method = str(request.get("method") or "")
            params = request.get("params") or {}
            if not isinstance(params, dict):
                raise IpcError("params 必须是对象", code="bad_request")
            handler = self._handlers.get(method)
            if handler is None:
                raise IpcError(f"未知方法 {method!r}", code="unknown_method")
            return {"id": request_id, "ok": True, "result": handler(params)}
        except IpcError as exc:
            return {"id": request_id, "ok": False, "error": {"code": exc.code, "message": exc.message}}
        except Exception as exc:  # noqa: BLE001 - the boundary reports, it does not decide
            return {
                "id": request_id,
                "ok": False,
                "error": {"code": "handler_error", "message": f"{type(exc).__name__}: {exc}"},
            }


class PipeClient:
    """Talks to a `PipeServer`. Enforces the timeout and the size cap on the way back."""

    def __init__(self, *, name: str = PIPE_NAME, timeout: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        self._name = name
        self._timeout = timeout
        self._conn: connection.Connection | None = None

    def connect(self, *, attempts: int = 20, delay: float = 0.25) -> None:
        last: Exception | None = None
        for _ in range(attempts):
            try:
                self._conn = connection.Client(self._name, family="AF_PIPE", authkey=_AUTHKEY)
                return
            except (OSError, EOFError) as exc:
                last = exc
                time.sleep(delay)
        raise IpcError(f"无法连接命名管道 {self._name}: {last}", code="connect_failed")

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass
            self._conn = None

    def call(self, method: str, params: dict[str, Any] | None = None) -> Any:
        if self._conn is None:
            raise IpcError("尚未连接", code="not_connected")
        request_id = uuid.uuid4().hex[:12]
        try:
            # `_encode` refuses an oversized frame here, before it reaches the pipe. A
            # client that sends one and relies on the server to reject it leaves the
            # connection in a state the server has to guess about.
            self._conn.send_bytes(_encode({"id": request_id, "method": method, "params": params or {}}))
        except (BrokenPipeError, ConnectionResetError, OSError) as exc:
            raise IpcError(f"连接已断开：{exc}", code="connection_lost") from exc
        if not self._conn.poll(self._timeout):
            raise IpcError(f"{method} 在 {self._timeout:.0f} 秒内没有响应", code="timeout")
        try:
            reply = json.loads(self._conn.recv_bytes(MAX_MESSAGE_BYTES).decode("utf-8"))
        except (OSError, EOFError, ValueError) as exc:
            raise IpcError(f"响应无法解析: {exc}", code="bad_reply") from exc

        if str(reply.get("id")) != request_id:
            # A reply for someone else's request is worse than no reply: it would be
            # attributed to this call and acted on.
            raise IpcError(
                f"响应 id 不匹配（发出 {request_id}，收到 {reply.get('id')}）", code="id_mismatch"
            )
        if not reply.get("ok"):
            error = reply.get("error") or {}
            raise IpcError(str(error.get("message") or "未知错误"), code=str(error.get("code") or "error"))
        return reply.get("result")


__all__ = [
    "DEFAULT_TIMEOUT_SECONDS",
    "MAX_MESSAGE_BYTES",
    "PIPE_NAME",
    "IpcError",
    "PipeClient",
    "PipeServer",
]
