# Wayland Computer-Use MCP: Architecture Roadmap & Backlog

This document defines the roadmap, completed milestones, architectural guidelines, and backlog for `wayland-computer-use-mcp`.

---

## 1. Completed Core Milestones (Production Ready)

### A. Modular Domain Package Architecture
- [x] **Compositors Package (`compositors/`)**:
  - `base.py`: Protocol definition, `WindowGeometry`, `DisplayInfo`, `WindowState`.
  - `kwin.py`: KDE Plasma 6 KWin scripting backend with client-side drop-shadow compensation.
  - `gnome.py`: GNOME Shell / Mutter backend via D-Bus and AT-SPI window bounds.
  - `wlroots.py`: Sway (`swaymsg`) and Hyprland (`hyprctl`) IPC socket integrations.
  - `generic.py`: Universal fallback querying AT-SPI root window dimensions.
  - `__init__.py`: Dynamic auto-detection and synchronous wrappers.
- [x] **Accessibility Package (`a11y/`)**:
  - `client.py`: Automatic enablement of `org.a11y.Status.ScreenReaderEnabled = True` on AT-SPI session bus (unlocks Chromium and Electron accessibility trees).
  - `tree.py`: Deep hierarchy traversal (up to depth 24) and 1D semantic flattener (`flatten_tree`) producing compact token-efficient IDs (`b1`, `e1`, `c1`, `s1`, `k1`).
  - `actions.py`: Hybrid semantic dispatcher (`interact_with_node`, `do_accessible_action`, `do_accessible_set_text`, `do_accessible_text_action`).
  - In-built viewport auto-scrolling: When target widgets reside outside visible viewport, automatically dispatches discrete wheel scroll events to bring them into view.
- [x] **Portal Package (`portal/`)**:
  - `remotedesktop.py`: RemoteDesktop portal session with restore token caching and discrete scroll ticks (`NotifyPointerAxisDiscrete`).
  - `screencast.py`: GStreamer PipeWire pipeline with dynamic stride/pixel format decoding (`RGBA`, `BGRA`, `BGRx`, `RGBx`, `RGB`) and pipeline `stop()` cleanup.
  - `input.py`: Dual-dispatch pointer motion and fast text typing with automated clipboard fallback for strings > 30 characters.
  - `session.py`: Coordinates portal handles, EIS file descriptors, frame cropping, and Set-of-Marks visual labeling with session `close()`.
- [x] **Modular Tools Package (`tools/`)**:
  - Partitioned into `input_tools.py`, `navigation_tools.py`, `visual_tools.py`, `process_tools.py`, and `system_tools.py`.
- [x] **Low-Level EIS Driver (`libei.py`)**:
  - Discrete wheel step injection via `ei_device_scroll_discrete`.
  - State machine tracking emulating devices per handle and `@property is_connected`.

### B. Hybrid Semantic-First Execution Model
- [x] Real-time visual tracking: Always moves cursor visibly over target (`hover(cx, cy)`) for human transparency and spatial verification.
- [x] Programmatic AT-SPI execution: Direct invocation of `DoAction`, `EditableText`, and `Text` interfaces.
- [x] Physical fallback: Custom widgets, dropdown popovers (`GtkDropDown`), checkboxes, and sliders fall back seamlessly to coordinate clicks and keypresses.
- [x] Bypasses purged: Removed phantom D-Bus bypasses (`do_accessible_select`).

### C. Developer Superpowers & Safety
- [x] **Crash-to-Context Exception Interception**: Stderr ring buffer interceptor automatically binds full Python tracebacks into tool responses when an action causes a crash or emits an exception.
- [x] **Client-Side Coordinate Clamping**: Coordinates strictly clamped to $[0 \le x \le W, 0 \le y \le H]$.
- [x] **Geometry Divergence Detection**: Window movement/resizing yields immediate abort to prevent misclicks into other surfaces.
- [x] **Hardware User Preemption**: Pauses automated interactions while physical mouse activity is detected.

---

## 2. Active Backlog & Next-Gen Production Features

### A. Tool Output Formatting & Labeled Screenshot Verification
- [ ] **Agent Payload & Schema Verification**:
  - Audit and strictly validate tool return formats across all 21 tools for fast LLM parsing and schema stability.
  - Standardize error envelopes and warning callouts (e.g. GitHub-style alerts in Markdown returns).
- [ ] **Fix & Verify Labeled Screenshot Taking (`take_labeled_screenshot`)**:
  - Verify Set-of-Marks overlay accuracy: ensure bounding box rectangles, badges, and font sizes scale cleanly across varying window sizes and fractional display scales.
  - Verify badge contrast and readability (pill background, bold contrasting numbering).
  - Verify dual content blocks: ensure both the local markdown image preview link and base64 MCP `ImageContent` block are properly received by clients (Claude Desktop, Cursor, Antigravity, Cline).
  - Fix any window frame coordinate offset regressions (e.g. client vs. buffer coordinate alignment).

