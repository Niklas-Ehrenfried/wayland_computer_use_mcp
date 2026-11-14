"""Contract and unit tests for Core, Blackbox, and Host-Passthrough architecture."""

from __future__ import annotations

import array
import os
import socket
import struct
import threading
import time
from unittest.mock import AsyncMock, MagicMock

from PIL import Image

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
from wayland_computer_use_mcp.config import (
    Config,
    get_config,
    set_config,
)
from wayland_computer_use_mcp.core import (
    InputSink,
    MockInputSink,
    MockSessionDriver,
    SessionContext,
    SessionDriver,
)
from wayland_computer_use_mcp.core.wayland_wire import (
    build_msg,
    pack_string,
    unpack_string,
)
from wayland_computer_use_mcp.libei import BTN_LEFT, KEY_ENTER, EIClient
from wayland_computer_use_mcp.portal.remotedesktop import AsyncLoopThread
from wayland_computer_use_mcp.portal.session import PortalSession
from wayland_computer_use_mcp.portal.virtual import VirtualSessionManager


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
    """SessionContext builds isolated child envs without mutating os.environ."""
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

    # Parent process environment stays unchanged
    assert os.environ["AT_SPI_BUS_ADDRESS"] == "unix:path=/host/atspi"
    assert os.environ["WAYLAND_DISPLAY"] == "wayland-host"


def test_protocol_conformance_drivers_and_sinks():
    """Verify SessionDriver and InputSink protocol compliance across backends."""
    async_thread = AsyncLoopThread()
    try:
        blackbox = BlackboxDriver()
        host = HostPassthroughDriver(async_thread)
        mock_driver = MockSessionDriver()
        ei_sink = EIClient(fd=None)
        wlr_sink = WlrVirtualInputClient(sock_path=None)
        mock_sink = MockInputSink()

        assert isinstance(blackbox, SessionDriver)
        assert isinstance(host, SessionDriver)
        assert isinstance(mock_driver, SessionDriver)
        assert isinstance(ei_sink, InputSink)
        assert isinstance(wlr_sink, InputSink)
        assert isinstance(mock_sink, InputSink)
        assert blackbox.is_isolated() is True
        assert mock_driver.is_isolated() is True
        assert host.is_isolated() is False
        assert blackbox.ensure_dialogs_above() is False
        assert mock_driver.ensure_dialogs_above() is False
    finally:
        async_thread.stop()


def test_adapter_selection_and_commands(monkeypatch):
    """Adapters route kwin/wlroots/weston commands and detect binaries cleanly."""
    assert isinstance(select_blackbox_adapter("kwin_wayland --virtual"), KWinNestedAdapter)
    assert isinstance(select_blackbox_adapter("sway"), WlrootsNestedAdapter)
    assert isinstance(select_blackbox_adapter("labwc"), WlrootsNestedAdapter)
    assert isinstance(select_blackbox_adapter("weston --headless"), WestonNestedAdapter)

    monkeypatch.setattr(
        "shutil.which", lambda name: f"/usr/bin/{name}" if name == "kwin_wayland" else None
    )
    kwin = KWinNestedAdapter()
    assert kwin.is_available() is True
    headed_cmd = kwin.build_command(
        headed=True, socket_name="wl-test", host_wayland_display="wayland-0", width=1024, height=768
    )
    assert headed_cmd is not None and "--wayland-display wayland-0" in headed_cmd
    headless_cmd = kwin.build_command(
        headed=False, socket_name="wl-test", host_wayland_display=None
    )
    assert headless_cmd is not None and "--virtual" in headless_cmd

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/labwc" if name == "labwc" else None)
    wlr = WlrootsNestedAdapter()
    assert wlr.is_available() is True
    assert (
        wlr.build_command(
            headed=True, socket_name="x", host_wayland_display="wayland-0", config_dir="/tmp/cfg"
        )
        == "labwc -C /tmp/cfg"
    )
    assert isinstance(select_blackbox_adapter(), WlrootsNestedAdapter)

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/sway" if name == "sway" else None)
    wlr_sway = WlrootsNestedAdapter()
    assert (
        wlr_sway.build_command(headed=True, socket_name="x", host_wayland_display="wayland-0")
        == "sway -c /dev/null"
    )

    weston = WestonNestedAdapter()
    monkeypatch.setattr(
        "shutil.which", lambda name: "/usr/bin/weston" if name == "weston" else None
    )
    assert "--backend=wayland-backend.so" in (
        weston.build_command(headed=True, socket_name="w1", host_wayland_display="wayland-0") or ""
    )
    assert "--backend=headless-backend.so" in (
        weston.build_command(headed=False, socket_name="w1", host_wayland_display=None) or ""
    )


