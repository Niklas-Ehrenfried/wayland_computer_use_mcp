# wayland-computer-use-mcp

[![FastMCP](https://img.shields.io/badge/FastMCP-2.0%2B-blue.svg)](https://github.com/jlowin/fastmcp)
[![Python](https://img.shields.io/badge/Python-3.11%2B-green.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Code Style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

**`wayland-computer-use-mcp`** is a high-performance Model Context Protocol (MCP) server providing an interactive GUI testing, automation, and desktop integration suite for Wayland environments (KDE Plasma 6 / KWin, GNOME Mutter, Hyprland, Sway, and generic Wayland).

Unlike conventional "computer use" agents that rely on high-latency video streaming and expensive pixel-based coordinate guessing, `wayland-computer-use-mcp` implements a **Tree-First Hybrid Semantic Execution Model**:
- **Atomic Programmatic Execution**: Inspects semantic widget trees via AT-SPI2 D-Bus interfaces and executes actions (`DoAction`, `EditableText`, `Text`) directly without coordinate ambiguity.
- **Observable Cursor Tracing**: Visibly translates the pointer over target elements prior to interaction, ensuring live tracking and transparency for human observers.
- **Resilient Physical Fallback**: Custom canvas widgets, dropdown popovers, and complex surfaces fall back smoothly to clamped physical pointer clicks, drags, discrete wheel scrolls, and keystrokes.

---

## ⚡ Token Efficiency: 90–95% Savings Over Vision-Only Approaches

Traditional screenshot-driven computer use models stream full monitor or window screenshots on every single action, consuming **1,500 to 3,500+ vision tokens per step**. A 10-step interaction sequence consumes 25,000–35,000+ tokens, introduces substantial latency, and suffers from visual coordinate hallucinations.

`wayland-computer-use-mcp` reduces token expenditure by over 90%:

| Interaction Tier | Modality / Tool | Typical Token Cost | Execution Latency | Determinism / Accuracy |
| :--- | :--- | :--- | :--- | :--- |
| **Traditional Computer Use** | Full Monitor Screenshot | ~2,500 – 3,500 tokens | 1.5 – 3.0s | High coordinate hallucination risk |
| **Window Smart Crop** | `capture_window_frame` | ~1,200 – 1,800 tokens | 0.8 – 1.2s | Visual ambiguity on dense layouts |
| **Collapsed 1D UI Tree** | `inspect_ui_tree` | **100 – 300 tokens** | **< 150ms** | **100% Deterministic (Node IDs)** |
| **Reactive UI Delta** | `wrap_with_delta` | **20 – 60 tokens** | **< 50ms** | **Zero-token re-polling** |

### Why This Architecture Conserves Tokens:
1. **1D Semantic Flattening**: Automatically filters out invisible layout containers (`GtkBox`, `GtkOverlay`, `QBoxLayout`), distilling only actionable widgets into a compact array of indexed elements (`b1`, `e1`, `c1`) with their role, state, and label.
2. **Direct Element Interaction**: Programmatic invocation via `interact_with_node(node_id="b1")` executes atomically without requiring intermediate verification screenshots.
3. **Reactive State Diffing**: Every action automatically computes a pre- and post-interaction semantic delta, returning a concise markdown summary (e.g. `b1 [button]: text '0 clicks' ➔ '1 clicks'`), eliminating redundant tree re-polling.
4. **Selective Visual Grounding**: Set-of-Marks visual overlays (`take_labeled_screenshot`) are utilized exclusively for layout or styling validation checkpoints.

---

## Operating Modes & Isolation Boundaries

The server operates in two distinct display modes and supports fine-grained access scopes:

### 1. Display Modes
- **Live Mode (`--live` or `WAYLAND_MCP_DISPLAY_MODE="live"`)** *(Default)*:
  - Connects to the user's active desktop session via `$WAYLAND_DISPLAY`.
  - Interfaces with the active session D-Bus and AT-SPI2 bus.
  - Automatically caches and reuses XDG Desktop Portal `restore_token` credentials to prevent repeated permission prompts.
  - Physically moves the desktop pointer so human operators can follow agent actions in real time.
- **Virtual / Isolated Mode (`--virtual` or `WAYLAND_MCP_DISPLAY_MODE="virtual"`)**:
  - Connects to or launches an isolated virtual Wayland compositor (e.g. `weston --backend=headless-backend.so`, `kwin_wayland --virtual`, or `gamescope`).
  - Completely separates agent actions from personal desktop workspaces, enabling unattended, headless, or CI/CD test automation.

### 2. Access Scopes
- **Window Isolation (`--window-only` or `WAYLAND_MCP_ACCESS_MODE="window"`)** *(Default)*:
  - Prompts the user to select only the target application window in the XDG ScreenCast portal prompt.
  - Clamps all coordinate motions strictly within the detected window geometry boundaries.
- **Full Display (`--fullscreen` or `WAYLAND_MCP_ACCESS_MODE="fullscreen"`)**:
  - Grants capture and interaction access to the entire display output.
- **Dual Selection (`--allow-all` or `WAYLAND_MCP_ACCESS_MODE="both"`)**:
  - Allows either window or monitor selection during the portal handshake.

---

## 🔒 Security Architecture & Upstream Portal Confinement Disclosure

> [!WARNING]
> ### Upstream XDG Portal Architectural Limitation
> Under current FreeDesktop XDG Desktop Portal specifications, selecting a single window in the `ScreenCast` permission prompt restricts video capture to that window; **however, the `RemoteDesktop` portal protocol currently does not enforce server-side pointer boundary confinement.** Once a remote desktop session is granted, the protocol allows input injection across the full display surface.

### How `wayland-computer-use-mcp` Mitigates This Risk:
To guarantee safe operation despite upstream protocol constraints, this server implements five layers of client-side containment:

1. **Strict Coordinate Boundary Clamping (`CoordinateClamper`)**:
   All injected pointer coordinates are mathematically clamped to $[0 \le x \le W, 0 \le y \le H]$ of the target application surface. The server strictly forbids emitting coordinates outside the active window frame.
2. **Dynamic Geometry Drift Detection (`GeometryDivergenceDetector`)**:
   Monitors the baseline window surface position and dimensions. If a window moves, resizes, or unminimizes unexpectedly while an action is pending, the operation is immediately aborted to prevent clicks from spilling into adjacent desktop surfaces.
3. **Hardware User Preemption (`UserInterventionDetector`)**:
   Tracks physical hardware cursor activity. If the user moves the physical mouse or types on the keyboard, automated interactions pause instantly to yield control to the human operator.
4. **Dangerous Shortcut Blacklist (`ShortcutFilter`)**:
   Blocks hazardous keyboard sequences (e.g. `Super/Meta`, `Ctrl+Alt+Delete`, `Alt+F4`, VT terminal switching).
5. **Virtual Display Sandbox Recommendation**:
   For evaluating autonomous agents or untrusted scripts, execute with `--virtual` to provide hardware-level process and display server isolation.

---

## Quickstart

### Run Directly via `uvx` (Zero installation required)

```bash
uvx wayland-computer-use-mcp
```

### Install in Virtual Environment

```bash
git clone https://github.com/your-org/wayland-computer-use-mcp.git
cd wayland-computer-use-mcp
uv venv --python python3 --system-site-packages
source .venv/bin/activate
uv pip install -e .
```

---

## MCP Client Configuration

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

---

## Exposed Tool Suite (21 Tools)

### 1. Tree-First Semantic Navigation
- **`inspect_ui_tree(pid, max_depth)`**: Returns the collapsed 1D interactive element list (`b1`, `e1`, `c1`) with widget names, roles, states, and coordinates.
- **`interact_with_node(node_id, action, text, ...)`**: Dispatches semantic interactions. Visibly traces cursor, executes AT-SPI action, and falls back to physical input if required. Auto-scrolls viewport if target is off-screen.
- **`click_element_by_label(label, role)`**: Resolves widgets by visible label or role and performs a targeted click.
- **`batch_actions(actions, pid)`**: Executes a batch of sequential UI operations atomically without intermediate screenshot pauses.

### 2. Clamped Physical Input
- **`click(x, y, button)`**: Executes a mouse click clamped to window bounds.
- **`double_click(x, y, button)`**: Dispatches a standard mouse double-click.
- **`right_click(x, y)`**: Dispatches a right-click (context menu).
- **`hover(x, y, duration_ms)`**: Moves pointer without clicking, activating Wayland tooltips or hover highlights.
- **`drag(start_x, start_y, end_x, end_y)`**: Performs a clamped mouse drag gesture.
- **`scroll(dx, dy)`**: Dispatches pointer wheel ticks via `NotifyPointerAxisDiscrete` and continuous deltas.
- **`type_text(text, x, y)`**: Types text using evdev keycodes with automated clipboard paste fallback for strings > 30 characters.
- **`key_combination(keys)`**: Sends modifier hotkeys (e.g. `["ctrl", "s"]`, `["alt", "tab"]`).

### 3. Visual Grounding & Inspection
- **`capture_window_frame(crop_box)`**: Captures a high-resolution window frame. Yields Markdown image preview links and MCP standard `ImageContent` blocks.
- **`take_labeled_screenshot()`**: Captures window frame annotated with numbered Set-of-Marks boundary badges.

### 4. Process Lifecycle & Crash Interception
- **`launch_app(script_path, args, cwd)`**: Spawns Python GUI scripts with automatic virtual environment discovery.
- **`terminate_app(pid)`**: Terminates application processes cleanly (`SIGTERM` escalated to `SIGKILL`).
- **`get_app_logs(pid, lines)`**: Retrieves console output and crash tracebacks from a thread-safe 200-line circular buffer.

### 5. OS & Desktop Integration
- **`clipboard_read()`**: Reads text from the Wayland clipboard (`wl-paste`).
- **`clipboard_write(text)`**: Writes text to the Wayland clipboard (`wl-copy`).
- **`window_control(action, pid)`**: Controls window state (`minimize`, `maximize`, `restore`, `close`).
- **`install_to_desktop(app_id, name, ...)`**: Generates a valid Linux `.desktop` launcher with worktree detection and version badging.
- **`uninstall_from_desktop(app_id)`**: Removes desktop launchers and associated icons.

---

## Interactive Test Rig

A complete 14-component GTK4/Adwaita verification application is provided in `examples/test_gui_app.py`:

```bash
uv run python examples/test_gui_app.py
```

Run the automated live end-to-end integration test suite:
```bash
uv run pytest tests/test_live_example_app.py -v
```

---

## License

MIT License. See [LICENSE](LICENSE) for details.
