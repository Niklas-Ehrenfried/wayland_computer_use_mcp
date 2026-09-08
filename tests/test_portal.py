"""Tests for portal session, frame cropping, and clamped input dispatching."""

import pytest
from PIL import Image

from wayland_computer_use_mcp.portal import PortalSession
from wayland_computer_use_mcp.security import global_clamper, global_geometry_detector


def test_portal_mock_capture_and_cropping():
    session = PortalSession()
    session._init_mock_session()

    # Capture full frame
    frame = session.capture_frame()
    assert isinstance(frame, Image.Image)
    assert frame.size == (1920, 1080)

    # Crop element [x, y, w, h]
    cropped = session.capture_frame(crop_box=[100, 100, 200, 150])
    assert cropped.size == (200, 150)


def test_crop_element_boundary_clamping():
    session = PortalSession()
    base_img = Image.new("RGBA", (500, 400), (255, 0, 0, 255))

    # Crop extending beyond image boundary
    cropped = session.crop_element(base_img, 450, 350, 100, 100)
    assert cropped.size == (50, 50)

    # Negative coordinates
    cropped_neg = session.crop_element(base_img, -50, -50, 100, 100)
    assert cropped_neg.size == (50, 50)


def test_dispatch_click_clamping():
    session = PortalSession()
    session._init_mock_session()
    global_clamper.set_bounds(800, 600)
    global_geometry_detector.record_capture(800, 600)

    # Valid click within bounds
    msg = session.dispatch_click(400, 300, button="left")
    assert "Clicked left button at (400, 300)" in msg

    # Out of bounds click raises ValueError
    with pytest.raises(ValueError, match="out of window bounds"):
        session.dispatch_click(900, 300)


def test_dispatch_drag_clamping():
    session = PortalSession()
    session._init_mock_session()
    global_clamper.set_bounds(800, 600)
    global_geometry_detector.record_capture(800, 600)

    msg = session.dispatch_drag(100, 100, 500, 500)
    assert "Dragged from (100, 100) to (500, 500)" in msg

    with pytest.raises(ValueError, match="out of window bounds"):
        session.dispatch_drag(100, 100, 1000, 500)


def test_dispatch_scroll_and_type():
    session = PortalSession()
    session._init_mock_session()

    scroll_msg = session.dispatch_scroll(0, -100)
    assert "Scrolled dx=0, dy=-100" in scroll_msg

    type_msg = session.dispatch_type_text("Hello World!")
    assert "Typed 12 characters" in type_msg

    type_msg_coords = session.dispatch_type_text("Hello World!", x=100, y=100)
    assert "Clicked" in type_msg_coords
    assert "Typed 12 characters" in type_msg_coords
