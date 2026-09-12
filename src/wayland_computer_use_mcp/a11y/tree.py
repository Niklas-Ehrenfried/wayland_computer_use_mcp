"""AT-SPI Tree Traversal, Node Caching, and Flattening."""

from __future__ import annotations

import asyncio
import collections
import logging
from typing import Any

from wayland_computer_use_mcp.a11y.client import AtspiInspector
from wayland_computer_use_mcp.a11y.constants import INTERACTIVE_ROLES, ROLE_PREFIX_MAP

logger = logging.getLogger(__name__)

# In-memory node cache: pid -> {node_id -> node_dict}
_node_cache: dict[int, dict[str, dict[str, Any]]] = collections.defaultdict(dict)


def clear_node_cache(pid: int | None = None) -> None:
    """Clears cached node mapping for a specific PID or all PIDs."""
    if pid is not None:
        _node_cache.pop(pid, None)
    else:
        _node_cache.clear()


def get_cached_node(pid: int, node_id: str) -> dict[str, Any] | None:
    """Retrieves cached node metadata for a given PID and node ID or accessible label."""
    pid_cache = _node_cache.get(pid, {})
    cached = pid_cache.get(node_id)
    if cached:
        return cached

    # Search by accessible name/label (case-insensitive fallback)
    target_clean = node_id.strip().lower()
    for node in pid_cache.values():
        name = str(node.get("name", "")).strip()
        if name == node_id:
            return node
        if name.lower() == target_clean:
            return node
    return None


def flatten_tree(tree: dict[str, Any], pid: int = 0) -> list[dict[str, Any]]:
    """Flattens hierarchical accessibility tree into a 1D list of interactive widgets and labels.

    Assigns compact, role-prefixed IDs (b1, e1, c1, s1, t1, etc.) and caches node metadata
    for direct action execution via invoke_node_action.
    """
    elements: list[dict[str, Any]] = []
    prefix_counts: dict[str, int] = collections.defaultdict(int)

    if pid is not None and pid >= 0:
        _node_cache[pid].clear()

    def _traverse(node: dict[str, Any]) -> None:
        if not node:
            return

        role = str(node.get("role", "")).lower()
        name = str(node.get("name", "")).strip()
        states = node.get("states", [])

        # Check if node is an interactive widget or informative context label
        is_interactive = role in INTERACTIVE_ROLES
        is_label = role in ("label", "static") and bool(name)

        if is_interactive or is_label:
            bounds = node.get("bounds", [0, 0, 0, 0])
            # Ignore unmapped or offscreen elements with negative or zero extents
            if len(bounds) == 4 and (
                bounds[0] < 0 or bounds[1] < 0 or bounds[2] <= 0 or bounds[3] <= 0
            ):
                for child in node.get("children", []):
                    _traverse(child)
                return

            prefix = ROLE_PREFIX_MAP.get(role, "w")
            prefix_counts[prefix] += 1
            node_id = f"{prefix}{prefix_counts[prefix]}"

            if role in (
                "check box",
                "checkbox",
                "check button",
                "checkbutton",
                "radio button",
                "radiobutton",
            ):
                indicator_size = min(bounds[3], 32)
                center = [bounds[0] + indicator_size // 2, bounds[1] + bounds[3] // 2]
            elif len(bounds) == 4 and bounds[2] > 0 and bounds[3] > 0:
                center = [bounds[0] + bounds[2] // 2, bounds[1] + bounds[3] // 2]
            elif len(bounds) >= 2:
                center = [bounds[0], bounds[1]]
            else:
                center = [0, 0]

            elem: dict[str, Any] = {
                "id": node_id,
                "role": role,
                "name": name,
                "enabled": "enabled" in states,
                "center": center,
                "bounds": bounds,
            }
            if "focused" in states:
                elem["focused"] = True
            if "value" in node and node["value"] is not None:
                elem["value"] = node["value"]

            elements.append(elem)

            if pid is not None and pid >= 0:
                _node_cache[pid][node_id] = {
                    **node,
                    "id": node_id,
                    "center": center,
                    "bounds": bounds,
                }

        for child in node.get("children", []):
            _traverse(child)

    _traverse(tree)
    return elements


def get_application_tree(pid: int, max_depth: int = 24) -> dict[str, Any]:
    """Synchronous entry point to retrieve pruned AT-SPI UI tree for a given PID."""
    inspector = AtspiInspector()
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
                tree = executor.submit(
                    asyncio.run, inspector.get_application_tree(pid, max_depth)
                ).result()
        else:
            tree = loop.run_until_complete(inspector.get_application_tree(pid, max_depth))
    except RuntimeError:
        tree = asyncio.run(inspector.get_application_tree(pid, max_depth))

    elements = flatten_tree(tree, pid=pid)
    tree["interactive_elements"] = elements
    return tree
