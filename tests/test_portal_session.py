"""Unit tests for PortalSession, ScreenCast, InputDispatcher, and RemoteDesktop portal flows."""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from dbus_fast import MessageType, Variant
from PIL import Image

from wayland_computer_use_mcp.a11y.tree import _node_cache
from wayland_computer_use_mcp.compat.host_driver import HostInputSink
from wayland_computer_use_mcp.config import get_config
from wayland_computer_use_mcp.libei import EIClient
from wayland_computer_use_mcp.portal import PortalSession, global_portal_session
from wayland_computer_use_mcp.portal import remotedesktop as rd_mod
from wayland_computer_use_mcp.portal.input import InputDispatcher
from wayland_computer_use_mcp.portal.remotedesktop import AsyncLoopThread, RemoteDesktopClient
from wayland_computer_use_mcp.portal.screencast import (
    ScreenCastPipeline,
    _capture_portal_screenshot_frame,
    crop_element,
)
from wayland_computer_use_mcp.security import global_clamper, global_geometry_detector
from wayland_computer_use_mcp.server import capture_window_frame


def test_portal_mock_capture_cropping_and_clamping():
    session = PortalSession()
    session.ensure_initialized()
    assert session.is_mock is True and session.is_active() is True

    assert session.capture_frame().size == (1920, 1080)
    assert session.capture_frame(crop_box=[100, 100, 200, 150]).size == (200, 150)

    base_img = Image.new("RGBA", (500, 400), (255, 0, 0, 255))
    assert session.crop_element(base_img, 450, 350, 100, 100).size == (50, 50)
    assert session.crop_element(base_img, -50, -50, 100, 100).size == (50, 50)

    global_clamper.set_bounds(800, 600)
    global_geometry_detector.record_capture(800, 600)
    assert "Clicked left button at (400, 300)" in session.dispatch_click(400, 300, button="left")
    with pytest.raises(ValueError, match="out of window bounds"):
        session.dispatch_click(900, 300)

    assert "Dragged from (100, 100) to (500, 500)" in session.dispatch_drag(100, 100, 500, 500)
    with pytest.raises(ValueError, match="out of window bounds"):
        session.dispatch_drag(100, 100, 1000, 500)

    assert "Scrolled dx=0, dy=-100" in session.dispatch_scroll(0, -100)
    assert "Typed 12 characters" in session.dispatch_type_text("Hello World!")
    msg_coords = session.dispatch_type_text("Hello World!", x=100, y=100)
    assert "Clicked" in msg_coords and "Typed 12 characters" in msg_coords


def test_screencast_crop_pipeline_and_artifact_save(tmp_path, monkeypatch):
    img = Image.new("RGB", (100, 100), color=(255, 0, 0))
    assert crop_element(img, 10, 10, 20, 20).size == (20, 20)
    assert crop_element(img, 80, 80, 50, 50).size == (20, 20)
    assert crop_element(img, 120, 120, 10, 10).size == (1, 1)

    pipeline = ScreenCastPipeline()
    monkeypatch.setenv("WAYLAND_MCP_SAVE_FRAMES_DIR", str(tmp_path))
    saved_path = pipeline.save_frame(Image.new("RGB", (50, 50), color=(0, 255, 0)))
    assert saved_path and os.path.exists(saved_path)
    pipeline.stop()
    assert pipeline._gst_pipeline is None

    # capture_window_frame defaults to in-memory, supports save_artifact=True + target + SoM overlay
    cap_dir = tmp_path / "cap"
    cap_dir.mkdir()
    monkeypatch.setenv("WAYLAND_MCP_SAVE_FRAMES_DIR", str(cap_dir))
    res_mem = capture_window_frame()
    assert res_mem.file_path == "" and not (cap_dir / "latest_capture.png").exists()

    res_disk = capture_window_frame(
        target="b1", annotate_elements=True, save_artifact=True, pid=123
    )
    assert res_disk.file_path == str((cap_dir / "latest_capture.png").resolve())
    assert (cap_dir / "latest_capture.png").exists() and len(res_disk.raw_bytes) > 0


def test_capture_window_frame_missing_target_hint(monkeypatch):
    class _DummyDriver:
        name = "dummy"

    cfg = get_config()
    monkeypatch.setattr(cfg, "mock_mode", False)
    monkeypatch.setattr(global_portal_session, "ensure_initialized", lambda: None)
    monkeypatch.setattr(global_portal_session, "check_active_app", lambda pid=None: None)
    monkeypatch.setattr(global_portal_session, "active_driver", lambda: _DummyDriver())
    monkeypatch.setattr(
        "wayland_computer_use_mcp.tools.visual_tools.focus_window", lambda pid: None
    )
    monkeypatch.setattr(
        global_portal_session,
        "capture_frame",
        lambda crop_box=None, save_to_disk=False: Image.new("RGBA", (100, 80), (0, 0, 0, 255)),
    )

    def _fake_tree(p: int):
        node = {
            "id": "b1",
            "name": "Submit Form",
            "role": "push button",
            "bounds": [10, 10, 50, 20],
        }
        _node_cache[p] = {"b1": node}
        return {"interactive_elements": [node], "children": []}

    monkeypatch.setattr(
        "wayland_computer_use_mcp.tools.visual_tools.get_application_tree", _fake_tree
    )
    res = capture_window_frame(target="Submt", pid=123)
    assert "not found" in res[0].text and "Submit Form" in res[0].text


