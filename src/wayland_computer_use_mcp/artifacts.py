"""Artifacts and project workspace root path resolution."""

from __future__ import annotations

import os
from pathlib import Path

_PROJECT_MARKERS = (
    ".agents",
    ".git",
    "pyproject.toml",
    "package.json",
    "Cargo.toml",
    "go.mod",
    "CMakeLists.txt",
)


def _is_non_project_dir(path: Path) -> bool:
    """Returns True for filesystem root, temp roots, or home dir without .agents."""
    resolved = path.resolve()
    if resolved == resolved.parent:  # '/'
        return True
    home = Path.home().resolve()
    if resolved == home and not (home / ".agents").is_dir():
        return True
    return False


def _find_workspace_root() -> Path | None:
    """Finds the active user project root so artifacts stay inside the project."""
    project_env = os.environ.get("WAYLAND_MCP_PROJECT_DIR")
    if project_env:
        p = Path(project_env).expanduser().resolve()
        if p.is_dir():
            return p

    cwd = Path.cwd().resolve()
    home = Path.home().resolve()
    for candidate in [cwd, *cwd.parents]:
        if candidate == candidate.parent:
            break
        if candidate == home and not (home / ".agents").is_dir():
            continue
        if any((candidate / marker).exists() for marker in _PROJECT_MARKERS):
            return candidate

    # If the user started in a new/empty project directory (not '/' or '$HOME')
    if not _is_non_project_dir(cwd) and os.access(cwd, os.W_OK):
        return cwd

    current = Path(__file__).resolve().parent
    for parent in [current, *current.parents]:
        if (parent / ".agents").is_dir() or (parent / "pyproject.toml").is_file():
            return parent
    return None


def get_repo_root() -> Path:
    """Returns active project workspace root, or XDG cache dir when running globally."""
    found = _find_workspace_root()
    if found is not None:
        return found
    cache_home = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return cache_home / "wayland-computer-use-mcp"


def get_artifacts_dir(subdir: str = "screenshots") -> Path:
    """Returns the artifact directory for screenshots or presets.

    Precedence:
    1. ``WAYLAND_MCP_SAVE_FRAMES_DIR`` when ``subdir == "screenshots"``
    2. ``WAYLAND_MCP_ARTIFACTS_DIR`` / ``subdir``
    3. ``<project_root>/.agents/artifacts/<subdir>`` (keeps project data in one place)
    4. ``$XDG_CACHE_HOME/wayland-computer-use-mcp/artifacts/<subdir>`` fallback
    """
    if subdir == "screenshots":
        env_dir = os.environ.get("WAYLAND_MCP_SAVE_FRAMES_DIR")
        if env_dir:
            p = Path(env_dir).expanduser().resolve()
            p.mkdir(parents=True, exist_ok=True)
            return p

    base_env = os.environ.get("WAYLAND_MCP_ARTIFACTS_DIR")
    if base_env:
        target_dir = Path(base_env).expanduser().resolve() / subdir
        target_dir.mkdir(parents=True, exist_ok=True)
        return target_dir

    workspace = _find_workspace_root()
    if workspace is not None:
        candidate = workspace / ".agents" / "artifacts" / subdir
        try:
            import tempfile

            candidate.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryFile(dir=candidate):
                pass
            return candidate
        except OSError:
            pass

    cache_home = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    target_dir = cache_home / "wayland-computer-use-mcp" / "artifacts" / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    return target_dir
