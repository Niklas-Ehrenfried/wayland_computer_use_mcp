"""Live integration test exercising MCP server tools against examples/test_gui_app.py."""

import pytest

from wayland_computer_use_mcp.server import (
    check_app_liveness,
    click_element_by_label,
    double_click,
    get_app_logs,
    hover,
    inspect_ui_tree,
    key_combination,
    launch_app,
    restart_app,
    right_click,
    take_labeled_screenshot,
    terminate_app,
    type_text,
)


def test_mcp_workflow_on_example_app():
    # 1. Launch test GUI app in check/verification mode
    res = launch_app("examples/test_gui_app.py", args=["--check"])
    assert res["status"] == "launched"
    pid = res["pid"]
    assert pid > 0

    # 2. Check liveness and logs
    liveness = check_app_liveness(pid)
    assert liveness["pid"] == pid
    logs = get_app_logs(pid)
    assert isinstance(logs, str)

    # 3. Inspect UI tree for the launched app
    tree = inspect_ui_tree(pid, max_depth=3)
    assert isinstance(tree, dict)
    assert len(tree.get("children", [])) > 0

    # 4. Take Set-of-Marks labeled screenshot
    labeled = take_labeled_screenshot(pid=pid)
    assert labeled["element_count"] > 0
    assert len(labeled["elements"]) > 0

    # 5. Test clicking element by label
    click_res = click_element_by_label("Click Me!", pid=pid)
    assert "Clicked" in click_res

    # 6. Test typing text into focused widget
    type_res = type_text("MCP Automated Testing")
    assert "Typed" in type_res

    # 7. Test safe key combination
    key_res = key_combination(["ctrl", "a"])
    assert "Dispatched key combination" in key_res

    # 8. Test that system shortcut is strictly BLOCKED with PermissionError
    with pytest.raises(PermissionError, match="Super/Meta/Win keys are prohibited"):
        key_combination(["super", "tab"])

    with pytest.raises(PermissionError, match="Ctrl\\+Alt combinations are prohibited"):
        key_combination(["ctrl", "alt", "t"])

    # 9. Test double click and right click
    dc_res = double_click(100, 100)
    assert "Double-clicked" in dc_res

    rc_res = right_click(100, 100)
    assert "Clicked right" in rc_res

    h_res = hover(100, 100, duration_ms=50)
    assert "Hovered" in h_res

    # 10. Test hot restart and termination
    restart_res = restart_app(pid)
    new_pid = restart_res["new_pid"]
    assert new_pid != pid
    term_res = terminate_app(new_pid)
    assert term_res["status"] == "terminated"
