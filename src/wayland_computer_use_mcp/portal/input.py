"""Unified clamped input dispatcher operating on InputSink contracts."""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from wayland_computer_use_mcp.clipboard import read_system_clipboard, write_system_clipboard
from wayland_computer_use_mcp.config import get_config
from wayland_computer_use_mcp.core.context import InputSink
from wayland_computer_use_mcp.libei import BTN_LEFT, BTN_MIDDLE, BTN_RIGHT, KEY_LEFTCTRL, KEY_V
from wayland_computer_use_mcp.security import global_clamper, global_shortcut_filter

logger = logging.getLogger(__name__)

_BUTTON_CODES = {
    "left": BTN_LEFT,
    "right": BTN_RIGHT,
    "middle": BTN_MIDDLE,
}


class InputDispatcher:
    """Dispatches pointer, scroll, keyboard, and clipboard actions via active InputSink."""

    def __init__(
        self,
        get_sink_fn: Callable[[], InputSink | None],
        verify_preconditions_fn: Callable[[], None],
        get_offsets_fn: Callable[[], tuple[int, int]],
    ) -> None:
        self._get_sink = get_sink_fn
        self._verify_preconditions = verify_preconditions_fn
        self._get_offsets = get_offsets_fn

    @staticmethod
    def _apply_action_delay() -> None:
        delay = get_config().action_delay_seconds
        if delay > 0:
            time.sleep(delay)

    def _require_sink(self, *, require_pointer: bool = False) -> InputSink:
        sink = self._get_sink()
        if sink is None:
            raise RuntimeError(
                "No active input sink available; use semantic interact_with_node actions "
                "or run with kwin_wayland / labwc / sway for coordinate input."
            )
        if require_pointer and not getattr(sink, "has_absolute_pointer", False):
            # Allow HostInputSink to attempt its RemoteDesktop fallback or raise explicit error
            from wayland_computer_use_mcp.compat.host_driver import HostInputSink

            if not isinstance(sink, HostInputSink):
                raise RuntimeError(
                    "Isolated virtual compositor has no pointer input sink; use semantic "
                    "interact_with_node actions or run with kwin_wayland / labwc / sway "
                    "for coordinate input."
                )
        return sink

    def dispatch_click(self, x: int, y: int, button: str = "left") -> str:
        """Validates bounds and executes mouse click."""
        self._verify_preconditions()
        cx, cy = global_clamper.validate_and_clamp(x, y)
        sink = self._require_sink(require_pointer=True)
        button_code = _BUTTON_CODES.get(button.lower(), BTN_LEFT)
        offset_x, offset_y = self._get_offsets()

        sink.pointer_motion_absolute(float(offset_x + cx), float(offset_y + cy))
        time.sleep(0.005)
        sink.button_click(button_code)
        self._apply_action_delay()
        return f"Clicked {button} button at ({cx}, {cy})"

    def dispatch_double_click(self, x: int, y: int, button: str = "left") -> str:
        """Performs a clamped double-click."""
        self._verify_preconditions()
        cx, cy = global_clamper.validate_and_clamp(x, y)
        sink = self._require_sink(require_pointer=True)
        button_code = _BUTTON_CODES.get(button.lower(), BTN_LEFT)
        offset_x, offset_y = self._get_offsets()

        sink.pointer_motion_absolute(float(offset_x + cx), float(offset_y + cy))
        time.sleep(0.005)
        sink.double_click(button_code)
        self._apply_action_delay()
        return f"Double-clicked {button} button at ({cx}, {cy})"

    def dispatch_right_click(self, x: int, y: int) -> str:
        """Performs a clamped right-click."""
        return self.dispatch_click(x, y, button="right")

    def dispatch_hover(self, x: int, y: int, duration_ms: int = 200) -> str:
        """Positions cursor at coordinates without clicking to trigger hover states."""
        self._verify_preconditions()
        cx, cy = global_clamper.validate_and_clamp(x, y)
        sink = self._require_sink(require_pointer=True)
        offset_x, offset_y = self._get_offsets()

        sink.pointer_motion_absolute(float(offset_x + cx), float(offset_y + cy))
        if duration_ms > 0:
            time.sleep(duration_ms / 1000.0)
        self._apply_action_delay()
        return f"Hovered pointer at ({cx}, {cy}) for {duration_ms}ms"

    def dispatch_drag(self, start_x: int, start_y: int, end_x: int, end_y: int) -> str:
        """Validates bounds and performs clamped drag-and-drop gesture."""
        self._verify_preconditions()
        s_x, s_y = global_clamper.validate_and_clamp(start_x, start_y)
        e_x, e_y = global_clamper.validate_and_clamp(end_x, end_y)
        sink = self._require_sink(require_pointer=True)
        offset_x, offset_y = self._get_offsets()

        sx, sy = float(offset_x + s_x), float(offset_y + s_y)
        ex, ey = float(offset_x + e_x), float(offset_y + e_y)
        steps = 4

        sink.pointer_motion_absolute(sx, sy)
        time.sleep(0.01)
        sink.button_down(BTN_LEFT)
        time.sleep(0.01)
        for i in range(1, steps + 1):
            cur_x = sx + (ex - sx) * (i / steps)
            cur_y = sy + (ey - sy) * (i / steps)
            sink.pointer_motion_absolute(cur_x, cur_y)
            time.sleep(0.005)
        time.sleep(0.01)
        sink.button_up(BTN_LEFT)
        self._apply_action_delay()
        return f"Dragged from ({s_x}, {s_y}) to ({e_x}, {e_y})"

    def dispatch_scroll(self, dx: int, dy: int) -> str:
        """Dispatches scroll deltas."""
        self._verify_preconditions()
        sink = self._require_sink(require_pointer=False)
        sink.scroll(float(dx), float(dy))
        self._apply_action_delay()
        return f"Scrolled dx={dx}, dy={dy}"

    def dispatch_type_text(self, text: str, x: int | None = None, y: int | None = None) -> str:
        """Types text via hybrid fast key events + clipboard paste fallback."""
        self._verify_preconditions()
        prefix = ""
        if x is not None and y is not None:
            prefix = self.dispatch_click(x, y, button="left") + ". "
            time.sleep(0.015)

        sink = self._require_sink(require_pointer=False)
        if len(text) > 30 or "\n" in text or not sink.can_type_text(text):
            if self._paste_clipboard_text(sink, text):
                self._apply_action_delay()
                return f"{prefix}Pasted {len(text)} characters via clipboard"

        typed_ok = True
        for char in text:
            if not sink.type_char(char):
                typed_ok = False
                break
            time.sleep(0.002)

        if not typed_ok:
            self._paste_clipboard_text(sink, text)

        self._apply_action_delay()
        return f"{prefix}Typed {len(text)} characters"

    def dispatch_key_combination(self, keys: list[str]) -> str:
        """Dispatches key combination while strictly blocking system shortcuts."""
        self._verify_preconditions()
        keycodes = global_shortcut_filter.validate_and_resolve(keys)
        sink = self._require_sink(require_pointer=False)

        updated: list[int] = []
        for k, code in zip(keys, keycodes, strict=True):
            stripped = k.strip()
            mapped = sink.resolve_key_name(stripped) if len(stripped) == 1 else None
            updated.append(mapped if isinstance(mapped, int) else code)

        sink.key_combination(updated)
        self._apply_action_delay()
        return f"Dispatched key combination: {'+'.join(keys)}"

    def dispatch_clipboard_read(self) -> str:
        """Reads current text from the system clipboard via active backends."""
        self._verify_preconditions()
        return read_system_clipboard()

    def dispatch_clipboard_write(self, text: str) -> str:
        """Writes text into the system clipboard via active backends."""
        self._verify_preconditions()
        if write_system_clipboard(text):
            return f"Copied {len(text)} characters to clipboard."
        return "Failed to write to clipboard: no supported clipboard backend available"

    def _paste_clipboard_text(self, sink: InputSink, text: str) -> bool:
        """Copies text to clipboard and presses Ctrl+V through the active InputSink."""
        try:
            if not write_system_clipboard(text):
                return False
            time.sleep(0.015)
            v_code = sink.resolve_key_name("v") or KEY_V
            sink.key_press(KEY_LEFTCTRL, True)
            time.sleep(0.005)
            sink.key_press(v_code, True)
            time.sleep(0.005)
            sink.key_press(v_code, False)
            time.sleep(0.005)
            sink.key_press(KEY_LEFTCTRL, False)
            return True
        except Exception as exc:
            logger.warning("Clipboard paste fallback failed: %s", exc)
            return False


__all__ = ["Any", "InputDispatcher"]
