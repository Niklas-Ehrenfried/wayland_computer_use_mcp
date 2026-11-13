#!/usr/bin/env python3
"""Chronos: Modern Todo & Calendar Studio.

A sleek, visually stunning Linux desktop application built with GTK 4 and Libadwaita
for scheduling appointments, managing tasks, and tracking productivity.
Designed for automated testing and human pair-programming via Wayland Computer-Use MCP.
"""

from __future__ import annotations

import datetime
import sys
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

CUSTOM_CSS = """
/* Modern Dark Glassmorphic Design System */
window.chronos-window {
    background: linear-gradient(135deg, #131722 0%, #1a202c 50%, #171923 100%);
    color: #edf2f7;
}

.calendar-card {
    background-color: rgba(26, 32, 44, 0.75);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 16px;
    padding: 16px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.25);
}

.main-content-card {
    background-color: rgba(26, 32, 44, 0.65);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 16px;
    padding: 20px;
    box-shadow: 0 8px 24px rgba(0, 0, 0, 0.25);
}

.card-title {
    font-size: 17px;
    font-weight: 700;
    color: #f7fafc;
    letter-spacing: -0.3px;
}

.badge-pill {
    padding: 4px 10px;
    border-radius: 9999px;
    font-size: 11px;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.5px;
}

.badge-urgent {
    background: linear-gradient(135deg, #e53e3e, #c53030);
    color: #ffffff;
}

.badge-high {
    background: linear-gradient(135deg, #dd6b20, #c05621);
    color: #ffffff;
}

.badge-normal {
    background: linear-gradient(135deg, #3182ce, #2b6cb0);
    color: #ffffff;
}

.badge-category {
    background-color: rgba(74, 85, 104, 0.5);
    color: #e2e8f0;
    border: 1px solid rgba(255, 255, 255, 0.1);
}

.task-row {
    background-color: rgba(30, 41, 59, 0.6);
    border: 1px solid rgba(255, 255, 255, 0.06);
    border-radius: 12px;
    padding: 12px 16px;
    margin-bottom: 8px;
    transition: all 200ms ease;
}

.task-row:hover {
    background-color: rgba(51, 65, 85, 0.8);
    border-color: rgba(99, 102, 241, 0.4);
}

.task-completed {
    opacity: 0.55;
    text-decoration: line-through;
}

.stat-box {
    background: linear-gradient(135deg, rgba(49, 130, 206, 0.15), rgba(79, 70, 229, 0.15));
    border: 1px solid rgba(99, 102, 241, 0.3);
    border-radius: 12px;
    padding: 14px;
}

.stat-number {
    font-size: 26px;
    font-weight: 800;
    color: #6366f1;
}

.stat-label {
    font-size: 12px;
    font-weight: 600;
    color: #a0aec0;
    text-transform: uppercase;
}
"""


class TodoTask:
    """Represents a scheduled todo item or appointment."""

    def __init__(
        self,
        task_id: str,
        title: str,
        date_str: str,
        time_str: str = "09:00",
        category: str = "Work",
        priority: str = "Normal",
        completed: bool = False,
    ) -> None:
        self.task_id = task_id
        self.title = title
        self.date_str = date_str
        self.time_str = time_str
        self.category = category
        self.priority = priority
        self.completed = completed