def test_screencast_cli_and_hidpi_crop(monkeypatch, tmp_path):
    import subprocess

    pipe = ScreenCastPipeline()
    pipe._node_id = 77
    monkeypatch.setattr(
        "shutil.which", lambda name: "/usr/bin/gst-launch-1.0" if name == "gst-launch-1.0" else None
    )

    def _fake_gst_run(cmd, **kwargs):
        out_path = next(a for a in cmd if a.startswith("location=")).split("=", 1)[1]
        Image.new("RGB", (32, 24), "green").save(out_path, format="PNG")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr("subprocess.run", _fake_gst_run)
    assert pipe._pull_gst_cli_frame().size == (32, 24)

    async def _raise_err():
        raise RuntimeError("no dbus in unit test")

    monkeypatch.setattr("dbus_fast.aio.MessageBus.connect", lambda self: _raise_err())
    assert _capture_portal_screenshot_frame() is None

    # HiDPI scale compensation in capture_frame
    monkeypatch.setattr(get_config(), "mock_mode", False)
    monkeypatch.setattr(global_portal_session, "_initialized", True)
    global_portal_session.target_pid = 31313
    monkeypatch.setattr(global_portal_session, "check_active_app", lambda pid=None: None)
    monkeypatch.setattr(global_portal_session, "_ensure_host_portal_ready", lambda: None)
    monkeypatch.setattr(
        global_portal_session, "_resolve_target_geometry", lambda: (100, 50, 400, 300)
    )

    class ScaleDriver:
        name = "scale_mock"

        def capture_output_frame(self):
            return Image.new("RGBA", (1920, 1080), (0, 0, 0, 255))

    monkeypatch.setattr(global_portal_session, "active_driver", lambda: ScaleDriver())
    monkeypatch.setattr("wayland_computer_use_mcp.compositors.get_display_scale", lambda x, y: 1.5)
    assert global_portal_session.capture_frame(save_to_disk=False).size == (600, 450)


def test_input_dispatcher_ei_rd_fallback_and_paste_paths(monkeypatch):
    mock_rd = MagicMock(spec=RemoteDesktopClient)
    mock_ei = MagicMock()
    mock_rd.ei_client = mock_ei
    sink = HostInputSink(mock_rd)
    dispatcher = InputDispatcher(
        get_sink_fn=lambda: sink,
        verify_preconditions_fn=lambda: None,
        get_offsets_fn=lambda: (0, 0),
    )

    assert "Clicked left" in dispatcher.dispatch_click(50, 50, button="left")
    mock_ei.pointer_motion_absolute.assert_called_with(50.0, 50.0)
    mock_rd.notify_pointer_motion_absolute.assert_not_called()
    assert "Double-clicked" in dispatcher.dispatch_double_click(50, 50, button="left")
    assert "Clicked right" in dispatcher.dispatch_right_click(60, 60)
    assert "Hovered pointer" in dispatcher.dispatch_hover(70, 70, duration_ms=20)
    assert "Dragged from" in dispatcher.dispatch_drag(10, 10, 80, 80)
    assert "Scrolled dx=0, dy=5" in dispatcher.dispatch_scroll(0, 5)
    mock_ei.type_char.return_value = True
    assert "Typed 5 characters" in dispatcher.dispatch_type_text("Hello", x=10, y=10)
    assert "Dispatched key combination: ctrl+c" in dispatcher.dispatch_key_combination(
        ["ctrl", "c"]
    )

    # Clipboard paste for >30 chars or multiline/unicode text
    pasted: list[str] = []
    monkeypatch.setattr(
        "wayland_computer_use_mcp.portal.input.write_system_clipboard",
        lambda text: pasted.append(text) or True,
    )
    long_str = "This is a very long string that should be pasted directly via clipboard."
    assert "Pasted" in dispatcher.dispatch_type_text(long_str)
    assert "Pasted" in dispatcher.dispatch_type_text("line1\nline2")
    assert pasted == [long_str, "line1\nline2"]

    # Fallback to RemoteDesktopClient when EI devices absent
    mock_ei_empty = MagicMock(
        pointer_abs_device=None,
        pointer_device=None,
        button_device=None,
        scroll_device=None,
        keyboard_device=None,
    )
    mock_rd.ei_client = mock_ei_empty
    assert "Clicked left" in dispatcher.dispatch_click(50, 50, button="left")
    mock_rd.notify_pointer_motion_absolute.assert_called_with(50.0, 50.0)
    assert "Hovered pointer" in dispatcher.dispatch_hover(70, 70, duration_ms=20)
    assert "Scrolled dx=0, dy=5" in dispatcher.dispatch_scroll(0, 5)


