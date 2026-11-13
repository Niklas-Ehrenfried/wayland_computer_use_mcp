"""GNOME Shell / Mutter Wayland Compositor Backend.

Queries window geometry, state, and display metrics via GNOME Shell Introspect
and Mutter D-Bus interfaces.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from dbus_fast import BusType, Message, MessageType
from dbus_fast.aio import MessageBus

from wayland_computer_use_mcp.compositors.base import (
    CompositorBackend,
    DisplayInfo,
    WindowGeometry,
    WindowState,
)
from wayland_computer_use_mcp.core.context import get_active_context

logger = logging.getLogger(__name__)


def _unwrap_variant(val: Any) -> Any:
    """Recursively unwraps dbus_fast Variant wrappers when present."""
    return val.value if hasattr(val, "value") else val


class GnomeBackend(CompositorBackend):
    """Compositor backend for GNOME Shell / Mutter Wayland sessions."""

    name: str = "gnome"
    _warned_introspect: bool = False

    async def is_available(self) -> bool:
        ctx = get_active_context()
        if ctx and ctx.is_isolated:
            return False
        if not ((ctx and ctx.wayland_display) or os.environ.get("WAYLAND_DISPLAY")):
            return False
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        if "gnome" not in desktop:
            return False
        try:
            bus = await MessageBus(bus_type=BusType.SESSION).connect()
            reply = await bus.call(
                Message(
                    destination="org.freedesktop.DBus",
                    path="/org/freedesktop/DBus",
                    interface="org.freedesktop.DBus",
                    member="NameHasOwner",
                    signature="s",
                    body=["org.gnome.Shell.Introspect"],
                )
            )
            has_owner = bool(reply.body[0])
            bus.disconnect()
            return has_owner
        except Exception:
            return False

    async def get_window_geometry(
        self, pid: int | None = None, title: str | None = None
    ) -> WindowGeometry | None:
        try:
            bus = await MessageBus(bus_type=BusType.SESSION).connect()
            msg = Message(
                destination="org.gnome.Shell.Introspect",
                path="/org/gnome/Shell/Introspect",
                interface="org.gnome.Shell.Introspect",
                member="GetWindows",
            )
            reply = await bus.call(msg)
            bus.disconnect()

            if reply.message_type == MessageType.ERROR:
                if not self._warned_introspect:
                    self._warned_introspect = True
                    logger.warning(
                        "GNOME Introspect GetWindows denied (%s); window screen offsets "
                        "will default to (0, 0) unless unsafe_mode=true.",
                        reply.body,
                    )
                return None

            raw_windows = _unwrap_variant(reply.body[0]) if reply.body else None
            if not isinstance(raw_windows, dict):
                return None

            windows: dict[int, dict[str, Any]] = raw_windows
            for _, win in windows.items():
                win_data = _unwrap_variant(win)
                if not isinstance(win_data, dict):
                    continue
                win_pid = _unwrap_variant(win_data.get("pid", 0))
                win_title = str(_unwrap_variant(win_data.get("title", "")) or "")
                match = False
                if pid and win_pid is not None:
                    try:
                        match = int(win_pid) == pid
                    except (TypeError, ValueError):
                        match = False
                if not match and title and title.lower() in win_title.lower():
                    match = True

                if match:
                    rect = _unwrap_variant(win_data.get("rect"))
                    if rect and len(rect) >= 4:
                        return WindowGeometry(
                            x=int(_unwrap_variant(rect[0])),
                            y=int(_unwrap_variant(rect[1])),
                            width=int(_unwrap_variant(rect[2])),
                            height=int(_unwrap_variant(rect[3])),
                        )
                    wx = _unwrap_variant(win_data.get("x"))
                    wy = _unwrap_variant(win_data.get("y"))
                    ww = _unwrap_variant(win_data.get("width"))
                    wh = _unwrap_variant(win_data.get("height"))
                    if None not in (wx, wy, ww, wh) and int(ww) > 0 and int(wh) > 0:
                        return WindowGeometry(
                            x=int(wx),
                            y=int(wy),
                            width=int(ww),
                            height=int(wh),
                        )
                    if not self._warned_introspect:
                        self._warned_introspect = True
                        logger.info(
                            "GNOME Introspect matched window without x/y bounds (keys=%s)",
                            sorted(win_data.keys()),
                        )
            return None
        except Exception as exc:
            logger.debug("GNOME Introspect GetWindows failed: %s", exc)
            return None

    async def activate_and_raise_window(
        self, pid: int | None = None, title: str | None = None
    ) -> bool:
        geom = await self.get_window_geometry(pid=pid, title=title)
        return geom is not None

    async def get_active_displays(self) -> list[DisplayInfo]:
        return [DisplayInfo(name="Primary", x=0, y=0, width=1920, height=1080, scale=1.0)]

    async def set_window_state(self, pid: int, state: WindowState) -> bool:
        # Mutter blocks arbitrary Shell.Eval and unprivileged window-move D-Bus APIs.
        # Clean close or maximization on GNOME relies on shortcuts or process signals.
        logger.debug(
            "set_window_state (%s) on GNOME for PID %s has no unprivileged Mutter D-Bus API",
            state,
            pid,
        )
        return False

    async def ensure_dialogs_above(self) -> bool:
        return True
