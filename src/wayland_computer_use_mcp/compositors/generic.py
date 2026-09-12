"""Generic Wayland Compositor Fallback Backend.

Operates purely within Wayland and XDG Desktop Portal standards when no
compositor-specific IPC (KWin, GNOME, wlroots) is detected.
"""

from __future__ import annotations

import logging

from wayland_computer_use_mcp.compositors.base import (
    CompositorBackend,
    DisplayInfo,
    WindowGeometry,
    WindowState,
)

logger = logging.getLogger(__name__)


class GenericCompositorBackend(CompositorBackend):
    """Generic fallback compositor backend."""

    name: str = "generic"

    async def is_available(self) -> bool:
        return True

    async def get_window_geometry(
        self, _pid: int | None = None, _title: str | None = None
    ) -> WindowGeometry | None:
        # Generic Wayland does not expose window geometry across security boundaries
        return None

    async def activate_and_raise_window(
        self, _pid: int | None = None, _title: str | None = None
    ) -> bool:
        return False

    async def get_active_displays(self) -> list[DisplayInfo]:
        return [DisplayInfo(name="Default", x=0, y=0, width=1920, height=1080, scale=1.0)]

    async def set_window_state(self, _pid: int, _state: WindowState) -> bool:
        return False

    async def ensure_dialogs_above(self) -> bool:
        return False
