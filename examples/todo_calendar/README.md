# Chronos — Tasks & Calendar Studio

**Chronos** is a modern, visually stunning Linux desktop Todo and Calendar application built with **GTK 4** and **Libadwaita** (`PyGObject`).

Designed with a sleek dark glassmorphic UI aesthetic, it features dynamic priority badges, category tagging, interactive calendar scheduling, and real-time statistics counters.

![Chronos Studio Preview](preview.png)

---

## Features

- **Modern Glassmorphic Dark UI**: Deep slate backgrounds, translucent frosted cards, subtle borders, and smooth hover transitions.
- **Calendar Scheduling**: Integrated `Gtk.Calendar` with active date selection and dynamic day format feedback.
- **Task Management**:
  - Add tasks with title, time, category (Work / Personal / Projects), and priority level (Normal / High / Urgent).
  - Quick checkbox toggling with strike-through styling.
  - Delete tasks directly with one click.
  - Dynamic filtering: **All**, **Pending**, and **Completed**.
- **Real-Time Analytics**: Live Pending and Completed counters that update instantly on task state changes.
- **Wayland Native & Accessible**: Full AT-SPI D-Bus accessibility tree exposure, verified autonomously with [`wayland-computer-use-mcp`](https://pypi.org/project/wayland-computer-use-mcp/).

---

## Running the Application

Ensure system GTK 4 and Libadwaita libraries are present (standard on GNOME / Fedora / Ubuntu / Arch).

Run directly using `uv`:

```bash
uv run python examples/todo_calendar/app.py
```
