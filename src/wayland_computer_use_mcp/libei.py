"""ctypes wrapper for libei (Emulated Input System).

Provides low-level and high-level interaction with libei for injecting:
- Absolute and relative pointer motion
- Mouse button clicks
- Scrolling deltas
- Keyboard key events
via the EIS file descriptor obtained from XDG RemoteDesktop portal.
"""

from __future__ import annotations

import ctypes
import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

# Standard Linux evdev input event codes
BTN_LEFT = 0x110  # 272
BTN_RIGHT = 0x111  # 273
BTN_MIDDLE = 0x112  # 274

# Common Linux keycodes
KEY_ENTER = 28
KEY_LEFTCTRL = 29
KEY_V = 47
KEY_LEFTSHIFT = 42
KEY_BACKSPACE = 14
KEY_TAB = 15
KEY_SPACE = 57
KEY_ESC = 1

# Character to Linux evdev keycode mapping (US QWERTY base)
CHAR_TO_KEYCODE: dict[str, tuple[int, bool]] = {
    "a": (30, False),
    "A": (30, True),
    "b": (48, False),
    "B": (48, True),
    "c": (46, False),
    "C": (46, True),
    "d": (32, False),
    "D": (32, True),
    "e": (18, False),
    "E": (18, True),
    "f": (33, False),
    "F": (33, True),
    "g": (34, False),
    "G": (34, True),
    "h": (35, False),
    "H": (35, True),
    "i": (23, False),
    "I": (23, True),
    "j": (36, False),
    "J": (36, True),
    "k": (37, False),
    "K": (37, True),
    "l": (38, False),
    "L": (38, True),
    "m": (50, False),
    "M": (50, True),
    "n": (49, False),
    "N": (49, True),
    "o": (24, False),
    "O": (24, True),
    "p": (25, False),
    "P": (25, True),
    "q": (16, False),
    "Q": (16, True),
    "r": (19, False),
    "R": (19, True),
    "s": (31, False),
    "S": (31, True),
    "t": (20, False),
    "T": (20, True),
    "u": (22, False),
    "U": (22, True),
    "v": (47, False),
    "V": (47, True),
    "w": (17, False),
    "W": (17, True),
    "x": (45, False),
    "X": (45, True),
    "y": (21, False),
    "Y": (21, True),
    "z": (44, False),
    "Z": (44, True),
    "1": (2, False),
    "!": (2, True),
    "2": (3, False),
    "@": (3, True),
    "3": (4, False),
    "#": (4, True),
    "4": (5, False),
    "$": (5, True),
    "5": (6, False),
    "%": (6, True),
    "6": (7, False),
    "^": (7, True),
    "7": (8, False),
    "&": (8, True),
    "8": (9, False),
    "*": (9, True),
    "9": (10, False),
    "(": (10, True),
    "0": (11, False),
    ")": (11, True),
    " ": (57, False),
    "\n": (28, False),
    "\t": (15, False),
    "-": (12, False),
    "_": (12, True),
    "=": (13, False),
    "+": (13, True),
    "[": (26, False),
    "{": (26, True),
    "]": (27, False),
    "}": (27, True),
    ";": (39, False),
    ":": (39, True),
    "'": (40, False),
    '"': (40, True),
    ",": (51, False),
    "<": (51, True),
    ".": (52, False),
    ">": (52, True),
    "/": (53, False),
    "?": (53, True),
    "\\": (43, False),
    "|": (43, True),
}


