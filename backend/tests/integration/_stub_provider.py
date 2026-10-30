"""A local OpenAI-compatible endpoint whose replies are scripted.

**Not a mock object.** This is a real HTTP server on a real loopback socket, and the real
`OpenAICompatibleProvider` talks to it through the real transport. That matters: the
behaviours these tests exist for — a timeout, a 500, a body that is not JSON, a response
with no `choices` — live in the transport and the parsing, and a monkeypatched provider
would skip every one of them.

The replies are scripted per test. Each scripted reply is one completion; the script is
consumed in order and the last entry repeats, which is what makes the loop and step-ceiling
cases expressible.

No API key is involved and none is needed: the endpoint is on this machine, and the guard
in `app.ai.providers.guard` allows loopback for exactly this reason.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any


def answer(text: str = "完成了。") -> dict[str, Any]:
    """A normal completion with prose and no tool call."""
    return {"choices": [{"message": {"content": text}, "finish_reason": "stop"}]}


def empty() -> dict[str, Any]:
    """A completion whose content is the empty string."""
    return {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]}


def no_choices() -> dict[str, Any]:
    """A 200 response with the expected shape missing its `choices` array."""
    return {"id": "stub", "object": "chat.completion"}


def tool_call(
    name: str, arguments: dict[str, Any] | None = None, call_id: str = "call_1"
) -> dict[str, Any]:
    """A completion asking for one tool call."""
    return {
        "choices": [
            {
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "id": call_id,
                            "type": "function",
                            "function": {
                                "name": name,
                                "arguments": json.dumps(arguments or {}),
                            },
                        }
                    ],
                },
                "finish_reason": "tool_calls",
            }
        ]
    }


def repeated_tool_call(name: str = "project_list") -> Callable[[int], dict[str, Any]]:
    """The *same* tool call every time — the loop the executor is supposed to detect."""
    return lambda _n: tool_call(name, {}, call_id="call_fixed")


def varying_tool_call(name: str = "project_list") -> Callable[[int], dict[str, Any]]:
    """A different tool call each time, so the loop guard never fires and the step
    ceiling is what stops the run."""
    return lambda n: tool_call(name, {"offset": n}, call_id=f"call_{n}")


class StubProvider:
    """A scripted OpenAI-compatible endpoint on loopback.

    Usage::

        with StubProvider([empty()]) as stub:
            point_the_account_at(stub.base_url)
            ...  # every reply comes from the script
    """

    def __init__(
        self,
        script: list[dict[str, Any] | Callable[[int], dict[str, Any]]],
        *,
        delay_seconds: float = 0.0,
        status: int = 200,
        raw_body: str | None = None,
        chunked_slow: float = 0.0,
        stream_pieces: list[str] | None = None,
        stream_usage: dict[str, int] | None = None,
        stream_garbage: str = "",
    ) -> None:
        self.script = script
        self.delay_seconds = delay_seconds
        self.status = status
        #: When set, the response body is this text rather than JSON — the "not JSON at
        #: all" case, which has to be distinguishable from a JSON body of the wrong shape.
        self.raw_body = raw_body
        #: Sleep this long *per completion* when the script is exhausted, for the
        #: mid-run-kill case where the child has to be alive when it is killed.
        self.chunked_slow = chunked_slow
        #: Server-sent events. When set, a request with `"stream": true` gets these as
        #: `data:` frames followed by `[DONE]`.
        self.stream_pieces = stream_pieces
        self.stream_usage = stream_usage
        #: A deliberately malformed frame, to check it is skipped rather than fatal.
        self.stream_garbage = stream_garbage
        self.requests: list[dict[str, Any]] = []
        self._calls = 0
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ lifecycle --
    @property
    def base_url(self) -> str:
        if self._server is None:
            raise RuntimeError("stub not started")
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/v1"

    def start(self) -> StubProvider:
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: Any) -> None:
                return

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                try:
                    stub.requests.append(json.loads(raw.decode("utf-8")))
                except (ValueError, UnicodeDecodeError):
                    stub.requests.append({"_unparseable": raw[:200].decode("utf-8", "replace")})

                if stub.delay_seconds:
                    time.sleep(stub.delay_seconds)

                if stub.status >= 400:
                    stub._respond(self, stub.status, json.dumps({"error": "scripted failure"}))
                    return
                if stub.raw_body is not None:
                    stub._respond(self, 200, stub.raw_body)
                    return

                if stub.stream_pieces is not None and stub._wants_stream():
                    stub._respond_stream(self)
                    return

                index = stub._calls
                stub._calls += 1
                if stub.chunked_slow and index >= len(stub.script):
                    time.sleep(stub.chunked_slow)

                entry = stub.script[min(index, len(stub.script) - 1)]
                payload = entry(index) if callable(entry) else entry
                stub._respond(self, 200, json.dumps(payload))

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True, name="stub-provider"
        )
        self._thread.start()
        return self

    def _wants_stream(self) -> bool:
        """Whether the request asked for server-sent events.

        Read from the request body the handler stored, so a caller that does not set
        `"stream": true` still gets the ordinary JSON path. That matters: the same stub
        then covers both transports, and a test cannot pass by accident on the wrong one.
        """
        if not self.requests:
            return False
        return bool(self.requests[-1].get("stream"))

    def _respond_stream(self, handler: BaseHTTPRequestHandler) -> None:
        """Write the scripted pieces as SSE frames, then the terminator."""
        frames: list[str] = []
        for piece in self.stream_pieces or []:
            frames.append(
                "data: "
                + json.dumps(
                    {"choices": [{"delta": {"content": piece}, "finish_reason": None}]},
                    ensure_ascii=False,
                )
                + "\n\n"
            )
        if self.stream_garbage:
            frames.append(f"data: {self.stream_garbage}\n\n")
        frames.append(
            "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]}) + "\n\n"
        )
        if self.stream_usage is not None:
            # The usage frame has an EMPTY choices array — the shape that crashes a parser
            # which indexes choices[0] unconditionally on the last frame of every stream.
            frames.append(
                "data: " + json.dumps({"choices": [], "usage": self.stream_usage}) + "\n\n"
            )
        frames.append("data: [DONE]\n\n")

        body = "".join(frames).encode("utf-8")
        handler.send_response(200)
        handler.send_header("Content-Type", "text/event-stream")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def _respond(self, handler: BaseHTTPRequestHandler, status: int, body: str) -> None:
        data = body.encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def __enter__(self) -> StubProvider:
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.stop()

    @property
    def call_count(self) -> int:
        return self._calls
