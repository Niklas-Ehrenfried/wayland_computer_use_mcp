"""Tests for artifacts management, presets workflows, and InteractionResult."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from wayland_computer_use_mcp.artifacts import get_artifacts_dir, get_repo_root
from wayland_computer_use_mcp.delta import InteractionResult, wrap_with_delta
from wayland_computer_use_mcp.presets import (
    delete_preset,
    execute_preset,
    get_preset,
    list_presets,
    save_preset,
)
from wayland_computer_use_mcp.tools.preset_tools import preset_workflow


def test_artifacts_directory_resolution(tmp_path, monkeypatch):
    monkeypatch.delenv("WAYLAND_MCP_SAVE_FRAMES_DIR", raising=False)
    repo = get_repo_root()
    assert repo.exists()

    screenshots_dir = get_artifacts_dir("screenshots")
    assert screenshots_dir.exists()
    assert "screenshots" in str(screenshots_dir)

    presets_dir = get_artifacts_dir("presets")
    assert presets_dir.exists()
    assert "presets" in str(presets_dir)

    # Custom env override
    custom_dir = tmp_path / "custom_screenshots"
    monkeypatch.setenv("WAYLAND_MCP_SAVE_FRAMES_DIR", str(custom_dir))
    resolved_custom = get_artifacts_dir("screenshots")
    assert resolved_custom == custom_dir
    assert custom_dir.exists()


def test_presets_lifecycle(tmp_path, monkeypatch):
    presets_dir = tmp_path / "presets"
    monkeypatch.setattr(
        "wayland_computer_use_mcp.presets.get_artifacts_dir",
        lambda subdir: presets_dir,
    )
    presets_dir.mkdir(parents=True, exist_ok=True)

    # 1. Initially empty
    assert list_presets() == []

    # 2. Save preset
    steps = [
        {"action": "interact", "node_id": "b1"},
        {"action": "type", "node_id": "e1", "text": "hello"},
    ]
    res_save = save_preset(
        name="test_macro",
        steps=steps,
        description="Test Macro Workflow",
        target_app="test_gui_app.py",
    )
    assert res_save["status"] == "saved"
    assert res_save["name"] == "test_macro"
    assert res_save["steps_count"] == 2

    # 3. List presets
    listed = list_presets()
    assert len(listed) == 1
    assert listed[0]["name"] == "test_macro"
    assert listed[0]["description"] == "Test Macro Workflow"
    assert listed[0]["steps_count"] == 2

    # 4. Get preset
    data = get_preset("test_macro")
    assert data["name"] == "test_macro"
    assert len(data["steps"]) == 2

    # 5. Execute preset (mock batch_actions)
    with patch(
        "wayland_computer_use_mcp.tools.navigation_tools.batch_actions",
        return_value={"status": "completed", "executed_steps_count": 2},
    ) as mock_batch:
        exec_res = execute_preset("test_macro", pid=9999)
        assert exec_res["status"] == "completed"
        assert exec_res["preset"] == "test_macro"
        mock_batch.assert_called_once_with(steps, pid=9999)

    # 6. Delete preset
    del_res = delete_preset("test_macro")
    assert del_res["status"] == "deleted"
    assert list_presets() == []

    # 7. Non-existent get/delete raises FileNotFoundError
    with pytest.raises(FileNotFoundError):
        get_preset("test_macro")

    with pytest.raises(FileNotFoundError):
        delete_preset("test_macro")


def test_presets_validation():
    # Invalid name with spaces/special characters
    with pytest.raises(ValueError, match="Invalid preset name"):
        save_preset("invalid name with spaces", steps=[{"action": "click"}])

    # Empty steps
    with pytest.raises(ValueError, match="non-empty list"):
        save_preset("valid_name", steps=[])

    # Invalid step schema (missing action)
    with pytest.raises(ValueError, match="at least an 'action' key"):
        save_preset("valid_name", steps=[{"node_id": "b1"}])


def test_preset_workflow_tool(tmp_path, monkeypatch):
    presets_dir = tmp_path / "presets"
    monkeypatch.setattr(
        "wayland_computer_use_mcp.presets.get_artifacts_dir",
        lambda subdir: presets_dir,
    )
    presets_dir.mkdir(parents=True, exist_ok=True)

    # 1. Call without arguments -> list
    res_list = preset_workflow()
    assert res_list["status"] == "success"
    assert res_list["action"] == "list"
    assert res_list["presets_count"] == 0

    # 2. Save via tool
    res_save = preset_workflow(
        action="save",
        name="quick_login",
        description="Quick login macro",
        steps=[{"action": "click", "node_id": "b1"}],
    )
    assert res_save["status"] == "saved"

    # 3. View via tool
    res_view = preset_workflow(action="view", name="quick_login")
    assert res_view["status"] == "success"
    assert res_view["preset"]["name"] == "quick_login"

    # 4. Run via tool
    with patch(
        "wayland_computer_use_mcp.tools.navigation_tools.batch_actions",
        return_value={"status": "completed", "executed_steps_count": 1},
    ):
        res_run = preset_workflow(name="quick_login")
        assert res_run["status"] == "completed"

    # 5. Delete via tool
    res_del = preset_workflow(action="delete", name="quick_login")
    assert res_del["status"] == "deleted"

    # 6. Invalid action
    with pytest.raises(ValueError, match="Unknown preset action"):
        preset_workflow(action="unsupported_action", name="dummy")


def test_interaction_result_backwards_compatibility():
    # Construct an InteractionResult dictionary
    result = InteractionResult(
        status="success",
        action="click",
        target="b1",
        result="Clicked left on 'Submit' [b1] at (100, 200)",
        ui_changes={
            "modified": [{"id": "e1", "description": "text changed"}],
            "appeared": [{"id": "sw1", "name": "Dark Mode"}],
            "hidden": [],
        },
    )

    # 1. Key access works
    assert result["status"] == "success"
    assert result["action"] == "click"
    assert len(result["ui_changes"]["modified"]) == 1

    # 2. Membership test for keys
    assert "status" in result
    assert "ui_changes" in result

    # 3. Membership test for string contents in result or target
    assert "Clicked" in result
    assert "Submit" in result
    assert "b1" in result

    # 4. Membership test for elements inside ui_changes
    assert "Dark Mode" in result
    assert "sw1" in result

    # 5. Stringification produces formatted result
    str_repr = str(result)
    assert "Clicked left on 'Submit'" in str_repr


def test_wrap_with_delta_returns_interaction_result():
    res = wrap_with_delta(
        pid=0,
        action_fn=lambda: "Action performed cleanly",
        action_name="test_action",
        target="target_element",
    )
    assert isinstance(res, InteractionResult)
    assert res["status"] == "success"
    assert res["action"] == "test_action"
    assert res["target"] == "target_element"
    assert "Action performed cleanly" in res
