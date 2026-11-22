"""Configuration management for wayland-computer-use-mcp.

Handles live vs virtual screen selection, portal source type permissions,
cache paths, and mock testing modes.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

DEFAULT_VIRTUAL_CMD = "auto"

CONFIG_DIR = Path.home() / ".config" / "wayland-computer-use-mcp"
CONFIG_FILE = CONFIG_DIR / "config.json"


@dataclass
class Config:
    # "auto" picks isolated Blackbox ("virtual") when a nested compositor is available,
    # falling back to "live" with lazy portals; "live" or "virtual" force that mode.
    display_mode: Literal["auto", "live", "virtual"] = "auto"

    # XDG ScreenCast portal source type bitmask:
    # 1 = Monitor, 2 = Window (default), 3 = Window | Monitor
    source_type: int = 2

    # Command to spawn a virtual Wayland compositor when in "virtual" mode
    virtual_compositor_cmd: str = DEFAULT_VIRTUAL_CMD

    # When True in "virtual" mode, opens a visible Playwright-style nested compositor window
    virtual_headed: bool = False

    # Virtual display name when spawned
    virtual_wayland_display: str = "wayland-mcp-virtual"

    # Directory for token persistence
    token_cache_dir: Path = Path.home() / ".cache" / "wayland-computer-use-mcp"

    # Mock mode for testing without real Wayland/D-Bus portal
    mock_mode: bool = False

    # Opt-in flag to expose install_to_desktop / uninstall_from_desktop MCP tools
    enable_desktop_installer: bool = False

    # Optional extra pause after physical mouse/keyboard actions (seconds)
    action_delay_seconds: float = 0.0

    # Defer XDG RemoteDesktop/ScreenCast portal prompts in live mode until visual/physical use
    lazy_portal: bool = False

    @classmethod
    def load(cls) -> Config:
        config = cls()

        # Load from config file if present
        if CONFIG_FILE.is_file():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if "display_mode" in data and data["display_mode"] in (
                        "auto",
                        "live",
                        "virtual",
                    ):
                        config.display_mode = data["display_mode"]
                    if "source_type" in data and isinstance(data["source_type"], int):
                        config.source_type = data["source_type"]
                    if "virtual_compositor_cmd" in data:
                        config.virtual_compositor_cmd = str(data["virtual_compositor_cmd"])
                    if "virtual_wayland_display" in data:
                        config.virtual_wayland_display = str(data["virtual_wayland_display"])
                    if "token_cache_dir" in data:
                        config.token_cache_dir = Path(data["token_cache_dir"])
                    if "mock_mode" in data:
                        config.mock_mode = bool(data["mock_mode"])
                    if "enable_desktop_installer" in data:
                        config.enable_desktop_installer = bool(data["enable_desktop_installer"])
                    if "virtual_headed" in data:
                        config.virtual_headed = bool(data["virtual_headed"])
                    if "action_delay_seconds" in data and isinstance(
                        data["action_delay_seconds"], (int, float)
                    ):
                        config.action_delay_seconds = float(data["action_delay_seconds"])
                    if "lazy_portal" in data:
                        config.lazy_portal = bool(data["lazy_portal"])
            except Exception:
                pass

        # Environment variable overrides
        env_mode = os.environ.get("WAYLAND_MCP_DISPLAY_MODE", "").strip().lower()
        if env_mode in ("auto", "live", "virtual"):
            config.display_mode = env_mode  # type: ignore
        elif env_mode in ("virtual-window", "virtual_window", "isolated", "headed"):
            config.display_mode = "virtual"
            config.virtual_headed = True

        # Access mode override (window vs fullscreen/monitor vs both)
        env_access = os.environ.get("WAYLAND_MCP_ACCESS_MODE", "").strip().lower()
        if env_access in ("window", "window-only", "2"):
            config.source_type = 2
        elif env_access in ("fullscreen", "monitor", "1"):
            config.source_type = 1
        elif env_access in ("all", "both", "3"):
            config.source_type = 3

        env_source = os.environ.get("WAYLAND_MCP_SOURCE_TYPE", "").strip()
        if env_source.isdigit():
            config.source_type = int(env_source)
        elif os.environ.get("WAYLAND_MCP_ALLOW_MONITOR", "").strip().lower() in (
            "1",
            "true",
            "yes",
        ):
            config.source_type = 3

        if "WAYLAND_MCP_VIRTUAL_CMD" in os.environ:
            config.virtual_compositor_cmd = os.environ["WAYLAND_MCP_VIRTUAL_CMD"]

        if os.environ.get("WAYLAND_MCP_VIRTUAL_HEADED", "").strip().lower() in (
            "1",
            "true",
            "yes",
        ):
            config.display_mode = "virtual"
            config.virtual_headed = True

        if os.environ.get("WAYLAND_MCP_MOCK", "").strip().lower() in ("1", "true", "yes"):
            config.mock_mode = True

        if os.environ.get("WAYLAND_MCP_ENABLE_DESKTOP_INSTALLER", "").strip().lower() in (
            "1",
            "true",
            "yes",
        ):
            config.enable_desktop_installer = True

        if "WAYLAND_MCP_ACTION_DELAY" in os.environ:
            try:
                config.action_delay_seconds = float(os.environ["WAYLAND_MCP_ACTION_DELAY"])
            except ValueError:
                pass

        if os.environ.get("WAYLAND_MCP_LAZY_PORTAL", "").strip().lower() in ("1", "true", "yes"):
            config.lazy_portal = True

        return config

    def apply_cli_args(self, args: list[str]) -> list[str]:
        """Parses custom flags from CLI args and returns remaining unhandled args.

        Supported flags:
        - --display-mode=live|virtual, --virtual, --live
        - --access=window|fullscreen|both, --fullscreen, --window-only, --allow-all
        - --mock
        - -h, --help, -v, --version
        """
        remaining = []
        for arg in args:
            if arg in ("-h", "--help"):
                print(get_help_text())
                import sys

                sys.exit(0)
            elif arg in ("-v", "--version"):
                import sys

                from wayland_computer_use_mcp import __version__

                print(f"wayland-computer-use-mcp {__version__}")
                sys.exit(0)
            elif arg.startswith("--display-mode="):
                mode = arg.split("=", 1)[1].strip().lower()
                if mode in ("auto", "live", "virtual"):
                    self.display_mode = mode  # type: ignore
                elif mode in ("virtual-window", "virtual_window", "isolated", "headed"):
                    self.display_mode = "virtual"
                    self.virtual_headed = True
            elif arg == "--auto":
                self.display_mode = "auto"
            elif arg == "--virtual":
                self.display_mode = "virtual"
            elif arg in ("--virtual-window", "--isolated-window"):
                self.display_mode = "virtual"
                self.virtual_headed = True
            elif arg == "--live":
                self.display_mode = "live"
            elif arg.startswith("--access="):
                access = arg.split("=", 1)[1].strip().lower()
                if access in ("window", "window-only"):
                    self.source_type = 2
                elif access in ("fullscreen", "monitor"):
                    self.source_type = 1
                elif access in ("both", "all"):
                    self.source_type = 3
            elif arg in ("--fullscreen", "--monitor"):
                self.source_type = 1
            elif arg == "--window-only":
                self.source_type = 2
            elif arg == "--allow-all":
                self.source_type = 3
            elif arg == "--mock":
                self.mock_mode = True
            elif arg == "--enable-desktop-installer":
                self.enable_desktop_installer = True
            elif arg == "--lazy-portal":
                self.lazy_portal = True
            else:
                remaining.append(arg)
        return remaining

    def save(self) -> None:
        """Persist current settings to user configuration file."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        data["token_cache_dir"] = str(self.token_cache_dir)
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)


