"""Tests for process management and virtualenv detection."""

import os
import sys
import time
from pathlib import Path

import pytest

from wayland_computer_use_mcp.process import (
    find_virtualenv_python,
    get_logs,
    is_responsive,
    launch,
    restart,
    terminate,
)


def test_find_virtualenv_python_detection(tmp_path):
    # Create fake project directory structure
    proj_dir = tmp_path / "my_project"
    venv_python = proj_dir / ".venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.touch()
    os.chmod(venv_python, 0o755)

    script = proj_dir / "src" / "app.py"
    script.parent.mkdir(parents=True)
    script.touch()

    detected = find_virtualenv_python(script)
    assert detected == venv_python


def test_find_virtualenv_python_fallback(tmp_path):
    script = tmp_path / "script.py"
    script.touch()
    detected = find_virtualenv_python(script)
    assert detected == Path(sys.executable)


def test_process_lifecycle(tmp_path):
    # Create a small python script that writes to stderr and sleeps
    script = tmp_path / "demo_app.py"
    script.write_text(
        "import sys, time\n"
        "sys.stderr.write('App started line 1\\n')\n"
        "sys.stderr.flush()\n"
        "sys.stderr.write('App working line 2\\n')\n"
        "sys.stderr.flush()\n"
        "time.sleep(3)\n"
    )

    pid = launch(str(script))
    assert pid > 0

    # Check liveness
    time.sleep(0.2)
    assert is_responsive(pid) is True

    # Check stderr logs
    logs = get_logs(pid, lines=10)
    assert "App started line 1" in logs
    assert "App working line 2" in logs

    # Terminate process
    success = terminate(pid)
    assert success is True
    time.sleep(0.1)
    assert is_responsive(pid) is False


def test_terminate_unowned_pid_raises():
    with pytest.raises(PermissionError, match="not owned by this session"):
        terminate(9999999)


def test_restart_process(tmp_path):
    script = tmp_path / "restart_app.py"
    script.write_text(
        "import sys, time\n"
        "sys.stderr.write('Instance running\\n')\n"
        "sys.stderr.flush()\n"
        "time.sleep(5)\n"
    )

    old_pid = launch(str(script))
    time.sleep(0.2)
    assert is_responsive(old_pid) is True

    new_pid = restart(old_pid)
    assert new_pid != old_pid
    assert is_responsive(old_pid) is False
    assert is_responsive(new_pid) is True

    # Clean up
    terminate(new_pid)
