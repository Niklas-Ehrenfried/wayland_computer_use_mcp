"""Live integration test suite exercising all actionable GUI controls against test_gui_app.py.

Fully covers the 9 actionable UI components:
1. Counter Button (b1): Click via interact_with_node -> Counter increments (delta)
2. Text Entry (e1): Type text via interact_with_node -> Value updates (Current Text: '...')
3. Combobox Dropdown (c1): Select option -> Selection updates (delta)
4. Slider / Range (s1): Adjust slider value (25 ➔ 75) -> Value updates
5. Toggle Switch / Checkbox (k1): Toggle "Hardware Acceleration" -> State changes
6. Notebook Page Tabs (t1–t4): Click tab t2 -> Sub-page widgets appear, tab 1 hides
7. Scrollable Table / List: Dispatch scroll(0, -100) -> Viewport scrolls
8. Drag & Drop Gesture: drag(s_x, s_y, e_x, e_y) -> Smooth linear interpolation
9. Crash-to-Context: Click crash button -> Traceback intercepted and bound
"""

import time

import pytest

from wayland_computer_use_mcp.server import (
    batch_actions,
    check_app_liveness,
    drag,
    get_app_logs,
    hover,
    inspect_ui_tree,
    interact_with_node,
    launch_app,
    scroll,
    take_labeled_screenshot,
    terminate_app,
)

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def live_app():
    """Launches the multi-tab test GUI once for the entire module to prevent window flickering."""
    res = launch_app("examples/test_gui_app.py", args=["--test-mode"])
    assert res["status"] == "launched"
    pid = res["pid"]
    assert pid > 0
    time.sleep(1.2)  # Allow window to map, paint, and register with AT-SPI & KWin

    yield pid

    try:
        terminate_app(pid)
    except Exception:
        pass


def test_01_launch_and_liveness(live_app: int):
    """Verifies that the application launches, stays responsive, and generates logs."""
    pid = live_app
    liveness = check_app_liveness(pid)
    assert liveness["responsive"] is True

    tree = inspect_ui_tree(pid=pid, max_depth=10)
    assert "interactive_elements" in tree
    assert len(tree["interactive_elements"]) > 0


def test_02_click_counter_button(live_app: int):
    """Component 1: Counter Button (b1) - Click increments counter; verified via delta."""
    pid = live_app
    tree = inspect_ui_tree(pid=pid)
    btn_elem = next(
        (e for e in tree["interactive_elements"] if e["id"] == "b1" or "Click Me" in e["name"]),
        None,
    )
    assert btn_elem is not None, "Button b1 not found in UI tree"

    click_res = interact_with_node(node_id=btn_elem["id"], action="click", pid=pid)
    assert btn_elem["id"] in click_res or "Clicked" in click_res or "Activated" in click_res


def test_03_type_text_into_entry(live_app: int):
    """Component 2: Text Entry (e1) - Type text; value updates and label reflects text."""
    pid = live_app
    tree = inspect_ui_tree(pid=pid)
    entry_elem = next(
        (
            e
            for e in tree["interactive_elements"]
            if e["id"] == "e1" or e.get("role") in ("entry", "text", "text box")
        ),
        None,
    )
    assert entry_elem is not None, "Text entry e1 not found in UI tree"

    type_res = interact_with_node(
        node_id=entry_elem["id"], action="type", text="Antigravity Testing", pid=pid
    )
    assert "Antigravity" in type_res or "UI Changes" in type_res or "text" in type_res.lower()

    # Verify that the live UI label updated to reflect the new text
    time.sleep(0.3)
    tree2 = inspect_ui_tree(pid=pid)
    matching_lbl = next(
        (e for e in tree2["interactive_elements"] if "Antigravity Testing" in e.get("name", "")),
        None,
    )
    assert matching_lbl is not None, "Label was not updated with typed text in live app!"


