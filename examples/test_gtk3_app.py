#!/usr/bin/env python3
"""Minimal GTK3 Wayland smoke-test application with AT-SPI2 widgets."""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402


def main() -> int:
    win = Gtk.Window(title="GTK3 Smoke App")
    win.set_default_size(400, 220)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
    box.set_margin_top(12)
    box.set_margin_bottom(12)
    box.set_margin_start(12)
    box.set_margin_end(12)

    count = 0
    status = Gtk.Label(label="Status: idle")

    entry = Gtk.Entry()
    entry.set_placeholder_text("Type note...")
    entry.get_accessible().set_name("Note")

    check = Gtk.CheckButton(label="Enable sync")

    btn = Gtk.Button(label="Apply")

    def on_clicked(_btn: Gtk.Button) -> None:
        nonlocal count
        count += 1
        note = entry.get_text().strip() or "empty"
        text = f"Applied: {count} | {note} | sync={check.get_active()}"
        status.set_text(text)
        print(f"[Gtk3Rig] {text}", flush=True)

    btn.connect("clicked", on_clicked)

    box.pack_start(status, False, False, 0)
    box.pack_start(entry, False, False, 0)
    box.pack_start(check, False, False, 0)
    box.pack_start(btn, False, False, 0)

    win.add(box)
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    print("[Gtk3Rig] ready", flush=True)
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
