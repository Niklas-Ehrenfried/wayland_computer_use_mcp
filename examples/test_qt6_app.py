#!/usr/bin/env python3
"""Minimal Qt6 (PyQt6 / PySide6) Wayland smoke-test application with AT-SPI2 widgets."""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_LINUX_ACCESSIBILITY_ALWAYS_ON", "1")
if os.environ.get("WAYLAND_DISPLAY"):
    os.environ.setdefault("QT_QPA_PLATFORM", "wayland")

try:
    from PyQt6.QtWidgets import (
        QApplication,
        QCheckBox,
        QLabel,
        QLineEdit,
        QMainWindow,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )
except ImportError:
    from PySide6.QtWidgets import (
        QApplication,
        QCheckBox,
        QLabel,
        QLineEdit,
        QMainWindow,
        QPushButton,
        QVBoxLayout,
        QWidget,
    )


def main() -> int:
    app = QApplication(sys.argv)
    win = QMainWindow()
    win.setWindowTitle("Qt6 Smoke App")
    win.resize(420, 260)

    root = QWidget(win)
    layout = QVBoxLayout(root)

    count = 0
    status = QLabel("Status: idle")
    status.setAccessibleName("Status: idle")

    note_input = QLineEdit()
    note_input.setAccessibleName("Note")
    note_input.setPlaceholderText("Type note...")

    sync_cb = QCheckBox("Enable sync")
    sync_cb.setAccessibleName("Enable sync")

    btn = QPushButton("Increment")
    btn.setAccessibleName("Increment")

    def on_click() -> None:
        nonlocal count
        count += 1
        note = note_input.text().strip() or "empty"
        text = f"Count: {count} | {note} | sync={sync_cb.isChecked()}"
        status.setText(text)
        status.setAccessibleName(text)
        print(f"[Qt6Rig] {text}", flush=True)

    btn.clicked.connect(on_click)

    layout.addWidget(status)
    layout.addWidget(note_input)
    layout.addWidget(sync_cb)
    layout.addWidget(btn)
    win.setCentralWidget(root)
    win.show()
    print("[Qt6Rig] ready", flush=True)
    return int(app.exec())


if __name__ == "__main__":
    raise SystemExit(main())
