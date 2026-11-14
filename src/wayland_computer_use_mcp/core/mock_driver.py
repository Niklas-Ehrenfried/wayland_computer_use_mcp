"""Dedicated MockSessionDriver implementing SessionDriver for tests and CI."""

from __future__ import annotations

from typing import Any

from PIL import Image, ImageDraw

from wayland_computer_use_mcp.core.context import InputSink, SessionContext


class MockInputSink:
    """In-memory no-op InputSink used when mock mode is active."""

    has_absolute_pointer: bool = True
    has_scroll: bool = True
    keyboard_device: int = 1

    def __init__(self) -> None:
        self.events: list[tuple[str, Any]] = []

    def pointer_motion_absolute(self, x: float, y: float) -> bool:
        self.events.append(("motion", (x, y)))
        return True

    def button_down(self, button_code: int = 0x110) -> bool:
        self.events.append(("button_down", button_code))
        return True

    def button_up(self, button_code: int = 0x110) -> bool:
        self.events.append(("button_up", button_code))
        return True

    def button_click(self, button_code: int = 0x110) -> bool:
        self.events.append(("click", button_code))
        return True

    def double_click(self, button_code: int = 0x110) -> bool:
        self.events.append(("double_click", button_code))
        return True

    def scroll(self, dx: float, dy: float) -> bool:
        self.events.append(("scroll", (dx, dy)))
        return True

    def type_char(self, ch: str) -> bool:
        self.events.append(("type_char", ch))
        return True

    def key_press(self, keycode: int, is_down: bool) -> bool:
        self.events.append(("key_press", (keycode, is_down)))
        return True

    def key_combination(self, keycodes: list[int]) -> bool:
        self.events.append(("key_combination", list(keycodes)))
        return True

    def can_type_text(self, text: str) -> bool:
        return True

    def resolve_key_name(self, key_name: str) -> int | None:
        return None

    def close(self) -> None:
        self.events.clear()


class MockSessionDriver:
    """Self-contained mock SessionDriver without host D-Bus/Wayland side effects."""

    name: str = "mock"

    def __init__(self) -> None:
        self._initialized: bool = False
        self._sink = MockInputSink()
        self._clipboard: str = ""
        self._frame: Image.Image | None = None

    def is_isolated(self) -> bool:
        return True

    def context(self) -> SessionContext:
        return SessionContext(mode="mock", headed=False, is_isolated=True)

    def is_active(self) -> bool:
        return self._initialized

    async def initialize(self, app_id: str = "default") -> None:
        self.activate_sync()

    def activate_sync(self) -> None:
        """Initializes mock surface synchronously."""
        self._initialized = True
        if self._frame is None:
            img = Image.new("RGBA", (1920, 1080), color=(45, 52, 54, 255))
            draw = ImageDraw.Draw(img)
            draw.rectangle(
                [50, 50, 600, 400], fill=(9, 132, 227, 255), outline=(255, 255, 255, 255)
            )
            draw.text((70, 70), "wayland-computer-use-mcp Mock Surface", fill=(255, 255, 255, 255))
            self._frame = img

    def get_input_sink(self) -> InputSink:
        return self._sink

    def capture_output_frame(self) -> Image.Image | None:
        if self._frame is None:
            self.activate_sync()
        return self._frame.copy() if self._frame is not None else None

    def query_window_geometry(
        self, pid: int | None = None, title: str | None = None
    ) -> tuple[int, int, int, int] | None:
        return (0, 0, 1920, 1080)

    def activate_window(self, pid: int | None = None, title: str | None = None) -> bool:
        return True

    def set_window_state(self, pid: int, state: str) -> bool:
        return True

    def read_clipboard(self) -> str | None:
        return self._clipboard

    def write_clipboard(self, text: str) -> bool:
        self._clipboard = text
        return True

    def ensure_dialogs_above(self) -> bool:
        return False

    def metadata(self) -> dict[str, Any]:
        return {
            "driver": self.name,
            "isolated": True,
            "session_handle": None,
            "eis_connected": True,
        }

    def close(self) -> None:
        self._sink.close()
        self._initialized = False
