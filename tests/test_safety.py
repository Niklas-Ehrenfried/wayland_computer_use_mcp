"""Tests for safety containment, system shortcut filtering, and fail-safes."""

import pytest

from wayland_computer_use_mcp.security import (
    GeometryDivergenceDetector,
    SystemShortcutFilter,
    UserPreemptionManager,
)
from wayland_computer_use_mcp.server import (
    click_element_by_label,
    clipboard_read,
    clipboard_write,
    double_click,
    focus_window,
    get_window_geometry,
    hover,
    key_combination,
    right_click,
    take_labeled_screenshot,
)


def test_system_shortcuts_strictly_blocked():
    # 1. Block Super / Meta / Win
    with pytest.raises(PermissionError, match="Super/Meta/Win keys are prohibited"):
        SystemShortcutFilter.validate_and_resolve(["super", "d"])

    with pytest.raises(PermissionError, match="Super/Meta/Win keys are prohibited"):
        SystemShortcutFilter.validate_and_resolve(["win", "r"])

    with pytest.raises(PermissionError, match="Super/Meta/Win keys are prohibited"):
        SystemShortcutFilter.validate_and_resolve(["meta"])

    # 2. Block Ctrl+Alt combinations
    with pytest.raises(PermissionError, match="Ctrl\\+Alt combinations are prohibited"):
        SystemShortcutFilter.validate_and_resolve(["ctrl", "alt", "t"])

    with pytest.raises(PermissionError, match="Ctrl\\+Alt combinations are prohibited"):
        SystemShortcutFilter.validate_and_resolve(["control", "leftalt", "delete"])

    # 3. Block SysRq / PrintScreen / Power
    with pytest.raises(PermissionError, match="prohibited for system stability"):
        SystemShortcutFilter.validate_and_resolve(["sysrq"])

    with pytest.raises(PermissionError, match="prohibited for system stability"):
        SystemShortcutFilter.validate_and_resolve(["printscreen"])


def test_allowed_application_shortcuts():
    # Safe application shortcuts must resolve cleanly
    codes_copy = SystemShortcutFilter.validate_and_resolve(["ctrl", "c"])
    assert len(codes_copy) == 2
    assert codes_copy == [29, 46]

    codes_save = SystemShortcutFilter.validate_and_resolve(["ctrl", "s"])
    assert len(codes_save) == 2

    codes_alt_f = SystemShortcutFilter.validate_and_resolve(["alt", "f"])
    assert len(codes_alt_f) == 2

    codes_tab = SystemShortcutFilter.validate_and_resolve(["shift", "tab"])
    assert len(codes_tab) == 2

    codes_fn = SystemShortcutFilter.validate_and_resolve(["f5"])
    assert len(codes_fn) == 1


def test_geometry_divergence_fail_safe():
    detector = GeometryDivergenceDetector()
    detector.record_capture(1920, 1080)

    # Same geometry passes
    detector.validate_stability(1920, 1080)

    # Geometry changed by user raises RuntimeError
    with pytest.raises(RuntimeError, match="Window geometry changed"):
        detector.validate_stability(1280, 720)


def test_user_physical_input_preemption(monkeypatch):
    import time

    manager = UserPreemptionManager(cooldown_seconds=0.1, max_wait_seconds=0.5)

    # 1. Record user physical input - wait_for_user_idle should wait 0.1s and then succeed cleanly
    manager.record_user_activity()
    start_t = time.time()
    manager.check_preemption(max_wait=0.5)
    elapsed = time.time() - start_t
    assert elapsed >= 0.08  # Confirms it waited for user cooldown to expire

    # 2. Timeout if user activity continues longer than max_wait
    manager.record_user_activity()
    with pytest.raises(RuntimeError, match="User physical input ongoing"):
        manager.check_preemption(max_wait=0.02)  # Cooldown is 0.1s, timeout is 0.02s

    # 3. Manual pause
    manager.set_paused(True)
    with pytest.raises(RuntimeError, match="Agent actions are currently paused"):
        manager.check_preemption(max_wait=0.05)

    manager.set_paused(False)
    manager.check_preemption(max_wait=0.5)  # Passes after pause cleared


def test_new_tools_execution(monkeypatch):
    from wayland_computer_use_mcp.config import get_config

    monkeypatch.setattr(get_config(), "mock_mode", True)

    # Test double click
    dc_res = double_click(100, 100, button="left")
    assert "Double-clicked" in dc_res

    # Test right click
    rc_res = right_click(150, 150)
    assert "Clicked right" in rc_res

    # Test hover
    h_res = hover(200, 200, duration_ms=50)
    assert "Hovered" in h_res

    # Test key combination (allowed)
    kc_res = key_combination(["ctrl", "c"])
    assert "Dispatched key combination" in kc_res

    # Test click element by label (uses synthetic tree if offline)
    el_res = click_element_by_label("Click Me!")
    assert "Clicked left" in el_res

    # Test labeled screenshot (Set-of-Marks)
    labeled = take_labeled_screenshot()
    assert "image" in labeled
    assert "elements" in labeled
    assert labeled["element_count"] > 0

    # Test clipboard tools
    w_res = clipboard_write("MCP Test Clipboard")
    assert "Copied" in w_res or "clipboard" in w_res
    _ = clipboard_read()

    # Test window geometry and focus
    geom = get_window_geometry(pid=123)
    assert "bounds" in geom
    assert "width" in geom

    foc = focus_window(pid=123)
    assert foc["pid"] == 123


def test_strict_window_confinement_and_app_presence(monkeypatch):
    """Enforces that if an app is not open/responsive or window is missing, actions fail."""
    from wayland_computer_use_mcp.config import get_config
    from wayland_computer_use_mcp.portal import global_portal_session

    # Force live checks
    monkeypatch.setattr(get_config(), "mock_mode", False)
    monkeypatch.setattr(global_portal_session, "_is_mock", False)
    monkeypatch.setattr(global_portal_session, "pipewire_node_id", 42)
    monkeypatch.setattr(global_portal_session, "_initialized", True)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")

    # 1. No app open -> fail immediately
    global_portal_session.target_pid = None
    with pytest.raises(RuntimeError, match="No active managed application"):
        global_portal_session.dispatch_click(50, 50)

    # 2. Dead / non-existent PID -> fail immediately
    global_portal_session.target_pid = 9999999
    with pytest.raises(RuntimeError, match="is not running or responsive"):
        global_portal_session.dispatch_click(50, 50)

    # 3. PID responsive but window not found -> fail immediately
    monkeypatch.setattr("wayland_computer_use_mcp.process.is_responsive", lambda pid: True)
    monkeypatch.setattr(
        "wayland_computer_use_mcp.compositors.query_window_geometry", lambda **kwargs: None
    )
    monkeypatch.setattr("wayland_computer_use_mcp.a11y.get_application_tree", lambda pid: {})
    with pytest.raises(RuntimeError, match="is not open, visible, or mapped"):
        global_portal_session.dispatch_click(50, 50)

    # 4. App window open (e.g. 500x400) but click is out of bounds -> fail immediately
    from wayland_computer_use_mcp.security import global_geometry_detector

    global_geometry_detector.record_capture(500, 400, 100, 100)
    monkeypatch.setattr(
        "wayland_computer_use_mcp.compositors.query_window_geometry",
        lambda **kwargs: (100, 100, 500, 400),
    )
    with pytest.raises(ValueError, match="out of window bounds"):
        global_portal_session.dispatch_click(600, 200)
