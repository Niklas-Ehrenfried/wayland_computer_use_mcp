"""Comprehensive unit tests targeting uncovered branches across modules.

Boosts branch coverage for clipboard, process_tools, kwin, overlay, and navigation_tools.
"""

from __future__ import annotations

import subprocess
from unittest.mock import patch

import pytest
from PIL import Image

from wayland_computer_use_mcp.clipboard import (
    read_system_clipboard,
    write_system_clipboard,
)
from wayland_computer_use_mcp.compositors import (
    activate_and_raise_window as set_kwin_keep_above,
)
from wayland_computer_use_mcp.compositors import (
    ensure_portal_dialogs_above,
    minimize_window,
    restore_window,
)
from wayland_computer_use_mcp.compositors import (
    query_window_geometry as query_kwin_geometry,
)
from wayland_computer_use_mcp.overlay import draw_labeled_overlay, load_overlay_font
from wayland_computer_use_mcp.portal import global_portal_session
from wayland_computer_use_mcp.tools.navigation_tools import (
    batch_actions,
    click_element_by_label,
)
from wayland_computer_use_mcp.tools.process_tools import (
    check_app_liveness,
    get_app_logs,
    launch_app,
    restart_app,
    terminate_app,
)

# =========================================================================
# 1. Clipboard Backend Branch Coverage Tests
# =========================================================================


def test_write_system_clipboard_wl_copy():
    with patch("shutil.which") as mock_which, patch("subprocess.run") as mock_run:
        mock_which.side_effect = lambda cmd: "/usr/bin/wl-copy" if cmd == "wl-copy" else None
        mock_run.return_value = subprocess.CompletedProcess(["wl-copy"], 0)
        assert write_system_clipboard("Hello Wayland") is True
        mock_run.assert_called_once()


def test_write_system_clipboard_wl_copy_fails_fallback_to_qdbus6():
    with patch("shutil.which") as mock_which, patch("subprocess.run") as mock_run:

        def fake_which(cmd):
            if cmd in ("wl-copy", "qdbus6"):
                return f"/usr/bin/{cmd}"
            return None

        mock_which.side_effect = fake_which
        # wl-copy raises, then qdbus6 succeeds
        mock_run.side_effect = [
            subprocess.SubprocessError("wl-copy crashed"),
            subprocess.CompletedProcess(["qdbus6"], 0),
        ]
        assert write_system_clipboard("Fallback Test") is True
        assert mock_run.call_count == 2


def test_write_system_clipboard_qdbus():
    with patch("shutil.which") as mock_which, patch("subprocess.run") as mock_run:
        mock_which.side_effect = lambda cmd: "/usr/bin/qdbus" if cmd == "qdbus" else None
        mock_run.return_value = subprocess.CompletedProcess(["qdbus"], 0)
        assert write_system_clipboard("Plasma 5") is True


def test_write_system_clipboard_xclip():
    with patch("shutil.which") as mock_which, patch("subprocess.run") as mock_run:
        mock_which.side_effect = lambda cmd: "/usr/bin/xclip" if cmd == "xclip" else None
        mock_run.return_value = subprocess.CompletedProcess(["xclip"], 0)
        assert write_system_clipboard("X11 test") is True


def test_write_system_clipboard_all_fail():
    with patch("shutil.which", return_value=None):
        assert write_system_clipboard("None") is False


def test_read_system_clipboard_wl_paste():
    with patch("shutil.which") as mock_which, patch("subprocess.run") as mock_run:
        mock_which.side_effect = lambda cmd: "/usr/bin/wl-paste" if cmd == "wl-paste" else None
        mock_run.return_value = subprocess.CompletedProcess(
            ["wl-paste"], 0, stdout="Pasted content"
        )
        assert read_system_clipboard() == "Pasted content"


def test_read_system_clipboard_qdbus6_fallback():
    with patch("shutil.which") as mock_which, patch("subprocess.run") as mock_run:
        mock_which.side_effect = lambda cmd: "/usr/bin/qdbus6" if cmd == "qdbus6" else None
        mock_run.return_value = subprocess.CompletedProcess(["qdbus6"], 0, stdout="QDBus text\n")
        assert read_system_clipboard() == "QDBus text"


