"""Wayland ScreenCast & RemoteDesktop portal pipeline.

Manages:
- D-Bus communication with org.freedesktop.portal.Desktop
- ScreenCast + RemoteDesktop session pairing & restore-token persistence
- PipeWire stream capture via GStreamer appsink
- Surface coordinate bounds updates in CoordinateClamper
- Boundary-clamped input injection (click, drag, scroll, type_text) via libei
- Live vs Virtual display mode orchestration
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from dbus_fast import Message, MessageType
from PIL import Image, ImageDraw, ImageFont

from wayland_computer_use_mcp.config import get_config
from wayland_computer_use_mcp.libei import (
    BTN_LEFT,
    BTN_MIDDLE,
    BTN_RIGHT,
    KEY_LEFTCTRL,
    KEY_V,
    EIClient,
)
from wayland_computer_use_mcp.security import (
    global_clamper,
    global_geometry_detector,
    global_preemption_manager,
    global_shortcut_filter,
    global_token_store,
)

logger = logging.getLogger(__name__)

# Try importing GStreamer via PyGObject
_GST_AVAILABLE = False
try:
    import gi

    gi.require_version("Gst", "1.0")
    from gi.repository import Gst  # type: ignore

    Gst.init(None)
    _GST_AVAILABLE = True
except Exception as exc:
    logger.warning("GStreamer PyGObject bindings unavailable: %s", exc)


DEFAULT_SAVE_DIR = "/home/niklas/.gemini/antigravity/brain/539f5e12-8178-4a5e-b7b9-b96af1210fa6"


def _debug_log(msg: str) -> None:
    logger.info(msg)
    save_dir_env = os.environ.get("WAYLAND_MCP_SAVE_FRAMES_DIR") or DEFAULT_SAVE_DIR
    if save_dir_env:
        try:
            p = Path(save_dir_env) / "portal_debug.log"
            with open(p, "a", encoding="utf-8") as f:
                f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
        except Exception:
            pass


class AsyncLoopThread:
    """Dedicated thread running an asyncio event loop for persistent D-Bus connections."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run_loop, name="PortalAsyncLoop", daemon=True)
        self.thread.start()

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def run(self, coro: Any, timeout: float = 120.0) -> Any:
        fut = asyncio.run_coroutine_threadsafe(coro, self.loop)
        return fut.result(timeout=timeout)


