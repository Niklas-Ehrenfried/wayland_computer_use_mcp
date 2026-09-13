"""Tests for CLI flags (--help, --version) and server lifecycle process cleanup."""

from unittest.mock import MagicMock, patch

import pytest

from wayland_computer_use_mcp.config import Config, get_help_text
from wayland_computer_use_mcp.process import (
    _lock,
    _processes,
    list_active_processes,
    prune_dead_processes,
    terminate_all_processes,
)
from wayland_computer_use_mcp.server import _cleanup_server_resources


def test_get_help_text_content():
    """Verifies that get_help_text contains essential flags."""
    text = get_help_text()
    assert "--live" in text
    assert "--virtual" in text
    assert "--window-only" in text
    assert "--fullscreen" in text
    assert "--mock" in text
    assert "--help" in text
    assert "--version" in text


def test_cli_help_flag_exits_zero():
    """Verifies that passing --help prints usage and exits cleanly with 0."""
    cfg = Config()
    with pytest.raises(SystemExit) as exc_info:
        cfg.apply_cli_args(["--help"])
    assert exc_info.value.code == 0


def test_cli_version_flag_exits_zero(capsys):
    """Verifies that passing --version prints version and exits cleanly with 0."""
    cfg = Config()
    with pytest.raises(SystemExit) as exc_info:
        cfg.apply_cli_args(["--version"])
    assert exc_info.value.code == 0
    out = capsys.readouterr().out
    assert "wayland-computer-use-mcp" in out


def test_prune_dead_processes():
    """Verifies that exited processes are pruned from registry."""
    mock_proc_alive = MagicMock()
    mock_proc_alive.poll.return_value = None

    mock_proc_dead = MagicMock()
    mock_proc_dead.poll.return_value = 0

    with _lock:
        _processes[99901] = mock_proc_alive
        _processes[99902] = mock_proc_dead

    try:
        pruned = prune_dead_processes()
        assert 99902 in pruned
        with _lock:
            assert 99902 not in _processes
            assert 99901 in _processes
    finally:
        with _lock:
            _processes.pop(99901, None)
            _processes.pop(99902, None)


def test_list_active_processes():
    """Verifies active processes list returns valid metadata."""
    mock_proc = MagicMock()
    mock_proc.poll.return_value = None

    with _lock:
        _processes[99903] = mock_proc

    try:
        active = list_active_processes()
        pids = [p["pid"] for p in active]
        assert 99903 in pids
    finally:
        with _lock:
            _processes.pop(99903, None)


def test_terminate_all_processes():
    """Verifies that terminate_all_processes attempts termination of all tracked processes."""
    mock_proc = MagicMock()
    mock_proc.poll.return_value = 0

    with _lock:
        _processes[99904] = mock_proc

    try:
        with patch("wayland_computer_use_mcp.process.terminate") as mock_term:
            mock_term.return_value = True
            terminated = terminate_all_processes()
            assert 99904 in terminated
            mock_term.assert_called_with(99904)
    finally:
        with _lock:
            _processes.pop(99904, None)


def test_cleanup_server_resources():
    """Verifies that server cleanup triggers portal session close and process termination."""
    with (
        patch("wayland_computer_use_mcp.portal.global_portal_session.close") as mock_close,
        patch("wayland_computer_use_mcp.process.terminate_all_processes") as mock_term_all,
    ):
        _cleanup_server_resources()
        mock_close.assert_called_once()
        mock_term_all.assert_called_once()
