"""System integration tools including clipboard and desktop entry installation."""

from __future__ import annotations

import logging
from typing import Any

from fastmcp import FastMCP

from wayland_computer_use_mcp.installer import (
    install_desktop_app,
    remove_desktop_app,
)
from wayland_computer_use_mcp.portal import global_portal_session

logger = logging.getLogger("wayland_computer_use_mcp.tools.system")


def clipboard_read() -> str:
    """Reads current text contents from the Wayland clipboard via wl-paste."""
    return global_portal_session.dispatch_clipboard_read()


def clipboard_write(text: str) -> str:
    """Writes text into the Wayland clipboard via wl-copy."""
    return global_portal_session.dispatch_clipboard_write(text)


def install_to_desktop(
    app_id: str,
    name: str,
    exec_path: str,
    icon_path: str | None = None,
    version: str | None = None,
    is_dev: bool | None = None,
) -> dict[str, Any]:
    """Registers a script as a native Linux desktop application with optional version badging."""
    return install_desktop_app(
        app_id=app_id,
        name=name,
        exec_path=exec_path,
        icon_path=icon_path,
        version=version,
        is_dev=is_dev,
    )


def uninstall_from_desktop(app_id: str) -> dict[str, Any]:
    """Removes an installed desktop application entry and associated launcher icon."""
    return remove_desktop_app(app_id=app_id)


def window_control(action: str, pid: int | None = None) -> dict[str, Any]:
    """Controls window state across any active Wayland compositor (KDE, GNOME, Hyprland, Sway).

    Supported actions:
    - 'minimize': Minimizes the target application window.
    - 'maximize': Maximizes the target application window.
    - 'restore': Restores or unminimizes/unmaximizes the target application window.
    - 'focus': Activates and brings the target application window to the foreground.
    - 'close': Requests a clean close of the target window.

    Args:
        action: The desired window management action.
        pid: The target application PID. Defaults to the active managed application.
    """
    effective_pid = pid or global_portal_session.target_pid
    if not effective_pid:
        raise RuntimeError("No active application PID provided or currently managed.")

    from wayland_computer_use_mcp.compositors import (
        activate_and_raise_window,
        set_window_state,
    )

    norm_action = action.strip().lower()
    valid_actions = {"minimize", "maximize", "restore", "focus", "close"}
    if norm_action not in valid_actions:
        raise ValueError(
            f"Invalid window action '{action}'. Must be one of: {', '.join(sorted(valid_actions))}."
        )

    if norm_action == "focus":
        success = activate_and_raise_window(pid=effective_pid)
    else:
        success = set_window_state(effective_pid, norm_action)  # type: ignore

    return {
        "action": norm_action,
        "pid": effective_pid,
        "success": success,
        "status": f"Window action '{norm_action}' {'succeeded' if success else 'submitted'}.",
    }


def register_system_tools(mcp: FastMCP) -> None:
    """Registers clipboard, desktop integration, and window control tools onto FastMCP."""
    mcp.tool()(clipboard_read)
    mcp.tool()(clipboard_write)
    mcp.tool()(install_to_desktop)
    mcp.tool()(uninstall_from_desktop)
    mcp.tool()(window_control)
