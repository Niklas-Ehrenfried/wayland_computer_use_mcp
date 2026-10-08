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
- [x] **Modular Tools Package (`tools/`)** (24 Active Tools):
  - Partitioned into `input_tools.py`, `navigation_tools.py`, `visual_tools.py`, `process_tools.py`, and `system_tools.py`.
  - Added `restart_app(pid)` as an official FastMCP tool, strictly limited to MCP-launched applications to protect external user applications.
  - Added `watch_ui_events` and `list_managed_apps`.
  - Added optional `pid: int | None = None` parameter across all 8 input tools for explicit target application switching.
  - Consolidated internal helpers (`click_element_by_label`, `check_app_liveness`, `get_window_geometry`, `focus_window`) into internal domain modules, removing them from public server re-exports.
- [x] **Low-Level EIS Driver (`libei.py`)**:
  - Discrete wheel step injection via `ei_device_scroll_discrete`.
  - State machine tracking emulating devices per handle and `@property is_connected`.

### B. Hybrid Semantic-First Execution Model
- [x] Level 1 Programmatic Value Setting: `action="set_value"` on `scale`/`slider` widgets updates `org.a11y.atspi.Value.CurrentValue` atomically over D-Bus with zero mouse movement.
- [x] Semantic Parent Bubbling: Automatically traverses widget ancestor hierarchy when clicking static text labels or inner icons, activating parent actionable containers (`button`, `checkbox`, `Selection.SelectChild` on `list`/`table`) programmatically with zero physical mouse movement.
- [x] Programmatic AT-SPI execution: Direct invocation of `DoAction`, `EditableText`, `Value`, `Selection`, and `Text` interfaces.
- [x] Physical fallback: Custom widgets, dropdown popovers (`GtkDropDown`), and unexposed canvas controls fall back seamlessly to coordinate clicks and keypresses.
- [x] Actionable Delta-to-Delta Navigation: `delta.py` outputs full node ID, role, name/label, and states (`focused`, `checked`, `selected`) without premature truncation, allowing agents to chain interactions without querying `inspect_ui_tree`.
- [x] Zero-Call Startup: `launch_app` immediately returns the initial 1D interactive element tree.
- [x] Full Set-of-Marks Visual Coverage: Labeled screenshots consume `tree["interactive_elements"]` directly (including GTK4 `switch`, `list item`, `tree item`, `table cell`), rendering compact semantic IDs (`[b1]`, `[e1]`, `[sw1]`) on badges.

### C. Developer Superpowers, Safety & Lifecycle
- [x] **Crash-to-Context Exception Interception**: Stderr ring buffer interceptor automatically binds full Python tracebacks into tool responses when an action causes a crash or emits an exception.
- [x] **Client-Side Coordinate Clamping**: Coordinates strictly clamped to $[0 \le x \le W, 0 \le y \le H]$.
- [x] **Geometry Divergence Detection**: Window movement seamlessly updates baseline position and coordinate offsets without error; only window resize triggers an abort to prevent misclicks after layout changes.
- [x] **Hardware User Preemption**: Pauses automated interactions while physical mouse activity is detected.
- [x] **Graceful Shutdown & Signal Trapping**: Intercepts `SIGINT`, `SIGTERM`, and `atexit` to terminate spawned child processes and close portal sessions.
- [x] **Process Lifecycle Auto-Pruning**: `process.py` and `process_tools.py` automatically prune dead or externally closed PIDs and provide `list_managed_apps`.
- [x] **Screenshot Memory Leak Resolution**: Purged hardcoded paths; returns pure in-memory `ImageContent` unless `save_artifact=True` is explicitly passed; managed rolling cache.
- [x] **CLI Options**: Added formatted `-h`/`--help` and `-v`/`--version` handlers.
- [x] **MCP Prompts & Resources**: Exposes `wayland_automation_guide` and `wayland://system_prompt`.
- [x] **Packaging & Distribution Readiness**: Added `py.typed` (PEP 561), PyPI classifiers, and GitHub Actions CI workflow.
- [x] **Native Compositor Screenshot Engine Fallback**: Integrated `spectacle` (KDE Plasma), `grim` (wlroots/Sway), and `gnome-screenshot` (GNOME) into `screencast.py` to capture actual live desktop and window frames with zero permission popups, eliminating mock template fallbacks.
- [x] **Consolidated FastMCP Surface (24 Tools Total)**: Retired redundant `take_labeled_screenshot` tool, leaving clean `capture_window_frame` and `interact_with_node` as primary visual grounding and interaction primitives.
- [x] **Unified Artifact Storage**: Relocated screenshots to `.agents/artifacts/screenshots` and presets to `.agents/artifacts/presets` with helper module `artifacts.py`.
- [x] **Single-Source Input Injection**: Fixed dual-injection conflict in `portal/input.py` by using `ei_client` exclusively when devices are active and `rd_client.notify_pointer_axis` for reliable Wayland scrolling, resolving multi-click and double-trigger anomalies.
- [x] **AT-SPI Target ID Resolution Stability**: Fixed `_resolve_target_node_id` to strictly return compact node IDs (`b1`, `t2`) when matching by label, ensuring Level 1 / Level 2 actions dispatch to exact coordinates.
- [x] **Host Accessibility Bus Shielding**: Resolved temporary D-Bus socket collision to ensure `/run/user/1000/at-spi/bus_0` remains uninterrupted, keeping 100% of live integration tests green (14/14 passed).

