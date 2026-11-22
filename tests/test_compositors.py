"""Tests for compositors abstraction layer and window_control MCP tool."""

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
    ensure_portal_dialogs_above,
    get_compositor_backend,
    get_display_scale,
    query_window_geometry,
    reset_compositor_backend,
    set_window_state,
)
from wayland_computer_use_mcp.compositors.base import CompositorBackend, DisplayInfo
from wayland_computer_use_mcp.portal import global_portal_session
from wayland_computer_use_mcp.server import window_control


@pytest.fixture(autouse=True)
def reset_backend():
    reset_compositor_backend()
    yield
    reset_compositor_backend()


@pytest.mark.asyncio
async def test_generic_and_protocol_stubs():
    backend = GenericCompositorBackend()
    assert backend.name == "generic"
    assert await backend.is_available() is True
    assert await backend.get_window_geometry(123) is None
    assert await backend.activate_and_raise_window(123) is False
    assert await backend.set_window_state(123, "minimize") is False
    assert await backend.ensure_dialogs_above() is False
    displays = await backend.get_active_displays()
    assert len(displays) == 1 and displays[0].width == 1920

    assert await CompositorBackend.is_available(None) is None
    assert await CompositorBackend.get_window_geometry(None) is None
    assert await CompositorBackend.activate_and_raise_window(None) is None
    assert await CompositorBackend.get_active_displays(None) is None
    assert await CompositorBackend.set_window_state(None, 1, "minimize") is None
    assert await CompositorBackend.ensure_dialogs_above(None) is None


@pytest.mark.asyncio
async def test_kwin_backend_and_read_only_geometry():
    backend = KWinBackend()
    written_scripts: list[str] = []

    class FakeBus:
        unique_name = ":1.55"

        def add_message_handler(self, handler):
            from dbus_fast import Message, MessageType

            handler(
                Message(
                    message_type=MessageType.METHOD_CALL,
                    destination=":1.55",
                    path="/ReportGeom",
                    interface="org.wayland.Mcp",
                    member="Report",
                    signature="iiii",
                    body=[10, 20, 400, 300],
                    serial=1,
                )
            )

        async def call(self, msg):
            if msg.member == "loadScript":
                with open(msg.body[0], "r", encoding="utf-8") as fh:
                    written_scripts.append(fh.read())
            return MagicMock(body=[1])

        def send(self, _msg):
            pass

        def disconnect(self):
            pass

    with (
        patch.dict("os.environ", {"WAYLAND_DISPLAY": "wayland-0", "XDG_CURRENT_DESKTOP": "KDE"}),
        patch("wayland_computer_use_mcp.compositors.kwin.MessageBus") as mock_bus_cls,
    ):
        mock_bus_cls.return_value.connect = AsyncMock(return_value=FakeBus())
        assert await backend.is_available() is True
        geom = await backend.get_window_geometry(pid=1234)
        assert geom == WindowGeometry(10, 20, 400, 300)
        assert "keepAbove" not in written_scripts[0] and "activeWindow" not in written_scripts[0]
        assert await backend.activate_and_raise_window(pid=1234) is True
        assert "workspace.activeWindow" in written_scripts[1]
        assert await backend.set_window_state(1234, "minimize") is True
        assert await backend.ensure_dialogs_above() is True


@pytest.mark.asyncio
async def test_gnome_backend_variants_and_errors():
    backend = GnomeBackend()
    with patch.dict("os.environ", {}, clear=True):
        assert await backend.is_available() is False
    with patch.dict("os.environ", {"WAYLAND_DISPLAY": "wayland-0", "XDG_CURRENT_DESKTOP": "KDE"}):
        assert await backend.is_available() is False

    from dbus_fast import MessageType, Variant

    mock_bus = AsyncMock()
    mock_bus.disconnect = MagicMock()
    flat_reply = MagicMock(
        message_type=MessageType.METHOD_RETURN,
        body=[
            {
                2001: {
                    "pid": Variant("u", 7777),
                    "title": Variant("s", "Wayland MCP Test Rig"),
                    "x": Variant("i", 280),
                    "y": Variant("i", 96),
                    "width": Variant("u", 720),
                    "height": Variant("u", 640),
                }
            }
        ],
    )
    mock_bus.call.return_value = flat_reply

    with patch("wayland_computer_use_mcp.compositors.gnome.MessageBus") as mock_bus_cls:
        mock_bus_cls.return_value.connect = AsyncMock(return_value=mock_bus)
        assert (await backend.get_window_geometry(pid=7777)).tuple == (280, 96, 720, 640)
        assert (await backend.get_window_geometry(title="Wayland MCP Test Rig")).width == 720
        assert await backend.get_window_geometry(pid=9999) is None

        mock_bus.call.return_value = MagicMock(
            message_type=MessageType.METHOD_RETURN,
            body=[
                {
                    2002: {
                        "pid": Variant("u", 8888),
                        "title": Variant("s", "Other"),
                        "rect": Variant("(iiii)", (10, 40, 500, 400)),
                    }
                }
            ],
        )
        assert (await backend.get_window_geometry(pid=8888)).tuple == (10, 40, 500, 400)

        assert await backend.activate_and_raise_window(pid=8888) is True
        assert await backend.set_window_state(8888, "minimize") is False
        assert await backend.ensure_dialogs_above() is True

        mock_bus.call.return_value = MagicMock(
            message_type=MessageType.ERROR, body=["AccessDenied"]
        )
        assert await backend.get_window_geometry(pid=7777) is None


