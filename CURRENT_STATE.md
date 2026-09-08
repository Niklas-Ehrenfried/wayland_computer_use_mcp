# Wayland Computer-Use MCP Server: Current Implementation & Architecture

This document provides a component-by-component breakdown of `wayland-computer-use-mcp`, detailing how each module is currently implemented, how it communicates with Wayland and D-Bus protocols, and how the system functions end-to-end.

---

## 1. Architectural Overview

`wayland-computer-use-mcp` is an open-source Model Context Protocol (MCP) server that enables autonomous AI coding agents to observe and interact with graphical desktop applications running under Wayland compositors (specifically KDE Plasma 6 / KWin on Kubuntu, seat0).

```
   ┌─────────────────────────────────────────────────────────────┐
   │                    AI Agent / MCP Client                    │
   └──────────────────────────────┬──────────────────────────────┘
                                  │ stdio (JSON-RPC / MCP)
   ┌──────────────────────────────▼──────────────────────────────┐
   │             server.py (FastMCP Tool Registry)               │
   └──────┬──────────────┬──────────────┬──────────────┬─────────┘
          │              │              │              │
          ▼              ▼              ▼              ▼
     portal.py        a11y.py        kwin.py      process.py
  (ScreenCast &     (AT-SPI2 DBus  (KWin Script  (Lifecycle &
  RemoteDesktop)     Tree Parsing)  Auto-Focus)   Ring Buffer)
          │              │              │
          ▼              │              │
      libei.py           │              │
   (Direct EIS Seat      │              │
   Input Injection)      │              │
          │              │              │
   ┌──────▼──────────────▼──────────────▼────────────────────────┐
   │            Wayland Compositor & D-Bus Session               │
   │      (org.freedesktop.portal.Desktop, KWin, AT-SPI2)        │
   └─────────────────────────────────────────────────────────────┘
```

---

## 2. Component-by-Component Implementation

### A. FastMCP Server Entrypoint ([`src/wayland_computer_use_mcp/server.py`](src/wayland_computer_use_mcp/server.py))
- **Role**: Exposes tools over the MCP JSON-RPC protocol via `mcp.server.fastmcp.FastMCP`.
- **Tool Categories**:
  - **Process Lifecycle**: `launch_app`, `restart_app`, `terminate_app`, `get_app_logs`, `check_app_liveness`.
  - **Visual & Tree Grounding**: `capture_window_frame`, `take_labeled_screenshot`, `inspect_ui_tree`, `focus_window`, `get_window_geometry`.
  - **Input Injection**: `click`, `double_click`, `right_click`, `hover`, `drag`, `scroll`, `type_text`, `key_combination`, `click_element_by_label`.
  - **Desktop Integration**: `clipboard_read`, `clipboard_write`, `install_to_desktop`, `uninstall_from_desktop`.
- **Precondition & Auto-Focus Integration**:
  - Automatically raises and focuses the managed application window (`focus_window(pid)`) prior to capturing frames or taking labeled screenshots.
  - Returns dual content blocks for screenshots: Markdown image link for chat UI embedding + base64-encoded PNG block compliant with the MCP specification.

---

### B. Portal Pipeline & Frame Capture ([`src/wayland_computer_use_mcp/portal.py`](src/wayland_computer_use_mcp/portal.py))
- **Role**: Manages ScreenCast and RemoteDesktop D-Bus sessions, video stream decoding, and dual-dispatch input injection.
- **Session Handshake**:
  1. Calls `CreateSession` on `org.freedesktop.portal.ScreenCast` and `org.freedesktop.portal.RemoteDesktop`.
  2. Calls `SelectDevices` requesting Pointer and Keyboard access.
  3. Calls `SelectSources` specifying source types (Window = 2, Monitor = 1).
  4. Calls `Start`, waiting for user approval or restoring cached tokens (`restore_token`).
  5. Resolves the PipeWire stream Node ID and EIS socket file descriptor (`eis_fd`).
