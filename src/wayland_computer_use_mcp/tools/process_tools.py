"""Process lifecycle tools for wayland-computer-use-mcp."""

from __future__ import annotations

import logging
import time
from typing import Any

from fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from wayland_computer_use_mcp.config import get_config, resolve_effective_display_mode
from wayland_computer_use_mcp.portal import global_portal_session
from wayland_computer_use_mcp.process import (
    get_logs,
    get_recent_traceback,
    is_responsive,
    launch,
    list_active_processes,
    terminate,
)
from wayland_computer_use_mcp.process import (
    restart as restart_process,
)
from wayland_computer_use_mcp.security import global_clamper, global_preemption_manager
from wayland_computer_use_mcp.tools import emit_ctx_log

logger = logging.getLogger("wayland_computer_use_mcp.tools.process")


def ensure_portal_dialogs_above() -> bool:
    """Delegates portal dialog foregrounding through the active session driver."""
    return global_portal_session.active_driver().ensure_dialogs_above()


def _session_summary() -> dict[str, Any]:
    cfg = get_config()
    effective_mode, effective_headed, _ = resolve_effective_display_mode(cfg)
    driver = global_portal_session.active_driver()
    meta = global_portal_session.metadata()
    sink = None
    try:
        sink = driver.get_input_sink()
    except Exception:
        sink = None
    return {
        "mode": "mock" if cfg.mock_mode else effective_mode,
        "configured_mode": cfg.display_mode,
        "virtual_headed": effective_headed if effective_mode == "virtual" else cfg.virtual_headed,
        "driver": meta.get("driver", getattr(driver, "name", "unknown")),
        "adapter": meta.get("adapter"),
        "backend_kind": meta.get("backend_kind"),
        "isolated": bool(meta.get("isolated", False)),
        "is_active": global_portal_session.is_active(),
        "input_ready": sink is not None or bool(meta.get("eis_connected", False)),
    }


def _lifecycle_metadata(
    elements: list[dict[str, Any]],
    hierarchy: dict[str, Any] | None = None,
) -> dict[str, Any]:
    meta = global_portal_session.metadata()
    payload: dict[str, Any] = {
        "eis_connected": bool(meta.get("eis_connected", False)),
        "session": _session_summary(),
        "interactive_elements": elements,
    }
    if hierarchy:
        payload["hierarchy"] = hierarchy
    if meta.get("session_handle") is not None:
        payload["session_handle"] = meta.get("session_handle")
    if meta.get("restore_token") is not None:
        payload["restore_token"] = meta.get("restore_token")
    return payload