def test_find_created_wayland_socket(tmp_path):
    """VirtualSessionManager detects compositor-selected sockets (e.g. sway/labwc wayland-0)."""
    host_sock = tmp_path / "wayland-0-real"
    # Symlink representing host parent socket that should be skipped
    (tmp_path / "wayland-1").symlink_to(host_sock)
    (tmp_path / "wayland-0.lock").write_text("", encoding="utf-8")

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(tmp_path / "wayland-0"))
    try:
        found = VirtualSessionManager._find_created_wayland_socket(
            str(tmp_path), exclude="wayland-1"
        )
        assert found == "wayland-0"
    finally:
        sock.close()


def test_decode_shm_frame_formats_and_y_invert():
    """decode_shm_frame decodes XRGB/ARGB/XBGR/ABGR and handles Y inversion."""
    # Single pixel: R=0x12, G=0x34, B=0x56
    # Little-endian XRGB8888 in memory is B, G, R, X
    xrgb_buf = bytes([0x56, 0x34, 0x12, 0xFF])
    img = decode_shm_frame(xrgb_buf, WL_SHM_FORMAT_XRGB8888, 1, 1, 4)
    assert img is not None and img.getpixel((0, 0)) == (0x12, 0x34, 0x56)

    argb_img = decode_shm_frame(xrgb_buf, WL_SHM_FORMAT_ARGB8888, 1, 1, 4)
    assert argb_img is not None and argb_img.getpixel((0, 0)) == (0x12, 0x34, 0x56)

    # XBGR8888 / ABGR8888 in memory is R, G, B, A
    xbgr_buf = bytes([0x12, 0x34, 0x56, 0xFF])
    xbgr_img = decode_shm_frame(xbgr_buf, WL_SHM_FORMAT_XBGR8888, 1, 1, 4)
    abgr_img = decode_shm_frame(xbgr_buf, WL_SHM_FORMAT_ABGR8888, 1, 1, 4)
    assert xbgr_img is not None and xbgr_img.getpixel((0, 0)) == (0x12, 0x34, 0x56)
    assert abgr_img is not None and abgr_img.getpixel((0, 0)) == (0x12, 0x34, 0x56)

    # Two rows to verify y_invert=True flips top and bottom rows
    row0 = bytes([0x00, 0x00, 0xFF, 0xFF])  # Red
    row1 = bytes([0xFF, 0x00, 0x00, 0xFF])  # Blue
    flipped = decode_shm_frame(row0 + row1, WL_SHM_FORMAT_XRGB8888, 1, 2, 4, y_invert=True)
    assert flipped is not None
    assert flipped.getpixel((0, 0)) == (0, 0, 255)
    assert flipped.getpixel((0, 1)) == (255, 0, 0)

    assert decode_shm_frame(b"\x00\x00", WL_SHM_FORMAT_XRGB8888, 1, 1, 4) is None


