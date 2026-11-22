"""Low-level coordinate and keyboard input dispatch tools with safety containment."""

from __future__ import annotations

import logging
from typing import Any, Literal

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
    delta_pid = (
        effective_pid
        if (with_delta and effective_pid > 0 and not global_portal_session.is_mock)
        else 0
    )
    res = wrap_with_delta(delta_pid, fn, action_name=action_name, target=target)
    return check_process_health_and_enrich(effective_pid, res)


def click(
    x: int,
    y: int,
    button: Literal["left", "right", "middle"] = "left",
    coordinate_space: Literal["logical", "physical"] = "logical",
    pid: int | None = None,
) -> InteractionResult:
    """Dispatches a mouse click clamped within the active window boundaries."""
    return _dispatch_tracked_input(
        pid,
        "click",
        f"({x}, {y})",
        lambda: global_portal_session.dispatch_click(
            x, y, button=button, coordinate_space=coordinate_space
        ),
    )


def double_click(
    x: int,
    y: int,
    button: Literal["left", "right", "middle"] = "left",
    coordinate_space: Literal["logical", "physical"] = "logical",
    pid: int | None = None,
) -> InteractionResult:
    """Dispatches a double-click within the active window boundaries."""
    return _dispatch_tracked_input(
        pid,
        "double_click",
        f"({x}, {y})",
        lambda: global_portal_session.dispatch_double_click(
            x, y, button=button, coordinate_space=coordinate_space
        ),
    )


def right_click(
    x: int,
    y: int,
    coordinate_space: Literal["logical", "physical"] = "logical",
    pid: int | None = None,
) -> InteractionResult:
    """Dispatches a right-click within the active window boundaries."""
    return _dispatch_tracked_input(
        pid,
        "right_click",
        f"({x}, {y})",
        lambda: global_portal_session.dispatch_right_click(x, y, coordinate_space=coordinate_space),
    )


def hover(
    x: int,
    y: int,
    duration_ms: int = 500,
    coordinate_space: Literal["logical", "physical"] = "logical",
    pid: int | None = None,
) -> InteractionResult:
    """Positions cursor at coordinates without clicking to trigger hover states or tooltips."""
    return _dispatch_tracked_input(
        pid,
        "hover",
        f"({x}, {y})",
        lambda: global_portal_session.dispatch_hover(
            x, y, duration_ms=duration_ms, coordinate_space=coordinate_space
        ),
        with_delta=False,
    )


def drag(
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    coordinate_space: Literal["logical", "physical"] = "logical",
    pid: int | None = None,
) -> InteractionResult:
    """Performs a clamped drag-and-drop gesture from start to end coordinates."""
    return _dispatch_tracked_input(
        pid,
        "drag",
        f"({start_x}, {start_y}) -> ({end_x}, {end_y})",
        lambda: global_portal_session.dispatch_drag(
            start_x, start_y, end_x, end_y, coordinate_space=coordinate_space
        ),
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
    coordinate_space: Literal["logical", "physical"] = "logical",
    pid: int | None = None,
) -> InteractionResult:
    """Injects keyboard characters. If coordinates (x, y) are provided, clicks first to focus."""
    target = f"({x}, {y})" if (x is not None and y is not None) else "focused"
    return _dispatch_tracked_input(
        pid,
        "type_text",
        target,
        lambda: global_portal_session.dispatch_type_text(
            text, x=x, y=y, coordinate_space=coordinate_space
        ),
    )


def key_combination(keys: list[str], pid: int | None = None) -> InteractionResult:
    """Dispatches allowed key combinations (e.g. ['ctrl', 'c']) after security filtering."""
    return _dispatch_tracked_input(
        pid,
        "key_combination",
        "+".join(keys),
        lambda: global_portal_session.dispatch_key_combination(keys),
    )


def mouse(
    action: Literal["click", "double_click", "right_click", "hover", "drag", "scroll"],
    x: int | None = None,
    y: int | None = None,
    end_x: int | None = None,
    end_y: int | None = None,
    dx: int = 0,
    dy: int = 0,
    button: Literal["left", "right", "middle"] = "left",
    duration_ms: int = 500,
    coordinate_space: Literal["logical", "physical"] = "logical",
    pid: int | None = None,
) -> InteractionResult:
    """Level 2 coordinate mouse fallback (prefer interact_with_node when AT-SPI is available).

    Actions:
    - 'click' | 'double_click' | 'right_click': requires (x, y).
    - 'hover': requires (x, y); optional duration_ms.
    - 'drag': requires (x, y) start and (end_x, end_y) target.
    - 'scroll': uses dx / dy discrete wheel deltas.
    Set coordinate_space='physical' when passing raw pixels from HiDPI capture_window_frame.
    """
    act = action.strip().lower()
    if act in ("click", "double_click", "right_click", "hover"):
        if x is None or y is None:
            raise ValueError(f"Mouse action '{act}' requires 'x' and 'y' coordinates.")
        if act == "click":
            return click(x, y, button=button, coordinate_space=coordinate_space, pid=pid)
        if act == "double_click":
            return double_click(x, y, button=button, coordinate_space=coordinate_space, pid=pid)
        if act == "right_click":
            return right_click(x, y, coordinate_space=coordinate_space, pid=pid)
        return hover(x, y, duration_ms=duration_ms, coordinate_space=coordinate_space, pid=pid)
    if act == "drag":
        if None in (x, y, end_x, end_y):
            raise ValueError("Mouse action 'drag' requires 'x', 'y', 'end_x', and 'end_y'.")
        return drag(
            int(x),  # type: ignore[arg-type]
            int(y),  # type: ignore[arg-type]
            int(end_x),  # type: ignore[arg-type]
            int(end_y),  # type: ignore[arg-type]
            coordinate_space=coordinate_space,
            pid=pid,
        )
    if act == "scroll":
        return scroll(dx, dy, pid=pid)
    raise ValueError(f"Unknown mouse action '{action}'.")


def keyboard(
    action: Literal["type", "keys"],
    text: str | None = None,
    keys: list[str] | None = None,
    x: int | None = None,
    y: int | None = None,
    pid: int | None = None,
) -> InteractionResult:
    """Level 2 keyboard fallback (prefer interact_with_node for named inputs).

    Actions:
    - 'type': requires ``text`` (optionally clicks ``(x, y)`` first to focus).
    - 'keys': requires non-empty ``keys`` list (e.g. ``['ctrl', 's']``).
    """
    act = action.strip().lower()
    if act == "type":
        if text is None:
            raise ValueError("Keyboard action 'type' requires 'text'.")
        return type_text(text, x=x, y=y, pid=pid)
    if act == "keys":
        if not keys:
            raise ValueError("Keyboard action 'keys' requires a non-empty 'keys' list.")
        return key_combination(keys, pid=pid)
    raise ValueError(f"Unknown keyboard action '{action}'. Must be 'type' or 'keys'.")


def register_input_tools(mcp: FastMCP) -> None:
    """Registers consolidated mouse and keyboard fallback tools onto FastMCP."""
    input_ann = ToolAnnotations(
        readOnlyHint=False,
        destructiveHint=False,
        idempotentHint=False,
        openWorldHint=False,
    )
    mcp.tool(annotations=input_ann)(mouse)
    mcp.tool(annotations=input_ann)(keyboard)
