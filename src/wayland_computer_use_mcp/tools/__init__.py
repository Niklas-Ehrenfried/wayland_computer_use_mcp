"""Modular tool registration and aggregation for wayland-computer-use-mcp."""

from __future__ import annotations

from fastmcp import FastMCP

from wayland_computer_use_mcp.tools.input_tools import register_input_tools
from wayland_computer_use_mcp.tools.navigation_tools import register_navigation_tools
from wayland_computer_use_mcp.tools.process_tools import register_process_tools
from wayland_computer_use_mcp.tools.system_tools import register_system_tools
from wayland_computer_use_mcp.tools.visual_tools import register_visual_tools


def register_all_tools(mcp: FastMCP) -> None:
    """Registers all domain-specific tool modules onto the FastMCP server."""
    register_process_tools(mcp)
    register_navigation_tools(mcp)
    register_input_tools(mcp)
    register_visual_tools(mcp)
    register_system_tools(mcp)
