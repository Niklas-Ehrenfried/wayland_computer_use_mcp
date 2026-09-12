#!/usr/bin/env python3
"""Wayland MCP Multi-Tab Test Rig Application.

An interactive modern GUI application specifically designed to test all tools
provided by wayland-computer-use-mcp:
1. Multi-tab navigation (Adw.ViewStack / Gtk.Notebook)
2. Dropdown selection (Gtk.DropDown / Gtk.ComboBoxText)
3. Sub-page switching
4. Controls: Counter button, Text entry, Horizontal slider, Checkboxes, Toggle switch
5. Scrollable data list
6. Diagnostics & Event stream

Supports both GTK 4 + Libadwaita (when available) and standard GTK 3 fallback.
"""

from __future__ import annotations

import sys
import time
from typing import Any

HAS_GTK4 = False
try:
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: F401

    HAS_GTK4 = True
except Exception:
    import gi

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: F401


if HAS_GTK4:

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

        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self.set_title("Wayland MCP Test Rig")
            self.set_default_size(720, 640)
            self.click_count = 0

            toolbar_view = Adw.ToolbarView()

            header = Adw.HeaderBar()
            header.set_show_start_title_buttons(True)
            header.set_show_end_title_buttons(True)
            title = Adw.WindowTitle(
                title="Wayland MCP Test Rig", subtitle="Libadwaita Tool Verification"
            )
            header.set_title_widget(title)
            toolbar_view.add_top_bar(header)

            main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

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

            self.view_stack.add_titled(self._build_controls_tab(), "controls", "Controls & Inputs")
            self.view_stack.add_titled(
                self._build_navigation_tab(), "navigation", "Navigation & Pages"
            )
            self.view_stack.add_titled(self._build_data_tab(), "data", "Data & Lists")
            self.view_stack.add_titled(self._build_diagnostics_tab(), "diagnostics", "Diagnostics")

            overlay = Gtk.Overlay()
            overlay.set_child(main_vbox)

            self.fixed_layer = Gtk.Fixed()
            self.fixed_layer.set_can_target(False)
            self.click_indicator = VisualClickIndicator()
            self.fixed_layer.put(self.click_indicator, 0, 0)
            overlay.add_overlay(self.fixed_layer)

            toolbar_view.set_content(overlay)
            self.set_content(toolbar_view)

            click_gesture = Gtk.GestureClick()
            click_gesture.set_propagation_phase(Gtk.PropagationPhase.BUBBLE)
            click_gesture.connect("pressed", self._on_window_pressed)
            self.add_controller(click_gesture)

        def _on_window_pressed(
            self, gesture: Gtk.GestureClick, n_press: int, x: float, y: float
        ) -> None:
            self.click_indicator.trigger(x, y, self.fixed_layer)
            self._log_event(f"Physical Click at coordinates ({int(x)}, {int(y)})")

        def _log_event(self, message: str) -> None:
            t = time.strftime("%H:%M:%S")
            line = f"[{t}] {message}\n"
            end_iter = self.diag_buffer.get_end_iter()
            self.diag_buffer.insert(end_iter, line)

        def _build_controls_tab(self) -> Gtk.Widget:
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
            vbox.set_margin_start(16)
            vbox.set_margin_end(16)
            vbox.set_margin_top(16)
            vbox.set_margin_bottom(16)

            self.btn = Gtk.Button(label="Click Me! (0 clicks)")
            self.btn.add_css_class("suggested-action")
            self.btn.add_css_class("pill")
            self.btn.connect("clicked", self.on_button_clicked)
            vbox.append(self.btn)

            self.btn_status = Gtk.Label(label="Button not clicked yet", xalign=0.0)
            self.btn_status.add_css_class("dim-label")
            vbox.append(self.btn_status)

            self.entry = Gtk.Entry()
            self.entry.set_placeholder_text("Type something to test keyboard injection...")
            self.entry.connect("changed", self.on_text_changed)
            vbox.append(self.entry)

            self.text_status = Gtk.Label(label="Current Text: ''", xalign=0.0)
            self.text_status.add_css_class("dim-label")
            vbox.append(self.text_status)

            string_list = Gtk.StringList.new(
                [
                    "Development - Localhost",
                    "Staging - Testing Cluster",
                    "Production - Live Region",
                ]
            )
            self.dropdown = Gtk.DropDown.new(string_list, None)
            self.dropdown.set_selected(0)
            self.dropdown.connect("notify::selected", self.on_dropdown_changed)
            vbox.append(self.dropdown)

            self.combo_status = Gtk.Label(label="Selected: Development - Localhost", xalign=0.0)
            self.combo_status.add_css_class("dim-label")
            vbox.append(self.combo_status)

            self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
            self.scale.set_value(25.0)
            self.scale.set_draw_value(True)
            self.scale.connect("value-changed", self.on_slider_changed)
            vbox.append(self.scale)

            self.slider_status = Gtk.Label(label="Slider Value: 25.0", xalign=0.0)
            self.slider_status.add_css_class("dim-label")
            vbox.append(self.slider_status)
            return vbox

        def _build_navigation_tab(self) -> Gtk.Widget:
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
            vbox.set_margin_start(16)
            vbox.set_margin_end(16)
            vbox.set_margin_top(16)
            vbox.set_margin_bottom(16)

            self.sub_stack = Gtk.Stack()
            self.sub_stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)

            p1 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            p1.append(Gtk.Label(label="Overview Sub-Page", xalign=0.0))
            btn_to_analytics = Gtk.Button(label="Go to Analytics Page")
            btn_to_analytics.connect(
                "clicked", lambda b: self.sub_stack.set_visible_child_name("analytics")
            )
            p1.append(btn_to_analytics)

            p2 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            p2.append(Gtk.Label(label="Analytics Sub-Page", xalign=0.0))
            btn_to_settings = Gtk.Button(label="Go to Settings Page")
            btn_to_settings.connect(
                "clicked", lambda b: self.sub_stack.set_visible_child_name("settings")
            )
            p2.append(btn_to_settings)

            p3 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
            p3.append(Gtk.Label(label="Settings Sub-Page", xalign=0.0))
            btn_to_overview = Gtk.Button(label="Back to Overview Page")
            btn_to_overview.connect(
                "clicked", lambda b: self.sub_stack.set_visible_child_name("overview")
            )
            p3.append(btn_to_overview)

            self.sub_stack.add_named(p1, "overview")
            self.sub_stack.add_named(p2, "analytics")
            self.sub_stack.add_named(p3, "settings")

            switcher = Gtk.StackSwitcher(stack=self.sub_stack)
            vbox.append(switcher)

            self.nav_status = Gtk.Label(label="Active Sub-Page: overview", xalign=0.0)
            self.nav_status.add_css_class("dim-label")
            self.sub_stack.connect("notify::visible-child-name", self._on_sub_stack_changed)
            vbox.append(self.nav_status)
            vbox.append(self.sub_stack)
            return vbox

        def _on_sub_stack_changed(self, stack: Gtk.Stack, param: Any) -> None:
            name = stack.get_visible_child_name() or "unknown"
            self.nav_status.set_text(f"Active Sub-Page: {name}")
            self._log_event(f"Sub-page switched to: {name}")

        def _build_data_tab(self) -> Gtk.Widget:
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            vbox.set_margin_start(16)
            vbox.set_margin_end(16)
            vbox.set_margin_top(16)
            vbox.set_margin_bottom(16)

            self.chk = Gtk.CheckButton(label="Hardware Acceleration")
            self.chk.set_active(True)
            self.chk.connect("toggled", self.on_checkbox_toggled)
            vbox.append(self.chk)

            self.chk_status = Gtk.Label(label="Hardware Acceleration: Enabled", xalign=0.0)
            self.chk_status.add_css_class("dim-label")
            vbox.append(self.chk_status)

            self.row_status = Gtk.Label(label="Selected Row: None", xalign=0.0)
            self.row_status.add_css_class("dim-label")
            vbox.append(self.row_status)

            scrolled = Gtk.ScrolledWindow(vexpand=True)
            self.list_box = Gtk.ListBox()
            self.list_box.set_selection_mode(Gtk.SelectionMode.SINGLE)
            self.list_box.connect("row-selected", self._on_row_selected)
            for i in range(1, 41):
                self.list_box.append(
                    Gtk.Label(label=f"Dataset Row #{i:02d} - Active status", xalign=0.0)
                )
            scrolled.set_child(self.list_box)
            vbox.append(scrolled)
            return vbox

        def _on_row_selected(self, box: Gtk.ListBox, row: Any) -> None:
            if row is not None:
                idx = row.get_index() + 1
                txt = f"Dataset Row #{idx:02d}"
                self.row_status.set_text(f"Selected Row: {txt}")
                self._log_event(f"Row selected: {txt}")

        def _build_diagnostics_tab(self) -> Gtk.Widget:
            vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            vbox.set_margin_start(16)
            vbox.set_margin_end(16)
            vbox.set_margin_top(16)
            vbox.set_margin_bottom(16)

            info_lbl = Gtk.Label(
                label="Real-time application event stream & diagnostic buffer:",
                xalign=0.0,
            )
            vbox.append(info_lbl)

            self.btn_crash = Gtk.Button(label="Simulate Python Crash")
            self.btn_crash.add_css_class("destructive-action")
            self.btn_crash.connect("clicked", self.on_crash_clicked)
            vbox.append(self.btn_crash)

            scrolled = Gtk.ScrolledWindow(vexpand=True)
            scrolled.set_min_content_height(220)
            self.diag_view = Gtk.TextView(editable=False)
            self.diag_buffer = self.diag_view.get_buffer()
            self.diag_buffer.set_text("System initialized. Monitoring events...\n")
            scrolled.set_child(self.diag_view)
            vbox.append(scrolled)
            return vbox

        def on_crash_clicked(self, widget: Gtk.Button) -> None:
            print("Simulated crash button triggered!", file=sys.stderr)
            raise RuntimeError("Simulated Python GUI crash for MCP verification")

        def on_checkbox_toggled(self, widget: Gtk.CheckButton) -> None:
            active = widget.get_active()
            status = "Enabled" if active else "Disabled"
            self.chk_status.set_text(f"Hardware Acceleration: {status}")
            self._log_event(f"Checkbox toggled: {status}")

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


