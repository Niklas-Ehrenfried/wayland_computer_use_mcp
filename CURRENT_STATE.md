# Wayland Computer-Use MCP Server: Current Implementation & Architecture

This document provides a technical component-by-component breakdown of `wayland-computer-use-mcp`, detailing the modular package architecture, communication with Wayland and D-Bus protocols, the hybrid semantic-first execution model, and the end-to-end operational pipeline.

---

## 1. Architectural Overview

`wayland-computer-use-mcp` is an open-source Model Context Protocol (MCP) server enabling autonomous AI coding agents to observe, debug, and interact with graphical desktop applications running on modern Wayland compositors (KDE Plasma 6 / KWin, GNOME Mutter, Hyprland, Sway, and generic Wayland).

```
   ┌─────────────────────────────────────────────────────────────┐
   │                    AI Agent / MCP Client                    │
   └──────────────────────────────┬──────────────────────────────┘
                                  │ stdio (JSON-RPC / MCP Protocol)
   ┌──────────────────────────────▼──────────────────────────────┐
   │             server.py (FastMCP Tool Registry)               │
   └──────┬──────────────┬──────────────┬──────────────┬─────────┘
          │              │              │              │
          ▼              ▼              ▼              ▼
       tools/         a11y/        compositors/     portal/
   (5 Domain Tool  (AT-SPI2 DBus   (KWin, GNOME,   (ScreenCast,
     Categories)     Tree, 1D &    wlroots IPC &    RemoteDesktop,
                    Hybrid Model)  Auto-Select)    Dual-Dispatch)
          │              │              │              │
          │              │              │              ▼
          │              │              │           libei.py
          │              │              │        (Direct EIS Seat
          │              │              │        Input Injection)
          │              │              │              │
   ┌──────▼──────────────▼──────────────▼──────────────▼─────────┐
   │            Wayland Compositor & D-Bus Session               │
   │      (org.freedesktop.portal.Desktop, KWin, AT-SPI2)        │
   └─────────────────────────────────────────────────────────────┘
```

---

## 2. Component Packages Breakdown

### A. FastMCP Server Entrypoint ([`server.py`](file:///home/niklas/Documents/Coding/Hobby/wayland_computer_use_mcp/src/wayland_computer_use_mcp/server.py))
- **Role**: Exposes 22 default tools (+ 2 opt-in desktop installer tools when `--enable-desktop-installer` / `WAYLAND_MCP_ENABLE_DESKTOP_INSTALLER=1` is set), 1 prompt (`wayland_automation_guide`), and 1 resource (`wayland://system_prompt`) over the MCP JSON-RPC protocol via `fastmcp.FastMCP` with MCP `ToolAnnotations` hints.
- **Modularity**: Imports and re-exports tool suites from `wayland_computer_use_mcp.tools.*` with a cleanly sorted `__all__`.
- **CLI Configuration**: Evaluates arguments (`--live`, `--virtual`, `--virtual-window`, `--window-only`, `--fullscreen`, `--enable-desktop-installer`, `-h`/`--help`, `-v`/`--version`) before launching the FastMCP transport loop.
- **Packaging**: Pure-Python core dependencies (`fastmcp`, `dbus-fast`, `pillow`); `PyGObject` moved to optional `[gstreamer]` extra, unused `pycairo` removed.
- **Graceful Lifecycle Cleanup**: Intercepts `SIGINT`, `SIGTERM`, and `atexit` to terminate spawned processes and safely release XDG portal sessions, pooled AT-SPI D-Bus connections, and PipeWire streams.

---

### B. Modular Tools Package ([`tools/`](file:///home/niklas/Documents/Coding/Hobby/wayland_computer_use_mcp/src/wayland_computer_use_mcp/tools/))
Partitioned into 6 focused domain sub-modules (22 default tools + 2 opt-in tools):
1. **`input_tools.py`** (8 tools):
   - `click`, `double_click`, `right_click`, `hover`, `drag`, `scroll`, `type_text`, `key_combination`.
   - All 8 input tools accept an optional `pid: int | None = None` parameter and consistently return structured `InteractionResult` dictionaries with `ui_changes`.
