"""Host desktop passthrough driver implementing SessionDriver for live host windows."""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from PIL import Image

import wayland_computer_use_mcp.compositors as compositors_pkg
from wayland_computer_use_mcp.config import get_config
from wayland_computer_use_mcp.core.context import InputCapabilities, InputSink, SessionContext
from wayland_computer_use_mcp.libei import BTN_LEFT, EIClient
from wayland_computer_use_mcp.portal.remotedesktop import (
    AsyncLoopThread,
    RemoteDesktopClient,
)
from wayland_computer_use_mcp.portal.screencast import ScreenCastPipeline

logger = logging.getLogger(__name__)


class HostInputSink:
    """Unified InputSink bridging libei (primary) and RemoteDesktop D-Bus fallback."""

    def __init__(self, rd_client: RemoteDesktopClient) -> None:
        self.rd_client = rd_client

    @property
    def _ei(self) -> EIClient | None:
        return self.rd_client.ei_client

    @property
    def has_absolute_pointer(self) -> bool:
        ei = self._ei
        if ei is None:
            return False
        if getattr(ei, "pointer_abs_device", None):
            return True
        pointer_dev = getattr(ei, "pointer_device", None)
        rel_dev = getattr(ei, "pointer_rel_device", None)
        return bool(pointer_dev and pointer_dev is not rel_dev)

    @property
    def has_scroll(self) -> bool:
        ei = self._ei
        return bool(ei and getattr(ei, "scroll_device", None))

    @property
    def keyboard_device(self) -> Any:
        ei = self._ei
        return getattr(ei, "keyboard_device", None) if ei is not None else None

    def _require_pointer_targetable(self) -> None:
        ei = self._ei
        if (
            ei is not None
            and getattr(ei, "ctx", None) is not None
            and not self.has_absolute_pointer
            and getattr(ei, "pointer_device", None) is not None
            and not getattr(self.rd_client, "pipewire_node_id", None)
        ):
            raise RuntimeError(
                "Cannot move pointer to absolute coordinates: compositor EIS only provided "
                "a relative pointer without a ScreenCast stream. Share a screen or window "
                "in the OS portal permission prompt to enable absolute pointer control."
            )

    def pointer_motion_absolute(self, x: float, y: float) -> Any:
        if self.has_absolute_pointer and self._ei is not None:
            return self._ei.pointer_motion_absolute(x, y)
        self._require_pointer_targetable()
        return self.rd_client.notify_pointer_motion_absolute(x, y)

    def button_down(self, button_code: int = BTN_LEFT) -> Any:
        if self.has_absolute_pointer and self._ei is not None:
            return self._ei.button_down(button_code)
        self._require_pointer_targetable()
        return self.rd_client.notify_pointer_button(button_code, True)

    def button_up(self, button_code: int = BTN_LEFT) -> Any:
        if self.has_absolute_pointer and self._ei is not None:
            return self._ei.button_up(button_code)
        self._require_pointer_targetable()
        return self.rd_client.notify_pointer_button(button_code, False)

    def button_click(self, button_code: int = BTN_LEFT) -> Any:
        if self.has_absolute_pointer and self._ei is not None:
            return self._ei.button_click(button_code)
        self._require_pointer_targetable()
        self.rd_client.notify_pointer_button(button_code, True)
        time.sleep(0.008)
        return self.rd_client.notify_pointer_button(button_code, False)

    def double_click(self, button_code: int = BTN_LEFT) -> Any:
        if self.has_absolute_pointer and self._ei is not None:
            return self._ei.double_click(button_code)
        self._require_pointer_targetable()
        self.rd_client.notify_pointer_button(button_code, True)
        self.rd_client.notify_pointer_button(button_code, False)
        time.sleep(0.04)
        self.rd_client.notify_pointer_button(button_code, True)
        return self.rd_client.notify_pointer_button(button_code, False)

    def scroll(self, dx: float, dy: float) -> Any:
        if self.has_scroll and self._ei is not None:
            try:
                return self._ei.scroll(dx, dy)
            except Exception as exc:
                logger.warning("EI scroll failed, falling back to RemoteDesktop: %s", exc)
        return self.rd_client.notify_pointer_axis(dx, dy)

    def can_type_text(self, text: str) -> bool:
        if self.keyboard_device and self._ei is not None:
            return self._ei.can_type_text(text)
        return False

    def resolve_key_name(self, key_name: str) -> int | None:
        if self._ei is not None:
            return self._ei.resolve_key_name(key_name)
        return None

    def type_char(self, ch: str) -> bool:
        if self.keyboard_device and self._ei is not None:
            return self._ei.type_char(ch)
        return False

    def key_press(self, keycode: int, is_down: bool) -> Any:
        if self.keyboard_device and self._ei is not None:
            return self._ei.key_press(keycode, is_down)
        return self.rd_client.notify_keyboard_keycode(keycode, is_down)

    def key_combination(self, keycodes: list[int]) -> Any:
        if self.keyboard_device and self._ei is not None:
            return self._ei.key_combination(keycodes)
        for code in keycodes:
            self.rd_client.notify_keyboard_keycode(code, True)
            time.sleep(0.003)
        time.sleep(0.01)
        for code in reversed(keycodes):
            self.rd_client.notify_keyboard_keycode(code, False)
            time.sleep(0.003)
        return True

    def close(self) -> None:
        if self._ei is not None:
            self._ei.close()


