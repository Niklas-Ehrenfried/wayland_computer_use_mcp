#!/usr/bin/env python3
"""Minimal WebKitGTK 6.0 Wayland smoke-test application exposing DOM nodes over AT-SPI2."""

from __future__ import annotations

import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("WebKit", "6.0")
from gi.repository import Gtk, WebKit  # noqa: E402

HTML_DOC = """<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><title>WebKit Smoke App</title></head>
<body style="font-family: sans-serif; padding: 20px;">
  <h1>WebKit Smoke</h1>
  <label for="q">Search Query</label>
  <input id="q" aria-label="Search Query" type="text" value="" />
  <button id="apply" type="button"
    onclick="const v=document.getElementById('q').value||'none';
             document.getElementById('out').textContent='Applied: '+v;
             console.log('[WebKitRig] Applied: '+v);">Apply Query</button>
  <p id="out" role="status">Applied: idle</p>
</body>
</html>
"""


def on_activate(app: Gtk.Application) -> None:
    win = Gtk.ApplicationWindow(application=app, title="WebKit Smoke App")
    win.set_default_size(520, 320)

    view = WebKit.WebView()
    settings = view.get_settings()
    settings.set_enable_javascript(True)
    view.load_html(HTML_DOC, "http://localhost/")

    win.set_child(view)
    win.present()
    print("[WebKitRig] ready", flush=True)


def main() -> int:
    app = Gtk.Application(application_id="org.example.WaylandMcpWebKitSmoke")
    app.connect("activate", on_activate)
    return int(app.run(sys.argv))


if __name__ == "__main__":
    raise SystemExit(main())