2. **`navigation_tools.py`** (4 tools):
   - `inspect_ui_tree(pid, max_depth)`: Returns the collapsed 1D interactive widget list with compact IDs (`b1`, `e1`, `c1`, `sw1`, `li1`) and tracked `states`.
   - `interact_with_node(target, action, text, value, role, node_id, pid, settle_timeout_ms, ...)`: Core semantic-first dispatcher (`target` primary, `node_id` deprecated alias, Playwright-style `role` filter) with event-driven settlement and structured `ui_changes` diffs.
   - `batch_actions(actions, pid)`: Executes consecutive interactions atomically with optional per-step `"role"` filtering, step-by-step delta tracking, and automatic `batch_failure.png` trace capture on live failures.
   - `watch_ui_events(timeout_ms, pid)`: Streams accessibility events directly from AT-SPI2 D-Bus to track asynchronous UI changes.
3. **`preset_tools.py`** (1 tool):
   - `preset_workflow(action, name, description, steps, pid)`: Manages and executes reusable multi-step interaction macro presets (`list`, `run`, `view`, `save`, `delete`).
4. **`visual_tools.py`** (1 tool):
   - `capture_window_frame(crop_box, save_artifact, pid)`: Returns MCP in-memory `ImageContent` by default to prevent storage leaks, saving to disk only when `save_artifact=True`.
5. **`process_tools.py`** (5 tools):
   - `launch_app(target, args, restart, cwd)`: Spawns GUI apps or scripts with automatic venv discovery, returning the **initial interactive UI tree immediately** to eliminate an extra tool call.
   - `restart_app(pid)`: Gracefully terminates and re-launches an active process, preserving original launch arguments and working directory. Strictly validates process ownership: unmanaged or user-opened applications raise `PermissionError` to preserve desktop consistency.
   - `list_managed_apps()`: Lists all active sessions and prunes dead/externally-closed PIDs.
   - `terminate_app(pid)`: Clean termination (`SIGTERM` escalated to `SIGKILL`).
   - `get_app_logs(pid, lines)`: Retrieves console output and crash tracebacks from circular buffer.
6. **`system_tools.py`** (3 default tools + 2 opt-in tools):
   - `clipboard_read`, `clipboard_write`: Portal-first clipboard (`org.freedesktop.portal.Clipboard`) with `wl-paste`/`wl-copy`, `qdbus6`/`qdbus`, and `xclip` fallbacks.
   - `window_control(action, pid)`: State control (`minimize`, `maximize`, `restore`, `close`).
   - Opt-in (`register_desktop_installer_tools`): `install_to_desktop`, `uninstall_from_desktop` for universal Linux `.desktop` launcher registration.

---

### C. Accessibility Package ([`a11y/`](file:///home/niklas/Documents/Coding/Hobby/wayland_computer_use_mcp/src/wayland_computer_use_mcp/a11y/))
- **`client.py`**:
  - Resolves AT-SPI bus address via D-Bus session or unix domain socket (`/run/user/{uid}/at-spi/bus_0`).
  - Automatically sets `org.a11y.Status.ScreenReaderEnabled = True` and `IsEnabled = True` on D-Bus upon connection, enabling accessibility trees in Chromium and Electron apps without requiring special CLI flags.
- **`events.py`**:
  - Real-time AT-SPI2 D-Bus signal monitoring (`Object:StateChanged`, `ChildrenChanged`, `TextChanged`, `Window:Activate`).
  - Implements `wait_for_settled(timeout_ms, pid)` to guarantee UI event settlement before returning deltas.
- **`tree.py`**:
  - Deep traversal across complex modern widget trees (Libadwaita `ToolbarView -> Overlay -> ScrolledWindow -> Viewport -> Box`).
  - Prunes non-interactive layout containers into a compact 1D element list (`flatten_tree`).
  - Maintains `_node_cache` mapping compact IDs (`b1`, `e1`, `c1`) to D-Bus object paths and geometry.