def test_read_system_clipboard_qdbus():
    with patch("shutil.which") as mock_which, patch("subprocess.run") as mock_run:
        mock_which.side_effect = lambda cmd: "/usr/bin/qdbus" if cmd == "qdbus" else None
        mock_run.return_value = subprocess.CompletedProcess(["qdbus"], 0, stdout="QDBus5 text\n")
        assert read_system_clipboard() == "QDBus5 text"


def test_read_system_clipboard_xclip():
    with patch("shutil.which") as mock_which, patch("subprocess.run") as mock_run:
        mock_which.side_effect = lambda cmd: "/usr/bin/xclip" if cmd == "xclip" else None
        mock_run.return_value = subprocess.CompletedProcess(["xclip"], 0, stdout="xclip text")
        assert read_system_clipboard() == "xclip text"


def test_read_system_clipboard_none():
    with patch("shutil.which", return_value=None):
        assert read_system_clipboard() == ""


# =========================================================================
# 2. Process Tools Lifecycle & Edge Cases
# =========================================================================


def test_process_tools_launch_app_clean():
    with (
        patch(
            "wayland_computer_use_mcp.tools.process_tools.launch", return_value=4242
        ) as mock_launch,
        patch("wayland_computer_use_mcp.tools.process_tools.ensure_portal_dialogs_above"),
        patch("wayland_computer_use_mcp.tools.process_tools._ensure_session_initialized"),
    ):
        res = launch_app("dummy_app.py", args=["--arg1"])
        assert res["status"] == "launched"
        assert res["pid"] == 4242
        assert global_portal_session.target_pid == 4242
        mock_launch.assert_called_once_with("dummy_app.py", ["--arg1"], None)


def test_process_tools_launch_app_with_restart():
    global_portal_session.target_pid = 1111
    with (
        patch("wayland_computer_use_mcp.tools.process_tools.is_responsive", return_value=True),
        patch("wayland_computer_use_mcp.tools.process_tools.minimize_window") as mock_min,
        patch(
            "wayland_computer_use_mcp.tools.process_tools.restart_process", return_value=2222
        ) as mock_restart,
        patch("wayland_computer_use_mcp.tools.process_tools.ensure_portal_dialogs_above"),
        patch("wayland_computer_use_mcp.tools.process_tools._ensure_session_initialized"),
    ):
        res = launch_app("app.py", restart=True)
        assert res["status"] == "restarted"
        assert res["old_pid"] == 1111
        assert res["pid"] == 2222
        mock_min.assert_called_once_with(1111)
        mock_restart.assert_called_once_with(1111)


def test_process_tools_restart_app_direct():
    with (
        patch("wayland_computer_use_mcp.tools.process_tools.minimize_window"),
        patch("wayland_computer_use_mcp.tools.process_tools.restart_process", return_value=3333),
        patch("wayland_computer_use_mcp.tools.process_tools.ensure_portal_dialogs_above"),
        patch("wayland_computer_use_mcp.tools.process_tools._ensure_session_initialized"),
    ):
        res = restart_app(1234)
        assert res["status"] == "restarted"
        assert res["new_pid"] == 3333
        assert global_portal_session.target_pid == 3333


def test_process_tools_terminate_app():
    global_portal_session.target_pid = 5555
    with patch("wayland_computer_use_mcp.tools.process_tools.terminate") as mock_term:
        res = terminate_app()
        assert res["status"] == "terminated"
        assert res["pid"] == 5555
        assert global_portal_session.target_pid is None
        mock_term.assert_called_once_with(5555)

    with pytest.raises(RuntimeError, match="No target PID"):
        terminate_app(pid=None)


def test_process_tools_check_liveness():
    res_none = check_app_liveness(pid=None)
    assert res_none["alive"] is False

    with patch("wayland_computer_use_mcp.tools.process_tools.is_responsive", return_value=True):
        res = check_app_liveness(pid=9999)
        assert res["alive"] is True
        assert res["pid"] == 9999


