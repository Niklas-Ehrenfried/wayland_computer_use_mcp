#!/usr/bin/env python3
"""Minimal zero-widget GTK4 CSS canvas app for Level 2/3 coordinate + visual tests."""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

_CSS = b"""
.canvas-red {
    background-color: #dc2626;
}
.canvas-green {
    background-color: #22c55e;
}
"""


def on_activate(app: Gtk.Application) -> None:
    win = Gtk.ApplicationWindow(application=app, title="Canvas Smoke App")
    win.set_default_size(420, 260)
    win.set_decorated(False)

    provider = Gtk.CssProvider()
    provider.load_from_data(_CSS)
    display = Gdk.Display.get_default()
    if display is not None:
        Gtk.StyleContext.add_provider_for_display(
            display,
            provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    surface = Gtk.Box()
    surface.set_hexpand(True)
    surface.set_vexpand(True)
    surface.add_css_class("canvas-red")

    gesture = Gtk.GestureClick()

    def on_pressed(_gesture: Gtk.GestureClick, _n_press: int, x: float, y: float) -> None:
        surface.remove_css_class("canvas-red")
        surface.add_css_class("canvas-green")
        print(f"[CanvasRig] clicked at ({x:.1f}, {y:.1f})", flush=True)

    gesture.connect("pressed", on_pressed)
    surface.add_controller(gesture)

    win.set_child(surface)
    win.present()
    print("[CanvasRig] ready", flush=True)


def main() -> int:
    app = Gtk.Application(application_id="org.example.WaylandMcpCanvasSmoke")
    app.connect("activate", on_activate)
    return int(app.run(sys.argv))


if __name__ == "__main__":
    raise SystemExit(main())