- **`actions.py`**:
  - **Hybrid Semantic-First Execution Dispatcher (Zero Cursor Hijack)**:
    1. Level 1 programmatic execution (silent, zero pointer motion):
       - Direct activations: Invokes `org.a11y.atspi.Action.DoAction(0)` on buttons, checkboxes, tabs, and menu items.
       - Range controls: Sets `action="set_value"` atomically via `org.a11y.atspi.Value` (`CurrentValue`) without physical drag gestures.
       - Text editing: Modifies text via `EditableText.SetTextContents()` or `Text.SetSelection()`.
       - Semantic Parent Bubbling: If an agent targets a static child label/icon lacking an action interface, walks up the ancestor hierarchy to trigger the parent container (`push button`, `check box`, or `Selection.SelectChild` on `list`/`table`) directly over D-Bus.
    2. Level 2 physical fallback: Only when AT-SPI calls are unsupported/fail (e.g. custom canvas widgets, dropdown popovers) does execution fall back to coordinate clicks and keyboard events with desktop offset compensation.
- **`constants.py`**: Role mappings, prefix tables (`b` for buttons, `e` for entries, `c` for comboboxes, `s` for sliders), and ignored roles.
- **`synthetic.py`**: Fallback mock tree generator for testing without an active AT-SPI bus.

---

### D. Compositors Package ([`compositors/`](file:///home/niklas/Documents/Coding/Hobby/wayland_computer_use_mcp/src/wayland_computer_use_mcp/compositors/))
Modular abstraction layer supporting heterogeneous Wayland compositors:
- **`base.py`**: Defines `CompositorBackend` Protocol, `WindowGeometry`, and `DisplayInfo` dataclasses.
- **`kwin.py`**: KDE Plasma 6 KWin scripting driver. Uses `w.frameGeometry` to eliminate the 25px GTK/Libadwaita drop shadow, raises windows, and keeps permission dialogs above.
- **`gnome.py`**: GNOME Shell / Mutter backend via D-Bus and AT-SPI window bounds fallback.
- **`wlroots.py`**: Tiling compositor backend for Sway (`swaymsg`) and Hyprland (`hyprctl`).
- **`generic.py`**: Universal fallback querying AT-SPI root window dimensions.
- **`__init__.py`**: Auto-detects active compositor on startup and provides synchronous helper wrappers (`query_window_geometry`, `activate_and_raise_window`, `set_window_state`).

---

### E. Portal Package & Input Dispatch ([`portal/`](file:///home/niklas/Documents/Coding/Hobby/wayland_computer_use_mcp/src/wayland_computer_use_mcp/portal/))
- **`remotedesktop.py`**:
  - Manages `org.freedesktop.portal.RemoteDesktop` and `org.freedesktop.portal.Clipboard` (`RequestClipboard`, `SetSelection`, `SelectionRead`, `SelectionTransfer`) session lifecycle.
  - Dual-dispatch: Sends `NotifyPointerMotionAbsolute` for pointer movement and `NotifyPointerAxisDiscrete` for wheel scroll ticks.
  - Caches and restores portal approval tokens (`restore_token`), eliminating repetitive permission prompts.
- **`screencast.py`**:
  - Manages `org.freedesktop.portal.ScreenCast` session and PipeWire stream.
  - Optional `PyGObject` GStreamer PipeWire `appsink` pipeline (`[gstreamer]` extra), `gst-launch-1.0` CLI capture, native compositor capture (`spectacle`/`grim`/`gnome-screenshot`), and `org.freedesktop.portal.Screenshot` D-Bus fallback.
  - Pure in-memory streaming by default with rolling cache cleanup.
- **`input.py`**:
  - `InputDispatcher`: Dispatches clicks, drags, hovers, and fast 2ms keyboard text typing (`action_delay_seconds=0.0` default).
  - Automatic clipboard fallback: For text longer than 30 characters or newlines, pastes directly via `Ctrl+V` when a clipboard backend succeeds and falls back to key typing otherwise.
