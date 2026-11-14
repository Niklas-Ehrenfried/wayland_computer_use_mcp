"""Semantic accessibility tree navigation, widget interaction, and batching tools."""

from __future__ import annotations

import logging
import time
from typing import Annotated, Any

from fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from wayland_computer_use_mcp.a11y import (
    build_compact_hierarchy,
    get_application_tree,
    get_cached_node,
    global_event_listener,
    invoke_node_action,
)
from wayland_computer_use_mcp.delta import (
    InteractionResult,
    compute_ui_delta,
    format_delta_markdown,
    snapshot_interactive_state,
    wrap_with_delta,
)
from wayland_computer_use_mcp.portal import global_portal_session
from wayland_computer_use_mcp.process import (
    check_process_health_and_enrich,
    is_responsive,
)

logger = logging.getLogger("wayland_computer_use_mcp.tools.navigation")


def _match_element(
    candidates: list[tuple[str, dict[str, Any]]],
    target_lower: str,
    role_lower: str | None,
) -> str | None:
    # First pass: exact match
    for nid, node in candidates:
        if role_lower and role_lower not in node.get("role", "").strip().lower():
            continue
        if node.get("name", "").strip().lower() == target_lower:
            return nid
    # Second pass: substring match
    for nid, node in candidates:
        if role_lower and role_lower not in node.get("role", "").strip().lower():
            continue
        if target_lower in node.get("name", "").strip().lower():
            return nid
    return None


def _resolve_target_node_id(pid: int, target: str, role: str | None = None) -> str | None:
    """Resolves a target that might be either a compact node ID or a widget label
    with optional role filtering.
    """
    from wayland_computer_use_mcp.a11y import _node_cache

    pid_cache = _node_cache.get(pid, {})
    if target in pid_cache:
        return target

    if not role:
        cached = get_cached_node(pid, target)
        if cached and "id" in cached:
            return cached["id"]

    target_lower = target.strip().lower()
    role_lower = role.strip().lower() if role else None

    # Check cached nodes
    cached_for_pid = list(_node_cache.get(pid, {}).items())
    matched = _match_element(cached_for_pid, target_lower, role_lower)
    if matched:
        return matched

    # Fetch tree and match
    tree = get_application_tree(pid)
    elements = [(el.get("id", ""), el) for el in tree.get("interactive_elements", [])]
    return _match_element(elements, target_lower, role_lower)


def inspect_ui_tree(
    pid: int | None = None,
    max_depth: int = 24,
    include_raw_tree: Annotated[
        bool,
        Field(
            description=(
                "Include the unpruned raw AT-SPI node tree (D-Bus paths, layout wrappers). "
                "Defaults to False for a compact functional hierarchy and interactive elements."
            )
        ),
    ] = False,
) -> dict[str, Any]:
    """Inspects semantic accessibility tree and returns functional hierarchy + 1D interactive nodes.

    Includes compact functional component hierarchy, actionable elements, screen bounds, and
    process health by default. Set include_raw_tree=True only when debugging raw AT-SPI nodes.
    """
    effective_pid = pid or global_portal_session.target_pid
    if not effective_pid:
        raise RuntimeError("No active managed application PID. Launch an app first or provide pid.")

    if not global_portal_session.is_mock and not is_responsive(effective_pid):
        raise RuntimeError(f"Application with PID {effective_pid} is not active or responsive.")

    global_portal_session.target_pid = effective_pid

    tree = get_application_tree(effective_pid, max_depth=max_depth)
    responsive = is_responsive(effective_pid)
    hierarchy = tree.get("hierarchy") or build_compact_hierarchy(tree)

    if include_raw_tree:
        tree["pid"] = effective_pid
        tree["responsive"] = responsive
        tree["hierarchy"] = hierarchy
        return tree

    payload: dict[str, Any] = {
        "pid": effective_pid,
        "name": tree.get("name", ""),
        "role": tree.get("role", "application"),
        "responsive": responsive,
    }
    if "screen_bounds" in tree:
        payload["screen_bounds"] = tree["screen_bounds"]
    bounds = tree.get("bounds")
    if isinstance(bounds, list) and len(bounds) == 4 and bounds[2] > 0 and bounds[3] > 0:
        payload["bounds"] = bounds
    payload["hierarchy"] = hierarchy
    payload["interactive_elements"] = tree.get("interactive_elements", [])
    return payload


