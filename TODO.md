# Wayland Computer-Use MCP: Architecture Roadmap & TODO

This document defines the roadmap, design principles, feature backlog, and architectural guidelines for `wayland-computer-use-mcp`.

> [!NOTE]
> **Reference Architecture**: Consult the open-source `computer-use-linux` (Rust implementation) for proven Linux desktop interaction patterns, protocol abstractions, and performance optimizations.

---

## 1. Strategic Positioning: Python GUI Developer Superpower vs. Cross-Program Substrate

### Underlying Substrate: Universal Linux Program Support
Because tree parsing is built via the standard **AT-SPI2 accessibility D-Bus** and input injection via **Wayland XDG Portals / libei**, cross-program support is inherent to the architecture:
- **Native Applications**: Firefox, LibreOffice, GIMP, GNOME Calculator, Evince, etc.
- **Electron / Chromium Applications**: VS Code, Slack, Discord, Obsidian when launched with standard Wayland and accessibility flags:
  ```bash
  --enable-features=UseOzonePlatform --ozone-platform=wayland --force-renderer-accessibility
  ```

### Specialization & Superpower: Python GUI Development and Debugging
Generic "computer use" is brittle for AI models because the surface area of potential actions is vast. By tailoring the toolset explicitly for **Python GUI developers and autonomous debugging agents**, we provide deep closed-loop integrations that general OS automation tools cannot match:
- **The Closed Lifecycle Loop**: `launch_app`, `restart_app`, and `process.py` virtualenv auto-detection are purpose-built for iterative code modification and hot-reloading.
- **Deterministic UI State Verification**: An AI debugging a Python script can inspect the semantic UI tree to immediately verify if a layout change broke a button's callback or if a text entry field is raising validation flags—without spending thousands of image tokens on screenshots.

### Toolkit Compatibility & AT-SPI Standardization Matrix
| Toolkit / Framework | AT-SPI2 Export Quality | Strategy / Requirements |
| :--- | :--- | :--- |
| **PyQt5 / PyQt6 / PySide6** | **Flawless (Native)** | Out of the box, `QPushButton`, `QLineEdit`, `QComboBox`, etc. broadcast names, states, and roles over D-Bus. |
| **GTK 3 / GTK 4 + Libadwaita** | **Flawless (Native)** | Built-in via GTK accessibility layer. All standard widgets expose actions and states. |
| **Tkinter** | **Limited / Partial** | Weak default accessibility on Linux. Provide setup guidance or bundle `atk-bridge` / `pyatspi` adapters. |
| **Kivy / Pygame / Custom Canvas** | **None (Raw Pixels)** | Draw directly to framebuffers with no widget tree. Gracefully falls back to Set-of-Marks coordinate & visual screenshot pipeline. |

---

## 2. Tree-First Semantic Navigation (Playwright for Linux Desktops)

### Problem Statement
Relying predominantly on screenshot captures (`capture_window_frame`, `take_labeled_screenshot`) creates extreme token overhead (often 1,500–3,000+ tokens per image block), increases latency, and causes visual coordinate ambiguity on complex or fluid layouts.

### Solution: Collapsed 1D Semantic Interactive Element List
Make **semantic tree inspection the primary interaction paradigm**, reserving screenshots for deliberate visual verification checkpoints.

- [ ] **Tree Pruner & 1D Flattener**:
  - Filter out purely structural intermediate containers (`Gtk.Box`, `Gtk.Overlay`, `Gtk.Viewport`, unlabelled layout frames).
  - Extract only **interactive widgets** (`button`, `entry`, `combo box`, `slider`, `check box`, `radio button`, `tab`, `link`, `menu item`) and **informative context labels**.
  - Flatten the nested tree into a compact, token-efficient 1D list:
    ```json
    [
      {"id": "b1", "role": "button", "name": "Click Me! (0 clicks)", "enabled": true, "center": [112, 186]},
      {"id": "e1", "role": "entry", "name": "Username", "value": "", "valid": false, "center": [360, 303]},
      {"id": "c1", "role": "combo box", "name": "Environment", "selected": "Development", "center": [123, 442]},
      {"id": "s1", "role": "slider", "name": "Volume", "value": 25.0, "center": [360, 558]}
    ]
    ```

- [ ] **Native Direct Element Interaction (`interact_with_node`)**:
  - Introduce `interact_with_node(node_id: str, action: str = "click")`.
  - If the element implements `org.a11y.atspi.Action` over D-Bus (e.g. `GtkButton`, `QAbstractButton`), invoke `DoAction(0)` directly.
  - **Benefits**: Completely bypasses cursor movement, coordinate translations, window drop-shadow offsets, and compositor painting grace periods. Button clicks become instantaneous, 100% deterministic, and headless, operating exactly like a headless browser driver.
  - Keep coordinate-based input via `libei` / portal strictly as a Level 2 fallback for custom widgets lacking action interfaces.

