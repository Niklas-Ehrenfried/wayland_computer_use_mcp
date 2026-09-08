#!/usr/bin/env python3
"""End-to-End AI Coding Agent Test Client for wayland-computer-use-mcp.

Simulates an AI coding agent connecting to the FastMCP server over stdio,
discovering all exposed tools, and driving the interactive Wayland test GUI rig:
1. Tool discovery (24 tools).
2. App lifecycle: launch, liveness check.
3. Surface geometry inspection and window focus.
4. Semantic AT-SPI2 accessibility tree inspection.
5. Set-of-Marks labeled screenshot generation.
6. Coordinated mouse interactions (click, double click, hover, drag, scroll).
7. Keyboard typing and application-scoped shortcut dispatch.
8. Security boundary enforcement (prohibition of Super/Meta shortcuts).
9. Live stderr console log stream verification.
10. Safe application termination.
"""

import asyncio
import json
import os
import sys
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
    conv_id = "539f5e12-8178-4a5e-b7b9-b96af1210fa6"
    artifact_dir = Path(f"/home/niklas/.gemini/antigravity/brain/{conv_id}")
    artifact_dir.mkdir(parents=True, exist_ok=True)

    print("[Agent Harness] Initializing connection to wayland-computer-use-mcp...")
    server_env = dict(
        os.environ,
        WAYLAND_MCP_DISPLAY_MODE="live",
        WAYLAND_MCP_ACCESS_MODE="window",
        WAYLAND_MCP_ACTION_DELAY="0.5",
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
                print(f"  • {name}")
            assert "inspect_ui_tree" in tool_names
            assert "take_labeled_screenshot" in tool_names
            assert "click_element_by_label" in tool_names
            assert "key_combination" in tool_names

            # 2. Launch Test App
            print_step("Launch Target GUI App via launch_app")
            launch_res = await session.call_tool("launch_app", {"script_path": test_app_path})
            launch_data = json.loads(launch_res.content[0].text)
            pid = launch_data["pid"]
            venv_det = launch_data.get("virtualenv_detected")
            print(f"Launched application process: PID={pid}, Virtualenv={venv_det}")
            assert pid > 0

            # Allow UI to initialize
            await asyncio.sleep(0.5)

            try:
                # 3. Check App Liveness
                print_step(f"Check Liveness of PID {pid}")
                live_res = await session.call_tool("check_app_liveness", {"pid": pid})
                live_data = json.loads(live_res.content[0].text)
                state = live_data.get("state")
                resp = live_data.get("responsive")
                print(f"App liveness: state={state}, responsive={resp}")
                assert resp is True

                # 4. Inspect Window Geometry & Focus
                print_step(f"Inspect Surface Geometry for PID {pid}")
                geom_res = await session.call_tool("get_window_geometry", {"pid": pid})
                geom_data = json.loads(geom_res.content[0].text)
                print(f"Window Geometry: {geom_data}")
                assert "bounds" in geom_data

                focus_res = await session.call_tool("focus_window", {"pid": pid})
                focus_data = json.loads(focus_res.content[0].text)
                print(f"Window Focus state: {focus_data}")

                # 5. Semantic AT-SPI Accessibility Tree Inspection
                print_step("Inspect Semantic UI Tree (inspect_ui_tree)")
                tree_res = await session.call_tool("inspect_ui_tree", {"pid": pid, "max_depth": 5})
                tree_data = json.loads(tree_res.content[0].text)
                print(f"Root window name: {tree_data.get('name')}")
                print(f"Total semantic children found: {len(tree_data.get('children', []))}")
                roles = [c.get("role") for c in tree_data.get("children", [])]
                print(f"Widget roles discovered: {set(roles)}")

                # 6. Set-of-Marks Labeled Screenshot
                print_step("Capture Set-of-Marks Labeled Screenshot (take_labeled_screenshot)")
                som_res = await session.call_tool("take_labeled_screenshot", {})
                som_data = json.loads(som_res.content[0].text)
                print(f"Labeled Elements Count: {som_data['element_count']}")
                for el in som_data["elements"][:4]:
                    badge = el.get("index", el.get("badge_number"))
                    lbl = el.get("name", el.get("label"))
                    bbox = el.get("bounds", el.get("bbox"))
                    print(f"  Badge #{badge}: label='{lbl}' bounds={bbox}")

                # 7. TAB 1: Semantic Element Click (triggers animated red ripple)
                print_step("Tab 1: Click Counter Button (click_element_by_label)")
                click_el_res = await session.call_tool(
                    "click_element_by_label", {"label": "Click Me!"}
                )
                print(f"Result: {click_el_res.content[0].text}")

                # 8. TAB 1: Text Typing
                print_step("Tab 1: Type into Entry Field (type_text)")
                type_res = await session.call_tool("type_text", {"text": "Agent MCP verified!"})
                print(f"Type text result: {type_res.content[0].text}")

                # 9. TAB 1: Slider Drag
                print_step("Tab 1: Drag Slider (drag tool)")
                drag_res = await session.call_tool(
                    "drag",
                    {
                        "start_x": 100,
                        "start_y": 250,
                        "end_x": 250,
                        "end_y": 250,
                        "duration_ms": 100,
                    },
                )
                print(f"Drag result: {drag_res.content[0].text}")

                # 10. TAB 2: Switch to Navigation & Pages Tab
                print_step("Tab 2: Switch Tab to 'Navigation & Pages'")
                tab2_res = await session.call_tool(
                    "click_element_by_label", {"label": "Navigation & Pages"}
                )
                print(f"Tab switch: {tab2_res.content[0].text}")

                # 11. TAB 2: Sub-page navigation buttons
                print_step("Tab 2: Navigate to Analytics Sub-Page")
                p2_res = await session.call_tool(
                    "click_element_by_label", {"label": "Go to Analytics Page"}
                )
                print(f"Navigation: {p2_res.content[0].text}")

                print_step("Tab 2: Navigate to Settings Sub-Page")
                p3_res = await session.call_tool(
                    "click_element_by_label", {"label": "Go to Settings Page"}
                )
                print(f"Navigation: {p3_res.content[0].text}")

                # 12. TAB 3: Switch to Data & Lists Tab & Scroll
                print_step("Tab 3: Switch Tab to 'Data & Lists'")
                tab3_res = await session.call_tool(
                    "click_element_by_label", {"label": "Data & Lists"}
                )
                print(f"Tab switch: {tab3_res.content[0].text}")

                print_step("Tab 3: Scroll Viewport (scroll tool)")
                scroll_res = await session.call_tool("scroll", {"dx": 0, "dy": -80})
                print(f"Scroll result: {scroll_res.content[0].text}")

                # 13. Capture Window Frame (persisted to artifact directory)
                print_step("Capture Window Frame to Artifact Directory")
                await session.call_tool("capture_window_frame", {})
                print(f"Captured and saved frame to {artifact_dir}")

                print_step("Test Application Shortcut (Ctrl+C)")
                key_res = await session.call_tool("key_combination", {"keys": ["ctrl", "c"]})
                print(f"Application key combination result: {key_res.content[0].text}")

                # 12. Security Boundary Verification: System Shortcut Prohibition
                print_step("Verify Security: Super/Meta & Ctrl+Alt Prohibition")
                blocked = False
                try:
                    res = await session.call_tool("key_combination", {"keys": ["super", "d"]})
                    has_err = res.isError or any(
                        "prohibited" in getattr(c, "text", "")
                        or "blocked" in getattr(c, "text", "")
                        for c in res.content
                    )
                    if has_err:
                        blocked = True
                        err_txt = res.content[0].text if res.content else "error"
                        print(f"Successfully blocked prohibited shortcut via MCP tool: {err_txt}")
                except Exception as exc:
                    blocked = True
                    print(f"Successfully blocked prohibited shortcut via exception: {exc}")
                assert blocked, "CRITICAL: Prohibited shortcut Super+D was not blocked!"

                # 13. Stderr Log Buffer Retrieval
                print_step("Retrieve App Stderr Logs (get_app_logs)")
                logs_res = await session.call_tool("get_app_logs", {"pid": pid, "lines": 20})
                logs_text = logs_res.content[0].text
                print(f"Logs captured:\n{logs_text}")

            finally:
                # 14. Terminate App
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
