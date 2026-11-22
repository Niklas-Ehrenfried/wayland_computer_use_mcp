"""Modular tool registration and aggregation for wayland-computer-use-mcp."""

from __future__ import annotations

from fastmcp import Context, FastMCP


def emit_ctx_log(ctx: Context | None, message: str) -> None:
    """Safely sends a FastMCP log message from synchronous tool worker threads."""
    if ctx is None:
        return
    try:
        import anyio

        anyio.from_thread.run(ctx.info, message)
    except Exception:
        pass


def emit_ctx_progress(
    ctx: Context | None,
    progress: float,
    total: float | None = None,
    message: str | None = None,
) -> None:
    """Safely reports FastMCP tool progress from synchronous tool worker threads."""
    if ctx is None:
        return
    try:
        import anyio

        anyio.from_thread.run(ctx.report_progress, progress, total, message)
    except Exception:
        pass


from wayland_computer_use_mcp.tools.input_tools import register_input_tools  # noqa: E402
from wayland_computer_use_mcp.tools.navigation_tools import (  # noqa: E402
    register_navigation_tools,
)
from wayland_computer_use_mcp.tools.preset_tools import register_preset_tools  # noqa: E402
from wayland_computer_use_mcp.tools.process_tools import register_process_tools  # noqa: E402
from wayland_computer_use_mcp.tools.system_tools import register_system_tools  # noqa: E402
from wayland_computer_use_mcp.tools.visual_tools import register_visual_tools  # noqa: E402


def register_all_tools(mcp: FastMCP) -> None:
    """Registers all domain-specific tool modules onto the FastMCP server."""
    register_process_tools(mcp)
    register_navigation_tools(mcp)
    register_input_tools(mcp)
    register_visual_tools(mcp)
    register_system_tools(mcp)
    register_preset_tools(mcp)
