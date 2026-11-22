"""Artifacts and project workspace root path resolution."""

from __future__ import annotations

import os
import time
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


def prune_artifacts(
    subdir: str = "screenshots",
    max_files: int | None = None,
    max_age_hours: float | None = None,
) -> int:
    """Removes expired or excess oldest files from an artifact directory (never presets)."""
    if subdir == "presets":
        return 0
    try:
        from wayland_computer_use_mcp.config import get_config

        cfg = get_config()
        effective_max_files = cfg.max_screenshots if max_files is None else max_files
        effective_max_age = cfg.artifact_ttl_hours if max_age_hours is None else max_age_hours

        target_dir = get_artifacts_dir(subdir)
        if not target_dir.is_dir():
            return 0
        files = [p for p in target_dir.iterdir() if p.is_file()]
        if not files:
            return 0

        removed = 0
        cutoff = time.time() - max(0.0, effective_max_age) * 3600.0
        kept: list[tuple[float, Path]] = []
        for p in files:
            try:
                mtime = p.stat().st_mtime
            except OSError:
                continue
            if effective_max_age > 0 and mtime < cutoff:
                try:
                    p.unlink()
                    removed += 1
                except OSError:
                    pass
            else:
                kept.append((mtime, p))

        if effective_max_files > 0 and len(kept) > effective_max_files:
            kept.sort(key=lambda item: item[0])
            for _, p in kept[: len(kept) - effective_max_files]:
                try:
                    p.unlink()
                    removed += 1
                except OSError:
                    pass
        return removed
    except Exception:
        return 0
