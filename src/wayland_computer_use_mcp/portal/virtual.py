"""Isolated nested/headless Wayland sandbox lifecycle for virtual display mode."""

from __future__ import annotations

import logging
import os
import shlex
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from wayland_computer_use_mcp.config import DEFAULT_VIRTUAL_CMD, Config
from wayland_computer_use_mcp.core.context import SessionContext, set_active_context

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
        self.compositor_cmd: str | None = None
        self.adapter: Any = None

    @property
    def is_running(self) -> bool:
        return self.compositor_proc is not None and self.compositor_proc.poll() is None

    @staticmethod
    def _find_created_wayland_socket(runtime_dir: str, exclude: str | None = None) -> str | None:
        """Detects a compositor-chosen Wayland socket (e.g. wayland-0 for sway/labwc)."""
        try:
            for entry in sorted(os.listdir(runtime_dir)):
                if not entry.startswith("wayland-") or entry.endswith(".lock"):
                    continue
                if exclude and entry == exclude:
                    continue
                full = os.path.join(runtime_dir, entry)
                if os.path.islink(full):
                    continue
                try:
                    st = os.stat(full)
                except OSError:
                    continue
                if stat.S_ISSOCK(st.st_mode):
                    return entry
        except OSError:
            pass
        return None

    def to_context(self, cfg: Config | None = None) -> SessionContext:
        """Builds an explicit SessionContext snapshot for the active virtual sandbox."""
        headed = cfg.virtual_headed if cfg is not None else False
        return SessionContext(
            mode="blackbox",
            xdg_runtime_dir=self.runtime_dir,
            dbus_address=self.dbus_address,
            wayland_display=self.wayland_display,
            atspi_bus_address=None,
            headed=headed,
            is_isolated=True,
        )

    def ensure_started(self, cfg: Config, adapter: Any | None = None) -> None:
        """Starts private XDG_RUNTIME_DIR, dbus-daemon, and nested/headless compositor."""
        if self.is_running:
            return

        current_display = os.environ.get("WAYLAND_DISPLAY")
        socket_name = cfg.virtual_wayland_display
        if current_display and current_display == self.wayland_display:
            return

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

        comp_env = os.environ.copy()
        comp_env["XDG_RUNTIME_DIR"] = private_xdg
        comp_env.pop("AT_SPI_BUS_ADDRESS", None)

        host_link_name: str | None = None
        if cfg.virtual_headed and host_wl:
            host_sock = host_wl if os.path.isabs(host_wl) else os.path.join(host_xdg, host_wl)
            host_link_name = f"host-{os.path.basename(host_wl)}"
            if os.path.exists(host_sock):
                try:
                    os.symlink(host_sock, os.path.join(private_xdg, host_link_name))
                except OSError as exc:
                    logger.debug("Failed symlinking host Wayland socket: %s", exc)
            if socket_name == host_wl or socket_name == host_link_name:
                socket_name = f"{cfg.virtual_wayland_display}-{os.getpid()}"

        # Spawn isolated session D-Bus daemon so host apps, AT-SPI, and portals stay isolated
        dbus_addr = self._start_private_dbus(private_xdg)
        self.dbus_address = dbus_addr

        if dbus_addr:
            comp_env["DBUS_SESSION_BUS_ADDRESS"] = dbus_addr
        if cfg.virtual_headed and host_link_name:
            comp_env["WAYLAND_DISPLAY"] = host_link_name
        else:
            comp_env.pop("WAYLAND_DISPLAY", None)
            comp_env.setdefault("WLR_BACKENDS", "headless")

        explicit = cfg.virtual_compositor_cmd.strip()
        is_auto = explicit.lower() in ("", DEFAULT_VIRTUAL_CMD)
        tokens = shlex.split(explicit) if explicit else []
        is_bare_compositor = len(tokens) == 1 and os.path.basename(tokens[0]) in (
            "kwin_wayland",
            "labwc",
            "sway",
            "weston",
        )
        if adapter is None:
            from wayland_computer_use_mcp.blackbox.adapters import select_blackbox_adapter

            adapter = select_blackbox_adapter(None if is_auto else explicit)
        self.adapter = adapter

        if is_auto or is_bare_compositor:
            empty_cfg_dir = os.path.join(private_xdg, "compositor-cfg")
            os.makedirs(empty_cfg_dir, exist_ok=True)
            cmd = adapter.build_command(
                headed=cfg.virtual_headed,
                socket_name=socket_name,
                host_wayland_display=host_link_name if cfg.virtual_headed else None,
                config_dir=empty_cfg_dir,
            )
            if not cmd:
                self.stop()
                raise RuntimeError(
                    "No nested Wayland compositor available for virtual mode. "
                    "Install 'labwc' (recommended, e.g. sudo apt install labwc) or 'sway' / "
                    "'weston'."
                )
        else:
            cmd = explicit

        self.compositor_cmd = cmd
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
                detected = self._find_created_wayland_socket(private_xdg)
                if detected:
                    socket_name = detected
                    socket_path = os.path.join(private_xdg, socket_name)
                    break
                if self.compositor_proc.poll() is not None:
                    break
                time.sleep(0.05)

            self.wayland_display = socket_name

            from wayland_computer_use_mcp.a11y.client import reset_atspi_cache
            from wayland_computer_use_mcp.compositors import reset_compositor_backend

            set_active_context(self.to_context(cfg))
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

    def stop(self) -> None:
        """Stops nested compositor, private D-Bus, and removes temp runtime dir."""
        _terminate_proc_group(self.compositor_proc)
        self.compositor_proc = None
        self.compositor_cmd = None
        self.adapter = None

        _terminate_proc_group(self.dbus_proc)
        self.dbus_proc = None

        self.dbus_address = None
        self.wayland_display = None
        set_active_context(None)

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
