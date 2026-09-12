"""Wayland ScreenCast & RemoteDesktop portal pipeline.

Provides high-level session management, PipeWire/GStreamer screen capture, and libei input dispatch.
"""

from __future__ import annotations

from wayland_computer_use_mcp.portal.input import InputDispatcher
from wayland_computer_use_mcp.portal.remotedesktop import (
    AsyncLoopThread,
    RemoteDesktopClient,
)
from wayland_computer_use_mcp.portal.screencast import (
    ScreenCastPipeline,
    crop_element,
)
from wayland_computer_use_mcp.portal.session import PortalSession

# Global singleton instance
global_portal_session = PortalSession()

__all__ = [
    "AsyncLoopThread",
    "InputDispatcher",
    "PortalSession",
    "RemoteDesktopClient",
    "ScreenCastPipeline",
    "crop_element",
    "global_portal_session",
]
