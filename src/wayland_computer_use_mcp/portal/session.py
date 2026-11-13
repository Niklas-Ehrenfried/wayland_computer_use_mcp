"""Portal session orchestrator coordinating screencast, remotedesktop, and input."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from typing import Any

from PIL import Image, ImageDraw

from wayland_computer_use_mcp.config import get_config
from wayland_computer_use_mcp.libei import EIClient
from wayland_computer_use_mcp.portal.input import InputDispatcher
from wayland_computer_use_mcp.portal.remotedesktop import (
    AsyncLoopThread,
    RemoteDesktopClient,
)
from wayland_computer_use_mcp.portal.screencast import (
    ScreenCastPipeline,
    crop_element,
)
from wayland_computer_use_mcp.portal.virtual import VirtualSessionManager, _terminate_proc_group
from wayland_computer_use_mcp.security import (
    global_clamper,
    global_geometry_detector,
    global_preemption_manager,
)

logger = logging.getLogger(__name__)


class PortalSession:
    """Manages active ScreenCast and RemoteDesktop session via XDG portals and PipeWire."""

    def __init__(self) -> None:
        self.active_app_id: str = "default"
        self.target_pid: int | None = None
        self.target_title: str | None = None
        self.window_offset_x: int = 0
        self.window_offset_y: int = 0
        self.last_saved_labeled_path: str | None = None

        self._async_thread = AsyncLoopThread()
        self._rd_client = RemoteDesktopClient(self._async_thread)
        self._screencast = ScreenCastPipeline()
        self._input = InputDispatcher(
            rd_client=self._rd_client,
            verify_preconditions_fn=self._verify_preconditions,
            get_offsets_fn=lambda: (self.window_offset_x, self.window_offset_y),
            is_mock_fn=lambda: self.is_mock,
            get_ei_client_fn=self._get_or_create_ei_client,
        )

        self._lock = threading.Lock()
        self._initialized = False
        self._is_mock = False
        self._last_portal_error: str | None = None
        self._virtual_env = VirtualSessionManager()
        self._virtual_proc: subprocess.Popen[Any] | None = None

    @property
    def bus(self) -> Any:
        return self._rd_client.bus

    @property
    def session_handle(self) -> str | None:
        return self._rd_client.session_handle

    @session_handle.setter
    def session_handle(self, val: str | None) -> None:
        self._rd_client.session_handle = val

    @property
    def pipewire_node_id(self) -> int | None:
        return self._rd_client.pipewire_node_id

    @pipewire_node_id.setter
    def pipewire_node_id(self, val: int | None) -> None:
        self._rd_client.pipewire_node_id = val

    @property
    def eis_fd(self) -> int | None:
        return self._rd_client.eis_fd

    @eis_fd.setter
    def eis_fd(self, val: int | None) -> None:
        self._rd_client.eis_fd = val

    @property
    def ei_client(self) -> EIClient | None:
        return self._rd_client.ei_client

    @ei_client.setter
    def ei_client(self, val: EIClient | None) -> None:
        self._rd_client.ei_client = val

    @property
    def restore_token(self) -> str | None:
        return self._rd_client.restore_token

    @restore_token.setter
    def restore_token(self, val: str | None) -> None:
        self._rd_client.restore_token = val

    @property
    def last_saved_frame_path(self) -> str | None:
        return self._screencast.last_saved_frame_path

    @property
    def is_mock(self) -> bool:
        return (
            self._is_mock
            or self.pipewire_node_id == 99999
            or get_config().mock_mode
            or not os.environ.get("WAYLAND_DISPLAY")
        )

    def _get_or_create_ei_client(self) -> EIClient:
        if not self._rd_client.ei_client:
            self._rd_client.ei_client = EIClient(fd=None)
        return self._rd_client.ei_client

    def is_active(self) -> bool:
        return self._initialized and (
            self.pipewire_node_id is not None or self.is_mock or self._virtual_env.is_running
        )

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

        # Isolated virtual / Playwright-style nested window mode
        if cfg.display_mode == "virtual" and not cfg.mock_mode:
            self._ensure_virtual_compositor()
            eis_fd, backend_kind = await self._virtual_env.connect_direct_eis()
            if eis_fd is not None:
                self._rd_client.eis_fd = eis_fd
                self._rd_client.ei_client = EIClient(fd=eis_fd)
            elif self._rd_client.ei_client is None:
                self._rd_client.ei_client = EIClient(fd=None)
            self._initialized = True
            logger.info(
                "Initialized isolated virtual session (backend=%s, eis=%s)",
                backend_kind,
                eis_fd is not None,
            )
            return

        # If mock mode or test environment without Wayland display
        if cfg.mock_mode or not os.environ.get("WAYLAND_DISPLAY"):
            logger.info("Initializing portal session in mock/emulated mode.")
            self._init_mock_session()
            return

        self._last_portal_error = None
        try:
            _, node_id, _, _ = await self._rd_client.negotiate(self.active_app_id)
            if node_id:
                self._screencast.setup_pipeline(node_id)
            self._initialized = True
        except Exception as exc:
            import traceback

            err_detail = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            self._last_portal_error = f"{err_detail} | {traceback.format_exc()}"
            logger.warning("Portal D-Bus negotiation failed: %s", err_detail)
            if cfg.mock_mode or isinstance(exc, FileNotFoundError):
                logger.info("D-Bus socket unavailable; using mock session.")
                self._init_mock_session()
            else:
                self._initialized = False
                raise RuntimeError(
                    f"RemoteDesktop / ScreenCast portal negotiation failed: {err_detail}. "
                    "Please ensure the OS permission prompt was allowed."
                ) from exc

    def _ensure_virtual_compositor(self) -> None:
        """Verify or spawn a cross-DE isolated virtual/nested Wayland compositor."""
        cfg = get_config()
        self._virtual_env.ensure_started(cfg)
        self._virtual_proc = self._virtual_env.compositor_proc

    def _init_mock_session(self) -> None:
        """Initialize mock session for CI and testing."""
        self._initialized = True
        self._is_mock = True
        self.pipewire_node_id = 99999
        self.ei_client = EIClient(fd=None)
        img = Image.new("RGBA", (1920, 1080), color=(45, 52, 54, 255))
        draw = ImageDraw.Draw(img)
        draw.rectangle([50, 50, 600, 400], fill=(9, 132, 227, 255), outline=(255, 255, 255, 255))
        draw.text((70, 70), "wayland-computer-use-mcp Mock Surface", fill=(255, 255, 255, 255))
        self._screencast._last_frame = img
        global_clamper.set_bounds(1920, 1080)
        global_geometry_detector.record_capture(1920, 1080)

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

    @staticmethod
    def _extract_tree_window_bounds(tree: dict[str, Any]) -> tuple[int, int, int, int] | None:
        """Extracts positive window geometry from an AT-SPI tree root or top-level frame child."""
        candidates: list[dict[str, Any]] = [tree]
        for child in tree.get("children", []):
            if isinstance(child, dict) and str(child.get("role", "")).lower() in (
                "frame",
                "window",
                "dialog",
            ):
                candidates.append(child)
        for node in candidates:
            sb = node.get("screen_bounds") or node.get("bounds")
            if sb and len(sb) >= 4 and int(sb[2]) > 0 and int(sb[3]) > 0:
                return (max(0, int(sb[0])), max(0, int(sb[1])), int(sb[2]), int(sb[3]))
        return None

    def _resolve_target_geometry(self) -> tuple[int, int, int, int] | None:
        """Resolves active target window geometry via compositor D-Bus or AT-SPI fallback."""
        if not self.target_pid:
            return None
        from wayland_computer_use_mcp.compositors import query_window_geometry

        title = self._resolve_target_title()
        geom = query_window_geometry(pid=self.target_pid, title=title)
        if geom:
            return geom
        try:
            from wayland_computer_use_mcp.a11y import get_application_tree

            tree = get_application_tree(self.target_pid)
            return self._extract_tree_window_bounds(tree)
        except Exception:
            return None

    def capture_frame(
        self,
        crop_box: list[int] | None = None,
        save_to_disk: bool = False,
        filename: str = "latest_capture.png",
    ) -> Image.Image:
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

        geom = self._resolve_target_geometry()
        if geom:
            wx, wy, ww, wh = geom
            self.window_offset_x = wx
            self.window_offset_y = wy
            global_clamper.set_bounds(ww, wh)
            global_geometry_detector.record_capture(ww, wh, wx, wy)
            time.sleep(0.05)

        frame = self._screencast.pull_frame()
        if frame is None:
            if self._screencast._last_frame is not None:
                frame = self._screencast._last_frame.copy()
            else:
                frame = Image.new("RGBA", (1920, 1080), color=(40, 44, 52, 255))

        self._screencast._last_frame = frame

        if not self.target_pid or not geom:
            global_clamper.set_bounds(frame.width, frame.height)
            global_geometry_detector.record_capture(frame.width, frame.height, 0, 0)

        result_frame: Image.Image
        if crop_box:
            result_frame = crop_element(frame, *crop_box)
        elif self.target_pid and geom:
            wx, wy, ww, wh = geom
            result_frame = crop_element(frame, wx, wy, ww, wh)
        else:
            result_frame = frame

        if save_to_disk:
            self._screencast.save_frame(result_frame, filename=filename)
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
        return crop_element(frame, x, y, w, h)

    def _verify_preconditions(self) -> None:
        """Enforces physical preemption, auto-focus, and geometry fail-safes."""
        if self.is_mock:
            global_preemption_manager.check_preemption()
            global_geometry_detector.validate_stability(*global_clamper.dimensions)
            return

        self.check_active_app()
        if not self._initialized:
            self.ensure_initialized()
        global_preemption_manager.check_preemption()
        if not self.target_pid:
            raise RuntimeError(
                "No active managed application. "
                "Action blocked to prevent unintended desktop interaction."
            )
        geom = self._resolve_target_geometry()

        if not geom:
            raise RuntimeError(
                f"Target application window (PID {self.target_pid}) "
                "is not open, visible, or mapped. Action blocked."
            )

        wx, wy, ww, wh = geom
        global_geometry_detector.validate_stability(
            current_width=ww, current_height=wh, current_x=wx, current_y=wy
        )
        self.window_offset_x = wx
        self.window_offset_y = wy
        global_clamper.set_bounds(ww, wh)

    # Delegation to InputDispatcher
    def dispatch_click(self, x: int, y: int, button: str = "left") -> str:
        return self._input.dispatch_click(x, y, button=button)

    def dispatch_double_click(self, x: int, y: int, button: str = "left") -> str:
        return self._input.dispatch_double_click(x, y, button=button)

    def dispatch_right_click(self, x: int, y: int) -> str:
        return self._input.dispatch_right_click(x, y)

    def dispatch_hover(self, x: int, y: int, duration_ms: int = 500) -> str:
        return self._input.dispatch_hover(x, y, duration_ms=duration_ms)

    def dispatch_drag(self, start_x: int, start_y: int, end_x: int, end_y: int) -> str:
        return self._input.dispatch_drag(start_x, start_y, end_x, end_y)

    def dispatch_scroll(self, dx: int, dy: int) -> str:
        return self._input.dispatch_scroll(dx, dy)

    def dispatch_type_text(self, text: str, x: int | None = None, y: int | None = None) -> str:
        return self._input.dispatch_type_text(text, x=x, y=y)

    def dispatch_key_combination(self, keys: list[str]) -> str:
        return self._input.dispatch_key_combination(keys)

    def dispatch_clipboard_read(self) -> str:
        return self._input.dispatch_clipboard_read()

    def dispatch_clipboard_write(self, text: str) -> str:
        return self._input.dispatch_clipboard_write(text)

    def close(self) -> None:
        """Cleans up active screencast pipeline, remote desktop, libei, and event listeners."""
        self._screencast.stop()
        self._rd_client.close()
        try:
            from wayland_computer_use_mcp.a11y import global_event_listener

            global_event_listener.stop()
        except Exception:
            pass
        self._virtual_env.stop()
        if self._virtual_proc is not None:
            _terminate_proc_group(self._virtual_proc)
            self._virtual_proc = None
        self._initialized = False
        self._async_thread.stop()