def _wait_until_window_or_a11y_ready(pid: int, ready_timeout_ms: int) -> dict[str, Any] | None:
    """Polls until AT-SPI elements or compositor geometry appear (or process exits)."""
    if ready_timeout_ms <= 0 or get_config().mock_mode or pid <= 0:
        return None

    from wayland_computer_use_mcp.a11y import get_application_tree

    deadline = time.monotonic() + (ready_timeout_ms / 1000.0)
    geom_seen_at: float | None = None
    driver = global_portal_session.active_driver()
    last_tree: dict[str, Any] | None = None

    while time.monotonic() < deadline:
        if not is_responsive(pid):
            break
        try:
            tree = get_application_tree(pid)
            last_tree = tree
            if tree.get("interactive_elements"):
                return tree
        except Exception:
            pass

        try:
            if driver.query_window_geometry(pid=pid) is not None:
                if geom_seen_at is None:
                    geom_seen_at = time.monotonic()
                elif time.monotonic() - geom_seen_at >= 0.3:
                    break
        except Exception:
            pass
        time.sleep(0.1)

    return last_tree


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
    ready_timeout_ms: int = 4000,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Launches a Python GUI script, native binary, or $PATH command with auto-focus.

    Waits briefly (up to ``ready_timeout_ms`` in live/virtual mode) so initial AT-SPI
    interactive elements and compact hierarchy are immediately usable without extra polling.
    """
    cfg = get_config()
    if not cfg.mock_mode and not cfg.lazy_portal:
        ensure_portal_dialogs_above()

    emit_ctx_log(ctx, f"Launching target: {target}")
    current_pid = global_portal_session.target_pid
    if restart and current_pid and is_responsive(current_pid):
        global_portal_session.ensure_initialized()
        try:
            global_portal_session.active_driver().set_window_state(current_pid, "minimize")
            new_pid = restart_process(current_pid)
            ready_tree = _wait_until_window_or_a11y_ready(new_pid, ready_timeout_ms)
            initial_elements = _bind_focus_and_collect_elements(new_pid)
            hierarchy = ready_tree.get("hierarchy") if ready_tree else None
            emit_ctx_log(ctx, f"Restarted PID {new_pid} ({len(initial_elements)} elements)")
            return {
                "status": "restarted",
                "old_pid": current_pid,
                "pid": new_pid,
                "target": target,
                **_lifecycle_metadata(initial_elements, hierarchy=hierarchy),
            }
        except Exception as exc:
            logger.warning("Hot restart failed, falling back to clean launch: %s", exc)

    # In live window-selection mode before a portal session exists, launch the target app first so
    # its window is visible in the OS portal picker; in virtual/mock mode, init first so
    # WAYLAND_DISPLAY points to the isolated compositor before spawning the app.
    effective_mode, _, effective_lazy = resolve_effective_display_mode(cfg)
    defer_portal_init = (
        not global_portal_session.is_active() and effective_mode != "virtual" and not cfg.mock_mode
    )
    if not defer_portal_init:
        global_portal_session.ensure_initialized()

    pid = launch(target, args or [], cwd)
    if defer_portal_init:
        time.sleep(0.5)
        try:
            global_portal_session.ensure_initialized()
        except Exception:
            try:
                terminate(pid)
            except Exception:
                pass
            raise

    ready_tree = _wait_until_window_or_a11y_ready(pid, ready_timeout_ms)
    if not cfg.mock_mode and not global_portal_session.is_mock and not is_responsive(pid):
        logs = get_logs(pid, lines=40)
        tb = get_recent_traceback(pid)
        return {
            "status": "exited_early",
            "exited_early": True,
            "pid": pid,
            "target": target,
            "args": args or [],
            "cwd": cwd,
            "logs": logs,
            "traceback": tb,
            "recovery_hint": (
                "Process exited before a window or AT-SPI tree appeared. "
                "Inspect 'logs'/'traceback', fix the underlying error, and call launch_app() again."
            ),
            **_lifecycle_metadata([]),
        }

    initial_elements = _bind_focus_and_collect_elements(pid)
    hierarchy = ready_tree.get("hierarchy") if ready_tree else None
    emit_ctx_log(ctx, f"Launched PID {pid} ({len(initial_elements)} elements)")

    return {
        "status": "launched",
        "pid": pid,
        "target": target,
        "args": args or [],
        "cwd": cwd,
        **_lifecycle_metadata(initial_elements, hierarchy=hierarchy),
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
    _, _, effective_lazy = resolve_effective_display_mode(cfg)
    if not cfg.mock_mode and not effective_lazy:
        ensure_portal_dialogs_above()
    global_portal_session.ensure_initialized()

    new_pid = restart_process(effective_pid)
    initial_elements = _bind_focus_and_collect_elements(new_pid)

    return {
        "status": "restarted",
        "pid": new_pid,
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
    global_portal_session.clear_pid_state(effective_pid)
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


def get_session_status() -> dict[str, Any]:
    """Reports active display mode, compositor adapter, input readiness, and target window."""
    cfg = get_config()
    summary = _session_summary()
    w, h = global_clamper.dimensions
    pause_path = global_preemption_manager.takeover_pause_path()
    summary.update(
        {
            "target_pid": global_portal_session.target_pid,
            "target_title": global_portal_session.target_title,
            "window_bounds": [
                global_portal_session.window_offset_x,
                global_portal_session.window_offset_y,
                w,
                h,
            ],
            "window_scale": global_portal_session.window_scale,
            "takeover_paused": (
                global_preemption_manager.is_paused
                or global_preemption_manager._is_pause_file_active()
            ),
            "takeover_pause_file": str(pause_path) if pause_path else None,
            "artifact_retention": {
                "max_screenshots": cfg.max_screenshots,
                "artifact_ttl_hours": cfg.artifact_ttl_hours,
            },
            "managed_apps_count": len(list_active_processes()),
        }
    )
    return summary


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
    mcp.tool(annotations=readonly_ann)(get_session_status)
