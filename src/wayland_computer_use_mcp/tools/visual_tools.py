"""Visual grounding, Set-of-Marks screenshot labeling, and window geometry tools."""

from __future__ import annotations

import base64
import io
import logging
from typing import Any

from fastmcp import FastMCP
from mcp.types import ImageContent, TextContent, ToolAnnotations
from PIL import ImageDraw

from wayland_computer_use_mcp.a11y import (
    extract_active_window_node,
    get_application_tree,
    get_cached_node,
)
from wayland_computer_use_mcp.artifacts import get_artifacts_dir, prune_artifacts
from wayland_computer_use_mcp.portal import (
    global_portal_session,
)
from wayland_computer_use_mcp.security import (
    global_clamper,
    global_geometry_detector,
)

logger = logging.getLogger("wayland_computer_use_mcp.tools.visual")


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
    active_win = extract_active_window_node(tree)
    title = None
    if active_win and active_win.get("name"):
        title = str(active_win["name"])
    if not title:
        name = tree.get("name", "")
        if name and not name.endswith(".py"):
            title = name
    if title:
        global_portal_session.target_title = title

    driver = global_portal_session.active_driver()
    geom = driver.query_window_geometry(pid=effective_pid, title=title)
    if geom:
        wx, wy, ww, wh = geom
        global_clamper.set_bounds(ww, wh)
        global_geometry_detector.record_capture(ww, wh, wx, wy)
        global_portal_session.window_offset_x = wx
        global_portal_session.window_offset_y = wy
        bounds = [wx, wy, ww, wh]
    else:
        win_bounds = active_win.get("bounds") if active_win else None
        bounds = (
            win_bounds
            if isinstance(win_bounds, list)
            and len(win_bounds) >= 4
            and win_bounds[2] > 0
            and win_bounds[3] > 0
            else tree.get("bounds", [0, 0, 0, 0])
        )
        if len(bounds) >= 4 and bounds[2] > 0 and bounds[3] > 0:
            global_clamper.set_bounds(bounds[2], bounds[3])
            global_geometry_detector.record_capture(bounds[2], bounds[3], 0, 0)

    driver.activate_window(pid=effective_pid, title=title)

    return {
        "pid": effective_pid,
        "name": tree.get("name", f"PID-{effective_pid}"),
        "role": tree.get("role", "window"),
        "bounds": bounds,
        "focused": True,
    }


def _draw_element_annotations(
    pil_img: Any,
    elements: list[dict[str, Any]],
    offset_x: int = 0,
    offset_y: int = 0,
) -> Any:
    """Draws Set-of-Marks bounding boxes and compact ID badges on a copy of the captured frame."""
    annotated = pil_img.copy()
    draw = ImageDraw.Draw(annotated)
    w, h = annotated.size
    for elem in elements[:60]:
        eid = str(elem.get("id", "")).strip()
        bounds = elem.get("bounds")
        if not eid or not isinstance(bounds, list) or len(bounds) != 4:
            continue
        bx, by, bw, bh = bounds
        if bw <= 0 or bh <= 0:
            continue
        x1 = bx - offset_x
        y1 = by - offset_y
        x2 = x1 + bw
        y2 = y1 + bh
        if x2 <= 0 or y2 <= 0 or x1 >= w or y1 >= h:
            continue
        cx1, cy1 = max(0, x1), max(0, y1)
        cx2, cy2 = min(w - 1, x2), min(h - 1, y2)
        if cx2 <= cx1 or cy2 <= cy1:
            continue
        draw.rectangle([cx1, cy1, cx2, cy2], outline=(255, 80, 40), width=2)
        tag_w = max(22, len(eid) * 7 + 8)
        ty1 = max(0, cy1 - 14)
        draw.rectangle([cx1, ty1, min(w - 1, cx1 + tag_w), ty1 + 14], fill=(220, 45, 20))
        draw.text((cx1 + 3, ty1 + 2), eid, fill=(255, 255, 255))
    return annotated


def capture_window_frame(
    crop_box: list[int] | None = None,
    save_artifact: bool = False,
    pid: int | None = None,
    target: str | None = None,
    annotate_elements: bool = False,
) -> list[Any]:
    """Captures window frame as in-memory ImageContent (saves PNG when save_artifact=True).

    Optional ``target`` (element ID or label) crops closely around that widget, and
    ``annotate_elements=True`` draws Set-of-Marks element boxes and IDs (b1, e1, etc.).
    """
    global_portal_session.ensure_initialized()
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

    resolved_crop = crop_box
    tree_snapshot: dict[str, Any] | None = None
    target_missing_hint = ""
    if target and resolved_crop is None:
        lookup_pid = effective_pid or 0
        cached = get_cached_node(lookup_pid, target)
        if cached is None:
            tree_snapshot = get_application_tree(lookup_pid)
            cached = get_cached_node(lookup_pid, target)
        if cached and isinstance(cached.get("bounds"), list) and len(cached["bounds"]) == 4:
            bx, by, bw, bh = cached["bounds"]
            if bw > 0 and bh > 0:
                pad = 8
                resolved_crop = [max(0, bx - pad), max(0, by - pad), bw + pad * 2, bh + pad * 2]
        elif not global_portal_session.is_mock:
            from wayland_computer_use_mcp.tools.navigation_tools import _suggest_closest_targets

            suggestions = _suggest_closest_targets(lookup_pid, target)
            hint = f" Closest matches: {', '.join(suggestions)}" if suggestions else ""
            target_missing_hint = (
                f" Warning: target '{target}' not found; captured uncropped window.{hint}"
            )

    pil_img = global_portal_session.capture_frame(crop_box=resolved_crop, save_to_disk=False)

    if annotate_elements:
        try:
            if tree_snapshot is None:
                tree_snapshot = get_application_tree(effective_pid or 0)
            elements = tree_snapshot.get("interactive_elements", [])
            ox = resolved_crop[0] if resolved_crop and len(resolved_crop) >= 2 else 0
            oy = resolved_crop[1] if resolved_crop and len(resolved_crop) >= 2 else 0
            pil_img = _draw_element_annotations(pil_img, elements, offset_x=ox, offset_y=oy)
        except Exception:
            pass

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
            prune_artifacts("screenshots")
        except Exception:
            pass

    if embed_path:
        text_desc = (
            f"Frame captured ({pil_img.width}x{pil_img.height}) "
            f"and saved to artifact: {embed_path}{target_missing_hint}\n"
            f"![Captured Frame]({embed_path})"
        )
    else:
        text_desc = (
            f"Frame captured ({pil_img.width}x{pil_img.height}) in-memory.{target_missing_hint}"
        )

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