def test_input_dispatcher_blocks_relative_only_eis_without_screencast():
    mock_rd = MagicMock(spec=RemoteDesktopClient)
    mock_rd.pipewire_node_id = None
    ei = EIClient(fd=None)
    ei.ctx = 0x1234
    ei.pointer_rel_device = 0x5678
    ei.pointer_device = 0x5678
    ei.button_device = 0x5678
    ei.button_click = MagicMock()
    mock_rd.ei_client = ei
    dispatcher = InputDispatcher(
        get_sink_fn=lambda: HostInputSink(mock_rd),
        verify_preconditions_fn=lambda: None,
        get_offsets_fn=lambda: (0, 0),
    )

    with pytest.raises(RuntimeError, match="relative pointer without a ScreenCast stream"):
        dispatcher.dispatch_click(100, 120)
    with pytest.raises(RuntimeError, match="relative pointer without a ScreenCast stream"):
        dispatcher.dispatch_drag(100, 120, 300, 120)
    ei.button_click.assert_not_called()


def test_remotedesktop_persistence_streamless_clear_and_cleanup(monkeypatch):
    thread = AsyncLoopThread()
    try:
        assert thread.run(asyncio.sleep(0, result=42)) == 42
        empty_client = RemoteDesktopClient(thread)
        empty_client.close()
        assert empty_client.ei_client is None and empty_client.bus is None
    finally:
        thread.stop()

    def _make_fake_bus(return_streams: bool):
        sent_calls: list[tuple[str, str, list]] = []

        class FakeBus:
            def __init__(self):
                self.unique_name = ":1.99"
                self.handlers = []

            def add_message_handler(self, fn):
                self.handlers.append(fn)

            def remove_message_handler(self, fn):
                if fn in self.handlers:
                    self.handlers.remove(fn)

            def disconnect(self):
                pass

            async def call(self, msg):
                sent_calls.append((msg.interface, msg.member, msg.body))
                if msg.interface == "org.freedesktop.DBus.Properties" and msg.member == "Get":
                    return SimpleNamespace(
                        message_type=MessageType.METHOD_RETURN, body=[Variant("u", 2)], unix_fds=[]
                    )
                if (
                    msg.interface == "org.freedesktop.portal.RemoteDesktop"
                    and msg.member == "ConnectToEIS"
                ):
                    return SimpleNamespace(message_type=MessageType.ERROR, body=["no"], unix_fds=[])
                if msg.interface in (
                    "org.freedesktop.portal.RemoteDesktop",
                    "org.freedesktop.portal.ScreenCast",
                ) and isinstance(msg.body, list):
                    opts = next((b for b in msg.body if isinstance(b, dict)), {})
                    token_var = opts.get("handle_token")
                    if token_var is not None:
                        token = token_var.value if isinstance(token_var, Variant) else token_var
                        results = {}
                        if msg.member == "CreateSession":
                            results = {
                                "session_handle": Variant("s", "/org/freedesktop/portal/s/1")
                            }
                        elif msg.member == "Start":
                            results = {"restore_token": Variant("s", "new_tok_123")}
                            if return_streams:
                                results["streams"] = Variant("a(ua{sv})", [(42, {})])
                        sig = SimpleNamespace(
                            interface="org.freedesktop.portal.Request",
                            member="Response",
                            path=f"/org/freedesktop/portal/desktop/request/1_99/{token}",
                            body=[0, results],
                        )
                        for h in list(self.handlers):
                            h(sig)
                return SimpleNamespace(message_type=MessageType.METHOD_RETURN, body=[], unix_fds=[])

        return FakeBus, sent_calls

    FakeBus1, sent_calls1 = _make_fake_bus(return_streams=True)

    class _BusFactory:
        def __init__(self, **kw):
            self.bus = FakeBus1()

        async def connect(self):
            return self.bus

    monkeypatch.setattr(rd_mod, "MessageBus", _BusFactory)
    monkeypatch.setattr(rd_mod.global_token_store, "get_token", lambda app_id: "old_tok_999")
    saved_tokens: dict[str, str] = {}
    monkeypatch.setattr(
        rd_mod.global_token_store,
        "set_token",
        lambda app_id, tok: saved_tokens.__setitem__(app_id, tok),
    )
    monkeypatch.setattr(
        "wayland_computer_use_mcp.compositors.ensure_portal_dialogs_above", lambda: None
    )

    client = RemoteDesktopClient(MagicMock(spec=AsyncLoopThread))
    monkeypatch.setattr(client, "connect_eis_via_oeffis", lambda: None)
    _, node_id, restore_token, _ = asyncio.run(client.negotiate("test-app"))
    assert node_id == 42 and restore_token == "new_tok_123"
    select_dev = next(b[1] for i, m, b in sent_calls1 if m == "SelectDevices")
    select_src = next(b[1] for i, m, b in sent_calls1 if m == "SelectSources")
    assert select_dev["persist_mode"].value == 2
    assert "persist_mode" not in select_src and "restore_token" not in select_src

    FakeBus2, _ = _make_fake_bus(return_streams=False)

    class _BusFactory2:
        def __init__(self, **kw):
            self.bus = FakeBus2()

        async def connect(self):
            return self.bus

    deleted: list[str] = []
    saved_tokens.clear()
    monkeypatch.setattr(rd_mod, "MessageBus", _BusFactory2)
    monkeypatch.setattr(
        rd_mod.global_token_store, "delete_token", lambda a: deleted.append(a) or True
    )
    client2 = RemoteDesktopClient(MagicMock(spec=AsyncLoopThread))
    monkeypatch.setattr(client2, "connect_eis_via_oeffis", lambda: None)
    _, node_id2, restore_token2, _ = asyncio.run(client2.negotiate("test-app"))
    assert (
        node_id2 is None
        and restore_token2 is None
        and deleted == ["test-app"]
        and saved_tokens == {}
    )


