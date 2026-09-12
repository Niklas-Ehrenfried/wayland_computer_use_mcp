"""System clipboard abstraction supporting Wayland and X11 backends.

Provides automatic backend discovery and fallback:
1. wl-copy / wl-paste (Wayland native)
2. qdbus6 org.kde.klipper (KDE Plasma 6)
3. qdbus org.kde.klipper (KDE Plasma 5)
4. xclip (Xwayland / X11)
"""

from __future__ import annotations

import logging
import shutil
import subprocess

logger = logging.getLogger(__name__)


def write_system_clipboard(text: str) -> bool:
    """Writes text into system clipboard using available backends."""
    if shutil.which("wl-copy"):
        try:
            subprocess.run(
                ["wl-copy", text],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1.0,
            )
            return True
        except Exception as exc:
            logger.debug("wl-copy failed: %s", exc)

    if shutil.which("qdbus6"):
        try:
            res = subprocess.run(
                ["qdbus6", "org.kde.klipper", "/klipper", "setClipboardContents", text],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1.0,
            )
            if res.returncode == 0:
                return True
        except Exception as exc:
            logger.debug("qdbus6 klipper failed: %s", exc)

    if shutil.which("qdbus"):
        try:
            res = subprocess.run(
                ["qdbus", "org.kde.klipper", "/klipper", "setClipboardContents", text],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1.0,
            )
            if res.returncode == 0:
                return True
        except Exception as exc:
            logger.debug("qdbus klipper failed: %s", exc)

    if shutil.which("xclip"):
        try:
            subprocess.run(
                ["xclip", "-selection", "clipboard"],
                input=text.encode("utf-8"),
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1.0,
            )
            return True
        except Exception as exc:
            logger.debug("xclip failed: %s", exc)

    return False


def read_system_clipboard() -> str:
    """Reads current text from system clipboard using available backends."""
    if shutil.which("wl-paste"):
        try:
            res = subprocess.run(
                ["wl-paste", "--no-newline"],
                capture_output=True,
                text=True,
                timeout=1.0,
            )
            if res.returncode == 0:
                return res.stdout
        except Exception as exc:
            logger.debug("wl-paste read failed: %s", exc)

    if shutil.which("qdbus6"):
        try:
            res = subprocess.run(
                ["qdbus6", "org.kde.klipper", "/klipper", "getClipboardContents"],
                capture_output=True,
                text=True,
                timeout=1.0,
            )
            if res.returncode == 0:
                return res.stdout.rstrip("\r\n")
        except Exception as exc:
            logger.debug("qdbus6 klipper get failed: %s", exc)

    if shutil.which("qdbus"):
        try:
            res = subprocess.run(
                ["qdbus", "org.kde.klipper", "/klipper", "getClipboardContents"],
                capture_output=True,
                text=True,
                timeout=1.0,
            )
            if res.returncode == 0:
                return res.stdout.rstrip("\r\n")
        except Exception as exc:
            logger.debug("qdbus klipper get failed: %s", exc)

    if shutil.which("xclip"):
        try:
            res = subprocess.run(
                ["xclip", "-selection", "clipboard", "-o"],
                capture_output=True,
                text=True,
                timeout=1.0,
            )
            if res.returncode == 0:
                return res.stdout
        except Exception as exc:
            logger.debug("xclip get failed: %s", exc)

    return ""
