"""Tests for process lifecycle, virtualenv resolution, crash enrichment, and lifecycle tools."""

from __future__ import annotations

import collections
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wayland_computer_use_mcp.config import Config, get_config, set_config
from wayland_computer_use_mcp.portal import global_portal_session
from wayland_computer_use_mcp.process import (
    _last_warned_tb,
    _lock,
    _logs,
    _proc_metadata,
    _processes,
    check_process_health_and_enrich,
    find_virtualenv_python,
    get_logs,
    is_responsive,
    launch,
    list_active_processes,
    prune_dead_processes,
    restart,
    terminate,
    terminate_all_processes,
)
from wayland_computer_use_mcp.tools import process_tools
from wayland_computer_use_mcp.tools.process_tools import (
    get_app_logs,
    get_session_status,
    launch_app,
    restart_app,
    terminate_app,
)


def test_find_virtualenv_python_detection_and_fallback(tmp_path):
    proj_dir = tmp_path / "my_project"
    venv_python = proj_dir / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.touch()
    os.chmod(venv_python, 0o755)
    script = proj_dir / "src" / "app.py"
    script.parent.mkdir(parents=True)
    script.touch()
    assert find_virtualenv_python(script) == venv_python

    standalone = tmp_path / "script.py"
    standalone.touch()
    assert find_virtualenv_python(standalone) == Path(sys.executable)


def test_process_launch_logs_drain_restart_and_terminate(tmp_path):
    script = tmp_path / "demo_app.py"
    script.write_text(
        "import sys, time\n"
        "sys.stderr.write('App started line 1\\n')\n"
        "sys.stderr.flush()\n"
        "time.sleep(5)\n"
    )
    pid = launch(str(script))
    time.sleep(0.2)
    assert is_responsive(pid) is True
    assert "App started line 1" in get_logs(pid, lines=10)
    new_pid = restart(pid)
    assert new_pid != pid and is_responsive(pid) is False and is_responsive(new_pid) is True
    assert terminate(new_pid) is True
    time.sleep(0.1)
    assert is_responsive(new_pid) is False

    # Stdout pipe draining > 64KB buffer
    noisy = tmp_path / "noisy_stdout.py"
    noisy.write_text(
        "import sys\n"
        "for i in range(2500):\n"
        "    sys.stdout.write(f'stdout-line-{i:04d} ' + 'x' * 50 + '\\n')\n"
        "sys.stdout.flush()\n"
    )
    pid2 = launch(str(noisy))
    try:
        deadline = time.time() + 5.0
        logs = ""
        while time.time() < deadline:
            logs = get_logs(pid2, lines=20)
            if "stdout-line-2499" in logs:
                break
            time.sleep(0.05)
        assert "stdout-line-2499" in logs
    finally:
        try:
            terminate(pid2)
        except Exception:
            pass

    with pytest.raises(PermissionError, match="not owned by this session"):
        terminate(9999999)


def test_healthy_process_health_check_warns_once():
    fake_pid = 88899

    class AliveProc:
        def poll(self):
            return None

    _processes[fake_pid] = AliveProc()
    _logs[fake_pid] = collections.deque(["INFO: ready"], maxlen=200)
    _last_warned_tb.pop(fake_pid, None)
    try:
        with patch("wayland_computer_use_mcp.process.time.sleep") as mock_sleep:
            assert check_process_health_and_enrich(fake_pid, "ok") == "ok"
            mock_sleep.assert_not_called()

        _logs[fake_pid].extend(
            ["Traceback (most recent call last):", '  File "a.py", line 1', "RuntimeError: boom"]
        )
        assert "RuntimeError: boom" in check_process_health_and_enrich(fake_pid, "step 1")
        _logs[fake_pid].append("INFO: heartbeat")
        assert check_process_health_and_enrich(fake_pid, "step 2") == "step 2"
    finally:
        _processes.pop(fake_pid, None)
        _logs.pop(fake_pid, None)
        _last_warned_tb.pop(fake_pid, None)


def test_launch_path_binary_electron_and_missing_target(monkeypatch):
    fake_proc = MagicMock(pid=77712, stdout=None, stderr=None)
    monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/electron" if n == "electron" else None)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")

    with patch("subprocess.Popen", return_value=fake_proc) as mock_popen:
        pid = launch("electron", args=["./app"])
        try:
            cmd = mock_popen.call_args[0][0]
            env = mock_popen.call_args[1]["env"]
            assert cmd[0] == "/usr/bin/electron"
            assert "--force-renderer-accessibility" in cmd and "--ozone-platform=wayland" in cmd
            assert env["ACCESSIBILITY_ENABLED"] == "1"
            assert _proc_metadata[pid]["target"] == "electron"
        finally:
            _processes.pop(pid, None)
            _proc_metadata.pop(pid, None)

    monkeypatch.setattr("shutil.which", lambda n: None)
    missing_pid = launch("definitely_missing_script_xyz.py")
    time.sleep(0.2)
    assert is_responsive(missing_pid) is False
    assert (
        "no such file" in get_logs(missing_pid).lower()
        or "errno 2" in get_logs(missing_pid).lower()
    )


def test_prune_list_and_terminate_all_processes():
    alive = MagicMock()
    alive.poll.return_value = None
    dead = MagicMock()
    dead.poll.return_value = 0

    with _lock:
        _processes[99901] = alive
        _processes[99902] = dead
    try:
        assert 99902 in prune_dead_processes()
        assert 99901 in [p["pid"] for p in list_active_processes()]
        with patch("wayland_computer_use_mcp.process.terminate", return_value=True) as mock_term:
            assert 99901 in terminate_all_processes()
            mock_term.assert_called_with(99901)
    finally:
        with _lock:
            _processes.pop(99901, None)
            _processes.pop(99902, None)


