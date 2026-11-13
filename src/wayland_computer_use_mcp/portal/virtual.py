"""Isolated nested/headless Wayland sandbox lifecycle for virtual display mode."""

from __future__ import annotations

import asyncio
import logging
import os
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from wayland_computer_use_mcp.config import Config, detect_virtual_compositor_cmd

logger = logging.getLogger(__name__)


def _terminate_proc_group(proc: subprocess.Popen[Any] | None) -> None:
    """Terminates a child process (and its process group when isolated) cleanly."""
    if proc is None:
        return
    pgid: int | None = None
    raw_pid = getattr(proc, "pid", None)
    if (
        isinstance(subprocess.Popen, type)
        and isinstance(proc, subprocess.Popen)
        and type(raw_pid) is int
        and raw_pid > 1
    ):
        try:
            candidate = os.getpgid(raw_pid)
            own_pgid = os.getpgid(0)
            if candidate > 1 and candidate != own_pgid:
                pgid = candidate
        except OSError:
            pgid = None
    try:
        if pgid is not None:
            os.killpg(pgid, 15)  # SIGTERM
        else:
            proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=1.5)
    except Exception:
        try:
            if pgid is not None:
                os.killpg(pgid, 9)  # SIGKILL
            else:
                proc.kill()
        except Exception:
            pass


def _unmount_doc_portal(runtime_dir: str) -> None:
    """Unmounts any FUSE document portal mount inside a temporary virtual runtime dir."""
    doc_dir = os.path.join(runtime_dir, "doc")
    if not os.path.exists(doc_dir):
        return
    for cmd in (
        ["fusermount3", "-u", "-z", doc_dir],
        ["fusermount", "-u", "-z", doc_dir],
        ["umount", "-l", doc_dir],
    ):
        if shutil.which(cmd[0]):
            try:
                subprocess.run(
                    cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=1.5,
                    check=False,
                )
            except Exception:
                pass


def _terminate_runtime_processes(runtime_dir: str) -> None:
    """Terminates detached D-Bus-activated helpers still bound to runtime_dir."""
    if not runtime_dir or not os.path.isdir(runtime_dir):
        return
    own_pid = os.getpid()
    own_uid = os.geteuid()
    marker = runtime_dir.encode("utf-8")
    victims: list[int] = []
    try:
        entries = os.listdir("/proc")
    except OSError:
        return

    for name in entries:
        if not name.isdigit():
            continue
        pid = int(name)
        if pid <= 1 or pid == own_pid:
            continue
        proc_dir = os.path.join("/proc", name)
        try:
            if os.stat(proc_dir).st_uid != own_uid:
                continue
            matched = False
            for fname in ("environ", "cmdline"):
                try:
                    with open(os.path.join(proc_dir, fname), "rb") as fh:
                        if marker in fh.read():
                            matched = True
                            break
                except OSError:
                    continue
            if matched:
                victims.append(pid)
        except OSError:
            continue

    for pid in victims:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

    if victims:
        deadline = time.monotonic() + 0.8
        remaining = set(victims)
        while remaining and time.monotonic() < deadline:
            for pid in list(remaining):
                try:
                    os.kill(pid, 0)
                except OSError:
                    remaining.discard(pid)
            if remaining:
                time.sleep(0.05)
        for pid in remaining:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass


def _cleanup_runtime_artifacts(runtime_dir: str) -> None:
    """Cleans up FUSE mounts, detached helper processes, and the temporary runtime dir."""
    if not runtime_dir:
        return
    _unmount_doc_portal(runtime_dir)
    _terminate_runtime_processes(runtime_dir)
    _unmount_doc_portal(runtime_dir)
    shutil.rmtree(runtime_dir, ignore_errors=True)


