"""Pure-Python Wayland wire helpers for wlroots capture and virtual input."""

from __future__ import annotations

import logging
import mmap
import os
import select
import struct
import time
from typing import Any

from PIL import Image

from wayland_computer_use_mcp.core.wayland_wire import (
    bind_global_req,
    build_msg,
    connect_and_discover_globals,
    create_shm_fd,
    send_with_fd,
    unpack_string,
)
from wayland_computer_use_mcp.libei import (
    BTN_LEFT,
    BTN_MIDDLE,
    BTN_RIGHT,
    CHAR_TO_KEYCODE,
    KEY_LEFTSHIFT,
    KEY_RIGHTALT,
    build_charmap_from_xkb,
)

logger = logging.getLogger(__name__)

# Standard Wayland wl_shm format codes (little-endian memory byte order)
WL_SHM_FORMAT_ARGB8888 = 0
WL_SHM_FORMAT_XRGB8888 = 1
WL_SHM_FORMAT_XBGR8888 = 0x34324258
WL_SHM_FORMAT_ABGR8888 = 0x34324241

_RAW_PIXEL_MODES: dict[int, tuple[str, str]] = {
    WL_SHM_FORMAT_XRGB8888: ("RGB", "BGRX"),
    WL_SHM_FORMAT_ARGB8888: ("RGBA", "BGRA"),
    WL_SHM_FORMAT_XBGR8888: ("RGB", "RGBX"),
    WL_SHM_FORMAT_ABGR8888: ("RGBA", "RGBA"),
}


def decode_shm_frame(
    buf_bytes: bytes | memoryview,
    shm_format: int,
    width: int,
    height: int,
    stride: int,
    y_invert: bool = False,
) -> Image.Image | None:
    """Converts a captured wl_shm byte buffer into an RGB PIL.Image."""
    spec = _RAW_PIXEL_MODES.get(shm_format)
    if spec is None or width <= 0 or height <= 0 or stride < width * 4:
        return None
    mode, raw_mode = spec
    expected_len = stride * height
    if len(buf_bytes) < expected_len:
        return None
    img = Image.frombytes(
        mode, (width, height), bytes(buf_bytes[:expected_len]), "raw", raw_mode, stride, 1
    )
    if mode != "RGB":
        img = img.convert("RGB")
    if y_invert:
        img = img.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    return img


def _capture_wlr_screencopy_once(sock_path: str, timeout: float = 2.0) -> Image.Image | None:
    """Captures one output frame via zwlr_screencopy_manager_v1 without external binaries."""
    if not sock_path or not os.path.exists(sock_path):
        return None

    sock, globals_map, buf, _fds, next_id = connect_and_discover_globals(sock_path, timeout=1.0)
    mem_fd: int | None = None
    try:
        if (
            "zwlr_screencopy_manager_v1" not in globals_map
            or "wl_shm" not in globals_map
            or "wl_output" not in globals_map
        ):
            return None

        shm_name, _ = globals_map["wl_shm"]
        out_name, _ = globals_map["wl_output"]
        mgr_name, mgr_ver = globals_map["zwlr_screencopy_manager_v1"]

        shm_id = next_id
        out_id = next_id + 1
        mgr_id = next_id + 2
        frame_id = next_id + 3
        pool_id = next_id + 4
        wl_buf_id = next_id + 5

        init_req = (
            bind_global_req(2, shm_name, "wl_shm", 1, shm_id)
            + bind_global_req(2, out_name, "wl_output", 1, out_id)
            + bind_global_req(2, mgr_name, "zwlr_screencopy_manager_v1", min(mgr_ver, 3), mgr_id)
            + build_msg(mgr_id, 0, struct.pack("<IiI", frame_id, 0, out_id))
        )
        sock.sendall(init_req)

        shm_params: tuple[int, int, int, int] | None = None
        buffer_done = mgr_ver < 3
        copied = False
        flags = 0
        ready = False
        failed = False

        deadline = time.monotonic() + timeout
        while not ready and not failed and time.monotonic() < deadline:
            if not copied and shm_params is not None and buffer_done:
                fmt, w, h, stride = shm_params
                size = stride * h
                mem_fd = create_shm_fd("wlr_screencopy", size)
                create_pool = build_msg(shm_id, 0, struct.pack("<Ii", pool_id, size))
                send_with_fd(sock, create_pool, mem_fd)
                copy_reqs = build_msg(
                    pool_id, 0, struct.pack("<IiiiiI", wl_buf_id, 0, w, h, stride, fmt)
                ) + build_msg(frame_id, 0, struct.pack("<I", wl_buf_id))
                sock.sendall(copy_reqs)
                copied = True

            rlist, _, _ = select.select([sock], [], [], 0.15)
            if not rlist:
                continue
            data = sock.recv(65536)
            if not data:
                break
            buf.extend(data)
            while len(buf) >= 8:
                oid, sz_op = struct.unpack_from("<II", buf, 0)
                sz = sz_op >> 16
                op = sz_op & 0xFFFF
                if sz < 8 or len(buf) < sz:
                    break
                body = bytes(buf[8:sz])
                del buf[:sz]
                if oid != frame_id:
                    continue
                if op == 0 and len(body) >= 16:  # buffer(fmt, w, h, stride)
                    fmt, w, h, stride = struct.unpack_from("<IIII", body, 0)
                    if fmt in _RAW_PIXEL_MODES and shm_params is None:
                        shm_params = (fmt, w, h, stride)
                elif op == 1 and len(body) >= 4:  # flags(flags)
                    (flags,) = struct.unpack_from("<I", body, 0)
                elif op == 2:  # ready
                    ready = True
                    break
                elif op == 3:  # failed
                    failed = True
                    break
                elif op == 6:  # buffer_done
                    buffer_done = True

        if not ready or shm_params is None or mem_fd is None:
            return None

        fmt, w, h, stride = shm_params
        size = stride * h
        with mmap.mmap(mem_fd, size, access=mmap.ACCESS_READ) as mm:
            return decode_shm_frame(mm, fmt, w, h, stride, y_invert=bool(flags & 1))
    except Exception as exc:
        logger.debug("zwlr_screencopy_manager_v1 capture failed: %s", exc)
        return None
    finally:
        if mem_fd is not None:
            try:
                os.close(mem_fd)
            except OSError:
                pass
        try:
            sock.close()
        except OSError:
            pass


