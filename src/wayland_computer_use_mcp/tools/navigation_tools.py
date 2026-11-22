"""Semantic accessibility tree navigation, widget interaction, and batching tools."""

from __future__ import annotations

import difflib
import logging
import time
from typing import Annotated, Any, Literal

from fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field

from wayland_computer_use_mcp.a11y import (
    build_compact_hierarchy,
    get_application_tree,
    get_cached_node,
    global_event_listener,
    invoke_node_action,
)
from wayland_computer_use_mcp.artifacts import prune_artifacts
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
    get_recent_traceback,
    is_responsive,
)
from wayland_computer_use_mcp.tools import emit_ctx_log, emit_ctx_progress

logger = logging.getLogger("wayland_computer_use_mcp.tools.navigation")


class BatchActionStep(BaseModel):
    """Schema for an individual step in batch_actions or preset_workflow."""

    model_config = ConfigDict(extra="allow")

    action: Literal[
        "interact",
        "click",
        "double_click",
        "right_click",
        "type",
        "set_value",
        "clear",
        "key",
        "key_combination",
        "scroll",
        "hover",
        "wait",
        "wait_for",
    ]
    target: str | None = None
    node_id: str | None = None
    role: str | None = None
    text: str | None = None
    value: float | None = None
    x: int | None = None
    y: int | None = None
    button: Literal["left", "right", "middle"] | None = None
    keys: list[str] | None = None
    dx: int | None = None
    dy: int | None = None
    duration_ms: int | None = None
    ms: int | None = None
    state: Literal["present", "gone", "enabled", "checked", "focused"] | None = None
    timeout_ms: int | None = None


_ROLE_ALIAS_GROUPS = (
    frozenset({"button", "push button"}),
    frozenset({"entry", "text", "text box"}),
    frozenset({"check box", "checkbox"}),
    frozenset({"combo box", "combobox"}),
)


def _role_matches(node_role: str, requested_role: str | None) -> bool:
    if not requested_role:
        return True
    nr = node_role.strip().lower()
    rr = requested_role.strip().lower()
    if rr in nr:
        return True
    return any(nr in group and rr in group for group in _ROLE_ALIAS_GROUPS)


def _match_element(
    candidates: list[tuple[str, dict[str, Any]]],
    target_lower: str,
    role_lower: str | None,
) -> str | None:
    # First pass: exact match
    for nid, node in candidates:
        if not _role_matches(node.get("role", ""), role_lower):
            continue
        if node.get("name", "").strip().lower() == target_lower:
            return nid
    # Second pass: substring match
    for nid, node in candidates:
        if not _role_matches(node.get("role", ""), role_lower):
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


def _suggest_closest_targets(pid: int, target: str, limit: int = 5) -> list[str]:
    """Returns closest available element IDs/labels/roles to help agents recover cleanly."""
    from wayland_computer_use_mcp.a11y import _node_cache

    items = list(_node_cache.get(pid, {}).values())
    if not items and pid >= 0:
        try:
            items = get_application_tree(pid).get("interactive_elements", [])
        except Exception:
            items = []

    target_lower = target.strip().lower()
    suggestions: list[str] = []
    seen_ids: set[str] = set()

    for elem in items:
        eid = str(elem.get("id", ""))
        name = str(elem.get("name", "")).strip()
        role = str(elem.get("role", "")).strip()
        if target_lower and (target_lower in name.lower() or target_lower in eid.lower()):
            if eid and eid not in seen_ids:
                seen_ids.add(eid)
                suggestions.append(f"{eid} ('{name}' [{role}])")
                if len(suggestions) >= limit:
                    return suggestions

    names = [str(e.get("name", "")).strip() for e in items if e.get("name")]
    for match in difflib.get_close_matches(target, names, n=limit, cutoff=0.45):
        for elem in items:
            eid = str(elem.get("id", ""))
            name = str(elem.get("name", "")).strip()
            role = str(elem.get("role", "")).strip()
            if name == match and eid not in seen_ids:
                seen_ids.add(eid)
                suggestions.append(f"{eid} ('{name}' [{role}])")
                if len(suggestions) >= limit:
                    return suggestions

    for elem in items:
        if len(suggestions) >= limit:
            break
        eid = str(elem.get("id", ""))
        name = str(elem.get("name", "")).strip()
        role = str(elem.get("role", "")).strip()
        if eid and eid not in seen_ids:
            seen_ids.add(eid)
            suggestions.append(f"{eid} ('{name}' [{role}])")
    return suggestions


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
    query: Annotated[
        str | None,
        Field(description="Optional case-insensitive substring filter over element name or ID."),
    ] = None,
    role_filter: Annotated[
        str | None,
        Field(description="Optional case-insensitive accessible role filter (e.g. 'button')."),
    ] = None,
) -> dict[str, Any]:
    """Inspects semantic accessibility tree and returns functional hierarchy + 1D interactive nodes.

    Includes compact functional component hierarchy, actionable elements, screen bounds, and
    process health by default. Use ``query`` / ``role_filter`` to narrow large trees, or set
    ``include_raw_tree=True`` only when debugging raw AT-SPI nodes.
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
    all_elements = tree.get("interactive_elements", [])

    filtered_elements = all_elements
    q_lower = query.strip().lower() if query else None
    r_lower = role_filter.strip().lower() if role_filter else None
    if q_lower or r_lower:
        filtered_elements = [
            el
            for el in all_elements
            if (
                not q_lower
                or q_lower in str(el.get("name", "")).lower()
                or q_lower in str(el.get("id", "")).lower()
            )
            and (not r_lower or r_lower in str(el.get("role", "")).lower())
        ]

    if include_raw_tree:
        tree["pid"] = effective_pid
        tree["responsive"] = responsive
        tree["hierarchy"] = hierarchy
        if q_lower or r_lower:
            tree["total_elements_count"] = len(all_elements)
            tree["interactive_elements"] = filtered_elements
        return tree

    payload: dict[str, Any] = {
        "pid": effective_pid,
        "name": tree.get("name", ""),
        "role": tree.get("role", "application"),
        "responsive": responsive,
    }
    if "tree_generation" in tree:
        payload["tree_generation"] = tree["tree_generation"]
    if tree.get("atspi_available") is False:
        payload["atspi_available"] = False
        if tree.get("reason"):
            payload["reason"] = tree["reason"]
    if "screen_bounds" in tree:
        payload["screen_bounds"] = tree["screen_bounds"]
    bounds = tree.get("bounds")
    if isinstance(bounds, list) and len(bounds) == 4 and bounds[2] > 0 and bounds[3] > 0:
        payload["bounds"] = bounds
    payload["hierarchy"] = hierarchy
    if q_lower or r_lower:
        payload["total_elements_count"] = len(all_elements)
    payload["interactive_elements"] = filtered_elements
    return payload


def interact_with_node(
    target: Annotated[
        str | None,
        Field(description="Primary target: compact element ID (e.g. 'b1', 'e1') or widget label."),
    ] = None,
    action: Literal[
        "click",
        "press",
        "activate",
        "type",
        "set_value",
        "clear",
        "select_all",
        "select",
        "select_range",
        "select_text",
        "copy",
        "paste",
        "cut",
        "drag_select",
        "double_click",
        "right_click",
        "hover",
    ] = "click",
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
            suggestions = _suggest_closest_targets(effective_pid, resolved_target)
            hint = f" Closest matches: {', '.join(suggestions)}" if suggestions else ""
            raise ValueError(
                f"Element matching '{resolved_target}' not found in "
                f"accessibility tree for PID {effective_pid}.{hint}"
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
    settle_ms = settle_timeout_ms if settle_timeout_ms > 0 else (300 if is_live else 0)
    raw_res = wrap_with_delta(
        effective_pid if not global_portal_session.is_mock else 0,
        _execute,
        settle_ms=settle_ms,
        action_name=act,
        target=target_ident,
    )
    return check_process_health_and_enrich(effective_pid, raw_res)


def _matches_wait_state(
    elem: dict[str, Any] | None,
    state: Literal["present", "gone", "enabled", "checked", "focused"],
) -> bool:
    if state == "gone":
        return elem is None
    if elem is None:
        return False
    if state == "present":
        return True
    if state == "enabled":
        return bool(elem.get("enabled"))
    if state == "checked":
        return "checked" in elem.get("states", [])
    if state == "focused":
        return bool(elem.get("focused") or "focused" in elem.get("states", []))
    return False


def wait_for_element(
    target: Annotated[
        str,
        Field(description="Element ID (e.g. 'b1') or widget label to wait for."),
    ],
    role: Annotated[
        str | None,
        Field(description="Optional accessible role filter (e.g. 'push button', 'label')."),
    ] = None,
    state: Literal["present", "gone", "enabled", "checked", "focused"] = "present",
    timeout_ms: int = 5000,
    poll_interval_ms: int = 100,
    pid: int | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Polls AT-SPI until ``target`` satisfies ``state`` or ``timeout_ms`` elapses."""
    effective_pid = pid or global_portal_session.target_pid
    if not effective_pid:
        if global_portal_session.is_mock:
            effective_pid = 0
        else:
            raise RuntimeError(
                "No active managed application PID. Launch an app first or provide pid."
            )

    if effective_pid > 0:
        global_portal_session.target_pid = effective_pid

    target_lower = target.strip().lower()
    role_lower = role.strip().lower() if role else None
    start_t = time.monotonic()
    deadline = start_t + max(0, timeout_ms) / 1000.0
    interval_sec = max(0.02, poll_interval_ms / 1000.0)
    emit_ctx_log(ctx, f"Waiting for '{target}' to become '{state}'...")

    matched_elem: dict[str, Any] | None = None
    while True:
        if not global_portal_session.is_mock and effective_pid > 0:
            if not is_responsive(effective_pid):
                tb = get_recent_traceback(effective_pid)
                if tb:
                    raise RuntimeError(
                        f"Application process (PID {effective_pid}) crashed while waiting!"
                        f"\n\nTraceback:\n{tb}"
                    )
                raise RuntimeError(
                    f"Application with PID {effective_pid} exited while waiting for '{target}'."
                )

        tree = get_application_tree(effective_pid)
        elements = tree.get("interactive_elements", [])
        elem_by_id = {el.get("id", ""): el for el in elements if el.get("id")}
        matched_id = (
            target
            if target in elem_by_id
            else _match_element(
                [(el.get("id", ""), el) for el in elements], target_lower, role_lower
            )
        )
        matched_elem = elem_by_id.get(matched_id) if matched_id else None

        if _matches_wait_state(matched_elem, state):
            elapsed_ms = int((time.monotonic() - start_t) * 1000)
            return {
                "matched": True,
                "target": target,
                "state": state,
                "elapsed_ms": elapsed_ms,
                "element": matched_elem,
                "pid": effective_pid,
            }

        if time.monotonic() >= deadline:
            elapsed_ms = int((time.monotonic() - start_t) * 1000)
            return {
                "matched": False,
                "target": target,
                "state": state,
                "elapsed_ms": elapsed_ms,
                "element": matched_elem,
                "pid": effective_pid,
            }

        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(interval_sec, remaining))


