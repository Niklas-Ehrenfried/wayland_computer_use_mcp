# Architecture Evaluation, Tool Catalog & Optimization Plan (v0.1.0 ➔ v0.2.0)

This implementation plan provides a comprehensive architectural evaluation of `wayland-computer-use-mcp`, auditing all 23 exposed MCP tools with concrete input/output examples, identifying bloat, redundancies, and blindspots, and proposing targeted optimizations for full-coverage visual labeling.

---

## 1. Executive Architectural Health Check

The repository demonstrates a strong **Tree-First Hybrid Semantic Execution Model**, cutting LLM token consumption by 90–95% compared to vision-only approaches. However, a deep audit reveals **6 specific architectural inefficiencies, bloat items, and blindspots** that should be resolved:

1. **`take_labeled_screenshot` Incomplete Role Coverage & ID Disconnect**:
   - `session.py` (`generate_labeled_screenshot`) walks the tree using a hardcoded, incomplete set of 12 roles, missing GTK4 switches (`toggle button`), checkboxes (`check button`), numeric spinboxes (`spin button`), list items (`list item`), and tree items (`tree item`).
   - Furthermore, `draw_labeled_overlay` labels badges as numeric `[1]`, `[2]`, `[3]`, whereas the agent's semantic ID vocabulary is `b1`, `e1`, `c1`, `s1`, `k1`.
2. **5 Redundant / Unregistered Internal Functions (Surface Bloat)**:
   - `click_element_by_label`, `check_app_liveness`, `restart_app`, `focus_window`, and `get_window_geometry` are defined in tool files and re-exported in `server.py`, but are **not registered as FastMCP tools**.
   - `click_element_by_label` is completely redundant with `interact_with_node(target=label, action="click")`.
   - `check_app_liveness` is redundant with `list_managed_apps()`.
   - `focus_window` and `get_window_geometry` are internal compositor helpers.
   - Only `restart_app(pid)` offers distinct value (restarting an existing app without remembering its launch target).
3. **Implicit Global State in Input Tools (Missing `pid` Parameter)**:
   - The 8 coordinate input tools (`click`, `double_click`, `right_click`, `hover`, `drag`, `scroll`, `type_text`, `key_combination`) do not accept an optional `pid` parameter, relying strictly on mutable global state (`global_portal_session.target_pid`).
4. **Inconsistent Screenshot Artifact Cache Directories**:
   - `session.py` writes to `~/.cache/wayland-computer-use-mcp/screenshots/` while `visual_tools.py` writes to `.agents/artifacts/screenshots/`.
5. **Redundant Tree Traversal during Labeled Screenshot Generation**:
   - `get_application_tree()` already traverses and flattens the accessibility tree (`tree["interactive_elements"]`). `generate_labeled_screenshot()` currently walks the entire tree a second time.
6. **Deadlock Vulnerability in Process Locks**:
   - Successfully fixed in v0.1.0 baseline via `threading.RLock()`.

---

## 2. Comprehensive Tool Catalog (23 Active Tools with I/O Examples)

Below is the complete catalog of all 23 active MCP tools across the 5 domains, with exact input and output payloads.

---

### Category A: Process Lifecycle & Crash Interception (4 Tools)

#### 1. `launch_app`
- **Purpose**: Spawns a Python GUI script or application, binds Wayland portal session, brings window to foreground, and **immediately returns the initial 1D interactive element tree**.
- **Input Example**:
  ```json
  {
    "target": "examples/test_gui_app.py",
    "args": ["--debug"],
    "restart": false,
    "cwd": "/home/niklas/Documents/Coding/Hobby/wayland_computer_use_mcp"
  }
  ```
- **Output Example**:
  ```json
  {
    "status": "launched",
    "pid": 128450,
    "target": "examples/test_gui_app.py",
    "args": ["--debug"],
    "cwd": "/home/niklas/Documents/Coding/Hobby/wayland_computer_use_mcp",
    "session_handle": "/org/freedesktop/portal/desktop/request/1_100/mcp1",
    "restore_token": "token_e3b0c44298fc1c14",
    "eis_connected": true,
    "interactive_elements": [
      {
        "id": "b1",
        "role": "button",
        "name": "Click Me! (0 clicks)",
        "enabled": true,
        "center": [140, 95],
        "bounds": [50, 75, 180, 40]
      },
      {
        "id": "e1",
        "role": "entry",
        "name": "Username",
        "enabled": true,
        "center": [140, 150],
        "bounds": [50, 135, 180, 30]
      }
    ]
  }
  ```

