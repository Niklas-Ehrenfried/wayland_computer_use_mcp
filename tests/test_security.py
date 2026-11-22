"""Tests for CoordinateClamper, TokenStore, shortcut blacklist, and window safety guards."""

from __future__ import annotations

import os
import stat
import time

import pytest

from wayland_computer_use_mcp.blackbox import BlackboxDriver
from wayland_computer_use_mcp.config import get_config
from wayland_computer_use_mcp.portal import global_portal_session
from wayland_computer_use_mcp.security import (
    CoordinateClamper,
    GeometryDivergenceDetector,
    SystemShortcutFilter,
    TokenStore,
    UserPreemptionManager,
    global_clamper,
    global_geometry_detector,
)
from wayland_computer_use_mcp.server import (
    clipboard_read,
    clipboard_write,
    double_click,
    hover,
    inspect_ui_tree,
    interact_with_node,
    key_combination,
    right_click,
)
from wayland_computer_use_mcp.tools.visual_tools import focus_window


def test_coordinate_clamper_valid():
    clamper = CoordinateClamper(width=1000, height=800)
    assert clamper.validate_and_clamp(0, 0) == (0, 0)
    assert clamper.validate_and_clamp(500, 400) == (500, 400)
    assert clamper.validate_and_clamp(1000, 800) == (1000, 800)


def test_coordinate_clamper_out_of_bounds():
    clamper = CoordinateClamper(width=1000, height=800)
    for bad_x, bad_y in [(-1, 400), (500, -1), (1001, 400), (500, 801)]:
        with pytest.raises(ValueError, match="out of window bounds"):
            clamper.validate_and_clamp(bad_x, bad_y)


def test_coordinate_clamper_set_bounds():
    clamper = CoordinateClamper(width=100, height=100)
    clamper.set_bounds(1920, 1080)
    assert clamper.dimensions == (1920, 1080)
    assert clamper.validate_and_clamp(1500, 900) == (1500, 900)

    with pytest.raises(ValueError):
        clamper.set_bounds(0, 100)


def test_token_store_crud_and_permissions(tmp_path):
    token_file = tmp_path / "subdir" / "tokens.json"
    store = TokenStore(storage_path=token_file)

    assert store.get_token("test-app") is None
    store.set_token("test-app", "tok_xyz123")
    assert store.get_token("test-app") == "tok_xyz123"

    store.set_token("another-app", "tok_abc789")
    assert store.get_token("another-app") == "tok_abc789"
    assert store.get_token("test-app") == "tok_xyz123"

    parent_mode = stat.S_IMODE(os.stat(token_file.parent).st_mode)
    assert parent_mode & 0o777 == 0o700
    file_mode = stat.S_IMODE(os.stat(token_file).st_mode)
    assert file_mode & 0o777 == 0o600


def test_system_shortcuts_strictly_blocked():
    with pytest.raises(PermissionError, match="Super/Meta/Win keys are prohibited"):
        SystemShortcutFilter.validate_and_resolve(["super", "d"])
    with pytest.raises(PermissionError, match="Super/Meta/Win keys are prohibited"):
        SystemShortcutFilter.validate_and_resolve(["win", "r"])
    with pytest.raises(PermissionError, match="Super/Meta/Win keys are prohibited"):
        SystemShortcutFilter.validate_and_resolve(["meta"])

    with pytest.raises(PermissionError, match="Ctrl\\+Alt combinations are prohibited"):
        SystemShortcutFilter.validate_and_resolve(["ctrl", "alt", "t"])
    with pytest.raises(PermissionError, match="Ctrl\\+Alt combinations are prohibited"):
        SystemShortcutFilter.validate_and_resolve(["control", "leftalt", "delete"])

    with pytest.raises(PermissionError, match="prohibited for system stability"):
        SystemShortcutFilter.validate_and_resolve(["sysrq"])
    with pytest.raises(PermissionError, match="prohibited for system stability"):
        SystemShortcutFilter.validate_and_resolve(["printscreen"])

    with pytest.raises(PermissionError, match="Alt\\+F4 is prohibited"):
        SystemShortcutFilter.validate_and_resolve(["alt", "f4"])
    with pytest.raises(PermissionError, match="Alt\\+F4 is prohibited"):
        SystemShortcutFilter.validate_and_resolve(["leftalt", "f4"])

    with pytest.raises(ValueError, match="Unrecognized key"):
        SystemShortcutFilter.validate_and_resolve(["ctrl", "totally_unknown_key"])


def test_allowed_application_shortcuts():
    assert SystemShortcutFilter.validate_and_resolve(["ctrl", "c"]) == [29, 46]
    assert len(SystemShortcutFilter.validate_and_resolve(["ctrl", "s"])) == 2
    assert len(SystemShortcutFilter.validate_and_resolve(["alt", "f"])) == 2
    assert len(SystemShortcutFilter.validate_and_resolve(["shift", "tab"])) == 2
    assert len(SystemShortcutFilter.validate_and_resolve(["f5"])) == 1


def test_geometry_divergence_fail_safe():
    detector = GeometryDivergenceDetector()
    detector.record_capture(1920, 1080, x=100, y=100)
    detector.validate_stability(1920, 1080, current_x=100, current_y=100)

    detector.validate_stability(1920, 1080, current_x=250, current_y=350)
    assert detector.last_position == (250, 350)

    with pytest.raises(RuntimeError, match="Window geometry changed"):
        detector.validate_stability(1280, 720, current_x=250, current_y=350)


