"""Visual grounding, Set-of-Marks screenshot labeling, and window geometry tools."""

from __future__ import annotations

import base64
import io
import json
import logging
from pathlib import Path
from typing import Any

from fastmcp import FastMCP
from mcp.types import ImageContent, TextContent

from wayland_computer_use_mcp.a11y import get_application_tree
from wayland_computer_use_mcp.compositors import (
    activate_and_raise_window as set_kwin_keep_above,
)
from wayland_computer_use_mcp.compositors import (
    query_window_geometry as query_kwin_geometry,
)
from wayland_computer_use_mcp.portal import (
    global_portal_session,
)
from wayland_computer_use_mcp.process import is_responsive
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


def focus_window(pid: int | None = None) -> dict[str, Any]:
    """Directs focus and raises target window matching PID, ensuring it is in foreground."""
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

    set_kwin_keep_above(pid=effective_pid, title=title)

    return {
        "pid": effective_pid,
        "name": tree.get("name", f"PID-{effective_pid}"),
        "role": tree.get("role", "window"),
        "bounds": bounds,
        "focused": True,
    }


def take_labeled_screenshot(
    pid: int | None = None,
    save_artifact: bool = True,
) -> list[Any]:
    """Captures window frame with Set-of-Marks numbered bounding boxes and tags.

    Returns image content alongside indexed element markers for visual reasoning.
    """
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


def capture_window_frame(
    crop_box: list[int] | None = None,
    save_artifact: bool = True,
) -> list[Any]:
    """Captures the target window buffer and renders it inline for the agent."""
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
    contents: list[Any] = [
        TextContent(
            type="text",
            text=(
                f"Frame captured ({pil_img.width}x{pil_img.height}) "
                f"and saved to artifact: {embed_path}\n"
                f"![Captured Frame]({embed_path})"
            ),
        ),
        ImageContent(
            type="image",
            data=b64_data,
            mimeType="image/png",
        ),
    ]

    return CapturedFrameResult(contents, raw_bytes=raw_bytes, file_path=embed_path)


def get_window_geometry(pid: int) -> dict[str, Any]:
    """Retrieves current surface geometry (x, y, width, height) and bounds."""
    if not global_portal_session.is_mock and not is_responsive(pid):
        raise RuntimeError(f"Application with PID {pid} is not active or responsive.")

    tree = get_application_tree(pid)
    title = None
    for child in tree.get("children", []):
        if child.get("role") in ("window", "frame") and child.get("name"):
            title = child.get("name")
            break

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


def register_visual_tools(mcp: FastMCP) -> None:
    """Registers visual grounding and screenshot tools onto FastMCP."""
    mcp.tool()(take_labeled_screenshot)
    mcp.tool()(capture_window_frame)
