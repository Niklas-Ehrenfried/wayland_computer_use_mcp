"""Tests for configuration management."""

from wayland_computer_use_mcp.config import Config


def test_default_config():
    cfg = Config()
    assert cfg.display_mode == "live"
    assert cfg.source_type == 2  # Window only by default
    assert "headless" in cfg.virtual_compositor_cmd or "weston" in cfg.virtual_compositor_cmd


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
    monkeypatch.setenv("WAYLAND_MCP_ALLOW_MONITOR", "true")

    cfg = Config.load()
    assert cfg.source_type == 3


def test_save_and_load_config(tmp_path, monkeypatch):
    test_config_file = tmp_path / "config.json"
    monkeypatch.setattr("wayland_computer_use_mcp.config.CONFIG_FILE", test_config_file)
    monkeypatch.setattr("wayland_computer_use_mcp.config.CONFIG_DIR", tmp_path)

    cfg = Config(display_mode="virtual", source_type=1)
    cfg.save()

    assert test_config_file.is_file()
    loaded = Config.load()
    assert loaded.display_mode == "virtual"
    assert loaded.source_type == 1


def test_access_mode_env(monkeypatch):
    monkeypatch.setenv("WAYLAND_MCP_ACCESS_MODE", "fullscreen")
    cfg = Config.load()
    assert cfg.source_type == 1

    monkeypatch.setenv("WAYLAND_MCP_ACCESS_MODE", "window")
    cfg2 = Config.load()
    assert cfg2.source_type == 2

    monkeypatch.setenv("WAYLAND_MCP_ACCESS_MODE", "both")
    cfg3 = Config.load()
    assert cfg3.source_type == 3


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

    # Test --display-mode= and --access= variants
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

    # Invalid float should not crash and should retain default 0.4
    monkeypatch.setenv("WAYLAND_MCP_ACTION_DELAY", "invalid_float")
    cfg_invalid = Config.load()
    assert cfg_invalid.action_delay_seconds == 0.4


def test_set_and_get_config():
    """Verifies set_config sets global active instance."""
    from wayland_computer_use_mcp.config import get_config, set_config

    custom = Config(mock_mode=True, action_delay_seconds=0.5)
    set_config(custom)
    assert get_config() is custom