#### 2. `terminate_app`
- **Purpose**: Gracefully shuts down a managed application process (`SIGTERM` with 2.0s grace period, escalating to `SIGKILL`).
- **Input Example**:
  ```json
  {
    "pid": 128450
  }
  ```
- **Output Example**:
  ```json
  {
    "status": "terminated",
    "pid": 128450
  }
  ```

#### 3. `get_app_logs`
- **Purpose**: Retrieves circular buffer console logs (stdout/stderr) and exception tracebacks from the spawned process.
- **Input Example**:
  ```json
  {
    "pid": 128450,
    "lines": 20
  }
  ```
- **Output Example**:
  ```
  [16:10:02] Application initialized in GTK4 Wayland mode.
  [16:10:05] Button b1 activated. Counter incremented to 1.
  ```

#### 4. `list_managed_apps`
- **Purpose**: Scans all tracked processes, automatically prunes dead or externally closed PIDs, and returns active sessions.
- **Input Example**:
  ```json
  {}
  ```
- **Output Example**:
  ```json
  {
    "active_apps_count": 1,
    "target_pid": 128450,
    "apps": [
      {
        "pid": 128450,
        "target": "examples/test_gui_app.py",
        "args": ["--debug"],
        "responsive": true
      }
    ]
  }
  ```

---

### Category B: Semantic Tree Navigation & Synchronization (4 Tools)

#### 5. `inspect_ui_tree`
- **Purpose**: Inspects the accessibility tree via AT-SPI2 D-Bus and returns the collapsed 1D interactive element list with compact IDs (`b1`, `e1`, `c1`), roles, and coordinates.
- **Input Example**:
  ```json
  {
    "pid": 128450,
    "max_depth": 24
  }
  ```
- **Output Example**:
  ```json
  {
    "role": "application",
    "name": "GTK4 Component Testbed",
    "pid": 128450,
    "responsive": true,
    "bounds": [100, 100, 800, 600],
    "interactive_elements": [
      {
        "id": "b1",
        "role": "button",
        "name": "Click Me! (0 clicks)",
        "enabled": true,
        "center": [140, 95],
        "bounds": [50, 75, 180, 40]
      },
      {
        "id": "e1",
        "role": "entry",
        "name": "Search Query",
        "enabled": true,
        "center": [140, 150],
        "bounds": [50, 135, 180, 30]
      },
      {
        "id": "c1",
        "role": "combo box",
        "name": "Options",
        "enabled": true,
        "center": [140, 205],
        "bounds": [50, 190, 180, 30]
      }
    ]
  }
  ```

#### 6. `interact_with_node`
- **Purpose**: Dispatches hybrid semantic interaction (Level 1 AT-SPI `DoAction`, auto-scroll, physical fallback, cursor tracing) and returns post-action UI delta.
- **Input Example**:
  ```json
  {
    "node_id": "b1",
    "action": "click",
    "settle_timeout_ms": 100
  }
  ```
- **Output Example**:
  ```markdown
  Activated b1 via AT-SPI Action.DoAction(0).

  UI Changes:
  • Modified: b1 [button]: text 'Click Me! (0 clicks)' ➔ 'Click Me! (1 clicks)'
  ```

#### 7. `batch_actions`
- **Purpose**: Atomically executes a sequence of consecutive UI operations with fail-fast validation and step-by-step delta tracking.
- **Input Example**:
  ```json
  {
    "pid": 128450,
    "actions": [
      {"action": "type", "node_id": "e1", "text": "niklas@example.com"},
      {"action": "click", "node_id": "b2"},
      {"action": "wait", "ms": 150}
    ]
  }
  ```
- **Output Example**:
  ```json
  {
    "status": "success",
    "executed_steps_count": 3,
    "total_steps": 3,
    "results": [
      "Step 1: Typed text into e1 (niklas@example.com)",
      "Step 2: Activated b2 via AT-SPI. UI Changes: • Modified: lbl1 text 'Saved'",
      "Step 3: Waited 150ms"
    ]
  }
  ```

#### 8. `watch_ui_events`
- **Purpose**: Listens to AT-SPI2 D-Bus accessibility mutation signals (`StateChanged`, `ChildrenChanged`, `TextChanged`, `Window:Activate`) until the UI settles.
- **Input Example**:
  ```json
  {
    "pid": 128450,
    "timeout_ms": 1000
  }
  ```
- **Output Example**:
  ```json
  {
    "settled": true,
    "elapsed_ms": 85,
    "pid": 128450,
    "mutations": [
      {
        "action": "StateChanged",
        "detail": "focused",
        "path": "/org/a11y/atspi/accessible/42",
        "timestamp": 1726242000.15
      }
    ]
  }
  ```

---

### Category C: Boundary-Clamped Physical Input (8 Tools)

#### 9. `click`
- **Purpose**: Dispatches a single mouse click strictly clamped within window geometry bounds.
- **Input Example**:
  ```json
  {
    "x": 140,
    "y": 95,
    "button": "left"
  }
  ```
- **Output Example**:
  ```markdown
  Clicked (140, 95) with left button.

  UI Changes:
  • Modified: b1 [button]: text '0 clicks' ➔ '1 clicks'
  ```

#### 10. `double_click`
- **Purpose**: Dispatches a rapid double-click sequence clamped to window bounds.
- **Input Example**:
  ```json
  {
    "x": 140,
    "y": 150,
    "button": "left"
  }
  ```
- **Output Example**:
  ```markdown
  Double-clicked (140, 150) with left button.
  ```

#### 11. `right_click`
- **Purpose**: Dispatches a right-click to invoke context menus.
- **Input Example**:
  ```json
  {
    "x": 140,
    "y": 95
  }
  ```
- **Output Example**:
  ```markdown
  Right-clicked (140, 95).

  UI Changes:
  • Appeared: m1 [menu] 'Context Menu'
  ```

#### 12. `hover`
- **Purpose**: Translates the pointer over target coordinates without clicking to activate tooltips or hover CSS styles.
- **Input Example**:
  ```json
  {
    "x": 140,
    "y": 95,
    "duration_ms": 300
  }
  ```
- **Output Example**:
  ```
  Hovered at (140, 95) for 300ms.
  ```

#### 13. `drag`
- **Purpose**: Performs a clamped mouse drag gesture between two coordinate pairs.
- **Input Example**:
  ```json
  {
    "start_x": 50,
    "start_y": 200,
    "end_x": 200,
    "end_y": 200
  }
  ```
- **Output Example**:
  ```markdown
  Dragged from (50, 200) to (200, 200).

  UI Changes:
  • Modified: s1 [slider]: value 25.0 ➔ 75.0
  ```

#### 14. `scroll`
- **Purpose**: Dispatches discrete pointer axis wheel ticks (`dx` horizontal, `dy` vertical).
- **Input Example**:
  ```json
  {
    "dx": 0,
    "dy": -3
  }
  ```
- **Output Example**:
  ```markdown
  Scrolled dx=0, dy=-3.
  ```

#### 15. `type_text`
- **Purpose**: Injects keystrokes using evdev keycodes, with automated clipboard paste fallback for text > 30 characters.
- **Input Example**:
  ```json
  {
    "text": "Hello World!",
    "x": 140,
    "y": 150
  }
  ```
- **Output Example**:
  ```markdown
  Typed 12 characters via evdev keyboard injection.

  UI Changes:
  • Modified: e1 [entry]: value '' ➔ 'Hello World!'
  ```

#### 16. `key_combination`
- **Purpose**: Dispatches keyboard hotkeys (e.g. `Ctrl+S`, `Ctrl+Shift+P`) with security blacklisting for dangerous system chords.
- **Input Example**:
  ```json
  {
    "keys": ["ctrl", "s"]
  }
  ```
- **Output Example**:
  ```markdown
  Dispatched key combination: ['ctrl', 's'].
  ```

---

### Category D: Visual Grounding & Set-of-Marks Overlay (2 Tools)

#### 17. `capture_window_frame`
- **Purpose**: Captures target application window PipeWire frame, returning an MCP in-memory `ImageContent` block.
- **Input Example**:
  ```json
  {
    "crop_box": [0, 0, 800, 600],
    "save_artifact": false
  }
  ```
- **Output Example**:
  - `TextContent`: `"Frame captured (800x600) in-memory."`
  - `ImageContent`: `data:image/png;base64,iVBORw0KGgo...`

#### 18. `take_labeled_screenshot`
- **Purpose**: Annotates the window frame with Set-of-Marks numbered/pill badges and cyan bounding boxes, returning image and element legend.
- **Input Example**:
  ```json
  {
    "pid": 128450,
    "save_artifact": false
  }
  ```
- **Output Example**:
  - `TextContent`:
    ```json
    {
      "element_count": 2,
      "elements": [
        {
          "index": 1,
          "id": "b1",
          "role": "button",
          "name": "Click Me! (0 clicks)",
          "center": [140, 95],
          "bounds": [50, 75, 180, 40]
        },
        {
          "index": 2,
          "id": "e1",
          "role": "entry",
          "name": "Username",
          "center": [140, 150],
          "bounds": [50, 135, 180, 30]
        }
      ]
    }
    ```
  - `ImageContent`: `data:image/png;base64,...` (annotated visual with badges).

---

### Category E: OS & Desktop Integration (5 Tools)

#### 19. `clipboard_read`
- **Purpose**: Reads text from the Wayland clipboard via `wl-paste` (or QDBus fallback).
- **Input Example**:
  ```json
  {}
  ```
- **Output Example**:
  ```
  "https://github.com/Niklas-Ehrenfried/wayland_computer_use_mcp"
  ```

#### 20. `clipboard_write`
- **Purpose**: Writes text to the Wayland clipboard via `wl-copy`.
- **Input Example**:
  ```json
  {
    "text": "Copied from MCP Agent"
  }
  ```
- **Output Example**:
  ```
  "Successfully wrote 21 characters to Wayland clipboard."
  ```

#### 21. `window_control`
- **Purpose**: Controls window state (`minimize`, `maximize`, `restore`, `focus`, `close`) across KWin, GNOME Mutter, Hyprland, and Sway.
- **Input Example**:
  ```json
  {
    "action": "maximize",
    "pid": 128450
  }
  ```
- **Output Example**:
  ```json
  {
    "action": "maximize",
    "pid": 128450,
    "success": true,
    "status": "Window action 'maximize' succeeded."
  }
  ```

#### 22. `install_to_desktop`
- **Purpose**: Generates and registers a valid XDG `.desktop` application launcher with worktree detection and version badging.
- **Input Example**:
  ```json
  {
    "app_id": "testbed-gui",
    "name": "Component Testbed",
    "exec_path": "examples/test_gui_app.py",
    "version": "1.0",
    "is_dev": true
  }
  ```
- **Output Example**:
  ```json
  {
    "status": "installed",
    "desktop_file": "/home/niklas/.local/share/applications/testbed-gui.desktop",
    "icon_file": "/home/niklas/.local/share/icons/hicolor/256x256/apps/testbed-gui.png",
    "exec": "/home/niklas/Documents/Coding/Hobby/wayland_computer_use_mcp/.venv/bin/python examples/test_gui_app.py"
  }
  ```

#### 23. `uninstall_from_desktop`
- **Purpose**: Unregisters an installed `.desktop` launcher and deletes badged icons.
- **Input Example**:
  ```json
  {
    "app_id": "testbed-gui"
  }
  ```
- **Output Example**:
  ```json
  {
    "status": "removed",
    "app_id": "testbed-gui"
  }
  ```

---

## 3. Bloat & Redundancy Audit (Items to Consolidate or Prune)

### Finding 1: 5 Unregistered Functions Lingering in Modules & Server Re-exports
In the codebase, five functions are defined in `tools/` and imported into `server.py`, but **never registered on FastMCP**:
- **`click_element_by_label(label, role, pid)`**: Completely redundant with `interact_with_node(target=label, action="click")`.
- **`check_app_liveness(pid)`**: Redundant with `list_managed_apps()` (which auto-prunes and checks responsiveness) and `is_responsive()`.
- **`get_window_geometry(pid)`**: Redundant with `inspect_ui_tree()` (which returns root bounds).
- **`focus_window(pid)`**: Internal helper; already exposed to agents via `window_control(action="focus")`.
- **`restart_app(pid)`**: Useful shortcut, but currently unregistered.

> [!NOTE]
> **Recommendation**:
> 1. Formally expose `restart_app(pid)` as a 24th tool on FastMCP, as it allows restarting an app by PID without requiring the agent to re-supply the script path, arguments, and working directory.
> 2. Cleanly designate `click_element_by_label`, `check_app_liveness`, `get_window_geometry`, and `focus_window` as internal helpers (`_helper`) and remove them from `server.py` re-exports to eliminate confusion.

---

### Finding 2: `take_labeled_screenshot` Label Coverage Bug & Badge ID Disconnect

> [!IMPORTANT]
> **The Problem with Current Labeled Screenshots**:
> 1. In `portal/session.py` (`generate_labeled_screenshot`), the tree is traversed using a private `walk(node)` that only checks 12 hardcoded roles:
>    `{"push button", "button", "entry", "text", "scale", "slider", "check box", "radio button", "menu item", "link", "page tab", "combo box"}`.
>    **It misses**:
>    - GTK4 switches and toggle buttons (`toggle button`, `togglebutton`)
>    - GTK check buttons (`check button`, `checkbutton`)
>    - Spin boxes and adjusters (`spin button`, `spinbutton`)
>    - List rows and cells (`list item`, `tree item`, `table cell`)
>    - Dropdown menus (`menu`, `popup menu`)
> 2. `get_application_tree(pid)` ALREADY runs `flatten_tree(tree, pid)` using the complete `INTERACTIVE_ROLES` table from `constants.py`! Running a second, incomplete walk in `session.py` is redundant and causes visible interactive elements to be skipped.
> 3. **Badge Identifier Mismatch**: `draw_labeled_overlay` prints badges as `[1]`, `[2]`, `[3]`. But the agent interacts using compact semantic IDs (`b1`, `e1`, `c1`, `s1`, `k1`).
>    If the badges on the screenshot render the compact IDs directly (e.g. `[b1]`, `[e1]`, `[c1]`) or include both index and compact ID, the agent can look at the screenshot and immediately issue `interact_with_node("b1")` with zero mental mapping.

**Proposed Enhancement**:
- Refactor `generate_labeled_screenshot` to consume `tree.get("interactive_elements")` directly from `get_application_tree()`.
- Update `draw_labeled_overlay` in `overlay.py` to display the compact ID `[b1]`, `[e1]`, `[k1]` (or `[1: b1]`) on each badge pill.
- Ensure every element with valid non-zero bounds marked visible/showing receives a badge.

---

### Finding 3: Parameter Inconsistency in Coordinate Input Tools
- Tools in `input_tools.py` (`click`, `double_click`, `right_click`, `hover`, `drag`, `scroll`, `type_text`, `key_combination`) do not accept an optional `pid: int | None = None` argument.
- In contrast, navigation tools (`inspect_ui_tree`, `interact_with_node`, `batch_actions`, `watch_ui_events`) all accept `pid: int | None = None`.
- **Recommendation**: Add `pid: int | None = None` to all 8 input tools. When passed, `global_portal_session.target_pid` is updated, window focus is ensured, and the delta is computed against the correct process.

---

### Finding 4: Inconsistent Artifact File Paths
- `screencast.py` and `session.py` use `~/.cache/wayland-computer-use-mcp/screenshots/`.
- `visual_tools.py` lines 187 & 246 write to `.agents/artifacts/screenshots/`.
- **Recommendation**: Unify on `~/.cache/wayland-computer-use-mcp/screenshots/` (or `WAYLAND_MCP_SAVE_FRAMES_DIR` env var) across all modules.

---

## 4. Architecture Blindspots & Inefficiencies to Reconsider

| Area | Current Behavior | Architectural Risk / Blindspot | Proposed Solution |
| :--- | :--- | :--- | :--- |
| **Multi-Monitor Scaling** | Logical compositor coordinates are assumed 1:1 with surface pixels. | Fractional scaling (1.25x, 1.5x, 1.75x) or multi-monitor setups with mismatched DPI can cause coordinate drift on physical clicks. | Incorporate `DisplayInfo.scale` from `CompositorBackend` to scale coordinates dynamically in `portal/input.py`. |
| **External Process Attachment** | Any PID can be passed to `terminate_app`. | If an agent attaches to an existing browser or IDE process, `terminate_app` or server shutdown hooks could kill user-critical applications. | Add an `externally_attached: bool` tag in process metadata to prohibit terminating externally attached PIDs. |
| **Batch Action Error Recovery** | `batch_actions` halts immediately on step failure, returning the error string. | Agent does not receive the UI delta of what mutated up to the failure point, making recovery harder. | Include the intermediate UI delta up to the failing step in the failure response. |
| **Artifact Rolling Retention** | When `save_artifact=True`, `latest_capture.png` overwrites, but custom named files or historical screenshots accumulate indefinitely. | Risk of filling disk cache over long testing sessions. | Implement a rolling retention policy (keep last 20 frames, TTL 24 hours). |

---

## 5. Verification Plan

### Automated Tests
```bash
# 1. Run full unit and integration test suite
uv run pytest -m "not live" -v

# 2. Run live GUI verification against GTK4 testbed
uv run pytest tests/test_live_example_app.py -v

# 3. Code style and formatting checks
uv run ruff check .
uv run ruff format --check .

# 4. Packaging and Twine checks
uv build
uvx twine check dist/*
```

### Manual Visual Verification
- Run `examples/test_gui_app.py` and invoke `take_labeled_screenshot()` to visually verify that:
  1. All 14 interactive components (including switches, toggle buttons, spinboxes, and combo boxes) receive bounding boxes.
  2. Badges render with crisp compact IDs (`[b1]`, `[e1]`, `[c1]`, `[s1]`, `[k1]`).
  3. No interactive element on screen is skipped.
