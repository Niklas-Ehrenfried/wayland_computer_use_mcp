"""Unit tests for Wayland data-control clipboard, portal clipboard, and CLI fallbacks."""

from __future__ import annotations

import os
import struct
import subprocess
from unittest.mock import patch

from dbus_fast import MessageType

from wayland_computer_use_mcp import clipboard
from wayland_computer_use_mcp.clipboard import read_system_clipboard, write_system_clipboard
from wayland_computer_use_mcp.config import get_config
from wayland_computer_use_mcp.portal import global_portal_session
from wayland_computer_use_mcp.portal.input import InputDispatcher
from wayland_computer_use_mcp.portal.remotedesktop import AsyncLoopThread, RemoteDesktopClient
from wayland_computer_use_mcp.tools.system_tools import clipboard_write
from wayland_computer_use_mcp.wayland_clipboard import (
    _build_msg,
    _pack_string,
    _pick_text_mime,
    _unpack_string,
)


def test_wayland_wire_helpers():
    for sample in ("", "text/plain", "text/plain;charset=utf-8", "UTF8_STRING"):
        packed = _pack_string(sample)
        assert len(packed) % 4 == 0
        decoded, next_off = _unpack_string(packed, 0)
        assert decoded == sample and next_off == len(packed)

    msg = _build_msg(obj_id=7, opcode=2, payload=b"\x01\x02\x03\x04")
    oid, size_op = struct.unpack_from("<II", msg, 0)
    assert oid == 7 and (size_op & 0xFFFF) == 2 and (size_op >> 16) == 12

    assert (
        _pick_text_mime(["STRING", "text/plain", "text/plain;charset=utf-8"])
        == "text/plain;charset=utf-8"
    )
    assert _pick_text_mime(["text/markdown", "text/html"]) == "text/markdown"
    assert _pick_text_mime(["image/png", "application/octet-stream"]) is None


def test_clipboard_cli_backends_and_dash_flag_safety(monkeypatch):
    with patch("shutil.which") as mock_which, patch("subprocess.run") as mock_run:
        mock_which.side_effect = lambda cmd: "/usr/bin/wl-copy" if cmd == "wl-copy" else None
        mock_run.return_value = subprocess.CompletedProcess(["wl-copy"], 0)
        assert write_system_clipboard("Hello Wayland") is True

        # Dash-leading text must go via stdin without injection flags
        assert write_system_clipboard("-n leading-dash") is True
        assert mock_run.call_args[0][0] == ["wl-copy"]
        assert mock_run.call_args[1]["input"] == b"-n leading-dash"

    with patch("shutil.which") as mock_which, patch("subprocess.run") as mock_run:
        mock_which.side_effect = lambda cmd: (
            f"/usr/bin/{cmd}" if cmd in ("wl-copy", "qdbus6") else None
        )
        mock_run.side_effect = [
            subprocess.SubprocessError("fail"),
            subprocess.CompletedProcess(["qdbus6"], 0),
        ]
        assert write_system_clipboard("Fallback Test") is True

    for bin_name, text in [("qdbus", "Plasma 5"), ("xclip", "X11")]:
        with (
            patch(
                "shutil.which",
                side_effect=lambda c, b=bin_name: f"/usr/bin/{b}" if c == b else None,
            ),
            patch("subprocess.run", return_value=subprocess.CompletedProcess([bin_name], 0)),
        ):
            assert write_system_clipboard(text) is True

    with patch("shutil.which", return_value=None):
        assert write_system_clipboard("None") is False
        assert read_system_clipboard() == ""

    for bin_name, stdout_val, expected in [
        ("wl-paste", "Pasted content", "Pasted content"),
        ("qdbus6", "QDBus text\n", "QDBus text"),
        ("qdbus", "QDBus5 text\n", "QDBus5 text"),
        ("xclip", "xclip text", "xclip text"),
    ]:
        with (
            patch(
                "shutil.which",
                side_effect=lambda c, b=bin_name: f"/usr/bin/{b}" if c == b else None,
            ),
            patch(
                "subprocess.run",
                return_value=subprocess.CompletedProcess([bin_name], 0, stdout=stdout_val),
            ),
        ):
            assert read_system_clipboard() == expected

    # Standalone clipboard tool works without active PID
    global_portal_session.target_pid = None
    msg = clipboard_write("standalone")
    assert "Copied" in msg or "clipboard" in msg