def get_help_text() -> str:
    """Returns formatted CLI help text."""
    return """wayland-computer-use-mcp: High-performance Wayland GUI automation MCP server.

Usage:
  wayland-computer-use-mcp [OPTIONS]

Options:
  --auto                      Use isolated virtual window if compositor available (default).
  --live                      Run in active Wayland user desktop session.
  --virtual                   Run in a headless virtual Wayland compositor session.
  --virtual-window            Open a visible Playwright-style isolated compositor window.
  --display-mode=MODE         Set display mode ('auto', 'live', or 'virtual').
  --window-only               Clamp capture and input to active application window (default).
  --fullscreen, --monitor     Allow full desktop/screen capture and interaction.
  --allow-all                 Allow either window or display capture.
  --access=ACCESS             Set access mode ('window', 'fullscreen', or 'both').
  --enable-desktop-installer  Expose install_to_desktop / uninstall_from_desktop tools.
  --lazy-portal               Defer host portal prompts until visual or physical use.
  --mock                      Run in headless mock mode for testing/CI.
  -h, --help                  Show this help message and exit.
  -v, --version               Show version and exit.

Environment Variables:
  WAYLAND_MCP_DISPLAY_MODE             'live' or 'virtual'
  WAYLAND_MCP_VIRTUAL_HEADED           Set '1' for visible nested compositor window
  WAYLAND_MCP_VIRTUAL_CMD              Override nested compositor binary/command
  WAYLAND_MCP_ACCESS_MODE              'window', 'fullscreen', or 'both'
  WAYLAND_MCP_LAZY_PORTAL              Set '1' to defer live host portal prompts
  WAYLAND_MCP_ACTION_DELAY             Extra pause in seconds after physical inputs
  WAYLAND_MCP_PROJECT_DIR              Override project root for .agents/artifacts/
  WAYLAND_MCP_SAVE_FRAMES_DIR          Directory to persist screenshots
  WAYLAND_MCP_ENABLE_DESKTOP_INSTALLER Set '1' to enable desktop shortcut tools
"""


_active_config: Config | None = None


def get_config() -> Config:
    """Retrieve global active configuration."""
    global _active_config
    if _active_config is None:
        _active_config = Config.load()
    return _active_config


def set_config(config: Config) -> None:
    """Set global active configuration."""
    global _active_config
    _active_config = config


def resolve_effective_display_mode(
    cfg: Config | None = None,
) -> tuple[Literal["live", "virtual"], bool, bool]:
    """Resolves (effective_mode, effective_headed, effective_lazy_portal) from config.

    When ``display_mode == "auto"``, prefers isolated Blackbox ("virtual") whenever
    ``dbus-daemon`` and a nested compositor (``kwin_wayland``, ``labwc``, or ``sway``)
    are installed; otherwise falls back to ``("live", True, True)``.
    """
    active = cfg or get_config()
    if active.display_mode == "virtual":
        return "virtual", bool(active.virtual_headed), bool(active.lazy_portal)
    if active.display_mode == "live":
        return "live", True, bool(active.lazy_portal)

    has_dbus = bool(shutil.which("dbus-daemon"))
    has_comp = any(shutil.which(cmd) for cmd in ("kwin_wayland", "labwc", "sway"))
    if has_dbus and has_comp:
        headed = bool(active.virtual_headed or os.environ.get("WAYLAND_DISPLAY"))
        return "virtual", headed, False
    return "live", True, True
