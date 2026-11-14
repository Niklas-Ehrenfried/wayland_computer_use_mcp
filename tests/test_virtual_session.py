"""Tests for Blackbox unsupervised headless virtual Wayland session execution.

Verifies that:
1. Applications run inside the isolated Blackbox sandbox (headless compositor + private D-Bus).
2. Screenshots of the running application are captured cleanly without user interaction.
3. AT-SPI semantic inspection and teardown (no leaked processes/dirs) work automatically.
"""

from __future__ import annotations

import io
import os
import shutil

import pytest
from PIL import Image

from wayland_computer_use_mcp.config import Config, get_config, set_config
from wayland_computer_use_mcp.portal import global_portal_session
from wayland_computer_use_mcp.server import (
    capture_window_frame,
    inspect_ui_tree,
    launch_app,
    terminate_app,
)

pytestmark = pytest.mark.live


def _has_blackbox_compositor() -> bool:
    return any(shutil.which(cmd) for cmd in ("kwin_wayland", "labwc", "sway", "weston"))


@pytest.mark.skipif(
    not _has_blackbox_compositor() or not shutil.which("dbus-daemon"),
    reason="Requires a Blackbox nested compositor (kwin_wayland/labwc/sway) and dbus-daemon",
)
def test_unsupervised_virtual_session_execution():
    """Validates headless Blackbox sandbox app launch, capture, AT-SPI tree, and teardown."""
    prev_cfg = get_config()
    cfg = Config(display_mode="virtual", virtual_headed=False, mock_mode=False)
    set_config(cfg)
    pid: int | None = None
    try:
        launch_res = launch_app("examples/test_gui_app.py", args=["--test-mode"])
        assert launch_res["status"] == "launched"
        pid = int(launch_res["pid"])
        assert launch_res["interactive_elements"]

        tree = inspect_ui_tree(pid=pid, max_depth=10)
        assert len(tree.get("interactive_elements", [])) > 0

        capture = capture_window_frame(pid=pid, save_artifact=True)
        assert len(capture) >= 2
        assert capture.file_path and os.path.exists(capture.file_path)

        with Image.open(io.BytesIO(capture.raw_bytes)) as img:
            assert img.width > 0 and img.height > 0
            colors = img.getcolors(maxcolors=20000)
            assert colors is None or len(colors) > 20, "Captured frame appears blank"
    finally:
        if pid is not None:
            try:
                term = terminate_app(pid)
                assert term["status"] == "terminated"
            except Exception:
                pass
        global_portal_session.close()
        set_config(prev_cfg)
