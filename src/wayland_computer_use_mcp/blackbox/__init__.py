"""Blackbox isolated sandbox engine for Playwright-style Wayland execution."""

from wayland_computer_use_mcp.blackbox.adapters import (
    BlackboxCompositorAdapter,
    KWinNestedAdapter,
    WestonNestedAdapter,
    WlrootsNestedAdapter,
    select_blackbox_adapter,
)
from wayland_computer_use_mcp.blackbox.driver import BlackboxDriver
from wayland_computer_use_mcp.blackbox.wlr_wire import (
    WlrVirtualInputClient,
    capture_wlr_screencopy,
    decode_shm_frame,
)

__all__ = [
    "BlackboxCompositorAdapter",
    "BlackboxDriver",
    "KWinNestedAdapter",
    "WestonNestedAdapter",
    "WlrootsNestedAdapter",
    "WlrVirtualInputClient",
    "capture_wlr_screencopy",
    "decode_shm_frame",
    "select_blackbox_adapter",
]
