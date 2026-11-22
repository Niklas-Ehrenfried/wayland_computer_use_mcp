#!/usr/bin/env python3
"""Minimal zero-widget GTK4 Cairo canvas app for Level 2/3 coordinate + visual tests."""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402


def on_activate(app: Gtk.Application) -> None:
    win = Gtk.ApplicationWindow(application=app, title="Canvas Smoke App")
    win.set_default_size(420, 260)
    win.set_decorated(False)

    state = {"clicked": False}
    area = Gtk.DrawingArea()
    area.set_content_width(420)
    area.set_content_height(260)

    def draw_func(_area: Gtk.DrawingArea, cr: object, width: int, height: int) -> None:
        cr.set_source_rgb(0.12, 0.14, 0.18)
        cr.rectangle(0, 0, width, height)
        cr.fill()

        if state["clicked"]:
            cr.set_source_rgb(0.15, 0.72, 0.28)
        else:
            cr.set_source_rgb(0.86, 0.20, 0.24)
        cr.rectangle(40, 40, max(40, width - 80), max(40, height - 80))
        cr.fill()

    area.set_draw_func(draw_func)

    gesture = Gtk.GestureClick()

    def on_pressed(_gesture: Gtk.GestureClick, _n_press: int, x: float, y: float) -> None:
        state["clicked"] = True
        area.queue_draw()
        print(f"[CanvasRig] clicked at ({x:.1f}, {y:.1f})", flush=True)

    gesture.connect("pressed", on_pressed)
    area.add_controller(gesture)

    win.set_child(area)
    win.present()
    print("[CanvasRig] ready", flush=True)


def main() -> int:
    app = Gtk.Application(application_id="org.example.WaylandMcpCanvasSmoke")
    app.connect("activate", on_activate)
    return int(app.run(sys.argv))


if __name__ == "__main__":
    raise SystemExit(main())
