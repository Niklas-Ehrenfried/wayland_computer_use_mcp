"""Pure-Python Wayland clipboard client using ext_data_control_manager_v1 / wlr-data-control."""

from __future__ import annotations

import array
import logging
import os
import select
import socket
import struct
import threading
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

_TEXT_MIMES = (
    "text/plain;charset=utf-8",
    "text/plain",
    "UTF8_STRING",
    "STRING",
    "TEXT",
)


def _pack_string(value: str) -> bytes:
    encoded = value.encode("utf-8") + b"\x00"
    pad = (4 - (len(encoded) % 4)) % 4
    return struct.pack("<I", len(encoded)) + encoded + (b"\x00" * pad)


def _unpack_string(body: bytes, offset: int = 0) -> tuple[str, int]:
    (slen,) = struct.unpack_from("<I", body, offset)
    raw = body[offset + 4 : offset + 4 + max(0, slen - 1)]
    pad = (4 - (slen % 4)) % 4
    return raw.decode("utf-8", errors="replace"), offset + 4 + slen + pad


def _build_msg(obj_id: int, opcode: int, payload: bytes = b"") -> bytes:
    size = 8 + len(payload)
    return struct.pack("<II", obj_id, (size << 16) | opcode) + payload


def _resolve_wayland_socket_path() -> str | None:
    wl_display = os.environ.get("WAYLAND_DISPLAY")
    if not wl_display:
        return None
    if os.path.isabs(wl_display):
        return wl_display if os.path.exists(wl_display) else None
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    candidate = os.path.join(runtime_dir, wl_display)
    return candidate if os.path.exists(candidate) else None


def _write_fd_and_close(fd: int, data: bytes) -> None:
    def _writer() -> None:
        try:
            view = memoryview(data)
            written = 0
            while written < len(view):
                written += os.write(fd, view[written:])
        except OSError as exc:
            logger.debug("Wayland clipboard send write failed: %s", exc)
        finally:
            try:
                os.close(fd)
            except OSError:
                pass

    if len(data) <= 32768:
        _writer()
    else:
        threading.Thread(target=_writer, daemon=True).start()


def _pick_text_mime(mimes: list[str]) -> str | None:
    for preferred in _TEXT_MIMES:
        if preferred in mimes:
            return preferred
    for mime in mimes:
        if mime.startswith("text/"):
            return mime
    return None


