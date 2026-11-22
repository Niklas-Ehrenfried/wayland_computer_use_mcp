"""Contract and unit tests for Core, Blackbox, Wayland wire clients, and VirtualSessionManager."""

from __future__ import annotations

import array
import os
import socket
import struct
import threading
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from PIL import Image

from wayland_computer_use_mcp import process as proc_mod
from wayland_computer_use_mcp.blackbox import (
    BlackboxDriver,
    KWinNestedAdapter,
    WestonNestedAdapter,
    WlrootsNestedAdapter,
    WlrVirtualInputClient,
    capture_wlr_screencopy,
    decode_shm_frame,
    select_blackbox_adapter,
)
from wayland_computer_use_mcp.blackbox.wlr_wire import (
    WL_SHM_FORMAT_ABGR8888,
    WL_SHM_FORMAT_ARGB8888,
    WL_SHM_FORMAT_XBGR8888,
    WL_SHM_FORMAT_XRGB8888,
    control_wlr_toplevel,
)
from wayland_computer_use_mcp.compat import HostPassthroughDriver
from wayland_computer_use_mcp.config import Config, get_config, set_config
from wayland_computer_use_mcp.core import (
    InputSink,
    MockInputSink,
    MockSessionDriver,
    SessionContext,
    SessionDriver,
    get_active_context,
)
from wayland_computer_use_mcp.core.wayland_wire import build_msg, pack_string, unpack_string
from wayland_computer_use_mcp.libei import BTN_LEFT, KEY_ENTER, EIClient
from wayland_computer_use_mcp.portal.remotedesktop import AsyncLoopThread
from wayland_computer_use_mcp.portal.session import PortalSession
from wayland_computer_use_mcp.portal.virtual import (
    VirtualSessionManager,
    cleanup_stale_virtual_dirs,
)


def _parse_wayland_packets(buf: bytearray) -> list[tuple[int, int, bytes]]:
    packets: list[tuple[int, int, bytes]] = []
    while len(buf) >= 8:
        oid, sz_op = struct.unpack_from("<II", buf, 0)
        sz = sz_op >> 16
        op = sz_op & 0xFFFF
        if sz < 8 or len(buf) < sz:
            break
        packets.append((oid, op, bytes(buf[8:sz])))
        del buf[:sz]
    return packets


def test_session_context_env_isolation_and_socket_resolution(monkeypatch, tmp_path):
    monkeypatch.setenv("AT_SPI_BUS_ADDRESS", "unix:path=/host/atspi")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-host")

    ctx = SessionContext(
        mode="blackbox",
        xdg_runtime_dir=str(tmp_path),
        dbus_address="unix:path=/tmp/private-dbus",
        wayland_display="wayland-mcp-virtual",
        atspi_bus_address=None,
        headed=True,
        is_isolated=True,
    )
    env = ctx.build_subprocess_env()
    assert env["XDG_RUNTIME_DIR"] == str(tmp_path)
    assert env["DBUS_SESSION_BUS_ADDRESS"] == "unix:path=/tmp/private-dbus"
    assert env["WAYLAND_DISPLAY"] == "wayland-mcp-virtual"
    assert "AT_SPI_BUS_ADDRESS" not in env
    assert ctx.wayland_socket_path() == str(tmp_path / "wayland-mcp-virtual")
    assert os.environ["AT_SPI_BUS_ADDRESS"] == "unix:path=/host/atspi"
    assert os.environ["WAYLAND_DISPLAY"] == "wayland-host"


def test_protocol_conformance_drivers_and_sinks():
    async_thread = AsyncLoopThread()
    try:
        blackbox = BlackboxDriver()
        host = HostPassthroughDriver(async_thread)
        mock_driver = MockSessionDriver()
        assert isinstance(blackbox, SessionDriver) and isinstance(host, SessionDriver)
        assert isinstance(mock_driver, SessionDriver)
        assert isinstance(EIClient(fd=None), InputSink)
        assert isinstance(WlrVirtualInputClient(sock_path=None), InputSink)
        assert isinstance(MockInputSink(), InputSink)
        assert blackbox.is_isolated() is True and host.is_isolated() is False
        assert blackbox.ensure_dialogs_above() is False
    finally:
        async_thread.stop()