def test_launch_restart_terminate_and_session_status_tools(tmp_path, monkeypatch):
    with (
        patch(
            "wayland_computer_use_mcp.tools.process_tools.launch", return_value=4242
        ) as mock_launch,
        patch("wayland_computer_use_mcp.tools.process_tools.ensure_portal_dialogs_above"),
        patch.object(global_portal_session, "ensure_initialized"),
    ):
        res = launch_app("dummy_app.py", args=["--arg1"])
        assert res["status"] == "launched" and res["pid"] == 4242
        mock_launch.assert_called_once_with("dummy_app.py", ["--arg1"], None)

    script = tmp_path / "sleep_app.py"
    script.write_text("import time\ntime.sleep(5)\n")
    first = launch_app(target=str(script))
    pid1 = first["pid"]
    second = launch_app(target=str(script), restart=True)
    pid2 = second["pid"]
    try:
        assert second["status"] == "restarted" and second["old_pid"] == pid1 and pid2 != pid1
        assert global_portal_session.target_pid == pid2
    finally:
        try:
            terminate(pid2)
        except Exception:
            pass
        global_portal_session.target_pid = None

    with pytest.raises(PermissionError, match="Only MCP-launched applications can be restarted"):
        restart_app(1234)
    with (
        patch("wayland_computer_use_mcp.process._processes", {1234: None}),
        patch.object(global_portal_session.active_driver(), "set_window_state"),
        patch("wayland_computer_use_mcp.tools.process_tools.restart_process", return_value=3333),
        patch("wayland_computer_use_mcp.tools.process_tools.ensure_portal_dialogs_above"),
        patch.object(global_portal_session, "ensure_initialized"),
    ):
        assert restart_app(1234)["new_pid"] == 3333

    with patch("wayland_computer_use_mcp.tools.process_tools.get_logs", return_value="Log line 1"):
        assert "Log line 1" in get_app_logs(pid=7777, lines=10)
    global_portal_session.target_pid = None
    with pytest.raises(RuntimeError, match="No active target PID"):
        get_app_logs(pid=None)
    with pytest.raises(RuntimeError, match="No target PID"):
        terminate_app(pid=None)

    # Session summary reports configured_mode="auto" alongside resolved mode
    status = get_session_status()
    assert status["mode"] == "mock"
    assert status["configured_mode"] == get_config().display_mode


def test_launch_live_order_exited_early_and_idle_virtual_teardown(monkeypatch):
    prev_cfg = get_config()
    call_order: list[str] = []
    terminated: list[int] = []
    try:
        set_config(Config(display_mode="live", mock_mode=False))
        monkeypatch.setattr(global_portal_session, "is_active", lambda: False)
        monkeypatch.setattr(process_tools, "ensure_portal_dialogs_above", lambda: True)
        monkeypatch.setattr(
            process_tools, "launch", lambda t, a, c: call_order.append("launch") or 555
        )
        monkeypatch.setattr(
            global_portal_session,
            "ensure_initialized",
            lambda app_id=None: call_order.append("portal"),
        )
        monkeypatch.setattr(process_tools, "is_responsive", lambda pid: True)
        monkeypatch.setattr(process_tools, "_bind_focus_and_collect_elements", lambda pid: [])

        assert launch_app("examples/test_gui_app.py")["pid"] == 555
        assert call_order == ["launch", "portal"]

        monkeypatch.setattr(
            global_portal_session,
            "ensure_initialized",
            lambda app_id=None: (_ for _ in ()).throw(RuntimeError("cancelled")),
        )
        monkeypatch.setattr(process_tools, "terminate", lambda pid: terminated.append(pid))
        with pytest.raises(RuntimeError, match="cancelled"):
            launch_app("examples/test_gui_app.py")
        assert terminated == [555]

        # Early startup crash returns structured exited_early recovery payload
        set_config(Config(display_mode="virtual", mock_mode=False))
        monkeypatch.setattr(global_portal_session, "ensure_initialized", lambda: None)
        monkeypatch.setattr(global_portal_session, "is_active", lambda: True)
        monkeypatch.setattr(process_tools, "launch", lambda t, a=None, c=None: 55123)
        monkeypatch.setattr(process_tools, "_wait_until_window_or_a11y_ready", lambda p, ms: None)
        monkeypatch.setattr(process_tools, "is_responsive", lambda p: False)
        monkeypatch.setattr(
            process_tools, "get_logs", lambda p, lines=40: "ImportError: missing dep"
        )
        monkeypatch.setattr(
            process_tools, "get_recent_traceback", lambda p: "Traceback: ImportError"
        )
        early = launch_app(target="broken.py")
        assert early["status"] == "exited_early" and early["exited_early"] is True
        assert "ImportError" in early["logs"] and early["recovery_hint"]
    finally:
        set_config(prev_cfg)
        global_portal_session.target_pid = None

    # terminate_app switches active target_pid to remaining app and closes virtual only when idle
    closed_calls: list[bool] = []
    remaining_apps = [[{"pid": 222}], []]
    monkeypatch.setattr(process_tools, "terminate", lambda pid: True)
    monkeypatch.setattr(process_tools, "list_active_processes", lambda: remaining_apps.pop(0))
    monkeypatch.setattr(
        global_portal_session, "close_virtual_if_idle", lambda: closed_calls.append(True) or True
    )

    global_portal_session.target_pid = 111
    res1 = terminate_app(pid=111)
    assert res1["virtual_session_closed"] is False and global_portal_session.target_pid == 222
    res2 = terminate_app(pid=222)
    assert res2["virtual_session_closed"] is True and global_portal_session.target_pid is None
    assert len(closed_calls) == 1
