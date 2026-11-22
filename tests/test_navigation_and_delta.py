"""Unit tests for semantic UI navigation, delta computation, event settlement, and batching."""

from __future__ import annotations

import collections
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image

from wayland_computer_use_mcp.a11y import (
    clear_node_cache,
    flatten_tree,
    get_cached_node,
    invoke_node_action,
)
from wayland_computer_use_mcp.a11y.events import AtspiEventListener
from wayland_computer_use_mcp.config import get_config
from wayland_computer_use_mcp.delta import (
    compute_ui_delta,
    format_delta_markdown,
    snapshot_interactive_state,
    wrap_with_delta,
)
from wayland_computer_use_mcp.portal import global_portal_session
from wayland_computer_use_mcp.process import (
    _logs,
    _processes,
    check_process_health_and_enrich,
    get_recent_traceback,
)
from wayland_computer_use_mcp.server import (
    batch_actions,
    drag,
    inspect_ui_tree,
    interact_with_node,
    scroll,
    watch_ui_events,
)
from wayland_computer_use_mcp.tools.navigation_tools import wait_for_element


@pytest.fixture(autouse=True)
def setup_mock(monkeypatch):
    monkeypatch.setattr(get_config(), "mock_mode", True)
    clear_node_cache()


def test_flatten_tree_compact_ids():
    tree = {
        "role": "application",
        "name": "DemoApp",
        "bounds": [0, 0, 800, 600],
        "children": [
            {
                "role": "push button",
                "name": "Submit",
                "states": ["visible", "enabled"],
                "bounds": [50, 100, 120, 40],
                "children": [],
            },
            {
                "role": "push button",
                "name": "Cancel",
                "states": ["visible", "enabled"],
                "bounds": [180, 100, 100, 40],
                "children": [],
            },
            {
                "role": "entry",
                "name": "Username",
                "states": ["visible", "enabled", "focused"],
                "bounds": [50, 200, 200, 32],
                "children": [],
            },
            {
                "role": "combo box",
                "name": "Theme",
                "states": ["visible", "enabled"],
                "bounds": [50, 300, 150, 36],
                "children": [],
            },
            {
                "role": "scale",
                "name": "Brightness",
                "states": ["visible", "enabled"],
                "bounds": [50, 400, 300, 30],
                "children": [],
            },
            {
                "role": "page tab",
                "name": "General",
                "states": ["visible", "enabled"],
                "bounds": [10, 10, 80, 30],
                "children": [],
            },
            {
                "role": "label",
                "name": "Status: Ready",
                "states": ["visible"],
                "bounds": [50, 500, 200, 20],
                "children": [],
            },
        ],
    }

    elements = flatten_tree(tree, pid=9999)
    assert [e["id"] for e in elements] == ["b1", "b2", "e1", "c1", "s1", "t1", "lbl1"]

    b1 = elements[0]
    assert b1["name"] == "Submit"
    assert b1["center"] == [110, 120]
    assert b1["enabled"] is True

    cached = get_cached_node(9999, "b1")
    assert cached is not None and cached["name"] == "Submit"


def test_invoke_node_action_fallback():
    tree = {
        "role": "application",
        "name": "TestApp",
        "bounds": [0, 0, 500, 500],
        "children": [
            {
                "role": "push button",
                "name": "Clickable Button",
                "states": ["visible", "enabled"],
                "bounds": [100, 100, 100, 50],
                "children": [],
            }
        ],
    }
    flatten_tree(tree, pid=8888)
    res = invoke_node_action(8888, "b1", action="click")
    assert "b1" in res
    assert "Clickable Button" in res
    assert "Fallback Level 2" in res or "Activated" in res