def test_adapter_selection_and_commands(monkeypatch):
    assert isinstance(select_blackbox_adapter("kwin_wayland --virtual"), KWinNestedAdapter)
    assert isinstance(select_blackbox_adapter("sway"), WlrootsNestedAdapter)
    assert isinstance(select_blackbox_adapter("labwc"), WlrootsNestedAdapter)
    assert isinstance(select_blackbox_adapter("weston --headless"), WestonNestedAdapter)

    monkeypatch.setattr(
        "shutil.which", lambda n: "/usr/bin/kwin_wayland" if n == "kwin_wayland" else None
    )
    kwin = KWinNestedAdapter()
    assert "--wayland-display wayland-0" in kwin.build_command(True, "wl-test", "wayland-0")
    assert "--virtual" in kwin.build_command(False, "wl-test", None)

    monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/labwc" if n == "labwc" else None)
    assert (
        WlrootsNestedAdapter().build_command(True, "x", "wayland-0", config_dir="/tmp/cfg")
        == "labwc -C /tmp/cfg"
    )
    monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/sway" if n == "sway" else None)
    assert WlrootsNestedAdapter().build_command(True, "x", "wayland-0") == "sway -c /dev/null"

    monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/weston" if n == "weston" else None)
    weston = WestonNestedAdapter()
    assert "--backend=wayland-backend.so" in weston.build_command(True, "w1", "wayland-0")
    assert "--backend=headless-backend.so" in weston.build_command(False, "w1", None)


def test_find_created_wayland_socket(tmp_path):
    (tmp_path / "wayland-1").symlink_to(tmp_path / "wayland-0-real")
    (tmp_path / "wayland-0.lock").write_text("", encoding="utf-8")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(tmp_path / "wayland-0"))
    try:
        assert (
            VirtualSessionManager._find_created_wayland_socket(str(tmp_path), exclude="wayland-1")
            == "wayland-0"
        )
    finally:
        sock.close()


def test_decode_shm_frame_formats_and_y_invert():
    xrgb_buf = bytes([0x56, 0x34, 0x12, 0xFF])
    assert decode_shm_frame(xrgb_buf, WL_SHM_FORMAT_XRGB8888, 1, 1, 4).getpixel((0, 0)) == (
        0x12,
        0x34,
        0x56,
    )
    assert decode_shm_frame(xrgb_buf, WL_SHM_FORMAT_ARGB8888, 1, 1, 4).getpixel((0, 0)) == (
        0x12,
        0x34,
        0x56,
    )

    xbgr_buf = bytes([0x12, 0x34, 0x56, 0xFF])
    assert decode_shm_frame(xbgr_buf, WL_SHM_FORMAT_XBGR8888, 1, 1, 4).getpixel((0, 0)) == (
        0x12,
        0x34,
        0x56,
    )
    assert decode_shm_frame(xbgr_buf, WL_SHM_FORMAT_ABGR8888, 1, 1, 4).getpixel((0, 0)) == (
        0x12,
        0x34,
        0x56,
    )

    flipped = decode_shm_frame(
        bytes([0, 0, 0xFF, 0xFF, 0xFF, 0, 0, 0xFF]), WL_SHM_FORMAT_XRGB8888, 1, 2, 4, y_invert=True
    )
    assert flipped.getpixel((0, 0)) == (0, 0, 255) and flipped.getpixel((0, 1)) == (255, 0, 0)
    assert decode_shm_frame(b"\x00\x00", WL_SHM_FORMAT_XRGB8888, 1, 1, 4) is None


