#!/usr/bin/env python3
"""End-to-End AI Coding Agent Example Client for wayland-computer-use-mcp.

Simulates an AI coding agent connecting to the FastMCP server over stdio,
discovering the 24 exposed tools, and driving examples/test_gui_app.py:
1. Tool discovery (24 tools).
2. App lifecycle: launch_app (returns initial tree) + list_managed_apps.
3. Semantic tree inspection (inspect_ui_tree) & Tree-First interaction (interact_with_node).
4. Atomic multi-step interaction (batch_actions).
5. Coordinated mouse interactions (drag, scroll) and keyboard shortcuts.
6. Security boundary enforcement (prohibition of Super/Meta shortcuts).
7. Optional screenshot capture & log retrieval (get_app_logs).
8. Safe application termination (terminate_app).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def print_step(title: str) -> None:
    print("\n========================================================")
    print(f" AGENT ACTION: {title}")
    print("========================================================")


async def run_agent_test() -> None:
    workspace = Path(__file__).resolve().parent.parent
    test_app_path = str(workspace / "examples" / "test_gui_app.py")
    artifact_dir = Path(tempfile.gettempdir()) / "wayland-mcp-example-artifacts"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    print("[Agent Harness] Initializing connection to wayland-computer-use-mcp...")
    server_env = dict(
        os.environ,
        WAYLAND_MCP_DISPLAY_MODE="live",
        WAYLAND_MCP_ACCESS_MODE="window",
        WAYLAND_MCP_ACTION_DELAY="0.3",
        WAYLAND_MCP_SAVE_FRAMES_DIR=str(artifact_dir),
    )
    server_params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "wayland_computer_use_mcp.server"],
        env=server_env,
    )

    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            print("[Agent Harness] MCP session initialized successfully.")

            # 1. Discover tools
            print_step("Discover MCP Tools")
            tools_result = await session.list_tools()
            tool_names = [t.name for t in tools_result.tools]
            print(f"Discovered {len(tool_names)} tools:")
            for name in sorted(tool_names):
                print(f"  - {name}")
            assert "inspect_ui_tree" in tool_names
            assert "interact_with_node" in tool_names
            assert "batch_actions" in tool_names
            assert "key_combination" in tool_names

            # 2. Launch Test App
            print_step("Launch Target GUI App via launch_app")
            launch_res = await session.call_tool("launch_app", {"target": test_app_path})
            launch_data = json.loads(launch_res.content[0].text)
            pid = launch_data["pid"]
            elem_count = len(launch_data.get("interactive_elements", []))
            print(f"Launched PID={pid}, initial elements={elem_count}")
            assert pid > 0

            await asyncio.sleep(0.5)

            try:
                # 3. Verify active apps
                print_step(f"Check Active Apps (list_managed_apps) for PID {pid}")
                apps_res = await session.call_tool("list_managed_apps", {})
                apps_data = json.loads(apps_res.content[0].text)
                print(f"Managed apps: {apps_data}")

                # 4. Semantic AT-SPI Accessibility Tree Inspection
                print_step("Inspect Semantic UI Tree (inspect_ui_tree)")
                tree_res = await session.call_tool("inspect_ui_tree", {"pid": pid, "max_depth": 12})
                tree_data = json.loads(tree_res.content[0].text)
                print(f"Root window name: {tree_data.get('name')}")
                print(f"Interactive elements: {len(tree_data.get('interactive_elements', []))}")

                # 5. Tree-First Click & Typing via interact_with_node
                print_step("Tab 1: Click 'Click Me!' via interact_with_node")
                click_el_res = await session.call_tool(
                    "interact_with_node",
                    {"target": "Click Me!", "action": "click", "pid": pid},
                )
                print(f"Result: {click_el_res.content[0].text}")

                print_step("Tab 1: Type text via batch_actions")
                batch_res = await session.call_tool(
                    "batch_actions",
                    {
                        "pid": pid,
                        "actions": [
                            {"action": "type", "target": "e1", "text": "Agent MCP verified!"},
                            {"action": "click", "target": "Navigation & Pages"},
                        ],
                    },
                )
                print(f"Batch result: {batch_res.content[0].text}")

                # 6. Scroll & Capture Frame
                print_step("Scroll & Capture Window Frame")
                scroll_res = await session.call_tool("scroll", {"dx": 0, "dy": -40, "pid": pid})
                print(f"Scroll result: {scroll_res.content[0].text}")
                await session.call_tool("capture_window_frame", {"save_artifact": True, "pid": pid})
                print(f"Captured and saved frame to {artifact_dir}")

                # 7. Security Boundary Verification
                print_step("Verify Security: Super/Meta Shortcut Prohibition")
                res = await session.call_tool(
                    "key_combination", {"keys": ["super", "d"], "pid": pid}
                )
                assert res.isError or any(
                    "prohibited" in getattr(c, "text", "").lower()
                    or "blocked" in getattr(c, "text", "").lower()
                    for c in res.content
                ), "CRITICAL: Prohibited shortcut Super+D was not blocked!"

                # 8. Log Retrieval
                print_step("Retrieve App Logs (get_app_logs)")
                logs_res = await session.call_tool("get_app_logs", {"pid": pid, "lines": 20})
                print(f"Logs captured:\n{logs_res.content[0].text}")

            finally:
                # 9. Terminate App
                print_step(f"Terminate Application PID {pid}")
                term_res = await session.call_tool("terminate_app", {"pid": pid})
                term_data = json.loads(term_res.content[0].text)
                print(f"Termination result: {term_data}")
                assert term_data.get("status") == "terminated"

    print("\n========================================================")
    print(" ALL END-TO-END MCP CODING AGENT CHECKS PASSED!")
    print("========================================================\n")


if __name__ == "__main__":
    asyncio.run(run_agent_test())
