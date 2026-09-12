"""FastMCP server definitions and entry point for wayland-computer-use-mcp.

Exposes the interactive GUI testing and desktop integration suite for Wayland:
- Process lifecycle (launch, restart, terminate, logs, liveness)
- Visual & semantic tree inspection (frame capture, AT-SPI2 tree traversal, Set-of-Marks)
- Clamped input injection (click, drag, scroll, type_text, key_combination)
- Tree-first semantic navigation and batching with closed-loop UI delta tracking
- Desktop integration (install_to_desktop, uninstall_from_desktop)
"""

from __future__ import annotations

import logging
import sys

from fastmcp import FastMCP

from wayland_computer_use_mcp.config import get_config
from wayland_computer_use_mcp.tools import register_all_tools

# Import tools for re-exporting backwards compatibility
from wayland_computer_use_mcp.tools.input_tools import (
    click,
    double_click,
    drag,
    hover,
    key_combination,
    right_click,
    scroll,
    type_text,
)
from wayland_computer_use_mcp.tools.navigation_tools import (
    batch_actions,
    click_element_by_label,
    inspect_ui_tree,
    interact_with_node,
)
from wayland_computer_use_mcp.tools.process_tools import (
    check_app_liveness,
    get_app_logs,
    launch_app,
    restart_app,
    terminate_app,
)
from wayland_computer_use_mcp.tools.system_tools import (
    clipboard_read,
    clipboard_write,
    install_to_desktop,
    uninstall_from_desktop,
    window_control,
)
from wayland_computer_use_mcp.tools.visual_tools import (
    capture_window_frame,
    focus_window,
    get_window_geometry,
    take_labeled_screenshot,
)

logger = logging.getLogger("wayland_computer_use_mcp")

# Initialize FastMCP application
mcp = FastMCP(
    "wayland-computer-use-mcp",
    instructions=(
        "Interactive Wayland window/desktop streaming, "
        "AT-SPI UI inspection, tree-first semantic navigation, "
        "and clamped input suite with reactive UI delta feedback."
    ),
)

# Register modular tool suites
register_all_tools(mcp)


def main() -> None:
    """CLI entry point for wayland-computer-use-mcp server."""
    logging.basicConfig(level=logging.INFO)
    cfg = get_config()
    remaining = cfg.apply_cli_args(sys.argv[1:])
    sys.argv = [sys.argv[0], *remaining]
    mcp.run()


if __name__ == "__main__":
    main()

__all__ = [
    "batch_actions",
    "capture_window_frame",
    "check_app_liveness",
    "click",
    "click_element_by_label",
    "clipboard_read",
    "clipboard_write",
    "double_click",
    "drag",
    "focus_window",
    "get_app_logs",
    "get_window_geometry",
    "hover",
    "inspect_ui_tree",
    "install_to_desktop",
    "interact_with_node",
    "key_combination",
    "launch_app",
    "main",
    "mcp",
    "restart_app",
    "right_click",
    "scroll",
    "take_labeled_screenshot",
    "terminate_app",
    "type_text",
    "uninstall_from_desktop",
    "window_control",
]
