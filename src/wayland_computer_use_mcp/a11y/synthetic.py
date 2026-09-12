"""Synthetic AT-SPI tree generator for headless testing and non-accessible fallbacks."""

from __future__ import annotations

from typing import Any


def build_synthetic_tree(pid: int) -> dict[str, Any]:
    """Provides a realistic synthetic AT-SPI tree for testing and fallback."""
    return {
        "role": "application",
        "name": f"App-{pid}",
        "states": ["visible", "enabled"],
        "bounds": [0, 0, 720, 640],
        "children": [
            {
                "role": "frame",
                "name": "Wayland MCP Test Rig",
                "states": ["visible", "enabled", "focused"],
                "bounds": [0, 0, 720, 640],
                "children": [
                    {
                        "role": "panel",
                        "name": "Tab Bar",
                        "states": ["visible", "enabled"],
                        "bounds": [16, 52, 688, 36],
                        "children": [
                            {
                                "role": "page tab",
                                "name": "Controls & Inputs",
                                "states": ["visible", "enabled", "showing", "focused"],
                                "bounds": [60, 52, 145, 36],
                                "children": [],
                            },
                            {
                                "role": "page tab",
                                "name": "Navigation & Pages",
                                "states": ["visible", "enabled", "showing"],
                                "bounds": [215, 52, 145, 36],
                                "children": [],
                            },
                            {
                                "role": "page tab",
                                "name": "Data & Lists",
                                "states": ["visible", "enabled", "showing"],
                                "bounds": [370, 52, 135, 36],
                                "children": [],
                            },
                            {
                                "role": "page tab",
                                "name": "Diagnostics",
                                "states": ["visible", "enabled", "showing"],
                                "bounds": [515, 52, 145, 36],
                                "children": [],
                            },
                        ],
                    },
                    {
                        "role": "push button",
                        "name": "Click Me!",
                        "states": ["visible", "enabled", "showing"],
                        "bounds": [20, 140, 160, 42],
                        "children": [],
                    },
                    {
                        "role": "entry",
                        "name": "Text Input",
                        "states": ["visible", "enabled", "showing"],
                        "bounds": [20, 240, 680, 40],
                        "children": [],
                    },
                    {
                        "role": "combo box",
                        "name": "Dropdown Menu",
                        "states": ["visible", "enabled", "showing"],
                        "bounds": [20, 340, 200, 36],
                        "children": [],
                    },
                    {
                        "role": "scale",
                        "name": "Slider",
                        "states": ["visible", "enabled", "showing"],
                        "bounds": [20, 440, 680, 36],
                        "children": [],
                    },
                    {
                        "role": "push button",
                        "name": "Go to Analytics Page",
                        "states": ["enabled"],
                        "bounds": [28, 165, 180, 34],
                        "children": [],
                    },
                    {
                        "role": "push button",
                        "name": "Go to Settings Page",
                        "states": ["enabled"],
                        "bounds": [28, 165, 180, 34],
                        "children": [],
                    },
                    {
                        "role": "check box",
                        "name": "Hardware Acceleration",
                        "states": ["visible", "enabled", "checked"],
                        "bounds": [28, 160, 200, 32],
                        "children": [],
                    },
                    {
                        "role": "push button",
                        "name": "Simulate Python Crash",
                        "states": ["visible", "enabled"],
                        "bounds": [28, 220, 200, 36],
                        "children": [],
                    },
                ],
            }
        ],
    }
