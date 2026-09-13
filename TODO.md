# Wayland Computer-Use MCP: Architecture Roadmap & Backlog

This document defines the roadmap, completed milestones, architectural guidelines, and backlog for `wayland-computer-use-mcp`.

---

## 1. Completed Core Milestones (Production Ready v0.1.0)

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
  - `events.py`: Real-time AT-SPI2 D-Bus signal listener (`Object:StateChanged`, `ChildrenChanged`, `TextChanged`, `Window:Activate`) with `wait_for_settled(timeout_ms, pid)` synchronization.
  - `tree.py`: Deep hierarchy traversal (up to depth 24) and 1D semantic flattener (`flatten_tree`) producing compact token-efficient IDs (`b1`, `e1`, `c1`, `s1`, `k1`).
  - `actions.py`: Hybrid semantic dispatcher (`interact_with_node`, `do_accessible_action`, `do_accessible_set_text`, `do_accessible_text_action`).
  - In-built viewport auto-scrolling: When target widgets reside outside visible viewport, automatically dispatches discrete wheel scroll events to bring them into view.
- [x] **Portal Package (`portal/`)**:
  - `remotedesktop.py`: RemoteDesktop portal session with restore token caching and discrete scroll ticks (`NotifyPointerAxisDiscrete`).
  - `screencast.py`: GStreamer PipeWire pipeline with dynamic stride/pixel format decoding (`RGBA`, `BGRA`, `BGRx`, `RGBx`, `RGB`), in-memory streaming, and pipeline `stop()` cleanup.
  - `input.py`: Dual-dispatch pointer motion and fast text typing with automated clipboard fallback for strings > 30 characters.
  - `session.py`: Coordinates portal handles, EIS file descriptors, frame cropping, and Set-of-Marks visual labeling with session `close()` and rolling cache.
- [x] **Modular Tools Package (`tools/`)** (23 Active Tools):
  - Partitioned into `input_tools.py`, `navigation_tools.py`, `visual_tools.py`, `process_tools.py`, and `system_tools.py`.
  - Added `watch_ui_events` and `list_managed_apps`.
- [x] **Low-Level EIS Driver (`libei.py`)**:
  - Discrete wheel step injection via `ei_device_scroll_discrete`.
  - State machine tracking emulating devices per handle and `@property is_connected`.

### B. Hybrid Semantic-First Execution Model
- [x] Real-time visual tracking: Always moves cursor visibly over target (`hover(cx, cy)`) for human transparency and spatial verification.
- [x] Programmatic AT-SPI execution: Direct invocation of `DoAction`, `EditableText`, and `Text` interfaces.
- [x] Physical fallback: Custom widgets, dropdown popovers (`GtkDropDown`), checkboxes, and sliders fall back seamlessly to coordinate clicks and keypresses.
- [x] Actionable Delta-to-Delta Navigation: `delta.py` outputs full node ID, role, name/label, and states (`focused`, `checked`, `selected`) without premature truncation, allowing agents to chain interactions without querying `inspect_ui_tree`.
- [x] Zero-Call Startup: `launch_app` immediately returns the initial 1D interactive element tree.

### C. Developer Superpowers, Safety & Lifecycle
- [x] **Crash-to-Context Exception Interception**: Stderr ring buffer interceptor automatically binds full Python tracebacks into tool responses when an action causes a crash or emits an exception.
- [x] **Client-Side Coordinate Clamping**: Coordinates strictly clamped to $[0 \le x \le W, 0 \le y \le H]$.
- [x] **Geometry Divergence Detection**: Window movement/resizing yields immediate abort to prevent misclicks into other surfaces.
- [x] **Hardware User Preemption**: Pauses automated interactions while physical mouse activity is detected.
- [x] **Graceful Shutdown & Signal Trapping**: Intercepts `SIGINT`, `SIGTERM`, and `atexit` to terminate spawned child processes and close portal sessions.
- [x] **Process Lifecycle Auto-Pruning**: `process.py` and `process_tools.py` automatically prune dead or externally closed PIDs and provide `list_managed_apps`.
- [x] **Screenshot Memory Leak Resolution**: Purged hardcoded paths; returns pure in-memory `ImageContent` unless `save_artifact=True` is explicitly passed; managed rolling cache.
- [x] **CLI Options**: Added formatted `-h`/`--help` and `-v`/`--version` handlers.
- [x] **MCP Prompts & Resources**: Exposes `wayland_automation_guide` and `wayland://system_prompt`.
- [x] **Packaging & Distribution Readiness**: Added `py.typed` (PEP 561), PyPI classifiers, and GitHub Actions CI workflow.

