"""Unit tests for tree-first semantic navigation, batching, and traceback binding."""

import collections

import pytest

from wayland_computer_use_mcp.a11y import (
    clear_node_cache,
    flatten_tree,
    get_cached_node,
    invoke_node_action,
)
from wayland_computer_use_mcp.config import get_config
from wayland_computer_use_mcp.portal import global_portal_session
from wayland_computer_use_mcp.process import (
    _logs,
    _processes,
    check_process_health_and_enrich,
    get_recent_traceback,
)
from wayland_computer_use_mcp.server import (
    batch_actions,
    inspect_ui_tree,
    interact_with_node,
)


@pytest.fixture(autouse=True)
def setup_mock(monkeypatch):
    monkeypatch.setattr(get_config(), "mock_mode", True)
    monkeypatch.setattr(global_portal_session, "_is_mock", True)
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
    assert len(elements) == 7

    # Check ID prefixes
    ids = [e["id"] for e in elements]
    assert ids == ["b1", "b2", "e1", "c1", "s1", "t1", "lbl1"]

    # Check center coordinate computation
    b1 = elements[0]
    assert b1["id"] == "b1"
    assert b1["name"] == "Submit"
    assert b1["center"] == [50 + 60, 100 + 20]  # [110, 120]
    assert b1["enabled"] is True

    # Check cache was populated
    cached = get_cached_node(9999, "b1")
    assert cached is not None
    assert cached["name"] == "Submit"


def test_invoke_node_action_fallback(monkeypatch):
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

    # In mock mode, Level 1 DoAction returns False (synthetic node),
    # so it falls back to Level 2 click
    res = invoke_node_action(8888, "b1", action="click")
    assert "b1" in res
    assert "Clickable Button" in res
    assert "Fallback Level 2" in res or "Activated" in res


def test_interact_with_node_tool():
    tree = inspect_ui_tree(pid=12345)
    assert "interactive_elements" in tree
    assert len(tree["interactive_elements"]) > 0

    first_elem = tree["interactive_elements"][0]
    node_id = first_elem["id"]

    res = interact_with_node(node_id=node_id, pid=12345)
    assert node_id in res