def test_wlr_screencopy_roundtrip_over_mock_unix_socket(tmp_path):
    """capture_wlr_screencopy performs registry + memfd SHM copy over UNIX socket."""
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
            frame_id = 0
            shm_id = 0
            mgr_id = 0
            while True:
                data, ancdata, _, _ = conn.recvmsg(65536, socket.CMSG_SPACE(16 * 4))
                for lvl, ctype, cdata in ancdata:
                    if lvl == socket.SOL_SOCKET and ctype == socket.SCM_RIGHTS:
                        received_fds.extend(array.array("i", cdata))
                if not data and not ancdata:
                    break
                buf.extend(data)
                for oid, op, body in _parse_wayland_packets(buf):
                    if oid == 1 and op == 1:  # get_registry
                        (reg_id,) = struct.unpack_from("<I", body, 0)
                        ev = (
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
                        conn.sendall(ev)
                    elif oid == 1 and op == 0:  # sync
                        (cb_id,) = struct.unpack_from("<I", body, 0)
                        conn.sendall(build_msg(cb_id, 0, struct.pack("<I", 1)))
                    elif oid == 2 and op == 0:  # registry.bind
                        _name, off = struct.unpack_from("<I", body, 0)[0], 4
                        iface, off = unpack_string(body, off)
                        _ver, new_id = struct.unpack_from("<II", body, off)
                        if iface == "wl_shm":
                            shm_id = new_id
                        elif iface == "zwlr_screencopy_manager_v1":
                            mgr_id = new_id
                    elif oid == mgr_id and op == 0:  # capture_output
                        frame_id = struct.unpack_from("<I", body, 0)[0]
                        conn.sendall(
                            build_msg(
                                frame_id,
                                0,
                                struct.pack("<IIII", WL_SHM_FORMAT_XRGB8888, 2, 1, 8),
                            )
                            + build_msg(frame_id, 6)  # buffer_done (v3)
                        )
                    elif oid == shm_id and op == 0:  # create_pool
                        pool_id, _sz = struct.unpack_from("<Ii", body, 0)
                        pool_fds[pool_id] = received_fds.pop(0)
                    elif oid == frame_id and op == 0:  # copy(buffer)
                        fd = next(iter(pool_fds.values()))
                        # 2 pixels in little-endian XRGB8888: (10,20,30) then (200,100,50)
                        pixels = bytes([30, 20, 10, 0xFF, 50, 100, 200, 0xFF])
                        os.pwrite(fd, pixels, 0)
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
        assert isinstance(img, Image.Image)
        assert img.size == (2, 1)
        assert img.getpixel((0, 0)) == (10, 20, 30)
        assert img.getpixel((1, 0)) == (200, 100, 50)
    finally:
        t.join(timeout=2.0)
        srv.close()


def test_wlr_virtual_input_wire_protocol(tmp_path):
    """WlrVirtualInputClient binds vptr/vkbd globals, sends keymap fd and input packets."""
    sock_path = str(tmp_path / "wayland-wlr-input")
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(sock_path)
    srv.listen(1)
    srv.settimeout(3.0)

    captured_packets: list[tuple[int, int, bytes]] = []
    captured_fd_sizes: list[int] = []
    ready_ev = threading.Event()
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
                        ev = (
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
                        conn.sendall(ev)
                    elif oid == 1 and op == 0:
                        (cb_id,) = struct.unpack_from("<I", body, 0)
                        conn.sendall(build_msg(cb_id, 0, struct.pack("<I", 1)))
                        ready_ev.set()
        finally:
            conn.close()

    t = threading.Thread(target=_server_worker, daemon=True)
    t.start()
    client = WlrVirtualInputClient(sock_path=sock_path, extent_w=1280, extent_h=800)
    try:
        assert client.has_absolute_pointer is True
        assert client.has_scroll is True
        assert client.keyboard_device is not None
        assert client.can_type_text("abc") is True

        assert client.pointer_motion_absolute(320, 240) is True
        assert client.button_click(BTN_LEFT) is True
        assert client.scroll(0, 2) is True
        assert client.type_char("a") is True
        client.key_combination([KEY_ENTER])

        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not any(op == 1 for _, op, _ in captured_packets):
            time.sleep(0.02)
        time.sleep(0.05)

        vptr_id = client._vptr_id
        vkbd_id = client._vkbd_id
        vptr_ops = [(op, body) for oid, op, body in captured_packets if oid == vptr_id]
        vkbd_ops = [(op, body) for oid, op, body in captured_packets if oid == vkbd_id]

        # motion_absolute opcode=1 check coordinates 320, 240
        motion_payloads = [body for op, body in vptr_ops if op == 1]
        assert motion_payloads
        _, ix, iy, ex, ey = struct.unpack_from("<IIIII", motion_payloads[0], 0)
        assert (ix, iy, ex, ey) == (320, 240, 1280, 800)

        # button opcode=2 (down & up), axis opcode=3, frame opcode=4
        assert any(op == 2 for op, _ in vptr_ops)
        assert any(op == 3 for op, _ in vptr_ops)
        assert any(op == 4 for op, _ in vptr_ops)

        # keymap fd was passed via SCM_RIGHTS and key events (op=1) were emitted
        assert captured_fd_sizes and captured_fd_sizes[0] > 0
        assert len([1 for op, _ in vkbd_ops if op == 1]) >= 4
    finally:
        stop_ev.set()
        client.close()
        t.join(timeout=2.0)
        srv.close()


def test_host_passthrough_lazy_portal_promotion(monkeypatch):
    """lazy_portal skips upfront portal prompt and promotes lazily on capture/input."""
    orig_cfg = get_config()
    cfg = Config(display_mode="live", mock_mode=False, lazy_portal=True)
    set_config(cfg)
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
            lambda **kwargs: (0, 0, 64, 64),
        )
        monkeypatch.setattr(
            "wayland_computer_use_mcp.compositors.ensure_portal_dialogs_above",
            lambda: False,
        )

        # Upfront initialization in lazy mode must NOT call negotiate()
        session.ensure_initialized()
        assert session.is_active() is True
        assert negotiate_mock.call_count == 0

        # Promoting via capture_frame triggers negotiate() lazily
        session.target_pid = 4321
        frame = session.capture_frame()
        assert frame.size == (64, 64)
        assert negotiate_mock.call_count == 1
    finally:
        session.close()
        set_config(orig_cfg)


def test_process_tools_route_dialog_raise_via_active_driver(monkeypatch):
    """launch_app delegates dialog foregrounding to active_driver().ensure_dialogs_above()."""
    from wayland_computer_use_mcp.tools import process_tools

    orig_cfg = get_config()
    set_config(Config(display_mode="live", mock_mode=False, lazy_portal=False))
    called = {"dialogs": 0}
    fake_driver = MagicMock()
    fake_driver.ensure_dialogs_above.side_effect = lambda: (
        called.__setitem__("dialogs", called["dialogs"] + 1) or True
    )

    monkeypatch.setattr(process_tools.global_portal_session, "active_driver", lambda: fake_driver)
    monkeypatch.setattr(process_tools.global_portal_session, "is_active", lambda: True)
    monkeypatch.setattr(process_tools.global_portal_session, "target_pid", None)
    monkeypatch.setattr(process_tools, "launch", lambda target, args=None, cwd=None: 12345)
    monkeypatch.setattr(process_tools, "_bind_focus_and_collect_elements", lambda pid: [])

    try:
        res = process_tools.launch_app("dummy.py")
        assert res["pid"] == 12345
        assert called["dialogs"] == 1
    finally:
        set_config(orig_cfg)


def test_wlr_foreign_toplevel_control_wire_roundtrip(tmp_path):
    """control_wlr_toplevel enumerates handles, skips closed windows, and emits opcodes."""
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
            except TimeoutError:
                continue
            except OSError:
                break
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
                            _, new_id = struct.unpack_from("<II", body, off)
                            if iface == "zwlr_foreign_toplevel_manager_v1" or gname == 10:
                                mgr_id = new_id
                                h_closed = 0xFF000001
                                h_target = 0xFF000002
                                conn.sendall(
                                    build_msg(mgr_id, 0, struct.pack("<I", h_closed))
                                    + build_msg(h_closed, 0, pack_string("Old Closed"))
                                    + build_msg(h_closed, 6)
                                    + build_msg(mgr_id, 0, struct.pack("<I", h_target))
                                    + build_msg(h_target, 0, pack_string("Labwc Target App"))
                                    + build_msg(h_target, 1, pack_string("org.example.Target"))
                                    + build_msg(h_target, 5)
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
        assert control_wlr_toplevel(sock_path, "minimize", title="Labwc Target") is True
        assert control_wlr_toplevel(sock_path, "maximize", app_hint="Target") is True
        assert control_wlr_toplevel(sock_path, "restore") is True
        assert control_wlr_toplevel(sock_path, "focus", title="Labwc Target") is True
        assert control_wlr_toplevel(sock_path, "close", title="Labwc Target") is True
        assert control_wlr_toplevel(sock_path, "invalid") is False

        h_ops = [op for _, oid, op, _ in recorded_actions if oid == 0xFF000002]
        assert h_ops == [2, 0, 3, 1, 4, 3, 4, 5]
        # Each action ends with manager.stop (op=0) on mgr_id
        assert len([1 for _, oid, op, _ in recorded_actions if oid != 0xFF000002 and op == 0]) == 5
    finally:
        stop_ev.set()
        t.join(timeout=2.0)
        srv.close()


def test_blackbox_driver_routes_window_control_via_wlroots_adapter(monkeypatch, tmp_path):
    """BlackboxDriver delegates activate_window and set_window_state to its adapter."""
    from wayland_computer_use_mcp.blackbox import BlackboxDriver, WlrootsNestedAdapter

    sock = tmp_path / "wayland-wlroots"
    sock.write_text("", encoding="utf-8")

    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/labwc" if name == "labwc" else None)
    adapter = WlrootsNestedAdapter()

    fake_sandbox = MagicMock()
    fake_sandbox.to_context.return_value = SessionContext(
        mode="blackbox",
        xdg_runtime_dir=str(tmp_path),
        wayland_display="wayland-wlroots",
        headed=False,
        is_isolated=True,
    )
    driver = BlackboxDriver(sandbox=fake_sandbox)
    driver._adapter = adapter

    calls: list[tuple[str, str, str | None, str | None]] = []
    monkeypatch.setattr(
        "wayland_computer_use_mcp.blackbox.adapters.control_wlr_toplevel",
        lambda path, action, title=None, app_hint=None: (
            calls.append((path, action, title, app_hint)) or True
        ),
    )

    assert driver.activate_window(pid=None, title="Demo App") is True
    assert driver.set_window_state(1234, "minimize") is True
    assert calls[0] == (str(sock), "focus", "Demo App", None)
    assert calls[1][0] == str(sock) and calls[1][1] == "minimize"


def test_wlr_virtual_keyboard_emits_xkb_modifiers_and_reads_output_extent(tmp_path):
    """WlrVirtualInputClient emits zwp_virtual_keyboard modifiers and syncs wl_output mode."""
    from wayland_computer_use_mcp.blackbox.wlr_wire import WlrVirtualInputClient

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
            vkbd_mgr_id = 0
            vkbd_id = 0
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
                            _, child_id = struct.unpack_from("<II", body, 0)
                            vkbd_id = child_id
                        elif oid == vkbd_id and op == 2 and len(body) >= 16:
                            (depressed,) = struct.unpack_from("<I", body, 0)
                            mod_events.append(depressed)
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
            # Wait briefly for mock server thread to parse packet
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


def test_labwc_bare_cmd_and_terminate_already_pruned_process(monkeypatch, tmp_path):
    """Bare WAYLAND_MCP_VIRTUAL_CMD=labwc uses isolated rc.xml and terminate handles exited PIDs."""
    from wayland_computer_use_mcp import process as proc_mod
    from wayland_computer_use_mcp.blackbox.adapters import WlrootsNestedAdapter
    from wayland_computer_use_mcp.portal.virtual import VirtualSessionManager

    cfg_dir = tmp_path / "labwc-cfg"
    cfg_dir.mkdir()
    monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/labwc" if name == "labwc" else None)
    adapter = WlrootsNestedAdapter(preferred_bin="labwc")
    cmd = adapter.build_command(
        headed=False, socket_name="wayland-0", host_wayland_display=None, config_dir=str(cfg_dir)
    )
    assert cmd == f"labwc -C {cfg_dir}"
    assert "MoveTo" in (cfg_dir / "rc.xml").read_text(encoding="utf-8")

    # Headless discovery must not exclude 'wayland-0' even if host WAYLAND_DISPLAY=wayland-0
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
    mgr = VirtualSessionManager()
    assert mgr._find_created_wayland_socket(str(tmp_path), exclude=None) is None

    # terminate() must cleanly return True if child already exited & was pruned from _processes
    fake_pid = 999991
    proc_mod._proc_metadata[fake_pid] = {"target": "dummy", "args": []}
    proc_mod._processes.pop(fake_pid, None)
    try:
        assert proc_mod.terminate(fake_pid) is True
    finally:
        proc_mod._proc_metadata.pop(fake_pid, None)
