"""Unit tests for pure-Python Wayland data-control clipboard wire helpers."""

from __future__ import annotations

import struct
from unittest.mock import patch

from wayland_computer_use_mcp import clipboard
from wayland_computer_use_mcp.config import get_config
from wayland_computer_use_mcp.wayland_clipboard import (
    _build_msg,
    _pack_string,
    _pick_text_mime,
    _unpack_string,
)


def test_wayland_wire_string_packing_roundtrip():
    for sample in ("", "text/plain", "text/plain;charset=utf-8", "UTF8_STRING"):
        packed = _pack_string(sample)
        assert len(packed) % 4 == 0
        decoded, next_off = _unpack_string(packed, 0)
        assert decoded == sample
        assert next_off == len(packed)


def test_wayland_wire_message_header():
    msg = _build_msg(obj_id=7, opcode=2, payload=b"\x01\x02\x03\x04")
    oid, size_op = struct.unpack_from("<II", msg, 0)
    assert oid == 7
    assert (size_op & 0xFFFF) == 2
    assert (size_op >> 16) == 12


def test_pick_text_mime_priority():
    assert _pick_text_mime(["STRING", "text/plain", "text/plain;charset=utf-8"]) == (
        "text/plain;charset=utf-8"
    )
    assert _pick_text_mime(["text/markdown", "text/html"]) == "text/markdown"
    assert _pick_text_mime(["image/png", "application/octet-stream"]) is None


def test_clipboard_module_skips_wayland_socket_in_mock_mode(monkeypatch):
    cfg = get_config()
    assert cfg.mock_mode is True
    with (
        patch(
            "wayland_computer_use_mcp.wayland_clipboard.write_wayland_clipboard",
            return_value=True,
        ) as mock_write,
        patch("shutil.which", return_value=None),
    ):
        assert clipboard.write_system_clipboard("hello") is False
        mock_write.assert_not_called()

    with (
        patch(
            "wayland_computer_use_mcp.wayland_clipboard.read_wayland_clipboard",
            return_value="should-not-read",
        ) as mock_read,
        patch("shutil.which", return_value=None),
    ):
        assert clipboard.read_system_clipboard() == ""
        mock_read.assert_not_called()

    monkeypatch.setattr(cfg, "mock_mode", False)
    with (
        patch("wayland_computer_use_mcp.clipboard._portal_write_clipboard", return_value=False),
        patch("wayland_computer_use_mcp.clipboard._portal_read_clipboard", return_value=None),
        patch(
            "wayland_computer_use_mcp.wayland_clipboard.write_wayland_clipboard",
            return_value=True,
        ) as mock_write,
        patch(
            "wayland_computer_use_mcp.wayland_clipboard.read_wayland_clipboard",
            return_value="from-wayland",
        ) as mock_read,
    ):
        assert clipboard.write_system_clipboard("hello") is True
        mock_write.assert_called_once_with("hello")
        assert clipboard.read_system_clipboard() == "from-wayland"
        mock_read.assert_called_once()
