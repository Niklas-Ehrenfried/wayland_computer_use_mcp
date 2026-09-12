"""KWin Wayland Compositor Backend.

Uses KWin Scripting over D-Bus to inspect window lists, retrieve client geometry,
raise/focus target applications, and control window states on KDE Plasma 6.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
from typing import Any

from dbus_fast import BusType, Message
from dbus_fast.aio import MessageBus

from wayland_computer_use_mcp.compositors.base import (
    CompositorBackend,
    DisplayInfo,
    WindowGeometry,
    WindowState,
)

logger = logging.getLogger(__name__)


class KWinBackend(CompositorBackend):
    """Compositor backend implementation for KDE Plasma 6 / KWin Wayland."""

    name: str = "kwin"

    async def is_available(self) -> bool:
        if not os.environ.get("WAYLAND_DISPLAY"):
            return False
        desktop = os.environ.get("XDG_CURRENT_DESKTOP", "").lower()
        if "kde" not in desktop and "plasma" not in desktop:
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
                    body=["org.kde.KWin"],
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
        if not os.environ.get("WAYLAND_DISPLAY"):
            return None

        try:
            bus = await MessageBus(bus_type=BusType.SESSION).connect()
        except Exception as exc:
            logger.debug("Could not connect to session bus for KWin scripting: %s", exc)
            return None

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[list[Any]] = loop.create_future()

        def msg_handler(msg: Message) -> bool:
            if msg.path == "/ReportGeom" and msg.member == "Report":
                if not fut.done():
                    fut.set_result(msg.body)
                bus.send(Message.new_method_return(msg, "s", ["ok"]))
                return True
            return False

        bus.add_message_handler(msg_handler)

        target_pid = pid or 0
        target_title = title or ""
        target_title_js = json.dumps(target_title)
        js_code = f"""
        var targetTitle = {target_title_js};
        var targetPid = {target_pid};
        var matchedWindow = null;
        var windows = workspace.windowList();
        if (targetPid > 0) {{
            for (var i = 0; i < windows.length; i++) {{
                var w = windows[i];
                if (w && w.pid === targetPid) {{
                    matchedWindow = w;
                    break;
                }}
            }}
        }}
        if (!matchedWindow && targetTitle && targetTitle.length > 0) {{
            for (var i = 0; i < windows.length; i++) {{
                var w = windows[i];
                var matches = w && w.caption &&
                    w.caption.toLowerCase().indexOf(targetTitle.toLowerCase()) !== -1;
                if (matches) {{
                    var cap = w.caption;
                    var isIde = cap.includes("Visual Studio") || cap.includes("Cursor") ||
                        cap.includes("Antigravity") || cap.includes("Code");
                    if (!isIde) {{
                        matchedWindow = w;
                        break;
                    }}
                }}
            }}
        }}
        if (matchedWindow) {{
            var w = matchedWindow;
            if (w.minimized) {{
                w.minimized = false;
            }}
            w.keepAbove = true;
            workspace.activeWindow = w;
            var cg = w.clientGeometry || w.bufferGeometry || w.frameGeometry;
            var gx = (cg ? cg.x : w.x) || 0;
            var gy = (cg ? cg.y : w.y) || 0;
            var gw = (cg ? cg.width : w.width) || 0;
            var gh = (cg ? cg.height : w.height) || 0;
            if (gw > 0 && gh > 0) {{
                callDBus(
                    '{bus.unique_name}',
                    '/ReportGeom',
                    'org.wayland.Mcp',
                    'Report',
                    Math.round(gx),
                    Math.round(gy),
                    Math.round(gw),
                    Math.round(gh)
                );
            }}
        }}
        """

        script_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
                f.write(js_code)
                script_path = f.name

            load_msg = Message(
                destination="org.kde.KWin",
                path="/Scripting",
                interface="org.kde.kwin.Scripting",
                member="loadScript",
                signature="s",
                body=[script_path],
            )
            load_reply = await bus.call(load_msg)
            if not load_reply.body or load_reply.body[0] < 0:
                return None

            script_id = load_reply.body[0]
            run_msg = Message(
                destination="org.kde.KWin",
                path=f"/Scripting/Script{script_id}",
                interface="org.kde.kwin.Script",
                member="run",
            )
            await bus.call(run_msg)

            res = await asyncio.wait_for(fut, timeout=1.0)
            if res and len(res) >= 4:
                return WindowGeometry(
                    x=int(res[0]), y=int(res[1]), width=int(res[2]), height=int(res[3])
                )
            return None
        except Exception as exc:
            logger.debug("KWin script geometry evaluation timed out or failed: %s", exc)
            return None
        finally:
            if script_path and os.path.exists(script_path):
                try:
                    os.unlink(script_path)
                except OSError:
                    pass
            bus.disconnect()

    async def activate_and_raise_window(
        self, pid: int | None = None, title: str | None = None
    ) -> bool:
        geom = await self.get_window_geometry(pid=pid, title=title)
        return geom is not None

    async def get_active_displays(self) -> list[DisplayInfo]:
        # Fallback to standard primary geometry if DBus outputs query is unneeded
        return [DisplayInfo(name="Primary", x=0, y=0, width=1920, height=1080, scale=1.0)]

    async def set_window_state(self, pid: int, state: WindowState) -> bool:
        if not os.environ.get("WAYLAND_DISPLAY") or pid <= 0:
            return False

        try:
            bus = await MessageBus(bus_type=BusType.SESSION).connect()
        except Exception:
            return False

        js_action = ""
        if state == "minimize":
            js_action = "w.minimized = true;"
        elif state == "maximize":
            js_action = "w.setMaximize(true, true);"
        elif state == "restore":
            js_action = (
                "w.minimized = false; w.setMaximize(false, false); workspace.activeWindow = w;"
            )
        elif state == "close":
            js_action = "w.closeWindow();"

        js_code = f"""
        var windows = workspace.windowList();
        for (var i = 0; i < windows.length; i++) {{
            var w = windows[i];
            if (w && w.pid === {pid}) {{
                {js_action}
                break;
            }}
        }}
        """

        script_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
                f.write(js_code)
                script_path = f.name

            load_msg = Message(
                destination="org.kde.KWin",
                path="/Scripting",
                interface="org.kde.kwin.Scripting",
                member="loadScript",
                signature="s",
                body=[script_path],
            )
            load_reply = await bus.call(load_msg)
            if not load_reply.body or load_reply.body[0] < 0:
                return False

            script_id = load_reply.body[0]
            run_msg = Message(
                destination="org.kde.KWin",
                path=f"/Scripting/Script{script_id}",
                interface="org.kde.kwin.Script",
                member="run",
            )
            await bus.call(run_msg)
            return True
        except Exception as exc:
            logger.debug("Failed to set KWin window state: %s", exc)
            return False
        finally:
            if script_path and os.path.exists(script_path):
                try:
                    os.unlink(script_path)
                except OSError:
                    pass
            bus.disconnect()

    async def ensure_dialogs_above(self) -> bool:
        if not os.environ.get("WAYLAND_DISPLAY"):
            return False

        try:
            bus = await MessageBus(bus_type=BusType.SESSION).connect()
        except Exception:
            return False

        js_code = """
        var windows = workspace.windowList();
        for (var i = 0; i < windows.length; i++) {
            var w = windows[i];
            if (!w) continue;
            var isPortal = false;
            if (w.resourceClass === "xdg-desktop-portal-kde" ||
                w.resourceClass === "xdg-desktop-portal-gnome" ||
                w.resourceClass === "xdg-desktop-portal-gtk") {
                isPortal = true;
            }
            if (w.caption && (
                w.caption.indexOf("Screen") !== -1 ||
                w.caption.indexOf("Remote") !== -1 ||
                w.caption.indexOf("Share") !== -1 ||
                w.caption.indexOf("Freigabe") !== -1 ||
                w.caption.indexOf("Desktop") !== -1
            )) {
                isPortal = true;
            }
            if (isPortal) {
                w.keepAbove = true;
                w.minimized = false;
                workspace.activeWindow = w;
            }
        }
        """

        script_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as f:
                f.write(js_code)
                script_path = f.name

            load_msg = Message(
                destination="org.kde.KWin",
                path="/Scripting",
                interface="org.kde.kwin.Scripting",
                member="loadScript",
                signature="s",
                body=[script_path],
            )
            load_reply = await bus.call(load_msg)
            if not load_reply.body or load_reply.body[0] < 0:
                return False

            script_id = load_reply.body[0]
            run_msg = Message(
                destination="org.kde.KWin",
                path=f"/Scripting/Script{script_id}",
                interface="org.kde.kwin.Script",
                member="run",
            )
            await bus.call(run_msg)
            return True
        except Exception:
            return False
        finally:
            if script_path and os.path.exists(script_path):
                try:
                    os.unlink(script_path)
                except OSError:
                    pass
            bus.disconnect()
