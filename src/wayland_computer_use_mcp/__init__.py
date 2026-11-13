"""wayland-computer-use-mcp: Interactive Wayland GUI testing and computer use MCP server."""

import os
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path

try:
    __version__ = _pkg_version("wayland-computer-use-mcp")
except PackageNotFoundError:
    __version__ = "0.1.1"

# Auto-detect standard Wayland and D-Bus session variables if stripped by MCP runner
uid = os.getuid() if hasattr(os, "getuid") else 1000
runtime_dir = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{uid}")
if "XDG_RUNTIME_DIR" not in os.environ and os.path.exists(runtime_dir):
    os.environ["XDG_RUNTIME_DIR"] = runtime_dir

if "DBUS_SESSION_BUS_ADDRESS" not in os.environ:
    bus_path = f"{runtime_dir}/bus"
    if os.path.exists(bus_path):
        os.environ["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={bus_path}"

if "WAYLAND_DISPLAY" not in os.environ and os.path.isdir(runtime_dir):
    for sock in sorted(Path(runtime_dir).glob("wayland-[0-9]*")):
        if not sock.name.endswith(".lock"):
            os.environ["WAYLAND_DISPLAY"] = sock.name
            break