def test_wlr_screencopy_roundtrip_over_mock_unix_socket(tmp_path):
    sock_path = str(tmp_path / "wayland-wlr-cap")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(1)
    srv.settimeout(3.0)

    def _server_worker() -> None:
        conn, _ = srv.accept()
        conn.settimeout(3.0)
        buf = bytearray()
        received_fds: list[int] = []
        pool_fds: dict[int, int] = {}
        try:
            frame_id = shm_id = mgr_id = 0
            while True:
                data, ancdata, _, _ = conn.recvmsg(65536, socket.CMSG_SPACE(16 * 4))
                for lvl, ctype, cdata in ancdata:
                    if lvl == socket.SOL_SOCKET and ctype == socket.SCM_RIGHTS:
                        received_fds.extend(array.array("i", cdata))
                if not data and not ancdata:
                    break
                buf.extend(data)
                for oid, op, body in _parse_wayland_packets(buf):
                    if oid == 1 and op == 1:
                        (reg_id,) = struct.unpack_from("<I", body, 0)
                        conn.sendall(
                            build_msg(
                                reg_id,
                                0,
                                struct.pack("<I", 1) + pack_string("wl_shm") + struct.pack("<I", 1),
                            )
                            + build_msg(
                                reg_id,
                                0,
                                struct.pack("<I", 2)
                                + pack_string("wl_output")
                                + struct.pack("<I", 1),
                            )
                            + build_msg(
                                reg_id,
                                0,
                                struct.pack("<I", 3)
                                + pack_string("zwlr_screencopy_manager_v1")
                                + struct.pack("<I", 3),
                            )
                        )
                    elif oid == 1 and op == 0:
                        (cb_id,) = struct.unpack_from("<I", body, 0)
                        conn.sendall(build_msg(cb_id, 0, struct.pack("<I", 1)))
                    elif oid == 2 and op == 0:
                        iface, off = unpack_string(body, 4)
                        _, new_id = struct.unpack_from("<II", body, off)
                        if iface == "wl_shm":
                            shm_id = new_id
                        elif iface == "zwlr_screencopy_manager_v1":
                            mgr_id = new_id
                    elif oid == mgr_id and op == 0:
                        frame_id = struct.unpack_from("<I", body, 0)[0]
                        conn.sendall(
                            build_msg(
                                frame_id, 0, struct.pack("<IIII", WL_SHM_FORMAT_XRGB8888, 2, 1, 8)
                            )
                            + build_msg(frame_id, 6)
                        )
                    elif oid == shm_id and op == 0:
                        pool_id, _ = struct.unpack_from("<Ii", body, 0)
                        pool_fds[pool_id] = received_fds.pop(0)
                    elif oid == frame_id and op == 0:
                        fd = next(iter(pool_fds.values()))
                        os.pwrite(fd, bytes([30, 20, 10, 0xFF, 50, 100, 200, 0xFF]), 0)
                        conn.sendall(
                            build_msg(frame_id, 1, struct.pack("<I", 0))
                            + build_msg(frame_id, 2, struct.pack("<III", 0, 0, 0))
                        )
                        return
        finally:
            for fd in [*received_fds, *pool_fds.values()]:
                try:
                    os.close(fd)
                except OSError:
                    pass
            conn.close()

    t = threading.Thread(target=_server_worker, daemon=True)
    t.start()
    try:
        img = capture_wlr_screencopy(sock_path, timeout=2.0)
        assert isinstance(img, Image.Image) and img.size == (2, 1)
        assert img.getpixel((0, 0)) == (10, 20, 30) and img.getpixel((1, 0)) == (200, 100, 50)
    finally:
        t.join(timeout=2.0)
        srv.close()