def capture_wlr_screencopy(sock_path: str, timeout: float = 2.0) -> Image.Image | None:
    """Captures an output frame via zwlr_screencopy_manager_v1, retrying cold-start black frames."""
    deadline = time.monotonic() + timeout
    img: Image.Image | None = None
    for attempt in range(4):
        remaining = max(0.2, deadline - time.monotonic())
        img = _capture_wlr_screencopy_once(sock_path, timeout=remaining)
        if img is None:
            return None
        # Retry briefly if compositor hasn't received the client's first surface commit yet
        if img.getextrema() != ((0, 0), (0, 0), (0, 0)):
            return img
        if attempt < 3 and time.monotonic() + 0.25 < deadline:
            time.sleep(0.25)
    return img


def _generate_default_xkb_keymap() -> str | None:
    """Generates a standard US XKB keymap string using libxkbcommon.so.0 if installed."""
    import ctypes
    import ctypes.util

    path = ctypes.util.find_library("xkbcommon") or "libxkbcommon.so.0"
    try:
        xkb = ctypes.CDLL(path)
    except OSError:
        return None

    class _RuleNames(ctypes.Structure):
        _fields_ = [
            ("rules", ctypes.c_char_p),
            ("model", ctypes.c_char_p),
            ("layout", ctypes.c_char_p),
            ("variant", ctypes.c_char_p),
            ("options", ctypes.c_char_p),
        ]

    xkb.xkb_context_new.argtypes = [ctypes.c_int]
    xkb.xkb_context_new.restype = ctypes.c_void_p
    xkb.xkb_context_unref.argtypes = [ctypes.c_void_p]
    xkb.xkb_keymap_new_from_names.argtypes = [
        ctypes.c_void_p,
        ctypes.POINTER(_RuleNames),
        ctypes.c_int,
    ]
    xkb.xkb_keymap_new_from_names.restype = ctypes.c_void_p
    xkb.xkb_keymap_get_as_string.argtypes = [ctypes.c_void_p, ctypes.c_uint]
    xkb.xkb_keymap_get_as_string.restype = ctypes.c_void_p
    xkb.xkb_keymap_unref.argtypes = [ctypes.c_void_p]

    libc = ctypes.CDLL(None)
    libc.free.argtypes = [ctypes.c_void_p]

    ctx = xkb.xkb_context_new(0)
    if not ctx:
        return None
    try:
        names = _RuleNames(None, None, b"us", None, None)
        kmap = xkb.xkb_keymap_new_from_names(ctx, ctypes.byref(names), 0)
        if not kmap:
            return None
        try:
            raw_ptr = xkb.xkb_keymap_get_as_string(kmap, 0xFFFFFFFF)
            if not raw_ptr:
                return None
            try:
                return ctypes.c_char_p(raw_ptr).value.decode("utf-8", errors="ignore")
            finally:
                libc.free(raw_ptr)
        finally:
            xkb.xkb_keymap_unref(kmap)
    finally:
        xkb.xkb_context_unref(ctx)


