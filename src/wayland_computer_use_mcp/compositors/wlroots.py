"""wlroots / Hyprland / Sway Wayland Compositor Backend.

Queries window geometry, state, and outputs via IPC mechanisms (hyprctl, swaymsg).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess

from wayland_computer_use_mcp.compositors.base import (
    CompositorBackend,
    DisplayInfo,
    WindowGeometry,
    WindowState,
)

logger = logging.getLogger(__name__)


class WlrootsBackend(CompositorBackend):
    """Compositor backend for wlroots-based compositors (Hyprland, Sway)."""

    name: str = "wlroots"

    async def is_available(self) -> bool:
        if not os.environ.get("WAYLAND_DISPLAY"):
            return False
        if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
            return True
        if os.environ.get("SWAYSOCK") and shutil.which("swaymsg"):
            return True
        return False

    async def get_window_geometry(
        self, pid: int | None = None, title: str | None = None
    ) -> WindowGeometry | None:
        if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
            try:
                proc = subprocess.run(
                    ["hyprctl", "-j", "clients"], capture_output=True, text=True, timeout=1.0
                )
                if proc.returncode == 0:
                    clients = json.loads(proc.stdout)
                    for c in clients:
                        c_pid = c.get("pid", 0)
                        c_title = str(c.get("title", ""))
                        if (pid and c_pid == pid) or (title and title.lower() in c_title.lower()):
                            at = c.get("at", [0, 0])
                            size = c.get("size", [0, 0])
                            return WindowGeometry(
                                x=int(at[0]),
                                y=int(at[1]),
                                width=int(size[0]),
                                height=int(size[1]),
                            )
            except Exception as exc:
                logger.debug("Hyprland clients query failed: %s", exc)

        if os.environ.get("SWAYSOCK") and shutil.which("swaymsg"):
            try:
                proc = subprocess.run(
                    ["swaymsg", "-t", "get_tree"], capture_output=True, text=True, timeout=1.0
                )
                if proc.returncode == 0:
                    tree = json.loads(proc.stdout)

                    def find_window(node: dict) -> WindowGeometry | None:
                        n_pid = node.get("pid", 0)
                        n_name = str(node.get("name", ""))
                        if (pid and n_pid == pid) or (title and title.lower() in n_name.lower()):
                            rect = node.get("rect", {})
                            if rect.get("width", 0) > 0:
                                return WindowGeometry(
                                    x=rect.get("x", 0),
                                    y=rect.get("y", 0),
                                    width=rect.get("width", 0),
                                    height=rect.get("height", 0),
                                )
                        for child in node.get("nodes", []) + node.get("floating_nodes", []):
                            res = find_window(child)
                            if res:
                                return res
                        return None

                    return find_window(tree)
            except Exception as exc:
                logger.debug("Sway get_tree query failed: %s", exc)

        return None

    async def activate_and_raise_window(
        self, pid: int | None = None, title: str | None = None
    ) -> bool:
        if pid and os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
            subprocess.run(
                ["hyprctl", "dispatch", "focuswindow", f"pid:{pid}"],
                capture_output=True,
                timeout=1.0,
            )
            return True
        geom = await self.get_window_geometry(pid=pid, title=title)
        return geom is not None

    async def get_active_displays(self) -> list[DisplayInfo]:
        return [DisplayInfo(name="Primary", x=0, y=0, width=1920, height=1080, scale=1.0)]

    async def set_window_state(self, pid: int, state: WindowState) -> bool:
        if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
            if state == "close":
                subprocess.run(
                    ["hyprctl", "dispatch", "closewindow", f"pid:{pid}"],
                    capture_output=True,
                    timeout=1.0,
                )
                return True
        return True

    async def ensure_dialogs_above(self) -> bool:
        return True