else:
    # --- GTK 3 Native Fallback ---
    class TestRigWindowGTK3(Gtk.Window):
        """Interactive GTK 3 fallback test window."""

        def __init__(self) -> None:
            super().__init__(title="Wayland MCP Test Rig")
            self.set_default_size(720, 640)
            self.click_count = 0

            main_vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            main_vbox.set_border_width(12)
            self.add(main_vbox)

            self.notebook = Gtk.Notebook()
            main_vbox.pack_start(self.notebook, True, True, 0)

            # Tab 1: Controls & Inputs
            t1 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            t1.set_border_width(12)

            self.btn = Gtk.Button(label="Click Me! (0 clicks)")
            self.btn.connect("clicked", self.on_button_clicked)
            t1.pack_start(self.btn, False, False, 0)

            self.btn_status = Gtk.Label(label="Button not clicked yet", xalign=0.0)
            t1.pack_start(self.btn_status, False, False, 0)

            self.entry = Gtk.Entry()
            self.entry.set_placeholder_text("Type something to test keyboard injection...")
            self.entry.connect("changed", self.on_text_changed)
            t1.pack_start(self.entry, False, False, 0)

            self.text_status = Gtk.Label(label="Current Text: ''", xalign=0.0)
            t1.pack_start(self.text_status, False, False, 0)

            self.combo = Gtk.ComboBoxText()
            for item in [
                "Development - Localhost",
                "Staging - Testing Cluster",
                "Production - Live Region",
            ]:
                self.combo.append_text(item)
            self.combo.set_active(0)
            self.combo.connect("changed", self.on_dropdown_changed)
            t1.pack_start(self.combo, False, False, 0)

            self.combo_status = Gtk.Label(label="Selected: Development - Localhost", xalign=0.0)
            t1.pack_start(self.combo_status, False, False, 0)

            self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
            self.scale.set_value(25.0)
            self.scale.connect("value-changed", self.on_slider_changed)
            t1.pack_start(self.scale, False, False, 0)

            self.slider_status = Gtk.Label(label="Slider Value: 25.0", xalign=0.0)
            t1.pack_start(self.slider_status, False, False, 0)

            self.notebook.append_page(t1, Gtk.Label(label="Controls & Inputs"))

            # Tab 2: Navigation & Pages
            t2 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            t2.set_border_width(12)
            self.nav_status = Gtk.Label(label="Active Sub-Page: overview", xalign=0.0)
            t2.pack_start(self.nav_status, False, False, 0)
            btn_an = Gtk.Button(label="Go to Analytics Page")
            btn_an.connect(
                "clicked", lambda b: self.nav_status.set_text("Active Sub-Page: analytics")
            )
            t2.pack_start(btn_an, False, False, 0)
            btn_st = Gtk.Button(label="Go to Settings Page")
            btn_st.connect(
                "clicked", lambda b: self.nav_status.set_text("Active Sub-Page: settings")
            )
            t2.pack_start(btn_st, False, False, 0)
            btn_ov = Gtk.Button(label="Back to Overview Page")
            btn_ov.connect(
                "clicked", lambda b: self.nav_status.set_text("Active Sub-Page: overview")
            )
            t2.pack_start(btn_ov, False, False, 0)
            self.notebook.append_page(t2, Gtk.Label(label="Navigation & Pages"))

            # Tab 3: Data & Lists
            t3 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            t3.set_border_width(12)
            self.chk = Gtk.CheckButton(label="Hardware Acceleration")
            self.chk.set_active(True)
            self.chk.connect("toggled", self.on_checkbox_toggled)
            t3.pack_start(self.chk, False, False, 0)

            self.chk_status = Gtk.Label(label="Hardware Acceleration: Enabled", xalign=0.0)
            t3.pack_start(self.chk_status, False, False, 0)

            self.row_status = Gtk.Label(label="Selected Row: None", xalign=0.0)
            t3.pack_start(self.row_status, False, False, 0)

            scrolled = Gtk.ScrolledWindow()
            list_box = Gtk.ListBox()
            list_box.connect(
                "row-selected",
                lambda b, r: (
                    self.row_status.set_text(f"Selected Row: Dataset Row #{r.get_index() + 1:02d}")
                    if r
                    else None
                ),
            )
            for i in range(1, 41):
                list_box.add(Gtk.Label(label=f"Dataset Row #{i:02d} - Active status", xalign=0.0))
            scrolled.add(list_box)
            t3.pack_start(scrolled, True, True, 0)
            self.notebook.append_page(t3, Gtk.Label(label="Data & Lists"))

            # Tab 4: Diagnostics
            t4 = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
            t4.set_border_width(12)
            self.btn_crash = Gtk.Button(label="Simulate Python Crash")
            self.btn_crash.connect("clicked", self.on_crash_clicked)
            t4.pack_start(self.btn_crash, False, False, 0)

            self.diag_view = Gtk.TextView(editable=False)
            self.diag_buffer = self.diag_view.get_buffer()
            self.diag_buffer.set_text("System initialized. Monitoring events...\n")
            t4.pack_start(self.diag_view, True, True, 0)
            self.notebook.append_page(t4, Gtk.Label(label="Diagnostics"))

            self.connect("destroy", Gtk.main_quit)

        def on_crash_clicked(self, widget: Gtk.Button) -> None:
            print("Simulated crash button triggered!", file=sys.stderr)
            raise RuntimeError("Simulated Python GUI crash for MCP verification")

        def on_checkbox_toggled(self, widget: Gtk.CheckButton) -> None:
            active = widget.get_active()
            status = "Enabled" if active else "Disabled"
            self.chk_status.set_text(f"Hardware Acceleration: {status}")
            self._log_event(f"Checkbox toggled: {status}")

        def _log_event(self, message: str) -> None:
            t = time.strftime("%H:%M:%S")
            line = f"[{t}] {message}\n"
            end_iter = self.diag_buffer.get_end_iter()
            self.diag_buffer.insert(end_iter, line)

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

        def on_dropdown_changed(self, widget: Gtk.ComboBoxText) -> None:
            active_text = widget.get_active_text() or "Unknown"
            self.combo_status.set_text(f"Selected: {active_text}")
            self._log_event(f"Dropdown selected: {active_text}")

        def on_slider_changed(self, widget: Gtk.Scale) -> None:
            val = widget.get_value()
            self.slider_status.set_text(f"Slider Value: {val:.1f}")
            self._log_event(f"Slider dragged: {val:.1f}")