class LibEIWrapper:
    """Dynamic ctypes binding to system libei.so.1."""

    def __init__(self) -> None:
        self.available = False
        try:
            self._cdll = ctypes.CDLL("libei.so.1")
            self._setup_prototypes()
            self.available = True
        except (OSError, AttributeError) as exc:
            logger.warning("libei.so.1 could not be loaded: %s", exc)

    def _setup_prototypes(self) -> None:
        c = self._cdll

        # Context lifecycle
        c.ei_new_sender.argtypes = [ctypes.c_void_p]
        c.ei_new_sender.restype = ctypes.c_void_p

        c.ei_unref.argtypes = [ctypes.c_void_p]
        c.ei_unref.restype = ctypes.c_void_p

        c.ei_setup_backend_fd.argtypes = [ctypes.c_void_p, ctypes.c_int]
        c.ei_setup_backend_fd.restype = ctypes.c_int

        c.ei_dispatch.argtypes = [ctypes.c_void_p]
        c.ei_dispatch.restype = ctypes.c_int

        c.ei_get_event.argtypes = [ctypes.c_void_p]
        c.ei_get_event.restype = ctypes.c_void_p

        c.ei_event_unref.argtypes = [ctypes.c_void_p]
        c.ei_event_unref.restype = ctypes.c_void_p

        c.ei_event_get_type.argtypes = [ctypes.c_void_p]
        c.ei_event_get_type.restype = ctypes.c_int

        c.ei_event_get_device.argtypes = [ctypes.c_void_p]
        c.ei_event_get_device.restype = ctypes.c_void_p

        c.ei_now.argtypes = [ctypes.c_void_p]
        c.ei_now.restype = ctypes.c_uint64

        # Device input methods
        c.ei_device_start_emulating.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        c.ei_device_start_emulating.restype = None

        c.ei_device_stop_emulating.argtypes = [ctypes.c_void_p]
        c.ei_device_stop_emulating.restype = None

        c.ei_device_frame.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
        c.ei_device_frame.restype = None

        c.ei_device_pointer_motion_absolute.argtypes = [
            ctypes.c_void_p,
            ctypes.c_double,
            ctypes.c_double,
        ]
        c.ei_device_pointer_motion_absolute.restype = None

        c.ei_device_pointer_motion.argtypes = [ctypes.c_void_p, ctypes.c_double, ctypes.c_double]
        c.ei_device_pointer_motion.restype = None

        c.ei_device_button_button.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_bool]
        c.ei_device_button_button.restype = None

        c.ei_device_scroll_delta.argtypes = [ctypes.c_void_p, ctypes.c_double, ctypes.c_double]
        c.ei_device_scroll_delta.restype = None

        c.ei_device_keyboard_key.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_bool]
        c.ei_device_keyboard_key.restype = None

        # Device queries
        if hasattr(c, "ei_device_has_capability"):
            c.ei_device_has_capability.argtypes = [ctypes.c_void_p, ctypes.c_int]
            c.ei_device_has_capability.restype = ctypes.c_bool

        # Seat binding (required for sender clients to receive devices)
        if hasattr(c, "ei_event_get_seat"):
            c.ei_event_get_seat.argtypes = [ctypes.c_void_p]
            c.ei_event_get_seat.restype = ctypes.c_void_p

        # ei_seat_bind_capabilities is variadic: (seat, cap, cap, ..., 0)
        # We only set restype; argtypes left unset for variadic calls.
        if hasattr(c, "ei_seat_bind_capabilities"):
            c.ei_seat_bind_capabilities.restype = None


libei = LibEIWrapper()