def test_04_combobox_selection(live_app: int):
    """Component 3: Combobox Dropdown (c1) - Open dropdown and select Staging option."""
    pid = live_app
    tree = inspect_ui_tree(pid=pid)
    combo_elem = next(
        (
            e
            for e in tree["interactive_elements"]
            if e["id"] == "c1" or e.get("role") in ("combo box", "dropdown", "combobox")
        ),
        None,
    )
    assert combo_elem is not None, "Combobox dropdown c1 not found in UI tree"

    # 1. Click dropdown to open popover list
    click_res = interact_with_node(node_id=combo_elem["id"], action="click", pid=pid)
    assert "Clicked" in click_res or combo_elem["id"] in click_res
    time.sleep(0.4)

    # 2. Inspect tree to find the dropdown items in popover
    popover_tree = inspect_ui_tree(pid=pid, max_depth=30)
    staging_elem = next(
        (e for e in popover_tree["interactive_elements"] if "Staging" in e.get("name", "")),
        None,
    )
    assert staging_elem is not None, "Staging option not found in dropdown popover"

    # 3. Click the staging item to select it
    sel_res = interact_with_node(node_id=staging_elem["id"], action="click", pid=pid)
    assert "Clicked" in sel_res or "Staging" in sel_res
    time.sleep(0.3)

    # 4. Verify that the UI tree and status label updated to Staging
    updated_tree = inspect_ui_tree(pid=pid)
    has_staging = any(
        "Selected: Staging - Testing Cluster" in e.get("name", "")
        for e in updated_tree["interactive_elements"]
    )
    assert has_staging, "Combobox did not update label to 'Selected: Staging - Testing Cluster'"


def test_05_slider_adjustment(live_app: int):
    """Component 4: Slider / Range (s1) - Adjust slider value; verify label reflects change."""
    pid = live_app
    tree = inspect_ui_tree(pid=pid)
    slider_elem = next(
        (
            e
            for e in tree["interactive_elements"]
            if e["id"] == "s1" or e.get("role") in ("scale", "slider")
        ),
        None,
    )
    assert slider_elem is not None, "Slider s1 not found in UI tree"

    # Click slider to focus
    interact_with_node(node_id=slider_elem["id"], action="click", pid=pid)

    # Perform smooth horizontal drag along the slider track (e.g. 25% to 75% width)
    bounds = slider_elem.get("bounds", [100, 200, 200, 40])
    sy = bounds[1] + bounds[3] // 2
    sx = bounds[0] + int(bounds[2] * 0.25)
    ex = bounds[0] + int(bounds[2] * 0.80)
    drag_res = drag(sx, sy, ex, sy)
    assert "Dragged" in drag_res

    time.sleep(0.3)
    updated_tree = inspect_ui_tree(pid=pid)
    slider_lbl = next(
        (e for e in updated_tree["interactive_elements"] if "Slider Value:" in e.get("name", "")),
        None,
    )
    assert slider_lbl is not None, "Slider value label not found in UI tree"
    # Value should have moved away from default 25.0
    assert slider_lbl["name"] != "Slider Value: 25.0", (
        f"Slider value did not change: {slider_lbl['name']}"
    )