def _classify_batch_error(exc: Exception) -> tuple[str, str]:
    msg = str(exc).lower()
    if isinstance(exc, PermissionError) or "prohibited" in msg or "blocked" in msg:
        return "permission_denied", "Action blocked by security policy or shortcut filter."
    if isinstance(exc, TimeoutError) or "timed out" in msg:
        return "timeout", "Increase timeout_ms or inspect_ui_tree to check current app state."
    if "crashed" in msg or "exited" in msg or "not active or responsive" in msg:
        return "process_crashed", "Inspect get_app_logs(), fix the underlying error, and relaunch."
    if "not found" in msg or "stale node" in msg:
        return "element_not_found", "Run inspect_ui_tree() and target element by label + role."
    if isinstance(exc, ValueError):
        return "invalid_step", "Verify required step keys (target/x/y/text/keys) match schema."
    return "action_failed", "Inspect failure_screenshot or call inspect_ui_tree() before retrying."


def batch_actions(
    actions: list[BatchActionStep],
    pid: int | None = None,
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Executes a sequence of consecutive UI interactions with fail-fast reporting.

    Supported step actions:
    - interact/click/double_click/right_click (by ``target`` or ``x, y``)
    - type/set_value/clear (by ``target`` or focused input)
    - key / key_combination (``keys``), scroll (``dx, dy``), hover
    - wait (``ms``) and wait_for (``target``, ``state``, ``timeout_ms``)
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

    for idx, raw_step in enumerate(actions, start=1):
        step: dict[str, Any] = (
            raw_step.model_dump(exclude_none=True)
            if hasattr(raw_step, "model_dump")
            else dict(raw_step)
        )
        action_type = str(step.get("action", "")).lower()
        emit_ctx_progress(ctx, idx - 1, total_steps, f"Step {idx}/{total_steps}: {action_type}")
        before = None
        if is_live and action_type not in ("wait", "wait_for"):
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

            elif action_type == "wait_for":
                if not raw_target:
                    raise ValueError(f"Step {idx} 'wait_for' requires target")
                wait_state = str(step.get("state", "present")).lower()
                wait_timeout = int(step.get("timeout_ms", 5000))
                wait_res = wait_for_element(
                    target=str(raw_target),
                    role=step_role,
                    state=wait_state,  # type: ignore[arg-type]
                    timeout_ms=wait_timeout,
                    pid=effective_pid,
                    ctx=ctx,
                )
                if not wait_res["matched"]:
                    raise TimeoutError(
                        f"Timed out waiting for '{raw_target}' to become '{wait_state}' "
                        f"after {wait_res['elapsed_ms']}ms"
                    )
                step_res = (
                    f"Element '{raw_target}' matched '{wait_state}' ({wait_res['elapsed_ms']}ms)"
                )

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
            error_code, recovery_hint = _classify_batch_error(exc)
            failure_info: dict[str, Any] = {
                "status": "failed",
                "failed_step": idx,
                "total_steps": total_steps,
                "error": str(exc),
                "error_code": error_code,
                "recovery_hint": recovery_hint,
                "executed_steps": executed_results,
                "steps": step_records,
            }
            if not global_portal_session.is_mock and global_portal_session.is_active():
                try:
                    global_portal_session.capture_frame(
                        save_to_disk=True, filename="batch_failure.png"
                    )
                    prune_artifacts("screenshots")
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
    emit_ctx_progress(ctx, total_steps, total_steps, "Batch actions complete")
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
    ctx: Context | None = None,
) -> dict[str, Any]:
    """Awaits UI settlement by monitoring real-time AT-SPI2 D-Bus mutation events.

    Returns captured mutations and whether the UI has settled, eliminating the need
    to re-query the full accessibility tree after triggers.
    """
    effective_pid = pid or global_portal_session.target_pid
    emit_ctx_log(ctx, f"Watching AT-SPI UI events for up to {timeout_ms}ms")
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
    mcp.tool(annotations=readonly_ann)(wait_for_element)
