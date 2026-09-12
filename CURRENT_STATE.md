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
- **Role**: Exposes 21 tools over the MCP JSON-RPC protocol via `mcp.server.fastmcp.FastMCP`.
- **Modularity**: Imports and re-exports tool suites from `wayland_computer_use_mcp.tools.*`, preserving backwards compatibility with older single-module imports while maintaining a cleanly sorted `__all__`.
- **CLI Configuration**: Evaluates arguments (`--live`, `--virtual`, `--window-only`, `--fullscreen`) to initialize configuration before launching the FastMCP transport loop.

---

### B. Modular Tools Package ([`tools/`](file:///home/niklas/Documents/Coding/Hobby/wayland_computer_use_mcp/src/wayland_computer_use_mcp/tools/))
Partitioned into 5 focused domain sub-modules:
1. **`input_tools.py`**:
   - `click`, `double_click`, `right_click`, `hover`, `drag`, `scroll`, `type_text`, `key_combination`.
   - All input actions wrap execution in `wrap_with_delta(pid)` to return real-time reactive UI feedback.
2. **`navigation_tools.py`**:
   - `inspect_ui_tree(pid, max_depth)`: Returns the collapsed 1D interactive widget list with compact IDs (`b1`, `e1`, `c1`).
   - `interact_with_node(node_id, action, text, ...)`: Core semantic-first dispatcher implementing the hybrid execution model.
   - `click_element_by_label(label, role)`: Resolves widgets by human-readable label or role and dispatches click.
   - `batch_actions(actions, pid)`: Executes consecutive interactions atomically without intermediate screenshot latency.
3. **`visual_tools.py`**:
   - `capture_window_frame(crop_box)`: Dual-return format with Markdown embedded image link + base64 PNG block.
   - `take_labeled_screenshot()`: Visual grounding overlay with numbered Set-of-Marks badges.
   - `focus_window(pid)`, `get_window_geometry(pid)`.
4. **`process_tools.py`**:
   - `launch_app`, `restart_app`, `terminate_app`, `check_app_liveness`, `get_app_logs`.
   - Integrates Crash-to-Context error binding: automatically intercepts stderr tracebacks and enriches responses.
5. **`system_tools.py`**:
   - `clipboard_read`, `clipboard_write`: Native Wayland clipboard interaction via `wl-paste` / `wl-copy` with QDBus fallback.
   - `window_control(action, pid)`: State control (`minimize`, `maximize`, `restore`, `close`).
   - `install_to_desktop`, `uninstall_from_desktop`: Linux desktop launcher registration with git worktree detection.

---

### C. Accessibility Package ([`a11y/`](file:///home/niklas/Documents/Coding/Hobby/wayland_computer_use_mcp/src/wayland_computer_use_mcp/a11y/))
- **`client.py`**:
  - Resolves AT-SPI bus address via D-Bus session or unix domain socket (`/run/user/{uid}/at-spi/bus_0`).
  - Automatically sets `org.a11y.Status.ScreenReaderEnabled = True` and `IsEnabled = True` on D-Bus upon connection, enabling accessibility trees in Chromium and Electron apps without requiring special CLI flags.
- **`tree.py`**:
  - Deep traversal (depth up to 24) across complex modern widget trees (Libadwaita `ToolbarView -> Overlay -> ScrolledWindow -> Viewport -> Box`).
  - Prunes non-interactive layout containers into a compact 1D element list (`flatten_tree`).
  - Maintains `_node_cache` mapping compact IDs (`b1`, `e1`, `c1`) to D-Bus object paths and geometry.
- **`actions.py`**:
  - **Hybrid Semantic-First Execution Dispatcher**:
    1. In-built auto-scrolling: If target widget is outside the visible viewport, automatically dispatches `scroll()` so it enters the viewport.
    2. Real-time visual tracking: Visibly moves cursor over target (`hover(cx, cy)`) for human transparency and spatial verification.
    3. Programmatic execution: Invokes `org.a11y.atspi.Action.DoAction(0)`, `EditableText.SetTextContents()`, or `Text.SetSelection()`.
    4. Physical fallback: For custom widgets, dropdown popovers (`GtkDropDown`), checkboxes, or when AT-SPI calls fail, falls back seamlessly to coordinate clicks and keyboard events.
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
  - Manages `org.freedesktop.portal.RemoteDesktop` session lifecycle.
  - Dual-dispatch: Sends `NotifyPointerMotionAbsolute` for visible cursor movement and `NotifyPointerAxisDiscrete` for wheel scroll ticks.
  - Caches and restores portal approval tokens (`restore_token`), eliminating repetitive permission prompts.
