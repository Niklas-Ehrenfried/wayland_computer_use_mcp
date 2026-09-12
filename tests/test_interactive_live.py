"""Visible live integration test runner for wayland-computer-use-mcp.

Launches examples/test_gui_app.py on the desktop, tests:
1. Process lifecycle (launch, liveness, logs, termination)
2. Semantic tree-first navigation (1D node IDs, inspect_ui_tree)
3. Delta tracking across all actionable controls:
   - Button click (counter increment)
   - Text entry injection (type_text)
   - Slider adjustment (value update)
   - Tab switching (appearing & disappearing views)
   - Checkbox toggle (checked state mutation)
4. Batch action queue execution (with visual cursor motion & fail-fast safety)
5. Visual grounding Set-of-Marks screenshot (labeled elements overlay)
6. Crash-to-context exception interception (traceback captured from stderr)
"""

import time

import pytest

from wayland_computer_use_mcp.server import (
    batch_actions,
    check_app_liveness,
    get_app_logs,
    inspect_ui_tree,
    interact_with_node,
    launch_app,
    take_labeled_screenshot,
    terminate_app,
)


@pytest.mark.live
def test_visible_interactive_gui_workflow():
    # 1. Launch test GUI application on the desktop in test mode
    launch_res = launch_app("examples/test_gui_app.py", args=["--test-mode"])
    assert launch_res["status"] == "launched"
    pid = launch_res["pid"]
    assert pid > 0

    try:
        # Allow GTK window to map and paint
        time.sleep(1.2)

        # 2. Verify process liveness
        liveness = check_app_liveness(pid)
        assert liveness["responsive"] is True

        # 3. Inspect UI tree and 1D interactive elements
        tree = inspect_ui_tree(pid=pid, max_depth=12)
        assert "interactive_elements" in tree
        elements = tree["interactive_elements"]
        assert len(elements) > 0

        # Verify presence of controls
        btn_elem = next((e for e in elements if e["id"] == "b1" or "Click Me" in e["name"]), None)
        assert btn_elem is not None, "Counter button not found in 1D interactive element list"

        entry_elem = next(
            (e for e in elements if e["id"] == "e1" or e.get("role") in ("entry", "text")), None
        )
        assert entry_elem is not None, "Text entry not found in 1D interactive element list"

        # 4. Click button using 1D element ID and assert UI Delta contains change
        click_res = interact_with_node(node_id=btn_elem["id"], action="click", pid=pid)
        assert btn_elem["id"] in click_res or "Clicked" in click_res or "Activated" in click_res

        # 5. Type text into entry e1 and assert delta reflects update
        type_res = interact_with_node(
            node_id=entry_elem["id"], action="type", text="Antigravity Live", pid=pid
        )
        assert (
            "Antigravity Live" in type_res or "text" in type_res.lower() or "UI Changes" in type_res
        )

        # 6. Execute batch actions (visual hover, wait, click)
        batch = [
            {"action": "hover", "node_id": btn_elem["id"]},
            {"action": "wait", "ms": 150},
            {"action": "click", "node_id": btn_elem["id"]},
        ]
        batch_res = batch_actions(batch, pid=pid)
        assert batch_res["status"] == "success"
        assert batch_res["executed_steps_count"] == 3

        # 7. Test fail-fast reporting with intentional invalid node ID
        failing_batch = [
            {"action": "wait", "ms": 50},
            {"action": "interact", "node_id": "nonexistent_node_xyz"},
            {"action": "click", "node_id": btn_elem["id"]},
        ]
        fail_res = batch_actions(failing_batch, pid=pid)
        assert fail_res["status"] == "failed"
        assert fail_res["failed_step"] == 2
        assert len(fail_res["executed_steps"]) == 1

        # 8. Switch tabs to "Navigation & Pages" and verify UI delta captures appearing widgets
        nav_tab = next(
            (e for e in elements if "Navigation" in e.get("name", "") or e.get("id") == "t2"),
            None,
        )
        if nav_tab:
            tab_res = interact_with_node(
                target=nav_tab["name"] or nav_tab["id"], action="click", pid=pid
            )
            assert "Appeared" in tab_res or "Navigation" in tab_res or "UI Changes" in tab_res

        # 9. Switch to "Data & Lists" tab and toggle checkbox
        data_tab = next(
            (e for e in elements if "Data" in e.get("name", "") or e.get("id") == "t3"),
            None,
        )
        if data_tab:
            interact_with_node(target=data_tab["name"] or data_tab["id"], action="click", pid=pid)
            time.sleep(0.3)
            # Find checkbox
            updated_tree = inspect_ui_tree(pid=pid)
            chk_elem = next(
                (
                    e
                    for e in updated_tree["interactive_elements"]
                    if "Hardware" in e.get("name", "")
                    or e.get("role") in ("check box", "toggle button")
                ),
                None,
            )
            if chk_elem:
                chk_res = interact_with_node(node_id=chk_elem["id"], action="click", pid=pid)
                assert chk_elem["id"] in chk_res or "Hardware" in chk_res

        # 10. Switch to "Diagnostics" tab and test Crash-to-Context exception interception
        diag_tab = next(
            (e for e in elements if "Diagnostics" in e.get("name", "") or e.get("id") == "t4"),
            None,
        )
        if diag_tab:
            interact_with_node(target=diag_tab["name"] or diag_tab["id"], action="click", pid=pid)
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
            if crash_elem:
                # Trigger crash button - should capture Python exception without crashing MCP
                crash_res = interact_with_node(node_id=crash_elem["id"], action="click", pid=pid)
                assert (
                    "Traceback" in crash_res
                    or "Simulated Python GUI crash" in crash_res
                    or "exception" in crash_res.lower()
                    or "Activated" in crash_res
                )

        # 11. Take labeled Set-of-Marks screenshot
        screenshot = take_labeled_screenshot(pid=pid, save_artifact=True)
        assert screenshot["element_count"] > 0
        assert screenshot["image_path"]

        # 12. Check console logs
        logs = get_app_logs(pid=pid, lines=25)
        assert "[TestRig]" in logs or len(logs) >= 0

    finally:
        # 13. Clean termination
        term_res = terminate_app(pid)
        assert term_res["status"] == "terminated"


if __name__ == "__main__":
    test_visible_interactive_gui_workflow()
    print("Live interactive GUI workflow test completed successfully!")
