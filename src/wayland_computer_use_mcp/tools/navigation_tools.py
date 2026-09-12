"""Semantic accessibility tree navigation, widget interaction, and batching tools."""

from __future__ import annotations

import logging
import time
from typing import Any

from fastmcp import FastMCP

from wayland_computer_use_mcp.a11y import (
    get_application_tree,
    get_cached_node,
    invoke_node_action,
)
from wayland_computer_use_mcp.delta import wrap_with_delta
from wayland_computer_use_mcp.portal import global_portal_session
from wayland_computer_use_mcp.process import (
    check_process_health_and_enrich,
    is_responsive,
)

logger = logging.getLogger("wayland_computer_use_mcp.tools.navigation")


def _resolve_target_node_id(pid: int, target: str, role: str | None = None) -> str | None:
    """Resolves a target that might be either a compact node ID or a widget label
    with optional role filtering.
    """
    cached = get_cached_node(pid, target)
    if cached:
        return target

    from wayland_computer_use_mcp.a11y import _node_cache

    target_lower = target.strip().lower()
    role_lower = role.strip().lower() if role else None

    cached_for_pid = _node_cache.get(pid, {})
    for nid, node in cached_for_pid.items():
        if role_lower and role_lower not in node.get("role", "").strip().lower():
            continue
        if node.get("name", "").strip().lower() == target_lower:
            return nid
    for nid, node in cached_for_pid.items():
        if role_lower and role_lower not in node.get("role", "").strip().lower():
            continue
        if target_lower in node.get("name", "").strip().lower():
            return nid

    tree = get_application_tree(pid)
    elements = tree.get("interactive_elements", [])

    for el in elements:
        if role_lower and role_lower not in el.get("role", "").strip().lower():
            continue
        if el.get("name", "").strip().lower() == target_lower:
            return el.get("id")

    for el in elements:
        if role_lower and role_lower not in el.get("role", "").strip().lower():
            continue
        if target_lower in el.get("name", "").strip().lower():
            return el.get("id")

    return None


def inspect_ui_tree(pid: int | None = None, max_depth: int = 24) -> dict[str, Any]:
    """Inspects semantic accessibility tree and returns compact 1D interactive elements.

    Includes window geometry, widget states, and automatically embeds process health.
    """
    effective_pid = pid or global_portal_session.target_pid
    if not effective_pid:
        raise RuntimeError("No active managed application PID. Launch an app first or provide pid.")

    if not global_portal_session.is_mock and not is_responsive(effective_pid):
        raise RuntimeError(f"Application with PID {effective_pid} is not active or responsive.")

    global_portal_session.target_pid = effective_pid
    try:
        from wayland_computer_use_mcp.tools.visual_tools import focus_window

        focus_window(effective_pid)
    except Exception:
        pass

    tree = get_application_tree(effective_pid, max_depth=max_depth)
    tree["pid"] = effective_pid
    tree["responsive"] = is_responsive(effective_pid)
    return tree


def interact_with_node(
    target: str | None = None,
    action: str = "click",
    text: str | None = None,
    node_id: str | None = None,
    pid: int | None = None,
    start_offset: int = 0,
    end_offset: int = -1,
) -> str:
    """Interacts with an interactive UI widget by 1D node ID (e.g. 'b1', 'e1') or label.

    Executes Level 1 AT-SPI actions (with Level 2 coordinate fallback), visibly moves cursor,
    and returns the post-action UI tree delta (modified/appeared widgets).
    Supports actions: click, type, clear, select_all, select_range, copy, paste, cut, drag_select,
    double_click, right_click, hover.
    """
    resolved_target = target or node_id
    if not resolved_target:
        raise ValueError("Must provide either 'target' or 'node_id'.")

    effective_pid = pid or global_portal_session.target_pid
    if not effective_pid:
        if global_portal_session.is_mock:
            effective_pid = 0
        else:
            raise RuntimeError(
                "No active managed application PID. Launch an app first or supply pid."
            )

    if not global_portal_session.is_mock and effective_pid > 0 and not is_responsive(effective_pid):
        raise RuntimeError(f"Application with PID {effective_pid} is not active or responsive.")

    if effective_pid > 0:
        global_portal_session.target_pid = effective_pid
        try:
            from wayland_computer_use_mcp.tools.visual_tools import focus_window

            focus_window(effective_pid)
        except Exception:
            pass

    resolved_node_id = _resolve_target_node_id(effective_pid, resolved_target)
    if not resolved_node_id:
        if global_portal_session.is_mock:
            resolved_node_id = resolved_target
        else:
            raise ValueError(
                f"Element matching '{resolved_target}' not found in "
                f"accessibility tree for PID {effective_pid}."
            )

    act = action.strip().lower()

    def _execute() -> str:
        return invoke_node_action(
            effective_pid,
            resolved_node_id,
            action=act,
            text=text,
            start_offset=start_offset,
            end_offset=end_offset,
        )

    if effective_pid > 0 and not global_portal_session.is_mock:
        raw_res = wrap_with_delta(effective_pid, _execute, settle_ms=80)
        return check_process_health_and_enrich(effective_pid, raw_res)
    else:
        return _execute()