- **`screencast.py`**:
  - Manages `org.freedesktop.portal.ScreenCast` session and PipeWire stream.
  - GStreamer pipeline dynamically decodes `RGBA`, `BGRA`, `BGRx`, `RGBx`, `RGB` buffers to PIL images without scanline corruption.
  - Includes `stop()` cleanup method for releasing GStreamer pipelines.
- **`input.py`**:
  - `InputDispatcher`: Dispatches clicks, drags, hovers, and keyboard text typing.
  - Automatic clipboard fallback: For text longer than 30 characters or containing complex unicode, pastes directly via `Ctrl+V`.
- **`session.py`**:
  - Coordinates portal handles, EIS file descriptors, frame cropping, and Set-of-Marks visual labeling.
  - Exposes `close()` method to cleanly terminate screencast pipelines, remote desktop sessions, and libei clients.

---

### F. Low-Level EIS Driver ([`libei.py`](file:///home/niklas/Documents/Coding/Hobby/wayland_computer_use_mcp/src/wayland_computer_use_mcp/libei.py))
- Direct ctypes bindings to `libei.so.1` for Emulated Input Server injection on `seat0`.
- Emulation state lifecycle: Tracks emulating devices per handle (`_start_emulating`, `_stop_emulating`).
- Discrete scrolling support: Calls `ei_device_scroll_discrete(dev, steps_x, steps_y)` alongside `ei_device_scroll_delta` to ensure GTK4 and Wayland compositors register wheel ticks immediately.
- Clean connection management: Exposes `@property is_connected` and cleans up file descriptors and contexts upon `close()`.

---

### G. Reactive Delta & Visual Overlay ([`delta.py`](file:///home/niklas/Documents/Coding/Hobby/wayland_computer_use_mcp/src/wayland_computer_use_mcp/delta.py) & [`overlay.py`](file:///home/niklas/Documents/Coding/Hobby/wayland_computer_use_mcp/src/wayland_computer_use_mcp/overlay.py))
- **`delta.py`**: Computes state differences between pre- and post-action snapshots (`modified`, `appeared`, `hidden`), returning formatted markdown summaries directly to the LLM agent.
- **`overlay.py`**: Renders Set-of-Marks visual markers: color-coded bounding boxes and badge pills `[1]`, `[2]` with system font fallbacks.

---

## 3. Interaction Paradigm: Hybrid Semantic-First Model

The server enforces a **Hybrid Semantic-First Execution Model**:
1. **Observable Cursor Tracking**: Every interaction visibly hovers the pointer over the target widget `(cx, cy)` prior to actuation, giving human operators visual feedback in live mode and validating spatial coordinates.
2. **Programmatic AT-SPI Execution**: Clicks, text entries, and selections execute atomically via AT-SPI D-Bus interfaces (`DoAction`, `EditableText`, `Text`) whenever supported.
3. **Physical Fallback for Complex Surfaces**: Popover menus (`GtkDropDown`), list item selections, and custom canvas widgets fall back smoothly to physical coordinate clicks and keypresses.
4. **Zero Phantom Bypasses**: Bypasses like `Selection.SelectChild` are strictly avoided; scrolling and navigation rely on real physical wheel events and in-built viewport auto-scrolling.

---

## 4. Test Suite & Validation Status

| Test Suite | File | Tests | Pass Rate | Execution Time |
| :--- | :--- | :--- | :--- | :--- |
| **Live GUI E2E Suite** | [`tests/test_live_example_app.py`](file:///home/niklas/Documents/Coding/Hobby/wayland_computer_use_mcp/tests/test_live_example_app.py) | 14 | **100%** (14/14) | 58.83s |
| **Non-Live Unit Tests** | `tests/test_*.py` | 105 | **100%** (105/105) | 55.21s |
| **Ruff Code Style** | `src/`, `tests/` | 58 files | **100%** Clean | 0 errors |
| **Vulture Dead Code** | `src/` | 58 files | **0 Dead Code** | Code 0 |