def test_interact_with_node_tool_and_typing_slider():
    tree = inspect_ui_tree(pid=12345)
    assert len(tree["interactive_elements"]) > 0
    first_id = tree["interactive_elements"][0]["id"]
    assert first_id in interact_with_node(node_id=first_id, pid=12345)

    custom = {
        "role": "application",
        "name": "ControlsApp",
        "bounds": [0, 0, 800, 600],
        "children": [
            {
                "role": "entry",
                "name": "Search Field",
                "states": ["visible", "enabled"],
                "bounds": [50, 50, 200, 30],
                "children": [],
            },
            {
                "role": "scale",
                "name": "Volume Slider",
                "states": ["visible", "enabled"],
                "bounds": [50, 100, 250, 30],
                "children": [],
            },
            {
                "role": "check box",
                "name": "Dark Mode",
                "states": ["visible", "enabled"],
                "bounds": [50, 150, 150, 30],
                "children": [],
            },
        ],
    }
    flatten_tree(custom, pid=5555)
    assert "Typed" in interact_with_node(
        node_id="e1", action="type", text="Antigravity Test", pid=5555
    )
    slider_res = interact_with_node(node_id="s1", action="click", pid=5555)
    assert "s1" in slider_res and ("Slider" in slider_res or "scale" in slider_res)
    lbl_res = interact_with_node(target="Hardware Acceleration", action="click", pid=5555)
    assert "k1" in lbl_res or "Clicked" in lbl_res


def test_batch_actions_success_fail_fast_and_recovery():
    actions = [
        {"action": "click", "x": 50, "y": 50},
        {"action": "type", "text": "Batch text", "x": 50, "y": 50},
        {"action": "key", "keys": ["ctrl", "c"]},
        {"action": "wait", "ms": 10},
    ]
    res = batch_actions(actions, pid=12345)
    assert res["status"] == "success"
    assert res["total_steps"] == 4
    assert res["executed_steps_count"] == 4

    fail_res = batch_actions(
        [
            {"action": "click", "x": 50, "y": 50},
            {"action": "interact", "node_id": "invalid_nonexistent_node"},
            {"action": "type", "text": "Should not execute"},
        ],
        pid=12345,
    )
    assert fail_res["status"] == "failed"
    assert fail_res["failed_step"] == 2
    assert fail_res["total_steps"] == 3
    assert len(fail_res["executed_steps"]) == 1
    assert fail_res["error_code"] == "element_not_found"

    # Empty batch edge case
    empty_res = batch_actions([], pid=12345)
    assert empty_res["status"] == "success"
    assert empty_res["executed_steps_count"] == 0

    # Permission denied & invalid step & timeout classifications
    perm_fail = batch_actions(
        actions=[{"action": "key_combination", "keys": ["super", "l"]}], pid=123
    )
    assert perm_fail["error_code"] == "permission_denied"
    assert "policy" in perm_fail["recovery_hint"].lower()

    invalid_fail = batch_actions(actions=[{"action": "click"}], pid=123)
    assert invalid_fail["error_code"] == "invalid_step"

    timeout_fail = batch_actions(
        actions=[
            {
                "action": "wait_for",
                "target": "DefinitelyAbsentWidget999",
                "state": "present",
                "timeout_ms": 40,
            }
        ],
        pid=12345,
    )
    assert timeout_fail["status"] == "failed"
    assert timeout_fail["error_code"] == "timeout"


def test_crash_to_context_enrichment():
    fake_pid = 77777
    _logs[fake_pid] = collections.deque(
        [
            "INFO: Initializing",
            "Traceback (most recent call last):",
            '  File "app.py", line 42, in on_clicked',
            "    raise ValueError('Sample Crash')",
            "ValueError: Sample Crash",
        ],
        maxlen=200,
    )
    tb = get_recent_traceback(fake_pid)
    assert tb is not None and "ValueError: Sample Crash" in tb

    class MockProc:
        def poll(self):
            return None

    _processes[fake_pid] = MockProc()
    msg = check_process_health_and_enrich(fake_pid, "Button clicked successfully")
    assert "Application emitted a Python exception to stderr" in msg
    assert "ValueError: Sample Crash" in msg

    class DeadProc:
        def poll(self):
            return 1

    _processes[fake_pid] = DeadProc()
    with pytest.raises(RuntimeError, match="crashed!\n\nTraceback:"):
        check_process_health_and_enrich(fake_pid, "Action completed")

    _processes.pop(fake_pid, None)
    _logs.pop(fake_pid, None)