def cleanup_stale_virtual_dirs() -> None:
    """Removes orphaned /tmp/wayland_mcp_virtual_* directories left by dead MCP processes."""
    tmp_root = Path(tempfile.gettempdir())
    own_pid = os.getpid()
    for candidate in tmp_root.glob("wayland_mcp_virtual_*"):
        if not candidate.is_dir():
            continue
        pid_file = candidate / ".mcp_pid"
        if not pid_file.is_file():
            continue
        try:
            owner_pid = int(pid_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
        if owner_pid <= 1 or owner_pid == own_pid:
            continue
        try:
            os.kill(owner_pid, 0)
        except ProcessLookupError:
            _cleanup_runtime_artifacts(str(candidate))
        except OSError:
            pass


class VirtualSessionManager:
    """Manages an isolated XDG_RUNTIME_DIR, private D-Bus session, and nested compositor."""

    def __init__(self) -> None:
        self.runtime_dir: str | None = None
        self.dbus_address: str | None = None
        self.wayland_display: str | None = None
        self.dbus_proc: subprocess.Popen[Any] | None = None
        self.compositor_proc: subprocess.Popen[Any] | None = None
        self._eis_bus: Any = None
        self._saved_env: dict[str, str | None] = {}

    @property
    def is_running(self) -> bool:
        return self.compositor_proc is not None and self.compositor_proc.poll() is None

    def _save_env(self) -> None:
        if not self._saved_env:
            for key in (
                "XDG_RUNTIME_DIR",
                "DBUS_SESSION_BUS_ADDRESS",
                "WAYLAND_DISPLAY",
                "AT_SPI_BUS_ADDRESS",
            ):
                self._saved_env[key] = os.environ.get(key)

    def _restore_env(self) -> None:
        for key, prev in self._saved_env.items():
            if prev is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = prev
        self._saved_env.clear()

    def ensure_started(self, cfg: Config) -> None:
        """Starts private XDG_RUNTIME_DIR, dbus-daemon, and nested/headless compositor."""
        if self.is_running:
            return

        current_display = os.environ.get("WAYLAND_DISPLAY")
        socket_name = cfg.virtual_wayland_display
        if current_display and current_display == self.wayland_display:
            return

        self._save_env()
        cleanup_stale_virtual_dirs()
        host_xdg = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
        host_wl = current_display

        private_xdg = tempfile.mkdtemp(prefix="wayland_mcp_virtual_")
        try:
            os.chmod(private_xdg, 0o700)
        except OSError:
            pass
        try:
            Path(private_xdg, ".mcp_pid").write_text(str(os.getpid()), encoding="utf-8")
        except OSError:
            pass
        self.runtime_dir = private_xdg

        if cfg.virtual_headed and host_wl:
            host_sock = os.path.join(host_xdg, host_wl)
            if os.path.exists(host_sock):
                try:
                    os.symlink(host_sock, os.path.join(private_xdg, host_wl))
                except OSError as exc:
                    logger.debug("Failed symlinking host Wayland socket: %s", exc)
            if socket_name == host_wl:
                socket_name = f"{cfg.virtual_wayland_display}-{os.getpid()}"

        # Spawn isolated session D-Bus daemon so host apps, AT-SPI, and portals stay isolated
        dbus_addr = self._start_private_dbus(private_xdg)
        self.dbus_address = dbus_addr

        comp_env = os.environ.copy()
        comp_env["XDG_RUNTIME_DIR"] = private_xdg
        comp_env.pop("AT_SPI_BUS_ADDRESS", None)
        if dbus_addr:
            comp_env["DBUS_SESSION_BUS_ADDRESS"] = dbus_addr
        if cfg.virtual_headed and host_wl:
            comp_env["WAYLAND_DISPLAY"] = host_wl
        else:
            comp_env.pop("WAYLAND_DISPLAY", None)

        cmd = detect_virtual_compositor_cmd(
            headed=cfg.virtual_headed,
            socket_name=socket_name,
            explicit_cmd=cfg.virtual_compositor_cmd,
            host_wayland_display=host_wl if cfg.virtual_headed else None,
        )
        logger.info("Starting isolated virtual compositor (headed=%s): %s", cfg.virtual_headed, cmd)
        try:
            self.compositor_proc = subprocess.Popen(
                shlex.split(cmd),
                env=comp_env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            socket_path = os.path.join(private_xdg, socket_name)
            deadline = time.time() + 4.0
            while time.time() < deadline:
                if os.path.exists(socket_path):
                    break
                if self.compositor_proc.poll() is not None:
                    break
                time.sleep(0.05)

            self.wayland_display = socket_name
            os.environ["XDG_RUNTIME_DIR"] = private_xdg
            if dbus_addr:
                os.environ["DBUS_SESSION_BUS_ADDRESS"] = dbus_addr
            os.environ["WAYLAND_DISPLAY"] = socket_name
            os.environ.pop("AT_SPI_BUS_ADDRESS", None)

            from wayland_computer_use_mcp.a11y.client import reset_atspi_cache
            from wayland_computer_use_mcp.compositors import reset_compositor_backend

            reset_atspi_cache()
            reset_compositor_backend()
        except Exception as exc:
            logger.warning("Could not launch isolated virtual compositor: %s", exc)
            self.stop()
            raise RuntimeError(f"Failed to launch virtual compositor '{cmd}': {exc}") from exc

    def _start_private_dbus(self, private_xdg: str) -> str | None:
        if not shutil.which("dbus-daemon"):
            logger.warning("dbus-daemon not found; virtual session will share host D-Bus bus.")
            return None
        dbus_env = os.environ.copy()
        dbus_env["XDG_RUNTIME_DIR"] = private_xdg
        dbus_env.pop("AT_SPI_BUS_ADDRESS", None)
        dbus_env.pop("DBUS_SESSION_BUS_ADDRESS", None)
        proc = subprocess.Popen(
            ["dbus-daemon", "--session", "--nofork", "--print-address=1"],
            env=dbus_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            start_new_session=True,
        )
        self.dbus_proc = proc
        if proc.stdout is not None:
            deadline = time.time() + 2.0
            while time.time() < deadline:
                line = proc.stdout.readline().strip()
                if line:
                    return line
                if proc.poll() is not None:
                    break
                time.sleep(0.02)
        return None

    async def connect_direct_eis(self) -> tuple[int | None, str]:
        """Connects directly to an isolated nested compositor's EIS service when supported."""
        if not self.dbus_address:
            return None, "no_private_dbus"

        from dbus_fast import Message, MessageType
        from dbus_fast.aio import MessageBus

        if self._eis_bus is not None:
            try:
                self._eis_bus.disconnect()
            except Exception:
                pass
            self._eis_bus = None

        bus = await MessageBus(bus_address=self.dbus_address, negotiate_unix_fd=True).connect()
        keep_bus = False
        try:
            kwin_ready = False
            for _ in range(30):
                reply = await bus.call(
                    Message(
                        destination="org.freedesktop.DBus",
                        path="/org/freedesktop/DBus",
                        interface="org.freedesktop.DBus",
                        member="NameHasOwner",
                        signature="s",
                        body=["org.kde.KWin"],
                    )
                )
                if reply.body and reply.body[0]:
                    kwin_ready = True
                    break
                if not self.is_running:
                    break
                await asyncio.sleep(0.1)

            if kwin_ready:
                eis_reply = await asyncio.wait_for(
                    bus.call(
                        Message(
                            destination="org.kde.KWin",
                            path="/org/kde/KWin/EIS/RemoteDesktop",
                            interface="org.kde.KWin.EIS.RemoteDesktop",
                            member="connectToEIS",
                            signature="i",
                            body=[7],  # Pointer | AbsolutePointer | Keyboard
                        )
                    ),
                    timeout=3.0,
                )
                if eis_reply.message_type != MessageType.ERROR and eis_reply.unix_fds:
                    # Keep the D-Bus peer connected because KWin tears down EIS when
                    # the requesting D-Bus client disconnects (NameOwnerChanged).
                    self._eis_bus = bus
                    keep_bus = True
                    return os.dup(int(eis_reply.unix_fds[0])), "kwin_eis"
                logger.warning(
                    "Nested KWin connectToEIS failed: %s %s",
                    eis_reply.error_name,
                    eis_reply.body,
                )
                return None, "kwin_no_eis"

            return None, "generic_isolated"
        finally:
            if not keep_bus:
                bus.disconnect()

    def stop(self) -> None:
        """Stops nested compositor & private D-Bus, restores env, and removes temp runtime dir."""
        if self._eis_bus is not None:
            try:
                self._eis_bus.disconnect()
            except Exception:
                pass
            self._eis_bus = None

        _terminate_proc_group(self.compositor_proc)
        self.compositor_proc = None

        _terminate_proc_group(self.dbus_proc)
        self.dbus_proc = None

        self.dbus_address = None
        self.wayland_display = None
        self._restore_env()

        if self.runtime_dir:
            _cleanup_runtime_artifacts(self.runtime_dir)
            self.runtime_dir = None

        try:
            from wayland_computer_use_mcp.a11y.client import reset_atspi_cache
            from wayland_computer_use_mcp.compositors import reset_compositor_backend
            from wayland_computer_use_mcp.wayland_clipboard import reset_wayland_clipboard

            reset_atspi_cache()
            reset_compositor_backend()
            reset_wayland_clipboard()
        except Exception:
            pass