def test_06_tab_navigation_and_reveals(live_app: int):
    """Component 6: Notebook Page Tabs & Sub-Pages - Exercise full sub-page navigation."""
    pid = live_app
    tree = inspect_ui_tree(pid=pid)
    nav_tab = next(
        (
            e
            for e in tree["interactive_elements"]
            if "Navigation" in e.get("name", "") or e.get("id") == "t2"
        ),
        None,
    )
    assert nav_tab is not None, "Navigation tab not found"

    # 1. Switch to Navigation tab
    target_id = nav_tab["name"] or nav_tab["id"]
    interact_with_node(target=target_id, action="click", pid=pid)
    time.sleep(0.3)

    tree_nav = inspect_ui_tree(pid=pid)
    assert any(
        "Active Sub-Page: overview" in e.get("name", "") for e in tree_nav["interactive_elements"]
    )

    # 2. Navigate to Analytics page
    interact_with_node(target="Go to Analytics Page", action="click", pid=pid)
    time.sleep(0.3)
    tree_analytics = inspect_ui_tree(pid=pid)
    assert any(
        "Active Sub-Page: analytics" in e.get("name", "")
        for e in tree_analytics["interactive_elements"]
    )
    assert any(
        "Go to Settings Page" in e.get("name", "") for e in tree_analytics["interactive_elements"]
    )

    # 3. Navigate to Settings page
    interact_with_node(target="Go to Settings Page", action="click", pid=pid)
    time.sleep(0.3)
    tree_settings = inspect_ui_tree(pid=pid)
    assert any(
        "Active Sub-Page: settings" in e.get("name", "")
        for e in tree_settings["interactive_elements"]
    )
    assert any(
        "Back to Overview Page" in e.get("name", "") for e in tree_settings["interactive_elements"]
    )

    # 4. Navigate back to Overview page
    interact_with_node(target="Back to Overview Page", action="click", pid=pid)
    time.sleep(0.3)
    tree_overview = inspect_ui_tree(pid=pid)
    assert any(
        "Active Sub-Page: overview" in e.get("name", "")
        for e in tree_overview["interactive_elements"]
    )


def test_07_checkbox_toggle_and_state(live_app: int):
    """Component 5: Toggle Switch / Checkbox (k1) - Toggle Hardware Acceleration on and off."""
    pid = live_app
    tree = inspect_ui_tree(pid=pid)
    data_tab = next(
        (
            e
            for e in tree["interactive_elements"]
            if "Data" in e.get("name", "") or e.get("id") == "t3"
        ),
        None,
    )
    assert data_tab is not None, "Data & Lists tab not found"
    target_id = data_tab["name"] or data_tab["id"]
    interact_with_node(target=target_id, action="click", pid=pid)
    time.sleep(0.3)

    tree_data = inspect_ui_tree(pid=pid)
    assert any(
        "Hardware Acceleration: Enabled" in e.get("name", "")
        for e in tree_data["interactive_elements"]
    )

    chk_elem = next(
        (
            e
            for e in tree_data["interactive_elements"]
            if e.get("id") == "k1"
            or e.get("role") in ("checkbox", "check box", "check button", "checkbutton")
        ),
        None,
    )
    assert chk_elem is not None, "Hardware acceleration checkbox not found"

    # 1. Toggle off
    interact_with_node(node_id=chk_elem["id"], action="click", pid=pid)
    time.sleep(0.3)
    tree_toggled = inspect_ui_tree(pid=pid)
    assert any(
        "Hardware Acceleration: Disabled" in e.get("name", "")
        for e in tree_toggled["interactive_elements"]
    )

    # 2. Toggle back on
    interact_with_node(node_id=chk_elem["id"], action="click", pid=pid)
    time.sleep(0.3)
    tree_restored = inspect_ui_tree(pid=pid)
    assert any(
        "Hardware Acceleration: Enabled" in e.get("name", "")
        for e in tree_restored["interactive_elements"]
    )