---

## 2. Active Roadmap: Unsupervised Operation & Live Preview Companion (v0.1.1)

### A. Automatic Window Dimension Cropping for Unsupervised Captures
- [ ] **Auto-Crop Virtual / Background Frame Captures**:
  - Automatically query target application window geometry `[wx, wy, ww, wh]` via AT-SPI or KWin scripting inside virtual sessions.
  - Automatically crop the raw full-screen virtual canvas (e.g. 1280x800) to the exact window bounds `(ww, wh)` before saving to `.agents/artifacts/screenshots/`.
  - Provide an assertion in tests verifying `img.width == ww` and `img.height == wh`.

### B. Secondary Session Live Preview Window (Floating Companion PiP)
- [ ] **Lightweight Floating Companion Window (`ui/preview.py`)**:
  - **Bottom-Right Screen Docking**: Small, sleek GTK4/Libadwaita preview window that pops up in the bottom-right corner of the user's primary monitor.
  - **Live Visual Feed**: Displays a live rendering of the secondary/unsupervised Wayland display socket (via PipeWire stream or periodic compositor frame grab).
  - **Movable & Resizable**: Freely movable and resizable by the user to monitor background agent activities without window focus disruption.
  - **Full-Screen Toggle**: Double-click or press `F11` to toggle between compact picture-in-picture (PiP) and full-screen preview.
  - **Configurable MCP Toggle**:
    - Add `preview_window: bool = True` to `Config` in `config.py`.
    - Override via environment variable `WAYLAND_MCP_PREVIEW_WINDOW=true/false`.
    - Agent tool or CLI flag to dynamically hide or show the preview companion.

### C. Unsupervised Live Integration Suite (`tests/test_unsupervised_with_preview.py`)
- [ ] **Isolated Unsupervised Test Runner**:
  - Spawns background virtual compositor with completely isolated `XDG_RUNTIME_DIR` (temp directory) to protect host desktop and host AT-SPI bus.
  - Runs the full 9-component GUI test suite (`test_gui_app.py`) inside the background session.
  - Validates companion preview window initialization, frame rendering, and automatic window cropping.

---

## 3. Backlog: Future Features (v0.2.0+)

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

### B. Nested Agent-Only Sandbox Session & Picture-in-Picture (PiP) Preview Window
- [ ] **Dedicated Nested Compositor (`cage` / `gamescope`)**:
  - **Zero User Disturbance**: Launch MCP-managed application processes inside an isolated nested Wayland display socket (e.g. `WAYLAND_DISPLAY=wayland-agent`).
  - **Virtual Pointer & Keyboard**: Agent's rapid mouse clicks, drags, and keystrokes are confined to the nested session, never stealing the human user's physical mouse pointer, window focus, or keyboard.
  - **Automatic Permission Granting**: Because the MCP server owns the nested compositor, all screencasting and virtual input operations have 100% native access without any XDG Desktop Portal security prompts.
- [ ] **Floating Picture-in-Picture (PiP) Companion Preview**:
  - Lightweight companion window built with `PyGObject` (GTK4 / Libadwaita) that docks cleanly in the bottom-right corner of the user's primary monitor.
  - **Live Visual Feed**: Displays a smooth, real-time preview of the agent's nested desktop activities.
  - **Interactive Controls**:
    - "Expand to Fullscreen" toggle to inspect application state in high resolution.
    - "Takeover" button allowing the human user to inject mouse/keyboard inputs into the nested session directly.
    - "Minimize to Tray / Picture-in-Picture" controls.
