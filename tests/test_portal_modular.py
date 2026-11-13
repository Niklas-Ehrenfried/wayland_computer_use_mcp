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
    """Verifies all input gestures execute cleanly via single-source libei
    when devices are active.
    """
    mock_rd = MagicMock(spec=RemoteDesktopClient)
    mock_ei = MagicMock()

    dispatcher = InputDispatcher(
        rd_client=mock_rd,
        verify_preconditions_fn=lambda: None,
        get_offsets_fn=lambda: (0, 0),
        is_mock_fn=lambda: False,
        get_ei_client_fn=lambda: mock_ei,
    )

    # Click - single source routes to EI, not RemoteDesktop
    res = dispatcher.dispatch_click(50, 50, button="left")
    assert "Clicked left" in res
    mock_ei.pointer_motion_absolute.assert_called_with(50.0, 50.0)
    mock_ei.button_click.assert_called()
    mock_rd.notify_pointer_motion_absolute.assert_not_called()

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
    mock_ei.pointer_motion_absolute.assert_called_with(70.0, 70.0)

    # Drag
    res = dispatcher.dispatch_drag(10, 10, 80, 80)
    assert "Dragged from" in res
    mock_ei.button_down.assert_called()
    mock_ei.button_up.assert_called()

    # Scroll
    res = dispatcher.dispatch_scroll(0, 5)
    assert "Scrolled dx=0, dy=5" in res
    mock_ei.scroll.assert_called_with(0.0, 5.0)

    # Type text - fast path
    mock_ei.type_char.return_value = True
    res = dispatcher.dispatch_type_text("Hello", x=10, y=10)
    assert "Typed 5 characters" in res

    # Key combination
    res = dispatcher.dispatch_key_combination(["ctrl", "c"])
    assert "Dispatched key combination: ctrl+c" in res


def test_input_dispatcher_fallback_rd_client():
    """Verifies that actions fall back to RemoteDesktopClient when EI devices are unavailable."""
    mock_rd = MagicMock(spec=RemoteDesktopClient)
    mock_ei = MagicMock()
    mock_ei.pointer_abs_device = None
    mock_ei.pointer_device = None
    mock_ei.button_device = None
    mock_ei.scroll_device = None
    mock_ei.keyboard_device = None

    dispatcher = InputDispatcher(
        rd_client=mock_rd,
        verify_preconditions_fn=lambda: None,
        get_offsets_fn=lambda: (0, 0),
        is_mock_fn=lambda: False,
        get_ei_client_fn=lambda: mock_ei,
    )

    # Click fallback
    res = dispatcher.dispatch_click(50, 50, button="left")
    assert "Clicked left" in res
    mock_rd.notify_pointer_motion_absolute.assert_called_with(50.0, 50.0)
    mock_rd.notify_pointer_button.assert_called()

    # Hover fallback
    res = dispatcher.dispatch_hover(70, 70, duration_ms=20)
    assert "Hovered pointer" in res
    mock_rd.notify_pointer_motion_absolute.assert_called_with(70.0, 70.0)

    # Scroll fallback
    res = dispatcher.dispatch_scroll(0, 5)
    assert "Scrolled dx=0, dy=5" in res
    mock_rd.notify_pointer_axis.assert_called_with(0.0, 5.0)


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


def test_async_loop_thread():
    """Verifies AsyncLoopThread executes coroutines and returns results."""
    thread = AsyncLoopThread()

    async def sample_coro(val):
        return val * 2

    res = thread.run(sample_coro(21))
    assert res == 42


