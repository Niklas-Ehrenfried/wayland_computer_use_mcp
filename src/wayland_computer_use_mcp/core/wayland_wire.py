"""Minimal shared Wayland UNIX-socket wire encoding primitives."""

from __future__ import annotations

import array
import ctypes
import os
import select
import socket
import struct
import tempfile


def pack_string(value: str) -> bytes:
    """Packs a NUL-terminated Wayland wire string padded to a 4-byte boundary."""
    encoded = value.encode("utf-8") + b"\x00"
    pad = (4 - (len(encoded) % 4)) % 4
    return struct.pack("<I", len(encoded)) + encoded + (b"\x00" * pad)


def unpack_string(body: bytes, offset: int = 0) -> tuple[str, int]:
    """Unpacks a Wayland wire string and returns (decoded_text, next_offset)."""
    (slen,) = struct.unpack_from("<I", body, offset)
    raw = body[offset + 4 : offset + 4 + max(0, slen - 1)]
    pad = (4 - (slen % 4)) % 4
    return raw.decode("utf-8", errors="replace"), offset + 4 + slen + pad


def build_msg(obj_id: int, opcode: int, payload: bytes = b"") -> bytes:
    """Constructs a raw Wayland protocol packet header + payload."""
    size = 8 + len(payload)
    return struct.pack("<II", obj_id, (size << 16) | opcode) + payload


def bind_global_req(
    registry_id: int, global_name: int, iface: str, version: int, new_id: int
) -> bytes:
    """Encodes a wl_registry.bind request (opcode 0)."""
    return build_msg(
        registry_id,
        0,
        struct.pack("<I", global_name) + pack_string(iface) + struct.pack("<II", version, new_id),
    )


def create_shm_fd(name: str, size: int = 0) -> int:
    """Creates an anonymous shared-memory file descriptor with CLOEXEC semantics."""
    fd: int | None = None
    mfd = getattr(os, "memfd_create", None)
    if callable(mfd):
        fd = int(mfd(name, getattr(os, "MFD_CLOEXEC", 1)))
    else:
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            fn = getattr(libc, "memfd_create", None)
            if fn is not None:
                fn.argtypes = [ctypes.c_char_p, ctypes.c_uint]
                fn.restype = ctypes.c_int
                raw_fd = fn(name.encode("utf-8", errors="ignore"), 1)
                if raw_fd >= 0:
                    fd = int(raw_fd)
        except Exception:
            fd = None

    if fd is None:
        shm_dir = "/dev/shm" if os.path.isdir("/dev/shm") else None
        tmp_fd, path = tempfile.mkstemp(prefix=f"{name}_", dir=shm_dir)
        try:
            os.unlink(path)
        except OSError:
            pass
        fd = tmp_fd

    if size > 0:
        os.ftruncate(fd, size)
    return fd


def send_with_fd(sock: socket.socket, payload: bytes, fd: int) -> None:
    """Sends a Wayland wire message carrying a single SCM_RIGHTS file descriptor."""
    sock.sendmsg(
        [payload],
        [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [fd]))],
    )


def connect_and_discover_globals(
    sock_path: str, timeout: float = 1.0
) -> tuple[socket.socket, dict[str, tuple[int, int]], bytearray, list[int], int]:
    """Connects to a Wayland socket, enumerates wl_registry globals, and returns state."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(sock_path)
    sock.setblocking(False)

    buf = bytearray()
    fds: list[int] = []
    globals_map: dict[str, tuple[int, int]] = {}
    synced = False

    sock.sendall(build_msg(1, 1, struct.pack("<I", 2)) + build_msg(1, 0, struct.pack("<I", 3)))
    deadline = __import__("time").monotonic() + timeout
    while not synced and __import__("time").monotonic() < deadline:
        rlist, _, _ = select.select([sock], [], [], 0.2)
        if not rlist:
            continue
        data, ancdata, _, _ = sock.recvmsg(65536, socket.CMSG_SPACE(16 * 4))
        for level, ctype, cdata in ancdata:
            if level == socket.SOL_SOCKET and ctype == socket.SCM_RIGHTS:
                fds.extend(array.array("i", cdata))
        if not data and not ancdata:
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
            if oid == 2 and op == 0:
                (name,) = struct.unpack_from("<I", body, 0)
                iface, off = unpack_string(body, 4)
                (ver,) = struct.unpack_from("<I", body, off)
                globals_map[iface] = (name, ver)
            elif oid == 3 and op == 0:
                synced = True

    for fd in fds:
        try:
            os.close(fd)
        except OSError:
            pass
    fds.clear()
    return sock, globals_map, buf, fds, 4
