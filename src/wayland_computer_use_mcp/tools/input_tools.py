"""Low-level coordinate and keyboard input dispatch tools with safety containment."""

from __future__ import annotations

import logging

from fastmcp import FastMCP

from wayland_computer_use_mcp.delta import wrap_with_delta
from wayland_computer_use_mcp.portal import global_portal_session
from wayland_computer_use_mcp.process import check_process_health_and_enrich

logger = logging.getLogger("wayland_computer_use_mcp.tools.input")


def click(x: int, y: int, button: str = "left") -> str:
    """Dispatches a mouse click clamped within the active window boundaries."""
    pid = global_portal_session.target_pid or 0

    def _do() -> str:
        return global_portal_session.dispatch_click(x, y, button=button)

    res = wrap_with_delta(pid, _do) if pid > 0 else _do()
    return check_process_health_and_enrich(global_portal_session.target_pid, res)


def double_click(x: int, y: int, button: str = "left") -> str:
    """Dispatches a double-click within the active window boundaries."""
    pid = global_portal_session.target_pid or 0

    def _do() -> str:
        return global_portal_session.dispatch_double_click(x, y, button=button)

    res = wrap_with_delta(pid, _do) if pid > 0 else _do()
    return check_process_health_and_enrich(global_portal_session.target_pid, res)


def right_click(x: int, y: int) -> str:
    """Dispatches a right-click within the active window boundaries."""
    pid = global_portal_session.target_pid or 0

    def _do() -> str:
        return global_portal_session.dispatch_right_click(x, y)

    res = wrap_with_delta(pid, _do) if pid > 0 else _do()
    return check_process_health_and_enrich(global_portal_session.target_pid, res)


def hover(x: int, y: int, duration_ms: int = 500) -> str:
    """Positions cursor at coordinates without clicking to trigger hover states or tooltips."""
    res = global_portal_session.dispatch_hover(x, y, duration_ms=duration_ms)
    return check_process_health_and_enrich(global_portal_session.target_pid, res)


def drag(start_x: int, start_y: int, end_x: int, end_y: int) -> str:
    """Performs a clamped drag-and-drop gesture from start to end coordinates."""
    pid = global_portal_session.target_pid or 0

    def _do() -> str:
        return global_portal_session.dispatch_drag(start_x, start_y, end_x, end_y)

    res = wrap_with_delta(pid, _do) if pid > 0 else _do()
    return check_process_health_and_enrich(global_portal_session.target_pid, res)


def scroll(dx: int, dy: int) -> str:
    """Dispatches discrete mouse wheel scroll deltas (positive dy=up, negative dy=down)."""
    pid = global_portal_session.target_pid or 0

    def _do() -> str:
        return global_portal_session.dispatch_scroll(dx, dy)

    res = wrap_with_delta(pid, _do) if pid > 0 else _do()
    return check_process_health_and_enrich(global_portal_session.target_pid, res)


def type_text(text: str, x: int | None = None, y: int | None = None) -> str:
    """Injects keyboard characters. If coordinates (x, y) are provided, clicks first to focus."""
    pid = global_portal_session.target_pid or 0

    def _do() -> str:
        return global_portal_session.dispatch_type_text(text, x=x, y=y)

    res = wrap_with_delta(pid, _do) if pid > 0 else _do()
    return check_process_health_and_enrich(global_portal_session.target_pid, res)


def key_combination(keys: list[str]) -> str:
    """Dispatches allowed key combinations (e.g. ['ctrl', 'c']) after security filtering."""
    pid = global_portal_session.target_pid or 0

    def _do() -> str:
        return global_portal_session.dispatch_key_combination(keys)

    res = wrap_with_delta(pid, _do) if pid > 0 else _do()
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
