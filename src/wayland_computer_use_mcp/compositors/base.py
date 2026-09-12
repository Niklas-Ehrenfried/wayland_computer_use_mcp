"""Base abstractions for Wayland Compositor backends.

Provides a unified, desktop-independent interface for window tracking,
geometry queries, activation/raising, and display metrics.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable


@dataclass(frozen=True)
class WindowGeometry:
    """Represents the physical and client geometry of an application window."""

    x: int
    y: int
    width: int
    height: int

    @property
    def tuple(self) -> tuple[int, int, int, int]:
        """Returns (x, y, width, height) tuple."""
        return (self.x, self.y, self.width, self.height)


@dataclass(frozen=True)
class DisplayInfo:
    """Information regarding a physical or virtual display output."""

    name: str
    x: int
    y: int
    width: int
    height: int
    scale: float = 1.0


WindowState = Literal["minimize", "maximize", "restore", "close"]


@runtime_checkable
class CompositorBackend(Protocol):
    """Abstract interface implemented by compositor backends (KWin, GNOME, wlroots, generic)."""

    name: str

    async def is_available(self) -> bool:
        """Determines if this compositor backend is active and reachable on the current session."""
        ...

    async def get_window_geometry(
        self, pid: int | None = None, title: str | None = None
    ) -> WindowGeometry | None:
        """Queries the exact geometry for a window identified by PID or caption."""
        ...

    async def activate_and_raise_window(
        self, pid: int | None = None, title: str | None = None
    ) -> bool:
        """Brings the specified window to the foreground, unminimizes, and focuses it."""
        ...

    async def get_active_displays(self) -> list[DisplayInfo]:
        """Queries the active display outputs and their scaling factors."""
        ...

    async def set_window_state(self, pid: int, state: WindowState) -> bool:
        """Alters the window state (minimize, maximize, restore, close)."""
        ...

    async def ensure_dialogs_above(self) -> bool:
        """Brings any system permission / portal dialogs to the foreground."""
        ...