def test_compute_ui_delta_and_markdown_edge_cases(monkeypatch):
    identical = {
        "b1": {"id": "b1", "role": "button", "name": "Save", "value": None, "states": {"enabled"}}
    }
    no_diff = compute_ui_delta(identical, identical)
    assert not no_diff["modified"] and not no_diff["appeared"] and not no_diff["hidden"]
    assert "None (no visible state or text change detected)" in format_delta_markdown(no_diff)

    before = {
        "b1": {
            "id": "b1",
            "name": "Click Me! (0 clicks)",
            "role": "button",
            "value": 0,
            "states": {"enabled", "visible"},
        },
        "e1": {"id": "e1", "name": "", "role": "entry", "value": "", "states": {"enabled"}},
        "k1": {"id": "k1", "name": "Accel", "role": "check box", "states": {"enabled", "checked"}},
        "t1": {"id": "t1", "name": "Pending", "role": "label", "states": {"showing"}},
    }
    after = {
        "b1": {
            "id": "b1",
            "name": "Clicked! (1 clicks)",
            "role": "button",
            "value": 1,
            "states": {"enabled", "visible", "focused"},
        },
        "e1": {
            "id": "e1",
            "name": "",
            "role": "entry",
            "value": "New text",
            "states": {"enabled"},
        },
        "k1": {"id": "k1", "name": "Accel", "role": "check box", "states": {"enabled"}},
        "t1": {"id": "t1", "name": "Pending", "role": "label", "states": set()},
        "b2": {"id": "b2", "name": "Submit", "role": "button", "states": {"enabled"}},
    }
    diff = compute_ui_delta(before, after)
    assert len(diff["modified"]) == 3
    assert len(diff["appeared"]) == 1
    assert len(diff["hidden"]) == 1
    md = format_delta_markdown(diff)
    assert "b1 [button]: text 'Click Me! (0 clicks)' ➔ 'Clicked! (1 clicks)'" in md
    assert "value 0 ➔ 1" in md
    assert "e1 [entry]: value '' ➔ 'New text'" in md
    assert "-checked" in md
    assert "Appeared: b2" in md
    assert "Hidden: t1" in md

    overflow_md = format_delta_markdown(
        {
            "modified": [],
            "appeared": [{"id": f"n{i}", "name": f"Node{i}"} for i in range(10)],
            "hidden": [{"id": f"h{i}", "name": f"Hidden{i}"} for i in range(8)],
        },
        max_items=5,
    )
    assert "+5 more" in overflow_md and "+3 more" in overflow_md

    assert wrap_with_delta(pid=0, action_fn=lambda: "Direct result") == "Direct result"
    monkeypatch.setattr(
        "wayland_computer_use_mcp.a11y.get_application_tree",
        lambda pid: (_ for _ in ()).throw(RuntimeError("D-Bus fault")),
    )
    assert snapshot_interactive_state(1234) == {}


def test_all_9_actionable_widgets_offline_suite():
    synthetic_tree = {
        "role": "application",
        "name": "FullCoverageApp",
        "bounds": [0, 0, 800, 600],
        "children": [
            {
                "role": "page tab",
                "name": "Controls & Inputs",
                "states": ["visible", "enabled"],
                "bounds": [10, 10, 80, 30],
                "children": [],
            },
            {
                "role": "page tab",
                "name": "Navigation & Pages",
                "states": ["visible", "enabled"],
                "bounds": [95, 10, 80, 30],
                "children": [],
            },
            {
                "role": "push button",
                "name": "Click Me! (0 clicks)",
                "states": ["visible", "enabled"],
                "bounds": [50, 60, 120, 35],
                "children": [],
            },
            {
                "role": "entry",
                "name": "Text Entry",
                "states": ["visible", "enabled"],
                "bounds": [50, 110, 200, 30],
                "children": [],
            },
            {
                "role": "combo box",
                "name": "Development - Localhost",
                "states": ["visible", "enabled"],
                "bounds": [50, 160, 200, 32],
                "children": [],
            },
            {
                "role": "scale",
                "name": "Slider",
                "states": ["visible", "enabled"],
                "value": 25.0,
                "bounds": [50, 210, 300, 30],
                "children": [],
            },
            {
                "role": "check box",
                "name": "Hardware Acceleration",
                "states": ["visible", "enabled", "checked"],
                "bounds": [50, 260, 180, 30],
                "children": [],
            },
        ],
    }
    flatten_tree(synthetic_tree, pid=4444)

    assert "b1" in interact_with_node(node_id="b1", action="click", pid=4444)
    assert "Typed" in interact_with_node(
        node_id="e1", action="type", text="Hello Wayland", pid=4444
    )
    assert "c1" in interact_with_node(node_id="c1", action="click", pid=4444)
    assert "s1" in interact_with_node(node_id="s1", action="click", pid=4444)
    assert "k1" in interact_with_node(node_id="k1", action="click", pid=4444)
    assert "t2" in interact_with_node(node_id="t2", action="click", pid=4444)
    assert "Scrolled" in scroll(0, -100)
    assert "Dragged" in drag(50, 220, 250, 220)


