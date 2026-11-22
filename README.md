# wayland-computer-use-mcp

[![FastMCP](https://img.shields.io/badge/FastMCP-2.0%2B-blue.svg)](https://github.com/jlowin/fastmcp)
[![Python](https://img.shields.io/badge/Python-3.11%2B-green.svg)](https://python.org)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Code Style: Ruff](https://img.shields.io/badge/code%20style-ruff-000000.svg)](https://github.com/astral-sh/ruff)

**`wayland-computer-use-mcp`** is a high-performance Model Context Protocol (MCP) server providing an interactive GUI testing, automation, and desktop integration suite for Wayland environments (KDE Plasma 6 / KWin, GNOME Mutter, Hyprland, Sway, and generic Wayland).

Unlike conventional "computer use" agents that rely on high-latency video streaming and expensive pixel-based coordinate guessing, `wayland-computer-use-mcp` implements a **Tree-First Hybrid Semantic Execution Model**:
- **Atomic Programmatic Execution**: Inspects semantic widget trees via AT-SPI2 D-Bus interfaces and executes actions (`DoAction`, `EditableText`, `Value`, `Text`) silently without moving the physical cursor.
- **Zero-Disruption Interactions**: The desktop pointer only moves on explicit pointer calls (`mouse`) or when Level 1 AT-SPI is unsupported and Level 2 fallback is needed.
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
2. **Direct Element Interaction**: Programmatic invocation via `interact_with_node(target="b1")` executes atomically without requiring intermediate verification screenshots.
3. **Reactive State Diffing**: Every action automatically computes a pre- and post-interaction semantic delta, returning a concise markdown summary (e.g. `b1 [button]: text '0 clicks' ➔ '1 clicks'`), eliminating redundant tree re-polling.
4. **Selective Visual Grounding**: High-resolution window captures (`capture_window_frame(target=..., annotate_elements=True)`) are utilized selectively for custom canvas layouts or styling/visual verification checkpoints.

---

## Operating Modes & Isolation Boundaries

The server operates in two distinct display modes and supports fine-grained access scopes:

### 1. Display Modes & Blackbox Architecture
- **Auto Mode (`--auto` or `WAYLAND_MCP_DISPLAY_MODE="auto"`)** *(Default)*:
  - Automatically isolates the session in a Playwright-style Blackbox compositor whenever `dbus-daemon` and a supported compositor (`kwin_wayland`, `labwc`, `sway`, or `weston`) are installed — using a visible nested window when `WAYLAND_DISPLAY` is present, or a headless virtual compositor otherwise — and falls back cleanly to lazy host-passthrough (`live`) when none is available.
- **Virtual / Blackbox Mode (`--virtual`, `--virtual-window`, `WAYLAND_MCP_DISPLAY_MODE="virtual"`, or `WAYLAND_MCP_DISPLAY_MODE="virtual-window"`)**:
  - Starts a private `XDG_RUNTIME_DIR`, isolated `dbus-daemon`, isolated AT-SPI2 bus, and a dedicated nested/headless compositor (`kwin_wayland` via direct EIS, `labwc`/`sway` via pure-Python `zwlr_screencopy_manager_v1` + virtual pointer/keyboard protocols, or `weston`), keeping your host pointer, focus, and clipboard untouched without triggering host portal prompts.
  - `--virtual`: Headless virtual Wayland compositor for unattended/CI runs.
  - `--virtual-window` / `WAYLAND_MCP_DISPLAY_MODE="virtual-window"` / `WAYLAND_MCP_VIRTUAL_HEADED=1`: Opens a **Playwright-style visible nested compositor window** so you can watch the agent drive an app in its own isolated window.
- **Live Host-Passthrough Mode (`--live` or `WAYLAND_MCP_DISPLAY_MODE="live"`)**:
  - Connects to the user's active desktop session via `$WAYLAND_DISPLAY`, session D-Bus, and AT-SPI2 bus.
  - Caches and reuses XDG Desktop Portal `restore_token` credentials to prevent repeated permission prompts.
  - Optional `--lazy-portal` / `WAYLAND_MCP_LAZY_PORTAL=1`: Defers XDG RemoteDesktop/ScreenCast prompts until a screenshot (`capture_window_frame`) or physical pointer/keyboard fallback is requested, keeping pure AT-SPI interaction prompt-free.

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
3. **User Input Preemption (`UserPreemptionManager`)**:
   Pauses automated interactions via `Window:Activate` focus-switch monitoring in `live` mode or programmatic pause (`set_paused(True)`) to yield control back to the user.
4. **Dangerous Shortcut Blacklist (`SystemShortcutFilter`)**:
   Blocks hazardous keyboard sequences (e.g. `Super/Meta`, `Ctrl+Alt+*`, `Alt+F4`, `PrintScreen`, `SysRq`, `Power`).
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
  Uses `kwin_wayland` (built into KDE Plasma), `labwc`/`sway`, or `weston`:
  ```bash
  # No extra compositor install needed on KDE Plasma (`kwin_wayland`); on GNOME/other desktops install labwc (recommended) or sway:
  sudo apt install -y labwc     # Fedora: sudo dnf install labwc / Arch: pacman -S labwc
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

## Exposed Tool Suite (18 Default Tools + 2 Opt-In Tools)

### 1. Tree-First Semantic Navigation & Macro Presets (6 Tools)
- **`inspect_ui_tree(pid, max_depth, include_raw_tree, query, role_filter)`**: Returns a compact functional `hierarchy`, monotonic `tree_generation`, and collapsed 1D `interactive_elements` list (`b1`, `e1`, `c1`, `sw1`, `li1`) fetched via bulk `Cache.GetItems` with parallel recursive fallback (pass `query`/`role_filter` to filter large UIs or `include_raw_tree=True` for full D-Bus nodes).
- **`interact_with_node(target, action, text, value, role, pid, settle_timeout_ms, ...)`**: Dispatches semantic interactions by compact ID (`b1`) or widget label (`node_id` kept as deprecated alias; optional `role` narrows matches like Playwright's `getByRole`) with stale-ID identity verification & rebinding. Executes Level 1 AT-SPI directly without moving the mouse cursor, falling back to physical input only when required. Returns structured JSON UI deltas and closest-match hints on lookup misses.
- **`wait_for_element(target, role, state, timeout_ms, poll_interval_ms, pid)`**: Polls until an element reaches `"present"`, `"gone"`, `"enabled"`, `"checked"`, or `"focused"` (fail-fast if the process crashes).
- **`batch_actions(actions, pid)`**: Executes a typed `BatchActionStep` sequence atomically (`click`, `type`, `wait`, `wait_for`, etc.) with step-by-step + net delta tracking, structured `error_code` / `recovery_hint`, and automatic `batch_failure.png` trace capture on live failures.
- **`preset_workflow(action, name, description, steps, pid)`**: Manages and executes reusable multi-step interaction presets (`list`, `run`, `view`, `save`, `delete`) stored as persistent JSON artifacts, auto-normalizing positional node IDs (`b1`) into stable semantic `target` + `role` locators on save.
- **`watch_ui_events(timeout_ms, pid)`**: Streams AT-SPI2 D-Bus accessibility events (`Object:StateChanged`, `ChildrenChanged`, `TextChanged`, `Window:Activate`) directly to observe async UI changes.

### 2. Clamped Physical Input (2 Consolidated Tools)
- **`mouse(action, x, y, end_x, end_y, button, duration_ms, dx, dy, pid)`**: Consolidated pointer tool supporting `action="click" | "double_click" | "right_click" | "hover" | "drag" | "scroll"`, all clamped strictly within the target window bounds.
- **`keyboard(action, text, keys, x, y, pid)`**: Consolidated keyboard tool supporting `action="type" | "keys"` with live XKB keymap synthesis, automated clipboard paste fallback for strings > 30 chars, and dangerous OS shortcut blocking.

### 3. Visual Grounding & Inspection (1 Tool)
- **`capture_window_frame(crop_box, save_artifact, pid, target, annotate_elements)`**: Captures a high-resolution window frame (with HiDPI scaling compensation, optional widget-targeted crop with closest-match hint when missing, and optional Set-of-Marks `b1`/`e1` overlay). In-memory MCP `ImageContent` by default; saves rolling disk cache (auto-pruned) when `save_artifact=True`.

### 4. Process Lifecycle, Diagnostics & Session Status (6 Tools)
- **`launch_app(target, args, restart, cwd, ready_timeout_ms)`**: Spawns Python GUI scripts, Electron/Tauri/Chromium apps (auto-enabling Wayland + renderer accessibility), Rust/C++ binaries, or `$PATH` commands, waiting briefly until ready and returning initial `interactive_elements`, compact `hierarchy`, and `session` status (or structured `exited_early` recovery payload with `logs`/`traceback` on startup crash).
- **`restart_app(pid)`**: Gracefully terminates and re-launches an active process, preserving original launch arguments and working directory. Strictly validates process ownership: unmanaged or user-opened applications raise `PermissionError` to preserve desktop consistency.
- **`terminate_app(pid)`**: Terminates application processes cleanly (`SIGTERM` escalated to `SIGKILL`).
- **`list_managed_apps()`**: Lists all active processes managed by the MCP server, pruning dead PIDs.
- **`get_app_logs(pid, lines)`**: Retrieves console output and crash tracebacks from a thread-safe circular buffer.
- **`get_session_status()`**: Reports active display mode, compositor adapter, input readiness, target PID/window bounds, and managed process count.

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
| **KDE Plasma 6** (`kwin_wayland`) | ✅ Live-tested | Full MCP suite verified live in both `live` mode (XDG Portal RemoteDesktop + ScreenCast, `libei` + live XKB keymaps, AT-SPI2, clipboard, KWin window control) and isolated `--virtual-window` mode (private `XDG_RUNTIME_DIR` + D-Bus + nested `kwin_wayland` + direct `kwin_eis` + native Wayland data-control clipboard). |
| **GNOME Shell** (`Mutter`) & **`labwc` Blackbox** | ✅ Live-tested | `tests/test_live_example_app.py` (18 E2E UI tests) and `tests/test_virtual_session.py` verified live under GNOME (`labwc` Blackbox nested window via `zwlr_screencopy_manager_v1` + virtual pointer/keyboard protocols) as well as GNOME host portals (`RemoteDesktop` + `ScreenCast`). |
| **wlroots** (`Sway`, `Hyprland`, `labwc`) | ✅ CI + Unit-tested | Host IPC backend (`swaymsg` / `hyprctl`), isolated Blackbox `labwc` + `sway` headless matrix in GitHub Actions CI (`WLR_BACKENDS=headless`), and wire-protocol unit tests. |

> **GNOME Note:** On GNOME Shell, enable **"Remember This Selection"** in the Remote Desktop portal dialog if you want persistent portal restore tokens across runs, and either share the target window directly or select a monitor when prompted.

---

## License

MIT License. See [LICENSE](LICENSE) for details.

