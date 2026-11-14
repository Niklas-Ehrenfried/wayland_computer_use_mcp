"""BlackboxDriver implementing SessionDriver inside the isolated nested/headless sandbox."""

from __future__ import annotations

import logging
import os
from typing import Any

from PIL import Image

from wayland_computer_use_mcp.blackbox.adapters import (
    BlackboxCompositorAdapter,
    select_blackbox_adapter,
)
from wayland_computer_use_mcp.config import DEFAULT_VIRTUAL_CMD, get_config
from wayland_computer_use_mcp.core.context import InputSink, SessionContext
from wayland_computer_use_mcp.portal.virtual import VirtualSessionManager

logger = logging.getLogger(__name__)


class BlackboxDriver:
    """Isolated sandbox SessionDriver backed by a nested/headless Wayland compositor."""

    name: str = "blackbox"

    def __init__(self, sandbox: VirtualSessionManager | None = None) -> None:
        self.sandbox = sandbox or VirtualSessionManager()
        self._adapter: BlackboxCompositorAdapter | None = None
        self._input_sink: InputSink | None = None
        self._backend_kind: str = "uninitialized"
        self._initialized: bool = False

    def is_isolated(self) -> bool:
        return True

    def context(self) -> SessionContext:
        return self.sandbox.to_context(get_config())

    def is_active(self) -> bool:
        return self._initialized and self.sandbox.is_running

    async def initialize(self, app_id: str = "default") -> None:
        if self._initialized and self.sandbox.is_running:
            return
        cfg = get_config()
        explicit = cfg.virtual_compositor_cmd.strip()
        is_auto = explicit.lower() in ("", DEFAULT_VIRTUAL_CMD)
        adapter = select_blackbox_adapter(None if is_auto else explicit)
        self.sandbox.ensure_started(cfg, adapter=adapter)
        ctx = self.context()
        self._adapter = self.sandbox.adapter or adapter

        sink, kind = await self._adapter.connect_input(ctx)
        self._input_sink = sink
        self._backend_kind = kind
        self._initialized = True
        logger.info(
            "Initialized blackbox driver (adapter=%s, input=%s, active=%s)",
            self._adapter.name,
            kind,
            sink is not None,
        )

    def get_input_sink(self) -> InputSink | None:
        return self._input_sink

    def capture_output_frame(self) -> Image.Image | None:
        if self._adapter is not None:
            return self._adapter.capture_output_frame(self.context())
        from wayland_computer_use_mcp.portal.screencast import capture_native_desktop_frame

        return capture_native_desktop_frame()

    @staticmethod
    def _resolve_window_hints(pid: int | None, title: str | None) -> tuple[str | None, str | None]:
        """Resolves (title, app_hint) from AT-SPI or tracked process target."""
        resolved_title = title
        app_hint: str | None = None
        if pid:
            try:
                from wayland_computer_use_mcp.process import _lock, _proc_metadata

                with _lock:
                    meta = _proc_metadata.get(pid)
                if meta and meta.get("target"):
                    app_hint = os.path.splitext(os.path.basename(str(meta["target"])))[0]
            except Exception:
                pass
            if not resolved_title:
                try:
                    from wayland_computer_use_mcp.a11y import get_application_tree

                    tree = get_application_tree(pid)
                    for child in tree.get("children", []):
                        if child.get("role") in ("window", "frame") and child.get("name"):
                            resolved_title = str(child["name"])
                            break
                except Exception:
                    pass
        return resolved_title, app_hint

    def query_window_geometry(
        self, pid: int | None = None, title: str | None = None
    ) -> tuple[int, int, int, int] | None:
        if self._adapter is None:
            return None
        return self._adapter.query_window_geometry(self.context(), pid=pid, title=title)

    def activate_window(self, pid: int | None = None, title: str | None = None) -> bool:
        if self._adapter is None:
            return False
        resolved_title, app_hint = self._resolve_window_hints(pid, title)
        return self._adapter.activate_window(
            self.context(), pid=pid, title=resolved_title, app_hint=app_hint
        )

    def set_window_state(self, pid: int, state: str) -> bool:
        if self._adapter is None:
            return False
        resolved_title, app_hint = self._resolve_window_hints(pid, None)
        return self._adapter.set_window_state(
            self.context(), pid=pid, state=state, title=resolved_title, app_hint=app_hint
        )

    def read_clipboard(self) -> str | None:
        from wayland_computer_use_mcp.wayland_clipboard import read_wayland_clipboard

        return read_wayland_clipboard()

    def write_clipboard(self, text: str) -> bool:
        from wayland_computer_use_mcp.wayland_clipboard import write_wayland_clipboard

        return write_wayland_clipboard(text)

    def ensure_dialogs_above(self) -> bool:
        return False

    def metadata(self) -> dict[str, Any]:
        return {
            "driver": self.name,
            "adapter": self._adapter.name if self._adapter else None,
            "backend_kind": self._backend_kind,
            "isolated": True,
            "session_handle": None,
            "eis_connected": self._input_sink is not None,
        }

    def close(self) -> None:
        if self._input_sink is not None:
            try:
                self._input_sink.close()
            except Exception:
                pass
            self._input_sink = None
        if self._adapter is not None and hasattr(self._adapter, "close"):
            try:
                self._adapter.close()
            except Exception:
                pass
        self._adapter = None
        self.sandbox.stop()
        self._initialized = False