- [ ] **Targeting by Label & ID**:
  - Support both `click_element_by_label(label="...")` and direct ID addressing (`interact_with_node(node_id="b1")`).
  - Gracefully match substrings and case-insensitive names.

- [ ] **Multi-Action Sequencing & Batching**:
  - Introduce `batch_actions(actions=[...])` tool to dispatch consecutive interactions (e.g. select tab -> focus field -> enter text -> submit) in a single round-trip without intermediate image captures.
  - Treat screenshots as on-demand verification checkpoints, not mandatory steps between micro-interactions.

---

## 3. Semantic Delta Tracking & Event Monitoring (`watch_ui_events`)

### Problem Statement
Constantly re-polling `inspect_ui_tree` after every micro-action consumes excessive tokens and CPU cycles, re-transmitting large chunks of unchanged tree structures.

### Solution: React-Style Virtual DOM Mutation Log
- [ ] **Native D-Bus Event Listener**:
  - Implement a background listener thread in `a11y.py` that subscribes to AT-SPI2 D-Bus signals:
    - `org.a11y.atspi.Event.Object:StateChanged`
    - `org.a11y.atspi.Event.Object:ChildrenChanged`
    - `org.a11y.atspi.Event.Object:TextChanged`
    - `org.a11y.atspi.Event.Window:Activate`
- [ ] **The `watch_ui_events` / `watch_ui_state` Tool**:
  - Provide a tool that allows the agent to await UI settling (e.g. 100–300ms) after an action and receive only the mutated elements:
    ```json
    {
      "mutations": [
        { "action": "state-changed", "id": "btn_4", "property": "enabled", "value": false },
        { "action": "text-changed", "id": "lbl_status", "value": "Submission successful!" },
        { "action": "child-added", "id": "dialog_modal", "role": "alert", "name": "Success" }
      ]
    }
    ```
  - Reduces token cost of continuous validation to almost zero.

---

## 4. Python Runtime & PyGUI Debugging Integration

### Automatic "Crash-to-Context" Payload Binding
- [ ] **Intercept & Bind stderr/stdout Tracebacks**:
  - Hook `process.py`'s thread-safe 200-line ring buffer (`collections.deque`).
  - If any tool invocation (click, type, action) causes the managed process to terminate or emit a Python traceback into stderr, intercept the standard tool response.
  - Automatically append the full Python traceback and recent log context directly into the MCP error/response block.
  - **Benefit**: If an agent's click triggers an `AttributeError` or `TypeError` in the application code, the agent immediately receives the exact traceback in context and can fix the file directly without wondering why the window disappeared.

### GUI Framework Component Profiler & Code Source Line Mapping
- [ ] **Source Code Mapping**:
  - Map D-Bus accessible object paths, names, and accessible descriptions back to Python widget variable declarations in source code (e.g. `submit_btn` in PyQt or `Gtk.Button` with id in GTK).
  - Attempt to resolve and annotate tree nodes with file and line references (e.g., `main.py:42`) when debug symbols or clear variable naming patterns exist in the local project workspace.
  - Highlight matching variable names in `inspect_ui_tree` so the agent connects UI bugs directly to codebase coordinates.

### Optimized Flattened Payload for Python GUI Debugging
- [ ] **Consolidated Execution Context**:
  - Structure inspection outputs to combine process health, console logs, and UI state:
    ```json
    {
      "application_status": "running",
      "pid": 49201,
      "recent_console_logs": [
        "[WARNING] QTargetClash: layout constraint violated on element 'btn_submit'"
      ],
      "ui_tree_snapshot": [
        { "id": "e1", "role": "entry", "name": "Username Input", "value": "", "valid": false },
        { "id": "b1", "role": "button", "name": "Submit", "enabled": false }
      ]
    }
    ```

---

## 5. Visual Grounding & "Smart Crop" Region-of-Interest (ROI) Capture

- [ ] **Window-Level "Smart Crop"**:
  - Full-screen monitor capture includes large swathes of desktop wallpaper, system trays, and unrelated windows, wasting thousands of vision tokens.
  - Query window geometry from `kwin.py` (or compositor backend) and perform an in-memory Pillow crop:
    ```python
    window_image = monitor_image.crop((x, y, x + width, y + height))
    ```
  - Transmit only the cropped window bounding box to the agent, reducing image token footprint by 60–80%.
