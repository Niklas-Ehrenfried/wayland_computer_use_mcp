"""wayland-computer-use-mcp: Interactive Wayland GUI testing and computer use MCP server."""

__version__ = "0.1.0"

import os

# Auto-detect standard Wayland and D-Bus session variables if stripped by MCP runner
uid = os.getuid() if hasattr(os, "getuid") else 1000
runtime_dir = f"/run/user/{uid}"
if "XDG_RUNTIME_DIR" not in os.environ and os.path.exists(runtime_dir):
    os.environ["XDG_RUNTIME_DIR"] = runtime_dir

if "DBUS_SESSION_BUS_ADDRESS" not in os.environ:
    bus_path = f"{runtime_dir}/bus"
    if os.path.exists(bus_path):
        os.environ["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus_path}"

if "WAYLAND_DISPLAY" not in os.environ:
    if os.path.exists(f"{runtime_dir}/wayland-0"):
        os.environ["WAYLAND_DISPLAY"] = "wayland-0"
    elif os.path.exists(f"{runtime_dir}/wayland-1"):
        os.environ["WAYLAND_DISPLAY"] = "wayland-1"
