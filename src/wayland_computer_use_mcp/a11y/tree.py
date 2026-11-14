"""AT-SPI Tree Traversal, Node Caching, and Flattening."""

from __future__ import annotations

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


_COLLAPSIBLE_ROLES = frozenset({"generic", "panel", "filler", "viewport", "scroll pane"})
_MAX_REASONABLE_EXTENT = 16384


def _has_valid_bounds(bounds: Any) -> bool:
    """Checks whether an AT-SPI [x, y, w, h] bounding box has finite, positive extents."""
    return (
        isinstance(bounds, (list, tuple))
        and len(bounds) == 4
        and all(isinstance(v, (int, float)) for v in bounds)
        and bounds[0] >= 0
        and bounds[1] >= 0
        and 0 < bounds[2] <= _MAX_REASONABLE_EXTENT
        and 0 < bounds[3] <= _MAX_REASONABLE_EXTENT
    )


def _is_redundant_inner_label(ancestors: list[dict[str, Any]], name: str) -> bool:
    """Returns True if an inner label merely duplicates a parent interactive widget's name."""
    if not name or not ancestors:
        return False
    for anc in reversed(ancestors[-4:]):
        anc_role = str(anc.get("role", "")).lower()
        anc_name = str(anc.get("name", "")).strip()
        if anc_role in INTERACTIVE_ROLES and anc_name == name:
            return True
    return False


def _flatten_tree_with_map(
    tree: dict[str, Any],
    pid: int = 0,
) -> tuple[list[dict[str, Any]], dict[int, dict[str, Any]]]:
    """Flattens hierarchical tree and returns (elements, {id(raw_node): compact_elem})."""
    elements: list[dict[str, Any]] = []
    node_elem_map: dict[int, dict[str, Any]] = {}
    prefix_counts: dict[str, int] = collections.defaultdict(int)

    if pid is not None and pid >= 0:
        _node_cache[pid].clear()

    def _traverse(node: dict[str, Any], ancestors: list[dict[str, Any]] | None = None) -> None:
        if not node or not isinstance(node, dict):
            return

        if ancestors is None:
            ancestors = []

        role = str(node.get("role", "")).lower()
        name = str(node.get("name", "")).strip()
        states = node.get("states", [])

        # Check if node is an interactive widget or informative context label
        is_interactive = role in INTERACTIVE_ROLES
        is_label = (
            role in ("label", "static")
            and bool(name)
            and not _is_redundant_inner_label(ancestors, name)
        )

        next_ancestors = ancestors + [node]

        if is_interactive or is_label:
            bounds = node.get("bounds", [0, 0, 0, 0])
            # Ignore unmapped or offscreen elements with negative or zero extents
            if len(bounds) == 4 and (
                bounds[0] < 0 or bounds[1] < 0 or bounds[2] <= 0 or bounds[3] <= 0
            ):
                for child in node.get("children", []):
                    _traverse(child, next_ancestors)
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

            # GTK4 widgets report active state via "sensitive" instead of "enabled"
            is_enabled = ("enabled" in states) or ("sensitive" in states)
            elem: dict[str, Any] = {
                "id": node_id,
                "role": role,
                "name": name,
                "enabled": is_enabled,
                "center": center,
                "bounds": bounds,
            }
            tracked_states: list[str] = []
            for s in (
                "visible",
                "enabled",
                "showing",
                "focused",
                "checked",
                "selected",
                "expanded",
                "pressed",
            ):
                if (s == "enabled" and is_enabled) or (s != "enabled" and s in states):
                    tracked_states.append(s)
            if tracked_states:
                elem["states"] = tracked_states
            if "focused" in states:
                elem["focused"] = True
            if "value" in node and node["value"] is not None:
                elem["value"] = node["value"]

            elements.append(elem)
            node_elem_map[id(node)] = elem

            if pid is not None and pid >= 0:
                _node_cache[pid][node_id] = {
                    **node,
                    "id": node_id,
                    "center": center,
                    "bounds": bounds,
                    "ancestors": [
                        {
                            "bus_name": a.get("bus_name"),
                            "path": a.get("path"),
                            "role": str(a.get("role", "")).lower(),
                            "name": str(a.get("name", "")).strip(),
                            "index_in_parent": a.get("index_in_parent", 0),
                        }
                        for a in reversed(ancestors)
                    ],
                }

        for child in node.get("children", []):
            _traverse(child, next_ancestors)

    _traverse(tree)
    return elements, node_elem_map


def flatten_tree(tree: dict[str, Any], pid: int = 0) -> list[dict[str, Any]]:
    """Flattens hierarchical accessibility tree into a 1D list of interactive widgets and labels.

    Assigns compact, role-prefixed IDs (b1, e1, c1, s1, t1, etc.) and caches node metadata
    for direct action execution via invoke_node_action.
    """
    elements, _ = _flatten_tree_with_map(tree, pid=pid)
    return elements


def build_compact_hierarchy(
    tree: dict[str, Any],
    node_elem_map: dict[int, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Builds a stripped functional component hierarchy without D-Bus metadata or layout noise."""
    if node_elem_map is None:
        _, node_elem_map = _flatten_tree_with_map(tree, pid=-1)

    def _compact(
        node: dict[str, Any],
        is_root: bool = False,
        ancestors: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        if not node or not isinstance(node, dict):
            return []

        if ancestors is None:
            ancestors = []

        role = str(node.get("role", "")).lower()
        name = str(node.get("name", "")).strip()
        bounds = node.get("bounds")
        elem = node_elem_map.get(id(node))

        # Drop redundant inner labels duplicating an interactive parent
        if (
            elem is None
            and role in ("label", "static")
            and _is_redundant_inner_label(ancestors, name)
        ):
            return []

        compact_children: list[dict[str, Any]] = []
        next_ancestors = ancestors + [node]
        for child in node.get("children", []):
            compact_children.extend(_compact(child, is_root=False, ancestors=next_ancestors))

        # Unwrap anonymous layout wrappers so functional grouping remains clear
        if not is_root and elem is None and not name and role in _COLLAPSIBLE_ROLES:
            return compact_children

        # Drop unmapped/offscreen non-interactive containers without valid children
        if not is_root and elem is None and not compact_children:
            return []
        if not is_root and elem is None and not _has_valid_bounds(bounds) and not compact_children:
            return []

        compact: dict[str, Any] = {}
        if elem is not None:
            compact["id"] = elem["id"]
        if role:
            compact["role"] = role
        if name:
            compact["name"] = name
        if role in ("frame", "window", "dialog") and _has_valid_bounds(bounds):
            compact["bounds"] = bounds
        if elem is not None and "value" in elem:
            compact["value"] = elem["value"]
        if elem is not None and elem.get("focused"):
            compact["focused"] = True
        if compact_children:
            compact["children"] = compact_children
        return [compact]

    nodes = _compact(tree, is_root=True)
    return nodes[0] if nodes else {"role": str(tree.get("role", "application")).lower()}


def get_application_tree(pid: int, max_depth: int = 24) -> dict[str, Any]:
    """Synchronous entry point to retrieve pruned AT-SPI UI tree for a given PID."""
    from wayland_computer_use_mcp.a11y.client import run_atspi_sync

    inspector = AtspiInspector()
    tree = run_atspi_sync(inspector.get_application_tree, pid, max_depth)

    elements, node_elem_map = _flatten_tree_with_map(tree, pid=pid)
    tree["interactive_elements"] = elements
    tree["hierarchy"] = build_compact_hierarchy(tree, node_elem_map=node_elem_map)
    return tree
