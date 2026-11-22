"""Lean session coordinator routing between Blackbox, HostPassthrough, and Mock drivers."""

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Literal

from PIL import Image

from wayland_computer_use_mcp.artifacts import get_artifacts_dir, prune_artifacts
from wayland_computer_use_mcp.blackbox.driver import BlackboxDriver
from wayland_computer_use_mcp.compat.host_driver import HostPassthroughDriver
from wayland_computer_use_mcp.config import get_config, resolve_effective_display_mode
from wayland_computer_use_mcp.core.context import (
    WindowSessionState,
    get_active_context,
    set_active_context,
)
from wayland_computer_use_mcp.core.driver import SessionDriver
from wayland_computer_use_mcp.core.mock_driver import MockSessionDriver
from wayland_computer_use_mcp.portal.input import InputDispatcher
from wayland_computer_use_mcp.portal.remotedesktop import AsyncLoopThread
from wayland_computer_use_mcp.portal.screencast import crop_element
from wayland_computer_use_mcp.security import (
    global_clamper,
    global_geometry_detector,
    global_preemption_manager,
)

logger = logging.getLogger(__name__)


class PortalSession:
    """Coordinates active SessionDriver, input clamping, frame capture, and teardown."""

    def __init__(self) -> None:
        self.active_app_id: str = "default"
        self._target_pid: int | None = None
        self._pid_states: dict[int, WindowSessionState] = {}
        self.target_title: str | None = None
        self.window_offset_x: int = 0
        self.window_offset_y: int = 0
        self.window_scale: float = 1.0
        self.last_saved_frame_path: str | None = None
        self.last_saved_labeled_path: str | None = None

        self._async_thread = AsyncLoopThread()
        self._blackbox = BlackboxDriver()
        self._host = HostPassthroughDriver(self._async_thread)
        self._mock = MockSessionDriver()

        self._lock = threading.Lock()
        self._initialized = False
        self._last_error: str | None = None
        self._last_frame: Image.Image | None = None

        self._input = InputDispatcher(
            get_sink_fn=lambda: self.active_driver().get_input_sink(),
            verify_preconditions_fn=self._verify_preconditions,
            get_offsets_fn=lambda: (self.window_offset_x, self.window_offset_y),
            get_scale_fn=lambda: self.window_scale,
        )

    @property
    def target_pid(self) -> int | None:
        return self._target_pid

    @target_pid.setter
    def target_pid(self, value: int | None) -> None:
        if value == self._target_pid:
            return
        if self._target_pid is not None:
            prev = self._pid_states.get(self._target_pid) or WindowSessionState(
                pid=self._target_pid
            )
            prev.title = self.target_title
            prev.offset_x = self.window_offset_x
            prev.offset_y = self.window_offset_y
            prev.bounds = global_clamper.dimensions
            prev.last_frame = self._last_frame
            prev.last_position = global_geometry_detector.last_position
            prev.last_dimensions = global_geometry_detector.last_dimensions
            prev.scale = self.window_scale
            self._pid_states[self._target_pid] = prev

        self._target_pid = value
        if value is not None:
            nxt = self._pid_states.get(value)
            if nxt is None:
                nxt = WindowSessionState(pid=value)
                self._pid_states[value] = nxt
            self.target_title = nxt.title
            self.window_offset_x = nxt.offset_x
            self.window_offset_y = nxt.offset_y
            self.window_scale = nxt.scale
            self._last_frame = nxt.last_frame
            global_clamper.set_bounds(*nxt.bounds)
            global_geometry_detector.last_position = nxt.last_position
            global_geometry_detector.last_dimensions = nxt.last_dimensions
        else:
            self.target_title = None
            self.window_offset_x = 0
            self.window_offset_y = 0
            self.window_scale = 1.0
            self._last_frame = None
            global_geometry_detector.last_position = None
            global_geometry_detector.last_dimensions = None

    def get_pid_state(self, pid: int) -> WindowSessionState | None:
        """Returns cached per-PID window geometry/offset state, syncing active PID first."""
        if pid == self._target_pid and pid is not None:
            state = self._pid_states.get(pid) or WindowSessionState(pid=pid)
            state.title = self.target_title
            state.offset_x = self.window_offset_x
            state.offset_y = self.window_offset_y
            state.bounds = global_clamper.dimensions
            state.last_frame = self._last_frame
            state.last_position = global_geometry_detector.last_position
            state.last_dimensions = global_geometry_detector.last_dimensions
            state.scale = self.window_scale
            self._pid_states[pid] = state
            return state
        return self._pid_states.get(pid)

    def clear_pid_state(self, pid: int) -> None:
        """Discards stored window state for an exited/terminated PID."""
        self._pid_states.pop(pid, None)
        if self._target_pid == pid:
            self._target_pid = None
            self.target_title = None
            self.window_offset_x = 0
            self.window_offset_y = 0
            self._last_frame = None
            global_geometry_detector.last_position = None
            global_geometry_detector.last_dimensions = None

    def active_driver(self) -> SessionDriver:
        """Selects the active driver: Mock (test/no-display), Blackbox (primary), or Host."""
        cfg = get_config()
        if cfg.mock_mode:
            return self._mock
        effective_mode, _, _ = resolve_effective_display_mode(cfg)
        if effective_mode == "virtual" or self._blackbox.is_active():
            return self._blackbox
        ctx = get_active_context()
        has_display = bool((ctx and ctx.wayland_display) or os.environ.get("WAYLAND_DISPLAY"))
        if not has_display:
            return self._mock
        return self._host

    @property
    def is_mock(self) -> bool:
        return self.active_driver() is self._mock

    def metadata(self) -> dict[str, Any]:
        """Returns active driver metadata (session_handle, adapter, eis_connected, etc.)."""
        return self.active_driver().metadata()

    def is_active(self) -> bool:
        return self._initialized and self.active_driver().is_active()

    def check_active_app(self, pid: int | None = None) -> None:
        """Verifies that an application process is actively running and responsive."""
        if self.is_mock:
            return
        effective_pid = pid or self.target_pid
        if not effective_pid:
            raise RuntimeError(
                "No active managed application. Please launch an application first using "
                "launch_app."
            )
        from wayland_computer_use_mcp.process import is_responsive

        if not is_responsive(effective_pid):
            raise RuntimeError(
                f"Application with PID {effective_pid} is not running or responsive. "
                "Please launch an application first using launch_app."
            )

    def ensure_initialized(self, app_id: str | None = None) -> None:
        """Synchronously ensures the selected SessionDriver is initialized."""
        with self._lock:
            if self.is_active():
                return
        self._async_thread.run(self.initialize(app_id=app_id))

    async def initialize(self, app_id: str | None = None) -> None:
        """Initializes the active SessionDriver (Mock, Blackbox, or HostPassthrough)."""
        self.active_app_id = app_id or "default"
        driver = self.active_driver()
        with self._lock:
            if self._initialized and driver.is_active():
                return

        self._last_error = None
        try:
            await driver.initialize(self.active_app_id)
            set_active_context(driver.context())
            self._initialized = True
        except Exception as exc:
            import traceback

            err_detail = f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
            self._last_error = f"{err_detail} | {traceback.format_exc()}"
            logger.warning("Session driver (%s) initialization failed: %s", driver.name, err_detail)
            if isinstance(exc, FileNotFoundError) and driver is self._host:
                logger.info("D-Bus socket unavailable; activating MockSessionDriver.")
                self._mock.activate_sync()
                set_active_context(self._mock.context())
                self._initialized = True
            else:
                self._initialized = False
                if driver is self._host:
                    raise RuntimeError(
                        f"RemoteDesktop / ScreenCast portal negotiation failed: {err_detail}. "
                        "Please ensure the OS permission prompt was allowed."
                    ) from exc
                raise

    def _ensure_host_portal_ready(self) -> None:
        """Promotes lazy host passthrough to portal control on visual/physical use."""
        driver = self.active_driver()
        _, _, effective_lazy = resolve_effective_display_mode(get_config())
        if (
            driver is self._host
            and effective_lazy
            and self._host.rd_client.pipewire_node_id is None
        ):
            self._host.ensure_dialogs_above()
            self._async_thread.run(self._host.ensure_portal_negotiated(self.active_app_id))

    def _resolve_target_title(self) -> str | None:
        """Resolves target window title from AT-SPI accessible tree if unset."""
        if self.target_title:
            return self.target_title
        if not self.target_pid:
            return None
        try:
            from wayland_computer_use_mcp.a11y import (
                extract_active_window_node,
                get_application_tree,
            )

            tree = get_application_tree(self.target_pid)
            win = extract_active_window_node(tree)
            if win and win.get("name"):
                self.target_title = str(win["name"])
                return self.target_title
        except Exception:
            pass
        return None

    @staticmethod
    def _extract_tree_window_bounds(tree: dict[str, Any]) -> tuple[int, int, int, int] | None:
        """Extracts non-zero [x, y, w, h] window bounds from AT-SPI application tree."""
        from wayland_computer_use_mcp.a11y import extract_active_window_node

        def _valid(b: Any) -> tuple[int, int, int, int] | None:
            if isinstance(b, list) and len(b) == 4 and b[2] > 0 and b[3] > 0:
                return int(b[0]), int(b[1]), int(b[2]), int(b[3])
            return None

        win = extract_active_window_node(tree)
        if win:
            w_bounds = _valid(win.get("bounds"))
            if w_bounds:
                return w_bounds
        return _valid(tree.get("bounds"))

    def _resolve_target_geometry(self) -> tuple[int, int, int, int] | None:
        """Resolves active target window geometry via driver compositor or AT-SPI fallback."""
        if not self.target_pid:
            return None
        title = self._resolve_target_title()
        geom = self.active_driver().query_window_geometry(pid=self.target_pid, title=title)
        if geom:
            return geom
        try:
            from wayland_computer_use_mcp.a11y import get_application_tree

            return self._extract_tree_window_bounds(get_application_tree(self.target_pid))
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
        driver = self.active_driver()
        if not self._initialized:
            if driver is self._mock:
                self._mock.activate_sync()
                self._initialized = True
            else:
                raise RuntimeError(
                    "Session is not initialized. "
                    + (f"Last error: {self._last_error}" if self._last_error else "")
                )

        if driver is self._mock:
            geom = None
        else:
            geom = self._resolve_target_geometry()
            if geom:
                wx, wy, ww, wh = geom
                self.window_offset_x = wx
                self.window_offset_y = wy
                global_clamper.set_bounds(ww, wh)
                global_geometry_detector.record_capture(ww, wh, wx, wy)
                time.sleep(0.05)

        self._ensure_host_portal_ready()
        frame = driver.capture_output_frame()
        if frame is None:
            if self._last_frame is not None:
                frame = self._last_frame.copy()
            else:
                frame = Image.new("RGBA", (1920, 1080), color=(40, 44, 52, 255))

        self._last_frame = frame

        if not self.target_pid or not geom:
            global_clamper.set_bounds(frame.width, frame.height)
            global_geometry_detector.record_capture(frame.width, frame.height, 0, 0)

        if crop_box:
            result_frame = crop_element(frame, *crop_box)
        elif self.target_pid and geom:
            wx, wy, ww, wh = geom
            scale = 1.0
            try:
                from wayland_computer_use_mcp.compositors import get_display_scale

                scale = max(1.0, float(get_display_scale(wx, wy) or 1.0))
            except Exception:
                scale = 1.0
            self.window_scale = scale
            if scale > 1.0:
                sx = int(round(wx * scale))
                sy = int(round(wy * scale))
                sw = int(round(ww * scale))
                sh = int(round(wh * scale))
                if (
                    (ww < frame.width or wh < frame.height)
                    and sx + sw <= frame.width + 2
                    and sy + sh <= frame.height + 2
                ):
                    wx, wy, ww, wh = sx, sy, sw, sh
            result_frame = crop_element(frame, wx, wy, ww, wh)
        else:
            result_frame = frame

        if save_to_disk:
            try:
                frame_path = get_artifacts_dir("screenshots") / filename
                result_frame.save(frame_path)
                self.last_saved_frame_path = str(frame_path)
                prune_artifacts("screenshots")
            except Exception as exc:
                logger.debug("Failed to save frame: %s", exc)
        return result_frame

    def crop_element(self, frame: Image.Image, x: int, y: int, w: int, h: int) -> Image.Image:
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
        self._ensure_host_portal_ready()
        global_preemption_manager.check_preemption()
        if not self.target_pid:
            raise RuntimeError(
                "No active managed application. "
                "Action blocked to prevent unintended desktop interaction."
            )
        geom = self._resolve_target_geometry()
        if not geom:
            if self.active_driver().is_isolated():
                if self._last_frame is not None:
                    fw, fh = self._last_frame.size
                else:
                    fw, fh = global_clamper.dimensions
                if fw <= 0 or fh <= 0:
                    fw, fh = 1280, 800
                geom = (0, 0, fw, fh)
            else:
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
        try:
            from wayland_computer_use_mcp.compositors import get_display_scale

            self.window_scale = max(1.0, float(get_display_scale(wx, wy) or 1.0))
        except Exception:
            self.window_scale = 1.0
        global_clamper.set_bounds(ww, wh)

    def dispatch_click(
        self,
        x: int,
        y: int,
        button: str = "left",
        coordinate_space: Literal["logical", "physical"] = "logical",
    ) -> str:
        return self._input.dispatch_click(x, y, button=button, coordinate_space=coordinate_space)

    def dispatch_double_click(
        self,
        x: int,
        y: int,
        button: str = "left",
        coordinate_space: Literal["logical", "physical"] = "logical",
    ) -> str:
        return self._input.dispatch_double_click(
            x, y, button=button, coordinate_space=coordinate_space
        )

    def dispatch_right_click(
        self,
        x: int,
        y: int,
        coordinate_space: Literal["logical", "physical"] = "logical",
    ) -> str:
        return self._input.dispatch_right_click(x, y, coordinate_space=coordinate_space)

    def dispatch_hover(
        self,
        x: int,
        y: int,
        duration_ms: int = 500,
        coordinate_space: Literal["logical", "physical"] = "logical",
    ) -> str:
        return self._input.dispatch_hover(
            x, y, duration_ms=duration_ms, coordinate_space=coordinate_space
        )

    def dispatch_drag(
        self,
        start_x: int,
        start_y: int,
        end_x: int,
        end_y: int,
        coordinate_space: Literal["logical", "physical"] = "logical",
    ) -> str:
        return self._input.dispatch_drag(
            start_x, start_y, end_x, end_y, coordinate_space=coordinate_space
        )

    def dispatch_scroll(self, dx: int, dy: int) -> str:
        return self._input.dispatch_scroll(dx, dy)

    def dispatch_type_text(
        self,
        text: str,
        x: int | None = None,
        y: int | None = None,
        coordinate_space: Literal["logical", "physical"] = "logical",
    ) -> str:
        return self._input.dispatch_type_text(text, x=x, y=y, coordinate_space=coordinate_space)

    def dispatch_key_combination(self, keys: list[str]) -> str:
        return self._input.dispatch_key_combination(keys)

    def dispatch_clipboard_read(self) -> str:
        return self._input.dispatch_clipboard_read()

    def dispatch_clipboard_write(self, text: str) -> str:
        return self._input.dispatch_clipboard_write(text)

    def _stop_session_resources(self) -> None:
        """Closes host portal, AT-SPI listeners, blackbox sandbox, and mock state."""
        self._host.close()
        try:
            from wayland_computer_use_mcp.a11y import close_atspi_pool, global_event_listener

            global_event_listener.stop()
            close_atspi_pool()
        except Exception:
            pass
        self._blackbox.close()
        self._mock.close()
        self._pid_states.clear()
        set_active_context(None)
        self._initialized = False

    def close_virtual_if_idle(self) -> bool:
        """Tears down the isolated virtual window when no managed apps remain."""
        effective_mode, _, _ = resolve_effective_display_mode(get_config())
        if effective_mode != "virtual" and not self._blackbox.is_active():
            return False
        self._stop_session_resources()
        return True

    def close(self) -> None:
        """Cleans up active session drivers, event listeners, and async thread."""
        self._stop_session_resources()
        self._async_thread.stop()