- **GStreamer PipeWire Pipeline**:
  - Pipeline description:
    ```text
    pipewiresrc path={node_id} keepalive-time=1000 resend-last=true !
    videoconvert ! video/x-raw,format=RGBA !
    appsink name=sink emit-signals=True max-buffers=1 drop=True
    ```
  - **Dynamic Stride & Pixel Format Decoding**: Inspects structure caps negotiated by GStreamer. Correctly decodes `RGBA`, `BGRA`, `BGRx`, `RGBx`, and `RGB` buffers into standard PIL Image instances, eliminating stride misalignment and scanline color noise.
- **Pre-Capture Window Raising**:
  - `capture_frame()` invokes `query_kwin_geometry()` *before* pulling buffers from the GStreamer appsink.
  - Grants a 50ms compositor paint grace period so newly raised windows are actively drawn before pixels are grabbed.
- **Dual-Dispatch Pointer Input**:
  - **Visible Cursor Movement**: Dispatches `NotifyPointerMotionAbsolute` over the XDG RemoteDesktop portal D-Bus interface, which moves the desktop cursor visibly on screen.
  - **Button Clicks**: Simultaneously fires `NotifyPointerButton` via the portal and `button_click` via `libei`.
- **Visual Grounding (Set-of-Marks)**:
  - `generate_labeled_screenshot()` overlays colored boundary boxes and numbered badges onto interactive elements identified via the AT-SPI2 tree.

---

### C. Direct EIS Input Injection ([`src/wayland_computer_use_mcp/libei.py`](src/wayland_computer_use_mcp/libei.py))
- **Role**: Ctypes bindings to system `libei.so.1` for low-level Emulated Input Server (EIS) protocol communication on `seat0`.
- **Capability Enums**:
  - Corrected to 1-indexed constants matching `libei.h`:
    - `EI_DEVICE_CAP_POINTER = 1` (Relative pointer)
    - `EI_DEVICE_CAP_POINTER_ABSOLUTE = 2` (Absolute pointer)
    - `EI_DEVICE_CAP_BUTTON = 3` (Mouse buttons)
    - `EI_DEVICE_CAP_SCROLL = 4` (Scroll wheels)
    - `EI_DEVICE_CAP_KEYBOARD = 5` (Keyboard keys)
    - `EI_DEVICE_CAP_TOUCH = 6`
- **Device Discovery Lifecycle**:
  1. `ei_new_sender(None)` allocates sender context.
  2. `ei_setup_backend_fd(ctx, fd)` connects to the EIS file descriptor obtained via `liboeffis` or D-Bus.
  3. On `EI_EVENT_SEAT_ADDED`, invokes variadic `ei_seat_bind_capabilities()` to request Pointer, Absolute Pointer, Button, Scroll, and Keyboard devices.
  4. On `EI_EVENT_DEVICE_ADDED` and `DEVICE_RESUMED`, identifies device handles via `ei_device_has_capability`.
- **Interaction Methods**:
  - `pointer_motion_absolute(x, y)`: Emulates absolute motion within target boundaries.
  - `button_click(button_code)`: Fires button down, 30ms dwell time, button up.
  - `key_press(keycode, is_down)`: Emulates physical keyboard keypresses.
  - `type_char(char)`: Translates characters into Linux evdev keycodes with automated Shift modifier handling.

---

### D. KWin Scripting & Geometry Resolution ([`src/wayland_computer_use_mcp/kwin.py`](src/wayland_computer_use_mcp/kwin.py))
- **Role**: Communicates with KDE KWin compositor via `org.kde.KWin.Scripting` over the session D-Bus.
- **Client-Side Drop-Shadow Elimination**:
  - GTK 4 and Libadwaita windows render 25px transparent drop shadows into their surface buffers.
  - `kwin.py` prioritizes `w.frameGeometry` over `w.bufferGeometry`:
    ```javascript
    var cg = w.frameGeometry || w.clientGeometry || w.bufferGeometry;
    ```
  - This eliminates the 25.0px offset completely, aligning clicks and Set-of-Marks labels to exact pixel precision.
