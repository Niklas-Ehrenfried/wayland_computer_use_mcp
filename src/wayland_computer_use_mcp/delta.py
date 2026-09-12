"""Semantic UI delta tracking and state diffing for wayland-computer-use-mcp.

Captures lightweight snapshots of interactive UI widgets before and after an action,
computes mutations (text changes, value updates, toggles, appeared/hidden elements),
and appends a compact Markdown summary to tool responses.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

logger = logging.getLogger("wayland_computer_use_mcp.delta")


def snapshot_interactive_state(pid: int) -> dict[str, dict[str, Any]]:
    """Captures a lightweight dictionary snapshot of current interactive elements."""
    if pid <= 0:
        return {}

    try:
        from wayland_computer_use_mcp.a11y import get_application_tree

        tree = get_application_tree(pid)
        interactive = tree.get("interactive_elements", [])
        snapshot: dict[str, dict[str, Any]] = {}

        for elem in interactive:
            node_id = elem.get("id")
            if not node_id:
                continue
            states = set(elem.get("states", []))
            if elem.get("focused"):
                states.add("focused")

            snapshot[node_id] = {
                "id": node_id,
                "role": elem.get("role", "widget"),
                "name": elem.get("name", ""),
                "value": elem.get("value"),
                "states": states,
                "bounds": elem.get("bounds", [0, 0, 0, 0]),
            }
        return snapshot
    except Exception as exc:
        logger.debug("Failed to capture pre/post UI snapshot for PID %s: %s", pid, exc)
        return {}


def compute_ui_delta(
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Compares before and after snapshots and categorizes mutations."""
    modified: list[dict[str, Any]] = []
    appeared: list[dict[str, Any]] = []
    hidden: list[dict[str, Any]] = []

    # Check for modified or removed items
    for node_id, b_info in before.items():
        if node_id not in after:
            hidden.append(b_info)
        else:
            a_info = after[node_id]
            changes = []

            # 1. Text / label / name changes
            b_name = b_info.get("name", "")
            a_name = a_info.get("name", "")
            if b_name != a_name:
                changes.append(f"text '{b_name}' ➔ '{a_name}'")

            # 2. Value changes (entries, sliders)
            b_val = b_info.get("value")
            a_val = a_info.get("value")
            if b_val != a_val:
                changes.append(f"value {b_val!r} ➔ {a_val!r}")

            # 3. State changes (checked, focused)
            b_states = b_info.get("states", set())
            a_states = a_info.get("states", set())
            added_states = a_states - b_states
            removed_states = b_states - a_states
            if added_states or removed_states:
                state_desc = []
                for s in added_states:
                    state_desc.append(f"+{s}")
                for s in removed_states:
                    state_desc.append(f"-{s}")
                changes.append(f"states ({', '.join(state_desc)})")

            if changes:
                modified.append(
                    {
                        "id": node_id,
                        "role": a_info.get("role", "widget"),
                        "description": "; ".join(changes),
                    }
                )

    # Check for newly appeared items
    for node_id, a_info in after.items():
        if node_id not in before:
            appeared.append(a_info)

    return {
        "modified": modified,
        "appeared": appeared,
        "hidden": hidden,
    }


def format_delta_markdown(delta: dict[str, list[dict[str, Any]]]) -> str:
    """Formats categorized UI mutations into a compact, agent-friendly Markdown block."""
    modified = delta.get("modified", [])
    appeared = delta.get("appeared", [])
    hidden = delta.get("hidden", [])

    if not modified and not appeared and not hidden:
        return "\n\nUI Changes: None (no visible state or text change detected)"

    lines = ["\n\nUI Changes:"]

    for item in modified:
        node_id = item["id"]
        role = item["role"]
        desc = item["description"]
        lines.append(f"• {node_id} [{role}]: {desc}")

    if appeared:
        app_strs = [f"{item['id']} ('{item.get('name', '')}')" for item in appeared[:5]]
        if len(appeared) > 5:
            app_strs.append(f"+{len(appeared) - 5} more")
        lines.append(f"• Appeared: {', '.join(app_strs)}")

    if hidden:
        hid_strs = [f"{item['id']} ('{item.get('name', '')}')" for item in hidden[:5]]
        if len(hidden) > 5:
            hid_strs.append(f"+{len(hidden) - 5} more")
        lines.append(f"• Hidden: {', '.join(hid_strs)}")

    return "\n".join(lines)


def wrap_with_delta(
    pid: int,
    action_fn: Callable[[], str],
    settle_ms: int = 80,
) -> str:
    """Executes action_fn while tracking pre- and post-action UI deltas."""
    if pid <= 0:
        return action_fn()

    before = snapshot_interactive_state(pid)
    result = action_fn()
    if settle_ms > 0:
        time.sleep(settle_ms / 1000.0)
    after = snapshot_interactive_state(pid)

    delta = compute_ui_delta(before, after)
    delta_str = format_delta_markdown(delta)
    return f"{result}{delta_str}"