def interact_with_node(
    target: Annotated[
        str | None,
        Field(description="Primary target: compact element ID (e.g. 'b1', 'e1') or widget label."),
    ] = None,
    action: str = "click",
    text: str | None = None,
    value: float | None = None,
    role: Annotated[
        str | None,
        Field(description="Optional accessible role filter (e.g. 'push button', 'entry')."),
    ] = None,
    node_id: Annotated[
        str | None,
        Field(description="Deprecated alias for 'target'; prefer passing 'target'."),
    ] = None,
    pid: int | None = None,
    start_offset: int = 0,
    end_offset: int = -1,
    settle_timeout_ms: int = 0,
) -> InteractionResult:
    """Interacts with an interactive UI widget by 1D node ID (e.g. 'b1', 'e1') or label.

    Executes Level 1 AT-SPI actions silently (without moving the cursor), falling back to
    Level 2 coordinate/keyboard input only when AT-SPI is unsupported, and returns the
    post-action UI tree delta (modified/appeared widgets). Optional ``role`` narrows name matches
    like Playwright's ``getByRole``.
    Supports actions: click, type, set_value, clear, select_all, select_range, copy,
    paste, cut, drag_select, double_click, right_click, hover.
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

    resolved_node_id = _resolve_target_node_id(effective_pid, resolved_target, role=role)
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
            value=value,
            start_offset=start_offset,
            end_offset=end_offset,
        )

    target_ident = resolved_node_id or resolved_target
    is_live = effective_pid > 0 and not global_portal_session.is_mock
    settle_ms = settle_timeout_ms if settle_timeout_ms > 0 else (60 if is_live else 0)
    raw_res = wrap_with_delta(
        effective_pid if not global_portal_session.is_mock else 0,
        _execute,
        settle_ms=settle_ms,
        action_name=act,
        target=target_ident,
    )
    return check_process_health_and_enrich(effective_pid, raw_res)


def batch_actions(
    actions: list[dict[str, Any]],
    pid: int | None = None,
) -> dict[str, Any]:
    """Executes a sequence of consecutive UI interactions with fail-fast reporting.

    Supported step schemas:
    - {"action": "interact"|"click"|"double_click"|"right_click", "target": "b1"}
    - {"action": "type"|"set_value"|"clear", "target": "e1", "text": "hello"}
    - {"action": "click", "x": 100, "y": 200, "button": "left"}
    - {"action": "key", "keys": ["ctrl", "a"]}
    - {"action": "hover", "target": "b1"}
    - {"action": "wait", "ms": 200}
    """
    effective_pid = pid or global_portal_session.target_pid
    if not effective_pid:
        raise RuntimeError("No active managed application PID. Launch an app first or supply pid.")
    if not global_portal_session.is_mock and not is_responsive(effective_pid):
        raise RuntimeError(f"Application with PID {effective_pid} is not active or responsive.")

    global_portal_session.target_pid = effective_pid

    executed_results: list[str] = []
    step_records: list[dict[str, Any]] = []
    total_steps = len(actions)
    is_live = effective_pid > 0 and not global_portal_session.is_mock
    batch_before = snapshot_interactive_state(effective_pid) if is_live else {}

    for idx, step in enumerate(actions, start=1):
        action_type = step.get("action", "").lower()
        before = None
        if is_live and action_type != "wait":
            before = snapshot_interactive_state(effective_pid)

        try:
            raw_target = step.get("target") or step.get("node_id")
            step_role = step.get("role")
            resolved_id = (
                _resolve_target_node_id(effective_pid, str(raw_target), role=step_role)
                or str(raw_target)
                if raw_target
                else None
            )

            if action_type in ("interact", "click", "double_click", "right_click"):
                act = "click" if action_type == "interact" else action_type
                if resolved_id is not None:
                    step_res = invoke_node_action(effective_pid, resolved_id, action=act)
                elif "x" in step and "y" in step:
                    if act == "double_click":
                        step_res = global_portal_session.dispatch_double_click(
                            step["x"], step["y"], button=step.get("button", "left")
                        )
                    elif act == "right_click":
                        step_res = global_portal_session.dispatch_right_click(step["x"], step["y"])
                    else:
                        step_res = global_portal_session.dispatch_click(
                            step["x"], step["y"], button=step.get("button", "left")
                        )
                else:
                    raise ValueError(
                        f"Step {idx} '{action_type}' requires target/node_id or (x, y)"
                    )

            elif action_type in ("type", "set_value", "clear"):
                txt = step.get("text", "")
                if resolved_id is not None:
                    step_res = invoke_node_action(
                        effective_pid,
                        resolved_id,
                        action=action_type,
                        text=txt,
                        value=step.get("value"),
                    )
                elif action_type == "type":
                    step_res = global_portal_session.dispatch_type_text(
                        txt, x=step.get("x"), y=step.get("y")
                    )
                else:
                    raise ValueError(f"Step {idx} '{action_type}' requires target/node_id")

            elif action_type in ("key", "key_combination"):
                keys = step.get("keys", [])
                step_res = global_portal_session.dispatch_key_combination(keys)

            elif action_type == "scroll":
                dx = step.get("dx", 0)
                dy = step.get("dy", 0)
                step_res = global_portal_session.dispatch_scroll(dx, dy)

            elif action_type == "hover":
                duration_ms = step.get("duration_ms", 100)
                if resolved_id is not None:
                    cached = get_cached_node(effective_pid, resolved_id)
                    center = cached.get("center", [0, 0]) if cached else [0, 0]
                    step_res = global_portal_session.dispatch_hover(
                        center[0], center[1], duration_ms=duration_ms
                    )
                elif "x" in step and "y" in step:
                    step_res = global_portal_session.dispatch_hover(
                        step["x"], step["y"], duration_ms=duration_ms
                    )
                else:
                    raise ValueError(f"Step {idx} 'hover' requires target/node_id or (x, y)")

            elif action_type == "wait":
                ms = step.get("ms", 100)
                time.sleep(ms / 1000.0)
                step_res = f"Waited {ms}ms"

            else:
                raise ValueError(f"Unknown action type '{action_type}' at step {idx}")

            step_delta: dict[str, list[dict[str, Any]]] = {
                "modified": [],
                "appeared": [],
                "hidden": [],
            }
            if before is not None:
                time.sleep(0.05)
                after = snapshot_interactive_state(effective_pid)
                step_delta = compute_ui_delta(before, after)

            enriched = check_process_health_and_enrich(effective_pid, step_res)
            has_delta = bool(
                step_delta["modified"] or step_delta["appeared"] or step_delta["hidden"]
            )
            step_summary = enriched + (format_delta_markdown(step_delta) if has_delta else "")
            executed_results.append(f"Step {idx}: {step_summary}")
            step_records.append(
                {
                    "step": idx,
                    "action": action_type,
                    "result": enriched,
                    "ui_changes": step_delta,
                }
            )

        except Exception as exc:
            failure_info: dict[str, Any] = {
                "status": "failed",
                "failed_step": idx,
                "total_steps": total_steps,
                "error": str(exc),
                "executed_steps": executed_results,
                "steps": step_records,
            }
            if not global_portal_session.is_mock and global_portal_session.is_active():
                try:
                    global_portal_session.capture_frame(
                        save_to_disk=True, filename="batch_failure.png"
                    )
                    if global_portal_session.last_saved_frame_path:
                        failure_info["failure_screenshot"] = (
                            global_portal_session.last_saved_frame_path
                        )
                except Exception:
                    pass
            return failure_info

    net_delta = (
        compute_ui_delta(batch_before, snapshot_interactive_state(effective_pid))
        if is_live and batch_before
        else {"modified": [], "appeared": [], "hidden": []}
    )
    return {
        "status": "success",
        "executed_steps_count": len(executed_results),
        "total_steps": total_steps,
        "results": executed_results,
        "steps": step_records,
        "ui_changes": net_delta,
    }


def watch_ui_events(
    timeout_ms: int = 500,
    pid: int | None = None,
) -> dict[str, Any]:
    """Awaits UI settlement by monitoring real-time AT-SPI2 D-Bus mutation events.

    Returns captured mutations and whether the UI has settled, eliminating the need
    to re-query the full accessibility tree after triggers.
    """
    effective_pid = pid or global_portal_session.target_pid
    global_event_listener.start()
    res = global_event_listener.wait_for_settled(timeout_ms=timeout_ms, pid=effective_pid)
    res["pid"] = effective_pid
    return res


def register_navigation_tools(mcp: FastMCP) -> None:
    """Registers semantic tree inspection and interaction tools onto the FastMCP server."""
    readonly_ann = ToolAnnotations(
        readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False
    )
    mutate_ann = ToolAnnotations(
        readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False
    )
    mcp.tool(annotations=readonly_ann)(inspect_ui_tree)
    mcp.tool(annotations=mutate_ann)(interact_with_node)
    mcp.tool(annotations=mutate_ann)(batch_actions)
    mcp.tool(annotations=readonly_ann)(watch_ui_events)