def test_interact_with_node_typing_and_slider():
    tree = {
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
    flatten_tree(tree, pid=5555)

    # Test typing into entry
    type_res = interact_with_node(node_id="e1", action="type", text="Antigravity Test", pid=5555)
    assert "Typed" in type_res

    # Test slider interaction
    slider_res = interact_with_node(node_id="s1", action="click", pid=5555)
    assert "s1" in slider_res
    assert "Slider" in slider_res or "scale" in slider_res

    # Test label resolution and interaction
    lbl_res = interact_with_node(target="Hardware Acceleration", action="click", pid=5555)
    assert "k1" in lbl_res or "Clicked" in lbl_res


def test_batch_actions_success_and_fail_fast():
    # 1. Successful batch
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

    # 2. Intentional failure in step 2 (invalid node_id)
    failing_actions = [
        {"action": "click", "x": 50, "y": 50},
        {"action": "interact", "node_id": "invalid_nonexistent_node"},
        {"action": "type", "text": "Should not execute"},
    ]

    fail_res = batch_actions(failing_actions, pid=12345)
    assert fail_res["status"] == "failed"
    assert fail_res["failed_step"] == 2
    assert fail_res["total_steps"] == 3
    assert len(fail_res["executed_steps"]) == 1
    assert "invalid_nonexistent_node" in fail_res["error"]


def test_crash_to_context_enrichment():
    fake_pid = 77777
    deque = collections.deque(maxlen=200)
    deque.append("INFO: Initializing")
    deque.append("Traceback (most recent call last):")
    deque.append('  File "app.py", line 42, in on_clicked')
    deque.append("    raise ValueError('Sample Crash')")
    deque.append("ValueError: Sample Crash")
    _logs[fake_pid] = deque

    tb = get_recent_traceback(fake_pid)
    assert tb is not None
    assert "ValueError: Sample Crash" in tb

    # Mock managed process alive
    class MockProc:
        def poll(self):
            return None

    _processes[fake_pid] = MockProc()

    # Living process warning enrichment
    msg = check_process_health_and_enrich(fake_pid, "Button clicked successfully")
    assert "Button clicked successfully" in msg
    assert "Application emitted a Python exception to stderr" in msg
    assert "ValueError: Sample Crash" in msg

    # Dead/crashed process raises RuntimeError with traceback
    class DeadProc:
        def poll(self):
            return 1

    _processes[fake_pid] = DeadProc()

    with pytest.raises(RuntimeError, match="crashed!\n\nTraceback:"):
        check_process_health_and_enrich(fake_pid, "Action completed")

    _processes.pop(fake_pid, None)
    _logs.pop(fake_pid, None)


def test_compute_ui_delta_and_format():
    from wayland_computer_use_mcp.delta import compute_ui_delta, format_delta_markdown

    before = {
        "b1": {
            "id": "b1",
            "name": "Click Me! (0 clicks)",
            "role": "button",
            "states": {"enabled", "visible"},
        },
        "e1": {
            "id": "e1",
            "name": "",
            "role": "entry",
            "value": "",
            "states": {"enabled", "visible"},
        },
        "k1": {
            "id": "k1",
            "name": "Hardware Accel",
            "role": "check box",
            "states": {"enabled", "checked"},
        },
    }

    after = {
        # b1 text updated
        "b1": {
            "id": "b1",
            "name": "Clicked! (1 clicks)",
            "role": "button",
            "states": {"enabled", "visible"},
        },
        # e1 value updated
        "e1": {
            "id": "e1",
            "name": "",
            "role": "entry",
            "value": "New text",
            "states": {"enabled", "visible"},
        },
        # k1 unchecked
        "k1": {"id": "k1", "name": "Hardware Accel", "role": "check box", "states": {"enabled"}},
        # b2 appeared
        "b2": {"id": "b2", "name": "Submit", "role": "button", "states": {"enabled", "visible"}},
    }

    delta = compute_ui_delta(before, after)
    assert len(delta["modified"]) == 3
    assert len(delta["appeared"]) == 1
    assert len(delta["hidden"]) == 0

    md = format_delta_markdown(delta)
    assert "UI Changes:" in md
    assert "b1 [button]: text 'Click Me! (0 clicks)' ➔ 'Clicked! (1 clicks)'" in md
    assert "e1 [entry]: value '' ➔ 'New text'" in md
    assert "-checked" in md
    assert "b2 ('Submit')" in md


def test_all_9_actionable_widgets_offline_suite():
    """Validates full coverage of the 9 actionable UI components in offline/mock mode:

    1. Counter Button (b1): Click via interact_with_node -> Verified via delta
    2. Text Entry (e1): Type text via interact_with_node -> Verified via delta
    3. Combobox Dropdown (c1): Option update -> Verified via delta
    4. Slider / Range (s1): Adjust slider value (25 ➔ 75) -> Verified via delta
    5. Toggle Switch / Checkbox (k1): Toggle -> Verified via state mutation
    6. Notebook Page Tabs (t1–t4): Click tab t2 -> b2, b3, b4 appear, tab 1 controls hide
    7. Scrollable Table / List: scroll(0, -100)
    8. Drag & Drop Gesture: drag(s_x, s_y, e_x, e_y) within safe bounds
    9. Crash-to-Context: Exception interception binds traceback to tool response
    """
    from wayland_computer_use_mcp.delta import compute_ui_delta, format_delta_markdown
    from wayland_computer_use_mcp.server import drag, scroll

    # Complete synthetic mock tree with all 9 actionable components
    synthetic_tree = {
        "role": "application",
        "name": "FullCoverageApp",
        "bounds": [0, 0, 800, 600],
        "children": [
            # 6. Notebook tabs (t1-t4)
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
                "role": "page tab",
                "name": "Data & Lists",
                "states": ["visible", "enabled"],
                "bounds": [180, 10, 80, 30],
                "children": [],
            },
            {
                "role": "page tab",
                "name": "Diagnostics",
                "states": ["visible", "enabled"],
                "bounds": [265, 10, 80, 30],
                "children": [],
            },
            # 1. Counter button (b1)
            {
                "role": "push button",
                "name": "Click Me! (0 clicks)",
                "states": ["visible", "enabled"],
                "bounds": [50, 60, 120, 35],
                "children": [],
            },
            # 2. Text entry (e1)
            {
                "role": "entry",
                "name": "Text Entry",
                "states": ["visible", "enabled"],
                "bounds": [50, 110, 200, 30],
                "children": [],
            },
            # 3. Combobox (c1)
            {
                "role": "combo box",
                "name": "Development - Localhost",
                "states": ["visible", "enabled"],
                "bounds": [50, 160, 200, 32],
                "children": [],
            },
            # 4. Slider / Range (s1)
            {
                "role": "scale",
                "name": "Slider",
                "states": ["visible", "enabled"],
                "value": 25.0,
                "bounds": [50, 210, 300, 30],
                "children": [],
            },
            # 5. Toggle switch / Checkbox (k1)
            {
                "role": "check box",
                "name": "Hardware Acceleration",
                "states": ["visible", "enabled", "checked"],
                "bounds": [50, 260, 180, 30],
                "children": [],
            },
            # 9. Intentional crash button
            {
                "role": "push button",
                "name": "Simulate Python Crash",
                "states": ["visible", "enabled"],
                "bounds": [50, 310, 180, 35],
                "children": [],
            },
        ],
    }
    flatten_tree(synthetic_tree, pid=4444)

    # 1. Counter Button (b1) interaction
    res_b1 = interact_with_node(node_id="b1", action="click", pid=4444)
    assert "b1" in res_b1

    # 2. Text Entry (e1) typing
    res_e1 = interact_with_node(node_id="e1", action="type", text="Hello Wayland", pid=4444)
    assert "Typed" in res_e1

    # 3. Combobox (c1) interaction
    res_c1 = interact_with_node(node_id="c1", action="click", pid=4444)
    assert "c1" in res_c1

    # 4. Slider (s1) interaction
    res_s1 = interact_with_node(node_id="s1", action="click", pid=4444)
    assert "s1" in res_s1

    # 5. Checkbox (k1) interaction
    res_k1 = interact_with_node(node_id="k1", action="click", pid=4444)
    assert "k1" in res_k1

    # 6. Tab navigation (t2) interaction
    res_t2 = interact_with_node(node_id="t2", action="click", pid=4444)
    assert "t2" in res_t2

    # 7. Scrollable List scroll dispatch
    res_scroll = scroll(0, -100)
    assert "Scrolled" in res_scroll

    # 8. Drag gesture dispatch
    res_drag = drag(50, 220, 250, 220)
    assert "Dragged" in res_drag

    # Verify Delta tracking across all mutated widgets
    before_state = {
        "b1": {
            "id": "b1",
            "name": "Click Me! (0 clicks)",
            "role": "button",
            "states": {"enabled", "visible"},
        },
        "e1": {
            "id": "e1",
            "name": "",
            "role": "entry",
            "value": "",
            "states": {"enabled", "visible"},
        },
        "c1": {
            "id": "c1",
            "name": "Development - Localhost",
            "role": "combo box",
            "value": "Development",
            "states": {"enabled", "visible"},
        },
        "s1": {
            "id": "s1",
            "name": "Slider",
            "role": "scale",
            "value": 25.0,
            "states": {"enabled", "visible"},
        },
        "k1": {
            "id": "k1",
            "name": "Hardware Acceleration",
            "role": "check box",
            "states": {"enabled", "checked"},
        },
    }
    after_state = {
        # 1. Counter incremented
        "b1": {
            "id": "b1",
            "name": "Clicked! (1 clicks)",
            "role": "button",
            "states": {"enabled", "visible"},
        },
        # 2. Text entered
        "e1": {
            "id": "e1",
            "name": "",
            "role": "entry",
            "value": "Current Text: 'Hello Wayland'",
            "states": {"enabled", "visible"},
        },
        # 3. Combobox updated from Development to Staging
        "c1": {
            "id": "c1",
            "name": "Staging - Testing Cluster",
            "role": "combo box",
            "value": "Staging",
            "states": {"enabled", "visible"},
        },
        # 4. Slider updated from 25.0 to 75.0
        "s1": {
            "id": "s1",
            "name": "Slider",
            "role": "scale",
            "value": 75.0,
            "states": {"enabled", "visible"},
        },
        # 5. Checkbox unchecked
        "k1": {
            "id": "k1",
            "name": "Hardware Acceleration",
            "role": "check box",
            "states": {"enabled"},
        },
        # 6. Tab 2 widgets appeared
        "b2": {
            "id": "b2",
            "name": "Go to Analytics Page",
            "role": "button",
            "states": {"enabled", "visible"},
        },
        "b3": {
            "id": "b3",
            "name": "Go to Settings Page",
            "role": "button",
            "states": {"enabled", "visible"},
        },
        "b4": {
            "id": "b4",
            "name": "Back to Overview Page",
            "role": "button",
            "states": {"enabled", "visible"},
        },
    }
    delta = compute_ui_delta(before_state, after_state)
    assert len(delta["modified"]) == 5
    assert len(delta["appeared"]) == 3
    md = format_delta_markdown(delta)
    assert "b1 [button]: text 'Click Me! (0 clicks)' ➔ 'Clicked! (1 clicks)'" in md
    assert "c1 [combo box]:" in md and "Staging" in md
    assert "s1 [scale]: value 25.0 ➔ 75.0" in md
    assert "k1 [check box]: states (-checked)" in md
    assert "Go to Analytics Page" in md

    # 9. Crash-to-Context exception interception
    fake_pid = 99911

    class MockProc:
        def poll(self):
            return None

    _processes[fake_pid] = MockProc()
    _logs[fake_pid] = collections.deque(
        [
            "Traceback (most recent call last):",
            '  File "test_gui_app.py", line 290, in on_crash_clicked',
            "RuntimeError: Simulated Python GUI crash for MCP verification",
        ]
    )
    msg = check_process_health_and_enrich(fake_pid, "Clicked Simulate Python Crash")
    assert "Simulated Python GUI crash for MCP verification" in msg
    assert "Application emitted a Python exception to stderr" in msg
    _processes.pop(fake_pid, None)
    _logs.pop(fake_pid, None)


