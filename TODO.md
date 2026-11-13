# Wayland Computer-Use MCP: Architecture Roadmap & Dual-Mode Plan

This document tracks completed milestones and the active **Dual-Mode Roadmap** for `wayland-computer-use-mcp`:
1. **Primary Mode (Independent Agent Work — Playwright Model)**: Run apps in an isolated/nested visible or headless Wayland compositor window (`--virtual-window` / `--virtual`) so the agent never hijacks the user's host mouse, keyboard focus, or clipboard while remaining live-observable.
2. **Opt-In Mode (Live Collaborative Help)**: Let the user explicitly pick and hand over an existing desktop window via a human-in-the-loop picker modal with strict attachment safeguards (`managed=False`).

---

## 1. Completed Milestones (v0.1.0 ➔ v0.1.1)

### A. Modular Domain Package Architecture
- [x] **Compositors Package (`compositors/`)**:
  - `base.py`: Protocol definition, `WindowGeometry`, `DisplayInfo`, `WindowState`.
  - `kwin.py`: KDE Plasma 6 KWin scripting backend with client-side drop-shadow compensation.
  - `gnome.py`: GNOME Shell / Mutter backend via D-Bus and AT-SPI window bounds.
  - `wlroots.py`: Sway (`swaymsg`) and Hyprland (`hyprctl`) IPC socket integrations.
  - `generic.py`: Universal fallback querying AT-SPI root window dimensions.
  - `__init__.py`: Dynamic auto-detection and synchronous wrappers.
- [x] **Accessibility Package (`a11y/`)**:
  - `client.py`: Pooled AT-SPI D-Bus client + automatic `org.a11y.Status.ScreenReaderEnabled = True` (unlocks Chromium/Electron accessibility trees).
  - `events.py`: Real-time AT-SPI2 D-Bus signal listener (`Object:StateChanged`, `ChildrenChanged`, `TextChanged`, `Window:Activate`) with `wait_for_settled(timeout_ms, quiet_period_ms, pid)` synchronization.
  - `tree.py`: Deep hierarchy traversal (depth 24), 1D semantic flattener (`flatten_tree`) producing compact IDs (`b1`, `e1`, `c1`, `s1`, `k1`) and preserving compact `states` flags (`visible`, `enabled`, `showing`, `focused`, `checked`, `selected`, `expanded`, `pressed`).
  - `actions.py`: Cursor-free hybrid semantic dispatcher (`invoke_node_action`, `do_accessible_action`, `do_accessible_set_text`, `do_accessible_text_action`).
- [x] **Portal & Zero-CLI-Dependency Infrastructure (`portal/`, `clipboard.py`)**:
  - `remotedesktop.py`: RemoteDesktop portal session with restore token caching, `org.freedesktop.portal.Clipboard` (`RequestClipboard`, `SetSelection`, `SelectionRead`, `SelectionTransfer` via Unix FD pipes), and discrete scroll ticks (`NotifyPointerAxisDiscrete`).
  - `screencast.py`: Optional `PyGObject` GStreamer PipeWire pipeline (`[gstreamer]` extra) + `gst-launch-1.0` CLI capture + native utilities (`spectacle`, `grim`, `gnome-screenshot`) + non-interactive `org.freedesktop.portal.Screenshot` D-Bus fallback.
  - `input.py`: Dual-dispatch pointer motion, fast 2ms keystroke injection (`action_delay_seconds=0.0` default), and automatic clipboard paste fallback for strings > 30 chars (degrading gracefully to key typing if no clipboard backend exists).
  - `clipboard.py`: Portal Clipboard first, then `wl-copy`/`wl-paste`, `qdbus6`/`qdbus`, and `xclip`.
  - `session.py`: Cross-DE virtual/headed compositor spawning via `detect_virtual_compositor_cmd()` with $XDG_RUNTIME_DIR socket polling, EIS file descriptors, frame cropping, and clean `close()`.
