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
        if os.environ.get("SWAYSOCK") and shutil.which("swaymsg"):
            criteria = f"[pid={pid}]" if pid else (f'[title="{title}"]' if title else "")
            if criteria:
                proc = subprocess.run(
                    ["swaymsg", f"{criteria} focus"],
                    capture_output=True,
                    timeout=1.0,
                )
                return proc.returncode == 0
        geom = await self.get_window_geometry(pid=pid, title=title)
        return geom is not None

    async def get_active_displays(self) -> list[DisplayInfo]:
        if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
            try:
                proc = subprocess.run(
                    ["hyprctl", "-j", "monitors"], capture_output=True, text=True, timeout=1.0
                )
                if proc.returncode == 0:
                    monitors = json.loads(proc.stdout)
                    displays = [
                        DisplayInfo(
                            name=str(m.get("name", "Monitor")),
                            x=int(m.get("x", 0)),
                            y=int(m.get("y", 0)),
                            width=int(m.get("width", 1920)),
                            height=int(m.get("height", 1080)),
                            scale=float(m.get("scale", 1.0)),
                        )
                        for m in sorted(
                            monitors, key=lambda item: not bool(item.get("focused", False))
                        )
                    ]
                    if displays:
                        return displays
            except Exception as exc:
                logger.debug("Hyprland monitors query failed: %s", exc)

        if os.environ.get("SWAYSOCK") and shutil.which("swaymsg"):
            try:
                proc = subprocess.run(
                    ["swaymsg", "-t", "get_outputs"], capture_output=True, text=True, timeout=1.0
                )
                if proc.returncode == 0:
                    outputs = json.loads(proc.stdout)
                    displays = []
                    for out in sorted(
                        outputs, key=lambda item: not bool(item.get("focused", False))
                    ):
                        if not out.get("active", True):
                            continue
                        rect = out.get("rect", {})
                        displays.append(
                            DisplayInfo(
                                name=str(out.get("name", "Output")),
                                x=int(rect.get("x", 0)),
                                y=int(rect.get("y", 0)),
                                width=int(rect.get("width", 1920)),
                                height=int(rect.get("height", 1080)),
                                scale=float(out.get("scale", 1.0)),
                            )
                        )
                    if displays:
                        return displays
            except Exception as exc:
                logger.debug("Sway outputs query failed: %s", exc)

        return [DisplayInfo(name="Primary", x=0, y=0, width=1920, height=1080, scale=1.0)]

    async def set_window_state(self, pid: int, state: WindowState) -> bool:
        if os.environ.get("HYPRLAND_INSTANCE_SIGNATURE") and shutil.which("hyprctl"):
            subprocess.run(
                ["hyprctl", "dispatch", "focuswindow", f"pid:{pid}"],
                capture_output=True,
                timeout=1.0,
            )
            if state == "close":
                subprocess.run(
                    ["hyprctl", "dispatch", "closewindow", f"pid:{pid}"],
                    capture_output=True,
                    timeout=1.0,
                )
            elif state in ("maximize", "restore"):
                subprocess.run(
                    ["hyprctl", "dispatch", "fullscreen", "1" if state == "maximize" else "0"],
                    capture_output=True,
                    timeout=1.0,
                )
            return True

        if os.environ.get("SWAYSOCK") and shutil.which("swaymsg"):
            cmd_map = {
                "close": f"[pid={pid}] kill",
                "maximize": f"[pid={pid}] fullscreen enable",
                "restore": f"[pid={pid}] fullscreen disable",
                "focus": f"[pid={pid}] focus",
                "minimize": f"[pid={pid}] move scratchpad",
            }
            sway_cmd = cmd_map.get(state)
            if sway_cmd:
                proc = subprocess.run(
                    ["swaymsg", sway_cmd],
                    capture_output=True,
                    timeout=1.0,
                )
                return proc.returncode == 0
        return True

    async def ensure_dialogs_above(self) -> bool:
        return True