def test_remotedesktop_negotiate_persistence_on_select_devices_only(monkeypatch):
    """Persistence options must go on RemoteDesktop.SelectDevices, never ScreenCast.SelectSources.

    xdg-desktop-portal (enforced strictly on GNOME) rejects ScreenCast.SelectSources
    on a RemoteDesktop session with 'Remote desktop sessions cannot persist'.
    """
    import asyncio
    from types import SimpleNamespace

    from dbus_fast import MessageType, Variant

    from wayland_computer_use_mcp.portal import remotedesktop as rd_mod

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
                return SimpleNamespace(message_type=MessageType.ERROR, body=["no eis"], unix_fds=[])
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
                            "session_handle": Variant("s", "/org/freedesktop/portal/session/1")
                        }
                    elif msg.member == "Start":
                        results = {
                            "streams": Variant("a(ua{sv})", [(42, {})]),
                            "restore_token": Variant("s", "new_tok_123"),
                        }
                    signal = SimpleNamespace(
                        interface="org.freedesktop.portal.Request",
                        member="Response",
                        path=f"/org/freedesktop/portal/desktop/request/1_99/{token}",
                        body=[0, results],
                    )
                    for h in list(self.handlers):
                        h(signal)
            return SimpleNamespace(message_type=MessageType.METHOD_RETURN, body=[], unix_fds=[])

    class FakeMessageBus:
        def __init__(self, **kwargs):
            self.bus = FakeBus()

        async def connect(self):
            return self.bus

    monkeypatch.setattr(rd_mod, "MessageBus", FakeMessageBus)
    monkeypatch.setattr(rd_mod.global_token_store, "get_token", lambda app_id: "old_tok_999")
    saved_tokens = {}
    monkeypatch.setattr(
        rd_mod.global_token_store,
        "set_token",
        lambda app_id, tok: saved_tokens.__setitem__(app_id, tok),
    )

    client = RemoteDesktopClient(MagicMock(spec=AsyncLoopThread))
    monkeypatch.setattr(client, "connect_eis_via_oeffis", lambda: None)
    monkeypatch.setattr(
        "wayland_computer_use_mcp.compositors.ensure_portal_dialogs_above", lambda: None
    )

    session_handle, node_id, restore_token, _ = asyncio.run(client.negotiate("test-app"))

    assert session_handle == "/org/freedesktop/portal/session/1"
    assert node_id == 42
    assert restore_token == "new_tok_123"
    assert saved_tokens == {"test-app": "new_tok_123"}

    select_devices_opts = next(
        body[1]
        for iface, member, body in sent_calls
        if iface == "org.freedesktop.portal.RemoteDesktop" and member == "SelectDevices"
    )
    select_sources_opts = next(
        body[1]
        for iface, member, body in sent_calls
        if iface == "org.freedesktop.portal.ScreenCast" and member == "SelectSources"
    )

    assert select_devices_opts["persist_mode"].value == 2
    assert select_devices_opts["restore_token"].value == "old_tok_999"
    assert "persist_mode" not in select_sources_opts
    assert "restore_token" not in select_sources_opts


def test_resolve_atspi_bus_address_does_not_enable_screen_reader(monkeypatch):
    """AT-SPI discovery enables IsEnabled only — never ScreenReaderEnabled (Orca)."""
    import asyncio
    from types import SimpleNamespace

    from wayland_computer_use_mcp.a11y import client as a11y_client

    prop_sets: list[str] = []

    class FakeSessionBus:
        def disconnect(self):
            pass

        async def call(self, msg):
            if msg.interface == "org.a11y.Bus" and msg.member == "GetAddress":
                return SimpleNamespace(body=["unix:path=/tmp/fake-atspi"])
            if msg.interface == "org.freedesktop.DBus.Properties" and msg.member == "Set":
                prop_sets.append(str(msg.body[1]))
                return SimpleNamespace(body=[])
            return SimpleNamespace(body=[])

    class FakeBusFactory:
        def __init__(self, **kwargs):
            pass

        async def connect(self):
            return FakeSessionBus()

    monkeypatch.delenv("AT_SPI_BUS_ADDRESS", raising=False)
    monkeypatch.setattr(a11y_client, "_cached_bus_address", None)
    monkeypatch.setattr(a11y_client, "MessageBus", FakeBusFactory)

    addr = asyncio.run(a11y_client.resolve_atspi_bus_address(force_refresh=True))
    assert addr == "unix:path=/tmp/fake-atspi"
    assert prop_sets == ["IsEnabled"]
    assert "ScreenReaderEnabled" not in prop_sets


def test_verify_preconditions_resolves_frame_child_bounds_fallback(monkeypatch):
    """When compositor introspection returns None (e.g. GNOME), use AT-SPI frame child bounds."""
    from wayland_computer_use_mcp.config import get_config
    from wayland_computer_use_mcp.security import global_clamper, global_geometry_detector

    session = PortalSession()
    session._initialized = True
    session._is_mock = False
    session.target_pid = 4321
    session.target_title = "Wayland MCP Test Rig"

    cfg = get_config()
    prev_mock_cfg = cfg.mock_mode
    cfg.mock_mode = False

    monkeypatch.setattr(session, "check_active_app", lambda: None)
    monkeypatch.setattr(
        "wayland_computer_use_mcp.compositors.query_window_geometry", lambda **kw: None
    )
    monkeypatch.setattr(
        "wayland_computer_use_mcp.a11y.get_application_tree",
        lambda pid: {
            "role": "application",
            "name": "test_gui_app.py",
            "children": [
                {
                    "role": "frame",
                    "name": "Wayland MCP Test Rig",
                    "bounds": [0, 0, 900, 700],
                }
            ],
        },
    )

    prev_dims = global_clamper.dimensions
    prev_last_dims = global_geometry_detector.last_dimensions
    prev_last_pos = global_geometry_detector.last_position
    try:
        global_geometry_detector.last_dimensions = None
        global_geometry_detector.last_position = None
        session._verify_preconditions()
        assert (session.window_offset_x, session.window_offset_y) == (0, 0)
        assert global_clamper.dimensions == (900, 700)
    finally:
        cfg.mock_mode = prev_mock_cfg
        global_clamper.set_bounds(*prev_dims)
        global_geometry_detector.last_dimensions = prev_last_dims
        global_geometry_detector.last_position = prev_last_pos


