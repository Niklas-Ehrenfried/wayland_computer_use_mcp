"""Cross-toolkit live Blackbox smoke tests (Cairo Canvas, Qt6, and WebKitGTK DOM)."""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
import time
from collections.abc import Generator
from pathlib import Path

import pytest
from PIL import Image

from wayland_computer_use_mcp.config import Config, get_config, set_config
from wayland_computer_use_mcp.portal import global_portal_session
from wayland_computer_use_mcp.server import (
    capture_window_frame,
    get_app_logs,
    inspect_ui_tree,
    interact_with_node,
    launch_app,
    mouse,
    terminate_app,
    wait_for_element,
)

pytestmark = pytest.mark.live


def _has_blackbox_compositor() -> bool:
    return any(shutil.which(cmd) for cmd in ("kwin_wayland", "labwc", "sway", "weston"))


def _find_python_for_modules(*modules: str) -> str | None:
    """Finds a Python interpreter that can import all requested modules."""
    probe = "; ".join(f"import {m}" for m in modules)
    candidates = [sys.executable, shutil.which("python3") or "", "/usr/bin/python3"]
    seen: set[str] = set()
    for py in candidates:
        if not py or py in seen:
            continue
        seen.add(py)
        try:
            res = subprocess.run(
                [py, "-c", probe],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
            if res.returncode == 0:
                return py
        except Exception:
            continue
    return None


def _sample_center_rgb(raw_png: bytes) -> tuple[int, int, int]:
    with Image.open(io.BytesIO(raw_png)) as img:
        rgb = img.convert("RGB")
        px = rgb.getpixel((rgb.width // 2, rgb.height // 2))
        return int(px[0]), int(px[1]), int(px[2])


@pytest.fixture
def blackbox_session() -> Generator[None, None, None]:
    if not _has_blackbox_compositor() or not shutil.which("dbus-daemon"):
        pytest.skip("Requires a Blackbox nested compositor and dbus-daemon")
    prev_cfg = get_config()
    set_config(Config(display_mode="virtual", virtual_headed=False, mock_mode=False))
    try:
        yield
    finally:
        global_portal_session.close()
        set_config(prev_cfg)


def test_canvas_visual_fallback_live(blackbox_session: None) -> None:
    """Zero-widget Cairo canvas exposes empty semantics and flips red->green on coordinate click."""
    res = launch_app("examples/test_canvas_app.py", ready_timeout_ms=6000)
    assert res["status"] == "launched"
    pid = int(res["pid"])
    try:
        assert res["interactive_elements"] == []
        tree = inspect_ui_tree(pid=pid)
        assert tree.get("interactive_elements") == []

        # Allow initial software-rendered Cairo surface to commit before sampling
        before_rgb = (0, 0, 0)
        for _ in range(8):
            before = capture_window_frame(pid=pid)
            before_rgb = _sample_center_rgb(before.raw_bytes)
            if before_rgb[0] > before_rgb[1] + 50:
                break
            time.sleep(0.25)
        assert before_rgb[0] > before_rgb[1] + 50, f"Expected red initial canvas, got {before_rgb}"

        click_res = mouse(action="click", x=210, y=130, pid=pid)
        assert click_res["status"] == "success"

        after_rgb = (0, 0, 0)
        for _ in range(8):
            after = capture_window_frame(pid=pid)
            after_rgb = _sample_center_rgb(after.raw_bytes)
            if after_rgb[1] > after_rgb[0] + 40:
                break
            time.sleep(0.25)
        assert after_rgb[1] > after_rgb[0] + 40, f"Expected green clicked canvas, got {after_rgb}"
        assert "[CanvasRig] clicked" in get_app_logs(pid=pid, lines=30)
    finally:
        terminate_app(pid)


def test_qt6_toolkit_live(blackbox_session: None) -> None:
    """Qt6 Widgets expose AT-SPI push buttons/checkboxes/inputs under Wayland."""
    py = _find_python_for_modules("PyQt6.QtWidgets") or _find_python_for_modules(
        "PySide6.QtWidgets"
    )
    if not py:
        pytest.skip("PyQt6 / PySide6 not installed in environment")

    repo_root = Path(__file__).resolve().parent.parent
    script_path = repo_root / "examples" / "test_qt6_app.py"
    res = launch_app(
        py,
        args=[str(script_path)],
        cwd=str(repo_root),
        ready_timeout_ms=8000,
    )
    assert res["status"] == "launched"
    pid = int(res["pid"])
    try:
        wait_res = wait_for_element(
            target="Increment", role="push button", timeout_ms=5000, pid=pid
        )
        assert wait_res["matched"] is True

        interact_with_node(target="Increment", role="push button", action="click", pid=pid)
        interact_with_node(target="Note", action="type", text="hello-qt6", pid=pid)
        interact_with_node(target="Enable sync", action="click", pid=pid)
        interact_with_node(target="Increment", role="push button", action="click", pid=pid)

        logs = get_app_logs(pid=pid, lines=40)
        assert "Count: 2 | hello-qt6 | sync=True" in logs
    finally:
        terminate_app(pid)


def test_webkit_dom_live(blackbox_session: None) -> None:
    """WebKitGTK 6.0 exposes DOM buttons and status updates over AT-SPI2 under Wayland."""
    py = _find_python_for_modules("gi")
    if not py:
        pytest.skip("PyGObject not installed")
    probe = subprocess.run(
        [
            py,
            "-c",
            "import gi; gi.require_version('WebKit', '6.0'); from gi.repository import WebKit",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
        check=False,
    )
    if probe.returncode != 0:
        pytest.skip("WebKitGTK 6.0 GI typelib not installed in environment")

    repo_root = Path(__file__).resolve().parent.parent
    script_path = repo_root / "examples" / "test_webkit_app.py"
    res = launch_app(
        py,
        args=[str(script_path)],
        cwd=str(repo_root),
        ready_timeout_ms=8000,
    )
    assert res["status"] == "launched"
    pid = int(res["pid"])
    try:
        wait_res = wait_for_element(target="Apply Query", timeout_ms=6000, pid=pid)
        assert wait_res["matched"] is True
        interact_with_node(target="Apply Query", action="click", pid=pid)
        time.sleep(0.2)
        tree = inspect_ui_tree(pid=pid)
        assert tree.get("atspi_available") is True
    finally:
        terminate_app(pid)