def test_wlr_virtual_input_wire_protocol(tmp_path):
    sock_path = str(tmp_path / "wayland-wlr-input")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(1)
    srv.settimeout(3.0)

    captured_packets: list[tuple[int, int, bytes]] = []
    captured_fd_sizes: list[int] = []
    stop_ev = threading.Event()

    def _server_worker() -> None:
        conn, _ = srv.accept()
        conn.settimeout(0.2)
        buf = bytearray()
        try:
            while not stop_ev.is_set():
                try:
                    data, ancdata, _, _ = conn.recvmsg(65536, socket.CMSG_SPACE(16 * 4))
                except TimeoutError:
                    continue
                for lvl, ctype, cdata in ancdata:
                    if lvl == socket.SOL_SOCKET and ctype == socket.SCM_RIGHTS:
                        for fd in array.array("i", cdata):
                            captured_fd_sizes.append(os.fstat(fd).st_size)
                            os.close(fd)
                if not data and not ancdata:
                    break
                buf.extend(data)
                for oid, op, body in _parse_wayland_packets(buf):
                    captured_packets.append((oid, op, body))
                    if oid == 1 and op == 1:
                        (reg_id,) = struct.unpack_from("<I", body, 0)
                        conn.sendall(
                            build_msg(
                                reg_id,
                                0,
                                struct.pack("<I", 1)
                                + pack_string("wl_seat")
                                + struct.pack("<I", 1),
                            )
                            + build_msg(
                                reg_id,
                                0,
                                struct.pack("<I", 2)
                                + pack_string("zwlr_virtual_pointer_manager_v1")
                                + struct.pack("<I", 1),
                            )
                            + build_msg(
                                reg_id,
                                0,
                                struct.pack("<I", 3)
                                + pack_string("zwp_virtual_keyboard_manager_v1")
                                + struct.pack("<I", 1),
                            )
                        )
                    elif oid == 1 and op == 0:
                        (cb_id,) = struct.unpack_from("<I", body, 0)
                        conn.sendall(build_msg(cb_id, 0, struct.pack("<I", 1)))
        finally:
            conn.close()

    t = threading.Thread(target=_server_worker, daemon=True)
    t.start()
    client = WlrVirtualInputClient(sock_path=sock_path, extent_w=1280, extent_h=800)
    try:
        assert (
            client.has_absolute_pointer and client.has_scroll and client.keyboard_device is not None
        )
        assert client.can_type_text("abc")
        assert client.pointer_motion_absolute(320, 240)
        assert client.button_click(BTN_LEFT) and client.scroll(0, 2) and client.type_char("a")
        client.key_combination([KEY_ENTER])

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not any(op == 1 for _, op, _ in captured_packets):
            time.sleep(0.02)
        time.sleep(0.05)

        vptr_ops = [(op, b) for oid, op, b in captured_packets if oid == client._vptr_id]
        vkbd_ops = [(op, b) for oid, op, b in captured_packets if oid == client._vkbd_id]
        motion_payloads = [b for op, b in vptr_ops if op == 1]
        assert motion_payloads and struct.unpack_from("<IIIII", motion_payloads[0], 0)[1:] == (
            320,
            240,
            1280,
            800,
        )
        assert captured_fd_sizes and captured_fd_sizes[0] > 0
        assert len([1 for op, _ in vkbd_ops if op == 1]) >= 4
    finally:
        stop_ev.set()
        client.close()
        t.join(timeout=2.0)
        srv.close()


def test_host_passthrough_lazy_portal_and_dialog_raise(monkeypatch):
    orig_cfg = get_config()
    set_config(Config(display_mode="live", mock_mode=False, lazy_portal=True))
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")

    session = PortalSession()
    try:
        negotiate_mock = AsyncMock(return_value=("sess/lazy", 77, 99, None))
        monkeypatch.setattr(session._host.rd_client, "negotiate", negotiate_mock)
        monkeypatch.setattr(session._host.screencast, "setup_pipeline", MagicMock())
        monkeypatch.setattr(
            session._host.screencast, "pull_frame", lambda: Image.new("RGB", (64, 64))
        )
        monkeypatch.setattr("wayland_computer_use_mcp.process.is_responsive", lambda _pid: True)
        monkeypatch.setattr(
            "wayland_computer_use_mcp.compositors.query_window_geometry",
            lambda **kw: (0, 0, 64, 64),
        )
        monkeypatch.setattr(
            "wayland_computer_use_mcp.compositors.ensure_portal_dialogs_above", lambda: False
        )

        session.ensure_initialized()
        assert session.is_active() is True and negotiate_mock.call_count == 0
        session.target_pid = 4321
        assert session.capture_frame().size == (64, 64) and negotiate_mock.call_count == 1
    finally:
        session.close()
        set_config(orig_cfg)

    from wayland_computer_use_mcp.tools import process_tools

    set_config(Config(display_mode="live", mock_mode=False, lazy_portal=False))
    called = {"dialogs": 0}
    fake_driver = MagicMock()
    fake_driver.ensure_dialogs_above.side_effect = lambda: (
        called.__setitem__("dialogs", called["dialogs"] + 1) or True
    )
    monkeypatch.setattr(process_tools.global_portal_session, "active_driver", lambda: fake_driver)
    monkeypatch.setattr(process_tools.global_portal_session, "is_active", lambda: True)
    monkeypatch.setattr(process_tools.global_portal_session, "target_pid", None)
    monkeypatch.setattr(process_tools, "launch", lambda t, a=None, c=None: 12345)
    monkeypatch.setattr(process_tools, "is_responsive", lambda pid: True)
    monkeypatch.setattr(process_tools, "_bind_focus_and_collect_elements", lambda pid: [])
    try:
        assert process_tools.launch_app("dummy.py")["pid"] == 12345 and called["dialogs"] == 1
    finally:
        set_config(orig_cfg)