class WaylandDataControlClient:
    """Thread-safe Wayland data-control clipboard client with persistent selection ownership."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._sock_path: str | None = None
        self._wake_r: int | None = None
        self._wake_w: int | None = None
        self._requests: list[tuple[str, Any, threading.Event, list[Any]]] = []

    def reset(self) -> None:
        """Stops the background Wayland clipboard loop and closes wakeup pipes."""
        with self._lock:
            thread = self._thread
            wake_w = self._wake_w
            if thread is not None and thread.is_alive():
                self._requests.append(("stop", None, threading.Event(), [None]))
                if wake_w is not None:
                    try:
                        os.write(wake_w, b"\x01")
                    except OSError:
                        pass
            self._thread = None
            self._sock_path = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=0.5)

    def _ensure_worker(self, target_path: str) -> bool:
        with self._lock:
            if (
                self._thread is not None
                and self._thread.is_alive()
                and self._sock_path == target_path
            ):
                return True

        self.reset()
        with self._lock:
            wake_r, wake_w = os.pipe()
            os.set_blocking(wake_r, False)
            ready_evt = threading.Event()
            ready_box: list[Any] = [False]
            self._wake_r = wake_r
            self._wake_w = wake_w
            self._sock_path = target_path
            self._requests.clear()
            self._thread = threading.Thread(
                target=self._worker_loop,
                args=(target_path, wake_r, wake_w, ready_evt, ready_box),
                daemon=True,
                name="wayland-data-control-clipboard",
            )
            self._thread.start()

        ready_evt.wait(timeout=1.5)
        return bool(ready_box[0])

    def _enqueue(self, kind: str, arg: Any, timeout: float = 2.0) -> Any:
        target_path = _resolve_wayland_socket_path()
        if not target_path:
            return False if kind == "write" else None
        if not self._ensure_worker(target_path):
            return False if kind == "write" else None

        done_evt = threading.Event()
        res_box: list[Any] = [False if kind == "write" else None]
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                return res_box[0]
            self._requests.append((kind, arg, done_evt, res_box))
            if self._wake_w is not None:
                try:
                    os.write(self._wake_w, b"\x01")
                except OSError:
                    return res_box[0]

        done_evt.wait(timeout=timeout)
        return res_box[0]

    def write_text(self, text: str) -> bool:
        """Publishes UTF-8 text to the Wayland selection and serves paste requests."""
        return bool(self._enqueue("write", text))

    def read_text(self) -> str | None:
        """Reads current UTF-8 text from the Wayland selection."""
        return self._enqueue("read", None)

    def _worker_loop(
        self,
        sock_path: str,
        wake_r: int,
        wake_w: int,
        ready_evt: threading.Event,
        ready_box: list[Any],
    ) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        buf = bytearray()
        fds: list[int] = []
        active_reads: dict[int, tuple[bytearray, threading.Event, list[Any], float]] = {}

        try:
            sock.connect(sock_path)
            sock.setblocking(False)

            next_id = 4
            globals_map: dict[str, tuple[int, int]] = {}
            pending_syncs: dict[int, Callable[[], None]] = {}
            sources: dict[int, bytes] = {}
            offers: dict[int, list[str]] = {}
            current_offer: list[int | None] = [None]
            primary_offer: list[int | None] = [None]
            dev_id: list[int] = [0]
            mgr_id: list[int] = [0]

            def alloc_id() -> int:
                nonlocal next_id
                oid = next_id
                next_id += 1
                return oid

            def pump_recv() -> bool:
                try:
                    data, ancdata, _, _ = sock.recvmsg(65536, socket.CMSG_SPACE(32 * 4))
                except BlockingIOError:
                    return True
                except OSError:
                    return False
                if not data and not ancdata:
                    return False
                for cmsg_level, cmsg_type, cmsg_data in ancdata:
                    if cmsg_level == socket.SOL_SOCKET and cmsg_type == socket.SCM_RIGHTS:
                        fds.extend(array.array("i", cmsg_data))
                buf.extend(data)
                return True

            def dispatch_events() -> bool:
                while len(buf) >= 8:
                    oid, sz_op = struct.unpack_from("<II", buf, 0)
                    sz = sz_op >> 16
                    op = sz_op & 0xFFFF
                    if sz < 8 or len(buf) < sz:
                        break
                    body = bytes(buf[8:sz])
                    del buf[:sz]

                    if oid == 1 and op == 0:  # wl_display.error
                        target, code = struct.unpack_from("<II", body, 0)
                        msg_text, _ = _unpack_string(body, 8)
                        logger.debug(
                            "Wayland clipboard error obj=%s code=%s: %s",
                            target,
                            code,
                            msg_text,
                        )
                        return False
                    if oid == 2 and op == 0:  # wl_registry.global
                        name = struct.unpack_from("<I", body, 0)[0]
                        iface, off = _unpack_string(body, 4)
                        (ver,) = struct.unpack_from("<I", body, off)
                        globals_map[iface] = (name, ver)
                    elif oid in pending_syncs and op == 0:  # wl_callback.done
                        cb = pending_syncs.pop(oid)
                        cb()
                    elif oid == dev_id[0]:
                        if op == 0:  # data_offer(new_id)
                            (offer_oid,) = struct.unpack_from("<I", body, 0)
                            offers[offer_oid] = []
                        elif op == 1:  # selection(offer_id)
                            (offer_oid,) = struct.unpack_from("<I", body, 0)
                            old = current_offer[0]
                            new_val = offer_oid if offer_oid != 0 else None
                            if old and old != new_val and old != primary_offer[0]:
                                try:
                                    sock.sendall(_build_msg(old, 1))  # offer.destroy
                                except OSError:
                                    pass
                                offers.pop(old, None)
                            current_offer[0] = new_val
                        elif op == 2:  # finished
                            return False
                        elif op == 3:  # primary_selection(offer_id)
                            (offer_oid,) = struct.unpack_from("<I", body, 0)
                            old = primary_offer[0]
                            new_val = offer_oid if offer_oid != 0 else None
                            if old and old != new_val and old != current_offer[0]:
                                try:
                                    sock.sendall(_build_msg(old, 1))
                                except OSError:
                                    pass
                                offers.pop(old, None)
                            primary_offer[0] = new_val
                    elif oid in offers and op == 0:  # offer(mime_type)
                        mime, _ = _unpack_string(body, 0)
                        offers[oid].append(mime)
                    elif oid in sources:
                        if op == 0:  # send(mime_type, fd)
                            _mime, _ = _unpack_string(body, 0)
                            if fds:
                                _write_fd_and_close(fds.pop(0), sources[oid])
                        elif op == 1:  # cancelled
                            sources.pop(oid, None)
                            try:
                                sock.sendall(_build_msg(oid, 1))  # source.destroy
                            except OSError:
                                pass
                return True

            # Enumerate globals
            init_done = False

            def _mark_init() -> None:
                nonlocal init_done
                init_done = True

            pending_syncs[3] = _mark_init
            sock.sendall(
                _build_msg(1, 1, struct.pack("<I", 2)) + _build_msg(1, 0, struct.pack("<I", 3))
            )
            deadline = time.monotonic() + 1.0
            while not init_done and time.monotonic() < deadline:
                rlist, _, _ = select.select([sock], [], [], 0.2)
                if rlist and (not pump_recv() or not dispatch_events()):
                    break

            mgr_iface = (
                "ext_data_control_manager_v1"
                if "ext_data_control_manager_v1" in globals_map
                else (
                    "zwlr_data_control_manager_v1"
                    if "zwlr_data_control_manager_v1" in globals_map
                    else None
                )
            )
            if not mgr_iface or "wl_seat" not in globals_map:
                ready_box[0] = False
                ready_evt.set()
                return

            seat_name, seat_ver = globals_map["wl_seat"]
            mgr_name, _mgr_ver = globals_map[mgr_iface]
            seat_id = alloc_id()
            mgr_id[0] = alloc_id()
            dev_id[0] = alloc_id()
            sync_dev_id = alloc_id()

            setup_done = False

            def _mark_setup() -> None:
                nonlocal setup_done
                setup_done = True

            pending_syncs[sync_dev_id] = _mark_setup
            bind_req = (
                _build_msg(
                    2,
                    0,
                    struct.pack("<I", seat_name)
                    + _pack_string("wl_seat")
                    + struct.pack("<II", min(seat_ver, 1), seat_id),
                )
                + _build_msg(
                    2,
                    0,
                    struct.pack("<I", mgr_name)
                    + _pack_string(mgr_iface)
                    + struct.pack("<II", 1, mgr_id[0]),
                )
                + _build_msg(mgr_id[0], 1, struct.pack("<II", dev_id[0], seat_id))
                + _build_msg(1, 0, struct.pack("<I", sync_dev_id))
            )
            sock.sendall(bind_req)

            deadline = time.monotonic() + 1.0
            while not setup_done and time.monotonic() < deadline:
                rlist, _, _ = select.select([sock], [], [], 0.2)
                if rlist and (not pump_recv() or not dispatch_events()):
                    break

            ready_box[0] = setup_done
            ready_evt.set()
            if not setup_done:
                return

            running = True
            while running:
                read_fds = [sock, wake_r, *active_reads.keys()]
                rlist, _, _ = select.select(read_fds, [], [], 0.25)

                if wake_r in rlist:
                    try:
                        while os.read(wake_r, 1024):
                            pass
                    except BlockingIOError:
                        pass

                    with self._lock:
                        queued = list(self._requests)
                        self._requests.clear()

                    for kind, arg, done_evt, res_box in queued:
                        if kind == "stop":
                            done_evt.set()
                            running = False
                            break
                        if kind == "write":
                            src_id = alloc_id()
                            sources[src_id] = str(arg).encode("utf-8")
                            payload = _build_msg(mgr_id[0], 0, struct.pack("<I", src_id))
                            for mime in _TEXT_MIMES[:4]:
                                payload += _build_msg(src_id, 0, _pack_string(mime))
                            payload += _build_msg(dev_id[0], 0, struct.pack("<I", src_id))
                            cb_id = alloc_id()

                            def _finish_write(
                                evt: threading.Event = done_evt, box: list[Any] = res_box
                            ) -> None:
                                box[0] = True
                                evt.set()

                            pending_syncs[cb_id] = _finish_write
                            sock.sendall(payload + _build_msg(1, 0, struct.pack("<I", cb_id)))
                        elif kind == "read":
                            cb_id = alloc_id()

                            def _start_read(
                                evt: threading.Event = done_evt, box: list[Any] = res_box
                            ) -> None:
                                offer_id = current_offer[0]
                                if not offer_id:
                                    box[0] = ""
                                    evt.set()
                                    return
                                mime = _pick_text_mime(offers.get(offer_id, []))
                                if not mime:
                                    box[0] = None
                                    evt.set()
                                    return
                                r_pipe, w_pipe = os.pipe()
                                os.set_blocking(r_pipe, False)
                                receive_msg = _build_msg(offer_id, 0, _pack_string(mime))
                                sock.sendmsg(
                                    [receive_msg],
                                    [
                                        (
                                            socket.SOL_SOCKET,
                                            socket.SCM_RIGHTS,
                                            array.array("i", [w_pipe]),
                                        )
                                    ],
                                )
                                os.close(w_pipe)
                                active_reads[r_pipe] = (
                                    bytearray(),
                                    evt,
                                    box,
                                    time.monotonic() + 1.5,
                                )

                            pending_syncs[cb_id] = _start_read
                            sock.sendall(_build_msg(1, 0, struct.pack("<I", cb_id)))

                if not running:
                    break

                if sock in rlist and (not pump_recv() or not dispatch_events()):
                    break

                now = time.monotonic()
                for r_pipe in list(active_reads.keys()):
                    chunk_buf, evt, box, pipe_deadline = active_reads[r_pipe]
                    finished = False
                    if r_pipe in rlist:
                        while True:
                            try:
                                chunk = os.read(r_pipe, 65536)
                            except BlockingIOError:
                                break
                            except OSError:
                                finished = True
                                break
                            if not chunk:
                                finished = True
                                break
                            chunk_buf.extend(chunk)
                    if not finished and now >= pipe_deadline:
                        finished = True
                    if finished:
                        active_reads.pop(r_pipe, None)
                        try:
                            os.close(r_pipe)
                        except OSError:
                            pass
                        box[0] = bytes(chunk_buf).decode("utf-8", errors="replace")
                        evt.set()

        except Exception as exc:
            logger.debug("Wayland data-control worker stopped: %s", exc)
            ready_box[0] = False
            ready_evt.set()
        finally:
            for r_pipe, (_cbuf, evt, box, _dl) in active_reads.items():
                try:
                    os.close(r_pipe)
                except OSError:
                    pass
                box[0] = None
                evt.set()
            for fd in fds:
                try:
                    os.close(fd)
                except OSError:
                    pass
            with self._lock:
                for _kind, _arg, evt, _box in self._requests:
                    evt.set()
                self._requests.clear()
                self._thread = None
                if self._wake_r == wake_r:
                    self._wake_r = None
                if self._wake_w == wake_w:
                    self._wake_w = None
            for pfd in (wake_r, wake_w):
                try:
                    os.close(pfd)
                except OSError:
                    pass
            try:
                sock.close()
            except OSError:
                pass


_GLOBAL_WAYLAND_CLIPBOARD = WaylandDataControlClient()


def write_wayland_clipboard(text: str) -> bool:
    """Writes text to the active Wayland session using native data-control."""
    return _GLOBAL_WAYLAND_CLIPBOARD.write_text(text)


def read_wayland_clipboard() -> str | None:
    """Reads text from the active Wayland session using native data-control."""
    return _GLOBAL_WAYLAND_CLIPBOARD.read_text()


def reset_wayland_clipboard() -> None:
    """Resets the persistent Wayland data-control clipboard connection."""
    _GLOBAL_WAYLAND_CLIPBOARD.reset()