def test_input_dispatcher_blocks_relative_only_eis_without_screencast(monkeypatch):
    """Do not click stationary cursor when EIS lacks abs pointer & ScreenCast stream."""
    import pytest

    from wayland_computer_use_mcp.libei import EIClient

    mock_rd = MagicMock(spec=RemoteDesktopClient)
    mock_rd.pipewire_node_id = None
    ei = EIClient(fd=None)
    ei.ctx = 0x1234
    ei.pointer_rel_device = 0x5678
    ei.pointer_device = 0x5678
    ei.button_device = 0x5678
    ei.button_click = MagicMock()

    dispatcher = InputDispatcher(
        rd_client=mock_rd,
        verify_preconditions_fn=lambda: None,
        get_offsets_fn=lambda: (0, 0),
        is_mock_fn=lambda: False,
        get_ei_client_fn=lambda: ei,
    )

    with pytest.raises(RuntimeError, match="relative pointer without a ScreenCast stream"):
        dispatcher.dispatch_click(100, 120)
    with pytest.raises(RuntimeError, match="relative pointer without a ScreenCast stream"):
        dispatcher.dispatch_drag(100, 120, 300, 120)

    ei.button_click.assert_not_called()
    mock_rd.notify_pointer_motion_absolute.assert_not_called()


def test_remotedesktop_negotiate_clears_streamless_token(monkeypatch):
    """When Start returns no streams, stale stored token is cleared and not saved."""
    import asyncio
    from types import SimpleNamespace

    from dbus_fast import MessageType, Variant

    from wayland_computer_use_mcp.portal import remotedesktop as rd_mod

    class FakeBus:
        def __init__(self):
            self.unique_name = ":1.88"
            self.handlers = []

        def add_message_handler(self, fn):
            self.handlers.append(fn)

        def remove_message_handler(self, fn):
            if fn in self.handlers:
                self.handlers.remove(fn)

        def disconnect(self):
            pass

        async def call(self, msg):
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
                        results = {"session_handle": Variant("s", "/org/freedesktop/portal/s/2")}
                    elif msg.member == "Start":
                        results = {"restore_token": Variant("s", "broken_streamless_tok")}
                    sig = SimpleNamespace(
                        interface="org.freedesktop.portal.Request",
                        member="Response",
                        path=f"/org/freedesktop/portal/desktop/request/1_88/{token}",
                        body=[0, results],
                    )
                    for h in list(self.handlers):
                        h(sig)
            return SimpleNamespace(message_type=MessageType.METHOD_RETURN, body=[], unix_fds=[])

    class FakeMessageBus:
        def __init__(self, **kwargs):
            self.bus = FakeBus()

        async def connect(self):
            return self.bus

    monkeypatch.setattr(rd_mod, "MessageBus", FakeMessageBus)
    monkeypatch.setattr(rd_mod.global_token_store, "get_token", lambda app_id: "stale_tok")
    deleted = []
    saved = {}
    monkeypatch.setattr(
        rd_mod.global_token_store, "delete_token", lambda app_id: deleted.append(app_id) or True
    )
    monkeypatch.setattr(
        rd_mod.global_token_store, "set_token", lambda app_id, t: saved.__setitem__(app_id, t)
    )

    client = RemoteDesktopClient(MagicMock(spec=AsyncLoopThread))
    monkeypatch.setattr(client, "connect_eis_via_oeffis", lambda: None)
    monkeypatch.setattr(
        "wayland_computer_use_mcp.compositors.ensure_portal_dialogs_above", lambda: None
    )

    _, node_id, restore_token, _ = asyncio.run(client.negotiate("test-app"))
    assert node_id is None
    assert restore_token is None
    assert deleted == ["test-app"]
    assert saved == {}