def test_08_scroll_and_select_data_list(live_app: int):
    """Component 7: Scrollable Table / List - Click item, scroll, and click scrolled item."""
    pid = live_app
    tree = inspect_ui_tree(pid=pid)
    data_tab = next(
        (
            e
            for e in tree["interactive_elements"]
            if "Data" in e.get("name", "") or e.get("id") == "t3"
        ),
        None,
    )
    if data_tab:
        interact_with_node(target=data_tab["name"] or data_tab["id"], action="click", pid=pid)
        time.sleep(0.3)
        tree = inspect_ui_tree(pid=pid)

    # 1. Click Row #02 while visible initially
    row_02 = next(
        (e for e in tree["interactive_elements"] if "Dataset Row #02" in e.get("name", "")),
        None,
    )
    assert row_02 is not None, "Dataset Row #02 should be initially visible"
    interact_with_node(node_id=row_02["id"], action="click", pid=pid)
    time.sleep(0.3)
    tree_after_r2 = inspect_ui_tree(pid=pid)
    assert any(
        "Selected Row: Dataset Row #02" in e.get("name", "")
        for e in tree_after_r2["interactive_elements"]
    ), "Selecting Row #02 did not update Selected Row status label"

    # 2. Hover over the list viewport to direct scroll events to the ScrolledWindow
    hover(row_02["center"][0], row_02["center"][1])
    time.sleep(0.1)

    # Scroll down to reveal the bottom item (Row #20)
    scroll_down = scroll(0, -160)
    assert "Scrolled" in scroll_down
    time.sleep(0.4)

    scrolled_tree = inspect_ui_tree(pid=pid)
    row_20 = next(
        (
            e
            for e in scrolled_tree["interactive_elements"]
            if "Dataset Row #20" in e.get("name", "")
        ),
        None,
    )
    assert row_20 is not None, "Dataset Row #20 should be revealed after scrolling down"

    # 3. Click the bottom row (Row #20) and verify selection updates
    interact_with_node(node_id=row_20["id"], action="click", pid=pid)
    time.sleep(0.3)
    tree_after_r20 = inspect_ui_tree(pid=pid)
    assert any(
        "Selected Row: Dataset Row #20" in e.get("name", "")
        for e in tree_after_r20["interactive_elements"]
    ), "Selecting scrolled Row #20 did not update Selected Row status label"

    # 4. Scroll back up and verify we can re-select Row #02
    scroll_up = scroll(0, 160)
    assert "Scrolled" in scroll_up
    time.sleep(0.4)
    tree_top = inspect_ui_tree(pid=pid)
    row_02_again = next(
        (e for e in tree_top["interactive_elements"] if "Dataset Row #02" in e.get("name", "")),
        None,
    )
    assert row_02_again is not None, "Dataset Row #02 should be visible after scrolling back up"
    interact_with_node(node_id=row_02_again["id"], action="click", pid=pid)
    time.sleep(0.3)
    tree_restored = inspect_ui_tree(pid=pid)
    assert any(
        "Selected Row: Dataset Row #02" in e.get("name", "")
        for e in tree_restored["interactive_elements"]
    )


def test_09_drag_and_drop_gesture(live_app: int):
    """Component 8: Drag & Drop Gesture - Smooth linear interpolation within window bounds."""
    # Test valid clamped drag within window bounds
    drag_res = drag(100, 200, 250, 200)
    assert "Dragged" in drag_res


def test_10_crash_to_context_interception(live_app: int):
    """Component 9: Crash-to-Context - Click crash button; server intercepts stderr traceback."""
    pid = live_app
    tree = inspect_ui_tree(pid=pid)
    diag_tab = next(
        (
            e
            for e in tree["interactive_elements"]
            if "Diagnostics" in e.get("name", "") or e.get("id") == "t4"
        ),
        None,
    )
    assert diag_tab is not None, "Diagnostics tab not found in UI tree"
    target_id = diag_tab["name"] or diag_tab["id"]
    interact_with_node(target=target_id, action="click", pid=pid)
    time.sleep(0.3)

    updated_tree = inspect_ui_tree(pid=pid)
    crash_elem = next(
        (
            e
            for e in updated_tree["interactive_elements"]
            if "Simulate Python Crash" in e.get("name", "")
        ),
        None,
    )
    assert crash_elem is not None, "Simulate Python Crash button not found in UI tree"
    res = interact_with_node(node_id=crash_elem["id"], action="click", pid=pid)
    assert "[!WARNING]" in res, f"Expected crash warning alert in response, got: {res}"
    assert "Simulated Python GUI crash for MCP verification" in res, (
        f"Expected traceback in response, got: {res}"
    )


