"""Visual grounding, Set-of-Marks screenshot labeling, and window geometry tools."""

from __future__ import annotations

import base64
import io
import logging
from typing import Any

from fastmcp import FastMCP
from mcp.types import ImageContent, TextContent, ToolAnnotations

from wayland_computer_use_mcp.a11y import get_application_tree
from wayland_computer_use_mcp.artifacts import get_artifacts_dir
from wayland_computer_use_mcp.compositors import (
    activate_and_raise_window as set_kwin_keep_above,
)
from wayland_computer_use_mcp.compositors import (
    query_window_geometry as query_kwin_geometry,
)
from wayland_computer_use_mcp.portal import (
    global_portal_session,
)
from wayland_computer_use_mcp.security import (
    global_clamper,
    global_geometry_detector,
)

logger = logging.getLogger("wayland_computer_use_mcp.tools.visual")


def _ensure_session_initialized() -> None:
    if not global_portal_session.is_active():
        global_portal_session.ensure_initialized()


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


def focus_window(pid: int | None = None) -> dict[str, Any]:
    """Directs focus and raises target window matching PID, ensuring it is in foreground."""
    effective_pid = pid or global_portal_session.target_pid
    if not effective_pid:
        raise RuntimeError("No active target PID to focus. Launch an app first or provide PID.")

    global_portal_session.target_pid = effective_pid

    if global_portal_session.is_mock:
        from wayland_computer_use_mcp.a11y import _node_cache

        if not _node_cache.get(effective_pid):
            get_application_tree(effective_pid)
        return {
            "pid": effective_pid,
            "name": global_portal_session.target_title or f"PID-{effective_pid}",
            "role": "window",
            "bounds": [0, 0, 1920, 1080],
            "focused": True,
        }

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

    set_kwin_keep_above(pid=effective_pid, title=title)

    return {
        "pid": effective_pid,
        "name": tree.get("name", f"PID-{effective_pid}"),
        "role": tree.get("role", "window"),
        "bounds": bounds,
        "focused": True,
    }


def capture_window_frame(
    crop_box: list[int] | None = None,
    save_artifact: bool = False,
    pid: int | None = None,
) -> list[Any]:
    """Captures window frame as in-memory ImageContent (saves PNG when save_artifact=True)."""
    _ensure_session_initialized()
    effective_pid = pid or global_portal_session.target_pid
    if effective_pid:
        global_portal_session.target_pid = effective_pid

    if not global_portal_session.is_mock:
        global_portal_session.check_active_app(effective_pid)

    if global_portal_session.target_pid:
        try:
            focus_window(global_portal_session.target_pid)
        except Exception:
            pass

    pil_img = global_portal_session.capture_frame(crop_box=crop_box, save_to_disk=False)
    buf = io.BytesIO()
    pil_img.save(buf, format="PNG")
    raw_bytes = buf.getvalue()
    b64_data = base64.b64encode(raw_bytes).decode("utf-8")

    embed_path = ""
    if save_artifact:
        try:
            artifact_dir = get_artifacts_dir("screenshots")
            artifact_dir.mkdir(parents=True, exist_ok=True)
            artifact_path = artifact_dir / "latest_capture.png"
            artifact_path.write_bytes(raw_bytes)
            embed_path = str(artifact_path.resolve())
        except Exception:
            pass

    if embed_path:
        text_desc = (
            f"Frame captured ({pil_img.width}x{pil_img.height}) "
            f"and saved to artifact: {embed_path}\n"
            f"![Captured Frame]({embed_path})"
        )
    else:
        text_desc = f"Frame captured ({pil_img.width}x{pil_img.height}) in-memory."

    contents: list[Any] = [
        TextContent(
            type="text",
            text=text_desc,
        ),
        ImageContent(
            type="image",
            data=b64_data,
            mimeType="image/png",
        ),
    ]

    return CapturedFrameResult(contents, raw_bytes=raw_bytes, file_path=embed_path)


def register_visual_tools(mcp: FastMCP) -> None:
    """Registers visual grounding and screenshot tools onto FastMCP."""
    mcp.tool(
        annotations=ToolAnnotations(
            readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
        )
    )(capture_window_frame)
