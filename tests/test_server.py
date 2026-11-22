"""Tests for FastMCP server tool registrations, schemas, consolidated inputs, and cleanup."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from wayland_computer_use_mcp import __version__
from wayland_computer_use_mcp.config import get_config
from wayland_computer_use_mcp.delta import InteractionResult
from wayland_computer_use_mcp.portal import global_portal_session
from wayland_computer_use_mcp.server import (
    _cleanup_server_resources,
    capture_window_frame,
    click,
    drag,
    hover,
    inspect_ui_tree,
    keyboard,
    mcp,
    mouse,
    scroll,
    type_text,
    wayland_automation_guide,
)


def test_package_version_and_bundled_prompt():
    assert __version__ == "0.3.0"
    prompt = wayland_automation_guide()
    assert "interact_with_node" in prompt
    assert "click_element_by_label" not in prompt


async def test_registered_tools():
    # Active high-leverage exposed MCP tools (18 default tools; desktop installer is opt-in)
    exposed_tool_names = [
        "launch_app",
        "restart_app",
        "terminate_app",
        "get_app_logs",
        "get_session_status",
        "capture_window_frame",
        "inspect_ui_tree",
        "interact_with_node",
        "wait_for_element",
        "batch_actions",
        "mouse",
        "keyboard",
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

    assert len(registered_names) == 18
    assert "install_to_desktop" not in registered_names
    assert "uninstall_from_desktop" not in registered_names
    assert mcp.instructions and "Tree-First" in mcp.instructions

    assert tool_map["inspect_ui_tree"].annotations.read_only_hint is True
    assert tool_map["wait_for_element"].annotations.read_only_hint is True
    assert tool_map["get_session_status"].annotations.read_only_hint is True
    assert tool_map["terminate_app"].annotations.destructive_hint is True

    node_schema = tool_map["interact_with_node"].parameters["properties"]
    assert "target" in node_schema
    assert "Deprecated" in node_schema["node_id"].get("description", "")
    assert "click" in node_schema["action"]["enum"]
    assert "enum" in tool_map["window_control"].parameters["properties"]["action"]
    assert "enum" in tool_map["preset_workflow"].parameters["properties"]["action"]
    assert "enum" in tool_map["mouse"].parameters["properties"]["action"]
    assert "enum" in tool_map["mouse"].parameters["properties"]["button"]
    assert "enum" in tool_map["keyboard"].parameters["properties"]["action"]

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
    monkeypatch.setattr(get_config(), "mock_mode", True)

    img = capture_window_frame(crop_box=[10, 10, 100, 100])
    assert img is not None
    assert img.data is not None and len(img.data) > 0
    assert img.to_image_content().mime_type == "image/png"

    assert "Clicked" in click(50, 50, button="left")
    assert "Dragged" in drag(10, 10, 80, 80)
    assert "Scrolled" in scroll(0, 10)
    assert "Typed" in type_text("test input")

    type_res_coords = type_text("test input", x=50, y=50)
    assert "Clicked" in type_res_coords
    assert "Typed" in type_res_coords


def test_consolidated_mouse_and_keyboard_tools(monkeypatch):
    """Consolidated mouse/keyboard tools handle actions, validation errors, and shortcut filters."""
    monkeypatch.setattr(get_config(), "mock_mode", True)

    assert "Clicked" in mouse(action="click", x=40, y=50)["result"]
    assert "Double" in mouse(action="double_click", x=40, y=50)["result"]
    assert "right" in mouse(action="right_click", x=40, y=50)["result"].lower()
    assert "Hovered" in mouse(action="hover", x=40, y=50, duration_ms=10)["result"]
    assert "Dragged" in mouse(action="drag", x=10, y=10, end_x=80, end_y=90)["result"]
    assert "Scrolled" in mouse(action="scroll", dx=0, dy=-5)["result"]

    with pytest.raises(ValueError, match="requires 'x' and 'y'"):
        mouse(action="click")
    with pytest.raises(ValueError, match="requires 'x', 'y', 'end_x', and 'end_y'"):
        mouse(action="drag", x=10, y=10)
    with pytest.raises(ValueError, match="Unknown mouse action"):
        mouse(action="triple_click")  # type: ignore[arg-type]

    assert "Typed" in keyboard(action="type", text="hello world")["result"]
    assert "Dispatched key combination" in keyboard(action="keys", keys=["ctrl", "s"])["result"]

    with pytest.raises(ValueError, match="requires 'text'"):
        keyboard(action="type")
    with pytest.raises(ValueError, match="requires a non-empty 'keys'"):
        keyboard(action="keys")
    with pytest.raises(ValueError, match="Unknown keyboard action"):
        keyboard(action="hold")  # type: ignore[arg-type]
    with pytest.raises(PermissionError, match="blocked"):
        keyboard(action="keys", keys=["super", "d"])


def test_input_tools_always_return_interaction_result(monkeypatch):
    cfg = get_config()
    monkeypatch.setattr(cfg, "source_type", 1)
    monkeypatch.setattr(global_portal_session, "target_pid", None)

    click_res = click(100, 120)
    assert isinstance(click_res, InteractionResult)
    assert click_res["status"] == "success"
    assert "ui_changes" in click_res

    hover_res = hover(200, 220)
    assert isinstance(hover_res, InteractionResult)
    assert hover_res["status"] == "success"


async def test_opt_in_desktop_installer_registration():
    from fastmcp import FastMCP

    from wayland_computer_use_mcp.tools.system_tools import register_desktop_installer_tools

    opt_mcp = FastMCP("opt-in-test")
    register_desktop_installer_tools(opt_mcp)
    names = {t.name for t in await opt_mcp.list_tools()}
    assert {"install_to_desktop", "uninstall_from_desktop"} <= names


def test_active_app_protection(monkeypatch):
    monkeypatch.setattr(get_config(), "mock_mode", False)
    monkeypatch.setattr(get_config(), "display_mode", "live")
    monkeypatch.setattr(global_portal_session, "is_active", lambda: True)
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    global_portal_session.target_pid = None

    with pytest.raises(RuntimeError, match="No active managed application"):
        capture_window_frame()
    with pytest.raises(RuntimeError, match="No active managed application"):
        click(10, 10)
    with pytest.raises(RuntimeError, match="No active managed application"):
        inspect_ui_tree()


async def test_tool_output_schemas_match_return_types():
    """Verify FastMCP output schemas match actual return types (no wrapped-string regression)."""
    tools = {t.name: t for t in await mcp.list_tools()}

    dict_tools = {
        "launch_app",
        "restart_app",
        "terminate_app",
        "list_managed_apps",
        "get_session_status",
        "inspect_ui_tree",
        "wait_for_element",
        "batch_actions",
        "watch_ui_events",
        "window_control",
        "preset_workflow",
    }
    for name in dict_tools:
        schema = tools[name].output_schema
        assert schema is not None and schema.get("type") == "object"
        assert not schema.get("x-fastmcp-wrap-result")

    for name in {"interact_with_node", "mouse", "keyboard"}:
        schema = tools[name].output_schema
        assert schema is None or not schema.get("x-fastmcp-wrap-result")

    for name in {"get_app_logs", "clipboard_read", "clipboard_write"}:
        schema = tools[name].output_schema
        assert schema is not None
        assert schema.get("properties", {}).get("result", {}).get("type") == "string"


def test_cleanup_server_resources():
    """Verifies that server cleanup triggers portal session close and process termination."""
    with (
        patch("wayland_computer_use_mcp.portal.global_portal_session.close") as mock_close,
        patch("wayland_computer_use_mcp.process.terminate_all_processes") as mock_term_all,
    ):
        _cleanup_server_resources()
        mock_close.assert_called_once()
        mock_term_all.assert_called_once()