def test_interact_with_node_text_actions_and_typing_thresholds():
    tree = {
        "role": "application",
        "name": "DemoApp",
        "bounds": [0, 0, 800, 600],
        "children": [
            {
                "role": "entry",
                "name": "Search Query",
                "states": ["visible", "enabled"],
                "bounds": [10, 20, 300, 30],
                "children": [],
            }
        ],
    }
    flatten_tree(tree, pid=0)
    assert "Typed" in interact_with_node(target="e1", action="type", text="Antigravity Test")
    assert "Selected" in interact_with_node(target="e1", action="select_all")
    sel_rng = interact_with_node(target="e1", action="select_range", start_offset=0, end_offset=5)
    assert "Selected" in sel_rng or "drag" in sel_rng.lower()
    assert "Copied" in interact_with_node(target="e1", action="copy")
    clr = interact_with_node(target="e1", action="clear")
    assert "clear" in clr.lower()
    assert "Pasted" in interact_with_node(target="e1", action="paste")
    assert "Cut" in interact_with_node(target="e1", action="cut")
    assert "Drag-selected" in interact_with_node(target="e1", action="drag_select")

    assert "Typed" in global_portal_session.dispatch_type_text("Short text")
    res_long = global_portal_session.dispatch_type_text(
        "This is an intentionally very long text string that exceeds 30 characters"
    )
    assert "Pasted" in res_long or "Typed" in res_long


def test_gtk4_sensitive_state_compact_hierarchy_and_filters(monkeypatch):
    sample_raw_tree = {
        "role": "application",
        "name": "Gtk4App",
        "bus_name": ":1.42",
        "path": "/org/a11y/atspi/accessible/root",
        "states": [],
        "bounds": [0, 0, 0, 0],
        "children": [
            {
                "role": "frame",
                "name": "Main Window",
                "bus_name": ":1.42",
                "path": "/org/a11y/atspi/accessible/1",
                "states": ["visible", "showing", "active"],
                "bounds": [0, 0, 720, 640],
                "children": [
                    {
                        "role": "generic",
                        "name": "",
                        "bus_name": ":1.42",
                        "path": "/org/a11y/atspi/accessible/2",
                        "states": ["visible", "showing"],
                        "bounds": [0, 0, 720, 640],
                        "children": [
                            {
                                "role": "push button",
                                "name": "Submit",
                                "bus_name": ":1.42",
                                "path": "/org/a11y/atspi/accessible/3",
                                "states": ["visible", "showing", "sensitive"],
                                "bounds": [20, 20, 100, 36],
                                "children": [
                                    {
                                        "role": "label",
                                        "name": "Submit",
                                        "states": ["visible", "showing"],
                                        "bounds": [25, 25, 90, 20],
                                        "children": [],
                                    }
                                ],
                            }
                        ],
                    },
                    {
                        "role": "tab panel",
                        "name": "",
                        "states": [],
                        "bounds": [0, 0, -394143696, 32698],
                        "children": [],
                    },
                ],
            }
        ],
    }

    monkeypatch.setattr(
        "wayland_computer_use_mcp.tools.navigation_tools.get_application_tree",
        lambda pid, max_depth=24: {
            **sample_raw_tree,
            "interactive_elements": flatten_tree(sample_raw_tree, pid=pid),
        },
    )

    compact_res = inspect_ui_tree(pid=4321)
    assert "children" not in compact_res
    assert "bus_name" not in compact_res
    btn = compact_res["interactive_elements"][0]
    assert btn["id"] == "b1" and btn["enabled"] is True and "enabled" in btn["states"]
    assert "-394143696" not in str(compact_res["hierarchy"])
    frame_node = compact_res["hierarchy"]["children"][0]
    assert frame_node["children"] == [{"id": "b1", "role": "push button", "name": "Submit"}]

    raw_res = inspect_ui_tree(pid=4321, include_raw_tree=True)
    assert "children" in raw_res and raw_res["bus_name"] == ":1.42"

    no_match = inspect_ui_tree(pid=4321, query="NonExistentXYZ")
    assert no_match["interactive_elements"] == []
    role_match = inspect_ui_tree(pid=4321, role_filter="push button")
    assert len(role_match["interactive_elements"]) == 1