- [x] **Modular Tools Package (`tools/`)** (22 Default Tools + 2 Opt-In Tools):
  - Partitioned into `input_tools.py`, `navigation_tools.py`, `preset_tools.py`, `visual_tools.py`, `process_tools.py`, and `system_tools.py`.
  - Retired redundant `take_labeled_screenshot` tool; kept `capture_window_frame` pure in-memory by default (`save_artifact=True` opts into disk storage in `.agents/artifacts/screenshots`).
  - Opt-in desktop launcher tools (`install_to_desktop`, `uninstall_from_desktop`) moved behind `--enable-desktop-installer` / `WAYLAND_MCP_ENABLE_DESKTOP_INSTALLER=1` with universal targets (Python/venv, scripts, binaries, AppImage, Flatpak, URLs).
  - Optional `pid: int | None = None` across all 8 input tools; `restart_app(pid)` strictly restricted to MCP-managed applications.

### B. Playwright-Inspired Reliability, Speed & Zero Cursor Hijacking
- [x] **Pure-Python Zero-C-Build Core Packaging**: Removed unused `pycairo` and moved `PyGObject` to optional `[gstreamer]` extra in `pyproject.toml`, leaving pure core dependencies (`fastmcp`, `dbus-fast`, `pillow`).
- [x] **Zero-Hijack Level 1 AT-SPI Execution**: Removed unconditional mouse cursor hover and viewport auto-scroll movement prior to Level 1 AT-SPI invocation; cursor/focus only move on explicit pointer tools or when Level 1 is unsupported and Level 2 fallback triggers.
- [x] **Playwright-Style `role` Semantic Locators**: Added `role: str | None = None` filter to `interact_with_node` and `batch_actions` (`target="Save", role="push button"`) to deterministically disambiguate identically named labels/buttons.
- [x] **Event-Driven UI Delta Settlement**: `wrap_with_delta` uses real-time AT-SPI `global_event_listener.wait_for_settled()` when connected instead of fixed `time.sleep()` pauses.
- [x] **Trace-on-Failure Diagnostics**: `batch_actions` automatically captures `batch_failure.png` (`failure_screenshot`) when a live step fails.
- [x] **Dynamic Visibility & State Diffing (`delta.py`, `tree.py`)**: `flatten_tree()` preserves `states` and `compute_ui_delta()` classifies widgets losing/gaining `showing`/`visible` flags (e.g., `Gtk.ListBox` filtering) into `hidden` / `appeared`.
- [x] **Cross-DE Visible Isolated Window (`--virtual-window`)**: Added `virtual_headed` (`--virtual-window`, `--isolated-window`, `WAYLAND_MCP_VIRTUAL_HEADED=1`) and `detect_virtual_compositor_cmd()` preferring `weston` (cross-DE across KDE, GNOME, Sway, Hyprland) before `kwin_wayland` / `sway`.
- [x] **Two-Phase Workflow Prompts (`instructions/system_prompt_extension.md`)**: Bundled package prompt data synced with `preset_workflow` and `interact_with_node` guidance.

---

## 2. Active Roadmap: Dual-Mode Architecture (v0.2.0)

### Track A: Primary Mode — Full Playwright-Style Isolated Sandbox Session
Goal: Make `--virtual-window` / `--virtual` self-contained with zero portal prompts and full Level 2 + visual support on every compositor (KDE, GNOME, Sway, Hyprland).
- [ ] **Isolated Private D-Bus & Runtime Environment per Virtual Session**:
  - Automatically provision a private `XDG_RUNTIME_DIR` + `dbus-run-session` + private `at-spi` bus alongside `--virtual-window` / `--virtual` (building on `tests/test_virtual_session.py`) so host desktop windows, clipboard, and AT-SPI bus stay 100% shielded.
- [ ] **Auto-Crop Virtual Frame Captures**:
  - Query target window bounds `[wx, wy, ww, wh]` inside the virtual/nested compositor and automatically crop raw 1280x800 canvas captures to exact `(ww, wh)` dimensions when `window_only` is active.
