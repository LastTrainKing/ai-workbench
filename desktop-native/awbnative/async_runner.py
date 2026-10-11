"""One asyncio loop, on its own thread, for a Qt application.

Qt's event loop and asyncio's are different loops. `await`-ing a service call from a QML
slot would block the UI thread for the length of the call, and driving asyncio from Qt's
loop needs a third-party bridge. Neither is necessary: the service layer is async and
independent of Qt, so it gets one loop on one background thread, and results come back as
Qt signals, which Qt queues onto the UI thread for us.

Every call carries a request id. That is not decoration: two calls can be in flight, and
without an id a slow one and a fast one are indistinguishable when they land. The id is
also what a cancellation or a timeout can be attributed to.

This is the piece the WebView build got from HTTP: request/response correlation, timeouts
and error separation. Here it is explicit, in one file, instead of implied by a network
stack.
"""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any

from PySide6.QtCore import QObject, Signal


class AsyncRunner(QObject):
    """Runs coroutines on a private loop and reports back through signals."""

    #: (request_id, result)
    completed = Signal(str, object)
    #: (request_id, human-readable message, machine-readable code)
    failed = Signal(str, str, str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._loop = asyncio.new_event_loop()
        self._ready = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True, name="awb-async")
        self._thread.start()
        self._ready.wait(timeout=10)
        self._inflight: set[str] = set()

    # ------------------------------------------------------------------ plumbing --
    def _serve(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._ready.set()
        self._loop.run_forever()

    def submit(
        self,
        request_id: str,
        factory: Callable[[], Awaitable[Any]],
        *,
        timeout: float = 120.0,
    ) -> None:
        """Schedule ``factory()`` and emit `completed`/`failed` when it settles.

        `factory` and not a coroutine: a coroutine object is bound to the loop that
        created it, and building it here (on the UI thread) would attach it to the wrong
        one.
        """
        self._inflight.add(request_id)

        async def _wrapped() -> None:
            try:
                value = await asyncio.wait_for(factory(), timeout)
            except TimeoutError:
                self._inflight.discard(request_id)
                self.failed.emit(request_id, f"操作超时（{timeout:.0f} 秒）", "timeout")
                return
            except asyncio.CancelledError:
                self._inflight.discard(request_id)
                self.failed.emit(request_id, "操作已取消", "cancelled")
                raise
            except Exception as exc:  # noqa: BLE001 - the boundary reports, it does not decide
                self._inflight.discard(request_id)
                self.failed.emit(request_id, f"{type(exc).__name__}: {exc}", "error")
                return
            self._inflight.discard(request_id)
            self.completed.emit(request_id, value)

        asyncio.run_coroutine_threadsafe(_wrapped(), self._loop)

    def call_blocking(
        self, factory: Callable[[], Awaitable[Any]], *, timeout: float = 120.0
    ) -> Any:
        """Run a coroutine and wait for it. For verification and CLI use only.

        The UI never calls this — blocking the event loop is what `submit` exists to
        avoid — but a test that has no event loop needs a synchronous door.
        """
        future = asyncio.run_coroutine_threadsafe(factory(), self._loop)
        return future.result(timeout=timeout)

    def cancel_all(self) -> None:
        """Cancel everything still in flight. Called on shutdown."""
        for task in asyncio.all_tasks(self._loop):
            self._loop.call_soon_threadsafe(task.cancel)

    def shutdown(self) -> None:
        """Cancel what is still running, then stop the loop.

        `cancel_all` only *schedules* the cancellations. Stopping the loop in the same
        breath destroyed the tasks without ever delivering them, and asyncio said so once
        per abandoned call — `Task was destroyed but it is pending!`, on every exit, printed
        over the last thing the log had to say. Waiting for the in-flight count to reach
        zero is the same condition the interface uses to mean "nothing is running".
        """
        self.cancel_all()
        deadline = time.monotonic() + 2.0
        while self._inflight and time.monotonic() < deadline:
            time.sleep(0.02)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)

    @property
    def inflight(self) -> int:
        return len(self._inflight)
