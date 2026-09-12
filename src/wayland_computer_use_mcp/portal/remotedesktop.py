"""RemoteDesktop portal client and EIS connection handling via D-Bus and liboeffis."""

from __future__ import annotations

import asyncio
import logging
import os
import select
import threading
import time
from pathlib import Path
from typing import Any

from dbus_fast import Message, MessageType
from dbus_fast.aio import MessageBus

from wayland_computer_use_mcp.libei import EIClient
from wayland_computer_use_mcp.security import global_token_store

logger = logging.getLogger(__name__)

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


class RemoteDesktopClient:
    """Handles XDG Desktop RemoteDesktop and ScreenCast portal negotiation and EIS input stream."""

    PORTAL_BUS = "org.freedesktop.portal.Desktop"
    PORTAL_PATH = "/org/freedesktop/portal/desktop"

    def __init__(self, async_thread: AsyncLoopThread) -> None:
        self._async_thread = async_thread
        self.bus: MessageBus | None = None
        self.session_handle: str | None = None
        self.pipewire_node_id: int | None = None
        self.eis_fd: int | None = None
        self.ei_client: EIClient | None = None
        self.restore_token: str | None = None

    async def negotiate(self, active_app_id: str) -> tuple[str, int | None, str | None, EIClient]:
        """Perform XDG portal session handshake via dbus-fast.

        Returns (session_handle, pipewire_node_id, restore_token, ei_client).
        """
        from dbus_fast import BusType, Variant

        _debug_log("Starting portal D-Bus negotiation...")
        self.bus = await MessageBus(bus_type=BusType.SESSION).connect()
        bus = self.bus
        sender_id = bus.unique_name[1:].replace(".", "_")

        # Add match rule for ALL portal Request signals for this connection
        await bus.call(
            Message(
                destination="org.freedesktop.DBus",
                path="/org/freedesktop/DBus",
                interface="org.freedesktop.DBus",
                member="AddMatch",
                signature="s",
                body=[
                    f"type='signal',interface='org.freedesktop.portal.Request',member='Response',path_namespace='/org/freedesktop/portal/desktop/request/{sender_id}'"
                ],
            )
        )

        loop = asyncio.get_running_loop()

        async def call_portal_action(
            interface: str,
            method: str,
            signature: str,
            args: list[Any],
            token: str,
            timeout: float = 60.0,
        ) -> dict[str, Any]:
            expected_path = f"/org/freedesktop/portal/desktop/request/{sender_id}/{token}"
            _debug_log(f"Calling portal {interface}.{method} with request token {token}...")
            future: asyncio.Future[dict[str, Any]] = loop.create_future()

            def on_signal(msg: Message) -> None:
                if (
                    msg.interface == "org.freedesktop.portal.Request"
                    and msg.member == "Response"
                    and msg.path == expected_path
                ):
                    _debug_log(f"Received Response for {token}: status={msg.body[0]}")
                    if not future.done():
                        response_code = msg.body[0]
                        results = msg.body[1] if len(msg.body) > 1 else {}
                        if response_code == 0:
                            unwrapped = {
                                k: (v.value if isinstance(v, Variant) else v)
                                for k, v in results.items()
                            }
                            future.set_result(unwrapped)
                        else:
                            err_msg = (
                                f"Portal request {method} rejected/cancelled by user "
                                f"(code {response_code})"
                            )
                            future.set_exception(PermissionError(err_msg))

            bus.add_message_handler(on_signal)
            try:
                reply = await bus.call(
                    Message(
                        destination=self.PORTAL_BUS,
                        path=self.PORTAL_PATH,
                        interface=interface,
                        member=method,
                        signature=signature,
                        body=args,
                    )
                )
                if reply.message_type == MessageType.ERROR:
                    raise RuntimeError(f"Portal method {method} failed: {reply.body}")

                from wayland_computer_use_mcp.compositors import ensure_portal_dialogs_above

                ensure_portal_dialogs_above()
                return await asyncio.wait_for(future, timeout=timeout)
            finally:
                bus.remove_message_handler(on_signal)

        ts = int(time.time() * 1000) % 1000000

        # 1. Create RemoteDesktop session
        create_res = await call_portal_action(
            "org.freedesktop.portal.RemoteDesktop",
            "CreateSession",
            "a{sv}",
            [
                {
                    "session_handle_token": Variant("s", f"u_{ts}"),
                    "handle_token": Variant("s", f"r_create_{ts}"),
                }
            ],
            f"r_create_{ts}",
        )
        self.session_handle = str(create_res["session_handle"])
        _debug_log(f"RemoteDesktop session created: {self.session_handle}")

        # 2. Select ScreenCast sources
        stored_token = global_token_store.get_token(active_app_id)
        screencast_opts: dict[str, Any] = {
            "handle_token": Variant("s", f"r_sc_sel_{ts}"),
            "types": Variant("u", 1),  # MONITOR
            "multiple": Variant("b", False),
        }
        if stored_token:
            screencast_opts["restore_token"] = Variant("s", stored_token)
            _debug_log(f"Using restore token for ScreenCast: {stored_token[:8]}...")

        await call_portal_action(
            "org.freedesktop.portal.ScreenCast",
            "SelectSources",
            "oa{sv}",
            [self.session_handle, screencast_opts],
            f"r_sc_sel_{ts}",
        )

        # 3. Select RemoteDesktop devices (Pointer | Keyboard)
        await call_portal_action(
            "org.freedesktop.portal.RemoteDesktop",
            "SelectDevices",
            "oa{sv}",
            [
                self.session_handle,
                {
                    "handle_token": Variant("s", f"r_rd_sel_{ts}"),
                    "types": Variant("u", 1 | 2),  # Pointer=1, Keyboard=2
                },
            ],
            f"r_rd_sel_{ts}",
        )

        # 4. Start session
        start_results = await call_portal_action(
            "org.freedesktop.portal.RemoteDesktop",
            "Start",
            "osa{sv}",
            [
                self.session_handle,
                "",  # parent_window
                {"handle_token": Variant("s", f"r_start_{ts}")},
            ],
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
            global_token_store.set_token(active_app_id, self.restore_token)

        # 5. Connect to EIS for input injection on user's seat (seat0)
        _debug_log("Connecting to EIS for input injection on user's seat...")
        oeffis_fd = self.connect_eis_via_oeffis()
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

        return self.session_handle, self.pipewire_node_id, self.restore_token, self.ei_client

    def connect_eis_via_oeffis(self) -> int | None:
        """Connects to EIS via system liboeffis to get an input injection fd."""
        try:
            import ctypes

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
            from wayland_computer_use_mcp.compositors import ensure_portal_dialogs_above

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

    def notify_pointer_motion_absolute(self, x: float, y: float) -> None:
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

    def notify_pointer_button(self, button_code: int, pressed: bool) -> None:
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

    def notify_pointer_axis(self, dx: float, dy: float) -> None:
        """Dispatch scroll deltas via D-Bus portal (discrete wheel steps and continuous)."""
        if not self.bus or not self.session_handle:
            return
        try:
            # 1. Dispatch discrete wheel increments for toolkits (GTK, Qt) on Wayland
            if dy != 0:
                steps_y = int(round(dy / 100.0)) if abs(dy) >= 50 else (1 if dy > 0 else -1)
                self._async_thread.run(
                    self.bus.call(
                        Message(
                            destination=self.PORTAL_BUS,
                            path=self.PORTAL_PATH,
                            interface="org.freedesktop.portal.RemoteDesktop",
                            member="NotifyPointerAxisDiscrete",
                            signature="oa{sv}ui",
                            body=[self.session_handle, {}, 0, steps_y],
                        )
                    )
                )
            if dx != 0:
                steps_x = int(round(dx / 100.0)) if abs(dx) >= 50 else (1 if dx > 0 else -1)
                self._async_thread.run(
                    self.bus.call(
                        Message(
                            destination=self.PORTAL_BUS,
                            path=self.PORTAL_PATH,
                            interface="org.freedesktop.portal.RemoteDesktop",
                            member="NotifyPointerAxisDiscrete",
                            signature="oa{sv}ui",
                            body=[self.session_handle, {}, 1, steps_x],
                        )
                    )
                )

            # 2. Also dispatch continuous NotifyPointerAxis
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

    def notify_keyboard_keycode(self, keycode: int, pressed: bool) -> None:
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