def test_role_disambiguation_batch_failure_screenshot_and_settle(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "wayland_computer_use_mcp.portal.screencast.get_artifacts_dir",
        lambda *a, **k: str(tmp_path),
    )
    tree = {
        "role": "application",
        "name": "DuplicateNamesApp",
        "bounds": [0, 0, 800, 600],
        "children": [
            {
                "role": "label",
                "name": "Save",
                "states": ["visible", "showing"],
                "bounds": [10, 10, 80, 20],
                "bus_name": ":1.5",
                "path": "/node/lbl",
                "children": [],
            },
            {
                "role": "push button",
                "name": "Save",
                "states": ["visible", "showing", "enabled"],
                "bounds": [10, 50, 100, 32],
                "bus_name": ":1.5",
                "path": "/node/btn",
                "children": [],
            },
        ],
    }
    flat = flatten_tree(tree, pid=9191)
    monkeypatch.setattr(
        "wayland_computer_use_mcp.tools.navigation_tools.get_application_tree",
        lambda pid: {**tree, "interactive_elements": flat},
    )
    monkeypatch.setattr("wayland_computer_use_mcp.delta.snapshot_interactive_state", lambda pid: {})
    dispatched = []
    monkeypatch.setattr(
        "wayland_computer_use_mcp.tools.navigation_tools.invoke_node_action",
        lambda pid, node_id, action="click", **kw: (
            dispatched.append(node_id) or f"clicked {node_id}"
        ),
    )

    out = interact_with_node(target="Save", role="push button", action="click", pid=9191)
    assert out["status"] == "success" and dispatched == ["b1"]

    dispatched.clear()
    monkeypatch.setattr(global_portal_session, "is_active", lambda: True)
    monkeypatch.setattr(type(global_portal_session), "is_mock", property(lambda self: False))
    monkeypatch.setattr(
        "wayland_computer_use_mcp.tools.navigation_tools.is_responsive", lambda pid: True
    )

    def _fake_capture(save_to_disk=False, filename=None):
        img = Image.new("RGB", (64, 48), "blue")
        if save_to_disk:
            p = tmp_path / (filename or "frame.png")
            img.save(p, format="PNG")
            global_portal_session.last_saved_frame_path = str(p)
        return img

    monkeypatch.setattr(global_portal_session, "capture_frame", _fake_capture)

    def _fail_second(pid, node_id, action="click", **kw):
        dispatched.append(node_id)
        if node_id == "b1":
            raise RuntimeError("boom on button")
        return "ok"

    monkeypatch.setattr(
        "wayland_computer_use_mcp.tools.navigation_tools.invoke_node_action", _fail_second
    )
    batch_res = batch_actions([{"target": "Save", "role": "button", "action": "click"}], pid=9191)
    assert dispatched == ["b1"]
    assert batch_res["status"] == "failed"
    assert batch_res["failure_screenshot"].endswith("batch_failure.png")
    assert (tmp_path / "batch_failure.png").exists()

    waited = []
    monkeypatch.setattr(
        "wayland_computer_use_mcp.a11y.events.global_event_listener.is_connected", lambda: True
    )
    monkeypatch.setattr(
        "wayland_computer_use_mcp.a11y.events.global_event_listener.wait_for_settled",
        lambda timeout_ms=250, quiet_period_ms=20, pid=None: (
            waited.append((timeout_ms, quiet_period_ms, pid)) or True
        ),
    )
    wrap_with_delta(1234, lambda: "ok", settle_ms=100)
    assert waited == [(100, 20, 1234)]


