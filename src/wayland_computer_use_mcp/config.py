"""Configuration management for wayland-computer-use-mcp.

Handles live vs virtual screen selection, portal source type permissions,
cache paths, and mock testing modes.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

CONFIG_DIR = Path.home() / ".config" / "wayland-computer-use-mcp"
CONFIG_FILE = CONFIG_DIR / "config.json"


@dataclass
class Config:
    # "live" uses active $WAYLAND_DISPLAY / session bus;
    # "virtual" targets/launches a virtual/headless compositor.
    display_mode: Literal["live", "virtual"] = "live"

    # XDG ScreenCast portal source type bitmask:
    # 1 = Monitor, 2 = Window (default), 3 = Window | Monitor
    source_type: int = 2

    # Command to spawn a virtual Wayland compositor when in "virtual" mode
    virtual_compositor_cmd: str = "weston --backend=headless-backend.so"

    # Virtual display name when spawned
    virtual_wayland_display: str = "wayland-mcp-virtual"

    # Directory for token persistence
    token_cache_dir: Path = Path.home() / ".cache" / "wayland-computer-use-mcp"

    # Mock mode for testing without real Wayland/D-Bus portal
    mock_mode: bool = False

    # Observable pause after visual mouse/keyboard actions (seconds)
    action_delay_seconds: float = 0.4

    @classmethod
    def load(cls) -> Config:
        config = cls()

        # Load from config file if present
        if CONFIG_FILE.is_file():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if "display_mode" in data and data["display_mode"] in ("live", "virtual"):
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
            except Exception:
                pass

        # Environment variable overrides
        env_mode = os.environ.get("WAYLAND_MCP_DISPLAY_MODE", "").strip().lower()
        if env_mode in ("live", "virtual"):
            config.display_mode = env_mode  # type: ignore

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

        if os.environ.get("WAYLAND_MCP_MOCK", "").strip().lower() in ("1", "true", "yes"):
            config.mock_mode = True

        if "WAYLAND_MCP_ACTION_DELAY" in os.environ:
            try:
                config.action_delay_seconds = float(os.environ["WAYLAND_MCP_ACTION_DELAY"])
            except ValueError:
                pass

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
                if mode in ("live", "virtual"):
                    self.display_mode = mode  # type: ignore
            elif arg == "--virtual":
                self.display_mode = "virtual"
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
  --live                      Run in active Wayland user desktop session (default).
  --virtual                   Run in an isolated virtual Wayland compositor session.
  --display-mode=MODE         Set display mode ('live' or 'virtual').
  --window-only               Clamp capture and input to active application window (default).
  --fullscreen, --monitor     Allow full desktop/screen capture and interaction.
  --allow-all                 Allow either window or display capture.
  --access=ACCESS             Set access mode ('window', 'fullscreen', or 'both').
  --mock                      Run in headless mock mode for testing/CI.
  -h, --help                  Show this help message and exit.
  -v, --version               Show version and exit.

Environment Variables:
  WAYLAND_MCP_DISPLAY_MODE    'live' or 'virtual'
  WAYLAND_MCP_ACCESS_MODE     'window', 'fullscreen', or 'both'
  WAYLAND_MCP_SAVE_FRAMES_DIR Directory to persist artifacts when save_artifact=True
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