- [ ] **Adaptive Visual Grounding**:
  - Fall back to Set-of-Marks labeled screenshots only when:
    1. The target application uses a raw graphics canvas engine (Kivy, Pygame) lacking an AT-SPI tree.
    2. `watch_ui_events` indicates zero mutations after a direct action.
    3. The agent explicitly requests visual asset/styling verification (icons, gradients, alignment).

---

## 6. Multi-Desktop Environment Support & Auto Compositor Switcher

### Problem Statement
Currently, window geometry query, raising, and keep-above features are implemented specifically for KDE KWin via KWin scripting over D-Bus (`kwin.py`). To run universally across Linux distributions (Ubuntu default GNOME, Fedora, Arch), the MCP server must support GNOME Shell / Mutter, wlroots, and headless environments.

- [ ] **Unified Compositor Interface (`compositor/base.py`)**:
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
- [ ] **Auto-Compositor Detector**:
  - Inspect `$XDG_CURRENT_DESKTOP`, `$DESKTOP_SESSION`, and running processes (`kwin_wayland`, `gnome-shell`, `sway`, `hyprland`).
  - Instantiate `KWinDriver`, `GnomeDriver`, `WlrootsDriver`, or `FallbackDriver` automatically.
- [ ] **GNOME Shell / Mutter Driver (`compositor/gnome.py`)**:
  - Support Mutter via `org.gnome.Mutter.DisplayConfig` and D-Bus APIs.
  - Fall back to AT-SPI accessible window bounds when compositor scripting APIs are restricted.
- [ ] **wlroots Driver (`compositor/wlroots.py`)**:
  - Support Sway (`swaymsg`) and Hyprland (`hyprctl`) IPC sockets for geometry query and window focus.

---

## 7. Tool Logic Refinement, Safety & Built-In Ergonomics

- [ ] **Strict Precondition Enforcement**:
  - Fail-fast with clear diagnostics if no application is active or managed (`require_active_app()`).
  - Auto-focus and unminimize before every action.
- [ ] **Client-Side Shadow Auto-Compensation**:
  - Generalize shadow offset compensation across all toolkits (GTK 3, GTK 4, Qt 6, Electron) to guarantee zero pixel misalignment.
- [ ] **Smart Input Fallbacks**:
  - Auto-click to focus when coordinates are supplied to `type_text(text, x=..., y=...)`.
  - Fallback from `libei` character typing to clipboard paste (`NotifyKeyboardKeycode` Ctrl+V) when non-standard unicode characters are encountered.
- [ ] **Multi-Monitor Display Scaling (HiDPI / Fractional Scale)**:
  - Detect output scale factors to scale coordinate injection seamlessly on 125%, 150%, and 200% displays.
- [ ] **Transient Popup / Context Menu Tracking**:
  - Track temporary `xdg_popup` surfaces (menus, popovers) and correctly offset coordinate clicks into popup bounds.

---

## 8. Agent Prompting Specification & Packaging Suite

To prevent foundational models from defaulting to token-heavy pixel clicking and unneeded screenshot queries, bundle explicit automation guidelines across distribution channels.

### Distribution Channels
1. **Integrated FastMCP Server Prompt (`server.py`)**:
   - Embed guidelines into the FastMCP server description and tool docstrings so connected agents adopt the tree-first mindset automatically.
2. **Repository-Level Configuration (`.clinerules` / `.cursorrules`)**:
   - Place rules at the root of developer workspaces so modern coding agents (Cline/Roo Code, Cursor, Windsurf) treat them as immutable operating principles.
3. **Standalone Instruction Profile (`instructions/system_prompt_extension.md`)**:
   - Provide [`instructions/system_prompt_extension.md`](instructions/system_prompt_extension.md) for custom orchestrators (LangChain, AutoGen, custom system prompts).

### Core Directives Enforced:
- **Tree-First Paradigm**: Never click raw pixel coordinates if a semantic text/ID alternative exists.
- **Multi-Modal Fallback Matrix**:
  1. *Level 1*: Direct AT-SPI action (`interact_with_node`).
  2. *Level 2*: Targeted coordinate input from tree bounding box (`click(x, y)`).
  3. *Level 3*: Visual screenshot fallback (`capture_window_frame` / `take_labeled_screenshot`).
- **Application Lifecycle Loops**: Modify code -> `restart_app` -> verify tree -> check crash traceback.
- **Anti-Patterns**:
  - ❌ Never screenshot after every action just to "confirm." Use `watch_ui_events` or tree node checks.
  - ❌ Never use relative mouse dragging when a slider or value node exists.
  - ❌ Never guess text field coordinates when programmatic selection is available.