- **Exact PID Matching & Window Exclusivity**:
  - Prioritizes `targetPid > 0 && w.pid === targetPid` over title matching.
  - Explicitly filters out code editor windows (VS Code, Antigravity, Cursor) that have the target script filename in their title.
  - Emits geometry over a transient `/ReportGeom` D-Bus endpoint for only the single matched window.
- **Window State Control**:
  - If a window is minimized, `w.minimized = false;` restores it.
  - Sets `w.keepAbove = true;` and `workspace.activeWindow = w;` to guarantee foreground presence.

---

### E. Semantic AT-SPI2 Accessibility Inspection ([`src/wayland_computer_use_mcp/a11y.py`](src/wayland_computer_use_mcp/a11y.py))
- **Role**: Queries the accessibility bus to retrieve semantic widget hierarchies and execute direct accessible actions.
- **Connection**:
  - Queries `org.a11y.Bus.GetAddress` or connects directly to unix socket `/run/user/{uid}/at-spi/bus_0`.
- **Target App Resolution**:
  - Inspects root registry `org.a11y.atspi.Registry` children.
  - Matches target application by PID using `org.freedesktop.DBus.GetConnectionUnixProcessID` or `org.a11y.atspi.Application.Id`.
- **Deep Hierarchy Traversal**:
  - Configured with `max_depth = 24` to traverse deep Libadwaita structures (`ToolbarView -> Overlay -> ScrolledWindow -> Viewport -> Box`).
  - Prunes non-interactive structural clutter while extracting bounds, roles, names, and D-Bus object paths.
- **Action Invocation (`do_accessible_action`)**:
  - Calls `org.a11y.atspi.Action.DoAction(action_index=0)` directly on the widget's D-Bus interface.

---

### F. Safety, Security & Fail-Safes ([`src/wayland_computer_use_mcp/security.py`](src/wayland_computer_use_mcp/security.py))
- **`CoordinateClamper`**: Clamps all injected coordinates strictly within `[0, width]` and `[0, height]` of the active surface.
- **`GeometryDetector`**: Tracks baseline surface position and dimensions. If a window moves or resizes unexpectedly while an action is pending, aborts the action to prevent misclicks into other desktop surfaces.
- **`PreemptionManager`**: Monitors hardware cursor movement. If the user moves the physical mouse, automated interactions yield immediately.
- **`ShortcutFilter`**: Blocks hazardous key combinations (Meta/Super, Ctrl+Alt+Del, Alt+F4, VT switching shortcuts).
- **`TokenStore`**: Safely persists and restores ScreenCast session tokens in `~/.cache/wayland-computer-use-mcp/restore_tokens.json`.

---

### G. Process Lifecycle Management ([`src/wayland_computer_use_mcp/process.py`](src/wayland_computer_use_mcp/process.py))
- **Virtualenv Auto-Detection**: Walks up directory trees to automatically find `.venv/bin/python`, `venv/bin/python`, or `.conda/bin/python`.
- **Ring Buffering**: Captures the last 200 lines of stderr/stdout from spawned processes in a thread-safe `collections.deque`.
- **Termination Escalation**: Sends `SIGTERM` followed by graceful wait, escalating to `SIGKILL` if un responsive.

---

### H. Reference Test Application ([`examples/test_gui_app.py`](examples/test_gui_app.py))
- **Technology**: GTK 4 + Libadwaita (`Adw.Application`, `Adw.ToolbarView`, `Adw.ViewStack`).
- **Event Propagation**: Configured with `Gtk.PropagationPhase.BUBBLE` so window-level click monitors do not consume clicks intended for child buttons, entries, or header controls.
- **Pages**:
  1. `Controls & Inputs`: Large pill button with click counter, `Gtk.Entry` text box, `Gtk.DropDown`, horizontal `Gtk.Scale` slider.
  2. `Navigation & Pages`: Sub-stack switcher (`Overview`, `Analytics`, `Settings`).
  3. `Data & Lists`: Hardware acceleration switch, 20 list items.
  4. `Diagnostics`: Real-time application event stream logging every click, drag, and tab transition.