def test_agent_ergonomics_watch_events_and_navigation_batch(monkeypatch, tmp_path):
    listener = AtspiEventListener()
    assert listener.get_recent_mutations() == []

    from dbus_fast import MessageType

    mock_msg = MagicMock()
    mock_msg.message_type = MessageType.SIGNAL
    mock_msg.interface = "org.a11y.atspi.Event.Object"
    mock_msg.member = "StateChanged"
    mock_msg.path = "/org/a11y/atspi/accessible/123"
    mock_msg.body = ["focused", True]
    listener._message_handler(mock_msg)
    mutations = listener.get_recent_mutations()
    assert len(mutations) == 1 and mutations[0]["detail"] == "focused"
    assert listener.wait_for_settled(timeout_ms=50)["settled"] is True

    ev_res = watch_ui_events(timeout_ms=50)
    assert ev_res["settled"] is True and "mutations" in ev_res and "elapsed_ms" in ev_res

    filtered = inspect_ui_tree(pid=12345, query="Submit", role_filter="button")
    assert all("submit" in e["name"].lower() for e in filtered["interactive_elements"])
    assert (
        wait_for_element(target="b1", state="enabled", timeout_ms=200, pid=12345)["matched"] is True
    )

    batch_wait = batch_actions(
        [{"action": "wait_for", "target": "b1", "state": "present", "timeout_ms": 200}], pid=12345
    )
    assert batch_wait["status"] == "success" and "matched 'present'" in batch_wait["results"][0]

    monkeypatch.setattr(get_config(), "mock_mode", False)
    monkeypatch.setattr(
        "wayland_computer_use_mcp.tools.navigation_tools.is_responsive", lambda p: True
    )
    monkeypatch.setattr(
        "wayland_computer_use_mcp.tools.navigation_tools._resolve_target_node_id",
        lambda pid, tgt, role=None: None,
    )
    monkeypatch.setattr(
        "wayland_computer_use_mcp.tools.navigation_tools._suggest_closest_targets",
        lambda pid, tgt, limit=5: ["b1 ('Submit' [push button])"],
    )
    with pytest.raises(ValueError, match="Closest matches: b1"):
        interact_with_node(target="Submt", pid=12345)

    # Full navigation batch step dispatcher coverage
    with (
        patch(
            "wayland_computer_use_mcp.tools.navigation_tools.invoke_node_action",
            return_value="Clicked b1",
        ) as mock_node_act,
        patch(
            "wayland_computer_use_mcp.tools.navigation_tools.check_process_health_and_enrich",
            side_effect=lambda p, m: m,
        ),
        patch(
            "wayland_computer_use_mcp.tools.navigation_tools.global_portal_session"
        ) as mock_session,
    ):
        mock_session.is_mock = True
        mock_session.target_pid = 1234
        mock_session.dispatch_hover.return_value = "Hovered"
        mock_session.dispatch_click.return_value = "Clicked"
        mock_session.dispatch_scroll.return_value = "Scrolled"
        mock_session.dispatch_type_text.return_value = "Typed"
        mock_session.dispatch_key_combination.return_value = "Pressed"
        res = batch_actions(
            [
                {"action": "wait", "ms": 10},
                {"action": "hover", "x": 100, "y": 150},
                {"action": "click", "x": 100, "y": 150, "button": "left"},
                {"action": "scroll", "dx": 0, "dy": -50},
                {"action": "type", "text": "hello"},
                {"action": "key_combination", "keys": ["ctrl", "a"]},
                {"action": "interact", "node_id": "b1"},
            ],
            pid=1234,
        )
        assert (
            res["status"] == "success" and res["executed_steps_count"] == 7 and mock_node_act.called
        )
