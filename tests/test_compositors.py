"""Unit tests for the modular Compositor Abstraction Layer.

Covers KWinBackend, GnomeBackend, WlrootsBackend, GenericCompositorBackend,
auto-detection, error handling, and window state changes.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from wayland_computer_use_mcp.compositors import (
    GenericCompositorBackend,
    GnomeBackend,
    KWinBackend,
    WindowGeometry,
    WlrootsBackend,
    activate_and_raise_window,
    get_compositor_backend,
    minimize_window,
    query_window_geometry,
    reset_compositor_backend,
    restore_window,
    set_window_state,
)


@pytest.fixture(autouse=True)
def reset_backend():
    reset_compositor_backend()
    yield
    reset_compositor_backend()


@pytest.mark.asyncio
async def test_generic_compositor_backend():
    backend = GenericCompositorBackend()
    assert backend.name == "generic"
    assert await backend.is_available() is True
    assert await backend.get_window_geometry(123) is None
    assert await backend.activate_and_raise_window(123) is False
    assert await backend.set_window_state(123, "minimize") is False
    assert await backend.ensure_dialogs_above() is False
    displays = await backend.get_active_displays()
    assert len(displays) == 1
    assert displays[0].width == 1920


@pytest.mark.asyncio
async def test_kwin_backend_available_and_geometry():
    backend = KWinBackend()
    with patch.dict("os.environ", {"WAYLAND_DISPLAY": "wayland-0", "XDG_CURRENT_DESKTOP": "KDE"}):
        mock_bus = AsyncMock()
        mock_bus.disconnect = MagicMock()
        mock_bus.unique_name = ":1.99"
        mock_reply = MagicMock()
        mock_reply.body = [True]
        mock_bus.call.return_value = mock_reply

        with patch("wayland_computer_use_mcp.compositors.kwin.MessageBus") as mock_bus_cls:
            mock_bus_cls.return_value.connect = AsyncMock(return_value=mock_bus)
            assert await backend.is_available() is True

            # Geometry query with /ReportGeom message simulation
            from dbus_fast import Message, MessageType

            def fake_add_handler(handler):
                fake_msg = Message(
                    message_type=MessageType.METHOD_CALL,
                    destination=":1.99",
                    path="/ReportGeom",
                    interface="org.wayland.Mcp",
                    member="Report",
                    signature="iiii",
                    body=[100, 200, 800, 600],
                    serial=1,
                )
                handler(fake_msg)

            mock_bus.add_message_handler = fake_add_handler
            mock_load_reply = MagicMock(body=[1])
            mock_bus.call = AsyncMock(return_value=mock_load_reply)
            mock_bus.send = MagicMock()

            geom = await backend.get_window_geometry(pid=1234)
            assert geom == WindowGeometry(100, 200, 800, 600)
            assert await backend.activate_and_raise_window(pid=1234) is True

            # Window state control
            assert await backend.set_window_state(1234, "minimize") is True

            # Dialogs above
            assert await backend.ensure_dialogs_above() is True


@pytest.mark.asyncio
async def test_gnome_backend_unavailability():
    backend = GnomeBackend()
    with patch.dict("os.environ", {}, clear=True):
        assert await backend.is_available() is False

    with patch.dict("os.environ", {"WAYLAND_DISPLAY": "wayland-0", "XDG_CURRENT_DESKTOP": "KDE"}):
        assert await backend.is_available() is False


@pytest.mark.asyncio
async def test_gnome_backend_window_geometry():
    backend = GnomeBackend()
    mock_bus = AsyncMock()
    mock_bus.disconnect = MagicMock()
    mock_reply = MagicMock()

    # GNOME Shell Introspect GetWindows returns {id: {pid, title, rect}}
    mock_reply.body = [
        {
            1001: {
                "pid": 5555,
                "title": "Test Application",
                "rect": [120, 150, 800, 600],
            }
        }
    ]
    mock_bus.call.return_value = mock_reply

    with patch("wayland_computer_use_mcp.compositors.gnome.MessageBus") as mock_bus_cls:
        mock_bus_cls.return_value.connect = AsyncMock(return_value=mock_bus)

        geom = await backend.get_window_geometry(pid=5555)
        assert geom is not None
        assert geom.x == 120
        assert geom.y == 150
        assert geom.width == 800
        assert geom.height == 600

        # Query by title
        geom_by_title = await backend.get_window_geometry(title="Test Application")
        assert geom_by_title is not None
        assert geom_by_title.width == 800

        # Query non-matching PID
        assert await backend.get_window_geometry(pid=9999) is None

        # Activation
        assert await backend.activate_and_raise_window(pid=5555) is True
        assert await backend.set_window_state(5555, "minimize") is True
        assert await backend.ensure_dialogs_above() is True


@pytest.mark.asyncio
async def test_wlroots_backend_hyprland():
    backend = WlrootsBackend()
    with patch.dict(
        "os.environ",
        {"WAYLAND_DISPLAY": "wayland-0", "HYPRLAND_INSTANCE_SIGNATURE": "dummy_sig"},
    ):
        with patch("shutil.which", return_value="/usr/bin/hyprctl"):
            assert await backend.is_available() is True

            mock_clients = [
                {
                    "pid": 7777,
                    "title": "Hyprland Target",
                    "at": [50, 60],
                    "size": [1024, 768],
                }
            ]
            mock_proc = MagicMock(returncode=0, stdout=json.dumps(mock_clients))
            with patch("subprocess.run", return_value=mock_proc) as mock_run:
                geom = await backend.get_window_geometry(pid=7777)
                assert geom == WindowGeometry(50, 60, 1024, 768)

                # Focus
                assert await backend.activate_and_raise_window(pid=7777) is True
                assert mock_run.called

                # Close
                assert await backend.set_window_state(7777, "close") is True


@pytest.mark.asyncio
async def test_wlroots_backend_sway():
    backend = WlrootsBackend()
    with patch.dict("os.environ", {"WAYLAND_DISPLAY": "wayland-0", "SWAYSOCK": "/tmp/sway.sock"}):
        with patch("shutil.which", return_value="/usr/bin/swaymsg"):
            assert await backend.is_available() is True

            mock_tree = {
                "nodes": [
                    {
                        "pid": 8888,
                        "name": "Sway Target",
                        "rect": {"x": 10, "y": 20, "width": 640, "height": 480},
                    }
                ]
            }
            mock_proc = MagicMock(returncode=0, stdout=json.dumps(mock_tree))
            with patch("subprocess.run", return_value=mock_proc):
                geom = await backend.get_window_geometry(pid=8888)
                assert geom == WindowGeometry(10, 20, 640, 480)


@pytest.mark.asyncio
async def test_compositor_factory_selection():
    # If all unavailable, falls back to generic
    with (
        patch(
            "wayland_computer_use_mcp.compositors.kwin.KWinBackend.is_available",
            AsyncMock(return_value=False),
        ),
        patch(
            "wayland_computer_use_mcp.compositors.gnome.GnomeBackend.is_available",
            AsyncMock(return_value=False),
        ),
        patch(
            "wayland_computer_use_mcp.compositors.wlroots.WlrootsBackend.is_available",
            AsyncMock(return_value=False),
        ),
    ):
        backend = await get_compositor_backend()
        assert backend.name == "generic"


def test_sync_wrappers():
    mock_backend = AsyncMock()
    mock_backend.get_window_geometry.return_value = WindowGeometry(10, 20, 300, 400)
    mock_backend.activate_and_raise_window.return_value = True
    mock_backend.set_window_state.return_value = True
    mock_backend.ensure_dialogs_above.return_value = True

    with patch(
        "wayland_computer_use_mcp.compositors.get_compositor_backend",
        AsyncMock(return_value=mock_backend),
    ):
        assert query_window_geometry(123) == (10, 20, 300, 400)
        assert activate_and_raise_window(123) is True
        assert minimize_window(123) is True
        assert restore_window(123) is True
        assert set_window_state(123, "maximize") is True


@pytest.mark.asyncio
async def test_gnome_backend_available_with_owner():
    """Verifies GnomeBackend reports available when session bus confirms Introspect owner."""
    backend = GnomeBackend()
    mock_bus = AsyncMock()
    mock_bus.disconnect = MagicMock()
    mock_reply = MagicMock(body=[True])
    mock_bus.call.return_value = mock_reply

    with patch.dict(
        "os.environ",
        {"WAYLAND_DISPLAY": "wayland-0", "XDG_CURRENT_DESKTOP": "GNOME"},
    ):
        with patch("wayland_computer_use_mcp.compositors.gnome.MessageBus") as mock_bus_cls:
            mock_bus_cls.return_value.connect = AsyncMock(return_value=mock_bus)
            assert await backend.is_available() is True


@pytest.mark.asyncio
async def test_compositor_displays_and_dialogs():
    """Verifies get_active_displays and dialog handling across backends."""
    kwin = KWinBackend()
    displays = await kwin.get_active_displays()
    assert len(displays) >= 1
    assert displays[0].width > 0

    gnome = GnomeBackend()
    g_displays = await gnome.get_active_displays()
    assert len(g_displays) >= 1
    assert g_displays[0].name == "Primary"

    wlroots = WlrootsBackend()
    w_displays = await wlroots.get_active_displays()
    assert len(w_displays) >= 1
    assert await wlroots.ensure_dialogs_above() is True


@pytest.mark.asyncio
async def test_compositor_protocol_stubs():
    """Verifies default Protocol method execution."""
    from wayland_computer_use_mcp.compositors.base import CompositorBackend

    assert await CompositorBackend.is_available(None) is None
    assert await CompositorBackend.get_window_geometry(None) is None
    assert await CompositorBackend.activate_and_raise_window(None) is None
    assert await CompositorBackend.get_active_displays(None) is None
    assert await CompositorBackend.set_window_state(None, 1, "minimize") is None
    assert await CompositorBackend.ensure_dialogs_above(None) is None