class HostPassthroughDriver:
    """Secondary SessionDriver bridging live host windows via XDG Portals and Host Compositors."""

    name: str = "host_passthrough"

    def __init__(self, async_thread: AsyncLoopThread) -> None:
        self._async_thread = async_thread
        self.rd_client = RemoteDesktopClient(async_thread)
        self.screencast = ScreenCastPipeline()
        self._sink = HostInputSink(self.rd_client)
        self._initialized: bool = False
        self._portal_negotiated: bool = False

    def is_isolated(self) -> bool:
        return False

    def context(self) -> SessionContext:
        return SessionContext(
            mode="passthrough",
            xdg_runtime_dir=os.environ.get("XDG_RUNTIME_DIR"),
            dbus_address=os.environ.get("DBUS_SESSION_BUS_ADDRESS"),
            wayland_display=os.environ.get("WAYLAND_DISPLAY"),
            atspi_bus_address=os.environ.get("AT_SPI_BUS_ADDRESS"),
            headed=True,
            is_isolated=False,
        )

    def is_active(self) -> bool:
        return self._initialized and (
            self.rd_client.pipewire_node_id is not None
            or self._portal_negotiated
            or get_config().lazy_portal
        )

    async def initialize(self, app_id: str = "default", eager: bool | None = None) -> None:
        lazy = get_config().lazy_portal if eager is None else not eager
        if lazy:
            self._initialized = True
            return
        await self.ensure_portal_negotiated(app_id)

    async def ensure_portal_negotiated(self, app_id: str = "default") -> None:
        if self._portal_negotiated and self.rd_client.pipewire_node_id is not None:
            return
        _, node_id, _, _ = await self.rd_client.negotiate(app_id)
        if node_id:
            self.screencast.setup_pipeline(node_id)
        self._portal_negotiated = True
        self._initialized = True

    def get_input_sink(self) -> InputSink:
        if self.rd_client.ei_client is None:
            self.rd_client.ei_client = EIClient(fd=None)
        return self._sink

    def input_capabilities(self) -> InputCapabilities:
        ei = self.rd_client.ei_client
        return InputCapabilities(
            has_absolute_pointer=self._sink.has_absolute_pointer,
            has_relative_pointer=bool(ei and getattr(ei, "relative_device", None) is not None),
            has_keyboard=bool(self._sink.keyboard_device is not None),
            is_isolated=False,
        )

    def capture_output_frame(self) -> Image.Image | None:
        return self.screencast.pull_frame()

    def query_window_geometry(
        self, pid: int | None = None, title: str | None = None
    ) -> tuple[int, int, int, int] | None:
        return compositors_pkg.query_window_geometry(pid=pid, title=title)

    def activate_window(self, pid: int | None = None, title: str | None = None) -> bool:
        return compositors_pkg.activate_and_raise_window(pid=pid, title=title)

    def set_window_state(self, pid: int, state: str) -> bool:
        return compositors_pkg.set_window_state(pid=pid, state=state)  # type: ignore[arg-type]

    def read_clipboard(self) -> str | None:
        if getattr(self.rd_client, "clipboard_enabled", False):
            return self.rd_client.read_clipboard_text()
        return None

    def write_clipboard(self, text: str) -> bool:
        if getattr(self.rd_client, "clipboard_enabled", False):
            return bool(self.rd_client.write_clipboard_text(text))
        return False

    def ensure_dialogs_above(self) -> bool:
        return compositors_pkg.ensure_portal_dialogs_above()

    def metadata(self) -> dict[str, Any]:
        return {
            "driver": self.name,
            "session_handle": self.rd_client.session_handle,
            "restore_token": self.rd_client.restore_token,
            "eis_connected": self.rd_client.eis_fd is not None,
            "isolated": False,
        }

    def close(self) -> None:
        self.screencast.stop()
        self.rd_client.close()
        self._portal_negotiated = False
        self._initialized = False
