"""Desktop integration, worktree detection, and Pillow-based icon badging.

Provides:
- detect_worktree: identifies whether execution path resides in an active git worktree
- badge_icon: composites a dev/version pill badge on application icons (256x256 RGBA)
- install_desktop_app: generates and registers XDG .desktop launcher with virtualenv Exec
- remove_desktop_app: cleanly unregisters .desktop launchers and badged icons
"""

from __future__ import annotations

import logging
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
    """Detects if target_path resides within a git worktree or dev agent directory.

    Checks:
    1. git rev-parse --git-dir output for '/worktrees/'
    2. Parent directory naming conventions (.worktree*, .claude*, .cursor*)
    Returns (is_worktree, identifier_name).
    """
    path = target_path.resolve()
    parent = path.parent if path.is_file() or path.suffix else path

    # 1. Check via git rev-parse
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
                branch_or_id = Path(git_dir).name
                return True, branch_or_id
    except Exception:
        pass

    # 2. Fallback: inspect directory hierarchy for dev/worktree prefixes
    check_dirs = [parent, *parent.parents]
    for d in check_dirs:
        name_lower = d.name.lower()
        if name_lower.startswith((".worktree", ".claude", ".cursor")) or "worktree" in name_lower:
            return True, d.name

    return False, ""


def badge_icon(src_icon_path: Path | None, dest_icon_path: Path, badge_text: str) -> None:
    """Generates a 256x256 RGBA PNG icon with a rounded rectangle pill badge.

    The badge is positioned in the bottom-right corner with fill=(35, 35, 35, 230)
    and white centered text (max 6 characters).
    """
    dest_icon_path.parent.mkdir(parents=True, exist_ok=True)

    # 1. Base image
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

    # 2. Composite pill badge in bottom-right corner
    text = badge_text[:6].upper()
    overlay = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    font = ImageFont.load_default()

    # Pill dimensions & placement
    pad_x = 10
    pad_y = 6
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]

    pill_w = max(60, text_w + pad_x * 2)
    pill_h = max(26, text_h + pad_y * 2)

    x2 = 246
    y2 = 246
    x1 = x2 - pill_w
    y1 = y2 - pill_h

    # Draw rounded rectangle pill badge
    draw.rounded_rectangle(
        [x1, y1, x2, y2],
        radius=8,
        fill=(35, 35, 35, 230),
        outline=(255, 255, 255, 180),
        width=1,
    )

    # Draw centered text
    text_x = x1 + (pill_w - text_w) // 2
    text_y = y1 + (pill_h - text_h) // 2
    draw.text((text_x, text_y), text, fill=(255, 255, 255, 255), font=font)

    # Merge and save
    final_img = Image.alpha_composite(base, overlay)
    final_img.save(dest_icon_path, format="PNG")


def _generate_default_icon(label: str) -> Image.Image:
    """Generates an aesthetic 256x256 default application icon."""
    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([16, 16, 240, 240], radius=32, fill=(41, 128, 185, 255))
    font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), label, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text((128 - tw // 2, 128 - th // 2), label, fill=(255, 255, 255, 255), font=font)
    return img


def install_desktop_app(
    app_id: str,
    name: str,
    exec_path: str,
    icon_path: str | None = None,
    version: str | None = None,
    is_dev: bool | None = None,
) -> dict[str, Any]:
    """Registers a script as a native Linux desktop application (.desktop).

    Auto-detects worktree state to badge dev icons and suffix names.
    Uses auto-detected virtualenv Python executable for the Exec line.
    """
    target_exec = Path(exec_path).resolve()

    if is_dev is None:
        worktree_active, _ = detect_worktree(target_exec)
        is_dev = worktree_active

    effective_app_id = f"{app_id}-dev" if is_dev else app_id
    effective_name = f"{name} (Dev)" if is_dev else name
    badge_label = version or ("DEV" if is_dev else "")

    # Resolve Python interpreter
    python_exec = find_virtualenv_python(target_exec)

    # Icon handling
    ICONS_DIR.mkdir(parents=True, exist_ok=True)
    icon_target_path = ICONS_DIR / f"{effective_app_id}.png"
    src_icon = Path(icon_path).resolve() if icon_path else None

    if is_dev or badge_label:
        badge_icon(src_icon, icon_target_path, badge_label or "DEV")
    elif src_icon and src_icon.is_file():
        shutil.copyfile(src_icon, icon_target_path)
    else:
        badge_icon(None, icon_target_path, name[:3])

    # Generate .desktop file
    APPLICATIONS_DIR.mkdir(parents=True, exist_ok=True)
    desktop_file_path = APPLICATIONS_DIR / f"{effective_app_id}.desktop"

    content = f"""[Desktop Entry]
Type=Application
Version=1.0
Name={effective_name}
Exec="{python_exec}" "{target_exec}"
Path={target_exec.parent}
Icon={icon_target_path}
Terminal=false
Categories=Development;Utility;
StartupNotify=true
"""

    with open(desktop_file_path, "w", encoding="utf-8") as f:
        f.write(content)

    # Update system desktop database
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

        icon_file = ICONS_DIR / f"{cid}.png"
        if icon_file.exists():
            icon_file.unlink()
            removed.append(str(icon_file))

    # Update desktop database
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
