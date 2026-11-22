"""Cross-toolkit live Blackbox smoke tests (Cairo Canvas, Qt6, and WebKitGTK DOM)."""

from __future__ import annotations

import io
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
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


def _is_pid_running(pid: int) -> bool:
    """Returns True only if pid exists and is not an unreaped zombie/dead task."""
    status_path = Path(f"/proc/{pid}/status")
    if not status_path.exists():
        return False
    try:
        for line in status_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if line.startswith("State:"):
                state = line.split(":", 1)[1].strip()
                return not state.startswith(("Z", "X"))
    except OSError:
        return False
    return True


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
        wait_res = wait_for_element(target="Apply Query", timeout_ms=10000, pid=pid)
        if not wait_res["matched"]:
            elems = inspect_ui_tree(pid=pid).get("interactive_elements", [])
            logs = get_app_logs(pid=pid, lines=40)
            raise AssertionError(
                f"Apply Query not found in WebKit AT-SPI tree; elements={elems}; logs={logs!r}"
            )
        interact_with_node(target="Apply Query", action="click", pid=pid)
        time.sleep(0.2)
        tree = inspect_ui_tree(pid=pid)
        assert tree.get("atspi_available") is True
    finally:
        terminate_app(pid)


def test_gtk3_toolkit_live(blackbox_session: None) -> None:
    """GTK3 widgets expose AT-SPI entries/checkboxes/buttons under Wayland."""
    py = _find_python_for_modules("gi")
    if not py:
        pytest.skip("PyGObject not installed")
    probe = subprocess.run(
        [
            py,
            "-c",
            "import gi; gi.require_version('Gtk', '3.0'); from gi.repository import Gtk",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
        check=False,
    )
    if probe.returncode != 0:
        pytest.skip("GTK3 GI typelib not installed in environment")

    repo_root = Path(__file__).resolve().parent.parent
    script_path = repo_root / "examples" / "test_gtk3_app.py"
    res = launch_app(
        py,
        args=[str(script_path)],
        cwd=str(repo_root),
        ready_timeout_ms=8000,
    )
    assert res["status"] == "launched"
    pid = int(res["pid"])
    try:
        wait_res = wait_for_element(target="Apply", role="push button", timeout_ms=5000, pid=pid)
        assert wait_res["matched"] is True

        interact_with_node(target="Note", action="type", text="hello-gtk3", pid=pid)
        interact_with_node(target="Enable sync", action="click", pid=pid)
        interact_with_node(target="Apply", role="push button", action="click", pid=pid)

        logs = get_app_logs(pid=pid, lines=40)
        assert "[Gtk3Rig] Applied: 1 | hello-gtk3 | sync=True" in logs
    finally:
        terminate_app(pid)


def test_wrapper_launched_descendant_pid_live(blackbox_session: None) -> None:
    """Wrapper commands (/bin/sh -c without exec) resolve descendant AT-SPI trees and clean up."""
    py = _find_python_for_modules("gi")
    if not py:
        pytest.skip("PyGObject not installed")
    probe = subprocess.run(
        [
            py,
            "-c",
            "import gi; gi.require_version('Gtk', '4.0'); from gi.repository import Gtk",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=5,
        check=False,
    )
    if probe.returncode != 0:
        pytest.skip("GTK4 GI typelib not installed")

    repo_root = Path(__file__).resolve().parent.parent
    script_path = repo_root / "examples" / "test_gui_app.py"
    shell_cmd = f"{shlex.quote(py)} {shlex.quote(str(script_path))} --test-mode; true"

    res = launch_app(
        "/bin/sh",
        args=["-c", shell_cmd],
        cwd=str(repo_root),
        ready_timeout_ms=8000,
    )
    assert res["status"] == "launched"
    pid = int(res["pid"])
    descendant_pids: set[int] = set()
    try:
        from wayland_computer_use_mcp.a11y.client import _collect_process_tree_pids

        wait_res = wait_for_element(target="Click Me!", timeout_ms=6000, pid=pid)
        assert wait_res["matched"] is True
        descendant_pids = _collect_process_tree_pids(pid) - {pid}
        assert descendant_pids, "Expected child GUI PID under wrapper /bin/sh"

        interact_with_node(target="Click Me!", action="click", pid=pid)
        clicked = wait_for_element(target="Clicked! (1 clicks)", timeout_ms=4000, pid=pid)
        assert clicked["matched"] is True
    finally:
        terminate_app(pid)

    time.sleep(0.15)
    for cpid in descendant_pids:
        assert not _is_pid_running(cpid), f"Descendant PID {cpid} still running after terminate"


def test_chromium_dom_live(blackbox_session: None) -> None:
    """Chromium/Chrome renders web DOM controls accessible over AT-SPI2 under Wayland."""
    browser_bin = next(
        (
            shutil.which(name)
            for name in ("google-chrome", "chromium", "chromium-browser")
            if shutil.which(name)
        ),
        None,
    )
    if not browser_bin:
        pytest.skip("Chromium / Google Chrome not installed in environment")

    html = (
        "data:text/html,<title>MCP Chrome Test</title>"
        "<h1>Hello</h1>"
        '<input aria-label="Search Box" type="text">'
        "<button onclick=\"document.getElementById('out').textContent="
        "'Clicked: '+document.querySelector('input').value\">Go Now</button>"
        '<p id="out">Idle</p>'
    )
    with tempfile.TemporaryDirectory(prefix="wayland-mcp-chrome-") as user_data_dir:
        args = [
            f"--user-data-dir={user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
            f"--app={html}",
        ]
        if (
            os.environ.get("WLR_RENDERER") == "pixman"
            or os.environ.get("LIBGL_ALWAYS_SOFTWARE") == "1"
        ):
            args.insert(0, "--disable-gpu")

        res = launch_app(browser_bin, args=args, ready_timeout_ms=8000)
        assert res["status"] == "launched"
        pid = int(res["pid"])
        try:
            wait_res = wait_for_element(target="Go Now", timeout_ms=8000, pid=pid)
            assert wait_res["matched"] is True

            interact_with_node(target="Search Box", action="click", pid=pid)
            time.sleep(0.15)
            interact_with_node(target="Search Box", action="type", text="wayland-ok", pid=pid)
            time.sleep(0.15)
            interact_with_node(target="Go Now", action="click", pid=pid)
            updated = wait_for_element(target="Clicked: wayland-ok", timeout_ms=5000, pid=pid)
            if not updated["matched"]:
                elems = inspect_ui_tree(pid=pid).get("interactive_elements", [])
                raise AssertionError(f"Expected 'Clicked: wayland-ok', got elements={elems}")
        finally:
            terminate_app(pid)
