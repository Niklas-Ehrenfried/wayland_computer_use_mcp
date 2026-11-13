"""Desktop integration, worktree detection, and universal .desktop launcher builder.

Provides:
- detect_worktree: identifies whether execution path resides in an active git worktree
- badge_icon: composites a dev/version pill badge on application icons (256x256 RGBA)
- install_desktop_app: generates and registers universal XDG .desktop shortcuts for
  Python scripts, shell scripts, AppImages, binaries, Flatpak apps, or CLI commands
- remove_desktop_app: cleanly unregisters .desktop launchers and badged icons
"""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont

from wayland_computer_use_mcp.process import find_virtualenv_python

logger = logging.getLogger(__name__)

APPLICATIONS_DIR = Path.home() / ".local" / "share" / "applications"
ICONS_DIR = Path.home() / ".local" / "share" / "icons" / "hicolor" / "256x256" / "apps"


def detect_worktree(target_path: Path) -> tuple[bool, str]:
    """Detects if target_path resides within a git worktree or dev agent directory."""
    path = target_path.resolve() if target_path.exists() else target_path
    parent = path.parent if path.is_file() or path.suffix else path

    try:
        res = subprocess.run(
            ["git", "-C", str(parent), "rev-parse", "--git-dir"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        if res.returncode == 0:
            git_dir = res.stdout.strip()
            if "/worktrees/" in git_dir:
                return True, Path(git_dir).name
    except Exception:
        pass

    check_dirs = [parent, *parent.parents]
    for d in check_dirs:
        name_lower = d.name.lower()
        if name_lower.startswith((".worktree", ".claude", ".cursor")) or "worktree" in name_lower:
            return True, d.name

    return False, ""


def badge_icon(src_icon_path: Path | None, dest_icon_path: Path, badge_text: str) -> None:
    """Generates a 256x256 RGBA PNG icon with a rounded rectangle pill badge."""
    dest_icon_path.parent.mkdir(parents=True, exist_ok=True)

    if src_icon_path and src_icon_path.is_file():
        try:
            base = Image.open(src_icon_path).convert("RGBA")
            base = base.resize((256, 256), Image.Resampling.LANCZOS)
        except Exception as exc:
            logger.warning(
                "Could not open source icon (%s); generating fallback: %s",
                src_icon_path,
                exc,
            )
            base = _generate_default_icon("APP")
    else:
        base = _generate_default_icon(badge_text[:3] or "APP")

    text = badge_text[:6].upper()
    overlay = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = ImageFont.load_default()

    pad_x = 10
    pad_y = 6
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    pill_w = max(60, text_w + pad_x * 2)
    pill_h = max(26, text_h + pad_y * 2)
    x2, y2 = 246, 246
    x1, y1 = x2 - pill_w, y2 - pill_h

    draw.rounded_rectangle(
        [x1, y1, x2, y2],
        radius=8,
        fill=(35, 35, 35, 230),
        outline=(255, 255, 255, 180),
        width=1,
    )
    text_x = x1 + (pill_w - text_w) // 2
    text_y = y1 + (pill_h - text_h) // 2
    draw.text((text_x, text_y), text, fill=(255, 255, 255, 255), font=font)

    final_img = Image.alpha_composite(base, overlay)
    final_img.save(dest_icon_path, format="PNG")


def _generate_default_icon(label: str) -> Image.Image:
    """Generates a clean 256x256 default monogram icon."""
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([16, 16, 240, 240], radius=32, fill=(41, 128, 185, 255))
    font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), label, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text((128 - tw // 2, 128 - th // 2), label, fill=(255, 255, 255, 255), font=font)
    return img


def _build_exec_command(
    exec_path: str,
    args: list[str] | None = None,
) -> tuple[str, Path, bool]:
    """Resolves a universal Exec string, working directory, and whether it's a local path.

    Supports Python scripts (auto-venv), shell scripts, AppImages, native binaries,
    Node/JS scripts, Flatpak IDs, PATH commands, and URLs (xdg-open).
    """
    extra = f" {' '.join(shlex.quote(a) for a in args)}" if args else ""

    if exec_path.startswith(("http://", "https://")):
        return f"xdg-open {shlex.quote(exec_path)}{extra}", Path.home(), False

    if exec_path.startswith("flatpak:"):
        flatpak_id = exec_path.split(":", 1)[1].strip()
        return f"flatpak run {shlex.quote(flatpak_id)}{extra}", Path.home(), False

    candidate = Path(exec_path).expanduser()
    if candidate.exists() or "/" in exec_path or candidate.suffix:
        resolved = candidate.resolve()
        work_dir = resolved.parent if resolved.parent.exists() else Path.home()
        suffix = resolved.suffix.lower()

        if suffix == ".py":
            python_exec = find_virtualenv_python(resolved)
            return (
                f"{shlex.quote(str(python_exec))} {shlex.quote(str(resolved))}{extra}",
                work_dir,
                True,
            )
        if suffix in (".sh", ".bash"):
            if resolved.exists() and not os.access(resolved, os.X_OK):
                return f"bash {shlex.quote(str(resolved))}{extra}", work_dir, True
            return f"{shlex.quote(str(resolved))}{extra}", work_dir, True
        if suffix in (".js", ".mjs", ".cjs"):
            node_bin = shutil.which("node") or "node"
            return f"{shlex.quote(node_bin)} {shlex.quote(str(resolved))}{extra}", work_dir, True
        if suffix == ".appimage" and resolved.exists() and not os.access(resolved, os.X_OK):
            try:
                resolved.chmod(resolved.stat().st_mode | 0o111)
            except OSError:
                pass

        return f"{shlex.quote(str(resolved))}{extra}", work_dir, True

    # Flatpak reverse-DNS ID (without local path)
    if exec_path.count(".") >= 2 and shutil.which("flatpak"):
        return f"flatpak run {shlex.quote(exec_path)}{extra}", Path.home(), False

    which_path = shutil.which(exec_path)
    cmd = which_path or exec_path
    return f"{shlex.quote(cmd)}{extra}", Path.home(), False


def _discover_nearby_icon(work_dir: Path) -> Path | None:
    """Looks for common icon files next to the target application."""
    for rel in (
        "icon.png",
        "icon.svg",
        "logo.png",
        "assets/icon.png",
        "assets/icon.svg",
    ):
        candidate = work_dir / rel
        if candidate.is_file():
            return candidate
    return None


def install_desktop_app(
    app_id: str,
    name: str,
    exec_path: str,
    icon_path: str | None = None,
    version: str | None = None,
    is_dev: bool | None = None,
    args: list[str] | None = None,
    comment: str | None = None,
    categories: str = "Utility;",
    terminal: bool = False,
) -> dict[str, Any]:
    """Creates a one-click Linux desktop application shortcut (.desktop) for any app or script.

    Supports Python scripts (auto-virtualenv), binaries, AppImages, shell scripts,
    Flatpak IDs, URLs, and PATH commands, with optional version/worktree icon badging.
    """
    exec_cmd, work_dir, is_local_path = _build_exec_command(exec_path, args=args)

    if is_dev is None:
        worktree_active, _ = detect_worktree(work_dir) if is_local_path else (False, "")
        is_dev = worktree_active

    effective_app_id = f"{app_id}-dev" if is_dev else app_id
    effective_name = f"{name} (Dev)" if is_dev else name
    badge_label = version or ("DEV" if is_dev else "")

    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    src_icon = (
        Path(icon_path).expanduser().resolve()
        if icon_path
        else (_discover_nearby_icon(work_dir) if is_local_path else None)
    )

    if (
        src_icon
        and src_icon.suffix.lower() == ".svg"
        and src_icon.is_file()
        and not (is_dev or badge_label)
    ):
        icon_target_path = ICONS_DIR / f"{effective_app_id}.svg"
        shutil.copyfile(src_icon, icon_target_path)
    else:
        icon_target_path = ICONS_DIR / f"{effective_app_id}.png"
        if is_dev or badge_label:
            badge_icon(src_icon, icon_target_path, badge_label or "DEV")
        elif src_icon and src_icon.is_file():
            shutil.copyfile(src_icon, icon_target_path)
        else:
            monogram = "".join(word[0] for word in name.split()[:2]).upper() or name[:3].upper()
            icon_img = _generate_default_icon(monogram)
            icon_img.save(icon_target_path, format="PNG")

    APPLICATIONS_DIR.mkdir(parents=True, exist_ok=True)
    desktop_file_path = APPLICATIONS_DIR / f"{effective_app_id}.desktop"

    lines = [
        "[Desktop Entry]",
        "Type=Application",
        "Version=1.0",
        f"Name={effective_name}",
        f"Exec={exec_cmd}",
        f"Path={work_dir}",
        f"Icon={icon_target_path}",
        f"Terminal={'true' if terminal else 'false'}",
        f"Categories={categories}",
        "StartupNotify=true",
    ]
    if comment:
        lines.append(f"Comment={comment}")

    with open(desktop_file_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")

    try:
        subprocess.run(
            ["update-desktop-database", str(APPLICATIONS_DIR)],
            capture_output=True,
            timeout=2.0,
        )
    except Exception:
        pass

    return {
        "status": "installed",
        "app_id": effective_app_id,
        "name": effective_name,
        "exec": exec_cmd,
        "desktop_file": str(desktop_file_path),
        "icon_file": str(icon_target_path),
        "is_dev": is_dev,
    }


def remove_desktop_app(app_id: str) -> dict[str, Any]:
    """Removes the application launcher and its associated icons from the OS desktop menu."""
    removed = []

    candidates = [app_id, f"{app_id}-dev"]
    for cid in candidates:
        desktop_file = APPLICATIONS_DIR / f"{cid}.desktop"
        if desktop_file.exists():
            desktop_file.unlink()
            removed.append(str(desktop_file))

        for ext in (".png", ".svg"):
            icon_file = ICONS_DIR / f"{cid}{ext}"
            if icon_file.exists():
                icon_file.unlink()
                removed.append(str(icon_file))

    try:
        subprocess.run(
            ["update-desktop-database", str(APPLICATIONS_DIR)],
            capture_output=True,
            timeout=2.0,
        )
    except Exception:
        pass

    return {
        "status": "uninstalled",
        "app_id": app_id,
        "removed_files": removed,
    }
