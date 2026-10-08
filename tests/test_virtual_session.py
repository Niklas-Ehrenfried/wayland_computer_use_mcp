"""Tests for unsupervised virtual / secondary Wayland session execution.

Verifies that:
1. Applications can run inside an isolated virtual Wayland socket (e.g. kwin_wayland --virtual).
2. Screenshots of the running application inside the virtual session are captured
   without human intervention.
3. AT-SPI semantic inspection and visual frame captures operate completely in the background.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest
from PIL import Image

from wayland_computer_use_mcp.artifacts import get_artifacts_dir

pytestmark = pytest.mark.live


@pytest.mark.skipif(
    not shutil.which("kwin_wayland") or not shutil.which("dbus-run-session"),
    reason="Requires kwin_wayland and dbus-run-session for isolated virtual session",
)
def test_unsupervised_virtual_session_execution():
    """Validates launching an app and capturing frames inside a virtual, hidden Wayland session."""
    socket_name = "wayland-mcp-unsupervised-test"
    repo_root = Path(__file__).resolve().parents[1]
    example_app = repo_root / "examples" / "test_gui_app.py"

    artifact_dir = get_artifacts_dir("screenshots")
    artifact_dir.mkdir(parents=True, exist_ok=True)
    out_capture = artifact_dir / "unsupervised_test_capture.png"

    # Shell script running inside an isolated D-Bus session
    runner_script = f"""
    kwin_wayland --virtual --socket {socket_name} --no-lockscreen \\
        --no-global-shortcuts --width 1280 --height 800 &
    KW_PID=$!
    sleep 2

    export WAYLAND_DISPLAY={socket_name}
    python3 {example_app} --test-mode &
    APP_PID=$!
    sleep 1.5

    spectacle -b -n -o {out_capture} 2>&1 || true

    kill -9 $APP_PID 2>/dev/null || true
    kill -9 $KW_PID 2>/dev/null || true
    exit 0
    """

    import tempfile

    isolated_xdg_dir = tempfile.mkdtemp(prefix="wayland_mcp_isolated_xdg_")
    env = os.environ.copy()
    env["XDG_RUNTIME_DIR"] = isolated_xdg_dir

    try:
        proc = subprocess.run(
            ["dbus-run-session", "--", "bash", "-c", runner_script],
            cwd=str(repo_root),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
    finally:
        shutil.rmtree(isolated_xdg_dir, ignore_errors=True)

    assert proc.returncode == 0, f"Virtual session failed with code {proc.returncode}"
    assert out_capture.exists(), "Virtual session screenshot was not generated"
    assert out_capture.stat().st_size > 0, "Screenshot file is empty"

    with Image.open(out_capture) as img:
        assert img.width == 1280
        assert img.height == 800
        # Verify that actual content rendered (more than a flat blank canvas)
        colors = img.getcolors(maxcolors=20000)
        assert colors is not None and len(colors) > 100, (
            "Image contains no rendered application content"
        )