def test_invoke_node_action_descends_into_combobox_toggle_button(monkeypatch):
    """GTK4 combo box clicks should activate their child toggle button via Level 1 AT-SPI."""
    from wayland_computer_use_mcp.a11y import actions as act_mod

    combo_node = {
        "id": "c1",
        "role": "combo box",
        "name": "Development - Localhost",
        "bus_name": ":1.50",
        "path": "/org/a11y/combo",
        "center": [100, 200],
        "bounds": [10, 180, 180, 40],
        "children": [
            {
                "role": "toggle button",
                "name": "Development - Localhost",
                "bus_name": ":1.50",
                "path": "/org/a11y/toggle",
                "children": [],
            }
        ],
    }

    calls = []
    monkeypatch.setattr(act_mod, "get_cached_node", lambda pid, nid: combo_node)
    monkeypatch.setattr(
        act_mod,
        "do_accessible_action",
        lambda b, p, idx=0: calls.append((b, p, idx)) or (p == "/org/a11y/toggle"),
    )

    res = act_mod.invoke_node_action(1234, "c1", action="click")
    assert "via child toggle button via AT-SPI" in res
    assert calls == [(":1.50", "/org/a11y/toggle", 0)]


def test_invoke_node_action_combobox_item_uses_keyboard_on_gnome(monkeypatch):
    """Dropdown items with unknown window offset use arrow + Return instead of blind clicks."""
    from wayland_computer_use_mcp import compositors
    from wayland_computer_use_mcp.a11y import actions as act_mod
    from wayland_computer_use_mcp.portal import global_portal_session

    item_node = {
        "id": "lbl2",
        "role": "label",
        "name": "Staging",
        "bus_name": ":1.50",
        "path": "/org/a11y/lbl2",
        "center": [90, 250],
        "bounds": [20, 235, 140, 30],
        "children": [],
        "ancestors": [
            {
                "role": "list item",
                "name": "Staging",
                "bus_name": ":1.50",
                "path": "/org/a11y/li1",
                "index_in_parent": 1,
            },
            {
                "role": "list",
                "name": "",
                "bus_name": ":1.50",
                "path": "/org/a11y/list",
                "index_in_parent": 0,
            },
            {
                "role": "combo box",
                "name": "Development - Localhost",
                "bus_name": ":1.50",
                "path": "/org/a11y/combo",
                "index_in_parent": 2,
            },
        ],
    }

    keys: list[list[str]] = []
    monkeypatch.setattr(act_mod, "get_cached_node", lambda pid, nid: item_node)
    monkeypatch.setattr(act_mod, "do_accessible_action", lambda b, p, idx=0: False)
    monkeypatch.setattr(act_mod, "_run_async_sync", lambda fn, *a, **kw: 0)
    monkeypatch.setattr(compositors, "query_window_geometry", lambda pid=0, title="": None)
    monkeypatch.setattr(
        global_portal_session, "dispatch_key_combination", lambda combo: keys.append(combo) or "ok"
    )

    monkeypatch.setattr(type(global_portal_session), "is_mock", property(lambda self: False))
    res = act_mod.invoke_node_action(1234, "lbl2", action="click")

    assert "via keyboard navigation" in res
    assert keys == [["down"], ["return"]]


def test_invoke_node_action_focused_checkbox_uses_space_on_gnome(monkeypatch):
    """Focused GTK4 check boxes with NActions=0 toggle via spacebar when offset is unknown."""
    from wayland_computer_use_mcp import compositors
    from wayland_computer_use_mcp.a11y import actions as act_mod
    from wayland_computer_use_mcp.portal import global_portal_session

    chk_node = {
        "id": "k1",
        "role": "check box",
        "name": "Enable Diagnostics",
        "bus_name": ":1.50",
        "path": "/org/a11y/k1",
        "center": [50, 90],
        "bounds": [20, 75, 160, 30],
        "states": ["enabled", "sensitive", "visible", "showing", "focusable", "focused"],
        "children": [],
        "ancestors": [],
    }

    keys: list[list[str]] = []
    monkeypatch.setattr(act_mod, "get_cached_node", lambda pid, nid: chk_node)
    monkeypatch.setattr(act_mod, "do_accessible_action", lambda b, p, idx=0: False)
    monkeypatch.setattr(compositors, "query_window_geometry", lambda pid=0, title="": None)
    monkeypatch.setattr(
        global_portal_session, "dispatch_key_combination", lambda combo: keys.append(combo) or "ok"
    )

    monkeypatch.setattr(type(global_portal_session), "is_mock", property(lambda self: False))
    res = act_mod.invoke_node_action(1234, "k1", action="click")

    assert "via spacebar" in res
    assert keys == [["space"]]
