"""Tests for MCP portability, resource cleanup, process draining, and tool consistency."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from wayland_computer_use_mcp import __version__
from wayland_computer_use_mcp.a11y import flatten_tree
from wayland_computer_use_mcp.artifacts import get_artifacts_dir
from wayland_computer_use_mcp.portal.remotedesktop import RemoteDesktopClient
from wayland_computer_use_mcp.portal.session import PortalSession
from wayland_computer_use_mcp.process import get_logs, launch, terminate
from wayland_computer_use_mcp.server import capture_window_frame, wayland_automation_guide
from wayland_computer_use_mcp.tools.navigation_tools import batch_actions
from wayland_computer_use_mcp.tools.process_tools import launch_app


def test_package_version_synced():
    assert __version__ == "0.1.1"


def test_bundled_system_prompt_loads_and_accurate(monkeypatch):
    prompt = wayland_automation_guide()
    assert "interact_with_node" in prompt
    assert "click_element_by_label" not in prompt


def test_artifacts_xdg_fallback_outside_workspace(tmp_path, monkeypatch):
    monkeypatch.delenv("WAYLAND_MCP_SAVE_FRAMES_DIR", raising=False)
    monkeypatch.delenv("WAYLAND_MCP_ARTIFACTS_DIR", raising=False)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg_cache"))
    monkeypatch.setattr("wayland_computer_use_mcp.artifacts._find_workspace_root", lambda: None)

    screenshots = get_artifacts_dir("screenshots")
    presets = get_artifacts_dir("presets")
    assert str(screenshots).startswith(str(tmp_path / "xdg_cache"))
    assert str(presets).startswith(str(tmp_path / "xdg_cache"))
    assert screenshots.is_dir()
    assert presets.is_dir()

    custom_base = tmp_path / "custom_base"
    monkeypatch.setenv("WAYLAND_MCP_ARTIFACTS_DIR", str(custom_base))
    assert get_artifacts_dir("presets") == custom_base / "presets"


def test_portal_session_close_releases_resources(monkeypatch):
    killpg_calls = []
    monkeypatch.setattr("os.killpg", lambda pgid, sig: killpg_calls.append((pgid, sig)))

    session = PortalSession()
    session._initialized = True
    mock_screencast_stop = MagicMock()
    session._screencast.stop = mock_screencast_stop

    mock_ei = MagicMock()
    session._rd_client.ei_client = mock_ei
    mock_bus = MagicMock()
    session._rd_client.bus = mock_bus

    mock_proc = MagicMock()
    session._virtual_proc = mock_proc

    session.close()

    assert killpg_calls == []
    mock_screencast_stop.assert_called_once()
    mock_ei.close.assert_called_once()
    mock_bus.disconnect.assert_called_once()
    mock_proc.terminate.assert_called_once()
    assert session._initialized is False
    assert session._virtual_proc is None
    assert session.ei_client is None
    assert session.bus is None

    # Verify AsyncLoopThread can still run work after close()
    async def _ping() -> str:
        return "pong"

    assert session._async_thread.run(_ping(), timeout=2.0) == "pong"
    session._async_thread.stop()


def test_remotedesktop_client_close_handles_none():
    from wayland_computer_use_mcp.portal.remotedesktop import AsyncLoopThread

    thread = AsyncLoopThread()
    try:
        client = RemoteDesktopClient(thread)
        client.close()
        assert client.ei_client is None
        assert client.bus is None
    finally:
        thread.stop()


def test_process_launch_drains_stdout_without_deadlock(tmp_path):
    script = tmp_path / "noisy_stdout.py"
    # Emit >128KB to stdout (exceeds default 64KB Linux pipe buffer)
    script.write_text(
        "import sys\n"
        "for i in range(2500):\n"
        "    sys.stdout.write(f'stdout-line-{i:04d} ' + 'x' * 50 + '\\n')\n"
        "sys.stdout.flush()\n"
    )
    pid = launch(str(script))
    try:
        deadline = time.time() + 5.0
        logs = ""
        while time.time() < deadline:
            logs = get_logs(pid, lines=20)
            if "stdout-line-2499" in logs:
                break
            time.sleep(0.05)
        assert "stdout-line-2499" in logs
    finally:
        try:
            terminate(pid)
        except Exception:
            pass


def test_launch_app_restart_updates_target_pid_and_elements(tmp_path, monkeypatch):
    from wayland_computer_use_mcp.portal import global_portal_session

    script = tmp_path / "sleep_app.py"
    script.write_text("import time\ntime.sleep(5)\n")

    global_portal_session.target_pid = None
    monkeypatch.setattr(global_portal_session, "_initialized", True)
    monkeypatch.setattr(global_portal_session, "pipewire_node_id", 99999)

    first = launch_app(target=str(script))
    pid1 = first["pid"]
    assert first["status"] == "launched"
    assert global_portal_session.target_pid == pid1
    assert "interactive_elements" in first

    second = launch_app(target=str(script), restart=True)
    pid2 = second["pid"]
    try:
        assert second["status"] == "restarted"
        assert second["old_pid"] == pid1
        assert pid2 != pid1
        assert global_portal_session.target_pid == pid2
        assert "interactive_elements" in second
    finally:
        try:
            terminate(pid2)
        except Exception:
            pass
        global_portal_session.target_pid = None


def test_launch_app_live_mode_launches_before_portal_prompt(monkeypatch):
    from wayland_computer_use_mcp.config import Config, get_config, set_config
    from wayland_computer_use_mcp.portal import global_portal_session
    from wayland_computer_use_mcp.tools import process_tools

    prev_cfg = get_config()
    call_order: list[str] = []
    terminated: list[int] = []

    try:
        set_config(Config(display_mode="live", mock_mode=False))
        monkeypatch.setattr(global_portal_session, "is_active", lambda: False)
        monkeypatch.setattr(process_tools, "ensure_portal_dialogs_above", lambda: True)
        monkeypatch.setattr(
            process_tools, "launch", lambda t, a, c: call_order.append("launch") or 555
        )
        monkeypatch.setattr(
            process_tools,
            "_ensure_session_initialized",
            lambda: call_order.append("portal"),
        )
        monkeypatch.setattr(process_tools, "_bind_focus_and_collect_elements", lambda pid: [])

        res = launch_app("examples/test_gui_app.py")
        assert res["pid"] == 555
        assert call_order == ["launch", "portal"]

        # Verify spawned app is cleaned up if user cancels the portal prompt
        call_order.clear()
        monkeypatch.setattr(
            process_tools,
            "_ensure_session_initialized",
            lambda: (_ for _ in ()).throw(RuntimeError("cancelled")),
        )
        monkeypatch.setattr(process_tools, "terminate", lambda pid: terminated.append(pid))
        with pytest.raises(RuntimeError, match="cancelled"):
            launch_app("examples/test_gui_app.py")
        assert terminated == [555]
    finally:
        set_config(prev_cfg)
        global_portal_session.target_pid = None


def test_capture_window_frame_in_memory_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("WAYLAND_MCP_SAVE_FRAMES_DIR", str(tmp_path))
    res = capture_window_frame()
    assert res.file_path == ""
    assert not (tmp_path / "latest_capture.png").exists()

    res_saved = capture_window_frame(save_artifact=True)
    assert res_saved.file_path == str((tmp_path / "latest_capture.png").resolve())
    assert (tmp_path / "latest_capture.png").exists()


def test_batch_actions_supports_label_and_text_actions():
    tree = {
        "role": "application",
        "name": "BatchDemo",
        "bounds": [0, 0, 800, 600],
        "children": [
            {
                "role": "push button",
                "name": "Save Changes",
                "states": ["visible", "enabled"],
                "bounds": [20, 20, 120, 40],
                "children": [],
            },
            {
                "role": "entry",
                "name": "Title Field",
                "states": ["visible", "enabled"],
                "bounds": [20, 80, 200, 30],
                "children": [],
            },
        ],
    }
    flatten_tree(tree, pid=5555)
    result = batch_actions(
        actions=[
            {"action": "click", "target": "Save Changes"},
            {"action": "type", "target": "Title Field", "text": "Hello"},
            {"action": "double_click", "x": 40, "y": 40},
            {"action": "clear", "target": "Title Field"},
        ],
        pid=5555,
    )
    assert result["status"] == "success"
    assert result["executed_steps_count"] == 4


def test_artifacts_respects_project_markers_and_env(tmp_path, monkeypatch):
    monkeypatch.delenv("WAYLAND_MCP_SAVE_FRAMES_DIR", raising=False)
    monkeypatch.delenv("WAYLAND_MCP_ARTIFACTS_DIR", raising=False)

    # 1. Explicit WAYLAND_MCP_PROJECT_DIR creates .agents/artifacts inside user's project
    project = tmp_path / "rust_app"
    project.mkdir()
    monkeypatch.setenv("WAYLAND_MCP_PROJECT_DIR", str(project))
    assert get_artifacts_dir("screenshots") == project / ".agents" / "artifacts" / "screenshots"

    # 2. CWD with Cargo.toml / package.json / .git discovers project root upward
    monkeypatch.delenv("WAYLAND_MCP_PROJECT_DIR", raising=False)
    web_proj = tmp_path / "web_app"
    nested = web_proj / "src" / "components"
    nested.mkdir(parents=True)
    (web_proj / "package.json").write_text('{"name":"demo"}\n')
    monkeypatch.chdir(nested)
    assert get_artifacts_dir("presets") == web_proj / ".agents" / "artifacts" / "presets"


def test_input_tools_always_return_interaction_result(monkeypatch):
    from wayland_computer_use_mcp.config import get_config
    from wayland_computer_use_mcp.delta import InteractionResult
    from wayland_computer_use_mcp.portal import global_portal_session
    from wayland_computer_use_mcp.server import click, hover

    cfg = get_config()
    monkeypatch.setattr(cfg, "source_type", 1)
    monkeypatch.setattr(global_portal_session, "target_pid", None)

    click_res = click(100, 120)
    assert isinstance(click_res, InteractionResult)
    assert click_res["status"] == "success"
    assert "ui_changes" in click_res

    hover_res = hover(200, 220)
    assert isinstance(hover_res, InteractionResult)
    assert hover_res["status"] == "success"


def test_universal_desktop_installer_targets(tmp_path, monkeypatch):
    from wayland_computer_use_mcp.installer import install_desktop_app, remove_desktop_app

    mock_apps_dir = tmp_path / "applications"
    mock_icons_dir = tmp_path / "icons"
    monkeypatch.setattr("wayland_computer_use_mcp.installer.APPLICATIONS_DIR", mock_apps_dir)
    monkeypatch.setattr("wayland_computer_use_mcp.installer.ICONS_DIR", mock_icons_dir)
    monkeypatch.setattr(
        "wayland_computer_use_mcp.installer.subprocess.run",
        lambda *a, **kw: MagicMock(returncode=0),
    )

    # 1. Shell script with extra args
    sh_script = tmp_path / "launch_studio.sh"
    sh_script.write_text("#!/bin/bash\necho hi\n")
    res_sh = install_desktop_app(
        app_id="my-studio",
        name="My Studio",
        exec_path=str(sh_script),
        args=["--fullscreen"],
        terminal=True,
        categories="Utility;Development;",
    )
    entry_sh = (mock_apps_dir / "my-studio.desktop").read_text()
    assert res_sh["status"] == "installed"
    assert "bash" in entry_sh and "--fullscreen" in entry_sh
    assert "Terminal=true" in entry_sh
    assert "Categories=Utility;Development;" in entry_sh
    assert remove_desktop_app("my-studio")["status"] == "uninstalled"

    # 2. URL & Flatpak targets
    res_url = install_desktop_app(
        app_id="docs-site",
        name="Docs Site",
        exec_path="https://example.org/docs",
    )
    assert res_url["status"] == "installed"
    url_entry = (mock_apps_dir / "docs-site.desktop").read_text()
    assert "xdg-open https://example.org/docs" in url_entry
    assert remove_desktop_app("docs-site")["status"] == "uninstalled"

    res_fp = install_desktop_app(
        app_id="calculator",
        name="Calculator",
        exec_path="flatpak:org.gnome.Calculator",
    )
    assert res_fp["status"] == "installed"
    fp_entry = (mock_apps_dir / "calculator.desktop").read_text()
    assert "flatpak run org.gnome.Calculator" in fp_entry
    assert remove_desktop_app("calculator")["status"] == "uninstalled"


def test_atspi_pool_lifecycle(monkeypatch):
    from wayland_computer_use_mcp.a11y import client as a11y_client

    a11y_client.close_atspi_pool()
    assert a11y_client.run_atspi_sync(_async_identity, "pooled-ok") == "pooled-ok"
    a11y_client.close_atspi_pool()


async def _async_identity(value: str) -> str:
    return value


def test_level1_atspi_never_moves_cursor_even_offscreen(monkeypatch):
    from wayland_computer_use_mcp.a11y import flatten_tree, invoke_node_action
    from wayland_computer_use_mcp.portal import global_portal_session
    from wayland_computer_use_mcp.security import global_clamper

    orig_dims = global_clamper.dimensions
    global_clamper.set_bounds(800, 600)
    try:
        tree = {
            "role": "application",
            "name": "OffscreenDemo",
            "bounds": [0, 0, 800, 600],
            "children": [
                {
                    "role": "push button",
                    "name": "Far Button",
                    "states": ["visible", "enabled"],
                    "bounds": [50, 2000, 120, 40],  # Below viewport height 600
                    "bus_name": ":1.99",
                    "path": "/org/a11y/atspi/accessible/42",
                    "children": [],
                }
            ],
        }
        flatten_tree(tree, pid=4242)

        hover_calls = []
        scroll_calls = []
        click_calls = []
        monkeypatch.setattr(
            global_portal_session, "dispatch_hover", lambda *a, **k: hover_calls.append(a)
        )
        monkeypatch.setattr(
            global_portal_session, "dispatch_scroll", lambda *a, **k: scroll_calls.append(a)
        )
        monkeypatch.setattr(
            global_portal_session, "dispatch_click", lambda *a, **k: click_calls.append(a)
        )
        monkeypatch.setattr(
            "wayland_computer_use_mcp.a11y.actions.do_accessible_action", lambda *a, **k: True
        )

        res = invoke_node_action(4242, "b1", action="click")
        assert "via AT-SPI" in res
        assert hover_calls == []
        assert scroll_calls == []
        assert click_calls == []
    finally:
        global_clamper.set_bounds(*orig_dims)


def test_virtual_compositor_cmd_cross_de_and_flags(monkeypatch):
    from wayland_computer_use_mcp.config import (
        Config,
        detect_virtual_compositor_cmd,
    )

    # Headed/headless mode prefers kwin_wayland (direct isolated EIS + ScreenShot2/spectacle),
    # then falls back to sway / weston
    monkeypatch.setattr(
        "shutil.which",
        lambda name: f"/usr/bin/{name}" if name in {"weston", "kwin_wayland"} else None,
    )
    assert detect_virtual_compositor_cmd(
        headed=True, socket_name="wl-test", host_wayland_display="wayland-0"
    ) == (
        "kwin_wayland --wayland-display wayland-0 --xwayland --no-lockscreen "
        "--no-global-shortcuts --width 1280 --height 800 --socket wl-test"
    )
    assert detect_virtual_compositor_cmd(headed=False, socket_name="wl-test") == (
        "kwin_wayland --virtual --socket wl-test --no-lockscreen --no-global-shortcuts "
        "--width 1280 --height 800"
    )

    monkeypatch.setattr(
        "shutil.which", lambda name: "/usr/bin/weston" if name == "weston" else None
    )
    assert detect_virtual_compositor_cmd(headed=True, socket_name="wl-test") == (
        "weston --socket=wl-test --width=1280 --height=800"
    )
    assert detect_virtual_compositor_cmd(headed=False, socket_name="wl-test") == (
        "weston --backend=headless-backend.so --socket=wl-test --width=1280 --height=800"
    )

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/sway" if name == "sway" else None)
    assert detect_virtual_compositor_cmd(headed=True, socket_name="wl-test") == "sway"

    # CLI flags & env var
    cfg = Config()
    cfg.apply_cli_args(["--virtual-window"])
    assert cfg.display_mode == "virtual"
    assert cfg.virtual_headed is True

    monkeypatch.setenv("WAYLAND_MCP_VIRTUAL_HEADED", "1")
    cfg_env = Config.load()
    assert cfg_env.virtual_headed is True


def test_role_disambiguation_and_batch_failure_screenshot(monkeypatch, tmp_path):
    from PIL import Image

    from wayland_computer_use_mcp.a11y import flatten_tree
    from wayland_computer_use_mcp.portal import global_portal_session
    from wayland_computer_use_mcp.tools.navigation_tools import (
        batch_actions,
        interact_with_node,
    )

    monkeypatch.setattr(
        "wayland_computer_use_mcp.portal.screencast.get_artifacts_dir",
        lambda *a, **k: str(tmp_path),
    )

    tree = {
        "role": "application",
        "name": "DuplicateNamesApp",
        "bounds": [0, 0, 800, 600],
        "children": [
            {
                "role": "label",
                "name": "Save",
                "states": ["visible", "showing"],
                "bounds": [10, 10, 80, 20],
                "bus_name": ":1.5",
                "path": "/node/lbl",
                "children": [],
            },
            {
                "role": "push button",
                "name": "Save",
                "states": ["visible", "showing", "enabled"],
                "bounds": [10, 50, 100, 32],
                "bus_name": ":1.5",
                "path": "/node/btn",
                "children": [],
            },
        ],
    }
    flat = flatten_tree(tree, pid=9191)
    assert flat[1]["states"] == ["visible", "enabled", "showing"]

    monkeypatch.setattr(
        "wayland_computer_use_mcp.tools.navigation_tools.get_application_tree",
        lambda pid: {**tree, "interactive_elements": flat},
    )
    monkeypatch.setattr(
        "wayland_computer_use_mcp.delta.snapshot_interactive_state",
        lambda pid: {},
    )
    dispatched = []
    monkeypatch.setattr(
        "wayland_computer_use_mcp.tools.navigation_tools.invoke_node_action",
        lambda pid, node_id, action="click", **kw: (
            dispatched.append(node_id) or f"clicked {node_id}"
        ),
    )

    out = interact_with_node(target="Save", role="push button", action="click", pid=9191)
    assert out["status"] == "success"
    assert dispatched == ["b1"]

    # Ambiguous without role would pick label t1 first; with role="button" batch picks b1
    dispatched.clear()
    monkeypatch.setattr(global_portal_session, "is_active", lambda: True)
    monkeypatch.setattr(type(global_portal_session), "is_mock", property(lambda self: False))
    monkeypatch.setattr(
        "wayland_computer_use_mcp.tools.navigation_tools.is_responsive",
        lambda pid: True,
    )

    def _fake_capture(save_to_disk=False, filename=None):
        img = Image.new("RGB", (64, 48), "blue")
        if save_to_disk:
            p = tmp_path / (filename or "frame.png")
            img.save(p, format="PNG")
            global_portal_session._screencast.last_saved_frame_path = str(p)
        return img

    monkeypatch.setattr(global_portal_session, "capture_frame", _fake_capture)

    def _fail_second(pid, node_id, action="click", **kw):
        dispatched.append(node_id)
        if node_id == "b1":
            raise RuntimeError("boom on button")
        return "ok"

    monkeypatch.setattr(
        "wayland_computer_use_mcp.tools.navigation_tools.invoke_node_action",
        _fail_second,
    )
    batch_res = batch_actions(
        [{"target": "Save", "role": "button", "action": "click"}],
        pid=9191,
    )
    assert dispatched == ["b1"]
    assert batch_res["status"] == "failed"
    assert batch_res["failure_screenshot"].endswith("batch_failure.png")
    assert (tmp_path / "batch_failure.png").exists()


def test_delta_state_tracking_and_event_settle(monkeypatch):
    from wayland_computer_use_mcp.delta import compute_ui_delta, wrap_with_delta

    before = {
        "b1": {
            "id": "b1",
            "role": "push button",
            "name": "Filter",
            "states": {"showing", "enabled"},
        },
        "c1": {"id": "c1", "role": "check box", "name": "Done", "states": {"showing", "enabled"}},
        "t1": {"id": "t1", "role": "label", "name": "Pending Task", "states": {"showing"}},
    }
    after = {
        "b1": {
            "id": "b1",
            "role": "push button",
            "name": "Filter",
            "states": {"showing", "enabled"},
        },
        "c1": {
            "id": "c1",
            "role": "check box",
            "name": "Done",
            "states": {"showing", "enabled", "checked"},
        },
        "t1": {"id": "t1", "role": "label", "name": "Pending Task", "states": set()},
    }
    diff = compute_ui_delta(before, after)
    assert any(h.get("id") == "t1" for h in diff["hidden"])
    assert any(
        m.get("id") == "c1" and "+checked" in m.get("description", "") for m in diff["modified"]
    )

    # wrap_with_delta uses AT-SPI wait_for_settled instead of unconditional sleep when connected
    waited = []
    monkeypatch.setattr(
        "wayland_computer_use_mcp.a11y.events.global_event_listener.is_connected",
        lambda: True,
    )
    monkeypatch.setattr(
        "wayland_computer_use_mcp.a11y.events.global_event_listener.wait_for_settled",
        lambda timeout_ms=250, quiet_period_ms=20, pid=None: (
            waited.append((timeout_ms, quiet_period_ms, pid)) or True
        ),
    )
    monkeypatch.setattr(
        "wayland_computer_use_mcp.delta.snapshot_interactive_state",
        lambda pid: {},
    )
    res = wrap_with_delta(1234, lambda: "ok", settle_ms=100)
    assert res["status"] == "success"
    assert waited == [(100, 20, 1234)]


def test_portal_clipboard_and_typing_fallback(monkeypatch):
    import os

    from dbus_fast import MessageType

    from wayland_computer_use_mcp import clipboard
    from wayland_computer_use_mcp.portal import global_portal_session
    from wayland_computer_use_mcp.portal.input import InputDispatcher
    from wayland_computer_use_mcp.portal.remotedesktop import (
        AsyncLoopThread,
        RemoteDesktopClient,
    )

    loop_thread = AsyncLoopThread()
    rd = RemoteDesktopClient(loop_thread)
    try:
        rd.session_handle = "/org/freedesktop/portal/desktop/session/1"
        rd.clipboard_enabled = True

        class _FakeReply:
            def __init__(self, fds=None):
                self.message_type = MessageType.METHOD_RETURN
                self.unix_fds = fds or []

        r_fd, w_fd = os.pipe()
        os.write(w_fd, b"hello portal clipboard")
        os.close(w_fd)

        class _FakeBus:
            async def call(self, msg):
                if msg.member == "SetSelection":
                    return _FakeReply()
                if msg.member == "SelectionRead":
                    return _FakeReply(fds=[r_fd])
                return _FakeReply()

        rd.bus = _FakeBus()
        assert rd.write_clipboard_text("test-clip") is True
        assert rd.read_clipboard_text() == "hello portal clipboard"

        # clipboard module delegates to portal first when enabled
        monkeypatch.setattr(global_portal_session, "_rd_client", rd)
        assert clipboard.write_system_clipboard("abc") is True

        # InputDispatcher falls back to char typing if clipboard write fails
        typed_chars = []

        class _FakeEI:
            def type_char(self, ch: str) -> bool:
                typed_chars.append(ch)
                return True

        dispatcher = InputDispatcher(
            rd,
            verify_preconditions_fn=lambda: None,
            get_offsets_fn=lambda: (0, 0),
            is_mock_fn=lambda: False,
            get_ei_client_fn=lambda: _FakeEI(),
        )
        monkeypatch.setattr(
            "wayland_computer_use_mcp.portal.input.write_system_clipboard", lambda text: False
        )
        long_text = "x" * 35
        res = dispatcher.dispatch_type_text(long_text)
        assert "Typed 35 characters" in res
        assert "".join(typed_chars) == long_text
    finally:
        loop_thread.stop()


def test_screencast_optional_gst_cli_and_portal_screenshot(monkeypatch, tmp_path):
    import subprocess

    from PIL import Image

    from wayland_computer_use_mcp.portal.screencast import (
        ScreenCastPipeline,
        _capture_portal_screenshot_frame,
    )

    pipe = ScreenCastPipeline()
    pipe._node_id = 77
    assert pipe._node_id == 77

    # Mock gst-launch-1.0 CLI capture path
    monkeypatch.setattr(
        "shutil.which",
        lambda name: "/usr/bin/gst-launch-1.0" if name == "gst-launch-1.0" else None,
    )

    def _fake_gst_run(cmd, **kwargs):
        out_arg = next(a for a in cmd if a.startswith("location="))
        out_path = out_arg.split("=", 1)[1]
        Image.new("RGB", (32, 24), "green").save(out_path, format="PNG")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr("subprocess.run", _fake_gst_run)
    cli_img = pipe._pull_gst_cli_frame()
    assert cli_img is not None
    assert cli_img.size == (32, 24)

    # _capture_portal_screenshot_frame gracefully returns None if portal unavailable/mocked
    shot_file = tmp_path / "shot.png"
    Image.new("RGB", (20, 10), "red").save(shot_file, format="PNG")

    monkeypatch.setattr(
        "dbus_fast.aio.MessageBus.connect",
        lambda self: _raise_mock_error(),
    )
    assert _capture_portal_screenshot_frame() is None


def test_virtual_session_manager_isolates_and_restores_env(monkeypatch, tmp_path):
    """VirtualSessionManager isolates XDG/D-Bus/WAYLAND env and restores cleanly on stop."""
    import os
    from types import SimpleNamespace

    from wayland_computer_use_mcp.config import Config
    from wayland_computer_use_mcp.portal.virtual import VirtualSessionManager

    host_xdg = tmp_path / "host_xdg"
    host_xdg.mkdir()
    (host_xdg / "wayland-0").write_text("")

    monkeypatch.setenv("XDG_RUNTIME_DIR", str(host_xdg))
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/tmp/host-bus")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("AT_SPI_BUS_ADDRESS", "unix:path=/tmp/host-atspi")

    spawned: list[list[str]] = []

    class FakeProc:
        def __init__(self, stdout_line: str = "") -> None:
            self.pid = 999999
            self.stdout = SimpleNamespace(readline=lambda: stdout_line)

        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    def fake_popen(cmd, env=None, **kwargs):
        spawned.append(list(cmd))
        if cmd[0] == "dbus-daemon":
            assert "AT_SPI_BUS_ADDRESS" not in env
            return FakeProc("unix:path=/tmp/isolated-dbus-test\n")
        # Nested compositor creates socket file immediately
        sock_dir = env["XDG_RUNTIME_DIR"]
        open(os.path.join(sock_dir, "wayland-mcp-virtual"), "w").close()
        return FakeProc()

    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("subprocess.Popen", fake_popen)

    mgr = VirtualSessionManager()
    cfg = Config(display_mode="virtual", virtual_headed=True)
    mgr.ensure_started(cfg)

    try:
        assert mgr.is_running
        assert os.environ["XDG_RUNTIME_DIR"] != str(host_xdg)
        assert os.environ["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/tmp/isolated-dbus-test"
        assert os.environ["WAYLAND_DISPLAY"] == "wayland-mcp-virtual"
        assert "AT_SPI_BUS_ADDRESS" not in os.environ
        assert os.path.islink(os.path.join(mgr.runtime_dir or "", "wayland-0"))
        assert len(spawned) == 2
    finally:
        mgr.stop()

    assert os.environ["XDG_RUNTIME_DIR"] == str(host_xdg)
    assert os.environ["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/tmp/host-bus"
    assert os.environ["WAYLAND_DISPLAY"] == "wayland-0"
    assert os.environ["AT_SPI_BUS_ADDRESS"] == "unix:path=/tmp/host-atspi"


async def _raise_mock_error():
    raise RuntimeError("no dbus in unit test")


def test_cleanup_stale_virtual_dirs_prunes_dead_owner(monkeypatch, tmp_path):
    """Stale virtual runtime directories whose owner PID exited should be cleaned up."""
    import os

    from wayland_computer_use_mcp.portal.virtual import cleanup_stale_virtual_dirs

    stale_dir = tmp_path / "wayland_mcp_virtual_dead"
    stale_dir.mkdir()
    (stale_dir / ".mcp_pid").write_text("999999", encoding="utf-8")

    live_dir = tmp_path / "wayland_mcp_virtual_live"
    live_dir.mkdir()
    (live_dir / ".mcp_pid").write_text(str(os.getpid()), encoding="utf-8")

    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))

    def fake_kill(pid, sig):
        if pid == 999999 and sig == 0:
            raise ProcessLookupError()

    monkeypatch.setattr("os.kill", fake_kill)

    cleanup_stale_virtual_dirs()
    assert not stale_dir.exists()
    assert live_dir.exists()


def test_terminate_app_closes_idle_virtual_session(monkeypatch):
    """terminate_app tears down virtual window when no managed apps remain."""
    from wayland_computer_use_mcp.portal import global_portal_session
    from wayland_computer_use_mcp.tools.process_tools import terminate_app

    closed_calls = []
    remaining_apps = [[{"pid": 222}], []]

    monkeypatch.setattr("wayland_computer_use_mcp.tools.process_tools.terminate", lambda pid: True)
    monkeypatch.setattr(
        "wayland_computer_use_mcp.tools.process_tools.list_active_processes",
        lambda: remaining_apps.pop(0),
    )
    monkeypatch.setattr(
        global_portal_session,
        "close_virtual_if_idle",
        lambda: closed_calls.append(True) or True,
    )

    global_portal_session.target_pid = 111
    res1 = terminate_app(pid=111)
    assert res1["virtual_session_closed"] is False
    assert global_portal_session.target_pid == 222
    assert closed_calls == []

    res2 = terminate_app(pid=222)
    assert res2["virtual_session_closed"] is True
    assert global_portal_session.target_pid is None
    assert len(closed_calls) == 1