def test_wlr_foreign_toplevel_control_and_blackbox_driver(monkeypatch, tmp_path):
    sock_path = str(tmp_path / "wayland-wlr-toplevel")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(4)

    recorded_actions: list[tuple[str, int, int, bytes]] = []
    stop_ev = threading.Event()

    def _server() -> None:
        while not stop_ev.is_set():
            srv.settimeout(0.1)
            try:
                conn, _ = srv.accept()
            except (TimeoutError, OSError):
                continue
            conn.setblocking(True)
            buf = bytearray()
            mgr_id = 0
            try:
                while not stop_ev.is_set():
                    data = conn.recv(4096)
                    if not data:
                        break
                    buf.extend(data)
                    for oid, op, body in _parse_wayland_packets(buf):
                        if oid == 1 and op == 1:
                            (reg_id,) = struct.unpack_from("<I", body, 0)
                            conn.sendall(
                                build_msg(
                                    reg_id,
                                    0,
                                    struct.pack("<I", 10)
                                    + pack_string("zwlr_foreign_toplevel_manager_v1")
                                    + struct.pack("<I", 3),
                                )
                                + build_msg(
                                    reg_id,
                                    0,
                                    struct.pack("<I", 11)
                                    + pack_string("wl_seat")
                                    + struct.pack("<I", 1),
                                )
                            )
                        elif oid == 2 and op == 0:
                            (gname,) = struct.unpack_from("<I", body, 0)
                            iface, off = unpack_string(body, 4)
                            if iface == "zwlr_foreign_toplevel_manager_v1" or gname == 10:
                                _, mgr_id = struct.unpack_from("<II", body, off)
                                conn.sendall(
                                    build_msg(mgr_id, 0, struct.pack("<I", 0xFF000001))
                                    + build_msg(0xFF000001, 0, pack_string("Old Closed"))
                                    + build_msg(0xFF000001, 6)
                                    + build_msg(mgr_id, 0, struct.pack("<I", 0xFF000002))
                                    + build_msg(0xFF000002, 0, pack_string("Labwc Target App"))
                                    + build_msg(0xFF000002, 1, pack_string("org.example.Target"))
                                    + build_msg(0xFF000002, 5)
                                )
                        elif oid == 1 and op == 0:
                            (cb_id,) = struct.unpack_from("<I", body, 0)
                            conn.sendall(build_msg(cb_id, 0, struct.pack("<I", 1)))
                        elif oid in (0xFF000001, 0xFF000002) or oid == mgr_id:
                            recorded_actions.append(("req", oid, op, body))
            finally:
                conn.close()

    t = threading.Thread(target=_server, daemon=True)
    t.start()
    try:
        assert control_wlr_toplevel(sock_path, "minimize", title="Labwc Target")
        assert control_wlr_toplevel(sock_path, "maximize", app_hint="Target")
        assert control_wlr_toplevel(sock_path, "restore")
        assert control_wlr_toplevel(sock_path, "focus", title="Labwc Target")
        assert control_wlr_toplevel(sock_path, "close", title="Labwc Target")
        assert control_wlr_toplevel(sock_path, "invalid") is False
        assert [op for _, oid, op, _ in recorded_actions if oid == 0xFF000002] == [
            2,
            0,
            3,
            1,
            4,
            3,
            4,
            5,
        ]
    finally:
        stop_ev.set()
        t.join(timeout=2.0)
        srv.close()

    sock = tmp_path / "wayland-wlroots"
    sock.write_text("", encoding="utf-8")
    monkeypatch.setattr("shutil.which", lambda n: "/usr/bin/labwc" if n == "labwc" else None)
    fake_sandbox = MagicMock()
    fake_sandbox.to_context.return_value = SessionContext(
        mode="blackbox",
        xdg_runtime_dir=str(tmp_path),
        wayland_display="wayland-wlroots",
        headed=False,
        is_isolated=True,
    )
    driver = BlackboxDriver(sandbox=fake_sandbox)
    driver._adapter = WlrootsNestedAdapter()
    calls: list = []
    monkeypatch.setattr(
        "wayland_computer_use_mcp.blackbox.adapters.control_wlr_toplevel",
        lambda path, act, title=None, app_hint=None: (
            calls.append((path, act, title, app_hint)) or True
        ),
    )
    assert driver.activate_window(pid=None, title="Demo App") is True
    assert driver.set_window_state(1234, "minimize") is True
    assert calls[0] == (str(sock), "focus", "Demo App", None) and calls[1][1] == "minimize"

    # BlackboxDriver target_script hint extraction
    with proc_mod._lock:
        proc_mod._proc_metadata[55511] = {"target_script": "/tmp/my_cool_app.py"}
    try:
        assert BlackboxDriver._resolve_window_hints(55511, "Window") == ("Window", "my_cool_app")
    finally:
        with proc_mod._lock:
            proc_mod._proc_metadata.pop(55511, None)


