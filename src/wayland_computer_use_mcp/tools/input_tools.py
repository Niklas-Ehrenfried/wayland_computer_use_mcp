"""Low-level coordinate and keyboard input dispatch tools with safety containment."""

from __future__ import annotations

import logging
from typing import Any

from fastmcp import FastMCP

from wayland_computer_use_mcp.delta import wrap_with_delta
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


def click(x: int, y: int, button: str = "left", pid: int | None = None) -> Any:
    """Dispatches a mouse click clamped within the active window boundaries."""
    effective_pid = _sync_target_pid(pid)

    def _do() -> str:
        return global_portal_session.dispatch_click(x, y, button=button)

    res = (
        wrap_with_delta(effective_pid, _do, action_name="click", target=f"({x}, {y})")
        if effective_pid > 0
        else _do()
    )
    return check_process_health_and_enrich(global_portal_session.target_pid, res)


def double_click(x: int, y: int, button: str = "left", pid: int | None = None) -> Any:
    """Dispatches a double-click within the active window boundaries."""
    effective_pid = _sync_target_pid(pid)

    def _do() -> str:
        return global_portal_session.dispatch_double_click(x, y, button=button)

    res = (
        wrap_with_delta(effective_pid, _do, action_name="double_click", target=f"({x}, {y})")
        if effective_pid > 0
        else _do()
    )
    return check_process_health_and_enrich(global_portal_session.target_pid, res)


def right_click(x: int, y: int, pid: int | None = None) -> Any:
    """Dispatches a right-click within the active window boundaries."""
    effective_pid = _sync_target_pid(pid)

    def _do() -> str:
        return global_portal_session.dispatch_right_click(x, y)

    res = (
        wrap_with_delta(effective_pid, _do, action_name="right_click", target=f"({x}, {y})")
        if effective_pid > 0
        else _do()
    )
    return check_process_health_and_enrich(global_portal_session.target_pid, res)


def hover(x: int, y: int, duration_ms: int = 500, pid: int | None = None) -> Any:
    """Positions cursor at coordinates without clicking to trigger hover states or tooltips."""
    _sync_target_pid(pid)
    res = global_portal_session.dispatch_hover(x, y, duration_ms=duration_ms)
    return check_process_health_and_enrich(global_portal_session.target_pid, res)


def drag(start_x: int, start_y: int, end_x: int, end_y: int, pid: int | None = None) -> Any:
    """Performs a clamped drag-and-drop gesture from start to end coordinates."""
    effective_pid = _sync_target_pid(pid)

    def _do() -> str:
        return global_portal_session.dispatch_drag(start_x, start_y, end_x, end_y)

    res = (
        wrap_with_delta(
            effective_pid,
            _do,
            action_name="drag",
            target=f"({start_x}, {start_y}) -> ({end_x}, {end_y})",
        )
        if effective_pid > 0
        else _do()
    )
    return check_process_health_and_enrich(global_portal_session.target_pid, res)


def scroll(dx: int, dy: int, pid: int | None = None) -> Any:
    """Dispatches discrete mouse wheel scroll deltas (positive dy=down, negative dy=up)."""
    effective_pid = _sync_target_pid(pid)

    def _do() -> str:
        return global_portal_session.dispatch_scroll(dx, dy)

    res = (
        wrap_with_delta(effective_pid, _do, action_name="scroll", target=f"dx={dx}, dy={dy}")
        if effective_pid > 0
        else _do()
    )
    return check_process_health_and_enrich(global_portal_session.target_pid, res)


def type_text(
    text: str,
    x: int | None = None,
    y: int | None = None,
    pid: int | None = None,
) -> Any:
    """Injects keyboard characters. If coordinates (x, y) are provided, clicks first to focus."""
    effective_pid = _sync_target_pid(pid)

    def _do() -> str:
        return global_portal_session.dispatch_type_text(text, x=x, y=y)

    res = (
        wrap_with_delta(
            effective_pid,
            _do,
            action_name="type_text",
            target=f"({x}, {y})" if (x is not None and y is not None) else "focused",
        )
        if effective_pid > 0
        else _do()
    )
    return check_process_health_and_enrich(global_portal_session.target_pid, res)


def key_combination(keys: list[str], pid: int | None = None) -> Any:
    """Dispatches allowed key combinations (e.g. ['ctrl', 'c']) after security filtering."""
    effective_pid = _sync_target_pid(pid)

    def _do() -> str:
        return global_portal_session.dispatch_key_combination(keys)

    res = (
        wrap_with_delta(effective_pid, _do, action_name="key_combination", target="+".join(keys))
        if effective_pid > 0
        else _do()
    )
    return check_process_health_and_enrich(global_portal_session.target_pid, res)


def register_input_tools(mcp: FastMCP) -> None:
    """Registers coordinate mouse and keyboard injection tools onto FastMCP."""
    mcp.tool()(click)
    mcp.tool()(double_click)
    mcp.tool()(right_click)
    mcp.tool()(hover)
    mcp.tool()(drag)
    mcp.tool()(scroll)
    mcp.tool()(type_text)
    mcp.tool()(key_combination)
