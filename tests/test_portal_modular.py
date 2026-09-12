"""Unit tests for the modularized portal pipeline components."""

import os
from unittest.mock import MagicMock

from PIL import Image

from wayland_computer_use_mcp.portal.input import InputDispatcher
from wayland_computer_use_mcp.portal.remotedesktop import (
    AsyncLoopThread,
    RemoteDesktopClient,
)
from wayland_computer_use_mcp.portal.screencast import (
    ScreenCastPipeline,
    crop_element,
)
from wayland_computer_use_mcp.portal.session import PortalSession


def test_screencast_crop_element():
    """Verifies cropping behavior with valid, out-of-bounds, and degenerate bounds."""
    img = Image.new("RGB", (100, 100), color=(255, 0, 0))

    # Valid crop
    cropped = crop_element(img, 10, 10, 20, 20)
    assert cropped.size == (20, 20)

    # Crop exceeding image size clamps to boundary
    cropped_clamp = crop_element(img, 80, 80, 50, 50)
    assert cropped_clamp.size == (20, 20)

    # Degenerate / zero crop returns 1x1 minimal image
    cropped_deg = crop_element(img, 120, 120, 10, 10)
    assert cropped_deg.size == (1, 1)


def test_screencast_pipeline_lifecycle(tmp_path, monkeypatch):
    """Verifies pipeline setup, save, and stop."""
    pipeline = ScreenCastPipeline()
    monkeypatch.setenv("WAYLAND_MCP_SAVE_FRAMES_DIR", str(tmp_path))

    img = Image.new("RGB", (50, 50), color=(0, 255, 0))
    path = pipeline.save_frame(img)
    assert path is not None
    assert os.path.exists(path)
    assert pipeline.last_saved_frame_path == path

    # Stop pipeline safely
    pipeline.stop()
    assert pipeline._gst_pipeline is None


def test_input_dispatcher_mock_actions():
    """Verifies all input gestures execute cleanly with proper validation."""
    mock_rd = MagicMock(spec=RemoteDesktopClient)
    mock_ei = MagicMock()

    dispatcher = InputDispatcher(
        rd_client=mock_rd,
        verify_preconditions_fn=lambda: None,
        get_offsets_fn=lambda: (0, 0),
        is_mock_fn=lambda: False,
        get_ei_client_fn=lambda: mock_ei,
    )

    # Click
    res = dispatcher.dispatch_click(50, 50, button="left")
    assert "Clicked left" in res
    mock_rd.notify_pointer_motion_absolute.assert_called_with(50.0, 50.0)
    mock_ei.button_click.assert_called()

    # Double click
    res = dispatcher.dispatch_double_click(50, 50, button="left")
    assert "Double-clicked" in res
    mock_ei.double_click.assert_called()

    # Right click
    res = dispatcher.dispatch_right_click(60, 60)
    assert "Clicked right" in res

    # Hover
    res = dispatcher.dispatch_hover(70, 70, duration_ms=20)
    assert "Hovered pointer" in res

    # Drag
    res = dispatcher.dispatch_drag(10, 10, 80, 80)
    assert "Dragged from" in res
    mock_ei.button_down.assert_called()
    mock_ei.button_up.assert_called()

    # Scroll
    res = dispatcher.dispatch_scroll(0, 5)
    assert "Scrolled dx=0, dy=5" in res
    mock_rd.notify_pointer_axis.assert_called_with(0.0, 5.0)

    # Type text - fast path
    mock_ei.type_char.return_value = True
    res = dispatcher.dispatch_type_text("Hello", x=10, y=10)
    assert "Typed 5 characters" in res

    # Key combination
    res = dispatcher.dispatch_key_combination(["ctrl", "c"])
    assert "Dispatched key combination: ctrl+c" in res


def test_input_dispatcher_long_text_clipboard_paste(monkeypatch):
    """Verifies strings > 30 characters trigger automatic clipboard paste fallback."""
    mock_rd = MagicMock(spec=RemoteDesktopClient)
    mock_ei = MagicMock()
    pasted_clipboard = []

    monkeypatch.setattr(
        "wayland_computer_use_mcp.portal.input.write_system_clipboard",
        lambda text: pasted_clipboard.append(text) or True,
    )

    dispatcher = InputDispatcher(
        rd_client=mock_rd,
        verify_preconditions_fn=lambda: None,
        get_offsets_fn=lambda: (0, 0),
        is_mock_fn=lambda: False,
        get_ei_client_fn=lambda: mock_ei,
    )

    long_str = "This is a very long string that should be pasted directly via clipboard."
    res = dispatcher.dispatch_type_text(long_str)
    assert "Pasted" in res
    assert long_str in pasted_clipboard


def test_portal_session_mock_mode():
    """Verifies PortalSession properties, mock initialization, and capture."""
    session = PortalSession()
    assert session.is_mock is True  # In test suite with mock config

    session.ensure_initialized()
    assert session.is_active() is True

    frame = session.capture_frame()
    assert isinstance(frame, Image.Image)
    assert frame.size == (1920, 1080)

    # Test Set-of-Marks labeled screenshot
    labeled, elements = session.generate_labeled_screenshot()
    assert isinstance(labeled, Image.Image)
    assert isinstance(elements, list)
    assert len(elements) > 0


def test_async_loop_thread():
    """Verifies AsyncLoopThread executes coroutines and returns results."""
    thread = AsyncLoopThread()

    async def sample_coro(val):
        return val * 2

    res = thread.run(sample_coro(21))
    assert res == 42
