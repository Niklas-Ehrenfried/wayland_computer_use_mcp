"""Persistent preset macro workflows for wayland-computer-use-mcp.

Allows saving, listing, viewing, executing, and deleting multi-step interaction presets
stored as JSON artifacts under .agents/artifacts/presets/<name>.json.
"""

from __future__ import annotations

import datetime
import json
import logging
import re
from typing import Any

from wayland_computer_use_mcp.artifacts import get_artifacts_dir

logger = logging.getLogger("wayland_computer_use_mcp.presets")

_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]+$")


def _sanitize_name(name: str) -> str:
    cleaned = name.strip()
    if cleaned.endswith(".json"):
        cleaned = cleaned[:-5]
    if not _NAME_PATTERN.match(cleaned):
        raise ValueError(
            f"Invalid preset name '{name}'. Names must contain only alphanumeric characters, "
            "underscores, and hyphens."
        )
    return cleaned


def list_presets() -> list[dict[str, Any]]:
    """Lists all saved interaction presets under .agents/artifacts/presets/."""
    presets_dir = get_artifacts_dir("presets")
    result: list[dict[str, Any]] = []

    for path in sorted(presets_dir.glob("*.json")):
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            result.append(
                {
                    "name": data.get("name", path.stem),
                    "description": data.get("description", ""),
                    "target_app": data.get("target_app"),
                    "steps_count": len(data.get("steps", [])),
                    "updated_at": data.get("updated_at", ""),
                    "file_path": str(path),
                }
            )
        except Exception as exc:
            logger.warning("Failed to parse preset file %s: %s", path, exc)

    return result


def get_preset(name: str) -> dict[str, Any]:
    """Retrieves a full preset workflow definition by name."""
    clean_name = _sanitize_name(name)
    preset_path = get_artifacts_dir("presets") / f"{clean_name}.json"
    if not preset_path.is_file():
        raise FileNotFoundError(f"Preset '{clean_name}' not found at {preset_path}.")

    with open(preset_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_preset(
    name: str,
    steps: list[dict[str, Any]],
    description: str | None = None,
    target_app: str | None = None,
) -> dict[str, Any]:
    """Saves or updates an interaction workflow preset."""
    clean_name = _sanitize_name(name)
    if not isinstance(steps, list) or not steps:
        raise ValueError("Preset must contain a non-empty list of action 'steps'.")

    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict) or "action" not in step:
            raise ValueError(
                f"Step {idx} must be a dictionary containing at least an 'action' key."
            )

    preset_path = get_artifacts_dir("presets") / f"{clean_name}.json"
    now_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

    payload = {
        "name": clean_name,
        "description": description or f"Preset macro for {clean_name}",
        "target_app": target_app,
        "steps": steps,
        "updated_at": now_iso,
    }

    with open(preset_path, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    return {
        "status": "saved",
        "name": clean_name,
        "file_path": str(preset_path),
        "steps_count": len(steps),
        "updated_at": now_iso,
    }


def delete_preset(name: str) -> dict[str, Any]:
    """Deletes an interaction workflow preset artifact."""
    clean_name = _sanitize_name(name)
    preset_path = get_artifacts_dir("presets") / f"{clean_name}.json"
    if not preset_path.is_file():
        raise FileNotFoundError(f"Preset '{clean_name}' not found at {preset_path}.")

    preset_path.unlink()
    return {
        "status": "deleted",
        "name": clean_name,
        "file_path": str(preset_path),
    }


def execute_preset(name: str, pid: int | None = None) -> dict[str, Any]:
    """Loads a preset workflow and executes it atomically via batch_actions."""
    preset_data = get_preset(name)
    steps = preset_data.get("steps", [])

    from wayland_computer_use_mcp.tools.navigation_tools import batch_actions

    batch_res = batch_actions(steps, pid=pid)
    batch_res["preset"] = preset_data.get("name", name)
    batch_res["description"] = preset_data.get("description", "")
    return batch_res
