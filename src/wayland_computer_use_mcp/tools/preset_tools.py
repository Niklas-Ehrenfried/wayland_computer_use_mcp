"""Preset macro workflow tools for saving, listing, viewing, and executing presets."""

from __future__ import annotations

import logging
from typing import Any

from fastmcp import FastMCP

from wayland_computer_use_mcp.presets import (
    delete_preset,
    execute_preset,
    get_preset,
    list_presets,
    save_preset,
)

logger = logging.getLogger("wayland_computer_use_mcp.tools.preset")


def preset_workflow(
    action: str = "run",
    name: str | None = None,
    description: str | None = None,
    steps: list[dict[str, Any]] | None = None,
    target_app: str | None = None,
    pid: int | None = None,
) -> dict[str, Any]:
    """Manages and executes reusable multi-step interaction macro presets.

    Actions:
    - 'list' (default if name is None): Lists all saved presets with descriptions.
    - 'run' (default if name is provided): Executes the preset sequence atomically.
    - 'view': Returns the full step sequence and metadata for the specified preset.
    - 'save': Saves or updates a preset sequence to .agents/artifacts/presets/<name>.json.
    - 'delete': Deletes a preset artifact.

    Step schema matches batch_actions:
    - {"action": "interact", "node_id": "b1"}
    - {"action": "type", "node_id": "e1", "text": "value"}
    - {"action": "click", "x": 100, "y": 200, "button": "left"}
    - {"action": "key", "keys": ["ctrl", "a"]}
    - {"action": "scroll", "dx": 0, "dy": 5}
    - {"action": "hover", "node_id": "b1"}
    - {"action": "wait", "ms": 50}
    """
    act = action.strip().lower()

    if name is None or act == "list":
        presets = list_presets()
        return {
            "status": "success",
            "action": "list",
            "presets_count": len(presets),
            "presets": presets,
        }

    if act == "view":
        data = get_preset(name)
        return {
            "status": "success",
            "action": "view",
            "preset": data,
        }

    if act == "save":
        if not steps:
            raise ValueError("Saving a preset requires a non-empty list of 'steps'.")
        return save_preset(name=name, steps=steps, description=description, target_app=target_app)

    if act == "delete":
        return delete_preset(name)

    if act == "run":
        return execute_preset(name, pid=pid)

    raise ValueError(
        f"Unknown preset action '{action}'. "
        "Supported actions: 'list', 'run', 'view', 'save', 'delete'."
    )


def register_preset_tools(mcp: FastMCP) -> None:
    """Registers preset macro workflow tools onto the FastMCP server."""
    mcp.tool()(preset_workflow)