def click_element_by_label(
    label: str,
    role: str | None = None,
    pid: int | None = None,
) -> str:
    """Finds an interactive element matching label in UI tree and clicks it with delta report."""
    effective_pid = pid or global_portal_session.target_pid or 0
    resolved = _resolve_target_node_id(effective_pid, label, role=role)
    target = resolved or label
    return interact_with_node(target=target, action="click", pid=effective_pid)


def batch_actions(
    actions: list[dict[str, Any]],
    pid: int | None = None,
) -> dict[str, Any]:
    """Executes a sequence of consecutive UI interactions with fail-fast reporting.

    Supported step schemas:
    - {"action": "interact"|"click", "node_id": "b1"}
    - {"action": "type", "text": "hello", "node_id": "e1"}
    - {"action": "click", "x": 100, "y": 200, "button": "left"}
    - {"action": "key", "keys": ["ctrl", "a"]}
    - {"action": "hover", "node_id": "b1"}
    - {"action": "wait", "ms": 200}
    """
    effective_pid = pid or global_portal_session.target_pid
    if not effective_pid:
        raise RuntimeError("No active managed application PID. Launch an app first or supply pid.")
    if not global_portal_session.is_mock and not is_responsive(effective_pid):
        raise RuntimeError(f"Application with PID {effective_pid} is not active or responsive.")

    global_portal_session.target_pid = effective_pid
    try:
        from wayland_computer_use_mcp.tools.visual_tools import focus_window

        focus_window(effective_pid)
    except Exception:
        pass

    executed_results: list[str] = []
    total_steps = len(actions)

    for idx, step in enumerate(actions, start=1):
        action_type = step.get("action", "").lower()
        try:
            if action_type in ("interact", "click"):
                if "node_id" in step:
                    step_res = invoke_node_action(effective_pid, step["node_id"], action="click")
                elif "x" in step and "y" in step:
                    btn = step.get("button", "left")
                    step_res = global_portal_session.dispatch_click(
                        step["x"], step["y"], button=btn
                    )
                else:
                    raise ValueError(f"Step {idx} 'click' requires either node_id or (x, y)")

            elif action_type == "type":
                txt = step.get("text", "")
                if "node_id" in step:
                    cached = get_cached_node(effective_pid, step["node_id"])
                    center = cached.get("center", [0, 0]) if cached else [0, 0]
                    step_res = global_portal_session.dispatch_type_text(
                        txt, x=center[0], y=center[1]
                    )
                else:
                    step_res = global_portal_session.dispatch_type_text(
                        txt, x=step.get("x"), y=step.get("y")
                    )

            elif action_type in ("key", "key_combination"):
                keys = step.get("keys", [])
                step_res = global_portal_session.dispatch_key_combination(keys)

            elif action_type == "scroll":
                dx = step.get("dx", 0)
                dy = step.get("dy", 0)
                step_res = global_portal_session.dispatch_scroll(dx, dy)

            elif action_type == "hover":
                if "node_id" in step:
                    cached = get_cached_node(effective_pid, step["node_id"])
                    center = cached.get("center", [0, 0]) if cached else [0, 0]
                    step_res = global_portal_session.dispatch_hover(
                        center[0], center[1], duration_ms=step.get("duration_ms", 100)
                    )
                else:
                    step_res = global_portal_session.dispatch_hover(
                        step["x"], step["y"], duration_ms=step.get("duration_ms", 100)
                    )

            elif action_type == "wait":
                ms = step.get("ms", 100)
                time.sleep(ms / 1000.0)
                step_res = f"Waited {ms}ms"

            else:
                raise ValueError(f"Unknown action type '{action_type}' at step {idx}")

            enriched = check_process_health_and_enrich(effective_pid, step_res)
            executed_results.append(f"Step {idx}: {enriched}")

        except Exception as exc:
            return {
                "status": "failed",
                "failed_step": idx,
                "total_steps": total_steps,
                "error": str(exc),
                "executed_steps": executed_results,
            }

    return {
        "status": "success",
        "executed_steps_count": len(executed_results),
        "total_steps": total_steps,
        "results": executed_results,
    }


def register_navigation_tools(mcp: FastMCP) -> None:
    """Registers semantic tree inspection and interaction tools onto the FastMCP server."""
    mcp.tool()(inspect_ui_tree)
    mcp.tool()(interact_with_node)
    mcp.tool()(batch_actions)