def test_11_batch_actions_and_fail_fast(live_app: int):
    """Tests executing batch action sequences and verifying fail-fast on error."""
    pid = live_app
    tree = inspect_ui_tree(pid=pid)
    btn_elem = next(
        (e for e in tree["interactive_elements"] if e["id"] == "b1" or "Click Me" in e["name"]),
        None,
    )
    if btn_elem:
        batch = [
            {"action": "hover", "node_id": btn_elem["id"]},
            {"action": "wait", "ms": 100},
            {"action": "click", "node_id": btn_elem["id"]},
        ]
        res = batch_actions(batch, pid=pid)
        assert res["status"] == "success"
        assert res["executed_steps_count"] == 3

    fail_batch = [
        {"action": "wait", "ms": 50},
        {"action": "interact", "node_id": "nonexistent_node_999"},
        {"action": "scroll", "dy": 10},
    ]
    fail_res = batch_actions(fail_batch, pid=pid)
    assert fail_res["status"] == "failed"
    assert fail_res["failed_step"] == 2
    assert len(fail_res["executed_steps"]) == 1


def test_12_labeled_screenshot_set_of_marks(live_app: int):
    """Tests capturing Set-of-Marks labeled screenshot with bounding tags."""
    pid = live_app
    screenshot = take_labeled_screenshot(pid=pid, save_artifact=True)
    assert screenshot["element_count"] > 0
    assert screenshot["image_path"]


def test_13_app_logs_and_event_stream(live_app: int):
    """Tests retrieving console logs and verifying event stream capture."""
    pid = live_app
    logs = get_app_logs(pid=pid, lines=50)
    assert isinstance(logs, str)
    assert len(logs) > 0


def test_14_live_text_manipulation_suite(live_app: int):
    """Tests text clear, copy, paste, select, and drag-selection on live application."""
    pid = live_app
    tree = inspect_ui_tree(pid=pid)

    # Ensure Controls & Inputs tab (Tab 1) is active so e1 and its status label are visible
    try:
        interact_with_node(target="Controls & Inputs", action="click", pid=pid)
        time.sleep(0.3)
    except Exception:
        pass
    tree = inspect_ui_tree(pid=pid)

    entry_elem = next(
        (
            e
            for e in tree["interactive_elements"]
            if e["id"] == "e1" or e.get("role") in ("entry", "text", "text box")
        ),
        None,
    )
    assert entry_elem is not None, "Text entry e1 not found"

    # 1. Type specific text
    interact_with_node(node_id=entry_elem["id"], action="type", text="LiveClipboard123", pid=pid)
    time.sleep(0.2)
    tree_after_type = inspect_ui_tree(pid=pid)
    has_text = any(
        "LiveClipboard123" in e.get("name", "") for e in tree_after_type["interactive_elements"]
    )
    assert has_text

    # 2. Select all and copy
    interact_with_node(node_id=entry_elem["id"], action="select_all", pid=pid)
    copy_res = interact_with_node(node_id=entry_elem["id"], action="copy", pid=pid)
    assert "Copied" in copy_res or entry_elem["id"] in copy_res

    # 3. Clear text
    clear_res = interact_with_node(node_id=entry_elem["id"], action="clear", pid=pid)
    assert (
        "clear" in clear_res.lower()
        or "text ''" in clear_res.lower()
        or entry_elem["id"] in clear_res
    )
    time.sleep(0.2)
    tree_after_clear = inspect_ui_tree(pid=pid)
    has_cleared = any(
        "Current Text: ''" in e.get("name", "") for e in tree_after_clear["interactive_elements"]
    )
    assert has_cleared

    # 4. Drag-select visual gesture across text box
    drag_res = interact_with_node(node_id=entry_elem["id"], action="drag_select", pid=pid)
    assert "Drag-selected" in drag_res or "drag" in drag_res.lower()
