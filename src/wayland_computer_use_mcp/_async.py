"""Shared async-to-sync bridge helpers."""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
from typing import Any


class PersistentLoop:
    """Thread-backed persistent asyncio loop enabling connection pooling across sync calls."""

    def __init__(self, name: str = "PersistentAsyncLoop") -> None:
        self._lock = threading.Lock()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.thread: threading.Thread | None = None
        self._name = name

    def _run_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()

    def _ensure_running(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if (
                self.loop is None
                or self.loop.is_closed()
                or self.thread is None
                or not self.thread.is_alive()
            ):
                new_loop = asyncio.new_event_loop()
                self.loop = new_loop
                self.thread = threading.Thread(
                    target=self._run_loop, args=(new_loop,), name=self._name, daemon=True
                )
                self.thread.start()
            return self.loop

    def run(self, coro: Any, timeout: float = 120.0) -> Any:
        loop = self._ensure_running()
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        return fut.result(timeout=timeout)

    def stop(self) -> None:
        with self._lock:
            if self.loop is not None and self.loop.is_running():
                self.loop.call_soon_threadsafe(self.loop.stop)
            if self.thread is not None and self.thread.is_alive():
                self.thread.join(timeout=2.0)
            if self.loop is not None and not self.loop.is_running() and not self.loop.is_closed():
                self.loop.close()
            self.loop = None
            self.thread = None


def run_async_sync(coro_fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Executes an async callable synchronously from either sync or async contexts."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(asyncio.run, coro_fn(*args, **kwargs)).result()
    return asyncio.run(coro_fn(*args, **kwargs))