---

## 2. Active Backlog & Next-Gen Roadmap (v0.2.0+)

### A. Desktop-Native App & Window Picker Modal (Zoom / MS Teams Style)
- [ ] **Native GTK4 / Libadwaita Picker Feasibility & Implementation**:
  - **Concept**: When an agent calls `launch_app()` without parameters, or calls a new dedicated `select_active_window()` tool, spawn an interactive desktop modal window for human or agent selection.
  - **Architecture**:
    - Build with `PyGObject` (`Gtk.ApplicationWindow`, `Adw.PreferencesWindow`, `Adw.TabView` / `Adw.ViewStack`) which is already installed and available on Linux Wayland systems.
    - Two-Tab Design:
      1. **"Running Windows" Tab**: Queries KWin / Mutter / wlroots IPC or AT-SPI for all active top-level windows. Displays a visual grid/list with application icons, window titles, PIDs, and geometry.
      2. **"Installed Applications" Tab**: Enumerates `.desktop` files in `/usr/share/applications` and `~/.local/share/applications`, showing application name, icon, comment, and launch command with a search/filter bar.
  - **Human-in-the-Loop Workflow**: Allows the human user to click the exact window/app they want the AI agent to automate, immediately binding that PID/window to the active MCP session.
  - **Headless Fallback**: If running in headless/CI mode (`WAYLAND_MCP_DISPLAY_MODE="virtual"`), bypass modal presentation and provide an indexed text list via tool return.

### B. External App Attachment Safeguards & Permission Policies
- [ ] **External vs. Managed App Separation**:
  - When attaching to an existing running application (e.g. via PID or window picker), mark the session as `managed=False` / `externally_attached=True`.
  - **Termination Safeguard**: Strictly prohibit `terminate_app` or server shutdown hooks from killing externally attached processes to prevent killing the user's primary browser, IDE, or terminal.
- [ ] **App Allowlist & Security Boundaries**:
  - Provide a configuration file (`wayland_mcp_config.toml` or `WAYLAND_MCP_ALLOWED_APPS`) specifying which applications or binary names can be automated.
  - Refuse automation or coordinate injection into blacklisted surfaces (e.g. password managers, terminal emulators running sudo).

### C. Multi-Monitor HiDPI & Fractional Scale Transformation
- [ ] **Scale Factor Matrix**:
  - Integrate `DisplayInfo.scale` from `CompositorBackend.get_active_displays()` into `portal/input.py` and `security.py`.
  - When Wayland fractional scaling (125%, 150%, 175%, 200%) is active, translate logical window coordinates to physical surface pixels seamlessly.

### D. Cross-Framework & Cross-Platform Test Integration
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

### E. Artifact Retention & Storage Policies
- [ ] **Configurable Retention**:
  - Implement rolling TTL or file count limits (e.g., max 20 screenshots, age > 24 hours) for `~/.cache/wayland_computer_use_mcp/artifacts/`.
  - Add tool or CLI flag to purge cached artifacts on command.

### F. Source Code Line Mapping (Python GUI Profiler)
- [ ] **D-Bus Node to Python Variable Resolver**:
  - When analyzing local workspaces, inspect Python GUI source files (`.py`) for variable declarations (e.g. `self.btn_submit = Gtk.Button(...)` or `self.username_input = QLineEdit()`).
  - Correlate accessible names and roles to local variable names and attach `source_loc: "main.py:42"` to tree nodes in `inspect_ui_tree`.