def test_portal_session_close_and_frame_child_bounds(monkeypatch):
    killpg_calls: list[tuple[int, int]] = []
    monkeypatch.setattr("os.killpg", lambda pgid, sig: killpg_calls.append((pgid, sig)))
    session = PortalSession()
    session._initialized = True
    session._host.screencast.stop = MagicMock()
    session._host.rd_client.ei_client = MagicMock()
    session._host.rd_client.bus = MagicMock()
    session._blackbox.close = MagicMock()
    session.close()
    assert killpg_calls == [] and session._initialized is False

    # AT-SPI frame child bounds fallback
    s2 = PortalSession()
    s2._initialized = True
    s2.target_pid = 4321
    s2.target_title = "Wayland MCP Test Rig"
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    cfg = get_config()
    prev_mock = cfg.mock_mode
    cfg.mock_mode = False
    monkeypatch.setattr(s2, "check_active_app", lambda: None)
    monkeypatch.setattr(
        "wayland_computer_use_mcp.compositors.query_window_geometry", lambda **kw: None
    )
    monkeypatch.setattr(
        "wayland_computer_use_mcp.a11y.get_application_tree",
        lambda pid: {
            "role": "application",
            "name": "test_gui_app.py",
            "children": [
                {"role": "frame", "name": "Wayland MCP Test Rig", "bounds": [0, 0, 900, 700]}
            ],
        },
    )
    prev_dims = global_clamper.dimensions
    prev_last = (global_geometry_detector.last_dimensions, global_geometry_detector.last_position)
    try:
        global_geometry_detector.last_dimensions = None
        global_geometry_detector.last_position = None
        s2._verify_preconditions()
        assert global_clamper.dimensions == (900, 700)
    finally:
        cfg.mock_mode = prev_mock
        global_clamper.set_bounds(*prev_dims)
        global_geometry_detector.last_dimensions, global_geometry_detector.last_position = prev_last


def test_per_pid_window_session_state_isolation():
    """Switching target_pid isolates title, offsets, clamper bounds, and detector baseline."""
    global_clamper.set_bounds(1920, 1080)
    global_portal_session.target_pid = 1001
    global_portal_session.target_title = "App One"
    global_portal_session.window_offset_x = 120
    global_portal_session.window_offset_y = 60
    global_clamper.set_bounds(800, 600)
    global_geometry_detector.record_capture(800, 600, 120, 60)

    global_portal_session.target_pid = 1002
    assert global_portal_session.target_title is None
    assert global_portal_session.window_offset_x == 0
    assert global_portal_session.window_offset_y == 0
    global_portal_session.target_title = "App Two"
    global_clamper.set_bounds(500, 400)
    global_geometry_detector.record_capture(500, 400, 900, 40)

    global_portal_session.target_pid = 1001
    assert global_portal_session.target_title == "App One"
    assert (global_portal_session.window_offset_x, global_portal_session.window_offset_y) == (
        120,
        60,
    )
    assert global_clamper.dimensions == (800, 600)
    assert global_geometry_detector.last_dimensions == (800, 600)
    assert global_geometry_detector.last_position == (120, 60)

    global_portal_session.clear_pid_state(1002)
    assert global_portal_session.get_pid_state(1002) is None
