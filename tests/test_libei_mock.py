"""Comprehensive unit tests for libei wrapper and EIClient input emulation."""

from unittest.mock import MagicMock

from wayland_computer_use_mcp.libei import (
    BTN_LEFT,
    BTN_RIGHT,
    CHAR_TO_KEYCODE,
    KEY_ENTER,
    KEY_LEFTCTRL,
    KEY_V,
    EIClient,
    libei,
)


def test_char_to_keycode_mappings():
    """Verifies standard character to evdev keycode mapping."""
    assert CHAR_TO_KEYCODE["a"] == (30, False)
    assert CHAR_TO_KEYCODE["A"] == (30, True)
    assert CHAR_TO_KEYCODE["1"] == (2, False)
    assert CHAR_TO_KEYCODE["!"] == (2, True)
    assert CHAR_TO_KEYCODE[" "] == (57, False)
    assert CHAR_TO_KEYCODE["\n"] == (28, False)


def test_eiclient_unconnected_safe_fallbacks():
    """Verifies that an unconnected EIClient (fd=None) safely no-ops without crashing."""
    client = EIClient(fd=None)
    assert client.fd is None
    assert client.ctx is None
    assert client.pointer_device is None

    # Actions should be completely safe when unconnected
    client.pointer_motion_absolute(100.0, 200.0)
    client.pointer_motion(10.0, 20.0)
    client.button_click(BTN_LEFT)
    client.double_click(BTN_LEFT)
    client.button_down(BTN_RIGHT)
    client.button_up(BTN_RIGHT)
    client.scroll(0.0, 5.0)
    client.key_press(KEY_ENTER, True)
    client.key_press(KEY_ENTER, False)

    # type_char with unmapped char returns False
    assert client.type_char("§") is False
    # type_char when unconnected returns False to signal clipboard fallback
    assert client.type_char("a") is False

    client.key_combination([KEY_LEFTCTRL, KEY_V])
    client.poll_events(timeout=0.01)
    client.close()


def test_eiclient_mocked_cdll(monkeypatch):
    """Verifies that EIClient properly interacts with libei ctypes C functions."""
    mock_c = MagicMock()
    mock_c.ei_new_sender.return_value = 0x1000
    mock_c.ei_setup_backend_fd.return_value = 0
    mock_c.ei_dispatch.return_value = 0
    mock_c.ei_get_event.return_value = None  # No events in poll loop

    monkeypatch.setattr(libei, "available", True)
    monkeypatch.setattr(libei, "_cdll", mock_c)

    client = EIClient(fd=42)
    assert client.ctx == 0x1000
    mock_c.ei_new_sender.assert_called_once()
    mock_c.ei_setup_backend_fd.assert_called_with(0x1000, 42)

    # Set up mock device pointers
    mock_dev = 0x2000
    client.pointer_device = mock_dev
    client.pointer_abs_device = mock_dev
    client.keyboard_device = mock_dev
    client.scroll_device = mock_dev

    # Test motion
    client.pointer_motion_absolute(300.0, 400.0)
    call_args = mock_c.ei_device_pointer_motion_absolute.call_args[0]
    assert call_args[0] == mock_dev
    assert call_args[1].value == 300.0
    assert call_args[2].value == 400.0
    mock_c.ei_device_frame.assert_called()

    # Test button
    client.button_click(BTN_LEFT)
    assert mock_c.ei_device_button_button.call_count >= 2

    # Test scroll
    client.scroll(2.0, 3.0)
    scroll_args = mock_c.ei_device_scroll_delta.call_args[0]
    assert scroll_args[0] == mock_dev
    assert scroll_args[1].value == 2.0
    assert scroll_args[2].value == 3.0

    # Test type_char with device present
    assert client.type_char("a") is True
    assert client.type_char("Z") is True

    # Test key
    client.key_press(KEY_ENTER, True)
    key_args = mock_c.ei_device_keyboard_key.call_args[0]
    assert key_args[0] == mock_dev
    assert key_args[1].value == KEY_ENTER
    assert key_args[2] is True

    # Test close
    client.close()
    mock_c.ei_unref.assert_called_with(0x1000)
    assert client.ctx is None


def test_eiclient_event_handling(monkeypatch):
    """Verifies that poll_events correctly processes CONNECT, SEAT_ADDED,
    and DEVICE_ADDED events.
    """
    mock_c = MagicMock()
    mock_c.ei_new_sender.return_value = 0x1000
    mock_c.ei_setup_backend_fd.return_value = 0

    # Event sequence: CONNECT, SEAT_ADDED, DEVICE_ADDED (pointer), None
    events = [0x5001, 0x5002, 0x5003, None]

    def mock_get_event(ctx):
        if events:
            return events.pop(0)
        return None

    mock_c.ei_get_event.side_effect = mock_get_event
    mock_c.ei_event_get_type.side_effect = lambda ev: {
        0x5001: EIClient.EI_EVENT_CONNECT,
        0x5002: EIClient.EI_EVENT_SEAT_ADDED,
        0x5003: EIClient.EI_EVENT_DEVICE_ADDED,
    }.get(ev, 0)

    mock_seat = 0x3000
    mock_c.ei_event_get_seat.return_value = mock_seat
    mock_dev = 0x4000
    mock_c.ei_event_get_device.return_value = mock_dev

    # Let device have POINTER_ABSOLUTE and BUTTON
    mock_c.ei_device_has_capability.side_effect = lambda dev, cap: (
        cap
        in (
            EIClient.EI_DEVICE_CAP_POINTER_ABSOLUTE,
            EIClient.EI_DEVICE_CAP_BUTTON,
        )
    )

    monkeypatch.setattr(libei, "available", True)
    monkeypatch.setattr(libei, "_cdll", mock_c)

    client = EIClient(fd=55)
    assert client.pointer_abs_device == mock_dev
    assert client.button_device == mock_dev
    client.close()