_MOD_MASKS: dict[int, int] = {
    42: 1,  # KEY_LEFTSHIFT -> Shift
    54: 1,  # KEY_RIGHTSHIFT -> Shift
    29: 4,  # KEY_LEFTCTRL -> Control
    97: 4,  # KEY_RIGHTCTRL -> Control
    56: 8,  # KEY_LEFTALT -> Mod1
    125: 64,  # KEY_LEFTMETA -> Mod4
    126: 64,  # KEY_RIGHTMETA -> Mod4
    100: 128,  # KEY_RIGHTALT -> Mod5 (Level3)
}


class WlrVirtualInputClient:
    """InputSink backed by zwlr_virtual_pointer_v1 + zwp_virtual_keyboard_v1."""

    def __init__(
        self, sock_path: str | None = None, extent_w: int = 1280, extent_h: int = 800
    ) -> None:
        self.sock_path = sock_path
        self.extent_w = max(1, extent_w)
        self.extent_h = max(1, extent_h)
        self._sock: Any = None
        self._vptr_id: int = 0
        self._vkbd_id: int = 0
        self._xkb_charmap: dict[str, tuple[int, bool, bool]] = {}
        self._held_mods: set[int] = set()
        self.has_absolute_pointer: bool = False
        self.has_scroll: bool = False
        self.keyboard_device: Any = None
        self._last_x: float = 0.0
        self._last_y: float = 0.0
        self._t0 = time.monotonic()

        if sock_path and os.path.exists(sock_path):
            self._connect(sock_path)

    def _ts_ms(self) -> int:
        return int((time.monotonic() - self._t0) * 1000) & 0xFFFFFFFF

    def _connect(self, sock_path: str) -> None:
        sock, globals_map, buf, _fds, next_id = connect_and_discover_globals(sock_path, timeout=1.0)
        try:
            seat = globals_map.get("wl_seat")
            out_global = globals_map.get("wl_output")
            vptr_mgr = globals_map.get("zwlr_virtual_pointer_manager_v1")
            vkbd_mgr = globals_map.get("zwp_virtual_keyboard_manager_v1")

            reqs = b""
            seat_id = 0
            out_id = 0
            if seat:
                seat_id = next_id
                next_id += 1
                reqs += bind_global_req(2, seat[0], "wl_seat", 1, seat_id)

            if out_global:
                out_id = next_id
                next_id += 1
                reqs += bind_global_req(
                    2, out_global[0], "wl_output", min(out_global[1], 2), out_id
                )

            if vptr_mgr:
                mgr_id = next_id
                vptr_id = next_id + 1
                next_id += 2
                reqs += bind_global_req(
                    2, vptr_mgr[0], "zwlr_virtual_pointer_manager_v1", 1, mgr_id
                )
                reqs += build_msg(mgr_id, 0, struct.pack("<II", seat_id, vptr_id))
                self._vptr_id = vptr_id
                self.has_absolute_pointer = True
                self.has_scroll = True

            if vkbd_mgr and seat_id:
                kmgr_id = next_id
                vkbd_id = next_id + 1
                next_id += 2
                reqs += bind_global_req(
                    2, vkbd_mgr[0], "zwp_virtual_keyboard_manager_v1", 1, kmgr_id
                )
                reqs += build_msg(kmgr_id, 0, struct.pack("<II", seat_id, vkbd_id))
                self._vkbd_id = vkbd_id

            if not reqs:
                sock.close()
                return

            sock.sendall(reqs)
            if out_id:
                self._read_output_extent(sock, buf, out_id)
            if self._vkbd_id:
                kmap_str = _generate_default_xkb_keymap()
                if kmap_str:
                    kbytes = kmap_str.encode("utf-8") + b"\x00"
                    kfd = create_shm_fd("wlr_xkb_keymap")
                    try:
                        os.write(kfd, kbytes)
                        send_with_fd(
                            sock,
                            build_msg(self._vkbd_id, 0, struct.pack("<II", 1, len(kbytes))),
                            kfd,
                        )
                    finally:
                        os.close(kfd)
                    self._xkb_charmap = build_charmap_from_xkb(kmap_str)
                    self.keyboard_device = self._vkbd_id
            self._sock = sock
        except Exception as exc:
            logger.debug("WlrVirtualInputClient init failed: %s", exc)
            try:
                sock.close()
            except OSError:
                pass
            self.has_absolute_pointer = False
            self.keyboard_device = None

    def _read_output_extent(self, sock: Any, buf: bytearray, out_id: int) -> None:
        """Reads wl_output.mode dimensions so absolute pointer extents match output pixels."""
        deadline = time.monotonic() + 0.15
        while time.monotonic() < deadline:
            rlist, _, _ = select.select([sock], [], [], 0.05)
            if not rlist:
                continue
            data = sock.recv(16384)
            if not data:
                break
            buf.extend(data)
            done = False
            while len(buf) >= 8:
                oid, sz_op = struct.unpack_from("<II", buf, 0)
                sz = sz_op >> 16
                op = sz_op & 0xFFFF
                if sz < 8 or len(buf) < sz:
                    break
                body = bytes(buf[8:sz])
                del buf[:sz]
                if oid == out_id and op == 1 and len(body) >= 12:  # mode(flags, w, h, refresh)
                    flags, w, h = struct.unpack_from("<Iii", body, 0)
                    if w > 0 and h > 0 and ((flags & 1) or self.extent_w == 1280):
                        self.extent_w = w
                        self.extent_h = h
                elif oid == out_id and op == 2:  # done
                    done = True
                    break
            if done:
                break

    def _send(self, data: bytes) -> bool:
        if self._sock is None:
            return False
        try:
            self._sock.sendall(data)
            return True
        except OSError:
            return False

    def pointer_motion_absolute(self, x: float, y: float) -> bool:
        if not self._vptr_id:
            return False
        self._last_x = float(x)
        self._last_y = float(y)
        ix = max(0, min(self.extent_w - 1, int(round(x))))
        iy = max(0, min(self.extent_h - 1, int(round(y))))
        pkt = build_msg(
            self._vptr_id,
            1,
            struct.pack("<IIIII", self._ts_ms(), ix, iy, self.extent_w, self.extent_h),
        ) + build_msg(self._vptr_id, 4)
        return self._send(pkt)

    @staticmethod
    def _button_code(button: int | str) -> int:
        if isinstance(button, int):
            return button
        return {"left": BTN_LEFT, "middle": BTN_MIDDLE, "right": BTN_RIGHT}.get(
            button.lower(), BTN_LEFT
        )

    def button_down(self, button_code: int | str = BTN_LEFT) -> bool:
        if not self._vptr_id:
            return False
        code = self._button_code(button_code)
        pkt = build_msg(self._vptr_id, 2, struct.pack("<III", self._ts_ms(), code, 1)) + build_msg(
            self._vptr_id, 4
        )
        return self._send(pkt)

    def button_up(self, button_code: int | str = BTN_LEFT) -> bool:
        if not self._vptr_id:
            return False
        code = self._button_code(button_code)
        pkt = build_msg(self._vptr_id, 2, struct.pack("<III", self._ts_ms(), code, 0)) + build_msg(
            self._vptr_id, 4
        )
        return self._send(pkt)

    def button_click(self, button_code: int | str = BTN_LEFT) -> bool:
        if not self.button_down(button_code):
            return False
        time.sleep(0.02)
        return self.button_up(button_code)

    def double_click(self, button_code: int | str = BTN_LEFT) -> bool:
        if not self.button_click(button_code):
            return False
        time.sleep(0.04)
        return self.button_click(button_code)

    @staticmethod
    def _scroll_steps(delta: float) -> int:
        if delta == 0:
            return 0
        if abs(delta) >= 50:
            return int(round(delta / 100.0))
        return 1 if delta > 0 else -1

    def scroll(self, dx: float, dy: float) -> bool:
        if not self._vptr_id:
            return False
        pkt = b""
        ts = self._ts_ms()
        steps_y = self._scroll_steps(dy)
        steps_x = self._scroll_steps(dx)
        if steps_y or steps_x:
            pkt += build_msg(self._vptr_id, 5, struct.pack("<I", 0))  # axis_source=wheel
        if steps_y:
            pkt += build_msg(self._vptr_id, 3, struct.pack("<IIi", ts, 0, steps_y * 15 * 256))
        if steps_x:
            pkt += build_msg(self._vptr_id, 3, struct.pack("<IIi", ts, 1, steps_x * 15 * 256))
        if not pkt:
            return True
        return self._send(pkt + build_msg(self._vptr_id, 4))

    def can_type_text(self, text: str) -> bool:
        if not self._vkbd_id:
            return False
        if self._xkb_charmap:
            return all(ch in self._xkb_charmap for ch in text)
        return all(ch in CHAR_TO_KEYCODE for ch in text)

    def resolve_key_name(self, key_name: str) -> int | None:
        if len(key_name) == 1 and self._xkb_charmap:
            entry = self._xkb_charmap.get(key_name.lower())
            if entry is not None:
                return entry[0]
        return None

    def _emit_key(self, keycode: int, pressed: bool) -> bool:
        pkt = build_msg(
            self._vkbd_id,
            1,
            struct.pack("<III", self._ts_ms(), keycode, 1 if pressed else 0),
        )
        if keycode in _MOD_MASKS:
            if pressed:
                self._held_mods.add(keycode)
            else:
                self._held_mods.discard(keycode)
            depressed = 0
            for code in self._held_mods:
                depressed |= _MOD_MASKS.get(code, 0)
            pkt += build_msg(self._vkbd_id, 2, struct.pack("<IIII", depressed, 0, 0, 0))
        return self._send(pkt)

    def type_char(self, ch: str) -> bool:
        if not self._vkbd_id:
            return False
        need_altgr = False
        if self._xkb_charmap:
            mapping = self._xkb_charmap.get(ch)
            if not mapping:
                return False
            keycode, need_shift, need_altgr = mapping
        else:
            mapping2 = CHAR_TO_KEYCODE.get(ch)
            if not mapping2:
                return False
            keycode, need_shift = mapping2

        if need_shift:
            self._emit_key(KEY_LEFTSHIFT, True)
        if need_altgr:
            self._emit_key(KEY_RIGHTALT, True)
        ok = self._emit_key(keycode, True) and self._emit_key(keycode, False)
        if need_altgr:
            self._emit_key(KEY_RIGHTALT, False)
        if need_shift:
            self._emit_key(KEY_LEFTSHIFT, False)
        return ok

    def key_press(self, keycode: int, is_down: bool) -> None:
        if self._vkbd_id:
            self._emit_key(keycode, is_down)

    def key_combination(self, keycodes: list[int]) -> None:
        if not self._vkbd_id:
            return
        for code in keycodes:
            self.key_press(code, True)
            time.sleep(0.003)
        time.sleep(0.01)
        for code in reversed(keycodes):
            self.key_press(code, False)
            time.sleep(0.003)

    def close(self) -> None:
        if self._sock is not None:
            try:
                if self._vptr_id:
                    self._sock.sendall(build_msg(self._vptr_id, 8))
                if self._vkbd_id:
                    self._sock.sendall(build_msg(self._vkbd_id, 3))
            except OSError:
                pass
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        self._vptr_id = 0
        self._vkbd_id = 0
        self.has_absolute_pointer = False
        self.has_scroll = False
        self.keyboard_device = None


