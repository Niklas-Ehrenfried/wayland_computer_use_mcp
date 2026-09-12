"""Compositor Abstraction Package for Wayland Computer Use.

Provides unified, desktop-agnostic window tracking, geometry queries,
and activation for KDE Plasma, GNOME, Hyprland, Sway, and generic Wayland.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from wayland_computer_use_mcp.compositors.base import (
    CompositorBackend,
    DisplayInfo,
    WindowGeometry,
    WindowState,
)
from wayland_computer_use_mcp.compositors.generic import GenericCompositorBackend
from wayland_computer_use_mcp.compositors.gnome import GnomeBackend
from wayland_computer_use_mcp.compositors.kwin import KWinBackend
from wayland_computer_use_mcp.compositors.wlroots import WlrootsBackend

logger = logging.getLogger(__name__)

_cached_backend: CompositorBackend | None = None


async def get_compositor_backend() -> CompositorBackend:
    """Auto-detects and returns the active compositor backend."""
    global _cached_backend
    if _cached_backend is not None:
        return _cached_backend

    backends: list[CompositorBackend] = [
        KWinBackend(),
        GnomeBackend(),
        WlrootsBackend(),
    ]

    for backend in backends:
        try:
            if await backend.is_available():
                logger.info("Selected Wayland compositor backend: %s", backend.name)
                _cached_backend = backend
                return _cached_backend
        except Exception as exc:
            logger.debug("Backend %s availability check failed: %s", backend.name, exc)

    logger.info("Falling back to GenericCompositorBackend")
    _cached_backend = GenericCompositorBackend()
    return _cached_backend


def reset_compositor_backend() -> None:
    """Resets the cached compositor backend (useful for tests)."""
    global _cached_backend
    _cached_backend = None


def _run_async_sync(coro_fn: Any, *args: Any, **kwargs: Any) -> Any:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            return ex.submit(asyncio.run, coro_fn(*args, **kwargs)).result()
    else:
        return asyncio.run(coro_fn(*args, **kwargs))


async def _query_geom_async(pid: int | None, title: str | None) -> tuple[int, int, int, int] | None:
    backend = await get_compositor_backend()
    geom = await backend.get_window_geometry(pid=pid, title=title)
    return geom.tuple if geom else None


def query_window_geometry(
    pid: int | None = None, title: str | None = None
) -> tuple[int, int, int, int] | None:
    """Synchronous helper querying window geometry across any active Wayland compositor."""
    try:
        return _run_async_sync(_query_geom_async, pid, title)
    except Exception:
        return None


async def _activate_async(pid: int | None, title: str | None) -> bool:
    backend = await get_compositor_backend()
    return await backend.activate_and_raise_window(pid=pid, title=title)


def activate_and_raise_window(pid: int | None = None, title: str | None = None) -> bool:
    """Synchronous helper activating and raising a window across any active Wayland compositor."""
    try:
        return bool(_run_async_sync(_activate_async, pid, title))
    except Exception:
        return False


async def _set_state_async(pid: int, state: WindowState) -> bool:
    backend = await get_compositor_backend()
    return await backend.set_window_state(pid=pid, state=state)


def set_window_state(pid: int, state: WindowState) -> bool:
    """Synchronous helper controlling window state (minimize, maximize, restore, close)."""
    try:
        return bool(_run_async_sync(_set_state_async, pid, state))
    except Exception:
        return False


def minimize_window(pid: int) -> bool:
    """Convenience helper to minimize window."""
    return set_window_state(pid, "minimize")


def restore_window(pid: int) -> bool:
    """Convenience helper to restore and raise window."""
    return set_window_state(pid, "restore")


async def _ensure_dialogs_above_async() -> bool:
    backend = await get_compositor_backend()
    return await backend.ensure_dialogs_above()


def ensure_portal_dialogs_above() -> bool:
    """Synchronous helper to bring system portal/permission dialogs to the foreground."""
    try:
        return bool(_run_async_sync(_ensure_dialogs_above_async))
    except Exception:
        return False


__all__ = [
    "CompositorBackend",
    "DisplayInfo",
    "GenericCompositorBackend",
    "GnomeBackend",
    "KWinBackend",
    "WindowGeometry",
    "WindowState",
    "WlrootsBackend",
    "activate_and_raise_window",
    "ensure_portal_dialogs_above",
    "get_compositor_backend",
    "minimize_window",
    "query_window_geometry",
    "reset_compositor_backend",
    "restore_window",
    "set_window_state",
]
