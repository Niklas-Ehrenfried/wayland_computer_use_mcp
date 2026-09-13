"""Process lifecycle tools for wayland-computer-use-mcp."""

from __future__ import annotations

import logging
from typing import Any

from fastmcp import FastMCP

from wayland_computer_use_mcp.compositors import (
    ensure_portal_dialogs_above,
    minimize_window,
)
from wayland_computer_use_mcp.portal import global_portal_session
from wayland_computer_use_mcp.process import (
    get_logs,
    is_responsive,
    launch,
    list_active_processes,
    prune_dead_processes,
    terminate,
)
from wayland_computer_use_mcp.process import (
    restart as restart_process,
)

logger = logging.getLogger("wayland_computer_use_mcp.tools.process")


def _ensure_session_initialized() -> None:
    if not global_portal_session.is_active():
        global_portal_session.ensure_initialized()


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
    ensure_portal_dialogs_above()
    _ensure_session_initialized()

    current_pid = global_portal_session.target_pid
    if restart and current_pid and is_responsive(current_pid):
        try:
            minimize_window(current_pid)
            new_pid = restart_process(current_pid)
            try:
                from wayland_computer_use_mcp.tools.visual_tools import focus_window

                focus_window(new_pid)
            except Exception:
                pass
            return {
                "status": "restarted",
                "old_pid": current_pid,
                "pid": new_pid,
                "target": target,
                "session_handle": global_portal_session.session_handle,
                "eis_connected": global_portal_session.eis_fd is not None,
            }
        except Exception as exc:
            logger.warning("Hot restart failed, falling back to clean launch: %s", exc)

    pid = launch(target, args or [], cwd)
    global_portal_session.target_pid = pid

    try:
        from wayland_computer_use_mcp.tools.visual_tools import focus_window

        focus_window(pid)
    except Exception:
        pass

    initial_elements: list[dict[str, Any]] = []
    try:
        from wayland_computer_use_mcp.a11y import get_application_tree

        tree = get_application_tree(pid)
        initial_elements = tree.get("interactive_elements", [])
    except Exception:
        pass

    return {
        "status": "launched",
        "pid": pid,
        "target": target,
        "args": args or [],
        "cwd": cwd,
        "session_handle": global_portal_session.session_handle,
        "restore_token": global_portal_session.restore_token,
        "eis_connected": global_portal_session.eis_fd is not None,
        "interactive_elements": initial_elements,
    }


def restart_app(pid: int) -> dict[str, Any]:
    """Gracefully terminates and re-launches an active process preserving arguments."""
    minimize_window(pid)
    ensure_portal_dialogs_above()
    _ensure_session_initialized()

    new_pid = restart_process(pid)
    global_portal_session.target_pid = new_pid
    try:
        from wayland_computer_use_mcp.tools.visual_tools import focus_window

        focus_window(new_pid)
    except Exception:
        pass

    initial_elements: list[dict[str, Any]] = []
    try:
        from wayland_computer_use_mcp.a11y import get_application_tree

        tree = get_application_tree(new_pid)
        initial_elements = tree.get("interactive_elements", [])
    except Exception:
        pass

    return {
        "status": "restarted",
        "old_pid": pid,
        "new_pid": new_pid,
        "session_handle": global_portal_session.session_handle,
        "restore_token": global_portal_session.restore_token,
        "eis_connected": global_portal_session.eis_fd is not None,
        "interactive_elements": initial_elements,
    }


def terminate_app(pid: int | None = None) -> dict[str, Any]:
    """Terminates an owned application process."""
    effective_pid = pid or global_portal_session.target_pid
    if not effective_pid:
        raise RuntimeError("No target PID to terminate.")

    terminate(effective_pid)
    if global_portal_session.target_pid == effective_pid:
        global_portal_session.target_pid = None

    return {
        "status": "terminated",
        "pid": effective_pid,
    }


def list_managed_apps() -> dict[str, Any]:
    """Lists all active application processes managed by this MCP session."""
    active = list_active_processes()
    return {
        "active_apps_count": len(active),
        "target_pid": global_portal_session.target_pid,
        "apps": active,
    }


def check_app_liveness(pid: int | None = None) -> dict[str, Any]:
    """Verifies if an application process is active, alive, and responsive."""
    prune_dead_processes()
    effective_pid = pid or global_portal_session.target_pid
    if not effective_pid:
        return {"pid": 0, "alive": False, "responsive": False}

    responsive = is_responsive(effective_pid)
    if not responsive and global_portal_session.target_pid == effective_pid:
        global_portal_session.target_pid = None

    return {
        "pid": effective_pid,
        "alive": responsive,
        "responsive": responsive,
    }


def get_app_logs(pid: int | None = None, lines: int = 50) -> str:
    """Retrieves captured stdout and stderr execution logs for an application process."""
    effective_pid = pid or global_portal_session.target_pid
    if not effective_pid:
        raise RuntimeError("No active target PID. Provide pid or launch an app first.")
    return get_logs(effective_pid, lines=lines)


def register_process_tools(mcp: FastMCP) -> None:
    """Registers application process lifecycle tools onto the FastMCP server."""
    mcp.tool()(launch_app)
    mcp.tool()(terminate_app)
    mcp.tool()(get_app_logs)
    mcp.tool()(list_managed_apps)
