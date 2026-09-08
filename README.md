# wayland-computer-use-mcp

[![FastMCP](https://img.shields.io/badge/FastMCP-2.0%2B-blue.svg)](https://github.com/jlowin/fastmcp)
[![Python](https://img.shields.io/badge/Python-3.11%2B-green.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

**`wayland-computer-use-mcp`** is a lightweight, production-grade Model Context Protocol (MCP) server providing an end-to-end interactive GUI testing, automation, and desktop integration suite for Wayland environments (specifically targeting Kubuntu/KDE Plasma and Ubuntu/GNOME).

---

## Key Capabilities

1. **Interactive Window & Desktop Streaming**: XDG Desktop Portals (`ScreenCast` via PipeWire + `RemoteDesktop` via libei/portal).
2. **Semantic UI Inspection**: AT-SPI2 D-Bus traversal with token-optimized tree pruning for multimodal LLMs.
3. **Client-Side Safety & Containment**: PID-bound process ownership, coordinate boundary clamping, and portal restore-token caching.
4. **Live vs. Virtual Screen Isolation**: Run directly on your live desktop or inside an isolated virtual Wayland display.
5. **Vibe-Coder Dev Primitives**: Virtualenv auto-detection, live stderr ring-buffering, worktree-aware desktop entry installation, and Pillow-based version/dev icon badging.

---

## Quickstart

### Run with `uvx` (No installation required)

```bash
uvx wayland-computer-use-mcp
```

### Install in Virtual Environment

```bash
# Clone and setup with uv
git clone https://github.com/your-org/wayland-computer-use-mcp.git
cd wayland-computer-use-mcp
uv venv --python python3 --system-site-packages
uv pip install -e .
```

---

## Client Configuration

You can configure **live vs. virtual** display and **window vs. fullscreen** access either via **environment variables** (`env`) or direct **CLI flags** (`args`). 

Defaults:
- Display mode: `live`
- Access scope: `window` (Window only, single surface isolation)

### 1. Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "wayland-computer-use": {
      "command": "uvx",
      "args": ["wayland-computer-use-mcp"],
      "env": {
        "WAYLAND_MCP_DISPLAY_MODE": "live",
        "WAYLAND_MCP_ACCESS_MODE": "window"
      }
    }
  }
}
```

To run in **virtual isolated mode** with **fullscreen access**:
```json
{
  "mcpServers": {
    "wayland-computer-use": {
      "command": "uvx",
      "args": ["wayland-computer-use-mcp", "--virtual", "--fullscreen"]
    }
  }
}
```

### 2. Cursor (`.cursor/mcp.json`)

```json
{
  "mcpServers": {
    "wayland-computer-use": {
      "command": "uvx",
      "args": ["wayland-computer-use-mcp", "--live", "--window-only"]
    }
  }
}
```

### 3. Antigravity IDE / Gemini Code Assist (`mcp_config.json`)

```json
{
  "mcpServers": {
    "wayland-computer-use": {
      "command": "uvx",
      "args": ["wayland-computer-use-mcp"],
      "env": {
        "WAYLAND_MCP_DISPLAY_MODE": "live",
        "WAYLAND_MCP_ACCESS_MODE": "window"
      }
    }
  }
}
```

### 4. Roo Code / Cline (`cline_mcp_settings.json`)

```json
{
  "mcpServers": {
    "wayland-computer-use": {
      "command": "uvx",
      "args": ["wayland-computer-use-mcp"],
      "env": {
        "WAYLAND_MCP_DISPLAY_MODE": "live",
        "WAYLAND_MCP_ACCESS_MODE": "window"
      }
    }
  }
}
```

### 5. OpenAI Codex / Goose / Zed

```json
{
  "mcpServers": {
    "wayland-computer-use": {
      "command": "wayland-computer-use-mcp",
      "args": ["--display-mode=live", "--access=window"]
    }
  }
}
```

---

## Display Configuration: Live vs. Virtual Screen

### Configuration Options
- **Display Modes**:
  - `live` (Default): Connects directly to the user's active desktop session (`$WAYLAND_DISPLAY`, live portal dialog).
  - `virtual`: Targets or spawns an isolated virtual Wayland compositor (e.g. `weston --backend=headless-backend.so`, `gamescope`, or `kwin_wayland --virtual`).
- **Access Scopes**:
  - `window` (Default, `source_type=2`): Prompts/streams only the selected window. Clamps clicks strictly to the window boundary.
  - `fullscreen` (`source_type=1`): Grants access to the full monitor/display.
  - `both` (`source_type=3`): Allows the user to select either in the portal prompt.

### Ways to Configure
1. **CLI Flags in `args`**:
   - `--live` or `--virtual` (or `--display-mode=live|virtual`)
   - `--window-only` or `--fullscreen` or `--allow-all` (or `--access=window|fullscreen|both`)
2. **Environment Variables in `env`**:
   - `WAYLAND_MCP_DISPLAY_MODE="live" | "virtual"`
   - `WAYLAND_MCP_ACCESS_MODE="window" | "fullscreen" | "both"`
   - `WAYLAND_MCP_SOURCE_TYPE="2" | "1" | "3"`
3. **User Configuration File (`~/.config/wayland-computer-use-mcp/config.json`)**:
   ```json
   {
     "display_mode": "live",
     "source_type": 2,
     "virtual_compositor_cmd": "weston --backend=headless-backend.so",
     "virtual_wayland_display": "wayland-mcp-virtual"
   }
   ```

---

## Interactive Test Rig GUI Application

The repository includes a dedicated GTK3 verification application in `examples/test_gui_app.py` to test all MCP tools:

```bash
# Run test application directly
python examples/test_gui_app.py
```

### Supported Testing Flows
| Tool | Target Widget in Test Rig | Verification Behavior |
| :--- | :--- | :--- |
| `launch_app` | Launches `examples/test_gui_app.py` | Spawns process, detects virtualenv, returns PID |
| `inspect_ui_tree` | All widgets | Returns pruned AT-SPI hierarchy (buttons, slider, entry, list) with coordinates |
| `click` | "Click Me!" Button | Increments click counter, updates label and stderr log |
| `type_text` | Text Entry Field | Types input string into active entry widget |
| `drag` | Horizontal Slider (Scale) | Drags slider thumb from start coordinate to end coordinate |
| `scroll` | Scrollable List View | Dispatches vertical/horizontal scroll events through 30 items |
| `capture_window_frame` | Window Surface | Captures PNG image or cropped element bounding box |
| `get_app_logs` | Console ring-buffer | Retrieves live stderr log lines emitted by the test rig |
| `restart_app` / `terminate_app` | Process lifecycle | Hot-restarts or safely terminates test rig process |

---

## Recommended Additional Tools for Enhanced GUI Interaction

Here is a curated list of high-value tools that can be added to further enhance agent GUI capabilities:

1. **`key_combination(keys: list[str])`**:
   - Sends simultaneous modifier hotkeys (e.g. `["ctrl", "c"]`, `["alt", "tab"]`, `["ctrl", "shift", "t"]`, `["super"]`).
2. **`click_element_by_label(label: str, role: str | None = None)`**:
   - Combines `inspect_ui_tree` with `click`: finds the matching AT-SPI accessible node by text/role, computes its center `(x + w//2, y + h//2)`, and fires the click automatically.
3. **`double_click(x: int, y: int)` & `right_click(x: int, y: int)`**:
   - Specialized mouse gestures for opening items or triggering context menus.
4. **`hover(x: int, y: int, duration_ms: int = 500)`**:
   - Moves cursor to position without clicking, triggering Wayland tooltips, hover highlights, or dropdown menus.
5. **`clipboard_read()` & `clipboard_write(text: str)`**:
   - Direct integration with Wayland clipboard (`wl-paste` and `wl-copy`) to read/write selections or verify copy-paste operations.
6. **`take_labeled_screenshot()`**:
   - Captures the window frame and overlays numeric bounding-box markers `[1]`, `[2]`, `[3]` corresponding to interactive AT-SPI elements (Set-of-Marks prompting for multimodal LLMs).
7. **`focus_window(pid: int)`**:
   - Requests window focus/activation via AT-SPI `Component.GrabFocus()` or compositor protocol.

---

## Exposed MCP Tools

### Process Lifecycle
- `launch_app(script_path, args, cwd)`: Launches a Python GUI script using auto-detected virtualenv and returns its PID.
- `restart_app(pid)`: Hot-restarts a managed application while preserving its command arguments and environment.
- `terminate_app(pid)`: Safely terminates an application process spawned by this session (SIGTERM escalated to SIGKILL).
- `get_app_logs(pid, lines)`: Retrieves recent stderr crash tracebacks and console output from a circular ring buffer.
- `check_app_liveness(pid)`: Checks if a managed process is active, responding, or in a zombie/crashed state.

### Visual & Tree Inspection
- `capture_window_frame(crop_box)`: Captures a high-resolution frame of the Wayland window/desktop. Optional `crop_box: [x, y, w, h]`.
- `inspect_ui_tree(pid, max_depth)`: Returns the pruned, semantic AT-SPI2 accessibility tree for the application.

### Clamped Input Injection
- `click(x, y, button)`: Fires a mouse click clamped to the window boundary.
- `drag(start_x, start_y, end_x, end_y)`: Performs a clamped drag-and-drop gesture within the window.
- `scroll(dx, dy)`: Dispatches horizontal/vertical scroll events to the active surface.
- `type_text(text)`: Types a string of text via hybrid libei keyboard keycodes with clipboard fallback.

### OS Desktop Integration
- `install_to_desktop(app_id, name, exec_path, icon_path, version, is_dev)`: Registers the script as a native Linux desktop application (`.desktop`) with worktree detection and version badging.
- `uninstall_from_desktop(app_id)`: Removes the application launcher and its associated icons from the OS desktop menu.

---

## Upstream Security Disclosure Note

> [!IMPORTANT]
> **Upstream Security Notice**:
> While `wayland-computer-use-mcp` enforces strict **client-side coordinate boundary clamping** on all incoming clicks, drags, and motions (preventing pointer coordinates from exceeding the identified window surface $[0 \le x \le W, 0 \le y \le H]$), native display-server coordinate confinement within the XDG `RemoteDesktop` portal protocol remains an active open upstream initiative in the FreeDesktop standards community.
> 
> When testing untrusted or automated agents, running in **virtual display mode** (`WAYLAND_MCP_DISPLAY_MODE="virtual"`) or within an isolated nested Wayland compositor provides an additional hardware and sandbox isolation boundary.

---

## License

MIT License. See [LICENSE](LICENSE) for details.