def main() -> None:
    if "--check" in sys.argv:
        print("GTK Multi-Tab Test Rig syntax and module imports valid.")
        sys.exit(0)

    test_mode = "--test-mode" in sys.argv
    crash_on_click = "--crash-on-click" in sys.argv
    clean_argv = [a for a in sys.argv if a not in ("--test-mode", "--crash-on-click")]

    if HAS_GTK4:
        app = Adw.Application(
            application_id="org.wayland.mcp.TestRig",
            flags=Gio.ApplicationFlags.NON_UNIQUE,
        )

        def on_activate(application: Adw.Application) -> None:
            Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
            win = TestRigWindow(application=application)
            if crash_on_click:

                def crashing_click(btn: Any) -> None:
                    print(
                        "Triggering intentional crash for Crash-to-Context test...", file=sys.stderr
                    )
                    raise RuntimeError("Intentional Test Exception: button click handler crashed!")

                win.btn.connect("clicked", crashing_click)
            win.present()
            tag = " in test mode" if test_mode else ""
            print(f"[TestRig] Application started successfully (GTK 4){tag}. Ready for MCP tools.")
            sys.stdout.flush()

        app.connect("activate", on_activate)
        app.run(clean_argv)
    else:
        win = TestRigWindowGTK3()
        if crash_on_click:

            def crashing_click(btn: Any) -> None:
                print("Triggering intentional crash for Crash-to-Context test...", file=sys.stderr)
                raise RuntimeError("Intentional Test Exception: button click handler crashed!")

            win.btn.connect("clicked", crashing_click)
        win.show_all()
        tag = " in test mode" if test_mode else ""
        print(f"[TestRig] App started successfully (GTK 3 fallback){tag}. Ready for MCP tools.")
        sys.stdout.flush()
        Gtk.main()


if __name__ == "__main__":
    main()