def test_wlr_virtual_keyboard_xkb_modifiers_and_output_extent(tmp_path):
    sock_path = str(tmp_path / "wayland-wlr-input")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(2)
    mod_events: list[int] = []
    stop_ev = threading.Event()

    def _server() -> None:
        while not stop_ev.is_set():
            srv.settimeout(0.1)
            try:
                conn, _ = srv.accept()
            except (TimeoutError, OSError):
                break
            conn.setblocking(True)
            buf = bytearray()
            vkbd_mgr_id = vkbd_id = 0
            try:
                while not stop_ev.is_set():
                    data = conn.recv(4096)
                    if not data:
                        break
                    buf.extend(data)
                    for oid, op, body in _parse_wayland_packets(buf):
                        if oid == 1 and op == 1:
                            (reg_id,) = struct.unpack_from("<I", body, 0)
                            conn.sendall(
                                build_msg(
                                    reg_id,
                                    0,
                                    struct.pack("<I", 1)
                                    + pack_string("wl_seat")
                                    + struct.pack("<I", 1),
                                )
                                + build_msg(
                                    reg_id,
                                    0,
                                    struct.pack("<I", 2)
                                    + pack_string("wl_output")
                                    + struct.pack("<I", 2),
                                )
                                + build_msg(
                                    reg_id,
                                    0,
                                    struct.pack("<I", 3)
                                    + pack_string("zwlr_virtual_pointer_manager_v1")
                                    + struct.pack("<I", 1),
                                )
                                + build_msg(
                                    reg_id,
                                    0,
                                    struct.pack("<I", 4)
                                    + pack_string("zwp_virtual_keyboard_manager_v1")
                                    + struct.pack("<I", 1),
                                )
                            )
                        elif oid == 1 and op == 0:
                            (cb_id,) = struct.unpack_from("<I", body, 0)
                            conn.sendall(build_msg(cb_id, 0, struct.pack("<I", 1)))
                        elif oid == 2 and op == 0:
                            (gname,) = struct.unpack_from("<I", body, 0)
                            iface, off = unpack_string(body, 4)
                            _, new_id = struct.unpack_from("<II", body, off)
                            if iface == "wl_output" or gname == 2:
                                conn.sendall(
                                    build_msg(new_id, 1, struct.pack("<Iiii", 1, 1280, 720, 60000))
                                    + build_msg(new_id, 2)
                                )
                            elif iface == "zwp_virtual_keyboard_manager_v1" or gname == 4:
                                vkbd_mgr_id = new_id
                        elif oid == vkbd_mgr_id and op == 0 and len(body) >= 8:
                            _, vkbd_id = struct.unpack_from("<II", body, 0)
                        elif oid == vkbd_id and op == 2 and len(body) >= 16:
                            mod_events.append(struct.unpack_from("<I", body, 0)[0])
            finally:
                conn.close()

    t = threading.Thread(target=_server, daemon=True)
    t.start()
    try:
        client = WlrVirtualInputClient(sock_path=sock_path)
        try:
            assert (client.extent_w, client.extent_h) == (1280, 720)
            client._vkbd_id = client._vkbd_id or 99
            assert client.type_char("T") is True
            for _ in range(20):
                if len(mod_events) >= 2:
                    break
                time.sleep(0.02)
            assert mod_events[:2] == [1, 0]
        finally:
            client.close()
    finally:
        stop_ev.set()
        t.join(timeout=2.0)
        srv.close()


