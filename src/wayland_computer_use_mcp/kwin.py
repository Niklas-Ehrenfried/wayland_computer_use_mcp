"""KWin Wayland Compositor D-Bus Scripting and Window Management.

Provides high-performance, non-polling window activation, keepAbove,
and exact client/buffer geometry queries on KDE Plasma 6.
"""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from typing import Any

from dbus_fast import BusType, Message
from dbus_fast.aio import MessageBus

logger = logging.getLogger(__name__)


async def query_kwin_geometry_async(
    pid: int | None = None, title: str | None = None, timeout: float = 1.0
) -> tuple[int, int, int, int] | None:
    """Interacts with KWin Scripting to focus target window and retrieve its geometry."""
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
    import json

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
            if (w && w.caption && w.caption.toLowerCase().indexOf(targetTitle.toLowerCase()) !== -1) {{
                var cap = w.caption;
                if (!cap.includes("Visual Studio") && !cap.includes("Cursor") && !cap.includes("Antigravity") && !cap.includes("Code")) {{
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
        var cg = w.frameGeometry || w.clientGeometry || w.bufferGeometry;
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

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as tf:
        tf.write(js_code)
        script_path = tf.name

    try:
        rep = await bus.call(
            Message(
                destination="org.kde.KWin",
                path="/Scripting",
                interface="org.kde.kwin.Scripting",
                member="loadScript",
                signature="s",
                body=[script_path],
            )
        )
        s_num = rep.body[0]
        await bus.call(
            Message(
                destination="org.kde.KWin",
                path=f"/Scripting/Script{s_num}",
                interface="org.kde.kwin.Script",
                member="run",
            )
        )
        geom = await asyncio.wait_for(fut, timeout=timeout)
        await bus.call(
            Message(
                destination="org.kde.KWin",
                path="/Scripting",
                interface="org.kde.kwin.Scripting",
                member="unloadScript",
                signature="s",
                body=[str(s_num)],
            )
        )
        return (int(geom[0]), int(geom[1]), int(geom[2]), int(geom[3]))
    except Exception as exc:
        logger.debug("KWin geometry query failed: %s", exc)
        return None
    finally:
        if os.path.exists(script_path):
            try:
                os.unlink(script_path)
            except OSError:
                pass
        bus.disconnect()


def query_kwin_geometry(
    pid: int | None = None, title: str | None = None, timeout: float = 1.0
) -> tuple[int, int, int, int] | None:
    """Synchronous wrapper for query_kwin_geometry_async."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                return ex.submit(
                    asyncio.run, query_kwin_geometry_async(pid, title, timeout)
                ).result()
        else:
            return loop.run_until_complete(query_kwin_geometry_async(pid, title, timeout))
    except Exception:
        try:
            return asyncio.run(query_kwin_geometry_async(pid, title, timeout))
        except Exception:
            return None


def set_kwin_keep_above(pid: int | None = None, title: str | None = None) -> bool:
    """Ensures window is focused and kept above others via KWin."""
    geom = query_kwin_geometry(pid, title)
    return geom is not None


async def run_kwin_script_async(js_code: str) -> bool:
    """Executes a transient KWin scripting snippet."""
    if not os.environ.get("WAYLAND_DISPLAY"):
        return False
    try:
        bus = await MessageBus(bus_type=BusType.SESSION).connect()
    except Exception:
        return False

    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as tf:
        tf.write(js_code)
        script_path = tf.name

    try:
        rep = await bus.call(
            Message(
                destination="org.kde.KWin",
                path="/Scripting",
                interface="org.kde.kwin.Scripting",
                member="loadScript",
                signature="s",
                body=[script_path],
            )
        )
        s_num = rep.body[0]
        await bus.call(
            Message(
                destination="org.kde.KWin",
                path=f"/Scripting/Script{s_num}",
                interface="org.kde.kwin.Script",
                member="run",
            )
        )
        await bus.call(
            Message(
                destination="org.kde.KWin",
                path="/Scripting",
                interface="org.kde.kwin.Scripting",
                member="unloadScript",
                signature="s",
                body=[str(s_num)],
            )
        )
        return True
    except Exception as exc:
        logger.debug("KWin script execution failed: %s", exc)
        return False
    finally:
        if os.path.exists(script_path):
            try:
                os.unlink(script_path)
            except OSError:
                pass
        bus.disconnect()


def run_kwin_script(js_code: str) -> bool:
    """Synchronously executes a KWin script snippet."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                return ex.submit(asyncio.run, run_kwin_script_async(js_code)).result()
        else:
            return loop.run_until_complete(run_kwin_script_async(js_code))
    except Exception:
        try:
            return asyncio.run(run_kwin_script_async(js_code))
        except Exception:
            return False


def ensure_portal_dialogs_above() -> bool:
    """Forces any xdg-desktop-portal permission dialogs to keepAbove and brings them forward."""
    js = """
    workspace.windowList().forEach(function(w) {
        if (w) {
            var isPortal = false;
            if (w.resourceClass === "xdg-desktop-portal-kde"
                || w.resourceName === "xdg-desktop-portal-kde") {
                isPortal = true;
            }
            if (w.caption && (
                w.caption.indexOf("Screen") !== -1 ||
                w.caption.indexOf("Remote") !== -1 ||
                w.caption.indexOf("Share") !== -1 ||
                w.caption.indexOf("Freigabe") !== -1 ||
                w.caption.indexOf("Zugriff") !== -1 ||
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
    });
    """
    return run_kwin_script(js)


def minimize_window(pid: int) -> bool:
    """Minimizes the target window by PID so dialogs are unobstructed."""
    if not pid:
        return False
    js = f"""
    workspace.windowList().forEach(function(w) {{
        if (w && w.pid === {pid}) {{
            w.minimized = true;
        }}
    }});
    """
    return run_kwin_script(js)


def restore_window(pid: int) -> bool:
    """Unminimizes, raises, and focuses target window by PID."""
    if not pid:
        return False
    js = f"""
    workspace.windowList().forEach(function(w) {{
        if (w && w.pid === {pid}) {{
            w.minimized = false;
            w.keepAbove = true;
            workspace.activeWindow = w;
        }}
    }});
    """
    return run_kwin_script(js)
