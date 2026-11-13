"""Driver protocol separating MCP tools from blackbox vs host-passthrough internals."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from wayland_computer_use_mcp.core.context import InputCapabilities, InputSink, SessionContext

if TYPE_CHECKING:
    from PIL import Image


@runtime_checkable
class SessionDriver(Protocol):
    """Unified session driver implemented by BlackboxDriver and HostPassthroughDriver."""

    name: str

    def context(self) -> SessionContext:
        """Returns the immutable/active SessionContext for this driver."""
        ...

    def is_active(self) -> bool:
        """Returns True when the backing display/input session is ready."""
        ...

    def is_isolated(self) -> bool:
        """Returns True when running in an isolated blackbox sandbox."""
        ...

    async def initialize(self, app_id: str = "default") -> None:
        """Starts or attaches the underlying display, bus, and input session."""
        ...

    def close(self) -> None:
        """Tears down session resources cleanly."""
        ...

    def get_input_sink(self) -> InputSink | None:
        """Returns active physical/virtual input injector."""
        ...

    def input_capabilities(self) -> InputCapabilities:
        """Describes active pointer/keyboard input availability."""
        ...

    def capture_output_frame(self) -> Image.Image | None:
        """Captures the current full compositor/monitor output frame."""
        ...

    def query_window_geometry(
        self, pid: int | None = None, title: str | None = None
    ) -> tuple[int, int, int, int] | None:
        """Resolves (x, y, width, height) geometry for a target window."""
        ...

    def activate_window(self, pid: int | None = None, title: str | None = None) -> bool:
        """Focuses and raises a target window."""
        ...

    def set_window_state(self, pid: int, state: str) -> bool:
        """Applies window state ('minimize', 'maximize', 'restore', 'close')."""
        ...

    def read_clipboard(self) -> str | None:
        """Reads text from the driver's clipboard channel when supported."""
        ...

    def write_clipboard(self, text: str) -> bool:
        """Writes text to the driver's clipboard channel when supported."""
        ...

    def ensure_dialogs_above(self) -> bool:
        """Brings host permission dialogs forward when applicable (no-op for blackbox)."""
        ...

    def metadata(self) -> dict[str, Any]:
        """Returns diagnostic metadata exposed by launch_app/restart_app."""
        ...