class EIClient:
    """Manages an active libei sender session over an EIS socket file descriptor."""

    # Event types from ei.h
    EI_EVENT_CONNECT = 1
    EI_EVENT_DISCONNECT = 2
    EI_EVENT_SEAT_ADDED = 3
    EI_EVENT_SEAT_REMOVED = 4
    EI_EVENT_DEVICE_ADDED = 5
    EI_EVENT_DEVICE_REMOVED = 6
    EI_EVENT_DEVICE_PAUSED = 7
    EI_EVENT_DEVICE_RESUMED = 8
    EI_EVENT_KEYBOARD_KEYMAP = 9

    # Device capabilities from libei.h (enum ei_device_capability)
    EI_DEVICE_CAP_POINTER = 1
    EI_DEVICE_CAP_POINTER_ABSOLUTE = 2
    EI_DEVICE_CAP_BUTTON = 3
    EI_DEVICE_CAP_SCROLL = 4
    EI_DEVICE_CAP_KEYBOARD = 5
    EI_DEVICE_CAP_TOUCH = 6

    def __init__(self, fd: int | None = None) -> None:
        self.fd = fd
        self.ctx: Any = None
        self.pointer_device: Any = None
        self.pointer_abs_device: Any = None
        self.pointer_rel_device: Any = None
        self.keyboard_device: Any = None
        self.button_device: Any = None
        self.scroll_device: Any = None
        self._connected = False
        self._emulating = False

        if fd is not None and libei.available:
            self._init_session(fd)

    def _init_session(self, fd: int) -> None:
        c = libei._cdll
        self.ctx = c.ei_new_sender(None)
        if not self.ctx:
            raise RuntimeError("Failed to create libei sender context")

        ret = c.ei_setup_backend_fd(self.ctx, fd)
        if ret != 0:
            raise RuntimeError(f"Failed to setup libei backend fd: {ret}")

        # Dispatch initial events: CONNECT → SEAT_ADDED → bind → DEVICE_ADDED
        self.poll_events(timeout=2.0)
        if not self.pointer_device:
            self.poll_events(timeout=2.0)
        self._connected = True
        logger.info(
            "libei session ready: abs_pointer=%s, rel_pointer=%s, keyboard=%s",
            self.pointer_abs_device is not None,
            self.pointer_rel_device is not None,
            self.keyboard_device is not None,
        )

    def poll_events(self, timeout: float = 0.5) -> None:
        """Poll and dispatch pending libei events, binding seats and discovering devices."""
        if not self.ctx or not libei.available:
            return

        c = libei._cdll
        deadline = time.time() + timeout
        while time.time() < deadline:
            c.ei_dispatch(self.ctx)
            event = c.ei_get_event(self.ctx)
            if not event:
                time.sleep(0.01)
                continue

            try:
                event_type = c.ei_event_get_type(event)
                logger.debug("libei event: %s", event_type)

                if event_type == self.EI_EVENT_SEAT_ADDED:
                    # Must bind capabilities to tell the compositor what we need.
                    if hasattr(c, "ei_event_get_seat") and hasattr(c, "ei_seat_bind_capabilities"):
                        seat = c.ei_event_get_seat(event)
                        if seat:
                            logger.info("libei: binding seat capabilities")
                            c.ei_seat_bind_capabilities(
                                ctypes.c_void_p(seat),
                                ctypes.c_uint(self.EI_DEVICE_CAP_POINTER),
                                ctypes.c_uint(self.EI_DEVICE_CAP_POINTER_ABSOLUTE),
                                ctypes.c_uint(self.EI_DEVICE_CAP_BUTTON),
                                ctypes.c_uint(self.EI_DEVICE_CAP_SCROLL),
                                ctypes.c_uint(self.EI_DEVICE_CAP_KEYBOARD),
                                ctypes.c_uint(0),  # sentinel
                            )

                elif event_type in (
                    self.EI_EVENT_DEVICE_ADDED,
                    self.EI_EVENT_DEVICE_RESUMED,
                ):
                    device = c.ei_event_get_device(event)
                    if device:
                        has_cap = getattr(c, "ei_device_has_capability", None)
                        if has_cap:
                            if has_cap(device, self.EI_DEVICE_CAP_POINTER_ABSOLUTE):
                                self.pointer_abs_device = device
                                self.pointer_device = self.pointer_device or device
                                logger.info("libei: acquired absolute pointer device")
                            if has_cap(device, self.EI_DEVICE_CAP_POINTER):
                                self.pointer_rel_device = device
                                self.pointer_device = self.pointer_device or device
                                logger.info("libei: acquired relative pointer device")
                            if has_cap(device, self.EI_DEVICE_CAP_BUTTON):
                                self.button_device = device
                                logger.info("libei: acquired button device")
                            if has_cap(device, self.EI_DEVICE_CAP_KEYBOARD):
                                self.keyboard_device = device
                                logger.info("libei: acquired keyboard device")
                            if has_cap(device, self.EI_DEVICE_CAP_SCROLL):
                                self.scroll_device = device
                                logger.info("libei: acquired scroll device")
                        else:
                            self.pointer_device = self.pointer_device or device
                            self.keyboard_device = self.keyboard_device or device
            finally:
                c.ei_event_unref(event)

    def _get_time_now(self) -> int:
        if self.ctx and libei.available:
            return int(libei._cdll.ei_now(self.ctx))
        return int(time.time() * 1_000_000)

    def pointer_motion_absolute(self, x: float, y: float) -> None:
        """Move cursor to absolute coordinates (x, y)."""
        if not libei.available or not self.ctx:
            logger.info("Mock EIS: pointer_motion_absolute(%s, %s)", x, y)
            return

        c = libei._cdll
        dev = self.pointer_abs_device or self.pointer_device
        if not dev:
            self.poll_events()
            dev = self.pointer_abs_device or self.pointer_device

        if dev:
            t = self._get_time_now()
            c.ei_device_start_emulating(dev, 0)
            c.ei_device_pointer_motion_absolute(dev, ctypes.c_double(x), ctypes.c_double(y))
            c.ei_device_frame(dev, t)
            c.ei_device_stop_emulating(dev)

    def pointer_motion(self, dx: float, dy: float) -> None:
        """Move cursor relatively by (dx, dy)."""
        if not libei.available or not self.ctx:
            logger.info("Mock EIS: pointer_motion(%s, %s)", dx, dy)
            return

        c = libei._cdll
        dev = self.pointer_rel_device or self.pointer_device
        if not dev:
            self.poll_events()
            dev = self.pointer_rel_device or self.pointer_device

        if dev:
            t = self._get_time_now()
            c.ei_device_start_emulating(dev, 0)
            c.ei_device_pointer_motion(dev, ctypes.c_double(dx), ctypes.c_double(dy))
            c.ei_device_frame(dev, t)
            c.ei_device_stop_emulating(dev)

    def button_click(self, button_code: int = BTN_LEFT) -> None:
        """Perform a click (press and release) for specified button code."""
        if not libei.available or not self.ctx:
            logger.info("Mock EIS: button_click(%s)", button_code)
            return

        c = libei._cdll
        dev = self.button_device or self.pointer_abs_device or self.pointer_device
        if not dev:
            self.poll_events()
            dev = self.button_device or self.pointer_abs_device or self.pointer_device

        if dev:
            t1 = self._get_time_now()
            c.ei_device_start_emulating(dev, 0)
            c.ei_device_button_button(dev, ctypes.c_uint32(button_code), True)
            c.ei_device_frame(dev, t1)

            time.sleep(0.03)  # Small natural delay between press and release

            t2 = self._get_time_now()
            c.ei_device_button_button(dev, ctypes.c_uint32(button_code), False)
            c.ei_device_frame(dev, t2)
            c.ei_device_stop_emulating(dev)

    def button_down(self, button_code: int = BTN_LEFT) -> None:
        """Hold mouse button down (for dragging)."""
        if not libei.available or not self.ctx:
            logger.info("Mock EIS: button_down(%s)", button_code)
            return

        c = libei._cdll
        dev = self.button_device or self.pointer_abs_device or self.pointer_device
        if dev:
            t = self._get_time_now()
            c.ei_device_start_emulating(dev, 0)
            c.ei_device_button_button(dev, ctypes.c_uint32(button_code), True)
            c.ei_device_frame(dev, t)

    def button_up(self, button_code: int = BTN_LEFT) -> None:
        """Release mouse button."""
        if not libei.available or not self.ctx:
            logger.info("Mock EIS: button_up(%s)", button_code)
            return

        c = libei._cdll
        dev = self.button_device or self.pointer_abs_device or self.pointer_device
        if dev:
            t = self._get_time_now()
            c.ei_device_button_button(dev, ctypes.c_uint32(button_code), False)
            c.ei_device_frame(dev, t)
            c.ei_device_stop_emulating(dev)

    def scroll(self, dx: float, dy: float) -> None:
        """Dispatch horizontal/vertical scroll deltas."""
        if not libei.available or not self.ctx:
            logger.info("Mock EIS: scroll(%s, %s)", dx, dy)
            return

        c = libei._cdll
        dev = self.scroll_device or self.pointer_device
        if dev:
            t = self._get_time_now()
            c.ei_device_start_emulating(dev, 0)
            c.ei_device_scroll_delta(dev, ctypes.c_double(dx), ctypes.c_double(dy))
            c.ei_device_frame(dev, t)
            c.ei_device_stop_emulating(dev)

    def key_press(self, keycode: int, is_down: bool) -> None:
        """Dispatch a single keyboard key down/up event."""
        if not libei.available or not self.ctx:
            logger.info("Mock EIS: key_press(%s, down=%s)", keycode, is_down)
            return

        c = libei._cdll
        dev = self.keyboard_device
        if not dev:
            self.poll_events()
            dev = self.keyboard_device

        if dev:
            t = self._get_time_now()
            c.ei_device_start_emulating(dev, 0)
            c.ei_device_keyboard_key(dev, ctypes.c_uint32(keycode), is_down)
            c.ei_device_frame(dev, t)
            if not is_down:
                c.ei_device_stop_emulating(dev)

    def type_char(self, char: str) -> bool:
        """Types a single character using evdev keycodes and shift modifier.

        Returns True if character was mapped and dispatched, False if fallback needed.
        """
        if char not in CHAR_TO_KEYCODE:
            return False

        if not self.keyboard_device:
            self.poll_events()
            if not self.keyboard_device:
                return False

        keycode, needs_shift = CHAR_TO_KEYCODE[char]
        if needs_shift:
            self.key_press(KEY_LEFTSHIFT, True)
            time.sleep(0.01)

        self.key_press(keycode, True)
        time.sleep(0.01)
        self.key_press(keycode, False)

        if needs_shift:
            time.sleep(0.01)
            self.key_press(KEY_LEFTSHIFT, False)

        return True

    def key_combination(self, keycodes: list[int]) -> None:
        """Dispatches a key combination: presses in order, releases in reverse."""
        for code in keycodes:
            self.key_press(code, True)
            time.sleep(0.01)

        time.sleep(0.03)

        for code in reversed(keycodes):
            self.key_press(code, False)
            time.sleep(0.01)

    def double_click(self, button_code: int = BTN_LEFT) -> None:
        """Dispatches a standard mouse double-click."""
        self.button_click(button_code)
        time.sleep(0.08)
        self.button_click(button_code)

    def close(self) -> None:
        """Clean up libei context and close file descriptor."""
        if self.ctx and libei.available:
            libei._cdll.ei_unref(self.ctx)
            self.ctx = None
        if self.fd is not None:
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None