class PortalSession:
    """Manages active ScreenCast and RemoteDesktop session via XDG portals and PipeWire."""

    PORTAL_BUS = "org.freedesktop.portal.Desktop"
    PORTAL_PATH = "/org/freedesktop/portal/desktop"

    def __init__(self) -> None:
        self.session_handle: str | None = None
        self.pipewire_node_id: int | None = None
        self.eis_fd: int | None = None
        self.ei_client: EIClient | None = None
        self.restore_token: str | None = None
        self.active_app_id: str = "default"
        self.target_pid: int | None = None
        self.target_title: str | None = None
        self.window_offset_x: int = 0
        self.window_offset_y: int = 0
        self.last_saved_frame_path: str | None = None
        self.last_saved_labeled_path: str | None = None

        self.bus: Any = None
        self._async_thread = AsyncLoopThread()
        self._gst_pipeline: Any = None
        self._gst_appsink: Any = None
        self._last_frame: Image.Image | None = None
        self._lock = threading.Lock()
        self._initialized = False
        self._is_mock = False

    @property
    def is_mock(self) -> bool:
        return (
            self._is_mock
            or self.pipewire_node_id == 99999
            or get_config().mock_mode
            or not os.environ.get("WAYLAND_DISPLAY")
        )

    def is_active(self) -> bool:
        return self._initialized and (self.pipewire_node_id is not None or self.is_mock)

    def check_active_app(self, pid: int | None = None) -> None:
        """Verifies that an application process is actively running and responsive."""
        if self.is_mock:
            return
        effective_pid = pid or self.target_pid
        if not effective_pid:
            raise RuntimeError(
                "No active managed application. "
                "Please launch an application first using launch_app."
            )
        from wayland_computer_use_mcp.process import is_responsive

        if not is_responsive(effective_pid):
            raise RuntimeError(
                f"Application with PID {effective_pid} is not running or responsive. "
                "Please launch an application first using launch_app."
            )

    def ensure_initialized(self, app_id: str | None = None) -> None:
        """Synchronously ensures session is initialized using the persistent async thread."""
        with self._lock:
            if self._initialized:
                return
        self._async_thread.run(self.initialize(app_id=app_id))

    async def initialize(self, app_id: str | None = None) -> None:
        """Initialize or restore ScreenCast and RemoteDesktop portal session."""
        with self._lock:
            if self._initialized:
                return

        cfg = get_config()
        self.active_app_id = app_id or "default"

        # Check virtual display mode
        if cfg.display_mode == "virtual":
            self._ensure_virtual_compositor()

        # If mock mode or test environment without Wayland display
        if cfg.mock_mode or not os.environ.get("WAYLAND_DISPLAY"):
            logger.info("Initializing portal session in mock/emulated mode.")
            self._init_mock_session()
            return

        self._last_portal_error: str | None = None
        try:
            await self._negotiate_portal_dbus()
        except Exception as exc:
            import traceback

            self._last_portal_error = f"{type(exc).__name__}: {exc} | {traceback.format_exc()}"
            logger.warning("Portal D-Bus negotiation failed: %s", exc)
            if cfg.mock_mode or isinstance(exc, FileNotFoundError):
                logger.info("D-Bus socket unavailable; using mock session.")
                self._init_mock_session()
            else:
                self._initialized = False
                raise RuntimeError(
                    f"RemoteDesktop / ScreenCast portal negotiation failed: {exc}. "
                    "Please ensure the OS permission prompt was allowed."
                ) from exc

    def _ensure_virtual_compositor(self) -> None:
        """Verify or spawn a virtual Wayland compositor when in virtual mode."""
        cfg = get_config()
        current_display = os.environ.get("WAYLAND_DISPLAY")
        if not current_display or current_display != cfg.virtual_wayland_display:
            logger.info("Starting virtual compositor: %s", cfg.virtual_compositor_cmd)
            try:
                subprocess.Popen(
                    cfg.virtual_compositor_cmd,
                    shell=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                time.sleep(1.0)
                os.environ["WAYLAND_DISPLAY"] = cfg.virtual_wayland_display
            except Exception as exc:
                logger.warning("Could not launch virtual compositor: %s", exc)

    def _init_mock_session(self) -> None:
        """Initialize mock session for CI and testing."""
        self._initialized = True
        self._is_mock = True
        self.pipewire_node_id = 99999
        self.ei_client = EIClient(fd=None)
        # Create default mock frame (1920x1080)
        img = Image.new("RGBA", (1920, 1080), color=(45, 52, 54, 255))
        draw = ImageDraw.Draw(img)
        draw.rectangle([50, 50, 600, 400], fill=(9, 132, 227, 255), outline=(255, 255, 255, 255))
        draw.text((70, 70), "wayland-computer-use-mcp Mock Surface", fill=(255, 255, 255, 255))
        self._last_frame = img
        global_clamper.set_bounds(1920, 1080)
        global_geometry_detector.record_capture(1920, 1080)

    async def _negotiate_portal_dbus(self) -> None:
        """Perform XDG portal session handshake via dbus-fast."""
        from dbus_fast import BusType, Message, MessageType, Variant
        from dbus_fast.aio import MessageBus

        _debug_log("Starting portal D-Bus negotiation...")
        self.bus = await MessageBus(bus_type=BusType.SESSION).connect()
        bus = self.bus
        cfg = get_config()
        sender_id = bus.unique_name[1:].replace(".", "_")

        # Add match rule for ALL portal Request signals for this connection
        await bus.call(
            Message(
                destination="org.freedesktop.DBus",
                path="/org/freedesktop/DBus",
                interface="org.freedesktop.DBus",
                member="AddMatch",
                signature="s",
                body=["type='signal',interface='org.freedesktop.portal.Request'"],
            )
        )

        futures: dict[str, asyncio.Future[Any]] = {}

        def on_msg(msg: Any) -> None:
            if msg.path and msg.member == "Response" and msg.path in futures:
                if not futures[msg.path].done():
                    futures[msg.path].set_result(msg.body)

        bus.add_message_handler(on_msg)

        async def call_and_await(
            dest: str,
            path: str,
            iface: str,
            member: str,
            sig: str,
            body: list[Any],
            req_token: str,
            timeout: float = 60.0,
        ) -> Any:
            req_path = f"/org/freedesktop/portal/desktop/request/{sender_id}/{req_token}"
            fut = asyncio.get_running_loop().create_future()
            futures[req_path] = fut
            reply = await bus.call(
                Message(
                    destination=dest,
                    path=path,
                    interface=iface,
                    member=member,
                    signature=sig,
                    body=body,
                )
            )
            if reply.message_type == MessageType.ERROR:
                futures.pop(req_path, None)
                raise RuntimeError(f"Portal {member} call error: {reply.body}")

            actual_req_path = reply.body[0]
            if actual_req_path != req_path:
                futures[actual_req_path] = fut

            keep_above_task = None
            if member == "Start":
                from wayland_computer_use_mcp.kwin import ensure_portal_dialogs_above

                async def _keep_dialogs_above() -> None:
                    while True:
                        try:
                            ensure_portal_dialogs_above()
                        except Exception:
                            pass
                        await asyncio.sleep(0.5)

                keep_above_task = asyncio.create_task(_keep_dialogs_above())

            try:
                res = await asyncio.wait_for(fut, timeout=timeout)
                status_code = res[0]
                results = res[1] if len(res) > 1 else {}
                if status_code != 0:
                    raise RuntimeError(
                        f"Portal {member} denied/cancelled by user (code {status_code})"
                    )
                unpacked: dict[str, Any] = {}
                for k, v in results.items():
                    unpacked[k] = v.value if hasattr(v, "value") else v
                return unpacked
            finally:
                if keep_above_task:
                    keep_above_task.cancel()
                futures.pop(req_path, None)
                futures.pop(actual_req_path, None)

        ts = int(time.time())
        cached_token = global_token_store.get_token(self.active_app_id)

        # 1. CreateSession
        _debug_log("Calling RemoteDesktop.CreateSession...")
        s_data = await call_and_await(
            self.PORTAL_BUS,
            self.PORTAL_PATH,
            "org.freedesktop.portal.RemoteDesktop",
            "CreateSession",
            "a{sv}",
            [
                {
                    "session_handle_token": Variant("s", f"s_{ts}"),
                    "handle_token": Variant("s", f"r_create_{ts}"),
                }
            ],
            f"r_create_{ts}",
        )
        self.session_handle = s_data["session_handle"]
        _debug_log(f"CreateSession OK: {self.session_handle}")

        # 2. SelectDevices (Pointer | Keyboard)
        select_devices_opts: dict[str, Any] = {
            "types": Variant("u", 1 | 2),
            "handle_token": Variant("s", f"r_dev_{ts}"),
        }
        if cached_token:
            select_devices_opts["restore_token"] = Variant("s", cached_token)

        _debug_log("Calling SelectDevices...")
        await call_and_await(
            self.PORTAL_BUS,
            self.PORTAL_PATH,
            "org.freedesktop.portal.RemoteDesktop",
            "SelectDevices",
            "oa{sv}",
            [self.session_handle, select_devices_opts],
            f"r_dev_{ts}",
        )
        _debug_log("SelectDevices OK")

        # 3. SelectSources for ScreenCast
        select_sources_opts: dict[str, Any] = {
            "types": Variant("u", cfg.source_type),
            "multiple": Variant("b", False),
            "handle_token": Variant("s", f"r_src_{ts}"),
        }
        if cached_token:
            select_sources_opts["restore_token"] = Variant("s", cached_token)

        _debug_log("Calling SelectSources...")
        await call_and_await(
            self.PORTAL_BUS,
            self.PORTAL_PATH,
            "org.freedesktop.portal.ScreenCast",
            "SelectSources",
            "oa{sv}",
            [self.session_handle, select_sources_opts],
            f"r_src_{ts}",
        )
        _debug_log("SelectSources OK")

        # 4. Start session (wait up to 120s for user prompt)
        _debug_log("Calling Start... awaiting user prompt approval...")
        start_opts: dict[str, Any] = {"handle_token": Variant("s", f"r_start_{ts}")}
        start_results = await call_and_await(
            self.PORTAL_BUS,
            self.PORTAL_PATH,
            "org.freedesktop.portal.RemoteDesktop",
            "Start",
            "osa{sv}",
            [self.session_handle, "", start_opts],
            f"r_start_{ts}",
            timeout=120.0,
        )
        _debug_log(f"Start OK: keys={list(start_results.keys())}")

        streams = start_results.get("streams", [])
        if streams and len(streams) > 0:
            s0 = streams[0]
            if hasattr(s0, "value"):
                s0 = s0.value
            self.pipewire_node_id = int(s0[0])
            _debug_log(f"PipeWire Node ID resolved: {self.pipewire_node_id}")

        new_restore_token = start_results.get("restore_token")
        if new_restore_token:
            self.restore_token = str(new_restore_token)
            global_token_store.set_token(self.active_app_id, self.restore_token)

        # 5. Connect to EIS for input injection on user's seat (seat0)
        _debug_log("Connecting to EIS for input injection on user's seat...")
        oeffis_fd = self._connect_eis_via_oeffis()
        if oeffis_fd is not None and oeffis_fd >= 0:
            self.eis_fd = oeffis_fd
            self.ei_client = EIClient(fd=self.eis_fd)
            _debug_log(
                f"EIS connected fd={self.eis_fd} via liboeffis (primary seat), "
                f"pointer={self.ei_client.pointer_device is not None}, "
                f"keyboard={self.ei_client.keyboard_device is not None}"
            )
        else:
            _debug_log("liboeffis unavailable; attempting D-Bus ConnectToEIS fallback...")
            try:
                eis_reply = await asyncio.wait_for(
                    bus.call(
                        Message(
                            destination=self.PORTAL_BUS,
                            path=self.PORTAL_PATH,
                            interface="org.freedesktop.portal.RemoteDesktop",
                            member="ConnectToEIS",
                            signature="oa{sv}",
                            body=[self.session_handle, {}],
                        )
                    ),
                    timeout=2.0,
                )
                if eis_reply.message_type != MessageType.ERROR and eis_reply.unix_fds:
                    self.eis_fd = eis_reply.unix_fds[0]
                    self.ei_client = EIClient(fd=self.eis_fd)
                    _debug_log(f"Connected to EIS fd={self.eis_fd} via D-Bus")
                else:
                    self.ei_client = EIClient(fd=None)
            except Exception as exc:
                _debug_log(f"ConnectToEIS fallback finished: {exc}")
                self.ei_client = EIClient(fd=None)

        # 6. Initialize GStreamer PipeWire pipeline
        if self.pipewire_node_id and _GST_AVAILABLE:
            _debug_log(f"Starting GStreamer pipeline for PipeWire node {self.pipewire_node_id}...")
            self._setup_gstreamer_pipeline(self.pipewire_node_id)
            _debug_log("GStreamer pipeline started!")

        self._initialized = True
        _debug_log("Portal session fully initialized!")

    def _setup_gstreamer_pipeline(self, node_id: int) -> None:
        """Build and start minimal GStreamer PipeWire appsink pipeline."""
        pipeline_desc = (
            f"pipewiresrc path={node_id} keepalive-time=1000 resend-last=true ! "
            f"videoconvert ! video/x-raw,format=RGBA ! "
            f"appsink name=sink emit-signals=True max-buffers=1 drop=True"
        )
        try:
            self._gst_pipeline = Gst.parse_launch(pipeline_desc)
            self._gst_appsink = self._gst_pipeline.get_by_name("sink")
            self._gst_pipeline.set_state(Gst.State.PLAYING)
        except Exception as exc:
            logger.warning("GStreamer pipeline initialization failed: %s", exc)

    # --- D-Bus RemoteDesktop Notify* input helpers ---

    def _notify_pointer_motion_absolute(self, x: float, y: float) -> None:
        """Move cursor to absolute screen coordinates via D-Bus portal."""
        if not self.bus or not self.session_handle:
            logger.warning("Cannot move pointer: no active portal session")
            return
        stream = self.pipewire_node_id or 0
        try:
            reply = self._async_thread.run(
                self.bus.call(
                    Message(
                        destination=self.PORTAL_BUS,
                        path=self.PORTAL_PATH,
                        interface="org.freedesktop.portal.RemoteDesktop",
                        member="NotifyPointerMotionAbsolute",
                        signature="oa{sv}udd",
                        body=[self.session_handle, {}, stream, x, y],
                    )
                )
            )
            if reply and reply.message_type == MessageType.ERROR:
                _debug_log(f"NotifyPointerMotionAbsolute error: {reply.error_name}: {reply.body}")
        except Exception as exc:
            _debug_log(f"NotifyPointerMotionAbsolute exception: {exc}")

    def _notify_pointer_button(self, button_code: int, pressed: bool) -> None:
        """Press or release a mouse button via D-Bus portal."""
        if not self.bus or not self.session_handle:
            logger.warning("Cannot click: no active portal session")
            return
        state = 1 if pressed else 0
        try:
            reply = self._async_thread.run(
                self.bus.call(
                    Message(
                        destination=self.PORTAL_BUS,
                        path=self.PORTAL_PATH,
                        interface="org.freedesktop.portal.RemoteDesktop",
                        member="NotifyPointerButton",
                        signature="oa{sv}iu",
                        body=[self.session_handle, {}, button_code, state],
                    )
                )
            )
            if reply and reply.message_type == MessageType.ERROR:
                _debug_log(f"NotifyPointerButton error: {reply.error_name}: {reply.body}")
        except Exception as exc:
            _debug_log(f"NotifyPointerButton exception: {exc}")

    def _notify_pointer_axis(self, dx: float, dy: float) -> None:
        """Dispatch scroll deltas via D-Bus portal."""
        if not self.bus or not self.session_handle:
            return
        try:
            reply = self._async_thread.run(
                self.bus.call(
                    Message(
                        destination=self.PORTAL_BUS,
                        path=self.PORTAL_PATH,
                        interface="org.freedesktop.portal.RemoteDesktop",
                        member="NotifyPointerAxis",
                        signature="oa{sv}dd",
                        body=[self.session_handle, {}, dx, dy],
                    )
                )
            )
            if reply and reply.message_type == MessageType.ERROR:
                _debug_log(f"NotifyPointerAxis error: {reply.error_name}: {reply.body}")
        except Exception as exc:
            _debug_log(f"NotifyPointerAxis exception: {exc}")

    def _notify_keyboard_keycode(self, keycode: int, pressed: bool) -> None:
        """Press or release a keyboard key via D-Bus portal."""
        if not self.bus or not self.session_handle:
            return
        state = 1 if pressed else 0
        self._async_thread.run(
            self.bus.call(
                Message(
                    destination=self.PORTAL_BUS,
                    path=self.PORTAL_PATH,
                    interface="org.freedesktop.portal.RemoteDesktop",
                    member="NotifyKeyboardKeycode",
                    signature="oa{sv}iu",
                    body=[self.session_handle, {}, keycode, state],
                )
            )
        )

    def _connect_eis_via_oeffis(self) -> int | None:
        """Connects to EIS via system liboeffis to get an input injection fd."""
        try:
            import ctypes
            import select

            c = ctypes.CDLL("liboeffis.so.1")
            c.oeffis_new.argtypes = [ctypes.c_void_p]
            c.oeffis_new.restype = ctypes.c_void_p
            c.oeffis_create_session.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
            c.oeffis_create_session.restype = None
            c.oeffis_dispatch.argtypes = [ctypes.c_void_p]
            c.oeffis_dispatch.restype = ctypes.c_int
            c.oeffis_get_fd.argtypes = [ctypes.c_void_p]
            c.oeffis_get_fd.restype = ctypes.c_int
            c.oeffis_get_eis_fd.argtypes = [ctypes.c_void_p]
            c.oeffis_get_eis_fd.restype = ctypes.c_int
            c.oeffis_get_error_message.argtypes = [ctypes.c_void_p]
            c.oeffis_get_error_message.restype = ctypes.c_char_p
            c.oeffis_unref.argtypes = [ctypes.c_void_p]
            c.oeffis_unref.restype = ctypes.c_void_p

            ctx = c.oeffis_new(None)
            if not ctx:
                return None
            # Request Pointer | Keyboard (devices=3)
            c.oeffis_create_session(ctx, 3)

            poll_fd = c.oeffis_get_fd(ctx)
            _debug_log(f"liboeffis: waiting for permission (poll_fd={poll_fd})...")
            deadline = time.time() + 15.0
            from wayland_computer_use_mcp.kwin import ensure_portal_dialogs_above

            while time.time() < deadline:
                try:
                    ensure_portal_dialogs_above()
                except Exception:
                    pass
                r, _, _ = select.select([poll_fd], [], [], 0.2)
                if r:
                    c.oeffis_dispatch(ctx)
                fd = c.oeffis_get_eis_fd(ctx)
                if fd >= 0:
                    _debug_log(f"liboeffis connected: eis_fd={fd}")
                    return fd
                err = c.oeffis_get_error_message(ctx)
                if err:
                    _debug_log(f"liboeffis error: {err.decode('utf-8', errors='replace')}")
                    break
            _debug_log("liboeffis: timed out waiting for EIS fd")
            c.oeffis_unref(ctx)
        except Exception as exc:
            _debug_log(f"liboeffis attempt failed: {exc}")
        return None

    def _resolve_target_title(self) -> str | None:
        """Resolves target window title from AT-SPI accessible tree if not already set."""
        if self.target_title:
            return self.target_title
        if not self.target_pid:
            return None
        try:
            from wayland_computer_use_mcp.a11y import get_application_tree

            tree = get_application_tree(self.target_pid)
            for child in tree.get("children", []):
                if child.get("role") in ("window", "frame") and child.get("name"):
                    self.target_title = child.get("name")
                    return self.target_title
            name = tree.get("name", "")
            if name and not name.endswith(".py"):
                self.target_title = name
                return self.target_title
            return None
        except Exception:
            return None

    def capture_frame(self, crop_box: list[int] | None = None) -> Image.Image:
        """Retrieves the latest video frame as a PIL.Image, updating bounds and cropping."""
        self.check_active_app()
        if not self._initialized:
            cfg = get_config()
            if cfg.mock_mode or not os.environ.get("WAYLAND_DISPLAY"):
                self._init_mock_session()
            else:
                raise RuntimeError(
                    "Portal session is not initialized. "
                    + (f"Last error: {self._last_portal_error}" if self._last_portal_error else "")
                )

        geom: tuple[int, int, int, int] | None = None
        if self.target_pid:
            from wayland_computer_use_mcp.kwin import query_kwin_geometry

            title = self._resolve_target_title()
            geom = query_kwin_geometry(pid=self.target_pid, title=title)
            if not geom:
                try:
                    from wayland_computer_use_mcp.a11y import get_application_tree

                    tree = get_application_tree(self.target_pid)
                    sb = tree.get("screen_bounds") or tree.get("bounds")
                    if sb and len(sb) >= 4 and sb[2] > 0 and sb[3] > 0:
                        geom = (int(sb[0]), int(sb[1]), int(sb[2]), int(sb[3]))
                except Exception:
                    pass

            if geom:
                wx, wy, ww, wh = geom
                self.window_offset_x = wx
                self.window_offset_y = wy
                global_clamper.set_bounds(ww, wh)
                global_geometry_detector.record_capture(ww, wh, wx, wy)
            time.sleep(0.05)

        frame: Image.Image | None = None

        if self._gst_appsink and _GST_AVAILABLE:
            try:
                sample = None
                for _ in range(10):
                    sample = self._gst_appsink.emit("try-pull-sample", 100000000)
                    if sample:
                        break
                if sample:
                    buf = sample.get_buffer()
                    caps = sample.get_caps()
                    structure = caps.get_structure(0)
                    w = structure.get_value("width")
                    h = structure.get_value("height")
                    success, map_info = buf.map(Gst.MapFlags.READ)
                    if success:
                        fmt = structure.get_value("format") if structure.has_field("format") else "RGBA"
                        if fmt == "RGBA":
                            frame = Image.frombytes("RGBA", (w, h), map_info.data, "raw", "RGBA").convert("RGB")
                        elif fmt == "BGRA":
                            frame = Image.frombytes("RGBA", (w, h), map_info.data, "raw", "BGRA").convert("RGB")
                        elif fmt == "BGRx":
                            frame = Image.frombytes("RGB", (w, h), map_info.data, "raw", "BGRX")
                        elif fmt == "RGBx":
                            frame = Image.frombytes("RGB", (w, h), map_info.data, "raw", "RGBX")
                        elif fmt == "RGB":
                            frame = Image.frombytes("RGB", (w, h), map_info.data, "raw", "RGB")
                        elif fmt == "BGR":
                            frame = Image.frombytes("RGB", (w, h), map_info.data, "raw", "BGR")
                        else:
                            frame = Image.frombytes("RGBA", (w, h), map_info.data, "raw", "RGBA").convert("RGB")
                        buf.unmap(map_info)
            except Exception as exc:
                logger.debug("Failed to pull GStreamer sample: %s", exc)

        if frame is None:
            if self._last_frame is not None:
                frame = self._last_frame.copy()
            else:
                frame = Image.new("RGBA", (1920, 1080), color=(40, 44, 52, 255))

        self._last_frame = frame

        if not self.target_pid or not geom:
            global_clamper.set_bounds(frame.width, frame.height)
            global_geometry_detector.record_capture(frame.width, frame.height, 0, 0)

        result_frame: Image.Image
        if crop_box:
            result_frame = self.crop_element(frame, *crop_box)
        elif self.target_pid and geom:
            wx, wy, ww, wh = geom
            result_frame = self.crop_element(frame, wx, wy, ww, wh)
        else:
            result_frame = frame

        save_dir_env = os.environ.get("WAYLAND_MCP_SAVE_FRAMES_DIR") or DEFAULT_SAVE_DIR
        try:
            save_dir = Path(save_dir_env)
            save_dir.mkdir(parents=True, exist_ok=True)
            frame_path = save_dir / f"frame_{int(time.time() * 1000)}.png"
            result_frame.save(frame_path)
            self.last_saved_frame_path = str(frame_path)
        except Exception as exc:
            logger.debug("Failed to save frame: %s", exc)

        return result_frame

    def crop_element(
        self,
        frame: Image.Image,
        x: int,
        y: int,
        w: int,
        h: int,
    ) -> Image.Image:
        """Crops the frame to requested bounds [x, y, w, h] clamped to surface boundaries."""
        img_w, img_h = frame.size
        clamped_x1 = max(0, min(x, img_w))
        clamped_y1 = max(0, min(y, img_h))
        clamped_x2 = max(clamped_x1, min(x + w, img_w))
        clamped_y2 = max(clamped_y1, min(y + h, img_h))

        if clamped_x2 <= clamped_x1 or clamped_y2 <= clamped_y1:
            # Return minimal 1x1 image if crop is zero-sized
            return Image.new("RGBA", (1, 1), (0, 0, 0, 0))

        return frame.crop((clamped_x1, clamped_y1, clamped_x2, clamped_y2))

    # --- Input Dispatching with Boundary Clamping & Safety Fail-Safes ---

    def _verify_preconditions(self) -> None:
        """Enforces physical preemption, auto-focus, and geometry fail-safes."""
        self.check_active_app()
        if not self._initialized:
            self.ensure_initialized()
        global_preemption_manager.check_preemption()
        if self.target_pid:
            from wayland_computer_use_mcp.kwin import query_kwin_geometry

            title = self._resolve_target_title()
            geom = query_kwin_geometry(pid=self.target_pid, title=title)
            if not geom:
                try:
                    from wayland_computer_use_mcp.a11y import get_application_tree

                    tree = get_application_tree(self.target_pid)
                    sb = tree.get("screen_bounds") or tree.get("bounds")
                    if sb and len(sb) >= 4 and sb[2] > 0 and sb[3] > 0:
                        geom = (int(sb[0]), int(sb[1]), int(sb[2]), int(sb[3]))
                except Exception:
                    pass

            if geom:
                wx, wy, ww, wh = geom
                global_geometry_detector.validate_stability(
                    current_width=ww, current_height=wh, current_x=wx, current_y=wy
                )
                self.window_offset_x = wx
                self.window_offset_y = wy
                global_clamper.set_bounds(ww, wh)
            else:
                global_geometry_detector.validate_stability(*global_clamper.dimensions)
        else:
            global_geometry_detector.validate_stability(*global_clamper.dimensions)

    def _apply_action_delay(self) -> None:
        """Applies configured delay after an action so visual interactions are observable."""
        cfg = get_config()
        if cfg.action_delay_seconds > 0:
            time.sleep(cfg.action_delay_seconds)

    def _ensure_kwin_mouseclick_effect(self) -> None:
        """Enables native KDE KWin mouseclick ripple effect if running on KDE Plasma."""
        try:
            from dbus_fast import BusType, Message
            from dbus_fast.aio import MessageBus

            async def _load() -> None:
                bus = await MessageBus(bus_type=BusType.SESSION).connect()
                await bus.call(
                    Message(
                        destination="org.kde.KWin",
                        path="/Effects",
                        interface="org.kde.kwin.Effects",
                        member="loadEffect",
                        signature="s",
                        body=["mouseclick"],
                    )
                )
                await bus.disconnect()

            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
                    ex.submit(asyncio.run, _load()).result()
            else:
                loop.run_until_complete(_load())
        except Exception:
            pass

    def dispatch_click(self, x: int, y: int, button: str = "left") -> str:
        """Validates bounds and executes mouse click."""
        self._verify_preconditions()
        self._ensure_kwin_mouseclick_effect()
        cx, cy = global_clamper.validate_and_clamp(x, y)

        btn_map = {
            "left": BTN_LEFT,
            "right": BTN_RIGHT,
            "middle": BTN_MIDDLE,
        }
        button_code = btn_map.get(button.lower(), BTN_LEFT)

        if not self.ei_client:
            self.ei_client = EIClient(fd=None)

        target_x = float(self.window_offset_x + cx)
        target_y = float(self.window_offset_y + cy)
        self._notify_pointer_motion_absolute(target_x, target_y)
        self.ei_client.pointer_motion_absolute(target_x, target_y)
        time.sleep(0.02)
        self._notify_pointer_button(button_code, True)
        self.ei_client.button_click(button_code)
        self._notify_pointer_button(button_code, False)
        self._apply_action_delay()

        return f"Clicked {button} button at ({cx}, {cy})"

    def dispatch_double_click(self, x: int, y: int, button: str = "left") -> str:
        """Performs a clamped double-click."""
        self._verify_preconditions()
        self._ensure_kwin_mouseclick_effect()
        cx, cy = global_clamper.validate_and_clamp(x, y)

        btn_map = {
            "left": BTN_LEFT,
            "right": BTN_RIGHT,
            "middle": BTN_MIDDLE,
        }
        button_code = btn_map.get(button.lower(), BTN_LEFT)

        if not self.ei_client:
            self.ei_client = EIClient(fd=None)

        target_x = float(self.window_offset_x + cx)
        target_y = float(self.window_offset_y + cy)
        self._notify_pointer_motion_absolute(target_x, target_y)
        self.ei_client.pointer_motion_absolute(target_x, target_y)
        time.sleep(0.02)
        self._notify_pointer_button(button_code, True)
        self._notify_pointer_button(button_code, False)
        time.sleep(0.05)
        self._notify_pointer_button(button_code, True)
        self._notify_pointer_button(button_code, False)
        self.ei_client.double_click(button_code)
        self._apply_action_delay()

        return f"Double-clicked {button} button at ({cx}, {cy})"

    def dispatch_right_click(self, x: int, y: int) -> str:
        """Performs a clamped right-click."""
        return self.dispatch_click(x, y, button="right")

    def dispatch_hover(self, x: int, y: int, duration_ms: int = 500) -> str:
        """Positions cursor at coordinates without clicking to trigger hover states or tooltips."""
        self._verify_preconditions()
        cx, cy = global_clamper.validate_and_clamp(x, y)

        if not self.ei_client:
            self.ei_client = EIClient(fd=None)

        target_x = float(self.window_offset_x + cx)
        target_y = float(self.window_offset_y + cy)
        self._notify_pointer_motion_absolute(target_x, target_y)
        self.ei_client.pointer_motion_absolute(target_x, target_y)
        time.sleep(max(10, duration_ms) / 1000.0)
        self._apply_action_delay()

        return f"Hovered pointer at ({cx}, {cy}) for {duration_ms}ms"

    def dispatch_drag(self, start_x: int, start_y: int, end_x: int, end_y: int) -> str:
        """Validates bounds and performs clamped drag-and-drop gesture."""
        self._verify_preconditions()
        s_x, s_y = global_clamper.validate_and_clamp(start_x, start_y)
        e_x, e_y = global_clamper.validate_and_clamp(end_x, end_y)

        if not self.ei_client:
            self.ei_client = EIClient(fd=None)

        s_target_x = float(self.window_offset_x + s_x)
        s_target_y = float(self.window_offset_y + s_y)
        e_target_x = float(self.window_offset_x + e_x)
        e_target_y = float(self.window_offset_y + e_y)

        # Move to start, mouse down, interpolate to end, mouse up
        self._notify_pointer_motion_absolute(s_target_x, s_target_y)
        self.ei_client.pointer_motion_absolute(s_target_x, s_target_y)
        time.sleep(0.05)
        self._notify_pointer_button(BTN_LEFT, True)
        self.ei_client.button_down(BTN_LEFT)
        time.sleep(0.05)

        # Linear interpolation over 10 steps for smooth drag recognition
        steps = 10
        for i in range(1, steps + 1):
            cur_x = s_target_x + (e_target_x - s_target_x) * (i / steps)
            cur_y = s_target_y + (e_target_y - s_target_y) * (i / steps)
            self._notify_pointer_motion_absolute(cur_x, cur_y)
            self.ei_client.pointer_motion_absolute(cur_x, cur_y)
            time.sleep(0.01)

        time.sleep(0.05)
        self._notify_pointer_button(BTN_LEFT, False)
        self.ei_client.button_up(BTN_LEFT)
        self._apply_action_delay()

        return f"Dragged from ({s_x}, {s_y}) to ({e_x}, {e_y})"

    def dispatch_scroll(self, dx: int, dy: int) -> str:
        """Dispatches scroll deltas."""
        self._verify_preconditions()
        if not self.ei_client:
            self.ei_client = EIClient(fd=None)

        self._notify_pointer_axis(float(dx), float(dy))
        self.ei_client.scroll(float(dx), float(dy))
        self._apply_action_delay()
        return f"Scrolled dx={dx}, dy={dy}"

    def dispatch_type_text(self, text: str, x: int | None = None, y: int | None = None) -> str:
        """Types text via hybrid libei key events + clipboard paste fallback.

        If x and y coordinates are provided, dispatches a click at (x, y) first to ensure focus.
        """
        self._verify_preconditions()
        prefix = ""
        if x is not None and y is not None:
            prefix = self.dispatch_click(x, y, button="left") + ". "
            time.sleep(0.05)

        if not self.ei_client:
            self.ei_client = EIClient(fd=None)

        can_type_pure = True
        for char in text:
            if not self.ei_client.type_char(char):
                can_type_pure = False
                break

        if not can_type_pure:
            # Fallback to Wayland clipboard copy + Ctrl+V paste
            self._paste_clipboard_text(text)

        self._apply_action_delay()
        return f"{prefix}Typed {len(text)} characters"

    def dispatch_key_combination(self, keys: list[str]) -> str:
        """Dispatches key combination while strictly blocking system shortcuts."""
        self._verify_preconditions()
        # Security validation against Super/Meta, Ctrl+Alt, and system shortcuts
        keycodes = global_shortcut_filter.validate_and_resolve(keys)

        if not self.ei_client:
            self.ei_client = EIClient(fd=None)

        self.ei_client.key_combination(keycodes)
        self._apply_action_delay()
        return f"Dispatched key combination: {'+'.join(keys)}"

    def dispatch_clipboard_read(self) -> str:
        """Reads current text from the Wayland clipboard via wl-paste."""
        self._verify_preconditions()
        try:
            res = subprocess.run(
                ["wl-paste", "--no-newline"],
                capture_output=True,
                text=True,
                timeout=1.0,
            )
            return res.stdout if res.returncode == 0 else ""
        except Exception as exc:
            logger.warning("wl-paste read failed: %s", exc)
            return ""

    def dispatch_clipboard_write(self, text: str) -> str:
        """Writes text into the Wayland clipboard via wl-copy."""
        self._verify_preconditions()
        try:
            subprocess.run(
                ["wl-copy", text],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1.0,
            )
            return f"Copied {len(text)} characters to clipboard."
        except Exception as exc:
            logger.warning("wl-copy write failed: %s", exc)
            return f"Failed to write to clipboard: {exc}"

    def click_element_by_label(
        self, label: str, role: str | None = None, pid: int | None = None
    ) -> str:
        """Finds element in AT-SPI tree matching label, computes center, and clicks."""
        from wayland_computer_use_mcp.a11y import get_application_tree

        self._verify_preconditions()
        target_pid = pid or self.target_pid or 0
        tree = get_application_tree(target_pid)
        target_label = label.strip().lower()
        target_role = role.strip().lower() if role else None

        matching_node: dict[str, Any] | None = None

        def search(node: dict[str, Any]) -> None:
            nonlocal matching_node
            if matching_node:
                return
            n_name = node.get("name", "").strip().lower()
            n_role = node.get("role", "").strip().lower()
            if target_label in n_name:
                if target_role is None or target_role in n_role:
                    matching_node = node
                    return
            for ch in node.get("children", []):
                search(ch)

        search(tree)

        if not matching_node:
            raise ValueError(f"No element matching label '{label}' found in accessibility tree.")

        b = matching_node.get("bounds", [0, 0, 0, 0])
        cx = b[0] + b[2] // 2
        cy = b[1] + b[3] // 2
        click_msg = self.dispatch_click(cx, cy, button="left")
        name = matching_node.get("name")
        role_name = matching_node.get("role")

        bus_n = matching_node.get("bus_name")
        obj_p = matching_node.get("path")
        if bus_n and obj_p:
            from wayland_computer_use_mcp.a11y import do_accessible_action

            do_accessible_action(bus_n, obj_p, 0)

        return f"{click_msg} (Target: '{name}' [{role_name}])"

    def generate_labeled_screenshot(
        self, pid: int | None = None
    ) -> tuple[Image.Image, list[dict[str, Any]]]:
        """Captures window frame and overlays numbered Set-of-Marks badges for widgets."""
        self.check_active_app(pid)
        from wayland_computer_use_mcp.a11y import get_application_tree

        if pid:
            self.target_pid = pid

        frame = self.capture_frame()
        tree = get_application_tree(pid or self.target_pid or 0)

        interactive_roles = {
            "push button",
            "button",
            "entry",
            "text",
            "scale",
            "slider",
            "check box",
            "radio button",
            "menu item",
            "link",
            "page tab",
            "combo box",
        }
        elements: list[dict[str, Any]] = []

        def walk(node: dict[str, Any]) -> None:
            role = node.get("role", "").lower()
            bounds = node.get("bounds", [0, 0, 0, 0])
            name = node.get("name", "").strip()
            states = node.get("states", [])

            # Filter out hidden or non-visible elements
            if states and "visible" not in states and "showing" not in states:
                return

            if role in interactive_roles and len(bounds) == 4 and bounds[2] > 0 and bounds[3] > 0:
                cx = bounds[0] + bounds[2] // 2
                cy = bounds[1] + bounds[3] // 2
                elements.append(
                    {
                        "role": role,
                        "name": name,
                        "bounds": bounds,
                        "center": [cx, cy],
                    }
                )
            for ch in node.get("children", []):
                walk(ch)

        walk(tree)

        root_bounds = tree.get("bounds", [0, 0, 0, 0])
        root_w = root_bounds[2] if len(root_bounds) >= 4 else 0
        root_h = root_bounds[3] if len(root_bounds) >= 4 else 0
        scale_x = frame.width / float(root_w) if root_w > 0 else 1.0
        scale_y = frame.height / float(root_h) if root_h > 0 else 1.0

        annotated = frame.copy().convert("RGBA")
        overlay = Image.new("RGBA", annotated.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(overlay)
        font = ImageFont.load_default()

        legend: list[dict[str, Any]] = []
        for idx, el in enumerate(elements, start=1):
            raw_x, raw_y, raw_w, raw_h = el["bounds"]
            if scale_x != 1.0 or scale_y != 1.0:
                x = int(raw_x * scale_x)
                y = int(raw_y * scale_y)
                w = int(raw_w * scale_x)
                h = int(raw_h * scale_y)
                cx = x + w // 2
                cy = y + h // 2
            else:
                x, y, w, h = raw_x, raw_y, raw_w, raw_h
                cx, cy = el["center"]

            draw.rectangle([x, y, x + w, y + h], outline=(0, 206, 201, 220), width=2)
            tag = f"[{idx}]"
            bbox = draw.textbbox((0, 0), tag, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            bx1 = max(0, x)
            by1 = y - th - 6
            if by1 < 0:
                by1 = y + 2
            bx2 = bx1 + tw + 8
            by2 = by1 + th + 6
            draw.rounded_rectangle([bx1, by1, bx2, by2], radius=4, fill=(214, 48, 49, 230))
            draw.text((bx1 + 4, by1 + 3), tag, fill=(255, 255, 255, 255), font=font)

            legend.append(
                {
                    "index": idx,
                    "role": el["role"],
                    "name": el["name"],
                    "center": [cx, cy],
                    "bounds": [x, y, w, h],
                }
            )

        final_img = Image.alpha_composite(annotated, overlay)

        save_dir_env = os.environ.get("WAYLAND_MCP_SAVE_FRAMES_DIR") or DEFAULT_SAVE_DIR
        try:
            save_dir = Path(save_dir_env)
            save_dir.mkdir(parents=True, exist_ok=True)
            labeled_path = save_dir / f"labeled_{int(time.time() * 1000)}.png"
            final_img.save(labeled_path)
            self.last_saved_labeled_path = str(labeled_path)
        except Exception as exc:
            logger.debug("Failed to save labeled frame: %s", exc)

        return final_img, legend

    def _paste_clipboard_text(self, text: str) -> None:
        """Copies text to clipboard using wl-copy and sends Ctrl+V."""
        try:
            subprocess.run(
                ["wl-copy", text],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=1.0,
            )
            time.sleep(0.05)
            self._notify_keyboard_keycode(KEY_LEFTCTRL, True)
            if self.ei_client:
                self.ei_client.key_press(KEY_LEFTCTRL, True)
            time.sleep(0.02)

            self._notify_keyboard_keycode(KEY_V, True)
            if self.ei_client:
                self.ei_client.key_press(KEY_V, True)
            time.sleep(0.02)

            self._notify_keyboard_keycode(KEY_V, False)
            if self.ei_client:
                self.ei_client.key_press(KEY_V, False)
            time.sleep(0.02)

            self._notify_keyboard_keycode(KEY_LEFTCTRL, False)
            if self.ei_client:
                self.ei_client.key_press(KEY_LEFTCTRL, False)
        except Exception as exc:
            logger.warning("Clipboard paste fallback failed: %s", exc)


# Global singleton instance
global_portal_session = PortalSession()
