# Wayland Computer-Use MCP: Architecture Roadmap & TODO

This document defines the roadmap, design principles, and planned enhancements for `wayland-computer-use-mcp`.

> [!NOTE]
> **Reference Architecture**: Consult the open-source `computer-use-linux` (Rust implementation) for proven Linux desktop interaction patterns, protocol abstractions, and performance optimizations.

---

## 1. Tree-First Semantic Navigation (Token-Optimized Automation)

### Problem Statement
Relying predominantly on screenshot captures (`capture_window_frame`, `take_labeled_screenshot`) creates extreme token overhead (often 1,500–3,000+ tokens per image block), increases network latency, and causes visual coordinate ambiguity on complex or fluid layouts.

### Solution: Collapsed 1D Semantic Interactive Element List
Make **semantic tree inspection the primary interaction paradigm**, reserving screenshots for deliberate visual verification checkpoints.

- [ ] **Tree Pruner & 1D Flattener**:
  - Filter out purely structural intermediate containers (`Gtk.Box`, `Gtk.Overlay`, `Viewport`, unlabelled generic frames).
  - Extract only **interactive widgets** (`button`, `entry`, `combo box`, `slider`, `check box`, `radio button`, `tab`, `link`, `menu item`) and **informative context labels**.
  - Flatten the nested tree into a compact, token-efficient 1D list:
    ```json
    [
      {"id": 1, "role": "button", "label": "Click Me! (0 clicks)", "center": [112, 186]},
      {"id": 2, "role": "entry", "label": "Search query...", "value": "", "center": [360, 303]},
      {"id": 3, "role": "combo box", "label": "Environment", "selected": "Development", "center": [123, 442]},
      {"id": 4, "role": "slider", "label": "Volume", "value": 25.0, "center": [360, 558]}
    ]
    ```
- [ ] **Targeting by Label & ID**:
  - Enhance `click_element_by_label` to also support direct `element_id` addressing from the flattened list (`click_element(id=1)`).
  - Execute direct AT-SPI2 `do_accessible_action` combined with physical pointer warping, requiring zero image tokens.
- [ ] **Multi-Action Sequencing & Batching**:
  - Establish agent guidelines and tool support for executing multiple contiguous actions before requesting a screenshot (e.g. click tab -> focus field -> enter text -> submit).
  - Introduce a `batch_actions(actions=[...])` tool to dispatch consecutive interactions in a single round-trip without intermediate image captures.
  - Screenshots should be treated as **on-demand verification**, not a mandatory step between each micro-interaction.

---

## 2. Multi-Desktop Environment Support & Auto Compositor Switcher

### Problem Statement
Currently, window geometry query, raising, and keep-above features are implemented specifically for KDE KWin via KWin scripting over D-Bus (`kwin.py`). To run universally on Linux distributions (e.g. Ubuntu default GNOME, Fedora Workstation, Arch Linux), the MCP server must support GNOME / Mutter, wlroots, and headless compositors seamlessly.

- [ ] **Auto Desktop Environment / Compositor Detector**:
  - Detect running desktop session via `$XDG_CURRENT_DESKTOP`, `$DESKTOP_SESSION`, and running process checks (`kwin_wayland`, `gnome-shell`, `sway`, `hyprland`, `weston`).
  - Dynamically instantiate the corresponding compositor driver.
- [ ] **GNOME Shell / Mutter Driver (`gnome.py`)**:
  - Implement window enumeration, geometry detection, and window raising for GNOME Shell.
  - Mechanism options:
    - `org.gnome.Mutter.DisplayConfig` D-Bus interface.
    - `org.gnome.Shell.Eval` (if enabled or via dedicated GNOME Shell extension / portal).
    - Wayland `ext-foreign-toplevel-list-v1` protocol where supported.
    - Fallback to AT-SPI2 accessible window bounds (`get_application_tree` root coordinates).
- [ ] **Unified Compositor Interface (`compositor/base.py`)**:
  - Abstract compositor-specific logic behind a common interface:
    ```python
    class CompositorDriver(ABC):
        @abstractmethod
        def query_window_geometry(self, pid: int | None, title: str | None) -> tuple[int, int, int, int] | None: ...
        @abstractmethod
        def focus_window(self, pid: int | None, title: str | None) -> bool: ...
        @abstractmethod
        def minimize_window(self, pid: int) -> bool: ...
        @abstractmethod
        def unminimize_window(self, pid: int) -> bool: ...
        @abstractmethod
        def ensure_dialogs_above(self) -> bool: ...
    ```
  - Modular implementation files:
    - `src/wayland_computer_use_mcp/compositor/kwin.py` (KDE Plasma)
    - `src/wayland_computer_use_mcp/compositor/gnome.py` (GNOME Shell)
    - `src/wayland_computer_use_mcp/compositor/wlroots.py` (Sway, Hyprland)
    - `src/wayland_computer_use_mcp/compositor/fallback.py` (AT-SPI bounds only)

---

## 3. Tool Logic Refinement & Built-In Ergonomics

- [ ] **Strict Precondition Enforcement**:
  - Ensure all interaction and capture tools immediately fail-fast with a clear error if no application is managed or active (`check_active_app()`).
  - Automatically invoke `focus_window()` and verify process liveness before executing input.
- [ ] **Client-Side Shadow Auto-Compensation**:
  - Expand client-side shadow compensation beyond KWin `frameGeometry` to handle CSD shadows across all Wayland toolkits (GTK 3, GTK 4, Qt 6, Electron).
- [ ] **Smart Input Fallbacks**:
  - When typing text, automatically attempt direct `libei` character typing; if unmapped characters or missing keyboard devices occur, fallback to clipboard write + portal `NotifyKeyboardKeycode` Ctrl+V paste.
  - Automatically click to focus when coordinates are supplied to `type_text(text, x=..., y=...)`.
- [ ] **Multi-Monitor Display Scaling (HiDPI / Fractional Scale)**:
  - Detect screen scale factors (`output.scale`) to automatically scale `(x, y)` coordinate injection on fractional scaled displays (125%, 150%, 200%).
- [ ] **Transient Popup / Context Menu Tracking**:
  - Detect when dropdown menus or popovers open temporary `xdg_popup` surfaces and route clicks into popup coordinates.