- **`session.py`**:
  - Coordinates portal handles, EIS file descriptors, frame cropping, and cross-DE `--virtual` / `--virtual-window` compositor lifecycles (`detect_virtual_compositor_cmd()` + socket polling).
  - Exposes `close()` method to cleanly terminate screencast pipelines, remote desktop sessions, and libei clients.

---

### F. Low-Level EIS Driver ([`libei.py`](file:///home/niklas/Documents/Coding/Hobby/wayland_computer_use_mcp/src/wayland_computer_use_mcp/libei.py))
- Direct ctypes bindings to `libei.so.1` for Emulated Input Server injection on `seat0`.
- Emulation state lifecycle: Tracks emulating devices per handle (`_start_emulating`, `_stop_emulating`).
- Discrete scrolling support: Calls `ei_device_scroll_discrete(dev, steps_x * 120, steps_y * 120)` adhering strictly to the libei 120-unit standard without conflicting simultaneous delta events.
- Positive dy represents scrolling down (increasing Y); negative dy represents scrolling up.
- Clean connection management: Exposes `@property is_connected` and cleans up file descriptors and contexts upon `close()`.

---

### G. Reactive Delta & Project Artifacts ([`delta.py`](file:///home/niklas/Documents/Coding/Hobby/wayland_computer_use_mcp/src/wayland_computer_use_mcp/delta.py) & [`artifacts.py`](file:///home/niklas/Documents/Coding/Hobby/wayland_computer_use_mcp/src/wayland_computer_use_mcp/artifacts.py))
- **`delta.py`**: Computes state differences between pre- and post-action snapshots (`modified`, `appeared`, `hidden`). Returns structured `InteractionResult` dictionaries exposing Node ID, Role, Label, and States without premature truncation.
- **`artifacts.py`**: Stores project presets and screenshots in `<project_root>/.agents/artifacts/` (discovering `WAYLAND_MCP_PROJECT_DIR` or common project markers like `.agents`, `.git`, `pyproject.toml`, `package.json`, `Cargo.toml`, `go.mod`, `CMakeLists.txt`), with XDG cache fallback (`$XDG_CACHE_HOME/wayland-computer-use-mcp/artifacts/`) when no project workspace exists.

---

## 3. Interaction Paradigm: Hybrid Semantic-First Model

The server enforces a **Hybrid Semantic-First Execution Model**:
1. **Unobtrusive Programmatic AT-SPI Execution**: Level 1 actions (`DoAction`, `EditableText`, `Text`) execute atomically via D-Bus without moving the user's physical mouse pointer, eliminating cursor hijacking and false hover triggers.
2. **Deterministic Physical Fallback**: For custom widgets, canvas elements, or when AT-SPI calls fail, falls back smoothly to clamped coordinate clicks and keypresses with full multi-monitor desktop offset support.
3. **Multi-Monitor Global Geometry**: Full unified desktop coordinates `(wx, wy)` without modulo wrapping, correctly projecting clicks and captures onto any monitor configuration.
4. **Continuous Delta-to-Delta Flow**: Actions return actionable UI deltas with full widget descriptors, eliminating redundant full-tree re-queries.

---

## 4. Test Suite & Validation Status

| Test Suite | File | Tests | Pass Rate |
| :--- | :--- | :--- | :--- |
| **Live GUI E2E Suite** | [`tests/test_live_example_app.py`](file:///home/niklas/Documents/Coding/Hobby/wayland_computer_use_mcp/tests/test_live_example_app.py) | 15 | **100%** (15/15) |
| **Unit & Non-Live Tests** | `tests/test_*.py` | 139 (`-m "not live"`) | **100%** (139/139) |
| **Host AT-SPI Shielding** | `tests/test_virtual_session.py` | 1 (`live` / `virtual` marker) | Isolated `XDG_RUNTIME_DIR` |
| **Ruff Code Style** | `src/`, `tests/` | 70 files | **100%** Clean (0 errors) |
| **Packaging (PEP 561 / Wheel)**| `pyproject.toml`, `py.typed` | Pure `py3-none-any` | Built via `uv build` |
| **Vulture Dead Code** | `src/` | 58 files | **0 Dead Code** (Code 0) |
