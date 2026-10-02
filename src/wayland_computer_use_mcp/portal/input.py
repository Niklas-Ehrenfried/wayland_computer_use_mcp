"""Boundary-clamped input event dispatcher via libei and RemoteDesktop portal."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Callable

from wayland_computer_use_mcp.clipboard import (
    read_system_clipboard,
    write_system_clipboard,
)
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
    global_shortcut_filter,
)

if TYPE_CHECKING:
    from wayland_computer_use_mcp.portal.remotedesktop import RemoteDesktopClient

logger = logging.getLogger(__name__)


class InputDispatcher:
    """Dispatches mouse, keyboard, and clipboard interactions with boundary validation
    and safety checks.
    """

    def __init__(
        self,
        rd_client: RemoteDesktopClient,
        verify_preconditions_fn: Callable[[], None],
        get_offsets_fn: Callable[[], tuple[int, int]],
        is_mock_fn: Callable[[], bool],
        get_ei_client_fn: Callable[[], EIClient],
    ) -> None:
        self.rd_client = rd_client
        self._verify_preconditions = verify_preconditions_fn
        self._get_offsets = get_offsets_fn
        self._is_mock = is_mock_fn
        self._get_ei_client = get_ei_client_fn

    def _apply_action_delay(self) -> None:
        """Applies configured delay after an action so visual interactions are observable."""
        cfg = get_config()
        if cfg.action_delay_seconds > 0:
            time.sleep(cfg.action_delay_seconds)

    def _has_ei_pointer(self) -> bool:
        ei = self._get_ei_client()
        return bool(ei and (ei.pointer_abs_device or ei.pointer_device or ei.button_device))

    def _has_ei_keyboard(self) -> bool:
        ei = self._get_ei_client()
        return bool(ei and ei.keyboard_device)

    def dispatch_click(self, x: int, y: int, button: str = "left") -> str:
        """Validates bounds and executes mouse click."""
        self._verify_preconditions()
        cx, cy = global_clamper.validate_and_clamp(x, y)
        if self._is_mock():
            return f"Clicked {button} button at ({cx}, {cy})"

        btn_map = {
            "left": BTN_LEFT,
            "right": BTN_RIGHT,
            "middle": BTN_MIDDLE,
        }
        button_code = btn_map.get(button.lower(), BTN_LEFT)
        offset_x, offset_y = self._get_offsets()
        target_x = float(offset_x + cx)
        target_y = float(offset_y + cy)

        if self._has_ei_pointer():
            ei_client = self._get_ei_client()
            ei_client.pointer_motion_absolute(target_x, target_y)
            time.sleep(0.02)
            ei_client.button_click(button_code)
        else:
            self.rd_client.notify_pointer_motion_absolute(target_x, target_y)
            time.sleep(0.02)
            self.rd_client.notify_pointer_button(button_code, True)
            time.sleep(0.02)
            self.rd_client.notify_pointer_button(button_code, False)

        self._apply_action_delay()
        return f"Clicked {button} button at ({cx}, {cy})"

    def dispatch_double_click(self, x: int, y: int, button: str = "left") -> str:
        """Performs a clamped double-click."""
        self._verify_preconditions()
        cx, cy = global_clamper.validate_and_clamp(x, y)
        if self._is_mock():
            return f"Double-clicked {button} button at ({cx}, {cy})"

        btn_map = {
            "left": BTN_LEFT,
            "right": BTN_RIGHT,
            "middle": BTN_MIDDLE,
        }
        button_code = btn_map.get(button.lower(), BTN_LEFT)
        offset_x, offset_y = self._get_offsets()
        target_x = float(offset_x + cx)
        target_y = float(offset_y + cy)

        if self._has_ei_pointer():
            ei_client = self._get_ei_client()
            ei_client.pointer_motion_absolute(target_x, target_y)
            time.sleep(0.02)
            ei_client.double_click(button_code)
        else:
            self.rd_client.notify_pointer_motion_absolute(target_x, target_y)
            time.sleep(0.02)
            self.rd_client.notify_pointer_button(button_code, True)
            self.rd_client.notify_pointer_button(button_code, False)
            time.sleep(0.05)
            self.rd_client.notify_pointer_button(button_code, True)
            self.rd_client.notify_pointer_button(button_code, False)

        self._apply_action_delay()
        return f"Double-clicked {button} button at ({cx}, {cy})"

    def dispatch_right_click(self, x: int, y: int) -> str:
        """Performs a clamped right-click."""
        return self.dispatch_click(x, y, button="right")

    def dispatch_hover(self, x: int, y: int, duration_ms: int = 500) -> str:
        """Positions cursor at coordinates without clicking to trigger hover states or tooltips."""
        self._verify_preconditions()
        cx, cy = global_clamper.validate_and_clamp(x, y)
        if self._is_mock():
            return f"Hovered pointer at ({cx}, {cy}) for {duration_ms}ms"

        offset_x, offset_y = self._get_offsets()
        target_x = float(offset_x + cx)
        target_y = float(offset_y + cy)

        if self._has_ei_pointer():
            ei_client = self._get_ei_client()
            ei_client.pointer_motion_absolute(target_x, target_y)
        else:
            self.rd_client.notify_pointer_motion_absolute(target_x, target_y)

        time.sleep(max(10, duration_ms) / 1000.0)
        self._apply_action_delay()
        return f"Hovered pointer at ({cx}, {cy}) for {duration_ms}ms"

    def dispatch_drag(self, start_x: int, start_y: int, end_x: int, end_y: int) -> str:
        """Validates bounds and performs clamped drag-and-drop gesture."""
        self._verify_preconditions()
        s_x, s_y = global_clamper.validate_and_clamp(start_x, start_y)
        e_x, e_y = global_clamper.validate_and_clamp(end_x, end_y)
        if self._is_mock():
            return f"Dragged from ({s_x}, {s_y}) to ({e_x}, {e_y})"

        offset_x, offset_y = self._get_offsets()
        s_target_x = float(offset_x + s_x)
        s_target_y = float(offset_y + s_y)
        e_target_x = float(offset_x + e_x)
        e_target_y = float(offset_y + e_y)

        if self._has_ei_pointer():
            ei_client = self._get_ei_client()
            ei_client.pointer_motion_absolute(s_target_x, s_target_y)
            time.sleep(0.05)
            ei_client.button_down(BTN_LEFT)
            time.sleep(0.05)

            steps = 10
            for i in range(1, steps + 1):
                cur_x = s_target_x + (e_target_x - s_target_x) * (i / steps)
                cur_y = s_target_y + (e_target_y - s_target_y) * (i / steps)
                ei_client.pointer_motion_absolute(cur_x, cur_y)
                time.sleep(0.01)

            time.sleep(0.05)
            ei_client.button_up(BTN_LEFT)
        else:
            self.rd_client.notify_pointer_motion_absolute(s_target_x, s_target_y)
            time.sleep(0.05)
            self.rd_client.notify_pointer_button(BTN_LEFT, True)
            time.sleep(0.05)

            steps = 10
            for i in range(1, steps + 1):
                cur_x = s_target_x + (e_target_x - s_target_x) * (i / steps)
                cur_y = s_target_y + (e_target_y - s_target_y) * (i / steps)
                self.rd_client.notify_pointer_motion_absolute(cur_x, cur_y)
                time.sleep(0.01)

            time.sleep(0.05)
            self.rd_client.notify_pointer_button(BTN_LEFT, False)

        self._apply_action_delay()
        return f"Dragged from ({s_x}, {s_y}) to ({e_x}, {e_y})"

    def dispatch_scroll(self, dx: int, dy: int) -> str:
        """Dispatches scroll deltas."""
        self._verify_preconditions()
        if self._is_mock():
            return f"Scrolled dx={dx}, dy={dy}"

        self.rd_client.notify_pointer_axis(float(dx), float(dy))
        if self._has_ei_pointer():
            try:
                self._get_ei_client().scroll(float(dx), float(dy))
            except Exception:
                pass

        self._apply_action_delay()
        return f"Scrolled dx={dx}, dy={dy}"

    def dispatch_type_text(self, text: str, x: int | None = None, y: int | None = None) -> str:
        """Types text via hybrid fast key events + clipboard paste fallback.

        If x and y coordinates are provided, dispatches a click at (x, y) first to ensure focus.
        Short strings (<= 30 chars without newlines) simulate fast key typing for visual display.
        Longer or multiline strings use automatic clipboard paste for instant, reliable transfer.
        """
        self._verify_preconditions()
        prefix = ""
        if x is not None and y is not None:
            prefix = self.dispatch_click(x, y, button="left") + ". "
            time.sleep(0.05)

        if self._is_mock():
            return f"{prefix}Typed {len(text)} characters"

        ei_client = self._get_ei_client()

        # For long or multiline text, paste immediately via clipboard
        if len(text) > 30 or "\n" in text:
            self._paste_clipboard_text(text)
            self._apply_action_delay()
            return f"{prefix}Pasted {len(text)} characters via clipboard"

        # For short text, simulate fast typing (pacing ~150ms per character)
        can_type_pure = True
        for char in text:
            if not ei_client.type_char(char):
                can_type_pure = False
                break
            time.sleep(0.150)

        if not can_type_pure:
            self._paste_clipboard_text(text)

        self._apply_action_delay()
        return f"{prefix}Typed {len(text)} characters"

    def dispatch_key_combination(self, keys: list[str]) -> str:
        """Dispatches key combination while strictly blocking system shortcuts."""
        self._verify_preconditions()
        keycodes = global_shortcut_filter.validate_and_resolve(keys)
        if self._is_mock():
            return f"Dispatched key combination: {'+'.join(keys)}"

        ei_client = self._get_ei_client()
        ei_client.key_combination(keycodes)
        self._apply_action_delay()
        return f"Dispatched key combination: {'+'.join(keys)}"

    def dispatch_clipboard_read(self) -> str:
        """Reads current text from the system clipboard via available backends."""
        self._verify_preconditions()
        if self._is_mock():
            return ""

        return read_system_clipboard()

    def dispatch_clipboard_write(self, text: str) -> str:
        """Writes text into the system clipboard via available backends."""
        self._verify_preconditions()
        if self._is_mock():
            return f"Copied {len(text)} characters to clipboard."

        success = write_system_clipboard(text)
        if success:
            return f"Copied {len(text)} characters to clipboard."
        return "Failed to write to clipboard: no supported clipboard backend available"

    def _paste_clipboard_text(self, text: str) -> None:
        """Copies text to clipboard using available backend and sends Ctrl+V."""
        try:
            write_system_clipboard(text)
            time.sleep(0.05)
            if self._has_ei_keyboard():
                ei_client = self._get_ei_client()
                ei_client.key_press(KEY_LEFTCTRL, True)
                time.sleep(0.02)
                ei_client.key_press(KEY_V, True)
                time.sleep(0.02)
                ei_client.key_press(KEY_V, False)
                time.sleep(0.02)
                ei_client.key_press(KEY_LEFTCTRL, False)
            else:
                self.rd_client.notify_keyboard_keycode(KEY_LEFTCTRL, True)
                time.sleep(0.02)
                self.rd_client.notify_keyboard_keycode(KEY_V, True)
                time.sleep(0.02)
                self.rd_client.notify_keyboard_keycode(KEY_V, False)
                time.sleep(0.02)
                self.rd_client.notify_keyboard_keycode(KEY_LEFTCTRL, False)
        except Exception as exc:
            logger.warning("Clipboard paste fallback failed: %s", exc)