- [ ] **Session Mode Configuration**:
  - Configurable via `WAYLAND_MCP_SESSION_MODE="shared"` (default, interacts with user's desktop directly) or `"nested"` (runs in the isolated session with PiP companion).
  - Integrates with the App/Window Picker Modal: user can select whether to launch an application directly on their desktop or into the isolated Agent Sandbox with PiP.

### C. External App Attachment Safeguards & Permission Policies
- [ ] **External vs. Managed App Separation**:
  - When attaching to an existing running application (e.g. via PID or window picker), mark the session as `managed=False` / `externally_attached=True`.
  - **Termination Safeguard**: Strictly prohibit `terminate_app` or server shutdown hooks from killing externally attached processes to prevent killing the user's primary browser, IDE, or terminal.
- [ ] **App Allowlist & Security Boundaries**:
  - Provide a configuration file (`wayland_mcp_config.toml` or `WAYLAND_MCP_ALLOWED_APPS`) specifying which applications or binary names can be automated.
  - Refuse automation or coordinate injection into blacklisted surfaces (e.g. password managers, terminal emulators running sudo).

### D. Multi-Monitor HiDPI & Fractional Scale Transformation
- [ ] **Scale Factor Matrix**:
  - Integrate `DisplayInfo.scale` from `CompositorBackend.get_active_displays()` into `portal/input.py` and `security.py`.
  - When Wayland fractional scaling (125%, 150%, 175%, 200%) is active, translate logical window coordinates to physical surface pixels seamlessly.

### E. Cross-Framework & Cross-Platform Test Integration
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

### F. Artifact Retention & Storage Policies
- [ ] **Configurable Retention**:
  - Implement rolling TTL or file count limits (e.g., max 20 screenshots, age > 24 hours) for `~/.cache/wayland_computer_use_mcp/artifacts/`.
  - Add tool or CLI flag to purge cached artifacts on command.

### G. Source Code Line Mapping (Python GUI Profiler)
- [ ] **D-Bus Node to Python Variable Resolver**:
  - When analyzing local workspaces, inspect Python GUI source files (`.py`) for variable declarations (e.g. `self.btn_submit = Gtk.Button(...)` or `self.username_input = QLineEdit()`).
  - Correlate accessible names and roles to local variable names and attach `source_loc: "main.py:42"` to tree nodes in `inspect_ui_tree`.

### H. Dynamic UI Visibility Diffing & State Change Tracking (`ui_changes`)
- [ ] **Accurate Element Appearance / Disappearance Diffing**:
  - **Identified Gap**: When interacting with dynamic filtering widgets (e.g., clicking "Completed" or "Pending" in a `Gtk.ListBox`), `interact_with_node` currently returns empty `ui_changes: {"modified":[],"appeared":[],"hidden":[]}` even though elements visually disappear.
  - **Root Causes**:
    - Filtered or removed items may either remain in the AT-SPI hierarchy with toggled state flags (`showing`/`visible` flag cleared) instead of being removed from the tree.
    - Child items in deeply nested container rows may exceed the snapshot depth comparison limit.
  - **Action Items**:
    - Update `delta.py` and `tree.py` to evaluate state transitions on accessible nodes (specifically checking `showing` / `visible` / `defunct` states).
    - Ensure filtered-out widgets that lose their `showing` state are properly categorized into `hidden: [...]` with their labels and IDs.
    - Ensure newly displayed elements are detected and surfaced in `appeared: [...]`.

### I. Agent Instructions & Workflow Synthesis (Exploratory Walkthrough ➔ Presets)
- [ ] **Adaptive Agent Workflow Guidance (`wayland_automation_guide`)**:
  - **Two-Phase Automation Pattern**:
    1. **Phase 1: Exploratory Walkthrough**: The AI coding agent performs an initial manual walkthrough (inspecting UI tree, validating coordinates, tapping buttons, capturing verification frames).
    2. **Phase 2: Workflow Synthesis**: Armed with deep knowledge of the application code and previous exploratory results, the agent synthesizes reusable preset workflows (`.agents/artifacts/presets/*.json`) via `preset_workflow`.
    3. **Phase 3: Fast & Token-Efficient Replays**: Subsequent test runs, CI validations, and repetitive UI verification tasks execute the compiled preset in a single atomic MCP tool call, eliminating repetitive roundtrips and drastically reducing LLM token consumption.
  - Update server-side system prompts and documentation in `instructions/` and `prompts/` to explicitly prescribe this workflow to client agents.