def test_virtual_session_isolation_labwc_rc_and_stale_cleanup(monkeypatch, tmp_path):
    host_xdg = tmp_path / "host_xdg"
    host_xdg.mkdir()
    (host_xdg / "wayland-0").write_text("")
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(host_xdg))
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/tmp/host-bus")
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    monkeypatch.setenv("AT_SPI_BUS_ADDRESS", "unix:path=/tmp/host-atspi")

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
        if cmd[0] == "dbus-daemon":
            assert "AT_SPI_BUS_ADDRESS" not in env
            return FakeProc("unix:path=/tmp/isolated-dbus-test\n")
        open(os.path.join(env["XDG_RUNTIME_DIR"], "wayland-mcp-virtual"), "w").close()
        return FakeProc()

    monkeypatch.setattr("shutil.which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr("subprocess.Popen", fake_popen)

    mgr = VirtualSessionManager()
    mgr.ensure_started(Config(display_mode="virtual", virtual_headed=True))
    try:
        ctx = get_active_context()
        assert ctx is not None and ctx.is_isolated and ctx.xdg_runtime_dir != str(host_xdg)
        sub_env = ctx.build_subprocess_env()
        assert "AT_SPI_BUS_ADDRESS" not in sub_env
        assert os.path.islink(os.path.join(mgr.runtime_dir or "", "host-wayland-0"))
        assert os.environ["WAYLAND_DISPLAY"] == "wayland-0"
    finally:
        mgr.stop()
    assert (
        get_active_context() is None
        and os.environ["AT_SPI_BUS_ADDRESS"] == "unix:path=/tmp/host-atspi"
    )

    # Bare labwc command + rc.xml creation + stale dir cleanup + already-pruned terminate
    cfg_dir = tmp_path / "labwc-cfg"
    cfg_dir.mkdir()
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/labwc" if name == "labwc" else None)
    assert (
        WlrootsNestedAdapter(preferred_bin="labwc").build_command(
            False, "wayland-0", None, config_dir=str(cfg_dir)
        )
        == f"labwc -C {cfg_dir}"
    )
    assert "MoveTo" in (cfg_dir / "rc.xml").read_text(encoding="utf-8")

    stale_dir = tmp_path / "wayland_mcp_virtual_dead"
    live_dir = tmp_path / "wayland_mcp_virtual_live"
    stale_dir.mkdir()
    live_dir.mkdir()
    (stale_dir / ".mcp_pid").write_text("999999", encoding="utf-8")
    (live_dir / ".mcp_pid").write_text(str(os.getpid()), encoding="utf-8")
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    monkeypatch.setattr(
        "os.kill",
        lambda pid, sig: (
            (_ for _ in ()).throw(ProcessLookupError()) if pid == 999999 and sig == 0 else None
        ),
    )
    cleanup_stale_virtual_dirs()
    assert not stale_dir.exists() and live_dir.exists()

    proc_mod._proc_metadata[999991] = {"target": "dummy", "args": []}
    proc_mod._processes.pop(999991, None)
    try:
        assert proc_mod.terminate(999991) is True
    finally:
        proc_mod._proc_metadata.pop(999991, None)
