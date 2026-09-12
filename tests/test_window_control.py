"""Unit tests for window_control tool across compositor states."""

import pytest

from wayland_computer_use_mcp.portal import global_portal_session
from wayland_computer_use_mcp.server import window_control


def test_window_control_no_pid_error():
    """Fails cleanly when no PID is passed and no managed application exists."""
    prev_pid = global_portal_session.target_pid
    try:
        global_portal_session.target_pid = None
        with pytest.raises(RuntimeError, match="No active application PID"):
            window_control(action="minimize")
    finally:
        global_portal_session.target_pid = prev_pid


def test_window_control_invalid_action():
    """Fails cleanly on unsupported window actions."""
    with pytest.raises(ValueError, match="Invalid window action"):
        window_control(action="explode", pid=12345)


def test_window_control_all_actions(monkeypatch):
    """Verifies minimize, maximize, restore, focus, close actions dispatch correctly."""
    dispatched_states = []
    activated_pids = []

    monkeypatch.setattr(
        "wayland_computer_use_mcp.compositors.set_window_state",
        lambda pid, state: dispatched_states.append((pid, state)) or True,
    )
    monkeypatch.setattr(
        "wayland_computer_use_mcp.compositors.activate_and_raise_window",
        lambda pid, **kwargs: activated_pids.append(pid) or True,
    )

    # 1. Minimize
    res_min = window_control(action="minimize", pid=42)
    assert res_min["success"] is True
    assert res_min["action"] == "minimize"
    assert (42, "minimize") in dispatched_states

    # 2. Maximize
    res_max = window_control(action="maximize", pid=42)
    assert res_max["success"] is True
    assert res_max["action"] == "maximize"
    assert (42, "maximize") in dispatched_states

    # 3. Restore
    res_rest = window_control(action="restore", pid=42)
    assert res_rest["success"] is True
    assert res_rest["action"] == "restore"
    assert (42, "restore") in dispatched_states

    # 4. Focus
    res_foc = window_control(action="focus", pid=42)
    assert res_foc["success"] is True
    assert res_foc["action"] == "focus"
    assert 42 in activated_pids

    # 5. Close
    res_cls = window_control(action="close", pid=42)
    assert res_cls["success"] is True
    assert res_cls["action"] == "close"
    assert (42, "close") in dispatched_states


def test_window_control_default_to_target_pid(monkeypatch):
    """Verifies that if pid is omitted, global_portal_session.target_pid is used."""
    dispatched_states = []
    monkeypatch.setattr(
        "wayland_computer_use_mcp.compositors.set_window_state",
        lambda pid, state: dispatched_states.append((pid, state)) or True,
    )

    prev_pid = global_portal_session.target_pid
    try:
        global_portal_session.target_pid = 7890
        res = window_control(action="minimize")
        assert res["pid"] == 7890
        assert (7890, "minimize") in dispatched_states
    finally:
        global_portal_session.target_pid = prev_pid