@pytest.mark.asyncio
async def test_wlroots_backend_hyprland_and_sway():
    backend = WlrootsBackend()
    with (
        patch.dict(
            "os.environ", {"WAYLAND_DISPLAY": "wayland-0", "HYPRLAND_INSTANCE_SIGNATURE": "sig"}
        ),
        patch("shutil.which", return_value="/usr/bin/hyprctl"),
    ):
        assert await backend.is_available() is True
        clients = [{"pid": 7777, "title": "Hyprland Target", "at": [50, 60], "size": [1024, 768]}]
        with patch(
            "subprocess.run", return_value=MagicMock(returncode=0, stdout=json.dumps(clients))
        ):
            assert await backend.get_window_geometry(pid=7777) == WindowGeometry(50, 60, 1024, 768)
            assert await backend.activate_and_raise_window(pid=7777) is True
            assert await backend.set_window_state(7777, "close") is True
            assert await backend.set_window_state(7777, "maximize") is True

        mons = [
            {
                "id": 0,
                "name": "DP-1",
                "x": 0,
                "y": 0,
                "width": 2560,
                "height": 1440,
                "focused": True,
            }
        ]
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout=json.dumps(mons))):
            assert (await backend.get_active_displays())[0].width == 2560

    with (
        patch.dict("os.environ", {"WAYLAND_DISPLAY": "wayland-0", "SWAYSOCK": "/tmp/sway.sock"}),
        patch("shutil.which", return_value="/usr/bin/swaymsg"),
    ):
        assert await backend.is_available() is True
        tree = {
            "nodes": [
                {
                    "pid": 8888,
                    "name": "Sway Target",
                    "rect": {"x": 10, "y": 20, "width": 640, "height": 480},
                }
            ]
        }
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout=json.dumps(tree))):
            assert await backend.get_window_geometry(pid=8888) == WindowGeometry(10, 20, 640, 480)
            assert await backend.set_window_state(8888, "maximize") is True
            assert await backend.set_window_state(8888, "close") is True


@pytest.mark.asyncio
async def test_compositor_factory_sync_wrappers_and_display_scale():
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
        assert (await get_compositor_backend()).name == "generic"

    mock_backend = AsyncMock()
    mock_backend.get_window_geometry.return_value = WindowGeometry(10, 20, 300, 400)
    mock_backend.activate_and_raise_window.return_value = True
    mock_backend.set_window_state.return_value = True
    mock_backend.get_active_displays.return_value = [
        DisplayInfo(name="DP-1", x=0, y=0, width=1920, height=1080, scale=1.5)
    ]
    with patch(
        "wayland_computer_use_mcp.compositors.get_compositor_backend",
        AsyncMock(return_value=mock_backend),
    ):
        assert query_window_geometry(123) == (10, 20, 300, 400)
        assert activate_and_raise_window(123) is True
        assert set_window_state(123, "minimize") is True
        assert get_display_scale(100, 100) == 1.5

    with patch.dict("os.environ", {}, clear=True):
        assert query_window_geometry(123) is None

    with patch("wayland_computer_use_mcp.compositors._run_async_sync", return_value=True):
        assert ensure_portal_dialogs_above() is True
        assert set_window_state(9876, "restore") is True


def test_window_control_tool(monkeypatch):
    global_portal_session.target_pid = None
    with pytest.raises(RuntimeError, match="No active application PID"):
        window_control(action="minimize")
    with pytest.raises(ValueError, match="Invalid window action"):
        window_control(action="explode", pid=12345)

    dispatched: list[tuple[int, str]] = []
    activated: list[int] = []
    fake_driver = MagicMock()
    fake_driver.set_window_state.side_effect = lambda p, s: dispatched.append((p, s)) or True
    fake_driver.activate_window.side_effect = lambda pid=None, **kw: activated.append(pid) or True
    monkeypatch.setattr(global_portal_session, "active_driver", lambda: fake_driver)

    for act in ("minimize", "maximize", "restore", "close"):
        assert window_control(action=act, pid=42)["success"] is True
        assert (42, act) in dispatched
    assert window_control(action="focus", pid=42)["success"] is True and 42 in activated

    global_portal_session.target_pid = 7890
    assert window_control(action="minimize")["pid"] == 7890
    assert (7890, "minimize") in dispatched
