"""AT-SPI2 Accessibility Inspection and Control Package.

Provides semantic UI tree inspection, token-optimized pruning, compact 1D
element indexing, and hybrid action execution.
"""

from __future__ import annotations

from wayland_computer_use_mcp.a11y.actions import (
    do_accessible_action,
    do_accessible_set_text,
    do_accessible_text_action,
    invoke_node_action,
    perform_accessible_action,
    perform_accessible_set_text,
    perform_accessible_text_action,
)
from wayland_computer_use_mcp.a11y.client import AtspiInspector, resolve_atspi_bus_address
from wayland_computer_use_mcp.a11y.constants import (
    CONTAINER_ROLES,
    IGNORED_ROLES,
    INTERACTIVE_ROLES,
    ROLE_PREFIX_MAP,
)
from wayland_computer_use_mcp.a11y.events import (
    AtspiEventListener,
    global_event_listener,
)
from wayland_computer_use_mcp.a11y.synthetic import build_synthetic_tree
from wayland_computer_use_mcp.a11y.tree import (
    _node_cache,
    clear_node_cache,
    flatten_tree,
    get_application_tree,
    get_cached_node,
)

__all__ = [
    "AtspiEventListener",
    "CONTAINER_ROLES",
    "IGNORED_ROLES",
    "INTERACTIVE_ROLES",
    "ROLE_PREFIX_MAP",
    "AtspiInspector",
    "_node_cache",
    "build_synthetic_tree",
    "clear_node_cache",
    "do_accessible_action",
    "do_accessible_set_text",
    "do_accessible_text_action",
    "flatten_tree",
    "get_application_tree",
    "get_cached_node",
    "global_event_listener",
    "invoke_node_action",
    "perform_accessible_action",
    "perform_accessible_set_text",
    "perform_accessible_text_action",
    "resolve_atspi_bus_address",
]