---

### B. Cross-Framework & Cross-Platform Test Integration
- [ ] **Modern Python GUI Framework Test Rigs**:
  - **PyQt6 / PySide6**:
    - Build a dedicated reference test rig (`examples/test_qt6_app.py`) with `QPushButton`, `QLineEdit`, `QComboBox`, `QSlider`, and `QTableWidget`.
    - Verify Qt 6 `QAccessible` tree export quality, 1D ID indexing, and `interact_with_node` execution.
  - **CustomTkinter / Modern Tkinter**:
    - Build test rig (`examples/test_customtkinter_app.py`) evaluating modern Tkinter styling and Linux ATK bridge compatibility.
  - **Flet / NiceGUI / Textual**:
    - Add reference testing for Flutter/Python (Flet) and webview/terminal hybrid interfaces.
- [ ] **Cross-Platform Web & Desktop Application Verification**:
  - **Electron / Chromium Desktop Apps**:
    - Test integration with applications like VS Code, Slack, or a minimal React/Electron test app running with `--enable-features=UseOzonePlatform --ozone-platform=wayland --force-renderer-accessibility`.
    - Verify that `ScreenReaderEnabled = True` auto-unmasks Electron webview DOM nodes.
  - **Tauri / WebKitGTK Applications**:
    - Validate semantic tree inspection and coordinate clicks on Tauri / WebKitGTK Linux applications.

---

### C. Real-Time D-Bus Event Monitoring (`watch_ui_events`)
- [ ] **Background AT-SPI2 Signal Listener**:
  - Create an asynchronous D-Bus signal listener in `a11y/` subscribing to:
    - `org.a11y.atspi.Event.Object:StateChanged` (e.g. enabled, checked, focused)
    - `org.a11y.atspi.Event.Object:ChildrenChanged` (dynamic widget addition/removal)
    - `org.a11y.atspi.Event.Object:TextChanged` (entry updates, status labels)
    - `org.a11y.atspi.Event.Window:Activate`
- [ ] **The `watch_ui_events(timeout_ms=500)` Tool**:
  - Allows an agent to await UI settling without polling or re-querying the whole tree.
  - Returns only the mutated delta items:
    ```json
    {
      "settled": true,
      "mutations": [
        {"action": "state-changed", "id": "b1", "property": "enabled", "value": false},
        {"action": "text-changed", "id": "lbl_status", "value": "Saved successfully"}
      ]
    }
    ```

---

### D. Multi-Monitor HiDPI & Fractional Scale Transformation
- [ ] **Scale Factor Matrix**:
  - Integrate `DisplayInfo.scale` from `CompositorBackend.get_active_displays()` into `portal/input.py` and `security.py`.
  - When Wayland fractional scaling (125%, 150%, 175%, 200%) is active, translate logical window coordinates to physical surface pixels seamlessly.

---

### E. Server Lifecycle & Graceful Shutdown
- [ ] **Signal Trapping & Process Cleanup**:
  - Register `signal.signal(signal.SIGINT, ...)` and `SIGTERM` in `server.py`.
  - On shutdown, invoke `global_portal_session.close()` to release GStreamer pipelines, EIS sockets, and portal sessions.
  - Terminate any running child processes managed by `process.py` to prevent orphaned background processes.

---

### F. Standalone Agent Prompting Specification
- [ ] **System Prompt Extension File (`instructions/system_prompt_extension.md`)**:
  - Create a standardized instructions file documenting:
    - The Tree-First navigation mindset (`inspect_ui_tree` -> `interact_with_node`).
    - The 3-tier fallback matrix (Level 1: Semantic AT-SPI -> Level 2: Clamped coordinate click -> Level 3: Visual Set-of-Marks screenshot).
    - Lifecycle loops (`launch_app` -> `restart_app` -> `get_app_logs`).
    - Ready-to-copy snippets for Claude Desktop, Cursor, Cline, and Antigravity.

---

### G. Source Code Line Mapping (Python GUI Profiler)
- [ ] **D-Bus Node to Python Variable Resolver**:
  - When analyzing local workspaces, inspect Python GUI source files (`.py`) for variable declarations (e.g. `self.btn_submit = Gtk.Button(...)` or `self.username_input = QLineEdit()`).
  - Correlate accessible names and roles to local variable names and attach `source_loc: "main.py:42"` to tree nodes in `inspect_ui_tree`.
