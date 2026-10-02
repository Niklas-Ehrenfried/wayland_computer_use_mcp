"""Centralized artifact storage management for screenshots, overlays, and presets.

Provides a single source of truth under the repository's `.agents/artifacts/` folder:
- `.agents/artifacts/screenshots/`
- `.agents/artifacts/presets/`
"""

from __future__ import annotations

import os
from pathlib import Path


def get_repo_root() -> Path:
    """Finds repository root containing .agents or pyproject.toml."""
    cwd = Path.cwd().resolve()
    if (cwd / ".agents").is_dir() or (cwd / "pyproject.toml").is_file():
        return cwd

    current = Path(__file__).resolve().parent
    for parent in [current, *current.parents]:
        if (parent / ".agents").is_dir() or (parent / "pyproject.toml").is_file():
            return parent
    return cwd


def get_artifacts_dir(subdir: str = "screenshots") -> Path:
    """Returns the single source of truth directory under .agents/artifacts/<subdir>."""
    if subdir == "screenshots":
        env_dir = os.environ.get("WAYLAND_MCP_SAVE_FRAMES_DIR")
        if env_dir:
            p = Path(env_dir).resolve()
            p.mkdir(parents=True, exist_ok=True)
            return p

    repo = get_repo_root()
    target_dir = repo / ".agents" / "artifacts" / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir
