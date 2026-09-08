#!/usr/bin/env python3
"""Wayland MCP Multi-Tab Test Rig Application (GTK 4 + Libadwaita).

An interactive modern GUI application specifically designed to test all tools
provided by wayland-computer-use-mcp:
1. Multi-tab navigation (Adw.ViewStack & Adw.ViewSwitcher)
2. Dropdown selection (Gtk.DropDown)
3. Sub-page switching (Gtk.Stack & Gtk.StackSwitcher)
4. Visual Click Indicator Overlay (glowing red dot at click coordinates with auto-fade)
5. Controls: Counter button, Text entry, Horizontal slider, Checkboxes, Toggle switch
6. Scrollable data list
7. Diagnostics & Event stream
"""

import sys
import time
from typing import Any

if "--check" in sys.argv:
    try:
        import gi

        gi.require_version("Gtk", "4.0")
        gi.require_version("Adw", "1")
        from gi.repository import Adw, Gtk  # noqa: F401

        print("GTK 4 + Libadwaita Multi-Tab Test Rig syntax and module imports valid.")
        sys.exit(0)
    except Exception as exc:
        print(f"[ERROR] Could not load GTK 4 / Libadwaita: {exc}", file=sys.stderr)
        sys.exit(1)

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402


class VisualClickIndicator(Gtk.Label):
    """Glowing red visual indicator that appears at click coordinates and auto-fades."""

    def __init__(self) -> None:
        super().__init__(label="●")
        self.add_css_class("click-indicator")
        self.set_halign(Gtk.Align.START)
        self.set_valign(Gtk.Align.START)
        self.set_visible(False)
        self._fade_timer = None

        css_data = """
        .click-indicator {
            color: #ff1e1e;
            font-size: 30px;
            font-weight: bold;
            text-shadow: 0 0 10px #ff3b3b;
        }
        """
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(css_data.encode("utf-8"))
        display = Gdk.Display.get_default()
        if display:
            Gtk.StyleContext.add_provider_for_display(
                display,
                css_provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )

    def trigger(self, x: float, y: float, fixed_container: Gtk.Fixed) -> None:
        """Positions the glowing dot at (x, y) and hides it after 500ms."""
        fixed_container.move(self, max(0, int(x - 14)), max(0, int(y - 18)))
        self.set_visible(True)

        if self._fade_timer:
            GLib.source_remove(self._fade_timer)
        self._fade_timer = GLib.timeout_add(500, self._on_fade_timeout)

    def _on_fade_timeout(self) -> bool:
        self.set_visible(False)
        self._fade_timer = None
        return False


class TestRigWindow(Adw.ApplicationWindow):
    """Interactive GTK4/Libadwaita test window."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.set_title("Wayland MCP Test Rig")
        self.set_default_size(720, 640)
        self.click_count = 0

        # ToolbarView integrates Adw.HeaderBar with title buttons (Close 'X', Maximize, Minimize)
        toolbar_view = Adw.ToolbarView()

        header = Adw.HeaderBar()
        header.set_show_start_title_buttons(True)
        header.set_show_end_title_buttons(True)
        title = Adw.WindowTitle(
            title="Wayland MCP Test Rig", subtitle="Libadwaita Tool Verification"
        )
        header.set_title_widget(title)
        toolbar_view.add_top_bar(header)

        # Content container
        main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # View Stack & Switcher (4 Tabs)
        self.view_stack = Adw.ViewStack()
        self.view_stack.set_vexpand(True)

        self.view_switcher = Adw.ViewSwitcher(
            stack=self.view_stack,
            policy=Adw.ViewSwitcherPolicy.WIDE,
        )
        self.view_switcher.set_margin_top(6)
        self.view_switcher.set_margin_bottom(6)
        main_vbox.append(self.view_switcher)
        main_vbox.append(self.view_stack)

        # TAB 1: Controls & Inputs
        self.tab1 = self._build_tab_controls()
        page1 = self.view_stack.add_titled(self.tab1, "controls", "Controls & Inputs")
        page1.set_icon_name("input-dialpad-symbolic")

        # TAB 2: Navigation & Pages
        self.tab2 = self._build_tab_navigation()
        page2 = self.view_stack.add_titled(self.tab2, "navigation", "Navigation & Pages")
        page2.set_icon_name("view-paged-symbolic")

        # TAB 3: Data & Lists
        self.tab3 = self._build_tab_data()
        page3 = self.view_stack.add_titled(self.tab3, "data", "Data & Lists")
        page3.set_icon_name("view-list-bullet-symbolic")

        # TAB 4: Diagnostics & Logs
        self.tab4 = self._build_tab_diagnostics()
        page4 = self.view_stack.add_titled(self.tab4, "diagnostics", "Diagnostics")
        page4.set_icon_name("utilities-terminal-symbolic")

        # Set default visible child so Tab 1 is immediately rendered
        self.view_stack.set_visible_child_name("controls")

        # Footer Status Bar
        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        status_box.set_margin_start(16)
        status_box.set_margin_end(16)
        status_box.set_margin_top(6)
        status_box.set_margin_bottom(8)
        self.status_bar = Gtk.Label(label="Status: Ready for MCP agent interactions.")
        self.status_bar.set_xalign(0.0)
        status_box.append(self.status_bar)
        main_vbox.append(status_box)

        # Main Overlay for VisualClickIndicator
        self.overlay = Gtk.Overlay()
        self.overlay.set_child(main_vbox)

        self.overlay_fixed = Gtk.Fixed()
        # can_target=False ensures the overlay NEVER intercepts clicks or blocks buttons/tabs
        self.overlay_fixed.set_can_target(False)

        self.click_indicator = VisualClickIndicator()
        self.click_indicator.set_can_target(False)
        self.overlay_fixed.put(self.click_indicator, 0, 0)
        self.overlay.add_overlay(self.overlay_fixed)
        self.overlay.set_measure_overlay(self.overlay_fixed, False)

        toolbar_view.set_content(self.overlay)
        self.set_content(toolbar_view)

        # Click gesture tracking in BUBBLE phase without consuming events
        click_gesture = Gtk.GestureClick.new()
        click_gesture.set_button(0)
        click_gesture.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
        click_gesture.connect("pressed", self._on_window_click)
        self.add_controller(click_gesture)

    def _on_window_click(self, gesture: Gtk.GestureClick, n_press: int, x: float, y: float) -> None:
        self.click_indicator.trigger(x, y, self.overlay_fixed)
        self._log_event(f"Click at ({x:.1f}, {y:.1f})")

    def trigger_visual_click(self, x: float, y: float) -> None:
        """Explicitly triggers a visual indicator at coordinates."""
        self.click_indicator.trigger(x, y, self.overlay_fixed)

    def _log_event(self, text: str) -> None:
        t_str = time.strftime("%H:%M:%S")
        entry = f"[{t_str}] {text}"
        self.status_bar.set_text(entry)
        if hasattr(self, "diag_buffer"):
            end_iter = self.diag_buffer.get_end_iter()
            self.diag_buffer.insert(end_iter, entry + "\n")
        sys.stderr.write(f"[TestRig] {entry}\n")
        sys.stderr.flush()

    # --- TAB 1: Controls & Inputs ---
    def _build_tab_controls(self) -> Gtk.Widget:
        scrolled = Gtk.ScrolledWindow(vexpand=True)
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        vbox.set_margin_start(16)
        vbox.set_margin_end(16)
        vbox.set_margin_top(12)
        vbox.set_margin_bottom(12)

        # 1. Counter Button
        group1 = Adw.PreferencesGroup(
            title="1. Counter Button (click tool)",
            description="Interactive button test with live click counter",
        )
        btn_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        btn_box.set_margin_top(4)
        btn_box.set_margin_bottom(4)
        btn_box.set_margin_start(4)
        btn_box.set_margin_end(4)

        self.btn = Gtk.Button(label="Click Me! (0 clicks)")
        self.btn.add_css_class("suggested-action")
        self.btn.add_css_class("pill")
        self.btn.set_size_request(160, 42)
        self.btn.connect("clicked", self.on_button_clicked)
        btn_box.append(self.btn)

        self.btn_status = Gtk.Label(label="Awaiting click...")
        self.btn_status.set_xalign(0.0)
        btn_box.append(self.btn_status)
        group1.add(btn_box)
        vbox.append(group1)

        # 2. Text Input
        group2 = Adw.PreferencesGroup(
            title="2. Text Input (type_text tool)",
            description="Text entry field with auto-focus and live feedback",
        )
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        text_box.set_margin_top(4)
        text_box.set_margin_bottom(4)
        text_box.set_margin_start(4)
        text_box.set_margin_end(4)

        self.entry = Gtk.Entry()
        self.entry.set_placeholder_text("Click here and test type_text...")
        self.entry.connect("changed", self.on_text_changed)
        text_box.append(self.entry)

        self.text_status = Gtk.Label(label="Current Text: (empty)")
        self.text_status.set_xalign(0.0)
        text_box.append(self.text_status)
        group2.add(text_box)
        vbox.append(group2)

        # 3. Dropdown Menu
        group3 = Adw.PreferencesGroup(
            title="3. Dropdown Menu (combo box selection)",
            description="Tests list item selection and popovers",
        )
        drop_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        drop_box.set_margin_top(4)
        drop_box.set_margin_bottom(4)
        drop_box.set_margin_start(4)
        drop_box.set_margin_end(4)

        self.dropdown = Gtk.DropDown.new_from_strings(
            [
                "Development - Localhost",
                "Staging - Testing Cluster",
                "Production - Live Region",
            ]
        )
        self.dropdown.connect("notify::selected", self.on_dropdown_changed)
        drop_box.append(self.dropdown)

        self.combo_status = Gtk.Label(label="Selected: Development - Localhost")
        self.combo_status.set_xalign(0.0)
        drop_box.append(self.combo_status)
        group3.add(drop_box)
        vbox.append(group3)

        # 4. Slider (Drag)
        group4 = Adw.PreferencesGroup(
            title="4. Slider (drag tool)",
            description="Horizontal slider (0-100) with drag position feedback",
        )
        slider_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        slider_box.set_margin_top(4)
        slider_box.set_margin_bottom(4)
        slider_box.set_margin_start(4)
        slider_box.set_margin_end(4)

        adj = Gtk.Adjustment(value=25.0, lower=0.0, upper=100.0, step_increment=1.0)
        self.slider = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj)
        self.slider.set_draw_value(True)
        self.slider.set_value_pos(Gtk.PositionType.RIGHT)
        self.slider.connect("value-changed", self.on_slider_changed)
        slider_box.append(self.slider)

        self.slider_status = Gtk.Label(label="Slider Value: 25.0")
        self.slider_status.set_xalign(0.0)
        slider_box.append(self.slider_status)
        group4.add(slider_box)
        vbox.append(group4)

        scrolled.set_child(vbox)
        return scrolled

    # --- TAB 2: Navigation & Pages ---
    def _build_tab_navigation(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        vbox.set_margin_start(16)
        vbox.set_margin_end(16)
        vbox.set_margin_top(12)
        vbox.set_margin_bottom(12)

        self.nav_stack = Gtk.Stack()
        self.nav_stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.nav_stack.set_transition_duration(250)

        nav_switcher = Gtk.StackSwitcher(stack=self.nav_stack)
        nav_switcher.set_halign(Gtk.Align.CENTER)
        nav_switcher.set_margin_bottom(10)
        vbox.append(nav_switcher)

        # Sub-Page 1: Overview
        p1 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        p1.set_margin_start(12)
        p1.set_margin_end(12)
        p1_title = Gtk.Label()
        p1_title.set_markup("<b>Overview Dashboard Page</b>")
        p1_title.set_xalign(0.0)
        p1.append(p1_title)
        p1_desc = Gtk.Label(label="System state: Nominal. Ready for deployment.", xalign=0.0)
        p1.append(p1_desc)
        btn_goto_analytics = Gtk.Button(label="Go to Analytics Page")
        btn_goto_analytics.set_halign(Gtk.Align.START)
        btn_goto_analytics.connect(
            "clicked", lambda w: self.nav_stack.set_visible_child_name("analytics")
        )
        p1.append(btn_goto_analytics)
        self.nav_stack.add_titled(p1, "overview", "Overview")

        # Sub-Page 2: Analytics
        p2 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        p2.set_margin_start(12)
        p2.set_margin_end(12)
        p2_title = Gtk.Label()
        p2_title.set_markup("<b>Analytics &amp; Metrics Page</b>")
        p2_title.set_xalign(0.0)
        p2.append(p2_title)
        p2_desc = Gtk.Label(label="Throughput: 94.2 MB/s | Latency: 1.2ms", xalign=0.0)
        p2.append(p2_desc)
        btn_goto_settings = Gtk.Button(label="Go to Settings Page")
        btn_goto_settings.set_halign(Gtk.Align.START)
        btn_goto_settings.connect(
            "clicked", lambda w: self.nav_stack.set_visible_child_name("settings")
        )
        p2.append(btn_goto_settings)
        self.nav_stack.add_titled(p2, "analytics", "Analytics")

        # Sub-Page 3: Settings
        p3 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        p3.set_margin_start(12)
        p3.set_margin_end(12)
        p3_title = Gtk.Label()
        p3_title.set_markup("<b>Application Settings Page</b>")
        p3_title.set_xalign(0.0)
        p3.append(p3_title)
        btn_back_overview = Gtk.Button(label="Back to Overview Page")
        btn_back_overview.set_halign(Gtk.Align.START)
        btn_back_overview.connect(
            "clicked", lambda w: self.nav_stack.set_visible_child_name("overview")
        )
        p3.append(btn_back_overview)
        self.nav_stack.add_titled(p3, "settings", "Settings")

        vbox.append(self.nav_stack)
        return vbox

    # --- TAB 3: Data & Lists ---
    def _build_tab_data(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        vbox.set_margin_start(16)
        vbox.set_margin_end(16)
        vbox.set_margin_top(12)
        vbox.set_margin_bottom(12)

        opts_group = Adw.PreferencesGroup(title="System Flags & Toggles")
        row_gpu = Adw.ActionRow(title="Hardware Acceleration")
        self.chk_gpu = Gtk.CheckButton(label="Enable GPU Acceleration")
        self.chk_gpu.connect("toggled", lambda w: self._log_event(f"GPU toggled: {w.get_active()}"))
        row_gpu.add_suffix(self.chk_gpu)
        opts_group.add(row_gpu)

        row_portal = Adw.ActionRow(title="RemoteDesktop Bridge")
        self.switch_portal = Gtk.Switch(active=True)
        self.switch_portal.set_valign(Gtk.Align.CENTER)
        self.switch_portal.connect(
            "notify::active",
            lambda s, g: self._log_event(f"Portal active: {s.get_active()}"),
        )
        row_portal.add_suffix(self.switch_portal)
        opts_group.add(row_portal)
        vbox.append(opts_group)

        # Scrolled dataset list
        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.set_min_content_height(160)
        items_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        items_box.set_margin_start(12)
        items_box.set_margin_end(12)
        items_box.set_margin_top(8)
        items_box.set_margin_bottom(8)

        for i in range(1, 35):
            lbl = Gtk.Label(
                label=f"Item #{i:02d}: Automated testing dataset record entry",
                xalign=0.0,
            )
            items_box.append(lbl)

        scrolled.set_child(items_box)
        vbox.append(scrolled)
        return vbox

    # --- TAB 4: Diagnostics & Logs ---
    def _build_tab_diagnostics(self) -> Gtk.Widget:
        vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        vbox.set_margin_start(16)
        vbox.set_margin_end(16)
        vbox.set_margin_top(12)
        vbox.set_margin_bottom(12)

        info_lbl = Gtk.Label(
            label="Real-time application event stream & diagnostic buffer:",
            xalign=0.0,
        )
        vbox.append(info_lbl)

        scrolled = Gtk.ScrolledWindow(vexpand=True)
        scrolled.set_min_content_height(220)
        self.diag_view = Gtk.TextView(editable=False)
        self.diag_buffer = self.diag_view.get_buffer()
        self.diag_buffer.set_text("System initialized. Monitoring events...\n")
        scrolled.set_child(self.diag_view)
        vbox.append(scrolled)
        return vbox

    # --- Handlers ---
    def on_button_clicked(self, widget: Gtk.Button) -> None:
        self.click_count += 1
        self.btn.set_label(f"Clicked! ({self.click_count} clicks)")
        msg = f"Button clicked (total: {self.click_count})"
        self.btn_status.set_text(msg)
        self._log_event(msg)

    def on_text_changed(self, entry: Gtk.Entry) -> None:
        val = entry.get_text()
        self.text_status.set_text(f"Current Text: '{val}'")
        self._log_event(f"Input text updated: '{val}'")

    def on_dropdown_changed(self, widget: Gtk.DropDown, param: Any) -> None:
        selected_idx = widget.get_selected()
        items = [
            "Development - Localhost",
            "Staging - Testing Cluster",
            "Production - Live Region",
        ]
        active_text = items[selected_idx] if 0 <= selected_idx < len(items) else "Unknown"
        self.combo_status.set_text(f"Selected: {active_text}")
        self._log_event(f"Dropdown selected: {active_text}")

    def on_slider_changed(self, widget: Gtk.Scale) -> None:
        val = widget.get_value()
        self.slider_status.set_text(f"Slider Value: {val:.1f}")
        self._log_event(f"Slider dragged: {val:.1f}")


def main() -> None:
    if "--check" in sys.argv:
        print("GTK 4 + Libadwaita Multi-Tab Test Rig syntax and module imports valid.")
        sys.exit(0)

    app = Adw.Application(
        application_id="org.wayland.mcp.TestRig",
        flags=Gio.ApplicationFlags.NON_UNIQUE,
    )

    def on_activate(application: Adw.Application) -> None:
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        win = TestRigWindow(application=application)
        win.present()
        print("[TestRig] Application started successfully. Ready for MCP tools.")
        sys.stdout.flush()

    app.connect("activate", on_activate)
    app.run(sys.argv)


if __name__ == "__main__":
    main()
