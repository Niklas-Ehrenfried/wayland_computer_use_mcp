"""Process lifecycle tools for wayland-computer-use-mcp."""

from __future__ import annotations

import logging
from typing import Any

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from wayland_computer_use_mcp.config import get_config
from wayland_computer_use_mcp.portal import global_portal_session
from wayland_computer_use_mcp.process import (
    get_logs,
    is_responsive,
    launch,
    list_active_processes,
    terminate,
)
from wayland_computer_use_mcp.process import (
    restart as restart_process,
)

logger = logging.getLogger("wayland_computer_use_mcp.tools.process")


def ensure_portal_dialogs_above() -> bool:
    """Delegates portal dialog foregrounding through the active session driver."""
    return global_portal_session.active_driver().ensure_dialogs_above()


def _lifecycle_metadata(elements: list[dict[str, Any]]) -> dict[str, Any]:
    meta = global_portal_session.metadata()
    return {
        "session_handle": meta.get("session_handle"),
        "restore_token": meta.get("restore_token"),
        "eis_connected": bool(meta.get("eis_connected", False)),
        "interactive_elements": elements,
    }


def _bind_focus_and_collect_elements(pid: int) -> list[dict[str, Any]]:
    """Updates target_pid, focuses the window, and extracts initial interactive elements."""
    global_portal_session.target_pid = pid
    try:
        from wayland_computer_use_mcp.tools.visual_tools import focus_window

        focus_window(pid)
    except Exception:
        pass

    try:
        from wayland_computer_use_mcp.a11y import get_application_tree

        tree = get_application_tree(pid)
        return tree.get("interactive_elements", [])
    except Exception:
        return []


def launch_app(
    target: str,
    args: list[str] | None = None,
    restart: bool = False,
    cwd: str | None = None,
) -> dict[str, Any]:
    """Launches a Python GUI script or system app with auto-focus and portal session binding.

    If restart=True or if the app is already running, gracefully terminates the old process
    and launches a fresh instance.
    """
    cfg = get_config()
    if not cfg.mock_mode and not cfg.lazy_portal:
        ensure_portal_dialogs_above()

    current_pid = global_portal_session.target_pid
    if restart and current_pid and is_responsive(current_pid):
        global_portal_session.ensure_initialized()
        try:
            global_portal_session.active_driver().set_window_state(current_pid, "minimize")
            new_pid = restart_process(current_pid)
            initial_elements = _bind_focus_and_collect_elements(new_pid)
            return {
                "status": "restarted",
                "old_pid": current_pid,
                "pid": new_pid,
                "target": target,
                **_lifecycle_metadata(initial_elements),
            }
        except Exception as exc:
            logger.warning("Hot restart failed, falling back to clean launch: %s", exc)

    # In live window-selection mode before a portal session exists, launch the target app first so
    # its window is visible in the OS portal picker; in virtual/mock mode, init first so
    # WAYLAND_DISPLAY points to the isolated compositor before spawning the app.
    defer_portal_init = (
        not global_portal_session.is_active()
        and cfg.display_mode != "virtual"
        and not cfg.mock_mode
    )
    if not defer_portal_init:
        global_portal_session.ensure_initialized()

    pid = launch(target, args or [], cwd)
    if defer_portal_init:
        import time

        time.sleep(0.5)
        try:
            global_portal_session.ensure_initialized()
        except Exception:
            try:
                terminate(pid)
            except Exception:
                pass
            raise

    initial_elements = _bind_focus_and_collect_elements(pid)

    return {
        "status": "launched",
        "pid": pid,
        "target": target,
        "args": args or [],
        "cwd": cwd,
        **_lifecycle_metadata(initial_elements),
    }


def restart_app(pid: int | None = None) -> dict[str, Any]:
    """Gracefully terminates and re-launches an active process launched by this MCP session,
    preserving its original launch command, arguments, and working directory.

    NOTE: Only applications launched by this MCP server can be restarted. External applications
    or user-opened windows cannot be restarted by the MCP server.
    """
    effective_pid = pid or global_portal_session.target_pid
    if not effective_pid:
        raise RuntimeError("No active managed application PID to restart.")

    from wayland_computer_use_mcp.process import _lock, _processes

    with _lock:
        is_owned = effective_pid in _processes

    if not is_owned:
        raise PermissionError(
            f"PID {effective_pid} was not launched by this MCP server. "
            "Only MCP-launched applications can be restarted to preserve "
            "consistency of user applications."
        )

    global_portal_session.active_driver().set_window_state(effective_pid, "minimize")
    cfg = get_config()
    if not cfg.mock_mode and not cfg.lazy_portal:
        ensure_portal_dialogs_above()
    global_portal_session.ensure_initialized()

    new_pid = restart_process(effective_pid)
    initial_elements = _bind_focus_and_collect_elements(new_pid)

    return {
        "status": "restarted",
        "old_pid": effective_pid,
        "new_pid": new_pid,
        **_lifecycle_metadata(initial_elements),
    }


def terminate_app(pid: int | None = None) -> dict[str, Any]:
    """Terminates an owned application process and closes idle virtual windows."""
    effective_pid = pid or global_portal_session.target_pid
    if not effective_pid:
        raise RuntimeError("No target PID to terminate.")

    terminate(effective_pid)
    if global_portal_session.target_pid == effective_pid:
        global_portal_session.target_pid = None

    remaining = list_active_processes()
    virtual_closed = False
    if not remaining:
        virtual_closed = global_portal_session.close_virtual_if_idle()
    elif global_portal_session.target_pid is None:
        global_portal_session.target_pid = remaining[-1]["pid"]

    return {
        "status": "terminated",
        "pid": effective_pid,
        "virtual_session_closed": virtual_closed,
    }


def list_managed_apps() -> dict[str, Any]:
    """Lists all active application processes managed by this MCP session."""
    active = list_active_processes()
    return {
        "active_apps_count": len(active),
        "target_pid": global_portal_session.target_pid,
        "apps": active,
    }


def get_app_logs(pid: int | None = None, lines: int = 50) -> str:
    """Retrieves captured stdout and stderr execution logs for an application process."""
    effective_pid = pid or global_portal_session.target_pid
    if not effective_pid:
        raise RuntimeError("No active target PID. Provide pid or launch an app first.")
    return get_logs(effective_pid, lines=lines)


def register_process_tools(mcp: FastMCP) -> None:
    """Registers application process lifecycle tools onto the FastMCP server."""
    mutate_ann = ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    )
    destructive_ann = ToolAnnotations(
        readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False
    )
    readonly_ann = ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )
    mcp.tool(annotations=mutate_ann)(launch_app)
    mcp.tool(annotations=mutate_ann)(restart_app)
    mcp.tool(annotations=destructive_ann)(terminate_app)
    mcp.tool(annotations=readonly_ann)(get_app_logs)
    mcp.tool(annotations=readonly_ann)(list_managed_apps)