def test_user_physical_input_preemption(monkeypatch):
    monkeypatch.setattr(get_config(), "display_mode", "live")
    manager = UserPreemptionManager(cooldown_seconds=0.1, max_wait_seconds=0.5)

    manager.record_user_activity()
    start_t = time.time()
    manager.check_preemption(max_wait=0.5)
    assert (time.time() - start_t) >= 0.08

    manager.record_user_activity()
    with pytest.raises(RuntimeError, match="User physical input ongoing"):
        manager.check_preemption(max_wait=0.02)

    manager.set_paused(True)
    with pytest.raises(RuntimeError, match="Agent actions are currently paused"):
        manager.check_preemption(max_wait=0.05)

    manager.set_paused(False)
    manager.check_preemption(max_wait=0.5)


def test_new_tools_execution(monkeypatch):
    monkeypatch.setattr(get_config(), "mock_mode", True)

    assert "Double-clicked left" in double_click(100, 100, button="left")
    assert "Clicked right button" in right_click(150, 150)
    assert "Hovered pointer" in hover(200, 200, duration_ms=10)
    assert "ctrl+c" in key_combination(["ctrl", "c"])

    with pytest.raises(PermissionError, match="Super/Meta/Win keys are prohibited"):
        key_combination(["super", "r"])

    el_res = interact_with_node(node_id="b1", action="click", pid=123)
    assert "Clicked" in str(el_res) or "node action" in str(el_res).lower()

    tree = inspect_ui_tree(pid=123)
    assert len(tree["interactive_elements"]) > 0

    w_res = clipboard_write("MCP Test Clipboard")
    assert "Copied" in w_res or "clipboard" in w_res
    _ = clipboard_read()

    assert focus_window(pid=123)["pid"] == 123


def test_strict_window_confinement_and_app_presence(monkeypatch):
    """Enforces that if an app is not open/responsive or window is missing, actions fail."""
    monkeypatch.setattr(get_config(), "mock_mode", False)
    monkeypatch.setattr(get_config(), "display_mode", "live")
    monkeypatch.setattr(global_portal_session, "_initialized", True)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")

    global_portal_session.target_pid = None
    with pytest.raises(RuntimeError, match="No active managed application"):
        global_portal_session.dispatch_click(50, 50)

    global_portal_session.target_pid = 9999999
    with pytest.raises(RuntimeError, match="is not running or responsive"):
        global_portal_session.dispatch_click(50, 50)

    monkeypatch.setattr("wayland_computer_use_mcp.process.is_responsive", lambda pid: True)
    monkeypatch.setattr(
        "wayland_computer_use_mcp.compositors.query_window_geometry", lambda **kwargs: None
    )
    monkeypatch.setattr("wayland_computer_use_mcp.a11y.get_application_tree", lambda pid: {})
    with pytest.raises(RuntimeError, match="is not open, visible, or mapped"):
        global_portal_session.dispatch_click(50, 50)

    global_geometry_detector.record_capture(500, 400, 100, 100)
    monkeypatch.setattr(
        "wayland_computer_use_mcp.compositors.query_window_geometry",
        lambda **kwargs: (100, 100, 500, 400),
    )
    with pytest.raises(ValueError, match="out of window bounds"):
        global_portal_session.dispatch_click(600, 200)


def test_isolated_blackbox_allows_canvas_without_geometry(monkeypatch):
    """In isolated blackbox mode, canvas apps without AT-SPI geometry fall back to output bounds."""
    monkeypatch.setattr(get_config(), "mock_mode", False)
    monkeypatch.setattr(get_config(), "display_mode", "virtual")
    monkeypatch.setattr(global_portal_session, "_initialized", True)
    global_portal_session._last_frame = None
    global_portal_session.target_pid = 424242
    global_geometry_detector.last_dimensions = None
    global_clamper.set_bounds(1280, 800)

    fake_bb = BlackboxDriver()
    monkeypatch.setattr(global_portal_session, "active_driver", lambda: fake_bb)
    monkeypatch.setattr("wayland_computer_use_mcp.process.is_responsive", lambda pid: True)
    monkeypatch.setattr(global_portal_session, "_resolve_target_geometry", lambda: None)

    global_portal_session._verify_preconditions()
    assert global_clamper.dimensions == (1280, 800)


def test_human_takeover_pause_file_works_in_virtual_mode(tmp_path, monkeypatch):
    """Creating WAYLAND_MCP_PAUSE_FILE or takeover.pause pauses even virtual sessions."""
    from wayland_computer_use_mcp.tools.process_tools import get_session_status

    pause_file = tmp_path / "takeover.pause"
    monkeypatch.setenv("WAYLAND_MCP_PAUSE_FILE", str(pause_file))
    monkeypatch.setattr(get_config(), "display_mode", "virtual")

    manager = UserPreemptionManager(cooldown_seconds=0.05, max_wait_seconds=0.1)
    assert manager.takeover_pause_path() == pause_file

    manager.check_preemption(max_wait=0.05)
    pause_file.write_text("paused by user\n", encoding="utf-8")
    assert get_session_status()["takeover_paused"] is True

    with pytest.raises(RuntimeError, match="user takeover"):
        manager.check_preemption(max_wait=0.06)

    pause_file.unlink()
    manager.check_preemption(max_wait=0.05)
