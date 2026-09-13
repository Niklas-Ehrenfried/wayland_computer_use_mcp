"""FastMCP server definitions and entry point for wayland-computer-use-mcp.

Exposes the interactive GUI testing and desktop integration suite for Wayland:
- Process lifecycle (launch, restart, terminate, logs, liveness)
- Visual & semantic tree inspection (frame capture, AT-SPI2 tree traversal, Set-of-Marks)
- Clamped input injection (click, drag, scroll, type_text, key_combination)
- Tree-first semantic navigation and batching with closed-loop UI delta tracking
- Desktop integration (install_to_desktop, uninstall_from_desktop)
"""

from __future__ import annotations

import atexit
import logging
import signal
import sys
from pathlib import Path
from typing import Any

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
    watch_ui_events,
)
from wayland_computer_use_mcp.tools.process_tools import (
    check_app_liveness,
    get_app_logs,
    launch_app,
    list_managed_apps,
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


@mcp.prompt("wayland_automation_guide")
def wayland_automation_guide() -> str:
    """Returns system prompt extension and execution guidelines for Wayland GUI automation."""
    try:
        base_dir = Path(__file__).resolve().parent.parent.parent
        prompt_path = base_dir / "instructions" / "system_prompt_extension.md"
        if prompt_path.is_file():
            return prompt_path.read_text(encoding="utf-8")
    except Exception:
        pass
    return (
        "Execute interactions using the Tree-First semantic paradigm "
        "(inspect_ui_tree -> interact_with_node)."
    )


@mcp.resource("wayland://system_prompt")
def get_system_prompt_resource() -> str:
    """Direct resource link to the Wayland computer-use agent prompting guidelines."""
    return wayland_automation_guide()


def _cleanup_server_resources() -> None:
    """Closes active portal sessions and terminates all child processes upon server exit."""
    try:
        from wayland_computer_use_mcp.portal import global_portal_session

        global_portal_session.close()
    except Exception:
        pass
    try:
        from wayland_computer_use_mcp.process import terminate_all_processes

        terminate_all_processes()
    except Exception:
        pass


def _handle_shutdown_signal(signum: int, frame: Any) -> None:
    logger.info("Received termination signal %s, cleaning up resources...", signum)
    _cleanup_server_resources()
    sys.exit(0)


atexit.register(_cleanup_server_resources)
try:
    signal.signal(signal.SIGINT, _handle_shutdown_signal)
    signal.signal(signal.SIGTERM, _handle_shutdown_signal)
except (ValueError, AttributeError):
    pass


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
    "list_managed_apps",
    "main",
    "mcp",
    "restart_app",
    "right_click",
    "scroll",
    "take_labeled_screenshot",
    "terminate_app",
    "type_text",
    "uninstall_from_desktop",
    "watch_ui_events",
    "window_control",
]
