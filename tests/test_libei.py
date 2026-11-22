"""Comprehensive unit tests for libei wrapper and EIClient input emulation."""

from __future__ import annotations

from unittest.mock import MagicMock

from wayland_computer_use_mcp.libei import (
    BTN_LEFT,
    BTN_RIGHT,
    CHAR_TO_KEYCODE,
    KEY_ENTER,
    KEY_LEFTCTRL,
    KEY_RIGHTALT,
    KEY_V,
    EIClient,
    build_charmap_from_xkb,
    libei,
)


def test_char_to_keycode_mappings():
    assert CHAR_TO_KEYCODE["a"] == (30, False)
    assert CHAR_TO_KEYCODE["A"] == (30, True)
    assert CHAR_TO_KEYCODE["1"] == (2, False)
    assert CHAR_TO_KEYCODE["!"] == (2, True)
    assert CHAR_TO_KEYCODE[" "] == (57, False)
    assert CHAR_TO_KEYCODE["\n"] == (28, False)


def test_eiclient_unconnected_safe_fallbacks():
    client = EIClient(fd=None)
    assert client.fd is None and client.ctx is None and client.pointer_device is None

    client.pointer_motion_absolute(100.0, 200.0)
    client.pointer_motion(10.0, 20.0)
    client.button_click(BTN_LEFT)
    client.double_click(BTN_LEFT)
    client.button_down(BTN_RIGHT)
    client.button_up(BTN_RIGHT)
    client.scroll(0.0, 5.0)
    client.key_press(KEY_ENTER, True)
    client.key_press(KEY_ENTER, False)
    assert client.type_char("§") is False
    assert client.type_char("a") is False
    client.key_combination([KEY_LEFTCTRL, KEY_V])
    client.poll_events(timeout=0.01)
    client.close()


def test_eiclient_mocked_cdll(monkeypatch):
    mock_c = MagicMock()
    mock_c.ei_new_sender.return_value = 0x1000
    mock_c.ei_setup_backend_fd.return_value = 0
    mock_c.ei_dispatch.return_value = 0
    mock_c.ei_get_event.return_value = None

    monkeypatch.setattr(libei, "available", True, raising=False)
    monkeypatch.setattr(libei, "_cdll", mock_c, raising=False)

    client = EIClient(fd=42)
    assert client.ctx == 0x1000
    mock_c.ei_setup_backend_fd.assert_called_with(0x1000, 42)

    mock_dev = 0x2000
    client.pointer_device = client.pointer_abs_device = client.keyboard_device = (
        client.scroll_device
    ) = mock_dev

    client.pointer_motion_absolute(300.0, 400.0)
    call_args = mock_c.ei_device_pointer_motion_absolute.call_args[0]
    assert call_args[0] == mock_dev and call_args[1].value == 300.0 and call_args[2].value == 400.0

    client.button_click(BTN_LEFT)
    assert mock_c.ei_device_button_button.call_count >= 2

    client.scroll(2.0, 3.0)
    scroll_args = mock_c.ei_device_scroll_discrete.call_args[0]
    assert scroll_args[1].value == 120 and scroll_args[2].value == 120

    assert client.type_char("a") is True and client.type_char("Z") is True
    client.key_press(KEY_ENTER, True)
    assert mock_c.ei_device_keyboard_key.call_args[0][1].value == KEY_ENTER

    client.close()
    mock_c.ei_unref.assert_called_with(0x1000)
    assert client.ctx is None


def test_eiclient_event_handling_and_relative_rejection(monkeypatch):
    mock_c = MagicMock()
    mock_c.ei_new_sender.return_value = 0x1000
    mock_c.ei_setup_backend_fd.return_value = 0

    events = [0x5001, 0x5002, 0x5003, None]
    mock_c.ei_get_event.side_effect = lambda ctx: events.pop(0) if events else None
    mock_c.ei_event_get_type.side_effect = lambda ev: {
        0x5001: EIClient.EI_EVENT_CONNECT,
        0x5002: EIClient.EI_EVENT_SEAT_ADDED,
        0x5003: EIClient.EI_EVENT_DEVICE_ADDED,
    }.get(ev, 0)
    mock_c.ei_event_get_seat.return_value = 0x3000
    mock_dev = 0x4000
    mock_c.ei_event_get_device.return_value = mock_dev
    mock_c.ei_device_has_capability.side_effect = lambda dev, cap: (
        cap in (EIClient.EI_DEVICE_CAP_POINTER_ABSOLUTE, EIClient.EI_DEVICE_CAP_BUTTON)
    )

    monkeypatch.setattr(libei, "available", True, raising=False)
    monkeypatch.setattr(libei, "_cdll", mock_c, raising=False)

    client = EIClient(fd=55)
    assert client.pointer_abs_device == mock_dev and client.button_device == mock_dev
    client.close()

    # Relative-only pointer rejects absolute motion
    events_rel = [0x5003]
    mock_c.ei_get_event.side_effect = lambda ctx: events_rel.pop(0) if events_rel else None
    mock_c.ei_device_has_capability.side_effect = lambda dev, cap: (
        cap in (EIClient.EI_DEVICE_CAP_POINTER, EIClient.EI_DEVICE_CAP_BUTTON)
    )
    rel_client = EIClient(fd=56)
    assert rel_client.pointer_abs_device is None and rel_client.has_absolute_pointer is False
    assert rel_client.pointer_motion_absolute(120.0, 240.0) is False
    mock_c.ei_device_pointer_motion_absolute.assert_not_called()
    rel_client.close()


def test_eiclient_emulating_sequence_and_xkb_charmap(monkeypatch):
    mock_c = MagicMock()
    mock_c.ei_new_sender.return_value = 0x1000
    mock_c.ei_setup_backend_fd.return_value = 0
    mock_c.ei_get_event.return_value = None

    monkeypatch.setattr(libei, "available", True, raising=False)
    monkeypatch.setattr(libei, "_cdll", mock_c, raising=False)

    client = EIClient(fd=57)
    client._start_emulating(0x4000)
    client._stop_emulating(0x4000)
    client._start_emulating(0x4000)
    assert [c.args[1].value for c in mock_c.ei_device_start_emulating.call_args_list] == [1, 2]
    client.close()

    xkb_client = EIClient(fd=None)
    xkb_client.keyboard_device = 0x3000
    xkb_client._xkb_charmap = {
        "y": (44, False, False),
        "z": (21, False, False),
        "@": (16, False, True),
    }
    assert xkb_client.resolve_key_name("y") == 44
    assert xkb_client.can_type_text("yz@") is True and xkb_client.can_type_text("yz€") is False

    pressed: list[tuple[int, bool]] = []
    monkeypatch.setattr(xkb_client, "key_press", lambda code, down: pressed.append((code, down)))
    assert xkb_client.type_char("@") is True
    assert pressed == [(KEY_RIGHTALT, True), (16, True), (16, False), (KEY_RIGHTALT, False)]
    assert build_charmap_from_xkb("") == {}
