"""FastMCP server definitions and entry point for wayland-computer-use-mcp.

Exposes the interactive GUI testing and desktop integration suite for Wayland:
- Process lifecycle (launch, restart, terminate, logs, liveness)
- Visual & semantic tree inspection (frame capture, AT-SPI2 tree traversal)
- Clamped input injection (click, drag, scroll, type_text)
- OS Desktop integration (install_to_desktop, uninstall_from_desktop)
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from mcp.types import ImageContent, TextContent

from wayland_computer_use_mcp.a11y import get_application_tree
from wayland_computer_use_mcp.config import get_config
from wayland_computer_use_mcp.installer import install_desktop_app, remove_desktop_app
from wayland_computer_use_mcp.portal import global_portal_session
from wayland_computer_use_mcp.process import (
    get_logs,
    is_responsive,
    launch,
    restart,
    terminate,
)

logger = logging.getLogger("wayland_computer_use_mcp")


def set_desktop_window_keep_above(pid: int | None = None, title: str | None = None) -> bool:
    """Uses KWin desktop scripting to set the target window keepAbove=true and raise it."""
    from wayland_computer_use_mcp.kwin import set_kwin_keep_above

    return set_kwin_keep_above(pid=pid, title=title)


class CapturedFrameResult(list):
    """List containing TextContent and ImageContent matching MCP specification,

    while preserving backwards compatibility (.data, .to_image_content()) for tests.
    """

    def __init__(
        self,
        items: list[Any],
        raw_bytes: bytes,
        file_path: str = "",
    ) -> None:
        super().__init__(items)
        self.raw_bytes = raw_bytes
        self.file_path = file_path

    @property
    def data(self) -> bytes:
        return self.raw_bytes

    def to_image_content(self) -> ImageContent:
        b64 = base64.b64encode(self.raw_bytes).decode("utf-8")
        return ImageContent(type="image", data=b64, mimeType="image/png")

    def __repr__(self) -> str:
        if self.file_path:
            return (
                f"![Captured Frame]({self.file_path})\n"
                f"CapturedFrameResult(file_path='{self.file_path}')"
            )
        return super().__repr__()


class LabeledScreenshotResult(list):
    """List containing Image and TextContent so FastMCP yields first-class ImageContent

    while preserving dict-like access for unit tests.
    """

    def __init__(
        self,
        items: list[Any],
        element_count: int,
        elements: list[dict[str, Any]],
        image_base64: str,
        image_path: str = "",
    ) -> None:
        super().__init__(items)
        self.element_count = element_count
        self.elements = elements
        self.image_base64 = image_base64
        self.image_path = image_path

    def __getitem__(self, key: Any) -> Any:
        if key in ("element_count", "elements", "image", "image_base64", "image_path"):
            return getattr(self, key if key != "image" else "image_base64")
        return super().__getitem__(key)

    def __contains__(self, key: Any) -> bool:
        if key in ("element_count", "elements", "image", "image_base64", "image_path"):
            return True
        return super().__contains__(key)

    def keys(self) -> list[str]:
        return ["image", "image_base64", "element_count", "elements", "image_path"]

    def get(self, key: str, default: Any = None) -> Any:
        if key in ("element_count", "elements", "image", "image_base64", "image_path"):
            return getattr(self, key if key != "image" else "image_base64")
        return default


# Initialize FastMCP application
mcp = FastMCP(
    "wayland-computer-use-mcp",
    instructions=(
        "Interactive Wayland window/desktop streaming, "
        "AT-SPI UI inspection, and clamped input suite."
    ),
)


# --- Process Lifecycle ---


@mcp.tool()
def launch_app(
    script_path: str,
    args: list[str] | None = None,
    cwd: str | None = None,
) -> dict[str, Any]:
    """Launches a Python GUI script using auto-detected virtualenv and returns its PID."""
    from wayland_computer_use_mcp.kwin import ensure_portal_dialogs_above

    # 1. Prompt and await OS portal permissions FIRST, before creating the app window
    # This guarantees the app window never covers or steals focus from permission dialogs
    ensure_portal_dialogs_above()
    _ensure_session_initialized()

    # 2. Once permissions are granted and session is active, launch the app process
    pid = launch(script_path, args or [], cwd)
    global_portal_session.target_pid = pid

    # 3. Bring window to foreground, set keepAbove, and record geometry
    focus_window(pid)
    return {
        "status": "launched",
        "pid": pid,
        "script_path": script_path,
        "args": args or [],
        "cwd": cwd,
        "session_handle": global_portal_session.session_handle,
        "restore_token": global_portal_session.restore_token,
        "eis_connected": global_portal_session.eis_fd is not None,
    }


@mcp.tool()
def restart_app(pid: int) -> dict[str, Any]:
    """Gracefully terminates and re-launches a process preserving arguments."""
    from wayland_computer_use_mcp.kwin import ensure_portal_dialogs_above, minimize_window

    # Minimize old window first so it doesn't obscure any dialogs
    minimize_window(pid)
    ensure_portal_dialogs_above()
    _ensure_session_initialized()

    new_pid = restart(pid)
    global_portal_session.target_pid = new_pid
    focus_window(new_pid)
    return {
        "status": "restarted",
        "old_pid": pid,
        "new_pid": new_pid,
        "session_handle": global_portal_session.session_handle,
        "restore_token": global_portal_session.restore_token,
        "eis_connected": global_portal_session.eis_fd is not None,
    }


@mcp.tool()
def terminate_app(pid: int) -> dict[str, Any]:
    """Terminates an owned application process."""
    terminate(pid)
    if global_portal_session.target_pid == pid:
        global_portal_session.target_pid = None
    return {
        "status": "terminated",
        "pid": pid,
    }


@mcp.tool()
def get_app_logs(pid: int, lines: int = 50) -> str:
    """Retrieves recent stderr crash tracebacks and console output for a process."""
    return get_logs(pid, lines)


@mcp.tool()
def check_app_liveness(pid: int) -> dict[str, Any]:
    """Checks if a managed process is active, responding, or in a zombie/crashed state."""
    alive = is_responsive(pid)
    return {
        "pid": pid,
        "responsive": alive,
        "state": "running" if alive else "exited_or_zombie",
    }


# --- Visual & Tree Inspection ---


def _ensure_session_initialized() -> None:
    """Ensures the portal session is initialized, respecting mock mode and live display."""
    if global_portal_session.is_active():
        return
    try:
        global_portal_session.ensure_initialized()
    except Exception as exc:
        from wayland_computer_use_mcp.config import get_config

        if get_config().mock_mode or not os.environ.get("WAYLAND_DISPLAY"):
            global_portal_session._init_mock_session()
        else:
            raise RuntimeError(f"Failed to initialize live Wayland screen portal: {exc}") from exc


@mcp.tool()
def capture_window_frame(
    crop_box: list[int] | None = None,
    save_artifact: bool = True,
) -> list[Any]:
    """Captures the target window buffer, renders it inline for the agent and user,

    and persists it to the workspace artifact path for Antigravity review.
    Optional crop_box: [x, y, w, h].
    """
    _ensure_session_initialized()
    if not global_portal_session.is_mock:
        global_portal_session.check_active_app()
    if global_portal_session.target_pid:
        try:
            focus_window(global_portal_session.target_pid)
        except Exception:
            pass
    pil_img = global_portal_session.capture_frame(crop_box)

    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    raw_bytes = buf.getvalue()
    b64_data = base64.b64encode(raw_bytes).decode("utf-8")

    contents: list[Any] = []
    saved_path_str = global_portal_session.last_saved_frame_path or ""

    if save_artifact:
        try:
            artifact_dir = Path(".agents/artifacts/screenshots")
            artifact_dir.mkdir(parents=True, exist_ok=True)
            artifact_path = artifact_dir / "latest_capture.png"
            artifact_path.write_bytes(raw_bytes)
            if not saved_path_str:
                saved_path_str = str(artifact_path.resolve())
        except Exception:
            pass

    embed_path = saved_path_str or "latest_capture.png"
    contents.append(
        TextContent(
            type="text",
            text=(
                f"Frame captured ({pil_img.width}x{pil_img.height}) "
                f"and saved to artifact: {embed_path}\n"
                f"![Captured Frame]({embed_path})"
            ),
        )
    )
    contents.append(
        ImageContent(
            type="image",
            data=b64_data,
            mimeType="image/png",
        )
    )

    return CapturedFrameResult(contents, raw_bytes=raw_bytes, file_path=embed_path)


@mcp.tool()
def inspect_ui_tree(pid: int, max_depth: int = 24) -> dict[str, Any]:
    """Returns the pruned, semantic AT-SPI2 accessibility tree for the application."""
    if not global_portal_session.is_mock and not is_responsive(pid):
        raise RuntimeError(f"Application with PID {pid} is not active or responsive.")
    global_portal_session.target_pid = pid
    return get_application_tree(pid, max_depth=max_depth)


# --- Clamped Input Injection & Enhanced Gestures ---


@mcp.tool()
def click(x: int, y: int, button: str = "left") -> str:
    """Fires a mouse click clamped to the window boundary."""
    return global_portal_session.dispatch_click(x, y, button)


@mcp.tool()
def double_click(x: int, y: int, button: str = "left") -> str:
    """Fires a clamped double-click gesture at coordinates."""
    return global_portal_session.dispatch_double_click(x, y, button)


@mcp.tool()
def right_click(x: int, y: int) -> str:
    """Fires a clamped right-click context menu gesture at coordinates."""
    return global_portal_session.dispatch_right_click(x, y)


@mcp.tool()
def hover(x: int, y: int, duration_ms: int = 500) -> str:
    """Hovers cursor at coordinates without clicking to reveal tooltips or flyouts."""
    return global_portal_session.dispatch_hover(x, y, duration_ms)


@mcp.tool()
def drag(start_x: int, start_y: int, end_x: int, end_y: int) -> str:
    """Performs a clamped drag-and-drop gesture within the window."""
    return global_portal_session.dispatch_drag(start_x, start_y, end_x, end_y)


@mcp.tool()
def scroll(dx: int, dy: int) -> str:
    """Dispatches horizontal/vertical scroll events to the active surface."""
    return global_portal_session.dispatch_scroll(dx, dy)


@mcp.tool()
def type_text(text: str, x: int | None = None, y: int | None = None) -> str:
    """Injects keyboard character sequences safely via libei or clipboard paste fallback.

    If x and y coordinates are provided, clicks at (x, y) first to focus the element.
    """
    return global_portal_session.dispatch_type_text(text, x=x, y=y)


@mcp.tool()
def key_combination(keys: list[str]) -> str:
    """Dispatches allowed key combinations (e.g. ['ctrl', 'c']) after security filtering."""
    return global_portal_session.dispatch_key_combination(keys)


@mcp.tool()
def click_element_by_label(
    label: str,
    role: str | None = None,
    pid: int | None = None,
) -> str:
    """Finds an interactive element matching label in UI tree, computes center, and clicks it."""
    return global_portal_session.click_element_by_label(label, role=role, pid=pid)


# --- Visual Grounding & Clipboard ---


@mcp.tool()
def take_labeled_screenshot(
    pid: int | None = None,
    save_artifact: bool = True,
) -> list[Any]:
    _ensure_session_initialized()
    effective_pid = pid or global_portal_session.target_pid
    if not global_portal_session.is_mock:
        global_portal_session.check_active_app(effective_pid)
    if effective_pid:
        try:
            focus_window(effective_pid)
        except Exception:
            pass

    pil_img, legend = global_portal_session.generate_labeled_screenshot(pid=effective_pid)
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    raw_bytes = buf.getvalue()
    b64_data = base64.b64encode(raw_bytes).decode("utf-8")

    labeled_path_str = global_portal_session.last_saved_labeled_path or ""
    if save_artifact:
        try:
            artifact_dir = Path(".agents/artifacts/screenshots")
            artifact_dir.mkdir(parents=True, exist_ok=True)
            artifact_path = artifact_dir / "latest_labeled.png"
            artifact_path.write_bytes(raw_bytes)
            if not labeled_path_str:
                labeled_path_str = str(artifact_path.resolve())
        except Exception:
            pass

    legend_text = json.dumps(
        {
            "element_count": len(legend),
            "elements": legend,
        },
        indent=2,
    )
    embed_path = labeled_path_str or "latest_labeled.png"
    embed_header = f"![Labeled Screenshot]({embed_path})\n\n"

    items: list[Any] = [
        TextContent(type="text", text=f"{embed_header}{legend_text}"),
        ImageContent(
            type="image",
            data=b64_data,
            mimeType="image/png",
        ),
    ]

    return LabeledScreenshotResult(
        items=items,
        element_count=len(legend),
        elements=legend,
        image_base64=b64_data,
        image_path=embed_path,
    )


@mcp.tool()
def clipboard_read() -> str:
    """Reads text from the Wayland desktop clipboard."""
    return global_portal_session.dispatch_clipboard_read()


@mcp.tool()
def clipboard_write(text: str) -> str:
    """Writes text to the Wayland desktop clipboard."""
    return global_portal_session.dispatch_clipboard_write(text)


@mcp.tool()
def focus_window(pid: int | None = None) -> dict[str, Any]:
    """Directs focus and raises target window matching PID (or current active app), ensuring it is in foreground."""
    from wayland_computer_use_mcp.kwin import query_kwin_geometry
    from wayland_computer_use_mcp.portal import global_clamper, global_geometry_detector

    effective_pid = pid or global_portal_session.target_pid
    if not effective_pid:
        raise RuntimeError("No active target PID to focus. Launch an app first or provide PID.")

    global_portal_session.target_pid = effective_pid
    tree = get_application_tree(effective_pid)
    title = None
    for child in tree.get("children", []):
        if child.get("role") in ("window", "frame") and child.get("name"):
            title = child.get("name")
            break
    if not title:
        name = tree.get("name", "")
        if name and not name.endswith(".py"):
            title = name
    if title:
        global_portal_session.target_title = title
    geom = query_kwin_geometry(pid=effective_pid, title=title)
    if geom:
        wx, wy, ww, wh = geom
        global_clamper.set_bounds(ww, wh)
        global_geometry_detector.record_capture(ww, wh, wx, wy)
        global_portal_session.window_offset_x = wx
        global_portal_session.window_offset_y = wy
        bounds = [wx, wy, ww, wh]
    else:
        bounds = tree.get("bounds", [0, 0, 0, 0])
        if len(bounds) >= 4 and bounds[2] > 0 and bounds[3] > 0:
            global_clamper.set_bounds(bounds[2], bounds[3])
            global_geometry_detector.record_capture(bounds[2], bounds[3], 0, 0)

    set_desktop_window_keep_above(pid=effective_pid, title=title)

    return {
        "pid": effective_pid,
        "name": tree.get("name", f"PID-{effective_pid}"),
        "role": tree.get("role", "window"),
        "bounds": bounds,
        "focused": True,
    }


@mcp.tool()
def get_window_geometry(pid: int) -> dict[str, Any]:
    """Retrieves current surface geometry (x, y, width, height) and bounds."""
    if not global_portal_session.is_mock and not is_responsive(pid):
        raise RuntimeError(f"Application with PID {pid} is not active or responsive.")

    from wayland_computer_use_mcp.kwin import query_kwin_geometry

    tree = get_application_tree(pid)
    title = None
    for child in tree.get("children", []):
        if child.get("role") in ("window", "frame") and child.get("name"):
            title = child.get("name")
            break
    if not title:
        title = tree.get("name")
    geom = query_kwin_geometry(pid=pid, title=title)
    if geom:
        wx, wy, ww, wh = geom
        return {
            "pid": pid,
            "bounds": [wx, wy, ww, wh],
            "width": ww,
            "height": wh,
            "x": wx,
            "y": wy,
        }
    bounds = tree.get("bounds", [0, 0, 0, 0])
    return {
        "pid": pid,
        "bounds": bounds,
        "width": bounds[2] if len(bounds) >= 4 else 0,
        "height": bounds[3] if len(bounds) >= 4 else 0,
    }


# --- OS Desktop Integration ---


@mcp.tool()
def install_to_desktop(
    app_id: str,
    name: str,
    exec_path: str,
    icon_path: str | None = None,
    version: str | None = None,
    is_dev: bool | None = None,
) -> dict[str, Any]:
    """Registers the script as a native Linux desktop application with optional version badging."""
    return install_desktop_app(
        app_id=app_id,
        name=name,
        exec_path=exec_path,
        icon_path=icon_path,
        version=version,
        is_dev=is_dev,
    )


@mcp.tool()
def uninstall_from_desktop(app_id: str) -> dict[str, Any]:
    """Removes the application launcher and its associated icons from the OS desktop menu."""
    return remove_desktop_app(app_id=app_id)


def main() -> None:
    """CLI entry point for wayland-computer-use-mcp server."""
    import sys

    logging.basicConfig(level=logging.INFO)
    cfg = get_config()
    remaining = cfg.apply_cli_args(sys.argv[1:])
    sys.argv = [sys.argv[0], *remaining]
    mcp.run()


if __name__ == "__main__":
    main()