def _select_toplevel_handle(
    handles: dict[int, dict[str, Any]],
    order: list[int],
    title: str | None = None,
    app_hint: str | None = None,
) -> int | None:
    """Selects best open foreign-toplevel handle matching title or app_hint."""
    open_ids = [hid for hid in order if not handles[hid]["closed"]]
    if not open_ids:
        return None
    if title:
        needle = title.lower()
        for hid in open_ids:
            info = handles[hid]
            if needle in info["title"].lower() or needle in info["app_id"].lower():
                return hid
    if app_hint:
        needle = app_hint.lower()
        for hid in open_ids:
            info = handles[hid]
            if needle in info["app_id"].lower() or needle in info["title"].lower():
                return hid
    if len(open_ids) == 1 or (not title and not app_hint):
        return open_ids[-1]
    return None


def control_wlr_toplevel(
    sock_path: str,
    action: str,
    title: str | None = None,
    app_hint: str | None = None,
    timeout: float = 1.0,
) -> bool:
    """Controls a window via zwlr_foreign_toplevel_management_v1 (labwc/sway/wlroots)."""
    norm = action.strip().lower()
    if norm not in {"minimize", "maximize", "restore", "focus", "close"}:
        return False
    if not sock_path or not os.path.exists(sock_path):
        return False

    sock, globals_map, buf, _fds, next_id = connect_and_discover_globals(sock_path, timeout=timeout)
    try:
        mgr_entry = globals_map.get("zwlr_foreign_toplevel_manager_v1")
        if mgr_entry is None:
            return False

        mgr_name, mgr_ver = mgr_entry
        mgr_id = next_id
        next_id += 1

        reqs = bind_global_req(
            2, mgr_name, "zwlr_foreign_toplevel_manager_v1", min(mgr_ver, 3), mgr_id
        )
        seat_id = 0
        seat_entry = globals_map.get("wl_seat")
        if seat_entry is not None:
            seat_id = next_id
            next_id += 1
            reqs += bind_global_req(2, seat_entry[0], "wl_seat", 1, seat_id)
        sync_cb_id = next_id
        next_id += 1
        reqs += build_msg(1, 0, struct.pack("<I", sync_cb_id))
        sock.sendall(reqs)

        handles: dict[int, dict[str, Any]] = {}
        order: list[int] = []
        synced = False
        deadline = time.monotonic() + timeout

        def _open_all_done() -> bool:
            open_handles = [handles[h] for h in order if not handles[h]["closed"]]
            return bool(open_handles) and all(h["done"] for h in open_handles)

        while time.monotonic() < deadline and not (synced and _open_all_done()):
            rlist, _, _ = select.select([sock], [], [], 0.1)
            if not rlist:
                if synced:
                    break
                continue
            data = sock.recv(65536)
            if not data:
                break
            buf.extend(data)
            while len(buf) >= 8:
                oid, sz_op = struct.unpack_from("<II", buf, 0)
                sz = sz_op >> 16
                op = sz_op & 0xFFFF
                if sz < 8 or len(buf) < sz:
                    break
                body = bytes(buf[8:sz])
                del buf[:sz]
                if oid == sync_cb_id and op == 0:
                    synced = True
                elif oid == mgr_id and op == 0 and len(body) >= 4:
                    (handle_id,) = struct.unpack_from("<I", body, 0)
                    if handle_id not in handles:
                        handles[handle_id] = {
                            "title": "",
                            "app_id": "",
                            "done": False,
                            "closed": False,
                        }
                        order.append(handle_id)
                elif oid in handles:
                    entry = handles[oid]
                    if op == 0 and len(body) >= 4:
                        entry["title"], _ = unpack_string(body, 0)
                    elif op == 1 and len(body) >= 4:
                        entry["app_id"], _ = unpack_string(body, 0)
                    elif op == 5:
                        entry["done"] = True
                    elif op == 6:
                        entry["closed"] = True

        target_id = _select_toplevel_handle(handles, order, title=title, app_hint=app_hint)
        if target_id is None:
            sock.sendall(build_msg(mgr_id, 0))
            return False

        action_msgs = b""
        if norm == "minimize":
            action_msgs = build_msg(target_id, 2)
        elif norm == "maximize":
            action_msgs = build_msg(target_id, 0)
        elif norm == "restore":
            action_msgs = build_msg(target_id, 3) + build_msg(target_id, 1)
            if seat_id:
                action_msgs += build_msg(target_id, 4, struct.pack("<I", seat_id))
        elif norm == "focus":
            if not seat_id:
                return False
            action_msgs = build_msg(target_id, 3) + build_msg(
                target_id, 4, struct.pack("<I", seat_id)
            )
        elif norm == "close":
            action_msgs = build_msg(target_id, 5)

        flush_cb_id = next_id
        next_id += 1
        sock.sendall(
            action_msgs + build_msg(mgr_id, 0) + build_msg(1, 0, struct.pack("<I", flush_cb_id))
        )

        flush_deadline = time.monotonic() + min(0.4, timeout)
        flushed = False
        while not flushed and time.monotonic() < flush_deadline:
            rlist, _, _ = select.select([sock], [], [], 0.05)
            if not rlist:
                continue
            data = sock.recv(65536)
            if not data:
                break
            buf.extend(data)
            while len(buf) >= 8:
                oid, sz_op = struct.unpack_from("<II", buf, 0)
                sz = sz_op >> 16
                op = sz_op & 0xFFFF
                if sz < 8 or len(buf) < sz:
                    break
                del buf[:sz]
                if oid == flush_cb_id and op == 0:
                    flushed = True
                    break
        return True
    except Exception as exc:
        logger.debug("control_wlr_toplevel failed: %s", exc)
        return False
    finally:
        try:
            sock.close()
        except OSError:
            pass