- [ ] **Nested Level 2 Input & Visual Bridge**:
  - Note: Plain nested `weston` isolates Level 1 AT-SPI windows cleanly across all DEs, while inner `RemoteDesktop`/EIS input portals require either an isolated portal backend (e.g., `kwin_wayland` + `xdg-desktop-portal-kde` on KDE) or direct compositor protocol input (e.g., `wlroots` virtual-pointer/virtual-keyboard protocols / XWayland fallback inside nested `sway` or `weston`). Implement a compositor-agnostic virtual pointer/keyboard bridge inside nested sessions so clicking/dragging works even for custom canvas widgets inside `weston`/`sway`.
- [ ] **Floating Companion Viewer / Interactive PiP Controls**:
  - Optional lightweight preview wrapper for headless runs (`WAYLAND_MCP_PREVIEW_WINDOW`) with fullscreen toggle (`F11`) and human "Takeover" button.

### Track B: Opt-In Mode — Live Collaborative Desktop Window Picker & Safeguards
Goal: When helping a user directly inside an already-open desktop app on their host workspace, require explicit handover and enforce strict ownership boundaries.
- [ ] **Native Window Picker Modal (`select_active_window`)**:
  - Optional GTK4 / Libadwaita picker (`[gstreamer]` / `PyGObject` when installed, or indexed text fallback in headless environments) listing active windows across KWin / GNOME / wlroots and `.desktop` apps so the human explicitly approves which window the agent drives.
- [ ] **External App Attachment Safeguards (`managed=False`)**:
  - Tag windows attached via PID or `select_active_window` as `managed=False` / `externally_attached=True`.
  - Strictly block `terminate_app`, `restart_app`, and server `atexit` shutdown hooks from closing externally attached user applications (browsers, IDEs, terminals).
- [ ] **App Allowlist & Sensitive-Surface Blocklist**:
  - Optional `WAYLAND_MCP_ALLOWED_APPS` / config allowlist + built-in refusal for password managers or privileged terminal prompts.

---

## 3. Verification Matrix & Longer-Term Backlog

### A. Multi-DE VM Verification Matrix (QEMU/KVM + `virt-manager`)
- [ ] **KDE Plasma 6 (KWin)** *(Verified locally; live E2E passing)*
- [ ] **GNOME Shell (Mutter)** — Verify `gnome.py` window geometry, Portal Clipboard, and `--virtual-window` (`weston`) inside Fedora/Ubuntu GNOME VM.
- [ ] **Sway / wlroots** — Verify `wlroots.py` IPC, `grim`, Portal Clipboard (`wl-clipboard` / `xdg-desktop-portal-wlr`), and nested `sway` / `weston` window.
- [ ] **Hyprland** — Verify `hyprctl` JSON geometry, workspace offset math, and `--virtual-window`.

### B. Multi-Monitor HiDPI & Fractional Scale Transformation
- [ ] **Scale Factor Matrix**:
  - Integrate `DisplayInfo.scale` from `CompositorBackend.get_active_displays()` into `portal/input.py` and `security.py` for 125% / 150% / 175% / 200% Wayland fractional scaling.

### C. Cross-Framework GUI Test Rigs
- [ ] **PyQt6 / PySide6 Test Rig (`examples/test_qt6_app.py`)**: Verify `QAccessible` tree export (`QPushButton`, `QLineEdit`, `QComboBox`, `QSlider`, `QTableWidget`).
- [ ] **Tkinter / CustomTkinter Rig** & **Electron / Tauri / WebKitGTK** verification (`ScreenReaderEnabled = True` DOM unmasking).

### D. Developer Ergonomics & Artifact Lifecycle
- [ ] **Configurable Artifact Retention**: Rolling max file count / TTL cleanup (`max_screenshots=20`, `max_age_hours=24`) inside `.agents/artifacts/` and XDG cache.
- [ ] **Source Code Line Mapping (`source_loc`)**: Optional AST scan matching Python GUI widget variables (`self.btn_submit = ...`) to AT-SPI nodes in `inspect_ui_tree`.
