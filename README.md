# wayland-computer-use-mcp

[![FastMCP](https://img.shields.io/badge/FastMCP-2.0%2B-blue.svg)](https://github.com/jlowin/fastmcp)
[![Python](https://img.shields.io/badge/Python-3.11%2B-green.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Code Style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

**`wayland-computer-use-mcp`** is a high-performance Model Context Protocol (MCP) server providing an interactive GUI testing, automation, and desktop integration suite for Wayland environments (KDE Plasma 6 / KWin, GNOME Mutter, Hyprland, Sway, and generic Wayland).

Unlike conventional "computer use" agents that rely on high-latency video streaming and expensive pixel-based coordinate guessing, `wayland-computer-use-mcp` implements a **Tree-First Hybrid Semantic Execution Model**:
- **Atomic Programmatic Execution**: Inspects semantic widget trees via AT-SPI2 D-Bus interfaces and executes actions (`DoAction`, `EditableText`, `Value`, `Text`) silently without moving the physical cursor.
- **Zero-Disruption Interactions**: The desktop pointer only moves on explicit pointer tools (`click`, `hover`, `drag`) or when Level 1 AT-SPI is unsupported and Level 2 fallback is needed.
- **Playwright-Style Locators & Deltas**: Supports optional `role` disambiguation (`target="Save", role="push button"`), event-driven UI settlement (`watch_ui_events`), and compact state diffs.
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
4. **Selective Visual Grounding**: High-resolution window captures (`capture_window_frame`) are utilized selectively for custom canvas layouts or styling/visual verification checkpoints.

---

## Operating Modes & Isolation Boundaries

The server operates in two distinct display modes and supports fine-grained access scopes:

### 1. Display Modes
- **Live Mode (`--live` or `WAYLAND_MCP_DISPLAY_MODE="live"`)** *(Default)*:
  - Connects to the user's active desktop session via `$WAYLAND_DISPLAY`.
  - Interfaces with the active session D-Bus and AT-SPI2 bus.
  - Caches and reuses XDG Desktop Portal `restore_token` credentials to prevent repeated permission prompts.
  - Executes Level 1 AT-SPI actions without moving the host cursor; physical pointer motion only triggers on explicit pointer tools or Level 2 fallback.
- **Virtual / Isolated Mode (`--virtual`, `--virtual-window`, `WAYLAND_MCP_DISPLAY_MODE="virtual"`, or `WAYLAND_MCP_DISPLAY_MODE="virtual-window"`)**:
  - Starts a private `XDG_RUNTIME_DIR`, isolated `dbus-daemon`, isolated AT-SPI2 bus, and a dedicated nested/headless compositor (`kwin_wayland`, `sway`, or `weston`), keeping your host pointer, focus, and clipboard untouched without triggering host portal prompts.
  - `--virtual`: Headless virtual Wayland compositor for unattended/CI runs.
  - `--virtual-window` / `WAYLAND_MCP_DISPLAY_MODE="virtual-window"` / `WAYLAND_MCP_VIRTUAL_HEADED=1`: Opens a **Playwright-style visible nested compositor window** so you can watch the agent drive an app in its own isolated window.

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

### Run Directly via `uvx` (Pure-Python core, zero C-build dependencies)

```bash
uvx wayland-computer-use-mcp
```

The core package requires only pure/wheel dependencies (`fastmcp`, `dbus-fast`, `pillow`). Clipboard transfers use `org.freedesktop.portal.Clipboard` or native Wayland `ext_data_control_manager_v1` / `zwlr_data_control_manager_v1` (with `wl-clipboard`/`qdbus`/`xclip` fallbacks), keyboard typing adapts to the compositor's live EIS XKB keymap, and screenshots use native compositor capture, `gst-launch-1.0` CLI, or `org.freedesktop.portal.Screenshot`.

- **Optional GStreamer Python bindings** (only needed for in-process PipeWire `appsink` streaming instead of CLI/native capture):
  ```bash
  uv pip install "wayland-computer-use-mcp[gstreamer]"
  ```
- **Visible isolated Playwright-style window**:
  Uses `kwin_wayland` (built into KDE Plasma), `sway`, or `weston`:
  ```bash
  # No extra compositor install needed on KDE Plasma (`kwin_wayland`); on GNOME/other desktops install weston or sway:
  sudo apt install -y weston    # or Fedora: sudo dnf install weston / Arch: pacman -S weston
  uvx wayland-computer-use-mcp --virtual-window
  ```
- **Target app development libraries** (only if building/running the GTK4 example rig):
  - **Debian / Ubuntu / Kubuntu**: `sudo apt install -y python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 at-spi2-core`
  - **Fedora / RHEL**: `sudo dnf install -y python3-gobject gtk4 libadwaita at-spi2-core`
  - **Arch Linux / Manjaro**: `sudo pacman -S --needed python-gobject gtk4 libadwaita at-spi2-core`

### Install in Virtual Environment

```bash
git clone https://github.com/Niklas-Ehrenfried/wayland_computer_use_mcp.git
cd wayland_computer_use_mcp
uv sync
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

## Exposed Tool Suite (22 Default Tools + 2 Opt-In Tools)

### 1. Tree-First Semantic Navigation & Macro Presets (5 Tools)
- **`inspect_ui_tree(pid, max_depth)`**: Returns the collapsed 1D interactive element list (`b1`, `e1`, `c1`, `sw1`, `li1`) with widget names, roles, compact `states`, and coordinates.
- **`interact_with_node(target, action, text, value, role, pid, settle_timeout_ms, ...)`**: Dispatches semantic interactions by compact ID (`b1`) or widget label (`node_id` kept as deprecated alias; optional `role` narrows matches like Playwright's `getByRole`). Executes Level 1 AT-SPI directly without moving the mouse cursor, falling back to physical input only when required. Returns structured JSON UI deltas.
- **`batch_actions(actions, pid)`**: Executes a batch of sequential UI operations atomically with step-by-step delta tracking, optional per-step `"role"` filtering, and automatic `batch_failure.png` trace capture when a live step fails.
- **`preset_workflow(action, name, description, steps, pid)`**: Manages and executes reusable multi-step interaction presets (`list`, `run`, `view`, `save`, `delete`) stored as persistent JSON artifacts.
- **`watch_ui_events(timeout_ms, pid)`**: Streams AT-SPI2 D-Bus accessibility events (`Object:StateChanged`, `ChildrenChanged`, `TextChanged`, `Window:Activate`) directly to observe async UI changes.

### 2. Clamped Physical Input (8 Tools)
- **`click(x, y, button, pid)`**: Executes a mouse click clamped to window bounds.
- **`double_click(x, y, button, pid)`**: Dispatches a standard mouse double-click.
- **`right_click(x, y, pid)`**: Dispatches a right-click (context menu).
- **`hover(x, y, duration_ms, pid)`**: Moves pointer without clicking, activating Wayland tooltips or hover highlights.
- **`drag(start_x, start_y, end_x, end_y, pid)`**: Performs a clamped mouse drag gesture.
- **`scroll(dx, dy, pid)`**: Dispatches pointer wheel ticks via `NotifyPointerAxisDiscrete` and continuous deltas.
- **`type_text(text, x, y, pid)`**: Types text using evdev keycodes with automated clipboard paste fallback for strings > 30 characters.
- **`key_combination(keys, pid)`**: Sends modifier hotkeys (e.g. `["ctrl", "s"]`, `["alt", "tab"]`). All 8 input tools accept an optional `pid` to explicitly target a specific application window.

### 3. Visual Grounding & Inspection (1 Tool)
- **`capture_window_frame(crop_box, save_artifact, pid)`**: Captures a high-resolution window frame. In-memory MCP `ImageContent` by default; saves rolling disk cache when `save_artifact=True`.

### 4. Process Lifecycle & Crash Interception (5 Tools)
- **`launch_app(target, args, restart, cwd)`**: Spawns Python GUI scripts or system apps with automatic virtual environment discovery. **Immediately returns the initial interactive element tree** so agents can act without a separate `inspect_ui_tree` call.
- **`restart_app(pid)`**: Gracefully terminates and re-launches an active process, preserving original launch arguments and working directory. Strictly validates process ownership: unmanaged or user-opened applications raise `PermissionError` to preserve desktop consistency.
- **`terminate_app(pid)`**: Terminates application processes cleanly (`SIGTERM` escalated to `SIGKILL`).
- **`list_managed_apps()`**: Lists all active processes managed by the MCP server, pruning dead PIDs.
- **`get_app_logs(pid, lines)`**: Retrieves console output and crash tracebacks from a thread-safe circular buffer.

### 5. OS & Desktop Integration (3 Default Tools + 2 Opt-In Tools)
- **`clipboard_read()`**: Reads text from clipboard (`org.freedesktop.portal.Clipboard` and native Wayland `ext_data_control_manager_v1`/`zwlr_data_control_manager_v1` first, falling back to `wl-paste`, `qdbus`, `xclip`).
- **`clipboard_write(text)`**: Writes text to clipboard (`org.freedesktop.portal.Clipboard` and native Wayland data-control first, falling back to `wl-copy`, `qdbus`, `xclip`).
- **`window_control(action, pid)`**: Controls window state (`minimize`, `maximize`, `restore`, `close`).

Opt-in desktop shortcut tools (enable with `--enable-desktop-installer` or `WAYLAND_MCP_ENABLE_DESKTOP_INSTALLER=1`):
- **`install_to_desktop(app_name, target, ...)`**: Generates a `.desktop` launcher in `~/.local/share/applications` for universal targets (Python scripts with auto-venv, shell scripts, JS, AppImages, binaries, Flatpak IDs, URLs, or `$PATH` commands) with nearby icon discovery, monogram SVG fallback, and worktree badging.
- **`uninstall_from_desktop(app_id)`**: Removes desktop launchers and associated icons.

---

## MCP Prompts & Resources

- **Prompt: `wayland_automation_guide`**: Best-practice agent instructions for tree-first navigation, delta-to-delta flow, and fallback rules.
- **Resource: `wayland://system_prompt`**: Dynamic guidelines for LLM agent integration into client system prompts.

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

## Tested Environments & Compositor Support

| Compositor / Desktop | Status | Verified Capabilities |
| :--- | :--- | :--- |
| **KDE Plasma 6** (`kwin_wayland`) | ✅ Live-tested | All 22 MCP tools verified live in both `live` mode (XDG Portal RemoteDesktop + ScreenCast, `libei` + live XKB keymaps, AT-SPI2, clipboard, KWin window control) and isolated `--virtual-window` mode (private `XDG_RUNTIME_DIR` + D-Bus + nested `kwin_wayland` + direct `kwin_eis` + native Wayland data-control clipboard). |
| **GNOME Shell** (`Mutter`, Ubuntu 26.04) | ✅ Live-tested | `tests/test_live_example_app.py` (15/15) & `tests/test_interactive_live.py` verified live: XDG Portal RemoteDesktop + ScreenCast (including single-window selection with window-origin mapping), `libei` input + AT-SPI2 keyboard fallbacks (dropdowns, sliders, tabs, text entry). |
| **wlroots** (`Sway`, `Hyprland`) | 🧪 Unit-tested | Backend implemented (`swaymsg` / `hyprctl`) and covered by unit tests; full live compositor runs not yet verified. |

> **GNOME Note:** On GNOME Shell, enable **"Remember This Selection"** in the Remote Desktop portal dialog if you want persistent portal restore tokens across runs, and either share the target window directly or select a monitor when prompted.

---

## License

MIT License. See [LICENSE](LICENSE) for details.