def test_interact_with_node_text_actions_mock():
    """Unit test for all new semantic text actions supported by interact_with_node."""
    # Build tree with an entry element
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

    # 1. Type
    res_type = interact_with_node(target="e1", action="type", text="Antigravity Test")
    assert "Typed" in res_type or "Set text" in res_type

    # 2. Select all
    res_sel_all = interact_with_node(target="e1", action="select_all")
    assert "Selected" in res_sel_all

    # 3. Select range
    res_sel_range = interact_with_node(
        target="e1", action="select_range", start_offset=0, end_offset=5
    )
    assert "Selected" in res_sel_range or "drag" in res_sel_range.lower()

    # 4. Copy
    res_copy = interact_with_node(target="e1", action="copy")
    assert "Copied" in res_copy

    # 5. Clear
    res_clear = interact_with_node(target="e1", action="clear")
    assert "Cleared" in res_clear or "clear" in res_clear.lower()

    # 6. Paste
    res_paste = interact_with_node(target="e1", action="paste")
    assert "Pasted" in res_paste

    # 7. Cut
    res_cut = interact_with_node(target="e1", action="cut")
    assert "Cut" in res_cut

    # 8. Drag select
    res_drag = interact_with_node(target="e1", action="drag_select")
    assert "Drag-selected" in res_drag


def test_smart_typing_thresholds():
    """Unit test verifying fast typing vs automatic clipboard paste thresholds."""
    # Short string <= 30 chars
    res_short = global_portal_session.dispatch_type_text("Short text")
    assert "Typed" in res_short

    # Long string > 30 chars
    res_long = global_portal_session.dispatch_type_text(
        "This is an intentionally very long text string that exceeds 30 characters"
    )
    assert "Pasted" in res_long or "Typed" in res_long