def test_clipboard_skips_wayland_socket_in_mock_and_delegates_to_portal_in_live(monkeypatch):
    cfg = get_config()
    assert cfg.mock_mode is True
    with (
        patch(
            "wayland_computer_use_mcp.wayland_clipboard.write_wayland_clipboard", return_value=True
        ) as mw,
        patch(
            "wayland_computer_use_mcp.wayland_clipboard.read_wayland_clipboard", return_value="x"
        ) as mr,
        patch("shutil.which", return_value=None),
    ):
        assert clipboard.write_system_clipboard("hello") is False
        assert clipboard.read_system_clipboard() == ""
        mw.assert_not_called()
        mr.assert_not_called()

    monkeypatch.setattr(cfg, "mock_mode", False)
    with (
        patch("wayland_computer_use_mcp.clipboard._driver_write_clipboard", return_value=False),
        patch("wayland_computer_use_mcp.clipboard._driver_read_clipboard", return_value=None),
        patch(
            "wayland_computer_use_mcp.wayland_clipboard.write_wayland_clipboard", return_value=True
        ),
        patch(
            "wayland_computer_use_mcp.wayland_clipboard.read_wayland_clipboard",
            return_value="from-wayland",
        ),
    ):
        assert clipboard.write_system_clipboard("hello") is True
        assert clipboard.read_system_clipboard() == "from-wayland"

    loop_thread = AsyncLoopThread()
    rd = RemoteDesktopClient(loop_thread)
    try:
        rd.session_handle = "/org/freedesktop/portal/desktop/session/1"
        rd.clipboard_enabled = True
        r_fd, w_fd = os.pipe()
        os.write(w_fd, b"hello portal clipboard")
        os.close(w_fd)

        class _FakeReply:
            def __init__(self, fds=None):
                self.message_type = MessageType.METHOD_RETURN
                self.unix_fds = fds or []

        class _FakeBus:
            async def call(self, msg):
                if msg.member == "SelectionRead":
                    return _FakeReply(fds=[r_fd])
                return _FakeReply()

        rd.bus = _FakeBus()
        assert rd.write_clipboard_text("test-clip") is True
        assert rd.read_clipboard_text() == "hello portal clipboard"

        monkeypatch.setattr(global_portal_session._host, "rd_client", rd)
        monkeypatch.setattr(
            global_portal_session, "active_driver", lambda: global_portal_session._host
        )
        assert clipboard.write_system_clipboard("abc") is True

        # Typing falls back cleanly to key-by-key typing when clipboard paste fails
        typed_chars: list[str] = []

        class _FakeEI:
            has_absolute_pointer = True
            has_scroll = True
            keyboard_device = 1

            def can_type_text(self, text: str) -> bool:
                return False

            def resolve_key_name(self, key_name: str) -> int | None:
                return None

            def type_char(self, ch: str) -> bool:
                typed_chars.append(ch)
                return True

        dispatcher = InputDispatcher(
            get_sink_fn=lambda: _FakeEI(),
            verify_preconditions_fn=lambda: None,
            get_offsets_fn=lambda: (0, 0),
        )
        monkeypatch.setattr(
            "wayland_computer_use_mcp.portal.input.write_system_clipboard", lambda t: False
        )
        long_text = "x" * 35
        res = dispatcher.dispatch_type_text(long_text)
        assert "Typed 35 characters" in res and "".join(typed_chars) == long_text
    finally:
        loop_thread.stop()
