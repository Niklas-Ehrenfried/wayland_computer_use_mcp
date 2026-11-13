"""System clipboard abstraction supporting Portal, Wayland data-control, CLI, KDE Klipper, and X11.

Provides automatic backend discovery and fallback:
1. org.freedesktop.portal.Clipboard (XDG Portal >= 1.18 via D-Bus, zero CLI binaries)
2. Native Wayland wire protocol (ext_data_control_manager_v1 / zwlr_data_control_manager_v1)
3. wl-copy / wl-paste (Wayland native CLI)
4. qdbus6 org.kde.klipper (KDE Plasma 6)
5. qdbus org.kde.klipper (KDE Plasma 5)
6. xclip (Xwayland / X11)
"""

from __future__ import annotations

import logging
import shutil
import subprocess

logger = logging.getLogger(__name__)


def _wayland_write_clipboard(text: str) -> bool:
    try:
        from wayland_computer_use_mcp.config import get_config
        from wayland_computer_use_mcp.wayland_clipboard import write_wayland_clipboard

        if get_config().mock_mode:
            return False
        return write_wayland_clipboard(text)
    except Exception as exc:
        logger.debug("Wayland data-control clipboard write failed: %s", exc)
    return False


def _wayland_read_clipboard() -> str | None:
    try:
        from wayland_computer_use_mcp.config import get_config
        from wayland_computer_use_mcp.wayland_clipboard import read_wayland_clipboard

        if get_config().mock_mode:
            return None
        return read_wayland_clipboard()
    except Exception as exc:
        logger.debug("Wayland data-control clipboard read failed: %s", exc)
    return None


def _portal_write_clipboard(text: str) -> bool:
    try:
        from wayland_computer_use_mcp.portal import global_portal_session

        rd = getattr(global_portal_session, "_rd_client", None)
        if rd and getattr(rd, "clipboard_enabled", False):
            return bool(rd.write_clipboard_text(text))
    except Exception as exc:
        logger.debug("Portal clipboard write failed: %s", exc)
    return False


def _portal_read_clipboard() -> str | None:
    try:
        from wayland_computer_use_mcp.portal import global_portal_session

        rd = getattr(global_portal_session, "_rd_client", None)
        if rd and getattr(rd, "clipboard_enabled", False):
            return rd.read_clipboard_text()
    except Exception as exc:
        logger.debug("Portal clipboard read failed: %s", exc)
    return None


def write_system_clipboard(text: str) -> bool:
    """Writes text into system clipboard using available backends."""
    if _portal_write_clipboard(text):
        return True

    if _wayland_write_clipboard(text):
        return True

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
    portal_val = _portal_read_clipboard()
    if portal_val is not None:
        return portal_val

    wayland_val = _wayland_read_clipboard()
    if wayland_val is not None:
        return wayland_val

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