def test_process_tools_get_app_logs():
    with patch(
        "wayland_computer_use_mcp.tools.process_tools.get_logs",
        return_value="Log line 1\nLog line 2",
    ):
        logs = get_app_logs(pid=7777, lines=10)
        assert "Log line 1" in logs

    global_portal_session.target_pid = None
    with pytest.raises(RuntimeError, match="No active target PID"):
        get_app_logs(pid=None)


# =========================================================================
# 3. KWin Window Management Branch Tests
# =========================================================================


def test_kwin_availability():
    with patch.dict("os.environ", {}, clear=True):
        assert query_kwin_geometry(123) is None

    with patch.dict("os.environ", {"WAYLAND_DISPLAY": "wayland-0"}):
        with patch(
            "wayland_computer_use_mcp.compositors._run_async_sync",
            return_value=(0, 0, 800, 600),
        ):
            assert query_kwin_geometry(123) == (0, 0, 800, 600)
            assert set_kwin_keep_above(123) is True


def test_kwin_window_helpers():
    with patch(
        "wayland_computer_use_mcp.compositors._run_async_sync",
        return_value=True,
    ):
        assert ensure_portal_dialogs_above() is True
        assert minimize_window(9876) is True
        assert restore_window(9876) is True


# =========================================================================
# 4. Overlay Rendering & Font Fallbacks
# =========================================================================


def test_overlay_font_fallback():
    with patch("os.path.exists", return_value=False):
        font = load_overlay_font()
        assert font is not None


def test_draw_labeled_overlay_scaling():
    base_img = Image.new("RGB", (3840, 2160), color=(100, 100, 100))
    elements = [
        {"id": "b1", "bounds": [50, 50, 200, 80], "name": "4K Button"},
        {"id": "lbl1", "bounds": [0, 0, 0, 0], "name": "Zero Dim"},
        {"id": "e1", "bounds": [300, 300, 400, 60], "name": "Input Box"},
    ]
    annotated, legend = draw_labeled_overlay(base_img, elements)
    assert annotated.size == (3840, 2160)
    assert len(legend) == 3


# =========================================================================
# 5. Navigation Tools & Batch Actions
# =========================================================================


def test_navigation_batch_actions():
    with (
        patch(
            "wayland_computer_use_mcp.tools.navigation_tools.invoke_node_action",
            return_value="Clicked b1",
        ) as mock_node_action,
        patch(
            "wayland_computer_use_mcp.tools.navigation_tools.check_process_health_and_enrich",
            side_effect=lambda pid, msg: msg,
        ),
        patch(
            "wayland_computer_use_mcp.tools.navigation_tools.global_portal_session"
        ) as mock_session,
    ):
        mock_session.is_mock = True
        mock_session.target_pid = 1234
        mock_session.dispatch_hover.return_value = "Hovered"
        mock_session.dispatch_click.return_value = "Clicked"
        mock_session.dispatch_scroll.return_value = "Scrolled"
        mock_session.dispatch_type_text.return_value = "Typed"
        mock_session.dispatch_key_combination.return_value = "Pressed keys"

        steps = [
            {"action": "wait", "ms": 10},
            {"action": "hover", "x": 100, "y": 150},
            {"action": "click", "x": 100, "y": 150, "button": "left"},
            {"action": "scroll", "dx": 0, "dy": -50},
            {"action": "type", "text": "hello"},
            {"action": "key_combination", "keys": ["Control_L", "a"]},
            {"action": "interact", "node_id": "b1"},
        ]
        res = batch_actions(steps, pid=1234)
        assert res["status"] == "success"
        assert res["executed_steps_count"] == 7
        assert mock_node_action.called


def test_navigation_click_element_by_label():
    with patch(
        "wayland_computer_use_mcp.tools.navigation_tools.interact_with_node",
        return_value="Clicked label",
    ) as mock_node:
        res = click_element_by_label("Submit", pid=5555)
        assert res == "Clicked label"
        mock_node.assert_called_once_with(target="Submit", action="click", pid=5555)
