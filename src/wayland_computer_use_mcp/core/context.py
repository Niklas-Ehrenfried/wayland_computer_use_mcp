"""Explicit session execution context and input sink protocol."""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Any, Literal, Protocol, runtime_checkable

SessionMode = Literal["blackbox", "passthrough", "mock"]


@dataclass(frozen=True)
class SessionContext:
    """Explicit display, D-Bus, and runtime environment for a managed session."""

    mode: SessionMode = "blackbox"
    xdg_runtime_dir: str | None = None
    dbus_address: str | None = None
    wayland_display: str | None = None
    atspi_bus_address: str | None = None
    host_wayland_display: str | None = None
    headed: bool = False
    is_isolated: bool = False

    def wayland_socket_path(self) -> str | None:
        """Returns resolved UNIX socket path for this session's Wayland display."""
        wl = self.wayland_display or os.environ.get("WAYLAND_DISPLAY")
        if not wl:
            return None
        if os.path.isabs(wl):
            return wl
        runtime_dir = (
            self.xdg_runtime_dir or os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        )
        return os.path.join(runtime_dir, wl)

    def build_subprocess_env(self, base_env: dict[str, str] | None = None) -> dict[str, str]:
        """Construct an explicit environment dict for child processes without global mutation."""
        env = dict(base_env if base_env is not None else os.environ)
        if self.xdg_runtime_dir:
            env["XDG_RUNTIME_DIR"] = self.xdg_runtime_dir
        if self.dbus_address:
            env["DBUS_SESSION_BUS_ADDRESS"] = self.dbus_address
        if self.wayland_display:
            env["WAYLAND_DISPLAY"] = self.wayland_display
        if self.atspi_bus_address:
            env["AT_SPI_BUS_ADDRESS"] = self.atspi_bus_address
        elif self.is_isolated:
            env.pop("AT_SPI_BUS_ADDRESS", None)
        return env


@runtime_checkable
class InputSink(Protocol):
    """Protocol shared by libei EIClient and Wayland virtual-input wire backends."""

    has_absolute_pointer: bool
    keyboard_device: Any

    def pointer_motion_absolute(self, x: float, y: float) -> Any: ...
    def button_down(self, button_code: int = 0x110) -> Any: ...
    def button_up(self, button_code: int = 0x110) -> Any: ...
    def button_click(self, button_code: int = 0x110) -> Any: ...
    def double_click(self, button_code: int = 0x110) -> Any: ...
    def scroll(self, dx: float, dy: float) -> Any: ...
    def type_char(self, ch: str) -> bool: ...
    def key_press(self, keycode: int, is_down: bool) -> Any: ...
    def key_combination(self, keycodes: list[int]) -> Any: ...
    def can_type_text(self, text: str) -> bool: ...
    def resolve_key_name(self, key_name: str) -> int | None: ...
    def close(self) -> None: ...


_ctx_lock = threading.Lock()
_active_context: SessionContext | None = None


def set_active_context(ctx: SessionContext | None) -> None:
    """Sets the active SessionContext used by runtime helpers."""
    global _active_context
    with _ctx_lock:
        _active_context = ctx


def get_active_context() -> SessionContext | None:
    """Returns the currently registered SessionContext, if any."""
    with _ctx_lock:
        return _active_context
