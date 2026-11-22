"""Tests for configuration management, CLI flags, and display-mode resolution."""

from __future__ import annotations

import pytest

from wayland_computer_use_mcp.config import (
    Config,
    get_config,
    get_help_text,
    resolve_effective_display_mode,
    set_config,
)


def test_default_config():
    cfg = Config()
    assert cfg.display_mode == "auto"
    assert cfg.source_type == 2  # Window only by default
    assert cfg.virtual_compositor_cmd == "auto"


def test_env_overrides(monkeypatch):
    monkeypatch.setenv("WAYLAND_MCP_DISPLAY_MODE", "virtual")
    monkeypatch.setenv("WAYLAND_MCP_SOURCE_TYPE", "3")
    monkeypatch.setenv("WAYLAND_MCP_MOCK", "1")

    cfg = Config.load()
    assert cfg.display_mode == "virtual"
    assert cfg.source_type == 3
    assert cfg.mock_mode is True


def test_allow_monitor_env(monkeypatch):
    monkeypatch.delenv("WAYLAND_MCP_SOURCE_TYPE", raising=False)
    monkeypatch.setenv("WAYLAND_MCP_ALLOW_MONITOR", "1")
    cfg = Config.load()
    assert cfg.source_type == 3


def test_save_and_load_config(tmp_path, monkeypatch):
    test_config_file = tmp_path / "config.json"
    monkeypatch.setattr("wayland_computer_use_mcp.config.CONFIG_FILE", test_config_file)
    monkeypatch.setattr("wayland_computer_use_mcp.config.CONFIG_DIR", tmp_path)

    cfg = Config(display_mode="virtual", source_type=1)
    cfg.save()
    assert test_config_file.exists()

    monkeypatch.delenv("WAYLAND_MCP_DISPLAY_MODE", raising=False)
    monkeypatch.delenv("WAYLAND_MCP_SOURCE_TYPE", raising=False)
    loaded = Config.load()
    assert loaded.display_mode == "virtual"
    assert loaded.source_type == 1


def test_access_mode_env(monkeypatch):
    monkeypatch.delenv("WAYLAND_MCP_SOURCE_TYPE", raising=False)
    monkeypatch.setenv("WAYLAND_MCP_ACCESS_MODE", "fullscreen")
    assert Config.load().source_type == 1

    monkeypatch.setenv("WAYLAND_MCP_ACCESS_MODE", "window")
    assert Config.load().source_type == 2

    monkeypatch.setenv("WAYLAND_MCP_ACCESS_MODE", "both")
    assert Config.load().source_type == 3


def test_apply_cli_args():
    cfg = Config()
    rem = cfg.apply_cli_args(["--virtual", "--fullscreen", "--mock", "--custom-arg"])
    assert cfg.display_mode == "virtual"
    assert cfg.source_type == 1
    assert cfg.mock_mode is True
    assert rem == ["--custom-arg"]

    cfg.apply_cli_args(["--live", "--window-only"])
    assert cfg.display_mode == "live"
    assert cfg.source_type == 2

    cfg.apply_cli_args(["--auto"])
    assert cfg.display_mode == "auto"

    cfg.apply_cli_args(["--display-mode=virtual", "--access=fullscreen"])
    assert cfg.display_mode == "virtual"
    assert cfg.source_type == 1

    cfg.apply_cli_args(["--display-mode=live", "--access=window"])
    assert cfg.display_mode == "live"
    assert cfg.source_type == 2

    cfg.apply_cli_args(["--access=both"])
    assert cfg.source_type == 3

    cfg.apply_cli_args(["--allow-all"])
    assert cfg.source_type == 3


def test_config_env_extras(monkeypatch):
    """Verifies WAYLAND_MCP_VIRTUAL_CMD and WAYLAND_MCP_ACTION_DELAY parsing."""
    monkeypatch.setenv("WAYLAND_MCP_VIRTUAL_CMD", "kwin_wayland --virtual")
    monkeypatch.setenv("WAYLAND_MCP_ACTION_DELAY", "0.25")

    cfg = Config.load()
    assert cfg.virtual_compositor_cmd == "kwin_wayland --virtual"
    assert cfg.action_delay_seconds == 0.25

    monkeypatch.setenv("WAYLAND_MCP_ACTION_DELAY", "invalid_float")
    cfg_invalid = Config.load()
    assert cfg_invalid.action_delay_seconds == 0.0


def test_set_and_get_config():
    """Verifies set_config sets global active instance."""
    custom = Config(mock_mode=True, action_delay_seconds=0.5)
    set_config(custom)
    assert get_config() is custom


def test_virtual_window_display_mode_aliases(monkeypatch):
    """WAYLAND_MCP_DISPLAY_MODE and --display-mode accept virtual-window/isolated."""
    monkeypatch.setenv("WAYLAND_MCP_DISPLAY_MODE", "virtual-window")
    cfg = Config.load()
    assert cfg.display_mode == "virtual"
    assert cfg.virtual_headed is True

    cfg2 = Config()
    cfg2.apply_cli_args(["--display-mode=isolated"])
    assert cfg2.display_mode == "virtual"
    assert cfg2.virtual_headed is True


