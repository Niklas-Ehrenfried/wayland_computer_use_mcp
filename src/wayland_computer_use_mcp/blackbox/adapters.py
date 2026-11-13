"""Compositor adapters inside the isolated Blackbox sandbox."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from typing import Any, Protocol, runtime_checkable

from PIL import Image

from wayland_computer_use_mcp.blackbox.wlr_wire import (
    WlrVirtualInputClient,
    capture_wlr_screencopy,
)
from wayland_computer_use_mcp.core.context import InputSink, SessionContext
from wayland_computer_use_mcp.libei import EIClient
from wayland_computer_use_mcp.portal.screencast import capture_native_desktop_frame

logger = logging.getLogger(__name__)


@runtime_checkable
class BlackboxCompositorAdapter(Protocol):
    """Abstraction over the compositor running inside the Blackbox sandbox."""

    name: str

    def is_available(self) -> bool: ...

    def build_command(
        self,
        headed: bool,
        socket_name: str,
        host_wayland_display: str | None,
        width: int = 1280,
        height: int = 800,
        config_dir: str | None = None,
    ) -> str | None: ...

    async def connect_input(
        self, ctx: SessionContext, width: int = 1280, height: int = 800
    ) -> tuple[InputSink | None, str]: ...

    def capture_output_frame(self, ctx: SessionContext) -> Image.Image | None: ...


class KWinNestedAdapter:
    """KDE KWin nested/headless compositor adapter with direct EIS."""

    name: str = "kwin"

    def __init__(self) -> None:
        self._eis_bus: Any = None

    def is_available(self) -> bool:
        return bool(shutil.which("kwin_wayland"))

    def build_command(
        self,
        headed: bool,
        socket_name: str,
        host_wayland_display: str | None,
        width: int = 1280,
        height: int = 800,
        config_dir: str | None = None,
    ) -> str | None:
        if not self.is_available():
            return None
        flags = (
            f"--wayland-display {host_wayland_display}"
            if headed and host_wayland_display
            else "--virtual"
        )
        return (
            f"kwin_wayland {flags} --xwayland --no-lockscreen "
            f"--no-global-shortcuts --width {width} --height {height} --socket {socket_name}"
        )

    async def connect_input(
        self, ctx: SessionContext, width: int = 1280, height: int = 800
    ) -> tuple[InputSink | None, str]:
        if not ctx.dbus_address:
            return None, "kwin_no_dbus"
        from dbus_fast import Message, MessageType
        from dbus_fast.aio import MessageBus

        self.close()
        bus = await MessageBus(bus_address=ctx.dbus_address, negotiate_unix_fd=True).connect()
        keep_bus = False
        try:
            kwin_ready = False
            for _ in range(30):
                reply = await bus.call(
                    Message(
                        destination="org.freedesktop.DBus",
                        path="/org/freedesktop/DBus",
                        interface="org.freedesktop.DBus",
                        member="NameHasOwner",
                        signature="s",
                        body=["org.kde.KWin"],
                    )
                )
                if reply.body and reply.body[0]:
                    kwin_ready = True
                    break
                await asyncio.sleep(0.1)

            if not kwin_ready:
                return None, "kwin_unavailable"

            eis_reply = await asyncio.wait_for(
                bus.call(
                    Message(
                        destination="org.kde.KWin",
                        path="/org/kde/KWin/EIS/RemoteDesktop",
                        interface="org.kde.KWin.EIS.RemoteDesktop",
                        member="connectToEIS",
                        signature="i",
                        body=[7],
                    )
                ),
                timeout=3.0,
            )
            if eis_reply.message_type != MessageType.ERROR and eis_reply.unix_fds:
                self._eis_bus = bus
                keep_bus = True
                fd = os.dup(int(eis_reply.unix_fds[0]))
                return EIClient(fd=fd), "kwin_eis"
            return None, "kwin_no_eis"
        finally:
            if not keep_bus:
                bus.disconnect()

    def capture_output_frame(self, ctx: SessionContext) -> Image.Image | None:
        return capture_native_desktop_frame()

    def close(self) -> None:
        if self._eis_bus is not None:
            try:
                self._eis_bus.disconnect()
            except Exception:
                pass
            self._eis_bus = None


class WlrootsNestedAdapter:
    """Portable wlroots compositor adapter (labwc / sway) using wire protocols."""

    name: str = "wlroots"

    def __init__(self) -> None:
        self._bin: str | None = self._detect_bin()

    @staticmethod
    def _detect_bin() -> str | None:
        for candidate in ("labwc", "sway"):
            if shutil.which(candidate):
                return candidate
        return None

    def is_available(self) -> bool:
        return self._bin is not None

    def build_command(
        self,
        headed: bool,
        socket_name: str,
        host_wayland_display: str | None,
        width: int = 1280,
        height: int = 800,
        config_dir: str | None = None,
    ) -> str | None:
        binary = self._bin or self._detect_bin()
        if not binary:
            return None
        if binary == "labwc":
            return f"labwc -C {config_dir}" if config_dir else "labwc"
        if binary == "sway":
            return "sway -c /dev/null"
        return binary

    async def connect_input(
        self, ctx: SessionContext, width: int = 1280, height: int = 800
    ) -> tuple[InputSink | None, str]:
        sock_path = ctx.wayland_socket_path()
        if not sock_path:
            return None, "wlroots_no_socket"
        sink = WlrVirtualInputClient(sock_path=sock_path, extent_w=width, extent_h=height)
        if sink.has_absolute_pointer or sink.keyboard_device:
            return sink, "wlroots_wire"
        sink.close()
        return None, "wlroots_no_input"

    def capture_output_frame(self, ctx: SessionContext) -> Image.Image | None:
        sock_path = ctx.wayland_socket_path()
        if sock_path:
            img = capture_wlr_screencopy(sock_path)
            if img is not None:
                return img
        return capture_native_desktop_frame()

    def close(self) -> None:
        return None


class WestonNestedAdapter:
    """Minimal Weston nested/headless compositor fallback."""

    name: str = "weston"

    def is_available(self) -> bool:
        return bool(shutil.which("weston"))

    def build_command(
        self,
        headed: bool,
        socket_name: str,
        host_wayland_display: str | None,
        width: int = 1280,
        height: int = 800,
        config_dir: str | None = None,
    ) -> str | None:
        if not self.is_available():
            return None
        if headed:
            return f"weston --backend=wayland-backend.so --socket={socket_name}"
        return f"weston --backend=headless-backend.so --socket={socket_name}"

    async def connect_input(
        self, ctx: SessionContext, width: int = 1280, height: int = 800
    ) -> tuple[InputSink | None, str]:
        return None, "weston_a11y_only"

    def capture_output_frame(self, ctx: SessionContext) -> Image.Image | None:
        sock_path = ctx.wayland_socket_path()
        if sock_path:
            img = capture_wlr_screencopy(sock_path)
            if img is not None:
                return img
        return capture_native_desktop_frame()

    def close(self) -> None:
        return None


def select_blackbox_adapter(explicit_cmd: str | None = None) -> BlackboxCompositorAdapter:
    """Selects best available blackbox nested compositor adapter for this host."""
    if explicit_cmd and explicit_cmd.strip().lower() != "auto":
        cmd_head = explicit_cmd.split()[0]
        if "kwin_wayland" in cmd_head:
            return KWinNestedAdapter()
        if any(name in cmd_head for name in ("labwc", "sway")):
            return WlrootsNestedAdapter()
        return WestonNestedAdapter()

    for adapter in (KWinNestedAdapter(), WlrootsNestedAdapter(), WestonNestedAdapter()):
        if adapter.is_available():
            return adapter
    return WestonNestedAdapter()
