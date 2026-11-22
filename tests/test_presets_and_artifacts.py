"""Tests for artifacts management, pruning, preset workflows, and InteractionResult."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from wayland_computer_use_mcp import artifacts
from wayland_computer_use_mcp.a11y.tree import _node_cache
from wayland_computer_use_mcp.artifacts import get_artifacts_dir, get_repo_root, prune_artifacts
from wayland_computer_use_mcp.delta import InteractionResult, wrap_with_delta
from wayland_computer_use_mcp.presets import (
    delete_preset,
    execute_preset,
    get_preset,
    list_presets,
    save_preset,
)
from wayland_computer_use_mcp.tools.preset_tools import preset_workflow


def test_artifacts_directory_markers_xdg_and_readonly_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("WAYLAND_MCP_SAVE_FRAMES_DIR", raising=False)
    monkeypatch.delenv("WAYLAND_MCP_ARTIFACTS_DIR", raising=False)
    monkeypatch.delenv("WAYLAND_MCP_PROJECT_DIR", raising=False)
    assert get_repo_root().exists()

    custom_screenshots = tmp_path / "custom_screenshots"
    monkeypatch.setenv("WAYLAND_MCP_SAVE_FRAMES_DIR", str(custom_screenshots))
    assert get_artifacts_dir("screenshots") == custom_screenshots and custom_screenshots.exists()

    # Project markers & WAYLAND_MCP_PROJECT_DIR
    monkeypatch.delenv("WAYLAND_MCP_SAVE_FRAMES_DIR", raising=False)
    proj = tmp_path / "rust_app"
    proj.mkdir()
    monkeypatch.setenv("WAYLAND_MCP_PROJECT_DIR", str(proj))
    assert get_artifacts_dir("screenshots") == proj / ".agents" / "artifacts" / "screenshots"

    monkeypatch.delenv("WAYLAND_MCP_PROJECT_DIR", raising=False)
    web_proj = tmp_path / "web_app"
    nested = web_proj / "src" / "components"
    nested.mkdir(parents=True)
    (web_proj / "package.json").write_text('{"name":"demo"}\n')
    monkeypatch.chdir(nested)
    assert get_artifacts_dir("presets") == web_proj / ".agents" / "artifacts" / "presets"

    # XDG cache fallback outside workspace
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg_cache"))
    monkeypatch.setattr("wayland_computer_use_mcp.artifacts._find_workspace_root", lambda: None)
    assert str(get_artifacts_dir("screenshots")).startswith(str(tmp_path / "xdg_cache"))

    # Read-only workspace fallback
    ro_ws = tmp_path / "ro_repo"
    ro_ws.mkdir()
    monkeypatch.setattr(artifacts, "_find_workspace_root", lambda: ro_ws)
    orig_mkdir = artifacts.Path.mkdir

    def fake_mkdir(self, *args, **kwargs):
        if str(ro_ws) in str(self):
            raise PermissionError("read-only mount")
        return orig_mkdir(self, *args, **kwargs)

    monkeypatch.setattr(artifacts.Path, "mkdir", fake_mkdir)
    resolved = get_artifacts_dir("screenshots")
    assert (
        resolved
        == tmp_path / "xdg_cache" / "wayland-computer-use-mcp" / "artifacts" / "screenshots"
    )

    # Rolling screenshot pruning
    prune_dir = tmp_path / "prune"
    prune_dir.mkdir()
    monkeypatch.setenv("WAYLAND_MCP_SAVE_FRAMES_DIR", str(prune_dir))
    for i in range(6):
        f = prune_dir / f"frame_{i}.png"
        f.write_bytes(b"png")
        os.utime(f, (1000 + i, 1000 + i))
    assert prune_artifacts("screenshots", max_files=3, max_age_hours=0) == 3
    assert sorted(p.name for p in prune_dir.iterdir()) == [
        "frame_3.png",
        "frame_4.png",
        "frame_5.png",
    ]

    # Config-driven defaults when max_files / max_age_hours are omitted
    from wayland_computer_use_mcp.config import get_config

    cfg = get_config()
    monkeypatch.setattr(cfg, "max_screenshots", 2)
    monkeypatch.setattr(cfg, "artifact_ttl_hours", 0.0)
    assert prune_artifacts("screenshots") == 1
    assert sorted(p.name for p in prune_dir.iterdir()) == ["frame_4.png", "frame_5.png"]


def test_presets_lifecycle_validation_and_traversal_guard(tmp_path, monkeypatch):
    presets_dir = tmp_path / "presets"
    monkeypatch.setattr("wayland_computer_use_mcp.presets.get_artifacts_dir", lambda s: presets_dir)
    presets_dir.mkdir(parents=True, exist_ok=True)

    assert list_presets() == []
    steps = [
        {"action": "interact", "node_id": "b1"},
        {"action": "type", "node_id": "e1", "text": "hello"},
    ]
    saved = save_preset(
        "test_macro", steps=steps, description="Test Macro Workflow", target_app="test_gui_app.py"
    )
    assert saved["status"] == "saved" and saved["steps_count"] == 2
    assert list_presets()[0]["name"] == "test_macro"
    assert len(get_preset("test_macro")["steps"]) == 2

    with patch(
        "wayland_computer_use_mcp.tools.navigation_tools.batch_actions",
        return_value={"status": "completed", "executed_steps_count": 2},
    ) as mock_batch:
        exec_res = execute_preset("test_macro", pid=9999)
        assert exec_res["status"] == "completed" and exec_res["preset"] == "test_macro"
        mock_batch.assert_called_once_with(steps, pid=9999)

    assert delete_preset("test_macro")["status"] == "deleted"
    assert list_presets() == []
    with pytest.raises(FileNotFoundError):
        get_preset("test_macro")
    with pytest.raises(FileNotFoundError):
        delete_preset("test_macro")

    for bad_name in ("invalid name with spaces", "../../etc/passwd", "../escape"):
        with pytest.raises(ValueError, match="Invalid preset name"):
            save_preset(bad_name, steps=[{"action": "click"}])
    with pytest.raises(ValueError, match="non-empty list"):
        save_preset("valid_name", steps=[])
    with pytest.raises(ValueError, match="at least an 'action' key"):
        save_preset("valid_name", steps=[{"node_id": "b1"}])


def test_preset_workflow_tool_and_positional_id_normalization(tmp_path, monkeypatch):
    monkeypatch.setenv("WAYLAND_MCP_ARTIFACTS_DIR", str(tmp_path))

    assert preset_workflow()["action"] == "list"
    _node_cache[8888] = {"b1": {"id": "b1", "name": "Apply Changes", "role": "push button"}}
    saved = preset_workflow(
        action="save",
        name="deploy_flow",
        description="Apply and check",
        steps=[{"action": "click", "target": "b1"}, {"action": "click", "target": "b99"}],
        pid=8888,
    )
    assert saved["normalized_steps"] == 1
    assert "warnings" in saved and "b99" in saved["warnings"][0]

    loaded = preset_workflow(action="view", name="deploy_flow")["preset"]
    assert loaded["steps"][0] == {
        "action": "click",
        "target": "Apply Changes",
        "role": "push button",
    }
    assert loaded["steps"][1]["target"] == "b99"

    with patch(
        "wayland_computer_use_mcp.tools.navigation_tools.batch_actions",
        return_value={"status": "completed", "executed_steps_count": 2},
    ):
        assert preset_workflow(name="deploy_flow")["status"] == "completed"

    assert preset_workflow(action="delete", name="deploy_flow")["status"] == "deleted"
    with pytest.raises(ValueError, match="Unknown preset action"):
        preset_workflow(action="unsupported_action", name="dummy")


def test_interaction_result_backwards_compatibility():
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
    assert result["status"] == "success" and "status" in result
    assert "Clicked" in result and "Submit" in result and "Dark Mode" in result and "sw1" in result
    assert "Clicked left on 'Submit'" in str(result)

    wrapped = wrap_with_delta(
        pid=0,
        action_fn=lambda: "Action performed cleanly",
        action_name="test_action",
        target="target_element",
    )
    assert isinstance(wrapped, InteractionResult) and "Action performed cleanly" in wrapped