def test_save_and_load_all_persisted_fields(tmp_path, monkeypatch):
    test_config_file = tmp_path / "config.json"
    monkeypatch.setattr("wayland_computer_use_mcp.config.CONFIG_FILE", test_config_file)
    monkeypatch.setattr("wayland_computer_use_mcp.config.CONFIG_DIR", tmp_path)

    cfg = Config(
        display_mode="virtual",
        virtual_headed=True,
        action_delay_seconds=0.15,
        lazy_portal=True,
    )
    cfg.save()
    loaded = Config.load()
    assert loaded.virtual_headed is True
    assert loaded.action_delay_seconds == 0.15
    assert loaded.lazy_portal is True


def test_get_help_text_and_cli_exits(capsys):
    """Verifies get_help_text flags and --help / --version clean exits."""
    text = get_help_text()
    for flag in ("--auto", "--live", "--virtual", "--window-only", "--fullscreen", "--mock"):
        assert flag in text
    assert "--help" in text
    assert "--version" in text

    cfg = Config()
    with pytest.raises(SystemExit) as exc_help:
        cfg.apply_cli_args(["--help"])
    assert exc_help.value.code == 0

    with pytest.raises(SystemExit) as exc_ver:
        cfg.apply_cli_args(["--version"])
    assert exc_ver.value.code == 0
    assert "wayland-computer-use-mcp" in capsys.readouterr().out


def test_resolve_effective_display_mode_auto_and_fallback(monkeypatch):
    """Config display_mode='auto' picks Blackbox when available and falls back to lazy live."""
    monkeypatch.setattr(
        "shutil.which",
        lambda name: f"/usr/bin/{name}" if name in ("dbus-daemon", "labwc") else None,
    )
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    assert resolve_effective_display_mode(Config(display_mode="auto")) == ("virtual", True, False)

    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert resolve_effective_display_mode(Config(display_mode="auto")) == ("virtual", False, False)

    monkeypatch.setattr("shutil.which", lambda name: None)
    assert resolve_effective_display_mode(Config(display_mode="auto")) == ("live", True, True)


def test_virtual_compositor_cmd_cross_de_and_flags(monkeypatch):
    from wayland_computer_use_mcp.blackbox import (
        KWinNestedAdapter,
        WestonNestedAdapter,
        WlrootsNestedAdapter,
        select_blackbox_adapter,
    )

    monkeypatch.setattr(
        "shutil.which",
        lambda name: f"/usr/bin/{name}" if name in {"weston", "kwin_wayland"} else None,
    )
    adapter = select_blackbox_adapter()
    assert isinstance(adapter, KWinNestedAdapter)
    assert adapter.build_command(
        headed=True, socket_name="wl-test", host_wayland_display="wayland-0"
    ) == (
        "kwin_wayland --wayland-display wayland-0 --xwayland --no-lockscreen "
        "--no-global-shortcuts --width 1280 --height 800 --socket wl-test"
    )
    assert adapter.build_command(
        headed=False, socket_name="wl-test", host_wayland_display=None
    ) == (
        "kwin_wayland --virtual --xwayland --no-lockscreen --no-global-shortcuts "
        "--width 1280 --height 800 --socket wl-test"
    )

    monkeypatch.setattr(
        "shutil.which", lambda name: "/usr/bin/weston" if name == "weston" else None
    )
    weston = select_blackbox_adapter()
    assert isinstance(weston, WestonNestedAdapter)
    assert weston.build_command(headed=True, socket_name="wl-test", host_wayland_display=None) == (
        "weston --backend=wayland-backend.so --socket=wl-test"
    )
    assert (
        weston.build_command(headed=False, socket_name="wl-test", host_wayland_display=None)
        == "weston --backend=headless-backend.so --socket=wl-test"
    )

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/labwc" if name == "labwc" else None)
    wlr = select_blackbox_adapter()
    assert isinstance(wlr, WlrootsNestedAdapter)
    assert (
        wlr.build_command(
            headed=True, socket_name="x", host_wayland_display="wayland-0", config_dir="/tmp/c"
        )
        == "labwc -C /tmp/c"
    )

    cfg = Config()
    cfg.apply_cli_args(["--virtual-window"])
    assert cfg.display_mode == "virtual"
    assert cfg.virtual_headed is True

    monkeypatch.setenv("WAYLAND_MCP_VIRTUAL_HEADED", "1")
    assert Config.load().virtual_headed is True


def test_retention_config_and_preview_window_cli(tmp_path, monkeypatch):
    """Verifies max_screenshots, artifact_ttl_hours, and --preview-window alias."""
    test_config_file = tmp_path / "config.json"
    monkeypatch.setattr("wayland_computer_use_mcp.config.CONFIG_FILE", test_config_file)
    monkeypatch.setattr("wayland_computer_use_mcp.config.CONFIG_DIR", tmp_path)

    cfg = Config(max_screenshots=10, artifact_ttl_hours=6.5)
    cfg.save()
    loaded = Config.load()
    assert loaded.max_screenshots == 10
    assert loaded.artifact_ttl_hours == 6.5

    monkeypatch.setenv("WAYLAND_MCP_MAX_SCREENSHOTS", "42")
    monkeypatch.setenv("WAYLAND_MCP_ARTIFACT_TTL_HOURS", "12.5")
    env_cfg = Config.load()
    assert env_cfg.max_screenshots == 42
    assert env_cfg.artifact_ttl_hours == 12.5

    cli_cfg = Config()
    cli_cfg.apply_cli_args(["--preview-window"])
    assert cli_cfg.display_mode == "virtual"
    assert cli_cfg.virtual_headed is True
    assert "--preview-window" in get_help_text()
