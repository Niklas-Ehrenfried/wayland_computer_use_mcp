"""Unit tests for AT-SPI2 bus discovery, Cache.GetItems bulk fetch, pruning, and Level 1 actions."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from dbus_fast import MessageType

from wayland_computer_use_mcp import a11y
from wayland_computer_use_mcp.a11y import (
    AtspiInspector,
    flatten_tree,
    get_application_tree,
    invoke_node_action,
)
from wayland_computer_use_mcp.a11y import actions as act_mod
from wayland_computer_use_mcp.a11y import client as a11y_client
from wayland_computer_use_mcp.a11y.actions import (
    _verify_cached_node_identity,
    do_accessible_action,
    do_accessible_set_text,
    do_accessible_text_action,
    perform_accessible_action,
    perform_accessible_set_text,
    perform_accessible_text_action,
)
from wayland_computer_use_mcp.a11y.tree import (
    _node_cache,
    extract_active_window_node,
    get_tree_generation,
)
from wayland_computer_use_mcp.config import get_config
from wayland_computer_use_mcp.portal import global_portal_session
from wayland_computer_use_mcp.security import global_clamper


def test_tree_pruning_invisible_and_separators():
    inspector = AtspiInspector()
    raw_tree = {
        "role": "application",
        "name": "TestApp",
        "states": ["visible", "enabled"],
        "bounds": [0, 0, 800, 600],
        "children": [
            {
                "role": "push button",
                "name": "Hidden Button",
                "states": ["enabled"],
                "bounds": [10, 10, 50, 20],
                "children": [],
            },
            {
                "role": "separator",
                "name": "",
                "states": ["visible"],
                "bounds": [0, 30, 800, 2],
                "children": [],
            },
            {
                "role": "panel",
                "name": "",
                "states": ["visible"],
                "bounds": [0, 40, 100, 100],
                "children": [],
            },
            {
                "role": "push button",
                "name": "OK",
                "states": ["visible", "enabled", "focused"],
                "bounds": [50, 50, 100, 40],
                "children": [],
            },
        ],
    }
    pruned = inspector._prune_tree(raw_tree)
    assert pruned is not None
    assert len(pruned["children"]) == 1
    assert pruned["children"][0]["name"] == "OK" and pruned["children"][0]["role"] == "push button"


def test_synthetic_tree_fallback():
    tree = get_application_tree(pid=12345, max_depth=3)
    assert tree["role"] == "application"
    assert ("PID 12345" in tree["name"] or "App-12345" in tree["name"]) and len(
        tree["children"]
    ) > 0


@pytest.mark.asyncio
async def test_perform_accessible_action_mocked():
    mock_bus = AsyncMock()
    mock_bus.call.return_value = MagicMock(body=[True])
    mock_bus.disconnect = MagicMock()

    with patch("wayland_computer_use_mcp.a11y.actions.AtspiInspector") as mock_insp_cls:
        insp = mock_insp_cls.return_value
        insp.connect = AsyncMock(return_value=True)
        insp.close = AsyncMock()
        insp.bus = mock_bus

        assert await perform_accessible_action(":1.10", "/node/1", 0) is True
        mock_bus.call.assert_called()
        assert await perform_accessible_set_text(":1.10", "/node/1", "New Text") is True
        assert do_accessible_action(":1.10", "/node/1", 0) is True
        assert do_accessible_set_text(":1.10", "/node/1", "New Text") is True


@pytest.mark.asyncio
async def test_perform_accessible_text_actions():
    mock_bus = AsyncMock()
    mock_bus.call.return_value = MagicMock(body=[True])
    mock_bus.disconnect = MagicMock()

    with patch("wayland_computer_use_mcp.a11y.actions.AtspiInspector") as mock_insp_cls:
        insp = mock_insp_cls.return_value
        insp.connect = AsyncMock(return_value=True)
        insp.close = AsyncMock()
        insp.bus = mock_bus

        for act in ("copy", "cut", "paste", "select"):
            assert await perform_accessible_text_action(":1.10", "/node/1", act, 0, 5) is True
        assert do_accessible_text_action(":1.10", "/node/1", "copy") is True


def test_accessible_action_failures():
    assert do_accessible_action("invalid.bus", "/invalid", 0) is False
    assert do_accessible_set_text("invalid.bus", "/invalid", "test") is False
    assert do_accessible_text_action("invalid.bus", "/invalid", "unknown") is False


@pytest.mark.asyncio
async def test_real_mode_empty_tree_no_synthetic_and_cycle_guard(monkeypatch):
    monkeypatch.setattr(get_config(), "mock_mode", False)
    insp = AtspiInspector()
    monkeypatch.setattr(insp, "connect", AsyncMock(return_value=False))
    empty = await insp.get_application_tree(pid=54321, max_depth=3)
    assert empty["atspi_available"] is False and empty["children"] == []

    insp2 = AtspiInspector()
    mock_bus = AsyncMock()
    insp2.bus = mock_bus

    async def fake_call(msg):
        if msg.member == "Get":
            return MagicMock(body=["Root"])
        if msg.member == "GetRoleName":
            return MagicMock(body=["panel"])
        if msg.member == "GetState":
            return MagicMock(body=[[1 << 25, 0]])
        if msg.member == "GetExtents":
            return MagicMock(body=[[0, 0, 100, 100]])
        if msg.member == "GetChildren":
            return MagicMock(body=[[(":1.1", "/root")]])
        return MagicMock(body=[])

    mock_bus.call = fake_call
    tree = await insp2._traverse_node(":1.1", "/root", current_depth=0, max_depth=5)
    assert tree["name"] == "Root" and tree["children"] == []


@pytest.mark.asyncio
async def test_fetch_tree_from_cache_complete_incomplete_unknown_role_and_cycle():
    """Covers Cache.GetItems fast-path, incomplete fallback, unknown role, and Value batching."""
    insp = AtspiInspector()
    insp.bus = MagicMock()

    items = [
        (
            (":1.2", "/org/a11y/atspi/accessible/root"),
            (":1.2", "/app"),
            (":1.2", "/org/a11y/atspi/null"),
            0,
            1,
            ["org.a11y.atspi.Accessible"],
            "DemoApp",
            4,
            "application",
            [0, 0],
        ),
        (
            (":1.2", "/org/a11y/atspi/accessible/frame1"),
            (":1.2", "/app"),
            (":1.2", "/org/a11y/atspi/accessible/root"),
            0,
            2,
            ["org.a11y.atspi.Accessible", "org.a11y.atspi.Component"],
            "Main Window",
            22,
            "frame",
            [0, 0],
        ),
        (
            (":1.2", "/org/a11y/atspi/accessible/btn1"),
            (":1.2", "/app"),
            (":1.2", "/org/a11y/atspi/accessible/frame1"),
            0,
            0,
            ["org.a11y.atspi.Accessible", "org.a11y.atspi.Action", "org.a11y.atspi.Component"],
            "Submit",
            43,
            "push button",
            [0, 0],
        ),
        # Unknown role ID 9999 with Value interface + self-parent loop safety on unrelated node
        (
            (":1.2", "/org/a11y/atspi/accessible/slider1"),
            (":1.2", "/app"),
            (":1.2", "/org/a11y/atspi/accessible/frame1"),
            1,
            0,
            ["org.a11y.atspi.Accessible", "org.a11y.atspi.Value", "org.a11y.atspi.Component"],
            "CustomSlider",
            9999,
            "slider",
            [0, 0],
        ),
    ]

    async def fake_call_complete(msg):
        if msg.member == "GetItems":
            return MagicMock(message_type=MessageType.METHOD_RETURN, body=[items])
        if msg.member == "GetRoleName":
            return MagicMock(message_type=MessageType.METHOD_RETURN, body=["slider"])
        if msg.member == "GetExtents":
            return MagicMock(message_type=MessageType.METHOD_RETURN, body=[[10, 20, 120, 40]])
        if msg.member == "GetCurrentValue":
            return MagicMock(message_type=MessageType.METHOD_RETURN, body=[42.5])
        return MagicMock(message_type=MessageType.METHOD_RETURN, body=[])

    insp.bus.call = fake_call_complete
    tree = await insp._fetch_tree_from_cache(":1.2", "/org/a11y/atspi/accessible/root", max_depth=8)
    assert tree is not None and tree["name"] == "DemoApp"
    btn_node, slider_node = tree["children"][0]["children"]
    assert btn_node["name"] == "Submit" and btn_node["role"] == "push button"
    assert btn_node["bounds"] == [10, 20, 120, 40]
    assert slider_node["role"] == "slider" and slider_node["value"] == 42.5

    # Incomplete lazy cache -> returns None
    inc = list(items)
    inc[1] = (
        inc[1][0],
        inc[1][1],
        inc[1][2],
        0,
        5,
        inc[1][5],
        inc[1][6],
        inc[1][7],
        inc[1][8],
        inc[1][9],
    )

    async def fake_inc(msg):
        return MagicMock(message_type=MessageType.METHOD_RETURN, body=[inc])

    insp.bus.call = fake_inc
    assert (
        await insp._fetch_tree_from_cache(":1.2", "/org/a11y/atspi/accessible/root", max_depth=8)
        is None
    )

    async def fake_err(msg):
        return MagicMock(message_type=MessageType.ERROR, body=["failed"])

    insp.bus.call = fake_err
    assert (
        await insp._fetch_tree_from_cache(":1.2", "/org/a11y/atspi/accessible/root", max_depth=8)
        is None
    )


def test_tree_generation_and_extract_active_window_node():
    g0 = get_tree_generation(777)
    t1 = a11y.get_application_tree(777)
    g1 = get_tree_generation(777)
    assert g1 == g0 + 1 and t1["tree_generation"] == g1
    assert a11y.get_application_tree(777)["tree_generation"] == g1 + 1

    multi = {
        "role": "application",
        "name": "App",
        "children": [
            {
                "role": "frame",
                "name": "Background Window",
                "states": ["showing", "visible"],
                "bounds": [0, 0, 800, 600],
            },
            {
                "role": "dialog",
                "name": "Preferences",
                "states": ["active", "showing", "visible"],
                "bounds": [100, 100, 400, 300],
            },
        ],
    }
    win = extract_active_window_node(multi)
    assert win is not None and win["role"] == "dialog" and win["name"] == "Preferences"


def test_verify_cached_node_identity_rebinds_or_rejects(monkeypatch):
    monkeypatch.setattr(get_config(), "mock_mode", False)
    cached = {
        "id": "b1",
        "name": "Save",
        "role": "push button",
        "bus_name": ":1.5",
        "path": "/node/1",
    }

    monkeypatch.setattr(
        "wayland_computer_use_mcp.a11y.actions._run_async_sync", lambda fn, b, p: "Save"
    )
    assert _verify_cached_node_identity(4242, "b1", cached) is cached

    monkeypatch.setattr(
        "wayland_computer_use_mcp.a11y.actions._run_async_sync", lambda fn, b, p: "Cancel"
    )
    rebound = {
        "id": "b2",
        "name": "Save",
        "role": "push button",
        "bus_name": ":1.5",
        "path": "/node/2",
    }

    def fake_refresh(pid, max_depth=24):
        _node_cache[pid] = {"b2": rebound}
        return {"children": [], "tree_generation": 2}

    monkeypatch.setattr("wayland_computer_use_mcp.a11y.actions.get_application_tree", fake_refresh)
    assert _verify_cached_node_identity(4242, "b1", cached) == rebound

    def fake_empty(pid, max_depth=24):
        _node_cache[pid] = {}
        return {"children": [], "tree_generation": 3}

    monkeypatch.setattr("wayland_computer_use_mcp.a11y.actions.get_application_tree", fake_empty)
    with pytest.raises(ValueError, match="Stale node ID 'b1'"):
        _verify_cached_node_identity(4242, "b1", cached)


def test_atspi_pool_and_zero_cursor_hijack_and_bus_discovery(monkeypatch):
    async def _identity(v: str) -> str:
        return v

    a11y_client.close_atspi_pool()
    assert a11y_client.run_atspi_sync(_identity, "pooled-ok") == "pooled-ok"
    a11y_client.close_atspi_pool()

    orig_dims = global_clamper.dimensions
    global_clamper.set_bounds(800, 600)
    try:
        tree = {
            "role": "application",
            "name": "OffscreenDemo",
            "bounds": [0, 0, 800, 600],
            "children": [
                {
                    "role": "push button",
                    "name": "Far Button",
                    "states": ["visible", "enabled"],
                    "bounds": [50, 2000, 120, 40],
                    "bus_name": ":1.99",
                    "path": "/a11y/42",
                    "children": [],
                }
            ],
        }
        flatten_tree(tree, pid=4242)
        calls: list = []
        monkeypatch.setattr(
            global_portal_session, "dispatch_hover", lambda *a, **k: calls.append("hover")
        )
        monkeypatch.setattr(
            global_portal_session, "dispatch_scroll", lambda *a, **k: calls.append("scroll")
        )
        monkeypatch.setattr(
            global_portal_session, "dispatch_click", lambda *a, **k: calls.append("click")
        )
        monkeypatch.setattr(
            "wayland_computer_use_mcp.a11y.actions.do_accessible_action", lambda *a, **k: True
        )
        assert "via AT-SPI" in invoke_node_action(4242, "b1", action="click")
        assert calls == []
    finally:
        global_clamper.set_bounds(*orig_dims)

    # resolve_atspi_bus_address enables IsEnabled only (never ScreenReaderEnabled)
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

    class FakeFactory:
        def __init__(self, **kwargs):
            pass

        async def connect(self):
            return FakeSessionBus()

    monkeypatch.delenv("AT_SPI_BUS_ADDRESS", raising=False)
    monkeypatch.setattr(a11y_client, "_cached_bus_address", None)
    monkeypatch.setattr(a11y_client, "MessageBus", FakeFactory)
    assert (
        asyncio.run(a11y_client.resolve_atspi_bus_address(force_refresh=True))
        == "unix:path=/tmp/fake-atspi"
    )
    assert prop_sets == ["IsEnabled"]


def test_gnome_combobox_and_checkbox_action_fallbacks(monkeypatch):
    combo_node = {
        "id": "c1",
        "role": "combo box",
        "name": "Dev",
        "bus_name": ":1.50",
        "path": "/org/a11y/combo",
        "center": [100, 200],
        "bounds": [10, 180, 180, 40],
        "children": [
            {
                "role": "toggle button",
                "name": "Dev",
                "bus_name": ":1.50",
                "path": "/org/a11y/toggle",
                "children": [],
            }
        ],
    }
    monkeypatch.setattr(act_mod, "get_cached_node", lambda pid, nid: combo_node)
    monkeypatch.setattr(
        act_mod, "do_accessible_action", lambda b, p, idx=0: p == "/org/a11y/toggle"
    )
    assert "via child toggle button via AT-SPI" in act_mod.invoke_node_action(
        1234, "c1", action="click"
    )

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
                "path": "/li1",
                "index_in_parent": 1,
            },
            {
                "role": "list",
                "name": "",
                "bus_name": ":1.50",
                "path": "/list",
                "index_in_parent": 0,
            },
            {
                "role": "combo box",
                "name": "Dev",
                "bus_name": ":1.50",
                "path": "/combo",
                "index_in_parent": 2,
            },
        ],
    }
    keys: list[list[str]] = []
    monkeypatch.setattr(act_mod, "get_cached_node", lambda pid, nid: item_node)
    monkeypatch.setattr(act_mod, "do_accessible_action", lambda *a, **kw: False)
    monkeypatch.setattr(act_mod, "_run_async_sync", lambda fn, *a, **kw: 0)
    monkeypatch.setattr(
        "wayland_computer_use_mcp.compositors.query_window_geometry", lambda pid=0, title="": None
    )
    monkeypatch.setattr(
        global_portal_session, "dispatch_key_combination", lambda combo: keys.append(combo) or "ok"
    )
    monkeypatch.setattr(type(global_portal_session), "is_mock", property(lambda self: False))
    assert "via keyboard navigation" in act_mod.invoke_node_action(1234, "lbl2", action="click")
    assert keys == [["down"], ["return"]]

    keys.clear()
    chk_node = {
        "id": "k1",
        "role": "check box",
        "name": "Enable",
        "bus_name": ":1.50",
        "path": "/k1",
        "center": [50, 90],
        "bounds": [20, 75, 160, 30],
        "states": ["enabled", "visible", "showing", "focused"],
        "children": [],
        "ancestors": [],
    }
    monkeypatch.setattr(act_mod, "get_cached_node", lambda pid, nid: chk_node)
    assert "via spacebar" in act_mod.invoke_node_action(1234, "k1", action="click")
    assert keys == [["space"]]


async def test_get_application_tree_matches_descendant_pid(monkeypatch):
    """Wrapper launchers match AT-SPI applications registered under a child PID."""
    from dbus_fast import MessageType

    from wayland_computer_use_mcp.a11y.client import AtspiInspector
    from wayland_computer_use_mcp.config import get_config

    monkeypatch.setattr(get_config(), "mock_mode", False)
    inspector = AtspiInspector(bus_address="unix:path=/tmp/fake-atspi")
    calls = []

    class FakeBus:
        async def call(self, msg):
            calls.append((msg.destination, msg.member, msg.body))
            reply = MagicMock()
            reply.message_type = MessageType.METHOD_RETURN
            if msg.member == "GetChildren":
                reply.body = [[[":1.77", "/org/a11y/atspi/accessible/root"]]]
            elif msg.member == "GetConnectionUnixProcessID":
                reply.body = [99002]
            else:
                reply.body = []
            return reply

    inspector.bus = FakeBus()  # type: ignore[assignment]
    monkeypatch.setattr(inspector, "connect", lambda: _async_return(True))
    monkeypatch.setattr(inspector, "close", lambda: _async_return(None))
    monkeypatch.setattr(
        "wayland_computer_use_mcp.a11y.client._collect_process_tree_pids",
        lambda pid: {99001, 99002} if pid == 99001 else {pid},
    )
    monkeypatch.setattr(
        inspector,
        "_fetch_tree_from_cache",
        lambda bus_name, root_path, max_depth: _async_return(
            {
                "role": "application",
                "name": "WrappedApp",
                "children": [{"role": "frame", "name": "W", "children": []}],
            }
        ),
    )

    tree = await inspector.get_application_tree(99001)
    assert tree["name"] == "WrappedApp"


async def _async_return(val):
    return val
