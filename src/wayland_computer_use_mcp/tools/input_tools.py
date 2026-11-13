"""Low-level coordinate and keyboard input dispatch tools with safety containment."""

from __future__ import annotations

import logging
from typing import Any

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from wayland_computer_use_mcp.delta import InteractionResult, wrap_with_delta
from wayland_computer_use_mcp.portal import global_portal_session
from wayland_computer_use_mcp.process import check_process_health_and_enrich

logger = logging.getLogger("wayland_computer_use_mcp.tools.input")


def _sync_target_pid(pid: int | None) -> int:
    effective_pid = pid or global_portal_session.target_pid or 0
    if pid and pid > 0:
        global_portal_session.target_pid = pid
        try:
            from wayland_computer_use_mcp.tools.visual_tools import focus_window

            focus_window(pid)
        except Exception:
            pass
    return effective_pid


def _dispatch_tracked_input(
    pid: int | None,
    action_name: str,
    target: str,
    fn: Any,
    *,
    with_delta: bool = True,
) -> InteractionResult:
    effective_pid = _sync_target_pid(pid)
    delta_pid = effective_pid if (with_delta and effective_pid > 0) else 0
    res = wrap_with_delta(delta_pid, fn, action_name=action_name, target=target)
    return check_process_health_and_enrich(global_portal_session.target_pid, res)


def click(x: int, y: int, button: str = "left", pid: int | None = None) -> InteractionResult:
    """Dispatches a mouse click clamped within the active window boundaries."""
    return _dispatch_tracked_input(
        pid,
        "click",
        f"({x}, {y})",
        lambda: global_portal_session.dispatch_click(x, y, button=button),
    )


def double_click(x: int, y: int, button: str = "left", pid: int | None = None) -> InteractionResult:
    """Dispatches a double-click within the active window boundaries."""
    return _dispatch_tracked_input(
        pid,
        "double_click",
        f"({x}, {y})",
        lambda: global_portal_session.dispatch_double_click(x, y, button=button),
    )


def right_click(x: int, y: int, pid: int | None = None) -> InteractionResult:
    """Dispatches a right-click within the active window boundaries."""
    return _dispatch_tracked_input(
        pid,
        "right_click",
        f"({x}, {y})",
        lambda: global_portal_session.dispatch_right_click(x, y),
    )


def hover(x: int, y: int, duration_ms: int = 500, pid: int | None = None) -> InteractionResult:
    """Positions cursor at coordinates without clicking to trigger hover states or tooltips."""
    return _dispatch_tracked_input(
        pid,
        "hover",
        f"({x}, {y})",
        lambda: global_portal_session.dispatch_hover(x, y, duration_ms=duration_ms),
        with_delta=False,
    )


def drag(
    start_x: int, start_y: int, end_x: int, end_y: int, pid: int | None = None
) -> InteractionResult:
    """Performs a clamped drag-and-drop gesture from start to end coordinates."""
    return _dispatch_tracked_input(
        pid,
        "drag",
        f"({start_x}, {start_y}) -> ({end_x}, {end_y})",
        lambda: global_portal_session.dispatch_drag(start_x, start_y, end_x, end_y),
    )


def scroll(dx: int, dy: int, pid: int | None = None) -> InteractionResult:
    """Dispatches discrete mouse wheel scroll deltas (positive dy=down, negative dy=up)."""
    return _dispatch_tracked_input(
        pid,
        "scroll",
        f"dx={dx}, dy={dy}",
        lambda: global_portal_session.dispatch_scroll(dx, dy),
    )


def type_text(
    text: str,
    x: int | None = None,
    y: int | None = None,
    pid: int | None = None,
) -> InteractionResult:
    """Injects keyboard characters. If coordinates (x, y) are provided, clicks first to focus."""
    target = f"({x}, {y})" if (x is not None and y is not None) else "focused"
    return _dispatch_tracked_input(
        pid,
        "type_text",
        target,
        lambda: global_portal_session.dispatch_type_text(text, x=x, y=y),
    )


def key_combination(keys: list[str], pid: int | None = None) -> InteractionResult:
    """Dispatches allowed key combinations (e.g. ['ctrl', 'c']) after security filtering."""
    return _dispatch_tracked_input(
        pid,
        "key_combination",
        "+".join(keys),
        lambda: global_portal_session.dispatch_key_combination(keys),
    )


def register_input_tools(mcp: FastMCP) -> None:
    """Registers coordinate mouse and keyboard injection tools onto FastMCP."""
    input_ann = ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
    for fn in (
        click,
        double_click,
        right_click,
        hover,
        drag,
        scroll,
        type_text,
        key_combination,
    ):
        mcp.tool(annotations=input_ann)(fn)