class ChronosWindow(Adw.ApplicationWindow):
    """Main Application Window for Chronos."""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.set_title("Chronos — Tasks & Calendar")
        self.set_default_size(1050, 680)
        self.add_css_class("chronos-window")

        # Initial Mock Data
        today_str = datetime.date.today().isoformat()
        self.tasks: list[TodoTask] = [
            TodoTask(
                "t_1",
                "Review Wayland MCP Pull Request",
                today_str,
                "10:30",
                "Work",
                "Urgent",
                False,
            ),
            TodoTask(
                "t_2",
                "Sync with Antigravity AI Pair Programmer",
                today_str,
                "14:00",
                "Work",
                "High",
                False,
            ),
            TodoTask(
                "t_3",
                "Weekly Gym & Cardio Session",
                today_str,
                "17:30",
                "Personal",
                "Normal",
                False,
            ),
            TodoTask(
                "t_4", "Submit v0.1.1 Release to PyPI", today_str, "19:00", "Work", "Urgent", True
            ),
            TodoTask(
                "t_5",
                "Plan Weekend Mountain Hike",
                "2026-10-12",
                "11:00",
                "Personal",
                "Normal",
                False,
            ),
        ]
        self.current_filter = "All"
        self.selected_date = datetime.date.today()

        self._build_ui()
        self._refresh_task_list()
        self._update_stats()

    def _build_ui(self) -> None:
        root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_content(root_box)

        # 1. HeaderBar with Title & Actions
        header = Adw.HeaderBar()
        title_widget = Adw.WindowTitle(
            title="Chronos Studio",
            subtitle="Smart Wayland Task & Schedule Hub",
        )
        header.set_title_widget(title_widget)

        # Quick Add Button in Header
        self.btn_quick_add = Gtk.Button(label="⚡ Add Task")
        self.btn_quick_add.add_css_class("suggested-action")
        self.btn_quick_add.connect("clicked", self._on_quick_add_clicked)
        header.pack_end(self.btn_quick_add)

        # Status badge in Header
        self.header_badge = Gtk.Label(label="Live Mode: Active")
        self.header_badge.add_css_class("badge-pill")
        self.header_badge.add_css_class("badge-normal")
        header.pack_start(self.header_badge)

        root_box.append(header)

        # 2. Split Area (Left Sidebar: Calendar & Stats; Right Area: Tasks & Form)
        h_split = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=20)
        h_split.set_margin_top(18)
        h_split.set_margin_bottom(18)
        h_split.set_margin_start(20)
        h_split.set_margin_end(20)
        h_split.set_hexpand(True)
        h_split.set_vexpand(True)
        root_box.append(h_split)

        # LEFT SIDEBAR
        left_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        left_box.set_size_request(340, -1)
        h_split.append(left_box)

        # Calendar Card
        cal_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        cal_card.add_css_class("calendar-card")
        left_box.append(cal_card)

        cal_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        cal_title = Gtk.Label(label="Calendar Schedule")
        cal_title.add_css_class("card-title")
        cal_header.append(cal_title)
        cal_card.append(cal_header)

        self.calendar = Gtk.Calendar()
        self.calendar.connect("day-selected", self._on_calendar_day_selected)
        cal_card.append(self.calendar)

        self.lbl_selected_date = Gtk.Label(
            label=f"Selected: {self.selected_date.strftime('%A, %b %d, %Y')}"
        )
        self.lbl_selected_date.set_halign(Gtk.Align.START)
        self.lbl_selected_date.add_css_class("stat-label")
        cal_card.append(self.lbl_selected_date)

        # Stats Card
        stats_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        stats_box.set_homogeneous(True)
        left_box.append(stats_box)

        # Pending Stat
        pending_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        pending_card.add_css_class("stat-box")
        self.lbl_pending_count = Gtk.Label(label="3")
        self.lbl_pending_count.add_css_class("stat-number")
        lbl_pending_txt = Gtk.Label(label="Pending")
        lbl_pending_txt.add_css_class("stat-label")
        pending_card.append(self.lbl_pending_count)
        pending_card.append(lbl_pending_txt)
        stats_box.append(pending_card)

        # Completed Stat
        completed_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        completed_card.add_css_class("stat-box")
        self.lbl_completed_count = Gtk.Label(label="1")
        self.lbl_completed_count.add_css_class("stat-number")
        self.lbl_completed_count.set_css_classes(["stat-number"])
        lbl_completed_txt = Gtk.Label(label="Completed")
        lbl_completed_txt.add_css_class("stat-label")
        completed_card.append(self.lbl_completed_count)
        completed_card.append(lbl_completed_txt)
        stats_box.append(completed_card)

        # RIGHT MAIN AREA
        right_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        right_box.set_hexpand(True)
        h_split.append(right_box)

        # 3. New Task Creator Bar
        creator_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        creator_card.add_css_class("main-content-card")
        right_box.append(creator_card)

        creator_title = Gtk.Label(label="Create New Task or Event")
        creator_title.add_css_class("card-title")
        creator_title.set_halign(Gtk.Align.START)
        creator_card.append(creator_title)

        inputs_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        creator_card.append(inputs_box)

        self.entry_task = Gtk.Entry()
        self.entry_task.set_placeholder_text("Task title / appointment summary...")
        self.entry_task.set_hexpand(True)
        self.entry_task.connect("activate", lambda _: self._on_add_task())
        inputs_box.append(self.entry_task)

        self.entry_time = Gtk.Entry()
        self.entry_time.set_placeholder_text("Time (e.g. 15:30)")
        self.entry_time.set_width_chars(12)
        self.entry_time.set_text("10:00")
        inputs_box.append(self.entry_time)

        # Category Dropdown
        categories = Gtk.StringList.new(["Work", "Personal", "Health", "Finance"])
        self.dropdown_cat = Gtk.DropDown.new(categories, None)
        inputs_box.append(self.dropdown_cat)

        # Priority Dropdown
        priorities = Gtk.StringList.new(["Normal", "High", "Urgent"])
        self.dropdown_prio = Gtk.DropDown.new(priorities, None)
        inputs_box.append(self.dropdown_prio)

        self.btn_submit = Gtk.Button(label="Add to Schedule")
        self.btn_submit.add_css_class("suggested-action")
        self.btn_submit.connect("clicked", lambda _: self._on_add_task())
        inputs_box.append(self.btn_submit)

        # 4. Filter Bar & Task List
        list_container = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        list_container.add_css_class("main-content-card")
        list_container.set_vexpand(True)
        right_box.append(list_container)

        filter_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        list_container.append(filter_bar)

        list_header_lbl = Gtk.Label(label="Scheduled Items")
        list_header_lbl.add_css_class("card-title")
        filter_bar.append(list_header_lbl)

        # Spacer
        filter_spacer = Gtk.Box()
        filter_spacer.set_hexpand(True)
        filter_bar.append(filter_spacer)

        # Filter Pills
        self.btn_filter_all = Gtk.Button(label="All")
        self.btn_filter_all.add_css_class("flat")
        self.btn_filter_all.connect("clicked", lambda _: self._set_filter("All"))
        filter_bar.append(self.btn_filter_all)

        self.btn_filter_active = Gtk.Button(label="Pending")
        self.btn_filter_active.add_css_class("flat")
        self.btn_filter_active.connect("clicked", lambda _: self._set_filter("Active"))
        filter_bar.append(self.btn_filter_active)

        self.btn_filter_completed = Gtk.Button(label="Completed")
        self.btn_filter_completed.add_css_class("flat")
        self.btn_filter_completed.connect("clicked", lambda _: self._set_filter("Completed"))
        filter_bar.append(self.btn_filter_completed)

        # Scrollable Box List
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_vexpand(True)
        scrolled.set_hexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        list_container.append(scrolled)

        self.task_list_box = Gtk.ListBox()
        self.task_list_box.set_selection_mode(Gtk.SelectionMode.NONE)
        self.task_list_box.add_css_class("boxed-list")
        scrolled.set_child(self.task_list_box)

    def _on_calendar_day_selected(self, calendar: Gtk.Calendar) -> None:
        gdate = calendar.get_date()
        year = gdate.get_year()
        month = gdate.get_month()
        day = gdate.get_day_of_month()
        self.selected_date = datetime.date(year, month, day)
        self.lbl_selected_date.set_text(f"Selected: {self.selected_date.strftime('%A, %b %d, %Y')}")
        self._refresh_task_list()

    def _on_quick_add_clicked(self, _btn: Gtk.Button) -> None:
        self.entry_task.grab_focus()

    def _on_add_task(self) -> None:
        title = self.entry_task.get_text().strip()
        if not title:
            return
        time_val = self.entry_time.get_text().strip() or "12:00"

        cat_idx = self.dropdown_cat.get_selected()
        categories = ["Work", "Personal", "Health", "Finance"]
        cat_str = categories[cat_idx] if cat_idx < len(categories) else "Work"

        prio_idx = self.dropdown_prio.get_selected()
        priorities = ["Normal", "High", "Urgent"]
        prio_str = priorities[prio_idx] if prio_idx < len(priorities) else "Normal"

        new_id = f"t_{len(self.tasks) + 1}_{int(GLib.get_monotonic_time())}"
        task = TodoTask(
            task_id=new_id,
            title=title,
            date_str=self.selected_date.isoformat(),
            time_str=time_val,
            category=cat_str,
            priority=prio_str,
            completed=False,
        )
        self.tasks.append(task)
        self.entry_task.set_text("")
        self._refresh_task_list()
        self._update_stats()

    def _set_filter(self, filter_name: str) -> None:
        self.current_filter = filter_name
        self._refresh_task_list()

    def _on_toggle_task(self, task: TodoTask, check_btn: Gtk.CheckButton) -> None:
        task.completed = check_btn.get_active()
        self._update_stats()
        self._refresh_task_list()

    def _on_delete_task(self, task: TodoTask) -> None:
        self.tasks = [t for t in self.tasks if t.task_id != task.task_id]
        self._refresh_task_list()
        self._update_stats()

    def _update_stats(self) -> None:
        pending = sum(1 for t in self.tasks if not t.completed)
        completed = sum(1 for t in self.tasks if t.completed)
        self.lbl_pending_count.set_text(str(pending))
        self.lbl_completed_count.set_text(str(completed))

    def _refresh_task_list(self) -> None:
        # Clear existing rows
        while child := self.task_list_box.get_first_child():
            self.task_list_box.remove(child)

        filtered = [
            t
            for t in self.tasks
            if (
                self.current_filter == "All"
                or (self.current_filter == "Active" and not t.completed)
                or (self.current_filter == "Completed" and t.completed)
            )
        ]

        if not filtered:
            empty_row = Gtk.ListBoxRow()
            empty_lbl = Gtk.Label(label="No tasks found for this view.")
            empty_lbl.set_margin_top(20)
            empty_lbl.set_margin_bottom(20)
            empty_lbl.add_css_class("dim-label")
            empty_row.set_child(empty_lbl)
            self.task_list_box.append(empty_row)
            return

        for task in filtered:
            row = Gtk.ListBoxRow()
            row_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
            row_box.add_css_class("task-row")
            row.set_child(row_box)

            # Checkbox
            chk = Gtk.CheckButton()
            chk.set_active(task.completed)
            chk.connect("toggled", lambda b, t=task: self._on_toggle_task(t, b))
            row_box.append(chk)

            # Title & Time container
            text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            text_box.set_hexpand(True)

            title_lbl = Gtk.Label(label=task.title)
            title_lbl.set_halign(Gtk.Align.START)
            if task.completed:
                title_lbl.add_css_class("task-completed")
            text_box.append(title_lbl)

            sub_lbl = Gtk.Label(label=f"⏰ {task.time_str} • {task.date_str}")
            sub_lbl.set_halign(Gtk.Align.START)
            sub_lbl.add_css_class("dim-label")
            text_box.append(sub_lbl)

            row_box.append(text_box)

            # Category Badge
            cat_badge = Gtk.Label(label=task.category)
            cat_badge.add_css_class("badge-pill")
            cat_badge.add_css_class("badge-category")
            row_box.append(cat_badge)

            # Priority Badge
            prio_badge = Gtk.Label(label=task.priority)
            prio_badge.add_css_class("badge-pill")
            if task.priority == "Urgent":
                prio_badge.add_css_class("badge-urgent")
            elif task.priority == "High":
                prio_badge.add_css_class("badge-high")
            else:
                prio_badge.add_css_class("badge-normal")
            row_box.append(prio_badge)

            # Delete Action Button
            btn_del = Gtk.Button(label="🗑")
            btn_del.add_css_class("flat")
            btn_del.connect("clicked", lambda _, t=task: self._on_delete_task(t))
            row_box.append(btn_del)

            self.task_list_box.append(row)


class ChronosApp(Adw.Application):
    """Application wrapper managing stylesheet and lifecycle."""

    def __init__(self) -> None:
        super().__init__(
            application_id="org.wayland.mcp.ChronosTasks",
            flags=Gio.ApplicationFlags.DEFAULT_FLAGS,
        )

    def do_activate(self) -> None:
        # Load Dark Gradient CSS
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(CUSTOM_CSS.encode("utf-8"))
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        win = self.props.active_window
        if not win:
            win = ChronosWindow(application=self)
        win.present()


def main() -> None:
    """Entry point for Chronos."""
    app = ChronosApp()
    app.run(sys.argv)


if __name__ == "__main__":
    main()
