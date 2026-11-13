"""Tests for FastMCP server tool registrations and executions."""

from wayland_computer_use_mcp.server import (
    capture_window_frame,
    click,
    drag,
    inspect_ui_tree,
    mcp,
    scroll,
    type_text,
)


async def test_registered_tools():
    # Active high-leverage exposed MCP tools (22 default tools; desktop installer is opt-in)
    exposed_tool_names = [
        "launch_app",
        "restart_app",
        "terminate_app",
        "get_app_logs",
        "capture_window_frame",
        "inspect_ui_tree",
        "interact_with_node",
        "batch_actions",
        "click",
        "double_click",
        "right_click",
        "hover",
        "drag",
        "scroll",
        "type_text",
        "key_combination",
        "clipboard_read",
        "clipboard_write",
        "window_control",
        "list_managed_apps",
        "watch_ui_events",
        "preset_workflow",
    ]

    tools = await mcp.list_tools()
    tool_map = {t.name: t for t in tools}
    registered_names = set(tool_map.keys())

    for name in exposed_tool_names:
        assert name in registered_names, f"Expected tool '{name}' to be registered"

    assert len(registered_names) == 22
    assert "install_to_desktop" not in registered_names
    assert "uninstall_from_desktop" not in registered_names

    # Verify ToolAnnotations hints for MCP clients
    assert tool_map["inspect_ui_tree"].annotations is not None
    assert tool_map["inspect_ui_tree"].annotations.read_only_hint is True
    assert tool_map["terminate_app"].annotations is not None
    assert tool_map["terminate_app"].annotations.destructive_hint is True

    # Verify interact_with_node schema exposes target as primary and node_id as deprecated
    node_schema = tool_map["interact_with_node"].parameters["properties"]
    assert "target" in node_schema
    assert "Deprecated" in node_schema["node_id"].get("description", "")

    # Retired / consolidated tools should not pollute the exposed MCP surface
    retired_tools = [
        "check_app_liveness",
        "click_element_by_label",
        "get_window_geometry",
        "focus_window",
        "take_labeled_screenshot",
    ]
    for name in retired_tools:
        assert name not in registered_names, f"Tool '{name}' should be consolidated/internal"


def test_server_capture_and_input(monkeypatch):
    from wayland_computer_use_mcp.config import get_config

    monkeypatch.setattr(get_config(), "mock_mode", True)

    # Test capture
    img = capture_window_frame(crop_box=[10, 10, 100, 100])
    assert img is not None
    assert img.data is not None
    assert len(img.data) > 0
    assert img.to_image_content().mime_type == "image/png"

    # Test input
    click_res = click(50, 50, button="left")
    assert "Clicked" in click_res

    drag_res = drag(10, 10, 80, 80)
    assert "Dragged" in drag_res

    scroll_res = scroll(0, 10)
    assert "Scrolled" in scroll_res

    type_res = type_text("test input")
    assert "Typed" in type_res

    type_res_coords = type_text("test input", x=50, y=50)
    assert "Clicked" in type_res_coords
    assert "Typed" in type_res_coords


async def test_opt_in_desktop_installer_registration():
    from fastmcp import FastMCP

    from wayland_computer_use_mcp.tools.system_tools import register_desktop_installer_tools

    opt_mcp = FastMCP("opt-in-test")
    register_desktop_installer_tools(opt_mcp)
    names = {t.name for t in await opt_mcp.list_tools()}
    assert {"install_to_desktop", "uninstall_from_desktop"} <= names


def test_active_app_protection(monkeypatch):
    import pytest

    from wayland_computer_use_mcp.config import get_config
    from wayland_computer_use_mcp.portal import global_portal_session

    # Force live mode checks
    monkeypatch.setattr(global_portal_session, "_is_mock", False)
    monkeypatch.setattr(get_config(), "mock_mode", False)
    monkeypatch.setattr(global_portal_session, "pipewire_node_id", 42)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    global_portal_session.target_pid = None

    with pytest.raises(RuntimeError, match="No active managed application"):
        capture_window_frame()

    with pytest.raises(RuntimeError, match="No active managed application"):
        click(10, 10)

    with pytest.raises(RuntimeError, match="No active managed application"):
        inspect_ui_tree()
