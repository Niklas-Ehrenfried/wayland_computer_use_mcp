"""Portal session orchestrator coordinating screencast, remotedesktop, and input."""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from wayland_computer_use_mcp.config import get_config
from wayland_computer_use_mcp.libei import EIClient
from wayland_computer_use_mcp.overlay import draw_labeled_overlay
from wayland_computer_use_mcp.portal.input import InputDispatcher
from wayland_computer_use_mcp.portal.remotedesktop import (
    AsyncLoopThread,
    RemoteDesktopClient,
)
from wayland_computer_use_mcp.portal.screencast import (
    ScreenCastPipeline,
    crop_element,
)
from wayland_computer_use_mcp.security import (
    global_clamper,
    global_geometry_detector,
    global_preemption_manager,
)

logger = logging.getLogger(__name__)

DEFAULT_SAVE_DIR = Path.home() / ".cache" / "wayland-computer-use-mcp" / "screenshots"


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

        self._last_portal_error = None
        try:
            _, node_id, _, _ = await self._rd_client.negotiate(self.active_app_id)
            if node_id:
                self._screencast.setup_pipeline(node_id)
            self._initialized = True
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

        geom: tuple[int, int, int, int] | None = None
        if self.target_pid:
            from wayland_computer_use_mcp.compositors import query_window_geometry

            title = self._resolve_target_title()
            geom = query_window_geometry(pid=self.target_pid, title=title)

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
        from wayland_computer_use_mcp.compositors import query_window_geometry

        title = self._resolve_target_title()
        geom = query_window_geometry(pid=self.target_pid, title=title)
        if not geom:
            try:
                from wayland_computer_use_mcp.a11y import get_application_tree

                tree = get_application_tree(self.target_pid)
                sb = tree.get("screen_bounds") or tree.get("bounds")
                if sb and len(sb) >= 4 and sb[2] > 0 and sb[3] > 0:
                    geom = (int(sb[0]), int(sb[1]), int(sb[2]), int(sb[3]))
            except Exception:
                pass

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

    def generate_labeled_screenshot(
        self,
        pid: int | None = None,
        save_to_disk: bool = False,
        filename: str = "latest_labeled.png",
    ) -> tuple[Image.Image, list[dict[str, Any]]]:
        """Captures window frame and overlays numbered Set-of-Marks badges for widgets."""
        self.check_active_app(pid)
        from wayland_computer_use_mcp.a11y import get_application_tree

        if pid:
            self.target_pid = pid

        frame = self.capture_frame(save_to_disk=False)
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
        final_img, legend = draw_labeled_overlay(frame, elements, root_bounds)

        if save_to_disk:
            save_dir_env = os.environ.get("WAYLAND_MCP_SAVE_FRAMES_DIR")
            save_dir = Path(save_dir_env) if save_dir_env else DEFAULT_SAVE_DIR
            try:
                save_dir.mkdir(parents=True, exist_ok=True)
                labeled_path = save_dir / filename
                final_img.save(labeled_path)
                self.last_saved_labeled_path = str(labeled_path)
            except Exception as exc:
                logger.debug("Failed to save labeled frame: %s", exc)

        return final_img, legend

    def close(self) -> None:
        """Cleans up active screencast pipeline, remote desktop, and libei resources."""
        if self.screencast_client:
            self.screencast_client.stop()
        if self._ei_client:
            try:
                self._ei_client.close()
            except Exception:
                pass
            self._ei_client = None
